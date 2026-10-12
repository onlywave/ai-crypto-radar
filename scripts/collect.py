#!/usr/bin/env python3
"""AI Crypto Radar — collector e motore dei segnali.

Gira su GitHub Actions ogni 15 minuti. Usa solo API pubbliche senza chiave:
Coinbase Exchange, Kraken, DefiLlama, Hyperliquid, OKX, Akash console, Solana RPC,
Blockscout, nodo Gonka, GitHub, alternative.me, CoinPaprika.

Output:
  data/latest.json       stato completo per il sito
  data/history.json      serie compatte per i delta (prezzo, OI, holder, supply, spesa…)
  data/signals_log.json  storico segnali con esito paper (MFE/MAE)
"""
from __future__ import annotations

import json
import math
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
CFG = json.loads((ROOT / "config" / "tokens.json").read_text())
CAT = json.loads((ROOT / "config" / "catalysts.json").read_text())
DATA = ROOT / "data"
DATA.mkdir(exist_ok=True)

NOW = datetime.now(timezone.utc)
NOW_TS = int(NOW.timestamp())
UA = {"User-Agent": "ai-crypto-radar/1.0 (+https://github.com/onlywave/ai-crypto-radar)"}
GH_TOKEN = os.environ.get("GITHUB_TOKEN")
ERRORS: list[str] = []

EXCHANGE_WORDS = ("binance", "coinbase", "kraken", "okx", "okex", "bybit", "upbit", "kucoin", "gate", "bitget",
                  "mexc", "htx", "huobi", "crypto.com", "bitfinex", "bitstamp", "bithumb", "bitvavo", "gemini")


# ---------------------------------------------------------------- http helpers
def get(url, params=None, timeout=20, headers=None):
    h = dict(UA)
    if headers:
        h.update(headers)
    for attempt in range(2):
        try:
            r = requests.get(url, params=params, timeout=timeout, headers=h)
            if r.status_code == 429 and attempt == 0:
                time.sleep(3)
                continue
            r.raise_for_status()
            return r.json()
        except Exception as e:  # noqa: BLE001
            if attempt == 1:
                ERRORS.append(f"GET {url.split('?')[0]}: {type(e).__name__} {str(e)[:120]}")
    return None


def post(url, payload, timeout=20):
    try:
        r = requests.post(url, json=payload, timeout=timeout, headers=UA)
        r.raise_for_status()
        return r.json()
    except Exception as e:  # noqa: BLE001
        ERRORS.append(f"POST {url}: {type(e).__name__} {str(e)[:120]}")
        return None


def fnum(x, default=None):
    try:
        v = float(x)
        return v if math.isfinite(v) else default
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------- indicators
def ema(values, n):
    if len(values) < n:
        return None
    k = 2 / (n + 1)
    e = sum(values[:n]) / n
    for v in values[n:]:
        e = v * k + e * (1 - k)
    return e


def rsi(values, n=14):
    if len(values) < n + 1:
        return None
    gains, losses = [], []
    for a, b in zip(values[:-1], values[1:]):
        d = b - a
        gains.append(max(d, 0))
        losses.append(max(-d, 0))
    ag = sum(gains[:n]) / n
    al = sum(losses[:n]) / n
    for g, l in zip(gains[n:], losses[n:]):
        ag = (ag * (n - 1) + g) / n
        al = (al * (n - 1) + l) / n
    if al == 0:
        return 100.0
    rs = ag / al
    return 100 - 100 / (1 + rs)


# ---------------------------------------------------------------- market data
def coinbase_daily(product):
    """Restituisce lista di candele giornaliere [(ts, open, high, low, close, vol)] crescenti."""
    rows = get(f"https://api.exchange.coinbase.com/products/{product}/candles", {"granularity": 86400})
    if not isinstance(rows, list) or not rows:
        return None
    out = [(int(r[0]), r[3], r[2], r[1], r[4], r[5]) for r in rows]
    out.sort(key=lambda x: x[0])
    return out


def coinbase_ticker(product):
    t = get(f"https://api.exchange.coinbase.com/products/{product}/ticker")
    if not t or "price" not in t:
        return None
    age_h = None
    try:
        ts = datetime.fromisoformat(t["time"].replace("Z", "+00:00"))
        age_h = (NOW - ts).total_seconds() / 3600
    except Exception:  # noqa: BLE001
        pass
    return {"price": fnum(t["price"]), "age_h": age_h}


