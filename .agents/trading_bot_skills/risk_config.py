"""Unica fonte autorevole per tutti i limiti di rischio del sistema.

Nessun altro file deve definire limiti di rischio, di perdita o di sizing.
Ogni valore qui sotto riporta la propria PROVENIENZA, cioe' da dove arriva e
se era un valore realmente applicato a runtime oppure documentazione o residuo.

Vedi P0_SAFETY_IMPLEMENTATION_PLAN.md, sezione P0-7.

GERARCHIA DEI LIMITI (dal piu' esterno al piu' interno). Ogni livello puo' solo
STRINGERE il livello precedente, mai allargarlo:

    VINCOLI FTMO / BROKER      <- imposti da terzi, noi non li scegliamo
            v
    ACCOUNT RISK POLICY        <- BASELINE_EQUITY_USD, i nostri limiti interni
            v
    LIMITE PERDITA GIORNALIERA <- MAX_PERDITA_GIORNALIERA_USD
            v
    RISCHIO PER TRADE          <- RISCHIO_PER_TRADE_PCT / _CAP_USD
            v
    POSITION SIZING            <- risk_engine.dimensiona()
            v
    VALIDAZIONE ORDINE         <- order_validation.valida_ordine()
            v
    ESECUZIONE                 <- alphaedge.esegui_o_valida()
"""

import os


# ---------------------------------------------------------------------------
# BASE DI CALCOLO
# ---------------------------------------------------------------------------

BASELINE_EQUITY_USD: float = 10_000.0
"""Capitale iniziale del conto, base fissa per tutte le percentuali interne.

E' un valore FISSO e dichiarato, non l'equity corrente: se l'equity scende, i
limiti in dollari NON si stringono da soli, e se sale non si allargano da soli.
Cambiare conto significa cambiare questo numero a mano, deliberatamente.

PROVENIENZA: decisione dell'utente del 2026-08-12. Conto FTMO 2-Step da $10.000.
"""


# ---------------------------------------------------------------------------
# RISCHIO PER SINGOLO TRADE  --  DECISIONE D1
# ---------------------------------------------------------------------------
# PROVENIENZA: decisione dell'utente del 2026-08-12.
# "0,25% dell'equity per trade, cap assoluto $25."
#
# NOTA DELL'UTENTE: valore iniziale di sviluppo e validazione, NON ottimizzato.
#
# Il Risk Engine applica sempre il piu' BASSO fra percentuale, cap e override.
# Nessuno di questi puo' alzare il rischio: agiscono solo verso il basso.

RISCHIO_PER_TRADE_PCT: float | None = 0.25
"""Percentuale dell'equity da rischiare su ogni singolo trade. None = non configurato."""

RISCHIO_PER_TRADE_CAP_USD: float | None = 25.0
"""Tetto assoluto in valuta conto. Vince sempre sulla percentuale se piu' basso.

Con equity a $10.000 i due valori coincidono ($25). Se l'equity sale il cap
diventa il vincolo attivo; se scende comanda la percentuale. E' voluto: il
rischio in dollari non cresce mai oltre $25 senza una decisione esplicita.
"""

RISCHIO_PER_TRADE_MIN_USD: float = 0.0
"""Soglia minima: sotto questa cifra il trade non vale il rischio operativo. 0 = disattivata."""

RISCHIO_OVERRIDE_PER_SIMBOLO: dict[str, float] = {}
"""Override per simbolo, in valuta conto. Esempio: {"XAUUSD": 25.0}.

Un override puo' solo essere applicato come tetto specifico per quel simbolo:
non puo' MAI far superare RISCHIO_PER_TRADE_CAP_USD (vedi risk_engine).

DECISIONE D2 (2026-08-12): XAUUSD e XAGUSD RESTANO nella watchlist e continuano
a essere analizzati. Nessun override serve: se il lotto minimo del broker
comporta un rischio superiore al limite, il Risk Engine risponde
"NO TRADE - POSITION SIZE INCOMPATIBLE WITH RISK LIMIT". Analisi != trade.
"""


# ---------------------------------------------------------------------------
# LIMITI GIORNALIERI  --  DECISIONE D3
# ---------------------------------------------------------------------------
# PROVENIENZA STORICA: alphaedge.py:56-57 aveva $5 di perdita e $50 di target.
# Erano gli UNICI due valori realmente applicati a runtime prima della
# centralizzazione. Le altre tre fonti trovate nell'audit NON erano applicate:
#   - trade_config.py MAX_DAILY_DRAWDOWN_PCT = 2%  -> raggiungibile solo da
#     check_drawdown_limits(), funzione mai chiamata da nessuno: LEGACY MORTO
#   - .agents/AGENTS.md "-$50.00"                  -> DOCUMENTAZIONE in prosa
#   - alphaedge.py:437 "-$200 / Target: +$400"     -> COMMENTO residuo dell'autore
#
# DECISIONE D3 (2026-08-12): il valore autorevole e' 0,75% del capitale.

MAX_PERDITA_GIORNALIERA_PCT: float = 0.75
"""Perdita giornaliera massima, in percentuale di BASELINE_EQUITY_USD."""

MAX_PERDITA_GIORNALIERA_USD: float = BASELINE_EQUITY_USD * MAX_PERDITA_GIORNALIERA_PCT / 100.0
"""Perdita realizzata massima nella giornata: $75 su $10.000. Controllo PRE-TRADE.

ATTENZIONE, due limiti precisi di questo controllo:
  1. non e' uno stop sull'equity: non chiude le posizioni aperte e non impedisce
     a un trade gia' in corso di superare la cifra. Blocca solo l'apertura di
     NUOVE posizioni.
  2. misura il P&L REALIZZATO dei soli deal con il nostro magic. Non vede il
     flottante e non vede le operazioni fatte a mano. Il controllo che copre il
     flottante e' il drawdown interno qui sotto, che lavora sull'equity.

Coerenza con il rischio per trade: $25 di rischio contro $75 di limite
significa che TRE stop pieni chiudono la giornata.
"""

