# PROJECT_AUDIT.md

**Repository:** `Riffmax-TradingBot-AI-RiffEx`
**Data audit:** 12/08/2026
**Fase:** PHASE 0 — Repository Audit
**Stato codice:** nessuna modifica alla logica di trading durante questa fase

> Convenzioni: `UNKNOWN` = informazione non presente nel repository.
> `REQUIRES VERIFICATION` = ipotesi non ancora provata empiricamente.

---

## A. Project overview

Repository **clonato da terzi** (`github.com/Riffmax2030-hub/Riffmax-TradingBot-AI-RiffEx`, autore Akanmu Sherif Olaide, ultimo commit upstream 28/07/2026), adottato come base di partenza invece di scrivere il bot da zero.

Contiene **due sistemi indipendenti** che condividono solo la cartella. Non comunicano, non condividono configurazione, non condividono modelli di dati:

| Sistema | Chi genera il segnale | Stato operativo |
|---|---|---|
| **A. Webhook Bridge** | TradingView (Pine Script) | **NON funzionante** (dipendenze assenti) |
| **B. AlphaEdge** | Python locale | **Funzionante**, sola lettura |

Il sistema realmente in uso è **B (AlphaEdge)**. Il sistema A è materiale di lettura.

**Totale codice:** 2.037 righe (16 file tracciati).

---

## B. Technology stack

| Componente | Versione | Stato |
|---|---|---|
| Python | 3.12.1 | installato |
| MetaTrader5 (pacchetto) | 5.0.6090 | installato |
| pandas | 3.0.3 | installato |
| numpy | 2.5.0 | installato |
| Terminale MT5 | build 5956 | installato, `C:\Program Files\MetaTrader 5` |
| fastapi / uvicorn / pydantic | — | **NON installati** |
| metatrader_client | — | **NON installato** |

`requirements.txt` dichiara `MetaTrader5`, `pandas`, `numpy`: copre AlphaEdge ma **non** il Webhook Bridge, che importa `fastapi` e `metatrader_client`.

Nessun gestore di ambienti virtuali nel repo. Nessun `pyproject.toml`, nessun lock file.

---

## C. Directory structure

```text
Riffmax-TradingBot-AI-RiffEx/
├── alphaedge.py                      615   SISTEMA B — motore strategia
├── run_autonomous_scanner.py          62   SISTEMA B — daemon 120s
├── approve_alphaedge_trade.py         14   SISTEMA B — approvazione manuale
├── telegram_commands.py               94   SISTEMA B — controllo remoto
├── webhook_bridge.py                 284   SISTEMA A — server FastAPI
├── ut_bot_strategy.pine              141   SISTEMA A — strategia TradingView
├── tradenow.py                       183   orfano, non importato da nessuno
├── tradenow1.py                      200   orfano, non importato da nessuno
├── requirements.txt / .env.example / .gitignore
├── README.md / AGENT_PROMPT.md
├── .agents/
│   ├── AGENTS.md                          regole di disciplina (prosa, non codice)
│   └── trading_bot_skills/
│       ├── indicators.py              52   BB, RSI, EMA, ATR, S/R
│       ├── risk.py                   129   solo assess_risk() e' raggiungibile
│       ├── trade_config.py            36   costanti + Telegram
│       └── __init__.py                 1
└── utilities/                        226   4 script diagnostici usa-e-getta
```

Nessuna directory `tests/`, `backtest/`, `data/`, `knowledge/`, `strategies/`.

---

## D. Current architecture

**Non esiste un'architettura modulare.** `alphaedge.py` è uno script monolitico di 615 righe che contiene, mescolate nello stesso file: configurazione, connessione, alert Telegram, sizing, logging su CSV, analisi della strategia, gestione posizioni, stampa e invio ordini.

L'unica separazione reale è il pacchetto `trading_bot_skills`, che isola gli indicatori e alcune costanti.

Non esistono: interfacce, dependency injection, strato di astrazione sul broker, separazione fra generazione del segnale ed esecuzione, configurazione esterna (i parametri sono costanti nel modulo).

---

## E. Trading flow

Flusso reale del sistema B, tracciato nel codice:

```text
MARKET DATA        mt5.copy_rates_from_pos()          alphaedge.py:143,164,198,232
    |                (M30, M5, H1, D1)
DATA QUALITY       warm_up_history()                  alphaedge.py:100-130
    |                (aggiunto durante questa sessione)
ANALYSIS           analyze_structural_edge()          alphaedge.py:191-351
    |                BB + RSI + EMA + ATR + S/R
SIGNAL             imbuto a 4 filtri                  alphaedge.py:302-306, 326
    |                M30 -> M5 -> D1 -> R:R >= 1.5
FILTER             correlazione oro/argento           alphaedge.py:468-484
    |
RISK               solo guardrail giornaliero         alphaedge.py:348
    |                (nessun position sizing)
ORDER              mt5.order_send() BUY/SELL_LIMIT    alphaedge.py:505
    |                pullback 0.25xATR, scadenza 2h
MT5                FTMO-Demo 1514232107
    |
POSITION           trailing stop a 1.0xATR            alphaedge.py:358-402
    |                blocca profitto a 0.5xATR
EXIT               SL / TP lato broker                (nessuna logica di uscita propria)
```

**Anelli mancanti rispetto al target:** feature engineering centralizzato, market regime detection, liquidity, VWAP, ORB, sessioni, order flow, confluence scoring, AI, signal validation, position sizing, decision trace.

---

## F. TradingView integration

**Stato: NON FUNZIONANTE.**

Architettura prevista: `Pine Script alert -> ngrok -> FastAPI :5001 -> MT5`.

- `ut_bot_strategy.pine` — strategia Pine v6 (UT Bot: ATR trailing stop + incrocio EMA). Scritta correttamente, con SL/TP da ATR, filtri di sessione e cooldown. **Non è mai stata caricata su TradingView** in questa configurazione: `REQUIRES VERIFICATION`.
- `webhook_bridge.py` — server FastAPI. Non avviabile: `fastapi`, `uvicorn` e `metatrader_client` non sono installati.
- L'URL ngrok nella documentazione era quello dell'autore originale, rimosso.

Esiste inoltre un canale TradingView **separato e non collegato a questo repo**: un MCP che permette a Claude di leggere i grafici di TradingView Desktop. Non è integrato con AlphaEdge in alcun modo.

---

## G. MT5 integration

**Stato: FUNZIONANTE.**

Connessione tramite `connect_mt5()` (`alphaedge.py:334`): se `MT5_LOGIN` è valorizzato nel `.env` fa login esplicito, altrimenti si aggancia al terminale già aperto e loggato. Il `.env` è attualmente vuoto per scelta.

Conto in uso: **FTMO-Demo, login 1514232107, USD, saldo 10.000, leva 1:100, `trade_mode = DEMO` verificato.**

Specifiche simboli rilevate: `volume_min = 0.01`, `volume_step = 0.01`, `trade_stops_level = 0` (nessuna distanza minima imposta per SL/TP), `filling_mode = 3` (FOK e IOC entrambi ammessi).

**Algo Trading nel terminale: `trade_allowed = False`.** Blocco totale: nessun ordine può partire finché non viene attivato manualmente.

Chiamate MT5 in scrittura presenti nel codice attivo: `TRADE_ACTION_PENDING` (nuovo ordine), `TRADE_ACTION_SLTP` (trailing), `symbol_select` (Market Watch).

---

## H. AI integration

**Stato: INESISTENTE.**

Nessuna chiamata a LLM, nessun client API, nessuna dipendenza AI, nessun prompt eseguito da codice. Il nome del repository contiene "AI" ma non c'è alcun componente di intelligenza artificiale.

`AGENT_PROMPT.md` è un prompt **da incollare a mano** in un assistente; descrive inoltre il sistema A sulla macchina dell'autore originale ed è **obsoleto**.

Rispetto all'architettura target (AI Analysis Engine con output strutturato e validato, incapace di bypassare il Risk Engine): **il componente va costruito da zero.**

---

## I. Strategy engine

Non esiste un motore multi-strategia. Esiste **una** strategia, cablata nel codice.

**AlphaEdge — mean reversion multi-timeframe** (`alphaedge.py:191`):

| Filtro | Timeframe | Condizione |
|---|---|---|
| 1 | M30 | `close <= BB_lower` **o** `low <= supporto + 0.5xATR`, **e** `RSI <= 32` (speculare a 68 per lo short) |
| 2 | M5 | incrocio EMA5/EMA13 nelle ultime 3 candele |
| 3 | D1 | `close > EMA20 > EMA50` (long) / `close < EMA20 < EMA50` (short) |
| 4 | — | `reward / risk >= 1.5` |

Ingresso: ordine **limite** a `prezzo -/+ 0.25xATR`, scadenza 7200s. SL: `supporto - 1.0xATR`. TP: `resistenza - 0.2xATR`.

**Il filtro H1** (`alphaedge.py:257-289`) calcola `h1_trend_aligned_buy/sell` su ~35 righe e **non li usa mai** nella decisione: finiscono solo nella stringa di log. Codice morto funzionale.