def kraken_daily(pair):
    j = get("https://api.kraken.com/0/public/OHLC", {"pair": pair, "interval": 1440})
    if not j or j.get("error"):
        return None
    res = j.get("result", {})
    key = next((k for k in res if k != "last"), None)
    if not key:
        return None
    return [(int(r[0]), fnum(r[1]), fnum(r[2]), fnum(r[3]), fnum(r[4]), fnum(r[6])) for r in res[key]]


def llama_prices(cg_ids):
    keys = ",".join(f"coingecko:{c}" for c in cg_ids)
    j = get(f"https://coins.llama.fi/prices/current/{keys}")
    out = {}
    if j and "coins" in j:
        for k, v in j["coins"].items():
            out[k.split(":", 1)[1]] = fnum(v.get("price"))
    return out


def llama_daily(cg_id, days=200):
    start = NOW_TS - days * 86400
    j = get(f"https://coins.llama.fi/chart/coingecko:{cg_id}", {"start": start, "span": days, "period": "1d"})
    try:
        pts = j["coins"][f"coingecko:{cg_id}"]["prices"]
    except Exception:  # noqa: BLE001
        return None
    return [(int(p["timestamp"]), None, None, None, fnum(p["price"]), None) for p in pts]


# ---------------------------------------------------------------- derivatives
def hyperliquid_ctx():
    j = post("https://api.hyperliquid.xyz/info", {"type": "metaAndAssetCtxs"})
    out = {}
    try:
        meta, ctxs = j[0], j[1]
        for u, c in zip(meta["universe"], ctxs):
            mark = fnum(c.get("markPx"))
            oi = fnum(c.get("openInterest"))
            out[u["name"]] = {
                "funding_8h": (fnum(c.get("funding"), 0) or 0) * 8 * 100,  # % per 8h
                "oi_usd": oi * mark if (oi is not None and mark) else None,
                "mark": mark,
                "venue": "Hyperliquid",
            }
    except Exception as e:  # noqa: BLE001
        ERRORS.append(f"hyperliquid parse: {e}")
    return out


def okx_swap(inst):
    fr = get("https://www.okx.com/api/v5/public/funding-rate", {"instId": inst})
    oi = get("https://www.okx.com/api/v5/public/open-interest", {"instType": "SWAP", "instId": inst})
    try:
        return {
            "funding_8h": fnum(fr["data"][0]["fundingRate"]) * 100,
            "oi_usd": fnum(oi["data"][0].get("oiUsd")),
            "venue": "OKX",
        }
    except Exception:  # noqa: BLE001
        return None


# ---------------------------------------------------------------- on-chain
def akash():
    j = get("https://console-api.akash.network/v1/dashboard-data")
    if not j:
        return None
    now, cmp_ = j.get("now", {}), j.get("compare", {})
    cap = j.get("networkCapacity", {})
    spend = fnum(now.get("dailyUUsdSpent"))
    prev = fnum(cmp_.get("dailyUUsdSpent"))
    return {
        "spesa_giorno_usd": spend / 1e6 if spend is not None else None,
        "spesa_giorno_prec_usd": prev / 1e6 if prev is not None else None,
        "lease_attivi": now.get("activeLeaseCount"),
        "gpu_attive": now.get("activeGPU"),
        "gpu_totali": cap.get("totalGPU"),
        "provider": cap.get("activeProviderCount"),
    }


def solana_supply(mint):
    for rpc in ("https://api.mainnet-beta.solana.com", "https://solana-rpc.publicnode.com"):
        j = post(rpc, {"jsonrpc": "2.0", "id": 1, "method": "getTokenSupply", "params": [mint]})
        try:
            return {"supply": fnum(j["result"]["value"]["uiAmountString"])}
        except Exception:  # noqa: BLE001
            continue
    return None


def is_exchange(addr_obj):
    if not isinstance(addr_obj, dict):
        return None
    name = (addr_obj.get("name") or "").lower()
    tags = []
    md = addr_obj.get("metadata") or {}
    for t in md.get("tags") or []:
        tags.append(((t.get("name") or "") + " " + (t.get("tagType") or "")).lower())
    blob = " ".join([name] + tags)
    if "exchange" in blob or any(w in blob for w in EXCHANGE_WORDS):
        return blob.strip()[:40]
    return None


