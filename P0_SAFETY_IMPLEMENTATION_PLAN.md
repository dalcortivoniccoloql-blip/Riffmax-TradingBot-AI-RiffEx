# P0_SAFETY_IMPLEMENTATION_PLAN.md

**Repository:** `Riffmax-TradingBot-AI-RiffEx`
**Data:** 12/08/2026
**Fase:** P0 — Safety (precede PHASE 1 Discord)
**Modalità:** `SAFE DEVELOPMENT MODE` — nessun ordine reale, nemmeno di test

---

## Vincoli di questa fase

| # | Vincolo | Come viene rispettato |
|---|---|---|
| 9 | Non collegare al live trading | `DRY_RUN = True` come default nel codice; `order_send()` irraggiungibile senza disattivarlo esplicitamente. Algo Trading resta OFF nel terminale. |
| 10 | Non modificare la strategia | `analyze_structural_edge()` non viene toccata. Soglie, filtri e timeframe restano identici. Nessuna modifica pensata per migliorare la performance. |
| — | Nessun ordine reale | Sostituito da `mt5.order_check()`, che valida la richiesta **contro il server** senza inviarla (API verificata come presente). |

**Conseguenza da accettare:** senza inviare un ordine reale, due punti dell'audit
restano `UNVERIFIED`: se FTMO accetta `ORDER_TIME_SPECIFIED` e quale `type_filling`
richiede. `order_check()` restituisce il `retcode` del server e dovrebbe intercettare
entrambi, ma **non è garantito che validi esattamente tutto ciò che valida `order_send()`**:
`REQUIRES VERIFICATION`. Il terzo punto (campo `comment`) viene invece **risolto
alla radice** da P0-2, che smette di dipendere da quel campo.

---

# P0-1 — Position sizing deterministico

### Problema

Il rischio per trade varia di ~40 volte fra un simbolo e l'altro. Il limite
giornaliero di $5 è inefficace: un singolo stop su XAUUSD lo supera di 5 volte.

### Causa

`get_lot_size()` (`alphaedge.py:150`) accetta `sl_price` ed `entry_price` e li
ignora: ritorna sempre `symbol_info.volume_min`. Il lotto è quindi deciso dal
broker, non dal rischio.

### Soluzione proposta

Nuovo modulo `trading_bot_skills/sizing.py` con una **funzione pura**, senza
chiamate a MT5, quindi testabile senza terminale:

```python
def calcola_lotto(distanza_stop, tick_size, tick_value, vol_min, vol_max, vol_step, rischio_usd) -> float
```

Formula: `rischio_per_lotto = (distanza_stop / tick_size) * tick_value`, poi
`lotto = rischio_usd / rischio_per_lotto`, **arrotondato per difetto** allo
`volume_step`, limitato a `[vol_min, vol_max]`.

Si usa `trade_tick_value_loss` (non `trade_tick_value`): è il valore conservativo,
e MT5 lo restituisce **già convertito in valuta conto** anche per simboli con
profitto in altra valuta (verificato su GBPJPY: profitto in JPY, tick value in USD).

**Regola critica:** se il lotto calcolato è inferiore a `volume_min`, la funzione
ritorna `0.0` e il trade **non viene aperto**. Non deve mai ripiegare sul lotto
minimo — è esattamente il bug attuale.

### ⚠️ Conseguenza operativa da approvare

Calcolato sull'ATR reale del 12/08/2026, ecco il rischio **minimo inevitabile** per
simbolo (al lotto minimo, stop ≈ 1.75×ATR):

| Fascia | Simboli | Rischio minimo |
|---|---|---|
| Sotto $1,50 | US500.cash, LTCUSD, SOLUSD, US30.cash, EURGBP, GER40.cash, **US100.cash ($0,49)**, XRPUSD, USDCAD, EURUSD, ETHUSD, USOIL.cash, USDCHF, USDJPY, GBPUSD, AUDUSD, GBPJPY | $0,08 – $1,38 |
| Sopra $2 | BTCUSD | $2,12 |
| **Fuori portata** | **XAUUSD** | **$16,89** |
| **Fuori portata** | **XAGUSD** | **$38,59** |

**Con un budget di rischio di $2 per trade, 18 simboli su 20 restano operativi, ma
oro e argento diventano non tradabili.** Non è un difetto dell'implementazione: è
il lotto minimo del broker che impone un rischio superiore al budget. Il codice
farà la cosa corretta — rifiutare il trade — invece di aprirlo comunque.

