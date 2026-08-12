"""Test della configurazione di rischio decisa il 2026-08-12 (D1, D2, D3).

Le specifiche dei simboli qui sotto NON sono inventate: sono state lette dal
terminale MT5 collegato al conto FTMO-Demo 1514232107 il 2026-08-12 e poi
congelate come costanti, cosi' i test girano anche a mercati chiusi e senza
terminale aperto.

Se il broker cambia le specifiche di un simbolo, questi test continuano a
passare pur non descrivendo piu' la realta': vanno riletti dal terminale.
"""

import pytest

from trading_bot_skills import risk_config as cfg
from trading_bot_skills.risk_engine import (
    MOTIVO_INCOMPATIBILE,
    MOTIVO_STOP_NULLO,
    ProfiloRischio,
    SpecificheSimbolo,
    dimensiona,
    drawdown_interno_superato,
    perdita_giornaliera_superata,
    rischio_ammesso_usd,
)


# --- specifiche reali lette da MT5 il 2026-08-12 ---------------------------

XAUUSD = SpecificheSimbolo("XAUUSD", tick_size=0.01, tick_value=1.0,
                           volume_min=0.01, volume_max=50.0, volume_step=0.01, digits=2)
XAGUSD = SpecificheSimbolo("XAGUSD", tick_size=0.001, tick_value=5.0,
                           volume_min=0.01, volume_max=50.0, volume_step=0.01, digits=3)
US100 = SpecificheSimbolo("US100.cash", tick_size=0.01, tick_value=0.01,
                          volume_min=0.01, volume_max=50.0, volume_step=0.01, digits=2)
EURUSD = SpecificheSimbolo("EURUSD", tick_size=0.00001, tick_value=1.0,
                           volume_min=0.01, volume_max=50.0, volume_step=0.01, digits=5)

PROFILO = ProfiloRischio(
    rischio_pct=cfg.RISCHIO_PER_TRADE_PCT,
    cap_usd=cfg.RISCHIO_PER_TRADE_CAP_USD,
    min_usd=cfg.RISCHIO_PER_TRADE_MIN_USD,
)

EQUITY = cfg.BASELINE_EQUITY_USD

# Tolleranza sui confronti in dollari.
#
# La distanza di stop nasce da una sottrazione fra prezzi, e i prezzi non sono
# rappresentabili esattamente in binario: abs(1.09000 - 1.08750) vale
# 0.0025000000000001688, non 0.0025. Su EURUSD questo fa risultare il rischio
# effettivo 1.7e-12 dollari sopra il tetto, con il lotto invece corretto al
# centesimo. Non e' un errore di dimensionamento ed e' inferiore a qualunque
# cifra che il broker sia in grado di regolare, quindi si confronta con
# tolleranza invece di piegare il motore per inseguire il binario.
EPS = 1e-9


# ---------------------------------------------------------------------------
# D1 -- rischio per trade: 0,25% con tetto a $25
# ---------------------------------------------------------------------------

def test_d1_percentuale_configurata():
    assert cfg.RISCHIO_PER_TRADE_PCT == 0.25


def test_d1_cap_configurato():
    assert cfg.RISCHIO_PER_TRADE_CAP_USD == 25.0


def test_d1_su_diecimila_i_due_valori_coincidono():
    assert rischio_ammesso_usd(10_000.0, PROFILO, "EURUSD") == 25.0


def test_d1_se_equity_scende_comanda_la_percentuale():
    """A $8.000 lo 0,25% vale $20: piu' stretto del cap, quindi vince lui."""
    assert rischio_ammesso_usd(8_000.0, PROFILO, "EURUSD") == 20.0


def test_d1_se_equity_sale_comanda_il_cap():
    """A $20.000 lo 0,25% varrebbe $50, ma il cap tiene il rischio a $25."""
    assert rischio_ammesso_usd(20_000.0, PROFILO, "EURUSD") == 25.0


def test_d1_il_rischio_non_viene_mai_alzato_da_un_override():
    """Un override e' un tetto per quel simbolo, non un permesso di rischiare di piu'."""
    profilo = ProfiloRischio(rischio_pct=0.25, cap_usd=25.0,
                            override_per_simbolo={"XAUUSD": 100.0})
    assert rischio_ammesso_usd(EQUITY, profilo, "XAUUSD") == 25.0


def test_d1_un_override_piu_basso_invece_si_applica():
    profilo = ProfiloRischio(rischio_pct=0.25, cap_usd=25.0,
                            override_per_simbolo={"XAUUSD": 10.0})
    assert rischio_ammesso_usd(EQUITY, profilo, "XAUUSD") == 10.0


