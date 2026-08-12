"""Test della modalita' dry-run (P0-5).

Il test piu' importante di tutta la suite: dimostra che con DRY_RUN attivo
`order_send` non viene MAI chiamato. Il finto MT5 fa fallire il test se
qualcuno prova a inviare un ordine davvero.
"""

import pytest

import alphaedge


class RispostaCheck:
    """Imita il risultato di mt5.order_check()."""

    def __init__(self, retcode=0, comment="Done", margin=12.5, equity=10_000.0):
        self.retcode = retcode
        self.comment = comment
        self.margin = margin
        self.equity = equity


class MT5Finto:
    """Registra le chiamate. order_send fa fallire il test se invocato."""

    TRADE_ACTION_SLTP = 6      # stesso valore della costante reale di MetaTrader5

    def __init__(self):
        self.check_chiamato_con = []
        self.send_chiamato = False

    def order_check(self, request):
        self.check_chiamato_con.append(request)
        return RispostaCheck()

    def order_send(self, request):
        self.send_chiamato = True
        raise AssertionError("order_send NON deve essere chiamato in dry-run")


RICHIESTA = {
    "symbol": "US100.cash",
    "volume": 0.03,
    "price": 29_700.0,
    "sl": 29_635.0,
    "tp": 29_800.0,
    "type": 2,
}


@pytest.fixture
def mt5_finto(monkeypatch, tmp_path):
    finto = MT5Finto()
    monkeypatch.setattr(alphaedge, "mt5", finto)
    monkeypatch.setattr(alphaedge, "PERCORSO_DRY_RUN", str(tmp_path / "dry_run.csv"))
    return finto


def test_dry_run_non_invia_mai_ordini(mt5_finto, monkeypatch):
    monkeypatch.setattr(alphaedge, "DRY_RUN", True)

    risultato = alphaedge.esegui_o_valida(RICHIESTA)

    assert risultato is None, "in dry-run non deve esserci alcun risultato di invio"
    assert not mt5_finto.send_chiamato
    assert len(mt5_finto.check_chiamato_con) == 1


def test_dry_run_passa_la_richiesta_intatta_a_order_check(mt5_finto, monkeypatch):
    monkeypatch.setattr(alphaedge, "DRY_RUN", True)

    alphaedge.esegui_o_valida(RICHIESTA)

    assert mt5_finto.check_chiamato_con[0] == RICHIESTA


def test_dry_run_registra_su_file(mt5_finto, monkeypatch, tmp_path):
    percorso = tmp_path / "dry_run.csv"
    monkeypatch.setattr(alphaedge, "DRY_RUN", True)

    alphaedge.esegui_o_valida(RICHIESTA)

    contenuto = percorso.read_text(encoding="utf-8")
    assert "US100.cash" in contenuto
    assert "retcode" in contenuto          # intestazione presente
    assert "29700.0" in contenuto


def test_dry_run_accoda_senza_riscrivere_l_intestazione(mt5_finto, monkeypatch, tmp_path):
    monkeypatch.setattr(alphaedge, "DRY_RUN", True)

    alphaedge.esegui_o_valida(RICHIESTA)
    alphaedge.esegui_o_valida(RICHIESTA)

    righe = (tmp_path / "dry_run.csv").read_text(encoding="utf-8").strip().splitlines()
    assert len(righe) == 3               # 1 intestazione + 2 registrazioni
    assert righe[0].startswith("timestamp")


def test_il_default_di_configurazione_e_dry_run():
    """SAFE DEVELOPMENT MODE: senza variabili d'ambiente il dry-run e' attivo."""
    from trading_bot_skills import risk_config
    import os

    assert os.getenv("ALPHAEDGE_DRY_RUN") in (None, "1"), \
        "l'ambiente di test non deve disattivare il dry-run"
    assert risk_config.DRY_RUN is True


def test_con_dry_run_disattivato_userebbe_order_send(mt5_finto, monkeypatch):
    """Verifica il ramo opposto senza inviare nulla: il finto solleva l'errore."""
    monkeypatch.setattr(alphaedge, "DRY_RUN", False)

    with pytest.raises(AssertionError, match="order_send"):
        alphaedge.esegui_o_valida(RICHIESTA)

    assert mt5_finto.send_chiamato


RICHIESTA_SLTP = {
    "action": MT5Finto.TRADE_ACTION_SLTP,
    "position": 123_456,
    "symbol": "US100.cash",
    "sl": 29_650.0,
    "tp": 29_800.0,
}


def test_dry_run_non_sposta_lo_stop_loss(mt5_finto, monkeypatch):
    """Il trailing stop passava da order_send() diretto, scavalcando il dry-run."""
    monkeypatch.setattr(alphaedge, "DRY_RUN", True)

    risultato = alphaedge.esegui_o_valida(RICHIESTA_SLTP)

    assert risultato is None
    assert not mt5_finto.send_chiamato, "in dry-run l'SL non deve essere modificato davvero"


def test_dry_run_non_chiama_order_check_su_una_richiesta_sltp(mt5_finto, monkeypatch):
    """order_check() controlla margine e fondi: su un SLTP non ha nulla da dire."""
    monkeypatch.setattr(alphaedge, "DRY_RUN", True)

    alphaedge.esegui_o_valida(RICHIESTA_SLTP)

    assert mt5_finto.check_chiamato_con == []


def test_dry_run_non_sporca_il_csv_con_le_richieste_sltp(mt5_finto, monkeypatch, tmp_path):
    monkeypatch.setattr(alphaedge, "DRY_RUN", True)

    alphaedge.esegui_o_valida(RICHIESTA_SLTP)

    assert not (tmp_path / "dry_run.csv").exists()


def test_senza_dry_run_la_richiesta_sltp_viene_inviata(mt5_finto, monkeypatch):
    """Il gate deve fermare solo il dry-run, non il funzionamento normale."""
    monkeypatch.setattr(alphaedge, "DRY_RUN", False)

    with pytest.raises(AssertionError, match="order_send"):
        alphaedge.esegui_o_valida(RICHIESTA_SLTP)

    assert mt5_finto.send_chiamato


def test_il_profilo_di_rischio_e_quello_deciso_in_d1():
    """D1 chiusa il 2026-08-12: 0,25% dell'equity con tetto assoluto a $25."""
    assert alphaedge.PROFILO_RISCHIO.rischio_pct == 0.25
    assert alphaedge.PROFILO_RISCHIO.cap_usd == 25.0


def test_il_magic_e_impostato_e_non_nullo():
    assert isinstance(alphaedge.ALPHAEDGE_MAGIC, int)
    assert alphaedge.ALPHAEDGE_MAGIC > 0