TARGET_PROFITTO_GIORNALIERO_USD: float = 50.0
"""Profitto realizzato oltre il quale si smette di aprire posizioni per oggi.

NON toccato dalla decisione D3, che riguardava solo la perdita. Resta il valore
storico. ASIMMETRIA DA NOTARE: si smette di guadagnare a +$50 ma si accettano
perdite fino a -$75. Se non e' voluta, e' TARGET_PROFITTO_GIORNALIERO_USD che
va rivisto, non il limite di perdita.
"""

MAX_POSIZIONI_APERTE: int = 5
"""Numero massimo di posizioni contemporanee gestite dal bot.

NUOVO controllo, prima assente. Il broker impone inoltre un massimo di 200
ordini pendenti (account_info().limit_orders), molto piu' alto di questo.
"""


# ---------------------------------------------------------------------------
# DRAWDOWN INTERNO MASSIMO
# ---------------------------------------------------------------------------
# PROVENIENZA: decisione dell'utente del 2026-08-12. "Internal Maximum
# Drawdown = 5%", cioe' $500 su $10.000.
#
# STATICO, misurato da BASELINE_EQUITY_USD, non trailing dal picco di equity.
# Scelta deliberata: rispecchia la Maximum Loss di FTMO, che le due fonti
# ufficiali consultate descrivono entrambe come statica sul capitale iniziale.
# Un trailing dal picco sarebbe un limite DIVERSO e piu' stretto, e andrebbe
# deciso esplicitamente.

MAX_DRAWDOWN_INTERNO_PCT: float = 5.0
"""Drawdown interno massimo, in percentuale di BASELINE_EQUITY_USD."""

MAX_DRAWDOWN_INTERNO_USD: float = BASELINE_EQUITY_USD * MAX_DRAWDOWN_INTERNO_PCT / 100.0
"""$500 su $10.000. Sotto equity = $9.500 il bot non apre piu' nulla.

A differenza del limite giornaliero, questo si misura sull'EQUITY, quindi
include il flottante delle posizioni aperte e le operazioni fatte a mano.
Sono due guardrail diversi, non uno la versione severa dell'altro.

Coerenza: a $25 di rischio pieno per trade servono 20 stop consecutivi per
esaurire questo budget.
"""


# ---------------------------------------------------------------------------
# VINCOLI FTMO  --  SOLO DOCUMENTAZIONE, MAI APPLICATI DA NOI
# ---------------------------------------------------------------------------
# Questi NON sono i nostri limiti e il codice non li usa per decidere: li
# applica FTMO lato server. Stanno qui per un solo motivo: permettere a un test
# di verificare che i nostri limiti interni restino sempre piu' conservativi.
# NON sostituire mai i limiti interni con questi.
#
# FONTI UFFICIALI verificate il 2026-08-12:
#   https://ftmo.com/en/trading-objectives/
#   https://academy.ftmo.com/lesson/maximum-loss/
#
# Maximum Daily Loss (2-Step): 5% del capitale iniziale. Ricalcolata ogni
#   giorno alle 00:00 CE(S)T come differenza fra il BALANCE registrato alle
#   00:00 CE(S)T del giorno corrente e il 5%. Il vincolo e' sull'EQUITY.
# Maximum Loss (2-Step): 10% del capitale iniziale, STATICA sul capitale
#   iniziale, nessun reset giornaliero, valida per Challenge, Verification e
#   FTMO Account.

FTMO_MAX_DAILY_LOSS_PCT: float = 5.0
FTMO_MAX_LOSS_PCT: float = 10.0
FTMO_RESET_GIORNALIERO: str = "00:00 CE(S)T"
FTMO_MAX_LOSS_TIPO: str = "statica sul capitale iniziale (non trailing)"


# ---------------------------------------------------------------------------
# IDENTIFICAZIONE DELLE POSIZIONI
# ---------------------------------------------------------------------------

ALPHAEDGE_MAGIC: int = int(os.getenv("ALPHAEDGE_MAGIC", "20260812"))
"""Identificatore numerico delle posizioni aperte da questo bot.

Sostituisce il confronto sul campo 'comment', che i broker possono sovrascrivere
o troncare. Il magic viene impostato da noi e restituito da MT5 invariato.
"""

ETICHETTA_ORDINE: str = "ALPHAEDGE"
"""Commento leggibile sull'ordine. Nessuna logica deve dipendere da questo campo."""


# ---------------------------------------------------------------------------
# MODALITA' OPERATIVA
# ---------------------------------------------------------------------------

DRY_RUN: bool = os.getenv("ALPHAEDGE_DRY_RUN", "1") != "0"
"""True = gli ordini vengono costruiti e validati con order_check(), mai inviati.

Default True: SAFE DEVELOPMENT MODE. Per inviare davvero servono, insieme:
ALPHAEDGE_DRY_RUN=0 nell'ambiente E Algo Trading attivo nel terminale MT5.
"""

MAX_TENTATIVI_CONNESSIONE: int = 3
"""Tentativi di riconnessione a MT5 prima di saltare il ciclo."""

BACKOFF_CONNESSIONE_SECONDI: float = 2.0
"""Attesa iniziale fra i tentativi di riconnessione. Raddoppia a ogni tentativo."""