def erc20(explorer, address, decimals, price):
    out = {}
    c = get(f"{explorer}/api/v2/tokens/{address}/counters")
    if c:
        out["holder"] = fnum(c.get("token_holders_count"))
    tr = get(f"{explorer}/api/v2/tokens/{address}/transfers")
    items = (tr or {}).get("items") or []
    if items and price:
        inflow = outflow = 0.0
        whales = []
        oldest = None
        for it in items:
            try:
                ts = datetime.fromisoformat(it["timestamp"].replace("Z", "+00:00"))
            except Exception:  # noqa: BLE001
                continue
            oldest = ts if (oldest is None or ts < oldest) else oldest
            raw = fnum((it.get("total") or {}).get("value"), 0)
            dec = int((it.get("total") or {}).get("decimals") or decimals)
            usd = raw / (10 ** dec) * price
            to_x, from_x = is_exchange(it.get("to")), is_exchange(it.get("from"))
            if to_x and not from_x:
                inflow += usd
            elif from_x and not to_x:
                outflow += usd
            if usd >= 100_000:
                whales.append({"usd": round(usd), "verso": to_x or "", "da": from_x or "",
                               "tx": it.get("transaction_hash") or (it.get("tx_hash")), "ts": it["timestamp"]})
        span_h = (NOW - oldest).total_seconds() / 3600 if oldest else None
        out.update({"afflussi_exchange_usd": round(inflow), "deflussi_exchange_usd": round(outflow),
                    "finestra_ore": round(span_h, 1) if span_h else None, "whale": whales[:5]})
    return out or None


def gonka():
    out = {}
    for base in ("https://node3.gonka.ai", "https://node1.gonka.ai:8443", "https://node2.gonka.ai:8443"):
        s = get(f"{base}/chain-api/cosmos/bank/v1beta1/supply")
        if s and "supply" in s:
            for x in s["supply"]:
                if x.get("denom") == "ngonka":
                    out["supply"] = fnum(x.get("amount"), 0) / 1e9
            p = get(f"{base}/v1/epochs/current/participants")
            try:
                parts = p["active_participants"]["participants"]
                out["partecipanti"] = len(parts)
                out["peso_totale"] = sum(fnum(x.get("weight"), 0) for x in parts)
                out["epoca"] = p["active_participants"].get("epoch_id") or p.get("epoch_id")
            except Exception:  # noqa: BLE001
                pass
            break
    return out or None


def llama_fees(slug):
    rev = get(f"https://api.llama.fi/summary/fees/{slug}", {"dataType": "dailyRevenue"})
    fee = get(f"https://api.llama.fi/summary/fees/{slug}", {"dataType": "dailyFees"})
    out = {}
    if rev and rev.get("total30d") is not None:
        out["ricavi_30g_usd"] = fnum(rev["total30d"])
        out["ricavi_annui_usd"] = fnum(rev["total30d"], 0) * 365 / 30
    if fee and fee.get("total30d") is not None:
        out["fee_30g_usd"] = fnum(fee["total30d"])
    return out or None


def github_release(repo):
    h = {"Accept": "application/vnd.github+json"}
    if GH_TOKEN:
        h["Authorization"] = f"Bearer {GH_TOKEN}"
    j = get(f"https://api.github.com/repos/{repo}/releases", {"per_page": 3}, headers=h)
    if not isinstance(j, list) or not j:
        return None
    r = j[0]
    return {"repo": repo, "tag": r.get("tag_name"), "data": r.get("published_at"), "url": r.get("html_url"),
            "prerelease": r.get("prerelease")}


# ---------------------------------------------------------------- history
HIST_PATH = DATA / "history.json"
HIST = json.loads(HIST_PATH.read_text()) if HIST_PATH.exists() else {}
KEEP_SEC = 8 * 86400


def hist_push(sym, row):
    arr = HIST.setdefault(sym, [])
    arr.append(row)
    cutoff = NOW_TS - KEEP_SEC
    HIST[sym] = [r for r in arr if r["t"] >= cutoff]


def hist_value_ago(sym, field, hours):
    """Valore del campo circa `hours` ore fa (il punto più vicino entro ±40%)."""
    target = NOW_TS - hours * 3600
    best, bestd = None, None
    for r in HIST.get(sym, []):
        if r.get(field) is None:
            continue
        d = abs(r["t"] - target)
        if d <= hours * 3600 * 0.4 and (bestd is None or d < bestd):
            best, bestd = r[field], d
    return best


def pct(a, b):
    if a is None or b in (None, 0):
        return None
    return (a / b - 1) * 100


