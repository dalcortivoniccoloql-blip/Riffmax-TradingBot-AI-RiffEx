"""Risk Engine deterministico.

Tutte le funzioni di questo modulo sono PURE: ricevono i dati come parametri e
non chiamano MetaTrader5. Questo permette di testarle senza terminale aperto e
a mercati chiusi, ed e' il motivo per cui esistono le dataclass qui sotto invece
di passare direttamente gli oggetti di MT5.

Il lotto non viene mai deciso dal broker: risulta sempre dal calcolo qui dentro.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field


MOTIVO_NON_CONFIGURATO = "RISK NOT CONFIGURED"
MOTIVO_INCOMPATIBILE = "NO TRADE - POSITION SIZE INCOMPATIBLE WITH RISK LIMIT"
MOTIVO_STOP_NULLO = "NO TRADE - DISTANZA STOP NULLA O NEGATIVA"
MOTIVO_SPECIFICHE = "NO TRADE - SPECIFICHE SIMBOLO NON VALIDE"
MOTIVO_SOTTO_MINIMO = "NO TRADE - RISCHIO SOTTO LA SOGLIA MINIMA UTILE"
MOTIVO_OK = "OK"


@dataclass(frozen=True)
class SpecificheSimbolo:
    """Sottoinsieme di mt5.symbol_info() necessario al sizing."""

    nome: str
    tick_size: float
    tick_value: float
    volume_min: float
    volume_max: float
    volume_step: float
    digits: int = 5


@dataclass(frozen=True)
class ProfiloRischio:
    """Parametri di rischio, tutti opzionali e combinabili.

    Il rischio ammesso e' il PIU' BASSO fra percentuale dell'equity, tetto
    assoluto ed eventuale override di simbolo. Nessuno di questi puo' alzare il
    rischio oltre gli altri: si applicano solo verso il basso.
    """

    rischio_pct: float | None = None
    cap_usd: float | None = None
    min_usd: float = 0.0
    override_per_simbolo: dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class EsitoSizing:
    """Risultato del calcolo. `consentito=False` significa sempre NO TRADE."""

    consentito: bool
    lotto: float
    rischio_effettivo_usd: float
    rischio_ammesso_usd: float
    motivo: str


def rischio_ammesso_usd(equity: float, profilo: ProfiloRischio, simbolo: str) -> float | None:
    """Rischio massimo consentito su un trade, in valuta conto.

    Restituisce None se il profilo non e' configurato: in quel caso il chiamante
    deve rifiutare il trade, mai ripiegare su un valore di default.
    """
    candidati: list[float] = []

    if profilo.rischio_pct is not None:
        if equity <= 0:
            return None
        candidati.append(equity * profilo.rischio_pct / 100.0)

    if profilo.cap_usd is not None:
        candidati.append(profilo.cap_usd)

    override = profilo.override_per_simbolo.get(simbolo)
    if override is not None:
        candidati.append(override)

    if not candidati:
        return None

    # il piu' restrittivo vince sempre: un override non puo' alzare il rischio
    return max(0.0, min(candidati))


def arrotonda_al_passo(volume: float, passo: float) -> float:
    """Arrotonda SEMPRE per difetto al multiplo di `passo`.

    Arrotondare per eccesso farebbe superare il rischio ammesso, quindi non e'
    mai ammesso. L'epsilon assorbe gli errori di rappresentazione dei float
    (esempio: 0.03 / 0.01 vale 2.9999... in virgola mobile).
    """
    if passo <= 0:
        return volume
    passi = math.floor(volume / passo + 1e-9)
    decimali = max(0, -math.floor(math.log10(passo)) + 2)
    return round(passi * passo, decimali)


def rischio_di_un_lotto(distanza_stop: float, specifiche: SpecificheSimbolo) -> float:
    """Perdita in valuta conto se un lotto pieno va a stop.

    tick_value e' gia' espresso in valuta conto da MT5, anche per simboli la cui
    valuta di profitto e' diversa (verificato su GBPJPY: profitto in JPY, tick
    value in USD).
    """
    return (distanza_stop / specifiche.tick_size) * specifiche.tick_value


def calcola_lotto(
    distanza_stop: float,
    specifiche: SpecificheSimbolo,
    rischio_usd: float | None,
) -> EsitoSizing:
    """Calcola il lotto che rischia al massimo `rischio_usd`.

    Non ripiega MAI sul lotto minimo: se il minimo del broker rischia piu' del
    consentito, il risultato e' NO TRADE. E' esattamente il caso di XAUUSD e
    XAGUSD su questo conto, ed e' il comportamento corretto.
    """
    if rischio_usd is None:
        return EsitoSizing(False, 0.0, 0.0, 0.0, MOTIVO_NON_CONFIGURATO)

    if distanza_stop <= 0:
        return EsitoSizing(False, 0.0, 0.0, rischio_usd, MOTIVO_STOP_NULLO)

    if specifiche.tick_size <= 0 or specifiche.tick_value <= 0 or specifiche.volume_min <= 0:
        return EsitoSizing(False, 0.0, 0.0, rischio_usd, MOTIVO_SPECIFICHE)

    per_lotto = rischio_di_un_lotto(distanza_stop, specifiche)
    if per_lotto <= 0:
        return EsitoSizing(False, 0.0, 0.0, rischio_usd, MOTIVO_SPECIFICHE)

    lotto = arrotonda_al_passo(rischio_usd / per_lotto, specifiche.volume_step)

    # il lotto minimo del broker rischia gia' piu' del consentito
    if lotto < specifiche.volume_min:
        rischio_al_minimo = per_lotto * specifiche.volume_min
        return EsitoSizing(False, 0.0, rischio_al_minimo, rischio_usd, MOTIVO_INCOMPATIBILE)

    if lotto > specifiche.volume_max:
        lotto = arrotonda_al_passo(specifiche.volume_max, specifiche.volume_step)

    effettivo = per_lotto * lotto
    return EsitoSizing(True, lotto, effettivo, rischio_usd, MOTIVO_OK)


def dimensiona(
    equity: float,
    profilo: ProfiloRischio,
    specifiche: SpecificheSimbolo,
    prezzo_ingresso: float,
    prezzo_stop: float,
) -> EsitoSizing:
    """Punto di ingresso unico del sizing: dall'equity al lotto, in un passo."""
    ammesso = rischio_ammesso_usd(equity, profilo, specifiche.nome)

    if ammesso is not None and profilo.min_usd > 0 and ammesso < profilo.min_usd:
        return EsitoSizing(False, 0.0, 0.0, ammesso, MOTIVO_SOTTO_MINIMO)

    return calcola_lotto(abs(prezzo_ingresso - prezzo_stop), specifiche, ammesso)