`tradenow.py` / `tradenow1.py` contengono una seconda logica (EMA9/EMA21 + RSI su M5, apertura di "basket" su 5 simboli) **non importata da nessuno**. `tradenow.py:47` apre BUY come fallback quando mancano i dati.

Nessun registro strategie, nessun versionamento, nessuna configurazione esterna: tutte le soglie sono numeri magici inline.

---

## J. Risk management

**Non esiste un Risk Engine.** Esistono due controlli sparsi nel flusso.

**Presente:**
- Guardrail giornaliero (`alphaedge.py:348`): se il P&L **realizzato** di oggi <= -$5 o >= +$50, non apre nuove posizioni.
- SL e TP obbligatori: ogni ordine li include.
- Un solo trade per simbolo (`alphaedge.py:494`).
- Filtro correlazione oro/argento.
- Trailing stop a protezione del profitto.

**Assente:**
- **Position sizing.** `get_lot_size()` (`alphaedge.py:150`) accetta `sl_price` ed `entry_price` e **li ignora**: ritorna sempre `volume_min`.
- `assess_risk()` viene chiamato con `risk_level="neutral"`, che (`risk.py:39-41`) restituisce SL e TP invariati: **è un no-op**.
- max weekly loss, max drawdown, max posizioni aperte, esposizione correlata, limite di spread, limite di slippage, kill switch, emergency close: **nessuno**.

**Misura del buco, con ATR reale del 12/08/2026 e lotto minimo:**

| Simbolo | ATR M30 | $/punto | Rischio stimato per trade |
|---|---|---|---|
| XAUUSD | 15,44 | 1,00 | **~$27,01** |
| BTCUSD | 85,17 | 0,01 | ~$1,49 |
| EURUSD | 0,0005 | 1000 | ~$0,80 |
| US100.cash | 37,34 | 0,01 | ~$0,65 |
| US30.cash | 30,69 | 0,01 | ~$0,54 |

**L'oro rischia ~40 volte il Nasdaq a parità di lotto.** Un singolo stop su XAUUSD (-$27) supera di 5 volte il limite giornaliero di $5, che non può impedirlo perché verifica il P&L *prima* di aprire, non durante.

---

## K. Execution

`mt5.order_send()` diretto, senza strato di astrazione.