# ---------------------------------------------------------------- signal engine
def active_catalysts(sym):
    out = []
    for e in CAT["eventi"]:
        if e["token"] not in (sym,) or not e.get("data") or not e.get("effetto"):
            continue
        d = datetime.fromisoformat(e["data"]).replace(tzinfo=timezone.utc)
        days = (d - NOW).total_seconds() / 86400
        if -1 <= days <= e.get("finestra", 0):
            out.append((e, days))
    return out


def evaluate(tok, m, d, oc, macro):
    sym, lv = tok["sym"], tok["levels"]
    p = m.get("price")
    reasons, comp = [], {}

    def add(cat, s, txt):
        reasons.append({"cat": cat, "s": s, "txt": txt})
        comp[cat] = comp.get(cat, 0) + s

    if p is None:
        return {"stato": "DATI MANCANTI", "score": None, "reasons": [], "copertura": 0}

    # trend & momentum
    e20, e50, r = m.get("ema20"), m.get("ema50"), m.get("rsi14")
    if e20 and e50:
        if p > e50 and e20 > e50:
            add("trend", 1, "Trend rialzista: prezzo sopra EMA50 e EMA20 > EMA50")
        elif p < e50 and e20 < e50:
            add("trend", -1, "Trend ribassista: prezzo sotto EMA50 e EMA20 < EMA50")
    if r is not None:
        if r < 30:
            add("momentum", 1, f"RSI14 {r:.0f}: ipervenduto")
        elif r > 75:
            add("momentum", -1, f"RSI14 {r:.0f}: ipercomprato")
    vr, c24 = m.get("vol_ratio"), m.get("chg24")
    if vr and c24 is not None and vr > 2:
        if c24 > 5:
            add("volume", 1, f"Volume {vr:.1f}x la media con prezzo +{c24:.1f}%")
        elif c24 < -5:
            add("volume", -1, f"Volume {vr:.1f}x la media con prezzo {c24:.1f}%")

    # livelli
    zlo, zhi = lv["zone"]
    in_zone = zlo * 0.99 <= p <= zhi * 1.01
    if in_zone:
        add("livelli", 1, f"Prezzo in zona d'ingresso {zlo}–{zhi}")
    last_close = m.get("last_close")
    if last_close and last_close > lv["breakout"]:
        add("livelli", 1, f"Chiusura giornaliera sopra il breakout {lv['breakout']}")
    broke = p < lv["invalid"]
    if broke:
        add("livelli", -2, f"Sotto il livello di invalidazione {lv['invalid']}")

    # derivati
    if d:
        fr, oic = d.get("funding_8h"), d.get("oi_chg24")
        if fr is not None and oic is not None and fr > 0.05 and oic > 10:
            add("derivati", -1, f"Long affollati: funding {fr:.3f}%/8h, OI +{oic:.0f}% in 24h")
        if fr is not None and c24 is not None and fr < -0.02 and c24 > 0:
            add("derivati", 1, f"Funding negativo ({fr:.3f}%/8h) con prezzo in salita: short squeeze possibile")
        if oic is not None and c24 is not None and oic < -20 and c24 < -8:
            add("derivati", 1, f"Flush di leva: OI {oic:.0f}% e prezzo {c24:.1f}% in 24h")

    # on-chain / fondamentali in tempo reale
    if oc:
        t = tok["onchain"]["type"]
        if t == "akash":
            s7 = oc.get("spesa_vs_media7_pct")
            if s7 is not None and s7 > 25:
                add("onchain", 1, f"Spesa Akash +{s7:.0f}% sulla media 7 giorni")
            elif s7 is not None and s7 < -25:
                add("onchain", -1, f"Spesa Akash {s7:.0f}% sulla media 7 giorni")
        if t == "solana_supply":
            sc = oc.get("supply_chg24")
            if sc is not None and sc < 0:
                add("onchain", 1, f"Supply in calo nelle 24h ({sc:+.0f} token): burn netto")
        if t == "erc20":
            vol = m.get("vol24_usd")
            inf, out = oc.get("afflussi_exchange_usd") or 0, oc.get("deflussi_exchange_usd") or 0
            net = inf - out
            if vol and vol > 0:
                if net > 0.02 * vol:
                    add("onchain", -1, f"Afflussi netti verso exchange {net/1e3:,.0f}k $ (pressione in vendita)")
                elif -net > 0.02 * vol:
                    add("onchain", 1, f"Deflussi netti dagli exchange {-net/1e3:,.0f}k $ (accumulo)")
            h7 = oc.get("holder_chg7_pct")
            if h7 is not None and h7 > 1:
                add("onchain", 1, f"Holder +{h7:.1f}% in 7 giorni")
            elif h7 is not None and h7 < -1:
                add("onchain", -1, f"Holder {h7:.1f}% in 7 giorni")
        if t == "gonka":
            pc = oc.get("partecipanti_chg24_pct")
            if pc is not None and pc >= 20:
                add("onchain", 1, f"Partecipanti rete +{pc:.0f}% in 24h")
            elif pc is not None and pc <= -20:
                add("onchain", -1, f"Partecipanti rete {pc:.0f}% in 24h")

    # catalizzatori
    for e, days in active_catalysts(sym):
        when = "oggi" if abs(days) < 1 else (f"tra {days:.0f} giorni" if days > 0 else "ieri")
        add("catalizzatori", e["effetto"], f"{e['evento']} ({when})")

    # offerta (dossier: prezzo = valore di mercato / offerta)
    circ, nuova = tok.get("circolante"), tok.get("nuova_offerta")
    if circ and nuova is not None:
        dil = nuova / circ
        if nuova == 0:
            add("offerta", 1, "Offerta fissa: nessuna nuova emissione di token")
        elif dil > 0.10:
            fall = (1 / (1 + dil) - 1) * 100
            add("offerta", -1, f"Diluizione {dil*100:.0f}% l'anno: a valore invariato il prezzo scende del {abs(fall):.0f}%")

    # macro
    if macro.get("btc_sotto_ema50"):
        add("macro", -1, "BTC sotto EMA50 giornaliera: regime risk-off")
    fg = macro.get("fear_greed")
    if fg is not None and fg > 80:
        add("macro", -1, f"Fear & Greed {fg}: euforia")
    elif fg is not None and fg < 25:
        add("macro", 1, f"Fear & Greed {fg}: paura estrema")

    score = sum(x["s"] for x in reasons)
    gate = tok["gate"]
    if broke or score <= -3:
        stato = "USCITA"
    elif score <= -2:
        stato = "RIDUCI"
    elif gate == "fascia1" and score >= 3:
        stato = "ENTRATA"
    elif gate == "fascia1" and in_zone and score >= 1:
        stato = "ACCUMULO"
    elif gate == "fascia2" and score >= 4:
        stato = "SETUP TATTICO"
    else:
        stato = "ATTESA"
    nota_fascia = None
    if gate == "fascia3" and score >= 4:
        nota_fascia = "Timing favorevole, ma il token è 'da verificare': nessun segnale d'acquisto finché la tesi non migliora."

    sources = ["trend", "momentum", "livelli"]
    have = [m.get("ema50") is not None, r is not None, True]
    if tok.get("hl") or tok.get("okx_swap"):
        sources.append("derivati"); have.append(bool(d))
    if tok["onchain"]["type"] != "none":
        sources.append("onchain"); have.append(bool(oc))
    cop = round(100 * sum(have) / len(have))
    return {"stato": stato, "score": score, "reasons": reasons, "componenti": comp, "nota_fascia": nota_fascia,
            "in_zona": in_zone, "sotto_invalidazione": broke, "copertura": cop}