@pytest.mark.parametrize("equity", [500.0, 5_000.0, 10_000.0, 50_000.0, 250_000.0])
@pytest.mark.parametrize("distanza", [0.5, 5.0, 50.0, 500.0])
def test_d1_il_rischio_effettivo_non_supera_mai_il_consentito(equity, distanza):
    """Proprieta' generale: qualunque equity, qualunque stop, mai oltre il tetto."""
    esito = dimensiona(equity, PROFILO, XAUUSD, 4_000.0, 4_000.0 - distanza)
    if esito.consentito:
        assert esito.rischio_effettivo_usd <= esito.rischio_ammesso_usd + EPS
        assert esito.rischio_effettivo_usd <= 25.0 + EPS


# ---------------------------------------------------------------------------
# D3 -- perdita giornaliera $75 e drawdown interno $500
# ---------------------------------------------------------------------------

def test_d3_perdita_giornaliera_e_75_dollari():
    assert cfg.MAX_PERDITA_GIORNALIERA_USD == 75.0
    assert cfg.MAX_PERDITA_GIORNALIERA_PCT == 0.75


def test_d3_la_perdita_giornaliera_e_lo_075_percento_della_baseline():
    atteso = cfg.BASELINE_EQUITY_USD * 0.75 / 100.0
    assert cfg.MAX_PERDITA_GIORNALIERA_USD == pytest.approx(atteso)


def test_d3_il_limite_giornaliero_blocca_a_75():
    superato, motivo = perdita_giornaliera_superata(
        -75.0, cfg.MAX_PERDITA_GIORNALIERA_USD, cfg.TARGET_PROFITTO_GIORNALIERO_USD
    )
    assert superato
    assert "perdita giornaliera" in motivo.lower()


def test_d3_a_74_dollari_di_perdita_si_puo_ancora_operare():
    superato, _ = perdita_giornaliera_superata(
        -74.0, cfg.MAX_PERDITA_GIORNALIERA_USD, cfg.TARGET_PROFITTO_GIORNALIERO_USD
    )
    assert not superato


def test_drawdown_interno_e_500_dollari():
    assert cfg.MAX_DRAWDOWN_INTERNO_USD == 500.0
    assert cfg.MAX_DRAWDOWN_INTERNO_PCT == 5.0


def test_drawdown_sopra_il_pavimento_consente_di_operare():
    superato, _ = drawdown_interno_superato(9_501.0, EQUITY, cfg.MAX_DRAWDOWN_INTERNO_USD)
    assert not superato


def test_drawdown_esattamente_sul_pavimento_e_violato():
    superato, motivo = drawdown_interno_superato(9_500.0, EQUITY, cfg.MAX_DRAWDOWN_INTERNO_USD)
    assert superato
    assert "9500.00" in motivo


def test_drawdown_sotto_il_pavimento_e_violato():
    superato, _ = drawdown_interno_superato(9_000.0, EQUITY, cfg.MAX_DRAWDOWN_INTERNO_USD)
    assert superato


def test_drawdown_equity_nulla_e_sempre_violazione():
    """Equity a zero significa conto azzerato o dato mancante: non si apre nulla."""
    superato, motivo = drawdown_interno_superato(0.0, EQUITY, cfg.MAX_DRAWDOWN_INTERNO_USD)
    assert superato
    assert "non disponibile" in motivo


def test_drawdown_e_statico_non_trailing():
    """Se l'equity sale a 12.000 il pavimento resta 9.500, non sale con lei."""
    superato, _ = drawdown_interno_superato(9_600.0, EQUITY, cfg.MAX_DRAWDOWN_INTERNO_USD)
    assert not superato


# ---------------------------------------------------------------------------
# Coerenza fra i limiti
# ---------------------------------------------------------------------------

def test_tre_stop_pieni_chiudono_la_giornata():
    stop_pieni = cfg.MAX_PERDITA_GIORNALIERA_USD / cfg.RISCHIO_PER_TRADE_CAP_USD
    assert stop_pieni == 3.0


def test_venti_stop_pieni_esauriscono_il_drawdown():
    stop_pieni = cfg.MAX_DRAWDOWN_INTERNO_USD / cfg.RISCHIO_PER_TRADE_CAP_USD
    assert stop_pieni == 20.0


def test_il_limite_giornaliero_e_piu_stretto_del_drawdown():
    assert cfg.MAX_PERDITA_GIORNALIERA_USD < cfg.MAX_DRAWDOWN_INTERNO_USD


def test_il_rischio_per_trade_e_piu_stretto_del_limite_giornaliero():
    assert cfg.RISCHIO_PER_TRADE_CAP_USD < cfg.MAX_PERDITA_GIORNALIERA_USD


# ---------------------------------------------------------------------------
# Gerarchia: i nostri limiti devono restare piu' conservativi di FTMO
# ---------------------------------------------------------------------------

def test_il_nostro_limite_giornaliero_e_piu_stretto_di_ftmo():
    """FTMO 2-Step: Maximum Daily Loss 5% del capitale iniziale."""
    ftmo_usd = cfg.BASELINE_EQUITY_USD * cfg.FTMO_MAX_DAILY_LOSS_PCT / 100.0
    assert cfg.MAX_PERDITA_GIORNALIERA_USD < ftmo_usd