**Assente:** idempotenza, protezione duplicati (l'unico controllo è "esiste già una posizione su questo simbolo"), retry, gestione timeout, gestione fill parziali, gestione riconnessione, controllo di spread e slippage pre-invio.

**Punti da verificare con un ordine reale (`REQUIRES VERIFICATION`):**

1. **Il campo `comment`.** AlphaEdge riconosce le proprie posizioni con `comment == "ALPHAEDGE_TRADE"` (`alphaedge.py:326,362,494`). Molti broker sovrascrivono o troncano quel campo. Se FTMO lo fa, **trailing stop e guardrail non riconoscerebbero mai i propri trade.** Nessun `magic number` è impostato, che sarebbe il metodo corretto.
2. Se FTMO accetta `ORDER_TIME_SPECIFIED` con scadenza a 7200s.
3. Nessun `type_filling` è specificato nella richiesta di ordine pendente.

Il guardrail cerca inoltre il commento `"SHERIFNEW_UT"` (`alphaedge.py:344`), che **non viene scritto da nessuna parte** nel repo: residuo dell'autore originale.

---

## L. Position management

Solo **trailing stop**: quando il profitto raggiunge 1.0xATR, sposta lo SL a +0.5xATR dall'ingresso (`alphaedge.py:358-402`).

Assenti: uscite parziali, time exit, uscita per invalidazione del setup, configurabilità per strategia. L'uscita è interamente delegata a SL/TP lato broker.

**Nota:** il blocco trailing è protetto solo da `execute_orders`, **non** da `approved_symbols`. Eseguire `approve_alphaedge_trade.py SIMBOLO` autorizza un nuovo ordine su quel simbolo **e in più** modifica gli stop di tutte le posizioni AlphaEdge aperte.

---

## M. Database

**Nessun database.** Nessun SQL, nessun SQLite, nessun ORM, nessuno schema.

Unica persistenza: `log_trade()` (`alphaedge.py:167`) appende una riga a `trade_log.csv` — registra solo le **aperture**, mai le chiusure né il P&L. Il file è in `.gitignore`. Non esiste storico delle performance.

---

## N. Logging

`logging` standard su console + `alphaedge_trading.log`, formato testo.

Non esiste logging strutturato né **Decision Trace**. Il motivo di scarto di un simbolo esiste solo come stringa di prosa nella tabella a video. Non è possibile rispondere a posteriori a "perché il bot ha aperto questa posizione", perché nulla viene persistito in forma interrogabile.

---

## O. Testing

**Nessun test.** Nessun `test_*.py`, `conftest.py`, `pytest.ini`, `tox.ini`, nessuna directory `tests/`, nessuna CI (`.github/` assente).

Copertura: **0%**. Il Risk Engine — che secondo la specifica dovrebbe avere i test più approfonditi — non esiste e quindi non è testato.

---

## P. Backtesting

**Nessun backtest.** Nessun engine, nessun dato storico salvato, nessuna metrica di performance, nessuna validazione out-of-sample, nessun walk-forward.

L'unico backtest possibile oggi è lo Strategy Tester di TradingView sul Pine Script del sistema A, che però implementa una strategia **diversa** da AlphaEdge.

**Baseline (richiesta da §27 MASTER / §45 ARCHITECTURE): NON ESISTENTE.** Operazioni eseguite dal bot: **0**. Nessun dato per calcolare profit factor, expectancy, drawdown, Sharpe, Sortino o win rate. Ogni futura modifica al momento **non è confrontabile con nulla**.

---

## Q. Security

**Risolto in questa sessione (commit `d207683`):** le credenziali MT5 dell'autore originale erano in chiaro in 8 file. Ora tutti i `MT5_CONFIG` leggono da variabili d'ambiente con default vuoti. Rimossi anche percorsi assoluti verso il PC dell'autore e riferimenti ad account/server/URL ngrok nella documentazione.

**NON risolvibile da qui:** quelle credenziali restano in **39 commit della history pubblica su GitHub**. Riscrivere la history non le rimuoverebbe dal remoto di terzi. Rimedio di competenza dell'autore originale: cambiare la password dell'account.

**Stato attuale:** nessun segreto nei sorgenti né nella documentazione (verificato). `.env` presente ma vuoto, correttamente in `.gitignore`. Nessuna chiave API di alcun tipo (non essendoci componenti AI). Algo Trading disattivato nel terminale.

**Residuo:** `tradenow.py`, `tradenow1.py`, `utilities/`, `webhook_bridge.py` sono stati ripuliti dalle credenziali ma non revisionati funzionalmente. Sono materiale di lettura, **non vanno eseguiti**.

---

## R. Technical debt

| # | Debito | File | Gravità |
|---|---|---|---|
| 1 | Nessun position sizing: rischio per trade variabile ~40x fra simboli | alphaedge.py:150 | **Alta** |
| 2 | `comment` come identificatore di proprietà invece del `magic number` | alphaedge.py:326,494 | **Alta** |
| 3 | Filtro H1 calcolato e mai usato (~35 righe morte) | alphaedge.py:257-289 | Media |
| 4 | `assess_risk()` no-op che simula risk management | risk.py:39-41 | Media |
| 5 | Monolite da 615 righe senza separazione dei livelli | alphaedge.py | Media |
| 6 | Soglie come numeri magici inline (32, 68, 0.25, 1.5, 7200...) | alphaedge.py | Media |
| 7 | RSI con media semplice invece dello smoothing di Wilder: i valori non coincidono con TradingView | indicators.py:12-19 | Media |
| 8 | Riferimento a `"SHERIFNEW_UT"` inesistente nel guardrail | alphaedge.py:344 | Bassa |
| 9 | Metà di `risk.py` irraggiungibile dopo la rimozione di trade_executor | risk.py:45-129 | Bassa |
| 10 | Sistema A non avviabile e non allineato a requirements.txt | webhook_bridge.py | Bassa |
| 11 | `tradenow*.py` orfani, con fallback che apre BUY senza dati | tradenow.py:47 | Bassa |
| 12 | `AGENT_PROMPT.md` descrive un sistema obsoleto | AGENT_PROMPT.md | Bassa |

---

## S. Missing components

Rispetto all'architettura target (§40 ARCHITECTURE), presente vs assente:

| Componente | Stato |
|---|---|
| Market Data | ✅ presente (MT5) |
| Technical Analysis | 🟡 parziale (BB, RSI, EMA, ATR, S/R) |
| Data Quality | 🟡 parziale (warm-up storico) |
| Market Structure (BOS/CHoCH/MSS) | ❌ assente |
| Liquidity / Sweeps | ❌ assente |
| VWAP | ❌ assente |
| ORB | ❌ assente |
| Session Analysis | 🟡 solo distinzione weekend/settimana |
| Order Flow | ❌ assente — **il feed MT5 non fornisce delta/footprint**: `REQUIRES VERIFICATION` su quali dati siano realmente ottenibili |
| Market Regime Detection | ❌ assente |
| Feature Engineering centralizzato | ❌ assente |
| Setup Detection Engine | 🟡 imbuto a 4 filtri cablato |
| Confluence Engine (scoring) | ❌ assente — la decisione è binaria |
| AI Analysis Engine | ❌ assente |
| Signal Validation | ❌ assente |
| Risk Engine | 🟡 due controlli sparsi |
| Position Sizing | ❌ assente |
| Execution Engine | 🟡 order_send diretto, non idempotente |
| Position Management | 🟡 solo trailing |
| Performance DB | ❌ assente |
| Backtesting | ❌ assente |
| Walk-Forward | ❌ assente |
| Optimization | ❌ assente |
| Decision Trace | ❌ assente |
| Monitoring / watchdog / heartbeat | ❌ assente |
| Kill switch / emergency close | 🟡 solo `/pause` via Telegram (file `alphaedge.paused`) |
| Knowledge Base | ❌ assente |
| Discord Intelligence Layer | ❌ assente |
| Multi-strategy / Portfolio Risk | ❌ assente |

---

## T. Critical risks

**R1 — Rischio per trade non controllato.** Al lotto minimo XAUUSD rischia ~$27 contro ~$0,65 di US100.cash. Il limite giornaliero di $5 è inefficace perché verifica solo prima dell'apertura. *Impatto:* perdite molto superiori a quelle progettate. *Mitigazione:* position sizing basato sul rischio prima di qualsiasi ordine.

**R2 — Identificazione delle proprie posizioni non verificata.** Se FTMO sovrascrive il `comment`, il bot non riconosce i propri trade: il trailing stop non scatta, il guardrail conta zero, e posizioni aperte restano senza gestione. *Mitigazione:* usare il `magic number`; verificare con un ordine di test.

**R3 — Nessuna baseline, nessun backtest.** La strategia non è mai stata validata. Non esiste evidenza che AlphaEdge abbia un edge. *Mitigazione:* backtest + walk-forward prima di qualsiasi capitale, anche demo prolungato.

**R4 — Esecuzione non idempotente.** Un retry o un doppio avvio del daemon può generare ordini duplicati. Non esiste lock di processo. *Mitigazione:* magic number + deduplica + lock file.

**R5 — Zero test su codice che muove denaro.** Ogni modifica è una scommessa. *Mitigazione:* test almeno su sizing, guardrail ed esecuzione.

**R6 — Dipendenza dall'ambiente locale.** Il bot vive solo con PC acceso, MT5 aperto e script in esecuzione. Nessun watchdog: se lo script muore con posizioni aperte, il trailing stop smette di funzionare silenziosamente.

**R7 — Credenziali di terzi in history pubblica.** Non sanabile da qui.

---

## U. Recommended next steps

Ordine derivato dalle priorità §48 ARCHITECTURE (P0 Safety → P1 Correctness → …).

**P0 — Safety (prima di qualsiasi ordine, anche demo)**

1. Position sizing deterministico: lotto calcolato da equity, rischio %, distanza dello stop, `trade_contract_size` e `volume_step` del simbolo. Chiude R1.
2. Sostituire `comment` con `magic number` come identificatore. Chiude R2.
3. Ordine di test singolo su US100.cash (rischio ~$0,65) per verificare i tre punti `REQUIRES VERIFICATION` della sezione K.
4. Protezione duplicati + lock di processo. Chiude R4.

**P1 — Correctness**

5. Test su sizing, guardrail e costruzione dell'ordine. Chiude R5.
6. Decidere il destino del filtro H1: collegarlo o rimuoverlo.
7. RSI con smoothing di Wilder, per allinearsi a TradingView.

**P2 — Validation (prerequisito a ogni discorso di strategia)**

8. Motore di backtest con spread, commissioni, slippage e sessioni.
9. Baseline di AlphaEdge sui dati storici: è l'unico modo per sapere se le modifiche future migliorano o peggiorano.

**P3 — Intelligence**

10. PHASE 1: ricerca dell'integrazione Discord (`DISCORD_INTEGRATION_PLAN.md`).

---

## File creati / modificati in questa fase

**Creati:** `PROJECT_AUDIT.md` (questo documento).

**Modificati:** nessuno. La logica di trading non è stata toccata durante PHASE 0.

*(Le modifiche ai commit `d207683`, `5e6b783`, `eaa153b`, `3e91733`, `8f2b2d3`, `e4b7f32` sono precedenti all'adozione di questo protocollo e sono documentate nelle sezioni Q e R.)*