# ---------------------------------------------------------------- paper log
LOG_PATH = DATA / "signals_log.json"
LOG = json.loads(LOG_PATH.read_text()) if LOG_PATH.exists() else {"segnali": []}
BUY = {"ENTRATA", "ACCUMULO", "SETUP TATTICO"}
STOP_BUDGET = 0.2414  # dossier: budget di perdita 3.500 $ su 14.500 $ di posizione spot
SELL = {"USCITA", "RIDUCI"}


def update_log(sym, sig, price, invalid):
    if price is None:
        return None
    opened = [s for s in LOG["segnali"] if s["token"] == sym and s["esito"] == "APERTO"]
    event = None
    for s in opened:
        s["max"] = max(s["max"], price)
        s["min"] = min(s["min"], price)
        s["mfe_pct"] = round(pct(s["max"], s["prezzo"]), 2)
        s["mae_pct"] = round(pct(s["min"], s["prezzo"]), 2)
        s["ultimo"] = price
        ret = pct(price, s["prezzo"])
        s["rend_pct"] = round(ret, 2)
        age_d = (NOW_TS - s["t"]) / 86400
        reason = None
        if sig["stato"] in SELL:
            reason = f"segnale {sig['stato']}"
        elif price <= s["stop"]:
            reason = "stop"
        elif s["mfe_pct"] >= 25 and price <= s["max"] * 0.88:
            reason = "trailing stop 12% dopo +25%"
        elif age_d >= 30:
            reason = "time stop 30 giorni"
        if reason:
            s["esito"] = "CHIUSO"
            s["chiusura"] = NOW.isoformat(timespec="minutes")
            s["motivo_chiusura"] = reason
            event = {"tipo": "chiusura", "token": sym, "rend_pct": s["rend_pct"], "motivo": reason}
    still_open = any(s["token"] == sym and s["esito"] == "APERTO" for s in LOG["segnali"])
    if sig["stato"] in BUY and not still_open:
        stop = max(invalid, price * (1 - STOP_BUDGET))
        LOG["segnali"].append({
            "id": f"{sym}-{NOW_TS}", "token": sym, "stato": sig["stato"], "t": NOW_TS,
            "data": NOW.isoformat(timespec="minutes"), "prezzo": price, "stop": round(stop, 8),
            "max": price, "min": price, "mfe_pct": 0.0, "mae_pct": 0.0, "ultimo": price, "rend_pct": 0.0,
            "score": sig["score"], "motivi": [r["txt"] for r in sig["reasons"] if r["s"] > 0][:5],
            "esito": "APERTO"})
        event = {"tipo": "apertura", "token": sym, "stato": sig["stato"], "prezzo": price}
    return event


