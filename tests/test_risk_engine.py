"""Test del Risk Engine.

Le specifiche dei simboli sono quelle REALI lette da FTMO-Demo il 12/08/2026:
non sono inventate, cosi' i test verificano il comportamento sul conto vero.
Nessun test richiede MetaTrader5 aperto.
"""

import pytest

from trading_bot_skills.risk_engine import (
    MOTIVO_INCOMPATIBILE,
    MOTIVO_NON_CONFIGURATO,
    MOTIVO_OK,
    MOTIVO_SOTTO_MINIMO,
    MOTIVO_STOP_NULLO,
    EsitoSizing,
    ProfiloRischio,
    SpecificheSimbolo,
    arrotonda_al_passo,
    calcola_lotto,
    dimensiona,
    perdita_giornaliera_superata,
    pnl_realizzato_da_deal,
    rischio_ammesso_usd,
    rischio_di_un_lotto,
)

XAUUSD = SpecificheSimbolo("XAUUSD", tick_size=0.01, tick_value=1.0,
                           volume_min=0.01, volume_max=100.0, volume_step=0.01, digits=2)
US100 = SpecificheSimbolo("US100.cash", tick_size=0.01, tick_value=0.01,
                          volume_min=0.01, volume_max=1000.0, volume_step=0.01, digits=2)
EURUSD = SpecificheSimbolo("EURUSD", tick_size=1e-05, tick_value=1.0,
                           volume_min=0.01, volume_max=50.0, volume_step=0.01, digits=5)


# --------------------------------------------------------------------------
# rischio di un lotto pieno
# --------------------------------------------------------------------------

@pytest.mark.parametrize("specifiche, distanza, atteso", [
    (XAUUSD, 27.0, 2700.0),
    (US100, 65.0, 65.0),
    (EURUSD, 0.0008, 80.0),
])
def test_rischio_di_un_lotto(specifiche, distanza, atteso):
    assert rischio_di_un_lotto(distanza, specifiche) == pytest.approx(atteso)


# --------------------------------------------------------------------------
# rischio ammesso: il piu' restrittivo vince sempre
# --------------------------------------------------------------------------

def test_solo_percentuale():
    profilo = ProfiloRischio(rischio_pct=0.5)
    assert rischio_ammesso_usd(10_000, profilo, "EURUSD") == pytest.approx(50.0)


def test_solo_cap_assoluto():
    profilo = ProfiloRischio(cap_usd=25.0)
    assert rischio_ammesso_usd(10_000, profilo, "EURUSD") == pytest.approx(25.0)


def test_cap_vince_sulla_percentuale_se_piu_basso():
    profilo = ProfiloRischio(rischio_pct=1.0, cap_usd=20.0)   # 1% = 100 USD
    assert rischio_ammesso_usd(10_000, profilo, "EURUSD") == pytest.approx(20.0)


def test_percentuale_vince_sul_cap_se_piu_bassa():
    profilo = ProfiloRischio(rischio_pct=0.1, cap_usd=500.0)  # 0.1% = 10 USD
    assert rischio_ammesso_usd(10_000, profilo, "EURUSD") == pytest.approx(10.0)


def test_override_di_simbolo_puo_solo_abbassare():
    profilo = ProfiloRischio(rischio_pct=1.0, override_per_simbolo={"XAUUSD": 5.0})
    assert rischio_ammesso_usd(10_000, profilo, "XAUUSD") == pytest.approx(5.0)


def test_override_non_puo_alzare_il_rischio():
    """Un override generoso non deve mai superare gli altri limiti."""
    profilo = ProfiloRischio(rischio_pct=0.1, cap_usd=10.0,
                             override_per_simbolo={"XAUUSD": 999.0})
    assert rischio_ammesso_usd(10_000, profilo, "XAUUSD") == pytest.approx(10.0)


def test_override_non_influenza_altri_simboli():
    profilo = ProfiloRischio(cap_usd=20.0, override_per_simbolo={"XAUUSD": 5.0})
    assert rischio_ammesso_usd(10_000, profilo, "EURUSD") == pytest.approx(20.0)


