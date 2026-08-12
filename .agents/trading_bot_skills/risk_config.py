"""Unica fonte autorevole per tutti i limiti di rischio del sistema.

Nessun altro file deve definire limiti di rischio, di perdita o di sizing.
Ogni valore qui sotto riporta la propria PROVENIENZA, cioe' da dove arriva e
se era un valore realmente applicato a runtime oppure documentazione o residuo.

Vedi P0_SAFETY_IMPLEMENTATION_PLAN.md, sezione P0-7.
"""

import os


# ---------------------------------------------------------------------------
# RISCHIO PER SINGOLO TRADE
# ---------------------------------------------------------------------------
# DELIBERATAMENTE NON CONFIGURATO.
#
# Fissare questi valori e' una decisione economica che richiede di conoscere i
# limiti reali del conto (D1 ancora aperta). Finche' RISCHIO_PER_TRADE_PCT e
# RISCHIO_PER_TRADE_CAP_USD restano entrambi None, il Risk Engine rifiuta ogni
# trade con motivo "RISK NOT CONFIGURED". E' il comportamento fail-safe voluto:
# meglio nessun trade che un trade dimensionato a caso.

RISCHIO_PER_TRADE_PCT: float | None = None
"""Percentuale dell'equity da rischiare su ogni singolo trade. None = non configurato."""

RISCHIO_PER_TRADE_CAP_USD: float | None = None
"""Tetto assoluto in valuta conto. Vince sempre sulla percentuale se piu' basso."""

RISCHIO_PER_TRADE_MIN_USD: float = 0.0
"""Soglia minima: sotto questa cifra il trade non vale il rischio operativo. 0 = disattivata."""

RISCHIO_OVERRIDE_PER_SIMBOLO: dict[str, float] = {}
"""Override per simbolo, in valuta conto. Esempio: {"XAUUSD": 25.0}.

Un override puo' solo essere applicato come tetto specifico per quel simbolo:
non puo' MAI far superare RISCHIO_PER_TRADE_CAP_USD (vedi risk_engine).
"""


# ---------------------------------------------------------------------------
# LIMITI GIORNALIERI
# ---------------------------------------------------------------------------
# PROVENIENZA: alphaedge.py:56-57. Erano gli UNICI due valori realmente applicati
# a runtime prima di questa centralizzazione. Sono migrati qui invariati, per non
# alterare il comportamento durante un intervento di sola sicurezza.
#
# Le altre tre fonti trovate nell'audit NON erano applicate:
#   - trade_config.py MAX_DAILY_DRAWDOWN_PCT = 2%  -> raggiungibile solo da
#     check_drawdown_limits(), funzione mai chiamata da nessuno: LEGACY MORTO
#   - .agents/AGENTS.md "-$50.00"                  -> DOCUMENTAZIONE in prosa
#   - alphaedge.py:437 "-$200 / Target: +$400"     -> COMMENTO residuo dell'autore
#
# Quale debba essere il valore autorevole e' la decisione D3, ancora aperta.

MAX_PERDITA_GIORNALIERA_USD: float = 5.0
"""Perdita realizzata massima nella giornata. Controllo PRE-TRADE.

ATTENZIONE: non e' uno stop sull'equity. Non chiude le posizioni aperte e non
impedisce a un trade gia' in corso di superare questa cifra. Blocca solo
l'apertura di NUOVE posizioni.
"""

TARGET_PROFITTO_GIORNALIERO_USD: float = 50.0
"""Profitto realizzato oltre il quale si smette di aprire posizioni per oggi."""

MAX_POSIZIONI_APERTE: int = 5
"""Numero massimo di posizioni contemporanee gestite dal bot.

NUOVO controllo, prima assente. Il broker impone inoltre un massimo di 200
ordini pendenti (account_info().limit_orders), molto piu' alto di questo.
"""


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
