# AI Crypto Radar

Segnali sistematici di entrata e uscita su nove token AI: **TAO, AKT, RENDER, IO, VIRTUAL, ATH, GNK, TRAC, RLC**.

Sito live: https://onlywave.github.io/ai-crypto-radar/

## Come funziona

- **Nel browser** i prezzi arrivano live ogni 15 secondi da Coinbase (DefiLlama per GNK), il funding da Hyperliquid ogni minuto. La rotaia di ogni token mostra il prezzo tra l'invalidazione, la zona d'ingresso e il breakout.
- **Ogni 15 minuti** GitHub Actions esegue `scripts/collect.py`:
  - prezzi e candele giornaliere: Coinbase, poi Kraken, poi DefiLlama;
  - derivati: Hyperliquid e OKX;
  - on-chain: Blockscout (holder e flussi verso exchange etichettati), Solana RPC (supply RENDER e IO), Akash Console (spesa sulla rete), nodo Gonka (supply e partecipanti);
  - ricavi DefiLlama, release GitHub, Fear & Greed, dominance BTC.

  Poi calcola il punteggio e lo stato di ogni token e scrive `data/latest.json`, `data/history.json` e `data/signals_log.json`.
- **Stati possibili**: Entrata, Accumulo, Setup tattico, Attesa, Riduci, Uscita. Le regole sono descritte sul sito e in `scripts/collect.py` (funzione `evaluate`).
- **Storico**: ogni segnale di acquisto apre una posizione simulata da 100 $. Si chiude su segnale di uscita, sullo stop, sul trailing stop o dopo 30 giorni. Per ogni posizione vengono registrati MFE e MAE.

## Configurazione

- `config/tokens.json`: per ogni token ci sono i livelli (zona, supporti, resistenze, breakout, invalidazione), il gate del paniere (`core`, `watch`, `excluded`), il filtro ricavi e le fonti.
- `config/catalysts.json`: upgrade, unlock e voti di governance, ciascuno con un effetto sul punteggio.

Per ricevere avvisi su Telegram aggiungi i secret `TELEGRAM_BOT_TOKEN` e `TELEGRAM_CHAT_ID` del repository: il workflow li usa da solo.

Strumento di analisi, non consulenza finanziaria personalizzata.