Le alternative sono: alzare il budget di rischio, oppure rimuovere XAUUSD e XAGUSD
dalla watchlist. **Richiede una tua decisione** (vedi "Decisioni aperte").

### File coinvolti

- `trading_bot_skills/sizing.py` *(nuovo)*
- `alphaedge.py` — `get_lot_size()` diventa un adattatore che legge le specifiche da MT5 e delega alla funzione pura
- `trading_bot_skills/trade_config.py` — nuova costante `RISCHIO_PER_TRADE_USD`

### Test necessari

- Tabella di casi attesi sui tre profili verificati (XAUUSD, US100.cash, EURUSD) con valori di tick reali
- `distanza_stop = 0` → ritorna `0.0`, nessuna divisione per zero
- Lotto calcolato sotto `volume_min` → ritorna `0.0` (caso XAUUSD)
- Lotto calcolato sopra `volume_max` → limitato a `volume_max`
- Arrotondamento sempre per difetto, mai per eccesso
- Test di proprietà: il rischio effettivo non supera mai `rischio_usd`

### Rischi della modifica

- **Riduzione drastica dei trade possibili.** Con budget stretto molti segnali verranno rifiutati. È l'effetto voluto, ma va compreso prima.
- `trade_tick_value_loss` è fornito dal broker: se sbagliato, il sizing è sbagliato. Mitigazione: confronto incrociato con `mt5.order_calc_profit()` nei test di integrazione.
- Su simboli con valuta di profitto diversa il valore può variare col cambio: il lotto va ricalcolato al momento del segnale, mai memorizzato.

---

# P0-2 — Magic number al posto di `comment`

### Problema

Il bot identifica le proprie posizioni confrontando `comment == "ALPHAEDGE_TRADE"`.
Molti broker sovrascrivono o troncano quel campo. Se accade, trailing stop e
guardrail non riconoscono i propri trade: le posizioni restano senza gestione.

### Causa

La richiesta d'ordine (`alphaedge.py:505`) non imposta `magic`. Il filtro su
`comment` compare in `alphaedge.py:326, 344, 362, 494`. Inoltre `alphaedge.py:344`
cerca `"SHERIFNEW_UT"`, stringa **mai scritta** in questo repo: residuo dell'autore.

### Soluzione proposta

`ALPHAEDGE_MAGIC`, intero configurabile da `.env` con default fisso. Impostato
nella richiesta d'ordine e usato come **unico** criterio di proprietà.

Verificato che il campo `magic` esiste sia su `TradePosition` sia su `TradeDeal`,
quindi copre posizioni aperte, ordini pendenti e storico per il guardrail.

Il `comment` resta come etichetta leggibile, ma nessuna logica vi dipende.
Rimozione del riferimento a `"SHERIFNEW_UT"`.

**Questo risolve R2 senza bisogno di alcun ordine reale:** il magic lo scriviamo
noi e MT5 lo restituisce; non è un campo che il broker riscrive.

### File coinvolti

- `alphaedge.py` — richiesta d'ordine e i 4 punti di filtraggio
- `trading_bot_skills/trade_config.py` — costante
- `.env.example` — nuova variabile documentata

### Test necessari

- Filtro su liste di posizioni fittizie con magic misti → seleziona solo i propri
- Posizione con magic corretto ma comment sovrascritto → **riconosciuta**
- Posizione aperta a mano (magic 0) → **ignorata**
- Il guardrail somma solo i deal con magic corretto

### Rischi della modifica

- **Eventuali posizioni già aperte prima della modifica avrebbero magic 0 e non verrebbero più gestite.** Attualmente ce ne sono 0, quindi il rischio è nullo ora, ma va applicato prima di qualunque operatività.
- Se in futuro si affiancasse una seconda strategia servirebbero magic distinti: prevedere la costante come mappa, non come scalare.

---

# P0-3 — Protezione duplicati e race condition

### Problema

Due istanze del daemon avviate per errore, o un retry, possono aprire ordini
duplicati sullo stesso segnale. Non esiste idempotenza.

### Causa

L'unico controllo è "esiste già una posizione su questo simbolo"
(`alphaedge.py:494`), calcolato **prima** del ciclo di invio. Fra il controllo e
`order_send()` passa tempo: nel frattempo un'altra istanza può aver aperto la
posizione. Nessun lock di processo.

### Soluzione proposta

Tre livelli:

1. **Lock di processo esclusivo** — nuovo modulo `trading_bot_skills/process_lock.py`. File di lock con PID; all'avvio, se il lock esiste, verifica se quel PID è ancora vivo (lock orfano dopo un crash → rimosso, altrimenti → uscita immediata).
2. **Ricontrollo immediato pre-invio** — rileggere posizioni e ordini pendenti (filtrati per magic) subito prima di `order_send()`, non a inizio ciclo.
3. **Chiave di deduplica** — `(simbolo, direzione, ora_apertura_barra_M30)` persistita su file. Lo stesso segnale sulla stessa barra non viene mai inviato due volte, nemmeno dopo un riavvio.

### File coinvolti

- `trading_bot_skills/process_lock.py` *(nuovo)*
- `trading_bot_skills/dedup.py` *(nuovo)*
- `alphaedge.py`, `run_autonomous_scanner.py`

### Test necessari

- Seconda istanza con lock attivo → esce senza operare
- Lock con PID inesistente → riconosciuto come orfano e rimosso
- Stessa chiave di deduplica due volte → secondo invio bloccato
- Chiave persistente attraverso un riavvio simulato
- Nuova barra M30 → stessa coppia simbolo/direzione **consentita**

### Rischi della modifica

- Un lock non rilasciato per un kill brutale blocca il bot finché non lo si rimuove: mitigato dal controllo PID, ma va documentato.
- Il file di deduplica cresce: serve pulizia delle voci più vecchie di N giorni.
- Il controllo PID su Windows può dare falsi positivi se il PID viene riciclato: mitigazione con timestamp di creazione nel lock.

---

# P0-4 — Test automatici

### Problema

Copertura 0% su codice che muove denaro. Ogni modifica è una scommessa e non
esiste rete di sicurezza per le regressioni.

### Causa

Nessun framework, nessuna directory `tests/`, nessuna CI. Il codice attuale non è
testabile: la logica è intrecciata alle chiamate MT5, che richiedono un terminale
aperto e connesso.

### Soluzione proposta

`pytest` (**non installato**, va aggiunto). Directory `tests/`.

Principio di design che rende possibile tutto il resto: **separare il calcolo puro
dall'I/O.** Ogni funzione P0 riceve i dati come parametri e non chiama MT5
direttamente. La suite gira quindi **senza terminale MT5 e a mercati chiusi**.

Test previsti: `test_sizing.py`, `test_magic.py`, `test_dedup.py`,
`test_process_lock.py`, `test_guardrail.py`, `test_order_validation.py`.

### File coinvolti

- `tests/` *(nuovo)*, `requirements-dev.txt` *(nuovo)*, `pytest.ini` *(nuovo)*

### Test necessari

*(Questa voce è essa stessa i test. Criterio di accettazione: la suite gira in
meno di 5 secondi, senza MT5, e passa al 100%.)*

### Rischi della modifica

- Rischio basso: aggiunge file senza toccare il comportamento a runtime.
- Il rifattorizzare per la testabilità tocca però `alphaedge.py`: va fatto a piccoli passi verificando che la scansione in sola lettura continui a produrre lo stesso output.

---

# P0-5 — Modalità dry-run dell'esecuzione

### Problema

Non c'è modo di verificare che la costruzione dell'ordine sia corretta senza
inviarlo davvero. Il primo ordine reale sarebbe anche il primo test.

### Causa

`execute_orders=False` interrompe il flusso **prima** che l'ordine venga costruito
(`alphaedge.py:478`). Tutto ciò che sta a valle — richiesta, SL/TP, scadenza,
filling, margine — non viene mai esercitato.

### Soluzione proposta

`DRY_RUN`, default `True`. Con `DRY_RUN` attivo il flusso arriva **fino in fondo**:
costruisce la richiesta completa, poi chiama `mt5.order_check(request)` invece di
`order_send(request)`.

`order_check()` valida la richiesta contro il server e restituisce retcode, margine
richiesto, equity risultante e commento del server — **senza piazzare nulla**.

Introduce un terzo stato oltre a "guarda" ed "esegui":

```text
execute_orders=False   -> analizza e stampa, non costruisce l'ordine
DRY_RUN=True           -> costruisce e VALIDA l'ordine, non lo invia   <- default
DRY_RUN=False          -> invia (richiede anche Algo Trading attivo)
```

Ogni validazione viene registrata in `dry_run_log.csv` con richiesta e risposta,
così si accumulano prove prima di qualsiasi operatività.

### File coinvolti

