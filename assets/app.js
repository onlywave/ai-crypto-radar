/* AI Crypto Radar — front-end live */
(() => {
  "use strict";
  const $ = (s, el = document) => el.querySelector(s);
  const TZ = "Europe/Zurich";
  const BUY = new Set(["ENTRATA", "ACCUMULO", "SETUP TATTICO"]);
  const SELL = new Set(["USCITA", "RIDUCI"]);
  const STATE_LABEL = { "ENTRATA": "Entrata", "ACCUMULO": "Accumulo", "SETUP TATTICO": "Setup tattico", "RIDUCI": "Riduci", "USCITA": "Uscita", "ATTESA": "Attesa", "DATI MANCANTI": "Dati mancanti" };
  const stClass = (s) => "st st-" + (s || "ATTESA").split(" ")[0];

  let DATA = null, LOG = null;
  const live = {};       // sym -> {price, t}
  const liveDer = {};    // hl sym -> funding_8h
  const open = new Set();
  let filter = "tutti";

  // ------------------------------------------------------------ format
  const nf = (d) => new Intl.NumberFormat("it-CH", { maximumFractionDigits: d, minimumFractionDigits: d });
  function fmtPrice(v) {
    if (v == null || !isFinite(v)) return "—";
    if (v >= 1000) return nf(0).format(v);
    if (v >= 100) return nf(1).format(v);
    if (v >= 1) return nf(3).format(v);
    if (v >= 0.01) return nf(4).format(v);
    return new Intl.NumberFormat("it-CH", { maximumSignificantDigits: 3 }).format(v);
  }
  const fmtPct = (v, d = 1) => (v == null || !isFinite(v)) ? "—" : (v > 0 ? "+" : "") + nf(d).format(v) + "%";
  function fmtUsd(v) {
    if (v == null || !isFinite(v)) return "—";
    return new Intl.NumberFormat("it-CH", { notation: "compact", maximumFractionDigits: 1 }).format(v) + " $";
  }
  const fmtTime = (iso) => iso ? new Date(iso).toLocaleString("it-CH", { timeZone: TZ, day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" }) : "—";
  const fmtDate = (iso) => iso ? new Date(iso + "T12:00:00Z").toLocaleDateString("it-CH", { timeZone: TZ, day: "numeric", month: "short", year: "numeric" }) : "senza data";
  function el(tag, cls, text) { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; }

  // ------------------------------------------------------------ data
  async function loadData() {
    try {
      const r = await fetch("data/latest.json?t=" + Date.now(), { cache: "no-store" });
      if (!r.ok) throw new Error(r.status);
      DATA = await r.json();
    } catch (e) {
      $("#summary").textContent = "I dati della scansione non sono ancora disponibili: la prima scansione parte entro 15 minuti dalla pubblicazione.";
      return;
    }
    try {
      const r = await fetch("data/signals_log.json?t=" + Date.now(), { cache: "no-store" });
      LOG = r.ok ? await r.json() : { segnali: [] };
    } catch { LOG = { segnali: [] }; }
    renderAll();
  }

  // ------------------------------------------------------------ live prices (browser)
  async function pollLive() {
    if (!DATA) return;
    const jobs = DATA.tokens.filter(t => t.coinbase).map(async t => {
      try {
        const r = await fetch(`https://api.exchange.coinbase.com/products/${t.coinbase}/ticker`);
        const j = await r.json();
        const age = (Date.now() - new Date(j.time).getTime()) / 3.6e6;
        if (j.price && age < 6) setLive(t.sym, parseFloat(j.price));
      } catch { /* resta l'ultimo prezzo */ }
    });
    jobs.push((async () => {
      try {
        const r = await fetch("https://api.exchange.coinbase.com/products/BTC-USD/ticker");
        const j = await r.json(); if (j.price) live.BTC = { price: parseFloat(j.price), t: Date.now() };
      } catch { }
    })());
    const noCb = DATA.tokens.filter(t => !t.coinbase || !live[t.sym]);
    if (noCb.length) jobs.push((async () => {
      try {
        const ids = noCb.map(t => "coingecko:" + t.cg).join(",");
        const r = await fetch("https://coins.llama.fi/prices/current/" + ids);
        const j = await r.json();
        noCb.forEach(t => { const c = j.coins && j.coins["coingecko:" + t.cg]; if (c && c.price) setLive(t.sym, c.price); });
      } catch { }
    })());
    await Promise.all(jobs);
    const ok = Object.keys(live).length > 1;
    $(".pulse").classList.toggle("on", ok);
    $("#m-live-t").textContent = ok ? "live " + new Date().toLocaleTimeString("it-CH", { timeZone: TZ, hour: "2-digit", minute: "2-digit", second: "2-digit" }) : "non raggiungibili";
    if (live.BTC) $("#m-btc").textContent = fmtPrice(live.BTC.price) + " $";
    updateLiveCells();
    if (calcReady) runCalc();
  }

  async function pollDerivs() {
    try {
      const r = await fetch("https://api.hyperliquid.xyz/info", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ type: "metaAndAssetCtxs" }) });
      const [meta, ctx] = await r.json();
      meta.universe.forEach((u, i) => { liveDer[u.name] = parseFloat(ctx[i].funding) * 8 * 100; });
    } catch { }
  }

  function setLive(sym, price) {
    const prev = live[sym] && live[sym].price;
    live[sym] = { price, t: Date.now(), dir: prev == null ? 0 : Math.sign(price - prev) };
  }

  // ------------------------------------------------------------ live flags
  function liveFlag(t, p) {
    const L = t.levels;
    if (p == null) return null;
    if (p < L.invalid) return { txt: "Live: sotto invalidazione", bad: true };
    if (p >= L.zone[0] * 0.99 && p <= L.zone[1] * 1.01) return { txt: "Live: in zona d'ingresso" };
    if (p > L.breakout) return { txt: "Live: sopra il breakout" };
    return null;
  }
  function railPos(t, p) {
    const L = t.levels, lo = L.invalid * 0.92, hi = L.breakout * 1.08;
    const f = (v) => Math.max(0, Math.min(100, (v - lo) / (hi - lo) * 100));
    return { f, mark: f(p) };
  }

  // ------------------------------------------------------------ render
  function renderAll() {
    renderMacro(); renderSummary(); renderTiers(); renderRows(); renderCal(); renderLog(); initCalc();
  }

  function renderMacro() {
    const m = DATA.macro || {};
    if (!live.BTC) $("#m-btc").textContent = m.btc ? fmtPrice(m.btc) + " $" : "—";
    $("#m-btc").title = m.btc_sotto_ema50 ? "BTC sotto EMA50 giornaliera (risk-off)" : "BTC sopra EMA50 giornaliera";
    $("#m-fg").textContent = m.fear_greed != null ? `${m.fear_greed} · ${m.fear_greed_label || ""}` : "—";
    $("#m-dom").textContent = m.btc_dominance != null ? nf(1).format(m.btc_dominance) + "%" : "—";
    const age = (Date.now() / 1000 - DATA.generato_ts) / 60;
    $("#m-scan").textContent = fmtTime(DATA.generato) + (age > 45 ? " (in ritardo)" : "");
  }

  function renderSummary() {
    const act = DATA.tokens.filter(t => BUY.has(t.segnale.stato) || SELL.has(t.segnale.stato));
    const m = DATA.macro || {};
    const regime = m.btc_sotto_ema50 ? "BTC è sotto la media a 50 giorni, quindi il regime è difensivo" : "BTC è sopra la media a 50 giorni";
    const s = $("#summary"); s.textContent = "";
    const f1 = DATA.tokens.filter(t => t.fascia === 1).map(t => t.sym).join(", ");
    if (!act.length) {
      s.append(`Nessun segnale attivo: tutti i nove token sono in attesa. ${regime}. In prima fascia ci sono ${f1}; senza convergenza dei trigger non si opera.`);
      return;
    }
    s.append(`${act.length} ${act.length === 1 ? "segnale attivo" : "segnali attivi"}: `);
    act.forEach((t, i) => {
      if (i) s.append(", ");
      const b = el("b", stClass(t.segnale.stato), `${t.sym} ${STATE_LABEL[t.segnale.stato] || t.segnale.stato}`);
      s.append(b);
    });
    s.append(`. ${regime}. In prima fascia ci sono ${f1}.`);
  }

  function rowVisible(t) {
    if (filter === "attivi") return BUY.has(t.segnale.stato) || SELL.has(t.segnale.stato);
    if (filter === "f1") return t.fascia === 1;
    return true;
  }

  function renderRows() {
    const box = $("#rows"); box.textContent = "";
    const tpl = $("#tpl-row");
    const order = { "USCITA": 0, "ENTRATA": 1, "ACCUMULO": 2, "SETUP TATTICO": 3, "RIDUCI": 4, "ATTESA": 5, "DATI MANCANTI": 6 };
    const list = DATA.tokens.slice().sort((a, b) => (order[a.segnale.stato] ?? 9) - (order[b.segnale.stato] ?? 9) || (a.rank ?? 9) - (b.rank ?? 9));
    list.filter(rowVisible).forEach(t => {
      const node = tpl.content.firstElementChild.cloneNode(true);
      node.dataset.sym = t.sym;
      $(".sym", node).textContent = t.sym;
      const nm = $(".nm", node); nm.textContent = t.name; nm.append(el("span", "fz fz-" + (t.fascia || 3), t.verdetto));
      const sig = t.segnale;
      const stEl = $(".st", node); stEl.className = stClass(sig.stato); stEl.textContent = STATE_LABEL[sig.stato] || sig.stato;
      const sc = sig.score;
      $(".score-n", node).textContent = sc == null ? "—" : (sc > 0 ? "+" : "") + sc;
      $(".score-n", node).title = `Punteggio ${sc}, copertura dati ${sig.copertura}%`;
      const bar = $(".score-bar i", node);
      if (sc != null) {
        const w = Math.min(Math.abs(sc), 6) / 6 * 50;
        bar.style.width = w + "%"; bar.style.left = sc >= 0 ? "50%" : (50 - w) + "%";
        bar.style.background = sc >= 0 ? "var(--buy)" : "var(--exit)";
      }
      const top = (sig.reasons || []).slice().sort((a, b) => Math.abs(b.s) - Math.abs(a.s)).slice(0, 2).map(r => (r.s > 0 ? "+" : "−") + " " + r.txt);
      $(".why", node).textContent = top.join("  ·  ") || "Nessun trigger attivo";
      const btn = $(".row-main", node);
      btn.addEventListener("click", () => toggle(t.sym, node, btn));
      if (open.has(t.sym)) { btn.setAttribute("aria-expanded", "true"); fillDetail(t, $(".detail", node)); $(".detail", node).hidden = false; }
      box.append(node);
    });
    updateLiveCells();
  }

  function updateLiveCells() {
    if (!DATA) return;
    DATA.tokens.forEach(t => {
      const node = document.querySelector(`.row[data-sym="${t.sym}"]`);
      if (!node) return;
      const lp = live[t.sym] ? live[t.sym].price : t.mercato.price;
      const pe = $(".price", node);
      pe.textContent = fmtPrice(lp) + " $";
      pe.classList.remove("flash-up", "flash-dn");
      if (live[t.sym] && live[t.sym].dir && Date.now() - live[t.sym].t < 3000) {
        pe.classList.add(live[t.sym].dir > 0 ? "flash-up" : "flash-dn");
        setTimeout(() => pe.classList.remove("flash-up", "flash-dn"), 1500);
      }
      const ref = t.mercato.last_close;
      const ch = ref ? (lp / ref - 1) * 100 : t.mercato.chg24;
      const ce = $(".chg", node); ce.textContent = fmtPct(ch) + " 24h"; ce.className = "chg " + (ch > 0 ? "up" : ch < 0 ? "dn" : "");
      const fl = liveFlag(t, lp), lf = $(".live-flag", node);
      lf.textContent = fl ? fl.txt : ""; lf.className = "live-flag" + (fl && fl.bad ? " bad" : "");
      const rp = railPos(t, lp), L = t.levels;
      const z = $(".zone", node); z.style.left = rp.f(L.zone[0]) + "%"; z.style.width = Math.max(1.5, rp.f(L.zone[1]) - rp.f(L.zone[0])) + "%";
      $(".mark", node).style.left = rp.mark + "%";
      node.querySelector(".rail").title = `Invalidazione ${fmtPrice(L.invalid)} · zona ${fmtPrice(L.zone[0])}–${fmtPrice(L.zone[1])} · breakout ${fmtPrice(L.breakout)}`;
    });
  }

  function toggle(sym, node, btn) {
    const d = $(".detail", node);
    if (open.has(sym)) { open.delete(sym); d.hidden = true; btn.setAttribute("aria-expanded", "false"); return; }
    open.add(sym);
    fillDetail(DATA.tokens.find(x => x.sym === sym), d);
    d.hidden = false; btn.setAttribute("aria-expanded", "true");
  }

  function kv(dl, k, v) { dl.append(el("dt", null, k), el("dd", null, v)); }

  function spark(t) {
    const s = t.mercato.spark || [];
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("class", "spark"); svg.setAttribute("viewBox", "0 0 300 70"); svg.setAttribute("preserveAspectRatio", "none");
    svg.setAttribute("role", "img"); svg.setAttribute("aria-label", `Prezzo ${t.sym} ultimi ${s.length} giorni`);
    if (s.length < 2) return svg;
    const L = t.levels;
    const lo = Math.min(...s, L.invalid), hi = Math.max(...s, L.breakout);
    const y = v => 66 - (v - lo) / (hi - lo || 1) * 62, x = i => i / (s.length - 1) * 300;
    const ns = "http://www.w3.org/2000/svg";
    const band = document.createElementNS(ns, "rect");
    band.setAttribute("x", 0); band.setAttribute("width", 300); band.setAttribute("y", y(L.zone[1])); band.setAttribute("height", Math.max(1, y(L.zone[0]) - y(L.zone[1])));
    band.setAttribute("fill", "var(--zone)"); svg.append(band);
    [[L.invalid, "var(--exit)"], [L.breakout, "var(--buy)"]].forEach(([v, c]) => {
      const ln = document.createElementNS(ns, "line");
      ln.setAttribute("x1", 0); ln.setAttribute("x2", 300); ln.setAttribute("y1", y(v)); ln.setAttribute("y2", y(v));
      ln.setAttribute("stroke", c); ln.setAttribute("stroke-dasharray", "4 3"); ln.setAttribute("stroke-width", "1"); svg.append(ln);
    });
    const p = document.createElementNS(ns, "polyline");
    p.setAttribute("points", s.map((v, i) => `${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(" "));
    p.setAttribute("fill", "none"); p.setAttribute("stroke", "var(--ink)"); p.setAttribute("stroke-width", "1.6"); p.setAttribute("stroke-linejoin", "round");
    svg.append(p);
    return svg;
  }

  function fillDetail(t, box) {
    box.textContent = "";
    const g = el("div", "dgrid");
    const sig = t.segnale, m = t.mercato, d = t.derivati, oc = t.onchain, L = t.levels;

    // trigger
    const c1 = el("div"); c1.append(el("h4", null, "Trigger del segnale"));
    const ul = el("ul", "reasons");
    (sig.reasons || []).forEach(r => {
      const li = el("li"); li.append(el("span", "s " + (r.s > 0 ? "up" : "dn"), (r.s > 0 ? "+" : "") + r.s), el("span", null, r.txt)); ul.append(li);
    });
    if (!ul.children.length) ul.append(el("li", null, "Nessun trigger attivo in questa scansione."));
    c1.append(ul);
    if (sig.nota_fascia) c1.append(el("p", "note-f", sig.nota_fascia));
    c1.append(el("p", "small", `Stato ${STATE_LABEL[sig.stato] || sig.stato}, punteggio ${sig.score ?? "—"}, copertura dati ${sig.copertura}%. Trigger fondamentale per entrare nel paniere: ${t.trigger_fondamentale}.`));
    g.append(c1);

    // prezzo e livelli
    const c2 = el("div"); c2.append(el("h4", null, "Prezzo e livelli (60 giorni)"), spark(t));
    const dl = el("dl", "kv");
    kv(dl, "Zona d'ingresso", `${fmtPrice(L.zone[0])} – ${fmtPrice(L.zone[1])}`);
    kv(dl, "Supporti", `${fmtPrice(L.s1)} · ${fmtPrice(L.s2)}`);
    kv(dl, "Resistenze", `${fmtPrice(L.r1)} · ${fmtPrice(L.r2)}`);
    kv(dl, "Breakout / invalidazione", `${fmtPrice(L.breakout)} / ${fmtPrice(L.invalid)}`);
    kv(dl, "RSI14 · EMA20 · EMA50", `${m.rsi14 != null ? nf(0).format(m.rsi14) : "—"} · ${fmtPrice(m.ema20)} · ${fmtPrice(m.ema50)}`);
    kv(dl, "Variazione 7g · 30g", `${fmtPct(m.chg7)} · ${fmtPct(m.chg30)}`);
    kv(dl, "Volume vs media 20g", m.vol_ratio != null ? nf(2).format(m.vol_ratio) + "x" : "—");
    c2.append(dl, el("p", "small", `Candele: ${m.fonte_candele || "—"}; prezzo scansione: ${m.fonte_prezzo || "—"}.`));
    g.append(c2);

    // derivati + on-chain
    const c3 = el("div"); c3.append(el("h4", null, "Derivati e on-chain"));
    const dl3 = el("dl", "kv");
    if (d) {
      const lf = t.hl && liveDer[t.hl] != null ? liveDer[t.hl] : d.funding_8h;
      kv(dl3, `Funding (${d.venue}, per 8h)`, lf != null ? nf(4).format(lf) + "%" : "—");
      kv(dl3, "Open interest · 24h", `${fmtUsd(d.oi_usd)} · ${fmtPct(d.oi_chg24)}`);
    } else kv(dl3, "Derivati", "nessun perpetuo liquido");
    if (oc) {
      if (oc.spesa_giorno_usd != null) { kv(dl3, "Spesa rete (giorno)", fmtUsd(oc.spesa_giorno_usd)); kv(dl3, "vs media 7 giorni", fmtPct(oc.spesa_vs_media7_pct)); kv(dl3, "Lease · GPU attive", `${oc.lease_attivi ?? "—"} · ${oc.gpu_attive ?? "—"}/${oc.gpu_totali ?? "—"}`); }
      if (oc.supply != null) { kv(dl3, "Supply", new Intl.NumberFormat("it-CH", { notation: "compact", maximumFractionDigits: 2 }).format(oc.supply)); kv(dl3, "Supply 24h", oc.supply_chg24 != null ? (oc.supply_chg24 > 0 ? "+" : "") + nf(0).format(oc.supply_chg24) : "in raccolta"); }
      if (oc.holder != null) { kv(dl3, "Holder", nf(0).format(oc.holder)); kv(dl3, "Holder 24h · 7g", `${fmtPct(oc.holder_chg24_pct, 2)} · ${fmtPct(oc.holder_chg7_pct, 2)}`); }
      if (oc.afflussi_exchange_usd != null) kv(dl3, `Flussi exchange (${oc.finestra_ore ?? "?"}h)`, `in ${fmtUsd(oc.afflussi_exchange_usd)} · out ${fmtUsd(oc.deflussi_exchange_usd)}`);
      if (oc.partecipanti != null) { kv(dl3, "Partecipanti rete", `${oc.partecipanti} (${fmtPct(oc.partecipanti_chg24_pct)})`); }
    } else kv(dl3, "On-chain", t.sym === "TAO" ? "nessuna fonte gratuita senza chiave" : "non disponibile in questa scansione");
    if (t.fees && t.fees.ricavi_annui_usd != null) kv(dl3, "Ricavi DefiLlama (annui, da 30g)", fmtUsd(t.fees.ricavi_annui_usd));
    c3.append(dl3);
    if (oc && oc.whale && oc.whale.length) {
      const w = el("ul", "whales");
      oc.whale.forEach(x => { const li = el("li"); li.textContent = `${fmtUsd(x.usd)} ${x.verso ? "→ " + x.verso : x.da ? "← " + x.da : "wallet → wallet"} · ${fmtTime(x.ts)}`; w.append(li); });
      c3.append(el("p", "small", "Trasferimenti grandi recenti:"), w);
    }
    g.append(c3);

    // la tesi (dossier)
    const c4 = el("div"); c4.append(el("h4", null, "La tesi: dal cliente al token"));
    const ch = el("div", "chain");
    const STEP = [["utilita", "Utile"], ["domanda", "Pagato"], ["margine", "Margine"], ["utile", "Utile netto"], ["token", "Al token"], ["valutazione", "Prezzo ok"]];
    const LAB = { si: "sì", parziale: "in parte", no: "no", nd: "non provato" };
    STEP.forEach(([k, lab]) => { const v = (t.catena || {})[k] || "nd"; const sp = el("span", "c-" + v); sp.append(lab, el("br"), LAB[v]); sp.title = `${lab}: ${LAB[v]}`; ch.append(sp); });
    c4.append(ch, el("p", "chain-leg", "Verde: provato · giallo: in parte · rosso: no · tratteggio: nessuna prova disponibile"));
    const pm = el("p", "plain"); pm.append(el("b", null, "Perché è qui: "), t.motivo || "—"); c4.append(pm);
    const pl = el("p", "plain"); pl.append(el("b", null, "Il limite: "), t.limite || "—"); c4.append(pl);
    const pu = el("p", "plain"); pu.append(el("b", null, "Sale di fascia se: "), t.promuove || "—"); c4.append(pu);
    const pd = el("p", "plain"); pd.append(el("b", null, "Scende se: "), t.declassa || "—"); c4.append(pd);
    if (t.verifiche) { c4.append(el("p", "small", "Da controllare a ogni trimestre:")); const ul2 = el("ul", "checks"); t.verifiche.forEach(v => ul2.append(el("li", null, v))); c4.append(ul2); }
    c4.append(el("p", "small", `Prove disponibili: ${(t.prove || []).join(", ") || "—"}. Kraken: ${t.kraken || "—"}.`));
    g.append(c4);

    // economia del token al prezzo di oggi
    const e = t.economia || {}, c5 = el("div"); c5.append(el("h4", null, "Economia del token, al prezzo di oggi"));
    const dl5 = el("dl", "kv");
    kv(dl5, "Valore di mercato", fmtUsd(e.mcap_usd));
    kv(dl5, "Ricavi annui", e.ricavi_annui_usd ? fmtUsd(e.ricavi_annui_usd) : "≈ 0");
    kv(dl5, "Valore / ricavi", e.mcap_su_ricavi ? nf(0).format(e.mcap_su_ricavi) + " volte" : "non calcolabile");
    kv(dl5, "Nuovi token l'anno", e.emissioni_annue_usd ? fmtUsd(e.emissioni_annue_usd) + ` (${nf(1).format(e.diluizione_pct)}%)` : "nessuno");
    kv(dl5, "Ricavi / nuovi token", e.copertura_emissioni_pct != null ? nf(0).format(e.copertura_emissioni_pct) + "%" : "—");
    c5.append(dl5);
    const ex = el("p", "plain");
    if (e.diluizione_pct > 0) ex.textContent = `Se il valore di mercato restasse uguale, i nuovi token farebbero scendere il prezzo di circa ${nf(1).format(Math.abs(e.prezzo_a_valore_invariato_pct))}% in un anno. Per restare fermo, il valore deve crescere almeno del ${nf(1).format(e.diluizione_pct)}%.`;
    else ex.textContent = "Nessun nuovo token creato: il prezzo non viene diluito, ma un'offerta fissa non protegge da un calo del valore di mercato.";
    c5.append(ex);
    if (e.mcap_su_ricavi) c5.append(el("p", "small", `Un rapporto valore/ricavi di ${nf(0).format(e.mcap_su_ricavi)} non è un P/E: buona parte dei ricavi paga i fornitori. Indica quanta crescita il prezzo già si aspetta.`));
    c5.append(el("p", "small", `${t.nota_offerta || ""} Fonte ricavi: ${e.fonte_ricavi || "—"}. ${t.fund.nota}`));
    const lk = el("div", "links");
    Object.entries(t.canali).forEach(([k, v]) => { if (!v) return; const a = el("a", null, k); a.href = v; a.target = "_blank"; a.rel = "noopener"; lk.append(a); });
    if (t.release && t.release.length) t.release.forEach(r => { const a = el("a", null, `release ${r.tag}`); a.href = r.url; a.target = "_blank"; a.rel = "noopener"; a.title = fmtTime(r.data); lk.append(a); });
    c5.append(lk);
    g.append(c5);
    box.append(g);
  }


  // ------------------------------------------------------------ tiers
  function renderTiers() {
    const box = $("#tiers"); box.textContent = "";
    const NAMES = { 1: ["Prima fascia", "può dare segnali d'acquisto"], 2: ["Seconda fascia", "solo setup tattici"], 3: ["Da verificare", "nessun acquisto: solo osservazione"] };
    [1, 2, 3].forEach(f => {
      const list = DATA.tokens.filter(t => t.fascia === f).sort((a, b) => a.rank - b.rank);
      if (!list.length) return;
      const row = el("div", "tier"), h = el("div", "tier-h fz-" + f, NAMES[f][0]); h.append(el("small", null, NAMES[f][1]));
      const items = el("div", "tier-list");
      list.forEach(t => {
        const it = el("div", "tier-item");
        it.append(el("span", "tsym", t.sym));
        const a = el("span"); a.append(el("span", "lab", "Perché"), t.motivo || "—");
        const b = el("span"); b.append(el("span", "lab", "Limite"), t.limite || "—");
        const k = el("span", "kr"); k.append(el("span", "lab", "Kraken"), t.kraken || "—");
        it.append(a, b, k); items.append(it);
      });
      row.append(h, items); box.append(row);
    });
    if (DATA.dossier) $("#rank-note").textContent = DATA.dossier.conclusione + " " + DATA.dossier.regola;
  }

  // ------------------------------------------------------------ risk calculator (dossier §16)
  let calcReady = false;
  function initCalc() {
    const sel = $("#c-tok");
    if (!calcReady) {
      DATA.tokens.slice().sort((a, b) => a.rank - b.rank).forEach(t => { const o = el("option", null, `${t.sym} · ${t.verdetto}`); o.value = t.sym; sel.append(o); });
      ["#c-tok", "#c-cap", "#c-cost", "#c-loss"].forEach(id => $(id).addEventListener("input", runCalc));
      calcReady = true;
    }
    runCalc();
  }
  function runCalc() {
    if (!DATA) return;
    const t = DATA.tokens.find(x => x.sym === $("#c-tok").value) || DATA.tokens[0];
    const cap = +$("#c-cap").value || 0, cost = +$("#c-cost").value || 0, loss = +$("#c-loss").value || 0;
    const pos = Math.max(0, cap - cost), dist = pos ? loss / pos : 0;
    const p = live[t.sym] ? live[t.sym].price : t.mercato.price;
    const stop = p * (1 - dist), inv = t.levels.invalid;
    const out = $("#calc-out"); out.textContent = "";
    const add = (k, v) => { const d = el("div", null, k); d.prepend(el("b", null, v)); out.append(d); };
    add("Acquisto spot", fmtUsd(pos));
    add("Distanza dello stop", nf(2).format(dist * 100) + "%");
    add("Prezzo di riferimento", fmtPrice(p) + " $");
    add("Stop dal budget", fmtPrice(stop) + " $");
    add("Quantità indicativa", p ? new Intl.NumberFormat("it-CH", { maximumFractionDigits: p < 1 ? 0 : 2 }).format(pos / p) + " " + t.sym : "—");
    const msg = el("p", "calc-msg");
    if (!pos || !loss) msg.textContent = "Inserisci capitale e perdita massima.";
    else if (stop > inv) msg.textContent = `Lo stop dal budget (${fmtPrice(stop)}) cade sopra l'invalidazione tecnica (${fmtPrice(inv)}): sei protetto prima che l'idea venga smentita. Se vuoi dare spazio fino a ${fmtPrice(inv)}, riduci l'importo a circa ${fmtUsd(loss / (1 - inv / p))}.`;
    else msg.textContent = `Lo stop dal budget (${fmtPrice(stop)}) è sotto l'invalidazione tecnica (${fmtPrice(inv)}): l'idea viene smentita prima che tu raggiunga la perdita massima.`;
    if (t.fascia === 3) { msg.classList.add("warn"); msg.textContent += " Attenzione: questo token è 'da verificare' e non genera segnali d'acquisto."; }
    if (t.kraken === "Non disponibile") msg.textContent += " Non è quotato su Kraken.";
    msg.append(" Uno stop esegue al mercato: in un crollo il prezzo effettivo può essere peggiore. Il budget vale per l'intera iniziativa, non per ogni nuovo tentativo.");
    out.append(msg);
  }

  // ------------------------------------------------------------ calendar
  function renderCal() {
    const sel = $("#cal-tok");
    if (sel.options.length === 1) {
      [...new Set(DATA.catalizzatori.map(e => e.token))].forEach(tk => { const o = el("option", null, tk); o.value = tk; sel.append(o); });
    }
    const tok = sel.value, past = $("#cal-past").checked, today = new Date().toISOString().slice(0, 10);
    const list = DATA.catalizzatori.filter(e => (!tok || e.token === tok) && (past || !e.data || e.data >= today))
      .sort((a, b) => (!a.data) - (!b.data) || (a.data < b.data ? -1 : a.data > b.data ? 1 : 0));
    const ol = $("#cal"); ol.textContent = "";
    if (!list.length) { ol.append(el("li", "empty", "Nessun evento per questo filtro.")); return; }
    list.forEach(e => {
      const li = el("li", (e.impatto === "Alto" ? "alto" : "") + (e.data && e.data < today ? " past" : ""));
      li.append(el("span", "when", fmtDate(e.data)), el("span", "tok", e.token), el("span", null, e.evento),
        el("span", "eff " + (e.effetto > 0 ? "up" : e.effetto < 0 ? "dn" : ""), e.effetto ? (e.effetto > 0 ? "+1 al segnale" : "−1 al segnale") : ""));
      const meta = el("span", "meta", `${e.tipo} · impatto ${e.impatto.toLowerCase()} · ${e.stato.toLowerCase()} · `);
      const a = el("a", null, "fonte"); a.href = e.fonte; a.target = "_blank"; a.rel = "noopener"; meta.append(a);
      li.append(meta); ol.append(li);
    });
  }

  // ------------------------------------------------------------ log
  function renderLog() {
    const p = DATA.paper || {}, box = $("#paper"); box.textContent = "";
    [["Posizioni aperte", p.aperti ?? 0], ["Chiuse", p.chiusi ?? 0], ["Win rate", p.win_rate != null ? nf(1).format(p.win_rate) + "%" : "—"],
     ["Rendimento medio", fmtPct(p.rend_medio_pct)], ["P&L su 100 $ a segnale", p.pnl_100_usd != null ? nf(2).format(p.pnl_100_usd) + " $" : "—"]]
      .forEach(([k, v]) => { const d = el("div", null, k); d.prepend(el("b", null, String(v))); box.append(d); });
    const tb = $("#log tbody"); tb.textContent = "";
    const rows = (LOG.segnali || []).slice().reverse();
    if (!rows.length) { const tr = el("tr"); const td = el("td", "empty", "Ancora nessun segnale: la storia si riempie da sola a ogni scansione."); td.colSpan = 9; tr.append(td); tb.append(tr); return; }
    rows.slice(0, 80).forEach(s => {
      const tr = el("tr");
      const cur = live[s.token] && s.esito === "APERTO" ? live[s.token].price : s.ultimo;
      const ret = s.esito === "APERTO" ? (cur / s.prezzo - 1) * 100 : s.rend_pct;
      tr.append(el("td", null, fmtTime(s.data)), el("td", null, s.token));
      const td = el("td"); td.append(el("b", stClass(s.stato), STATE_LABEL[s.stato] || s.stato)); tr.append(td);
      tr.append(el("td", "n", fmtPrice(s.prezzo)), el("td", "n", fmtPrice(cur)), el("td", "n " + (ret > 0 ? "up" : ret < 0 ? "dn" : ""), fmtPct(ret)),
        el("td", "n", fmtPct(s.mfe_pct)), el("td", "n", fmtPct(s.mae_pct)),
        el("td", null, s.esito === "APERTO" ? "aperto" : `chiuso: ${s.motivo_chiusura}`));
      tb.append(tr);
    });
  }

  // ------------------------------------------------------------ wiring
  document.querySelectorAll(".chip").forEach(b => b.addEventListener("click", () => {
    document.querySelectorAll(".chip").forEach(x => x.classList.remove("on")); b.classList.add("on");
    filter = b.dataset.f; if (DATA) renderRows();
  }));
  $("#cal-tok").addEventListener("change", () => DATA && renderCal());
  $("#cal-past").addEventListener("change", () => DATA && renderCal());

  (async () => {
    await loadData();
    pollLive(); pollDerivs();
    setInterval(pollLive, 15000);
    setInterval(pollDerivs, 60000);
    setInterval(loadData, 120000);
  })();
})();