def notify(lines):
    tok, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if not (tok and chat and lines):
        return
    try:
        requests.post(f"https://api.telegram.org/bot{tok}/sendMessage",
                      json={"chat_id": chat, "text": "\n".join(lines)[:3900], "disable_web_page_preview": True},
                      timeout=15)
    except Exception as e:  # noqa: BLE001
        ERRORS.append(f"telegram: {e}")


# ---------------------------------------------------------------- main
def main():
    toks = CFG["tokens"]
    prev_latest = json.loads((DATA / "latest.json").read_text()) if (DATA / "latest.json").exists() else {}
    prev_states = {t["sym"]: t.get("segnale", {}).get("stato") for t in prev_latest.get("tokens", [])}

    llama = llama_prices([t["cg"] for t in toks] + ["bitcoin"])
    hl = hyperliquid_ctx()

    # macro
    macro = {}
    fng = get("https://api.alternative.me/fng/", {"limit": 2})
    try:
        macro["fear_greed"] = int(fng["data"][0]["value"])
        macro["fear_greed_label"] = fng["data"][0]["value_classification"]
    except Exception:  # noqa: BLE001
        pass
    glob = get("https://api.coinpaprika.com/v1/global")
    if glob:
        macro["btc_dominance"] = fnum(glob.get("bitcoin_dominance_percentage"))
        macro["mcap_totale_usd"] = fnum(glob.get("market_cap_usd"))
    btc = coinbase_daily("BTC-USD") or kraken_daily("XBTUSD")
    if btc:
        closes = [c[4] for c in btc if c[4]]
        macro["btc"] = closes[-1]
        macro["btc_chg24"] = pct(closes[-1], closes[-2]) if len(closes) > 1 else None
        e50 = ema(closes, 50)
        macro["btc_ema50"] = e50
        macro["btc_sotto_ema50"] = bool(e50 and closes[-1] < e50)

    out_tokens, events = [], []
    for tok in toks:
        sym = tok["sym"]
        m = {}
        candles, src = None, None
        if tok.get("coinbase"):
            candles, src = coinbase_daily(tok["coinbase"]), "Coinbase"
        if not candles and tok.get("kraken"):
            candles, src = kraken_daily(tok["kraken"]), "Kraken"
        if not candles:
            candles, src = llama_daily(tok["cg"]), "DefiLlama"
        live = None
        if tok.get("coinbase"):
            tk = coinbase_ticker(tok["coinbase"])
            if tk and tk["price"] and (tk["age_h"] is None or tk["age_h"] < 6):
                live = tk["price"]
        price = live or llama.get(tok["cg"]) or (candles[-1][4] if candles else None)
        m["price"] = price
        m["fonte_prezzo"] = "Coinbase" if live else ("DefiLlama" if llama.get(tok["cg"]) else src)
        if candles:
            closes = [c[4] for c in candles if c[4]]
            m["ema20"], m["ema50"], m["ema200"] = ema(closes, 20), ema(closes, 50), ema(closes, 200)
            m["rsi14"] = rsi(closes[-120:])
            m["last_close"] = closes[-2] if len(closes) > 1 else None  # ultima candela chiusa
            m["chg24"] = pct(price, closes[-2]) if len(closes) > 1 else None
            m["chg7"] = pct(price, closes[-8]) if len(closes) > 8 else None
            m["chg30"] = pct(price, closes[-31]) if len(closes) > 31 else None
            vols = [c[5] for c in candles if c[5]]
            if len(vols) >= 21:
                avg = sum(vols[-21:-1]) / 20
                m["vol_ratio"] = vols[-1] / avg if avg else None
                m["vol24_usd"] = vols[-1] * price if price else None
            m["spark"] = [round(x, 8) for x in closes[-60:]]
            m["fonte_candele"] = src

        # derivati
        d = None
        if tok.get("hl") and tok["hl"] in hl:
            d = dict(hl[tok["hl"]])
        if (not d or d.get("oi_usd") is None) and tok.get("okx_swap"):
            d = okx_swap(tok["okx_swap"]) or d
        if d and d.get("oi_usd") is not None:
            d["oi_chg24"] = pct(d["oi_usd"], hist_value_ago(sym, "oi", 24))

        # on-chain
        oc, t = None, tok["onchain"]["type"]
        if t == "akash":
            oc = akash()
            if oc and oc.get("spesa_giorno_usd") is not None:
                past = [r.get("spend") for r in HIST.get(sym, []) if r.get("spend") is not None and r["t"] > NOW_TS - 7 * 86400]
                if len(past) >= 8:
                    avg7 = sum(past) / len(past)
                    oc["spesa_vs_media7_pct"] = pct(oc["spesa_giorno_usd"], avg7)
        elif t == "solana_supply":
            oc = solana_supply(tok["onchain"]["mint"])
            if oc and oc.get("supply") is not None:
                prev = hist_value_ago(sym, "supply", 24)
                oc["supply_chg24"] = (oc["supply"] - prev) if prev is not None else None
        elif t == "erc20":
            o = tok["onchain"]
            oc = erc20(o["explorer"], o["address"], o["decimals"], price)
            if (not oc or oc.get("holder") is None) and o.get("fallback"):
                fb = o["fallback"]
                oc = erc20(fb["explorer"], fb["address"], fb["decimals"], price)
                if oc:
                    oc["catena"] = "Ethereum (fallback)"
            if oc and oc.get("holder") is not None:
                oc["holder_chg7_pct"] = pct(oc["holder"], hist_value_ago(sym, "holders", 24 * 7))
                oc["holder_chg24_pct"] = pct(oc["holder"], hist_value_ago(sym, "holders", 24))
        elif t == "gonka":
            oc = gonka()
            if oc and oc.get("partecipanti") is not None:
                oc["partecipanti_chg24_pct"] = pct(oc["partecipanti"], hist_value_ago(sym, "parts", 24))
                if oc.get("supply") is not None:
                    prev = hist_value_ago(sym, "supply", 24)
                    oc["supply_chg24"] = (oc["supply"] - prev) if prev is not None else None

        fees = llama_fees(tok["llama_fees"]) if tok.get("llama_fees") else None
        rel = [x for x in (github_release(r) for r in tok.get("github", [])) if x]

        sig = evaluate(tok, m, d, oc, macro)
        ev = update_log(sym, sig, price, tok["levels"]["invalid"])
        if ev:
            events.append(ev)
        if prev_states.get(sym) and prev_states[sym] != sig["stato"]:
            events.append({"tipo": "cambio", "token": sym, "da": prev_states[sym], "a": sig["stato"], "prezzo": price})

        hist_push(sym, {
            "t": NOW_TS, "p": price,
            "oi": d.get("oi_usd") if d else None,
            "fr": d.get("funding_8h") if d else None,
            "holders": oc.get("holder") if oc else None,
            "supply": oc.get("supply") if oc else None,
            "spend": oc.get("spesa_giorno_usd") if oc else None,
            "parts": oc.get("partecipanti") if oc else None,
        })

        # economia del token con il prezzo corrente (dossier: flussi, offerta, valutazione)
        eco = {}
        circ, nuova = tok.get("circolante"), tok.get("nuova_offerta")
        if price and circ:
            mcap = price * circ
            ric = (fees or {}).get("ricavi_annui_usd")
            if tok["sym"] == "AKT" and oc and oc.get("spesa_giorno_usd"):
                ric_live = oc["spesa_giorno_usd"] * 365
            else:
                ric_live = None
            ric_usd = ric_live or ric or tok["fund"]["ricavi_m"] * 1e6
            fonte = "spesa on-chain di oggi ×365" if ric_live else ("DefiLlama, ultimi 30 giorni annualizzati" if ric else "stima del dossier")
            emis = (nuova or 0) * price
            eco = {
                "mcap_usd": mcap,
                "ricavi_annui_usd": ric_usd,
                "fonte_ricavi": fonte,
                "mcap_su_ricavi": mcap / ric_usd if ric_usd else None,
                "emissioni_annue_usd": emis,
                "copertura_emissioni_pct": (ric_usd / emis * 100) if emis else None,
                "diluizione_pct": (nuova / circ * 100) if nuova is not None else None,
                "prezzo_a_valore_invariato_pct": ((1 / (1 + nuova / circ) - 1) * 100) if nuova else 0.0,
                "stock_transazionale_30g_usd": ric_usd * 30 / 365 if ric_usd else None,
            }

        out_tokens.append({
            "sym": sym, "name": tok["name"], "gate": tok["gate"], "verdetto": tok["verdetto"],
            "fascia": tok.get("fascia"), "rank": tok.get("rank"), "motivo": tok.get("motivo"), "limite": tok.get("limite"),
            "catena": tok.get("catena"), "prove": tok.get("prove"), "promuove": tok.get("promuove"),
            "declassa": tok.get("declassa"), "verifiche": tok.get("verifiche"), "kraken": tok.get("kraken"),
            "nota_offerta": tok.get("nota_offerta"),
            "coinbase": tok.get("coinbase"), "cg": tok["cg"], "hl": tok.get("hl"),
            "levels": tok["levels"], "fund": tok["fund"], "trigger_fondamentale": tok["trigger_fondamentale"],
            "canali": tok["canali"], "mercato": m, "derivati": d, "onchain": oc, "fees": fees,
            "release": rel, "segnale": sig, "economia": eco,
        })

    # riepilogo paper
    closed = [s for s in LOG["segnali"] if s["esito"] == "CHIUSO"]
    wins = [s for s in closed if s.get("rend_pct", 0) > 0]
    paper = {
        "aperti": sum(1 for s in LOG["segnali"] if s["esito"] == "APERTO"),
        "chiusi": len(closed),
        "win_rate": round(100 * len(wins) / len(closed), 1) if closed else None,
        "rend_medio_pct": round(sum(s.get("rend_pct", 0) for s in closed) / len(closed), 2) if closed else None,
        "pnl_100_usd": round(sum(s.get("rend_pct", 0) for s in closed), 2) if closed else 0,
    }

    latest = {
        "generato": NOW.isoformat(timespec="seconds"),
        "generato_ts": NOW_TS,
        "macro": macro,
        "tokens": out_tokens,
        "catalizzatori": CAT["eventi"],
        "paper": paper,
        "eventi_run": events,
        "errori": ERRORS[-40:],
        "dossier": CFG.get("dossier"),
        "stop_budget_pct": STOP_BUDGET * 100,
        "versione": 2,
    }
    (DATA / "latest.json").write_text(json.dumps(latest, ensure_ascii=False, separators=(",", ":")))
    HIST_PATH.write_text(json.dumps(HIST, separators=(",", ":")))
    LOG["segnali"] = LOG["segnali"][-500:]
    LOG_PATH.write_text(json.dumps(LOG, ensure_ascii=False, indent=1))

    lines = []
    for e in events:
        if e["tipo"] == "apertura":
            lines.append(f"🟢 {e['token']}: {e['stato']} a {e['prezzo']}")
        elif e["tipo"] == "chiusura":
            lines.append(f"🔴 {e['token']}: chiuso ({e['motivo']}) {e['rend_pct']:+.1f}%")
        elif e["tipo"] == "cambio" and (e["a"] in BUY | SELL):
            lines.append(f"⚠️ {e['token']}: {e['da']} → {e['a']} a {e['prezzo']}")
    if lines:
        lines.append("https://onlywave.github.io/ai-crypto-radar/")
    notify(lines)

    print(f"OK {NOW.isoformat()} tokens={len(out_tokens)} eventi={len(events)} errori={len(ERRORS)}")
    for e in ERRORS:
        print("  ERR", e)
    for t in out_tokens:
        s = t["segnale"]
        print(f"  {t['sym']:8} {t['mercato'].get('price')!s:>12} {s['stato']:14} score={s['score']} cop={s.get('copertura')}%")


if __name__ == "__main__":
    sys.exit(main())