def perdita_giornaliera_superata(
    pnl_realizzato: float,
    max_perdita_usd: float,
    target_profitto_usd: float,
) -> tuple[bool, str]:
    """Guardrail giornaliero. Funzione pura: riceve il P&L gia' calcolato.

    Il confronto sulla perdita e' `<=`: raggiungere esattamente il limite lo
    considera superato.
    """
    if pnl_realizzato <= -abs(max_perdita_usd):
        return True, f"Limite di perdita giornaliera raggiunto: {pnl_realizzato:+.2f} USD"
    if pnl_realizzato >= abs(target_profitto_usd):
        return True, f"Target di profitto giornaliero raggiunto: {pnl_realizzato:+.2f} USD"
    return False, ""


def drawdown_interno_superato(
    equity: float,
    baseline_equity: float,
    max_drawdown_usd: float,
) -> tuple[bool, str]:
    """Guardrail sul capitale complessivo. Funzione pura.

    Misura l'EQUITY, non il P&L realizzato: include quindi il flottante delle
    posizioni aperte e qualunque operazione fatta a mano sul conto. E' un
    controllo DIVERSO da perdita_giornaliera_superata(), non una sua versione
    piu' severa.

    Statico: il pavimento e' baseline_equity - max_drawdown_usd e non si sposta
    quando l'equity sale. Un drawdown trailing dal picco sarebbe un altro
    limite e va deciso esplicitamente.

    Il confronto e' `<=`: toccare esattamente il pavimento lo considera violato.
    Un'equity non positiva e' sempre una violazione: significa conto azzerato o
    dato non disponibile, e in entrambi i casi non si apre nulla.
    """
    pavimento = baseline_equity - abs(max_drawdown_usd)

    if equity <= 0:
        return True, f"Equity non disponibile o azzerata ({equity:.2f} USD): nessun nuovo ordine"

    if equity <= pavimento:
        perdita = baseline_equity - equity
        return True, (
            f"Drawdown interno massimo raggiunto: equity {equity:.2f} USD, "
            f"pavimento {pavimento:.2f} USD (perdita {perdita:.2f} su un massimo di {abs(max_drawdown_usd):.2f})"
        )

    return False, ""


def pnl_realizzato_da_deal(deal, magic: int) -> float:
    """Somma profit + commission + swap dei soli deal di USCITA con il nostro magic.

    `deal` e' un iterabile di oggetti con attributi magic, entry, profit,
    commission, swap: accetta sia gli oggetti MT5 sia dei semplici stub nei test.
    Gli `entry` 1 e 3 sono uscite (out e out_by); 0 e' l'ingresso.
    """
    totale = 0.0
    for d in deal or ():
        if getattr(d, "magic", 0) != magic:
            continue
        if getattr(d, "entry", 0) not in (1, 3):
            continue
        totale += getattr(d, "profit", 0.0) + getattr(d, "commission", 0.0) + getattr(d, "swap", 0.0)
    return totale