def test_profilo_vuoto_non_e_configurato():
    assert rischio_ammesso_usd(10_000, ProfiloRischio(), "EURUSD") is None


def test_equity_non_positiva_con_profilo_percentuale():
    assert rischio_ammesso_usd(0.0, ProfiloRischio(rischio_pct=0.5), "EURUSD") is None


# --------------------------------------------------------------------------
# arrotondamento: mai per eccesso
# --------------------------------------------------------------------------

@pytest.mark.parametrize("grezzo, passo, atteso", [
    (0.0308, 0.01, 0.03),
    (0.0299, 0.01, 0.02),
    (0.03,   0.01, 0.03),    # il float 0.03/0.01 vale 2.9999...: senza epsilon darebbe 0.02
    (0.9,    0.1,  0.9),
    (0.0009, 0.01, 0.0),
])
def test_arrotonda_sempre_per_difetto(grezzo, passo, atteso):
    assert arrotonda_al_passo(grezzo, passo) == pytest.approx(atteso)


# --------------------------------------------------------------------------
# calcolo del lotto
# --------------------------------------------------------------------------

def test_us100_lotto_calcolato_correttamente():
    esito = calcola_lotto(65.0, US100, rischio_usd=2.0)
    assert esito.consentito
    assert esito.lotto == pytest.approx(0.03)
    assert esito.rischio_effettivo_usd == pytest.approx(1.95)


def test_eurusd_lotto_calcolato_correttamente():
    esito = calcola_lotto(0.0008, EURUSD, rischio_usd=2.0)
    assert esito.consentito
    assert esito.lotto == pytest.approx(0.02)
    assert esito.rischio_effettivo_usd == pytest.approx(1.60)


def test_xauusd_rifiutato_perche_il_lotto_minimo_rischia_troppo():
    """Il caso che ha motivato tutto P0-1: l'oro non e' dimensionabile a 2 USD."""
    esito = calcola_lotto(27.0, XAUUSD, rischio_usd=2.0)
    assert not esito.consentito
    assert esito.lotto == 0.0
    assert esito.motivo == MOTIVO_INCOMPATIBILE
    # riporta quanto si rischierebbe davvero al minimo, per poterlo mostrare
    assert esito.rischio_effettivo_usd == pytest.approx(27.0)


def test_xauusd_ammesso_se_il_budget_lo_consente():
    esito = calcola_lotto(27.0, XAUUSD, rischio_usd=30.0)
    assert esito.consentito
    assert esito.lotto == pytest.approx(0.01)


def test_mai_ripiegare_sul_lotto_minimo():
    """Regressione: il bug originale apriva comunque al lotto minimo."""
    for specifiche in (XAUUSD, US100, EURUSD):
        esito = calcola_lotto(1_000_000.0, specifiche, rischio_usd=0.01)
        assert not esito.consentito
        assert esito.lotto == 0.0


def test_rischio_non_configurato():
    esito = calcola_lotto(65.0, US100, rischio_usd=None)
    assert not esito.consentito
    assert esito.motivo == MOTIVO_NON_CONFIGURATO


@pytest.mark.parametrize("distanza", [0.0, -5.0])
def test_distanza_stop_non_valida(distanza):
    esito = calcola_lotto(distanza, US100, rischio_usd=2.0)
    assert not esito.consentito
    assert esito.motivo == MOTIVO_STOP_NULLO


def test_lotto_limitato_al_massimo_del_broker():
    esito = calcola_lotto(0.0001, EURUSD, rischio_usd=1_000_000.0)
    assert esito.consentito
    assert esito.lotto <= EURUSD.volume_max


# --------------------------------------------------------------------------
# proprieta' fondamentale: il rischio effettivo non supera mai l'ammesso
# --------------------------------------------------------------------------

@pytest.mark.parametrize("specifiche", [XAUUSD, US100, EURUSD])
@pytest.mark.parametrize("rischio", [0.5, 2.0, 7.5, 25.0, 100.0])
@pytest.mark.parametrize("moltiplicatore", [0.3, 1.0, 3.7, 12.0])
def test_il_rischio_effettivo_non_supera_mai_l_ammesso(specifiche, rischio, moltiplicatore):
    distanza = specifiche.tick_size * 100 * moltiplicatore
    esito = calcola_lotto(distanza, specifiche, rischio_usd=rischio)
    if esito.consentito:
        assert esito.rischio_effettivo_usd <= rischio + 1e-9


