"""Test del cancello di validazione ordini (P0-6).

Il punto centrale: nessun ordine senza stop loss valido deve poter passare.
"""

import pytest

from trading_bot_skills.order_validation import BUY, SELL, valida_ordine

# valori plausibili per US100.cash
BASE = dict(digits=2, volume_min=0.01, volume_max=1000.0, volume_step=0.01)


def buy(**override):
    parametri = dict(direzione=BUY, prezzo=29_700.0, sl=29_635.0, tp=29_800.0, volume=0.03, **BASE)
    parametri.update(override)
    return valida_ordine(**parametri)


def sell(**override):
    parametri = dict(direzione=SELL, prezzo=29_700.0, sl=29_765.0, tp=29_600.0, volume=0.03, **BASE)
    parametri.update(override)
    return valida_ordine(**parametri)


# --------------------------------------------------------------------------
# casi validi
# --------------------------------------------------------------------------

def test_buy_valido():
    esito = buy()
    assert esito.valido
    assert esito.motivo == "OK"


def test_sell_valido():
    assert sell().valido


def test_prezzi_arrotondati_ai_digits_del_simbolo():
    esito = buy(prezzo=29_700.123456, sl=29_635.987654, tp=29_800.555555)
    assert esito.prezzo == 29_700.12
    assert esito.sl == 29_635.99
    assert esito.tp == 29_800.56


def test_arrotondamento_a_cinque_decimali_per_il_forex():
    esito = valida_ordine(BUY, 1.153612345, 1.152012345, 1.156012345, 0.02,
                          digits=5, volume_min=0.01, volume_max=50.0, volume_step=0.01)
    assert esito.valido
    assert esito.sl == 1.15201


# --------------------------------------------------------------------------
# SL e TP obbligatori
# --------------------------------------------------------------------------

@pytest.mark.parametrize("valore", [0, 0.0, None])
def test_stop_loss_mancante_rifiutato(valore):
    esito = buy(sl=valore)
    assert not esito.valido
    assert "Stop loss obbligatorio" in esito.motivo


@pytest.mark.parametrize("valore", [0, 0.0, None])
def test_take_profit_mancante_rifiutato(valore):
    esito = buy(tp=valore)
    assert not esito.valido
    assert "Take profit obbligatorio" in esito.motivo


def test_stop_loss_nan_rifiutato():
    assert not buy(sl=float("nan")).valido


# --------------------------------------------------------------------------
# lato dello stop
# --------------------------------------------------------------------------

def test_buy_con_stop_sopra_il_prezzo_rifiutato():
    esito = buy(sl=29_750.0)
    assert not esito.valido
    assert "sotto il prezzo" in esito.motivo


def test_buy_con_stop_uguale_al_prezzo_rifiutato():
    assert not buy(sl=29_700.0).valido


def test_buy_con_target_sotto_il_prezzo_rifiutato():
    esito = buy(tp=29_600.0)
    assert not esito.valido
    assert "sopra il prezzo" in esito.motivo


def test_sell_con_stop_sotto_il_prezzo_rifiutato():
    esito = sell(sl=29_650.0)
    assert not esito.valido
    assert "sopra il prezzo" in esito.motivo


def test_sell_con_target_sopra_il_prezzo_rifiutato():
    assert not sell(tp=29_800.0).valido


def test_sl_e_tp_invertiti_rifiutati():
    """Errore classico: scambiare stop e target."""
    assert not buy(sl=29_800.0, tp=29_635.0).valido


# --------------------------------------------------------------------------
# distanza minima imposta dal broker
# --------------------------------------------------------------------------

def test_stop_troppo_vicino_rifiutato():
    esito = buy(sl=29_699.5, distanza_minima_stop=5.0)
    assert not esito.valido
    assert "troppo vicino" in esito.motivo


def test_target_troppo_vicino_rifiutato():
    esito = buy(tp=29_701.0, distanza_minima_stop=5.0)
    assert not esito.valido


def test_distanza_zero_non_impone_vincoli():
    """FTMO oggi espone trade_stops_level = 0, ma non va dato per scontato."""
    assert buy(sl=29_699.99, distanza_minima_stop=0.0).valido


# --------------------------------------------------------------------------
# volume
# --------------------------------------------------------------------------

@pytest.mark.parametrize("volume", [0.0, -0.01])
def test_volume_non_positivo_rifiutato(volume):
    assert not buy(volume=volume).valido


def test_volume_sotto_il_minimo_rifiutato():
    esito = buy(volume=0.005)
    assert not esito.valido
    assert "sotto il minimo" in esito.motivo


def test_volume_sopra_il_massimo_rifiutato():
    esito = buy(volume=5000.0)
    assert not esito.valido
    assert "sopra il massimo" in esito.motivo


def test_volume_non_multiplo_del_passo_rifiutato():
    esito = buy(volume=0.035)
    assert not esito.valido
    assert "multiplo" in esito.motivo


def test_volume_multiplo_valido_nonostante_i_float():
    """0.03 in virgola mobile non e' esattamente 3*0.01."""
    assert buy(volume=0.03).valido


# --------------------------------------------------------------------------
# direzione e prezzo
# --------------------------------------------------------------------------

@pytest.mark.parametrize("direzione", ["LONG", "buy", "", None, "NEUTRAL"])
def test_direzione_non_valida_rifiutata(direzione):
    assert not buy(direzione=direzione).valido


@pytest.mark.parametrize("prezzo", [0.0, -1.0, float("inf")])
def test_prezzo_non_valido_rifiutato(prezzo):
    assert not buy(prezzo=prezzo).valido