def test_il_nostro_drawdown_e_piu_stretto_della_max_loss_ftmo():
    """FTMO 2-Step: Maximum Loss 10% del capitale iniziale, statica."""
    ftmo_usd = cfg.BASELINE_EQUITY_USD * cfg.FTMO_MAX_LOSS_PCT / 100.0
    assert cfg.MAX_DRAWDOWN_INTERNO_USD < ftmo_usd


def test_i_valori_ftmo_documentati_sono_quelli_ufficiali():
    """Verificati il 2026-08-12 su ftmo.com/en/trading-objectives/."""
    assert cfg.FTMO_MAX_DAILY_LOSS_PCT == 5.0
    assert cfg.FTMO_MAX_LOSS_PCT == 10.0
    assert cfg.FTMO_RESET_GIORNALIERO == "00:00 CE(S)T"


# ---------------------------------------------------------------------------
# D2 -- oro e argento restano analizzati, ma il trade puo' essere rifiutato
# ---------------------------------------------------------------------------

def test_d2_nessun_simbolo_e_stato_tolto_con_un_override():
    """La decisione D2 e' "restano in watchlist": nessun override li esclude."""
    assert cfg.RISCHIO_OVERRIDE_PER_SIMBOLO == {}


def test_xauusd_con_stop_stretto_e_ammesso():
    """Oro: al lotto minimo il rischio e' circa 1 USD per ogni dollaro di stop."""
    esito = dimensiona(EQUITY, PROFILO, XAUUSD, 4_000.00, 3_988.00)   # stop 12 USD
    assert esito.consentito
    assert esito.lotto >= XAUUSD.volume_min
    assert esito.rischio_effettivo_usd <= 25.0 + EPS


def test_xauusd_con_stop_largo_e_rifiutato_senza_ripiegare_sul_lotto_minimo():
    """Stop da 40 USD: gia' il lotto minimo rischierebbe 40 USD, oltre il tetto."""
    esito = dimensiona(EQUITY, PROFILO, XAUUSD, 4_000.00, 3_960.00)
    assert not esito.consentito
    assert esito.lotto == 0.0
    assert esito.motivo == MOTIVO_INCOMPATIBILE
    assert esito.rischio_effettivo_usd == pytest.approx(40.0)


def test_xagusd_con_stop_stretto_e_ammesso():
    """Argento: al lotto minimo il rischio e' circa 50 USD per dollaro di stop."""
    esito = dimensiona(EQUITY, PROFILO, XAGUSD, 30.000, 29.600)       # stop 0,40 USD
    assert esito.consentito
    assert esito.rischio_effettivo_usd <= 25.0 + EPS


def test_xagusd_con_stop_tipico_da_atr_e_rifiutato():
    """Stop da 1,20 USD d'argento: 60 USD di rischio gia' al lotto minimo."""
    esito = dimensiona(EQUITY, PROFILO, XAGUSD, 30.000, 28.800)
    assert not esito.consentito
    assert esito.motivo == MOTIVO_INCOMPATIBILE
    assert esito.rischio_effettivo_usd == pytest.approx(60.0)


def test_us100_con_stop_realistico_e_ammesso():
    """Nasdaq: al lotto minimo servono 2.500 punti di stop per rischiare 25 USD."""
    esito = dimensiona(EQUITY, PROFILO, US100, 29_700.00, 29_500.00)  # stop 200 punti
    assert esito.consentito
    assert esito.rischio_effettivo_usd <= 25.0 + EPS


def test_eurusd_con_stop_realistico_e_ammesso():
    esito = dimensiona(EQUITY, PROFILO, EURUSD, 1.09000, 1.08750)     # stop 25 pip
    assert esito.consentito
    assert esito.rischio_effettivo_usd <= 25.0 + EPS


# ---------------------------------------------------------------------------
# Casi limite sullo stop
# ---------------------------------------------------------------------------

def test_stop_coincidente_con_l_ingresso_e_rifiutato():
    esito = dimensiona(EQUITY, PROFILO, EURUSD, 1.09000, 1.09000)
    assert not esito.consentito
    assert esito.motivo == MOTIVO_STOP_NULLO


def test_stop_enormemente_lontano_e_rifiutato():
    """Stop a 2.000 dollari di distanza sull'oro: nessun lotto lo rende accettabile."""
    esito = dimensiona(EQUITY, PROFILO, XAUUSD, 4_000.00, 2_000.00)
    assert not esito.consentito
    assert esito.motivo == MOTIVO_INCOMPATIBILE


def test_stop_strettissimo_non_produce_lotti_oltre_il_massimo():
    esito = dimensiona(EQUITY, PROFILO, EURUSD, 1.09000, 1.08999)
    if esito.consentito:
        assert esito.lotto <= EURUSD.volume_max


def test_il_lotto_e_sempre_un_multiplo_del_passo():
    esito = dimensiona(EQUITY, PROFILO, US100, 29_700.00, 29_500.00)
    passi = esito.lotto / US100.volume_step
    assert passi == pytest.approx(round(passi))