# --------------------------------------------------------------------------
# dimensiona(): dall'equity al lotto
# --------------------------------------------------------------------------

def test_dimensiona_percorso_completo():
    profilo = ProfiloRischio(rischio_pct=0.02, cap_usd=5.0)   # 0.02% di 10k = 2 USD
    esito = dimensiona(10_000, profilo, US100, prezzo_ingresso=29_700.0, prezzo_stop=29_635.0)
    assert esito.consentito
    assert esito.lotto == pytest.approx(0.03)


def test_dimensiona_usa_il_valore_assoluto_della_distanza():
    """Uno SELL ha lo stop sopra l'ingresso: la distanza resta positiva."""
    profilo = ProfiloRischio(cap_usd=2.0)
    long = dimensiona(10_000, profilo, US100, 29_700.0, 29_635.0)
    short = dimensiona(10_000, profilo, US100, 29_635.0, 29_700.0)
    assert long.lotto == short.lotto


def test_dimensiona_senza_configurazione_rifiuta():
    esito = dimensiona(10_000, ProfiloRischio(), US100, 29_700.0, 29_635.0)
    assert not esito.consentito
    assert esito.motivo == MOTIVO_NON_CONFIGURATO


def test_soglia_minima_utile():
    profilo = ProfiloRischio(cap_usd=0.10, min_usd=1.0)
    esito = dimensiona(10_000, profilo, US100, 29_700.0, 29_635.0)
    assert not esito.consentito
    assert esito.motivo == MOTIVO_SOTTO_MINIMO


# --------------------------------------------------------------------------
# guardrail giornaliero
# --------------------------------------------------------------------------

def test_guardrail_esattamente_al_limite_blocca():
    superato, _ = perdita_giornaliera_superata(-5.0, 5.0, 50.0)
    assert superato


def test_guardrail_appena_sopra_il_limite_consente():
    superato, _ = perdita_giornaliera_superata(-4.99, 5.0, 50.0)
    assert not superato


def test_guardrail_target_di_profitto_blocca():
    superato, motivo = perdita_giornaliera_superata(50.0, 5.0, 50.0)
    assert superato
    assert "profitto" in motivo


def test_guardrail_giornata_neutra_consente():
    superato, _ = perdita_giornaliera_superata(0.0, 5.0, 50.0)
    assert not superato


# --------------------------------------------------------------------------
# P&L giornaliero dai deal
# --------------------------------------------------------------------------

class DealFinto:
    def __init__(self, magic, entry, profit, commission=0.0, swap=0.0):
        self.magic = magic
        self.entry = entry
        self.profit = profit
        self.commission = commission
        self.swap = swap


MAGIC = 20260812


def test_pnl_somma_solo_le_uscite():
    deal = [
        DealFinto(MAGIC, entry=0, profit=999.0),   # ingresso: escluso
        DealFinto(MAGIC, entry=1, profit=-3.0),    # uscita
        DealFinto(MAGIC, entry=3, profit=1.5),     # uscita per stop-out
    ]
    assert pnl_realizzato_da_deal(deal, MAGIC) == pytest.approx(-1.5)


def test_pnl_esclude_i_magic_altrui():
    deal = [
        DealFinto(MAGIC, entry=1, profit=-2.0),
        DealFinto(999, entry=1, profit=-500.0),    # trade manuale o di altri
        DealFinto(0, entry=1, profit=-500.0),
    ]
    assert pnl_realizzato_da_deal(deal, MAGIC) == pytest.approx(-2.0)


def test_pnl_include_commissioni_e_swap():
    deal = [DealFinto(MAGIC, entry=1, profit=10.0, commission=-1.5, swap=-0.5)]
    assert pnl_realizzato_da_deal(deal, MAGIC) == pytest.approx(8.0)


@pytest.mark.parametrize("vuoto", [None, [], ()])
def test_pnl_senza_deal(vuoto):
    assert pnl_realizzato_da_deal(vuoto, MAGIC) == 0.0