- `alphaedge.py` — nuova funzione `esegui_o_valida(request)`
- `trading_bot_skills/trade_config.py` — costante `DRY_RUN`

### Test necessari

- Con `DRY_RUN=True`, `order_send` **non viene mai chiamato** (verificato con un finto che fallisce il test se invocato)
- La richiesta costruita contiene tutti i campi obbligatori
- Un retcode di errore dal server viene registrato e non bloccca la scansione degli altri simboli

### Rischi della modifica

- `order_check()` potrebbe non validare tutto ciò che valida `order_send()`: un dry-run pulito **non è una garanzia** che l'ordine reale passerebbe. Da documentare esplicitamente.
- Il flusso arriva più vicino all'invio reale: la separazione fra i due rami deve essere netta e coperta da test.

---

# P0-6 — SL e TP sempre obbligatori

### Problema

Nulla garantisce che un ordine parta con stop loss valido. Un SL a 0, o dal lato
sbagliato del prezzo, produrrebbe una posizione senza protezione.

### Causa

SL e TP sono calcolati dalla strategia e passati alla richiesta **senza alcuna
validazione**. Se `analyze_structural_edge()` restituisse 0.0 per un errore, l'ordine
partirebbe comunque.

### Soluzione proposta

Funzione pura `valida_ordine(...)` eseguita **subito prima** dell'invio o del
dry-run. Verifica: SL e TP diversi da zero; SL dal lato corretto (sotto l'ingresso
per BUY, sopra per SELL); TP dal lato opposto; distanza ≥ `trade_stops_level`
(oggi 0 su FTMO, ma da non dare per scontato); prezzi arrotondati ai `digits` del
simbolo; volume multiplo di `volume_step`.

Se una sola condizione fallisce: **NO TRADE**, con motivo registrato.

### File coinvolti

- `trading_bot_skills/order_validation.py` *(nuovo)*
- `alphaedge.py`

### Test necessari

- BUY con SL sopra l'ingresso → rifiutato
- SELL con SL sotto l'ingresso → rifiutato
- SL o TP a zero → rifiutato
- Distanza inferiore a `trade_stops_level` → rifiutato
- Prezzo con più decimali di `digits` → arrotondato correttamente
- Caso valido → accettato

### Rischi della modifica

- Rischio molto basso: aggiunge un cancello, non modifica calcoli.
- Potrebbe rifiutare segnali che oggi passerebbero. È l'effetto voluto.

---

# P0-7 — Verifica dei guardrail

### Problema

Esistono **tre limiti di perdita giornaliera diversi e incoerenti**, e solo uno è
applicato. Nessuno è mai stato testato.

| Fonte | Valore |
|---|---|
| `alphaedge.py:56` | **$5** ← l'unico applicato |
| `trade_config.py:8` | 2% di $10.000 = $200 |
| `.agents/AGENTS.md` | $50 |

### Causa

Costanti duplicate in punti diversi, aggiunte in momenti diversi, mai riconciliate.
Il calcolo del P&L giornaliero (`alphaedge.py:337-348`) non ha test: filtra i deal
per commento (vedi P0-2) e somma `profit + commission + swap`.

### Soluzione proposta

Un'unica fonte di verità in `trade_config.py`. Rimozione delle costanti duplicate.
Estrazione del calcolo in una funzione pura che riceve la lista dei deal e
restituisce il P&L, testabile su dati sintetici.

Aggiunta di un controllo oggi assente: **numero massimo di posizioni aperte**.

**Da documentare senza ambiguità:** il limite giornaliero è un controllo
*pre-trade* sul P&L **realizzato**. Non è uno stop sull'equity: non chiude le
posizioni aperte e non impedisce a un trade in corso di superare il limite.

### File coinvolti

- `trading_bot_skills/guardrail.py` *(nuovo)*
- `alphaedge.py`, `trading_bot_skills/trade_config.py`, `.agents/AGENTS.md`

### Test necessari

- P&L esattamente al limite → blocca (verifica del confronto `<=`)
- P&L appena sopra il limite → consente
- Deal di altri magic → esclusi dal calcolo
- Commissioni e swap inclusi nella somma
- Target di profitto raggiunto → blocca
- Nessun deal oggi → P&L zero, consente
- Massimo posizioni aperte raggiunto → blocca

### Rischi della modifica

- Cambiare il limite applicato da $5 a un altro valore **cambia il comportamento operativo**: richiede la tua decisione esplicita, non la prendo io.
- Il P&L dipende dal fuso del server (GMT+3 rilevato) contro l'ora locale: `datetime.now()` a `alphaedge.py:338` usa l'ora **locale** per definire "oggi". Discrepanza reale da correggere e testare.

---

# P0-8 — Riavvio, perdita connessione, retry

### Problema

Se MT5 si disconnette a metà scansione, le chiamate restituiscono `None` e il
codice le interpreta come "dati insufficienti": il simbolo diventa `NEUTRAL`
**silenziosamente**. Una disconnessione somiglia a un mercato tranquillo.

Se il processo muore con posizioni aperte, il trailing stop smette di funzionare
senza che nessuno se ne accorga.

### Causa

`connect_mt5()` non ha retry. Nessun controllo di `terminal_info().connected`
durante il ciclo. `analyze_structural_edge()` cattura ogni eccezione e ritorna
`NEUTRAL` (`alphaedge.py:349-351`), mascherando i guasti di connessione.

### Soluzione proposta

- **Health check** all'inizio di ogni ciclo: `terminal_info().connected` e `account_info()` non nullo. Se fallisce, riconnessione con backoff esponenziale (3 tentativi) e, se persiste, ciclo saltato e registrato — **mai una scansione su dati parziali**.
- **Distinguere i guasti dai segnali neutri:** introdurre un esito `ERRORE` separato da `NEUTRAL`, visibile nella tabella. Un simbolo in errore non deve mai apparire come "nessun segnale".
- **Riconciliazione al riavvio:** all'avvio, rilettura delle posizioni con magic proprio e ripristino dello stato di gestione, così il trailing riprende sulle posizioni preesistenti.
- **Retry solo idempotente:** i retry riguardano lettura e connessione. `order_send()` **non viene mai ritentato automaticamente** — è la fonte classica di ordini duplicati.

### File coinvolti

- `alphaedge.py` — `connect_mt5()`, `run_alphaedge()`, `analyze_structural_edge()`
- `run_autonomous_scanner.py`

### Test necessari

- MT5 finto che restituisce `None` → esito `ERRORE`, non `NEUTRAL`
- Disconnessione simulata → tentata riconnessione, ciclo saltato dopo 3 fallimenti
- Riavvio con posizioni preesistenti → riconosciute tramite magic
- `order_send` fallito → **nessun retry automatico**
- Backoff: gli intervalli crescono come previsto

### Rischi della modifica

- I retry possono mascherare un problema reale di connettività: limitarli e registrarli sempre.
- La riconciliazione al riavvio tocca la gestione delle posizioni: va testata con posizioni finte prima di qualunque uso.

---

## Decisioni aperte — servono le tue risposte

**D1. Budget di rischio per trade.** Da questo dipende quali simboli restano
operativi. Con $2: 18 simboli su 20, esclusi oro e argento. Serve un valore, e va
reso coerente col limite giornaliero (con $2 di rischio e $5 di limite, bastano
3 stop per chiudere la giornata).

**D2. Oro e argento.** XAUUSD richiede almeno $16,89 e XAGUSD almeno $38,59 per
trade, per via del lotto minimo. Le opzioni: alzare il budget, oppure toglierli
dalla watchlist. XAUUSD è uno dei tuoi due obiettivi dichiarati, quindi la
decisione non è neutra.

**D3. Limite giornaliero.** Quale delle tre fonti diventa quella autorevole: $5,
$50 o $200?

---

## Ordine di implementazione proposto

Ogni passo è indipendente e verificabile. Dopo ciascuno: test verdi + scansione in
sola lettura invariata.

```text
1. P0-4  infrastruttura test (pytest)         nessun impatto a runtime
2. P0-1  position sizing                      funzione pura + test
3. P0-2  magic number                         chiude R2
4. P0-6  validazione SL/TP                    cancello puro
5. P0-7  guardrail unificati                  richiede D3
6. P0-3  lock e deduplica                     chiude R4
7. P0-5  dry-run con order_check              chiude il ciclo senza ordini reali
8. P0-8  connessione, riavvio, retry          chiude R6
```

## Fuori portata in questa fase

Modifiche alla strategia, filtro H1, RSI di Wilder, backtest, baseline, Discord,
AI, rifattorizzazione del monolite oltre al minimo necessario per la testabilità.

---

## Stato

**Piano redatto. Nessun file di codice modificato.**
In attesa di approvazione esplicita e delle risposte a D1, D2, D3.
