"""Test della resilienza alla disconnessione da MT5 (P0-8).

Una disconnessione non somiglia a un errore: somiglia a un mercato tranquillo.
copy_rates_from_pos() restituisce dati vecchi o vuoti, la strategia non trova
nulla di interessante e stampa NEUTRAL su tutta la watchlist senza che nessuno
si accorga che il terminale era staccato. Per questo il ciclo va SALTATO, non
proseguito con dati parziali.
"""

import pytest

import alphaedge


class Terminale:
    def __init__(self, connected=True):
        self.connected = connected


class MT5Finto:
    """Terminale finto che diventa sano dopo `sano_dopo` tentativi."""

    def __init__(self, sano_dopo=0, solleva=False):
        self.sano_dopo = sano_dopo
        self.solleva = solleva
        self.tentativi = 0
        self.shutdown_chiamato = 0

    def terminal_info(self):
        if self.solleva:
            raise RuntimeError("terminale non raggiungibile")
        return Terminale(connected=self.tentativi >= self.sano_dopo)

    def account_info(self):
        if self.tentativi >= self.sano_dopo:
            return object()
        return None

    def shutdown(self):
        self.shutdown_chiamato += 1


@pytest.fixture
def niente_attese(monkeypatch):
    """Il backoff e' reale in produzione, inutile nei test."""
    monkeypatch.setattr(alphaedge.time, "sleep", lambda _: None)


@pytest.fixture
def connessioni(monkeypatch):
    """Conta le riconnessioni e fa avanzare il finto MT5 verso lo stato sano."""
    chiamate = []

    def falsa_connect():
        chiamate.append(1)
        alphaedge.mt5.tentativi += 1

    monkeypatch.setattr(alphaedge, "connect_mt5", falsa_connect)
    return chiamate


# ---------------------------------------------------------------------------
# connessione_sana
# ---------------------------------------------------------------------------

def test_connessione_sana_con_terminale_connesso(monkeypatch):
    monkeypatch.setattr(alphaedge, "mt5", MT5Finto(sano_dopo=0))
    assert alphaedge.connessione_sana()


def test_connessione_non_sana_se_il_terminale_e_staccato(monkeypatch):
    monkeypatch.setattr(alphaedge, "mt5", MT5Finto(sano_dopo=99))
    assert not alphaedge.connessione_sana()


def test_connessione_non_sana_se_mt5_solleva_eccezione(monkeypatch):
    """Un'eccezione non deve propagarsi: vale come "non connesso"."""
    monkeypatch.setattr(alphaedge, "mt5", MT5Finto(solleva=True))
    assert not alphaedge.connessione_sana()


# ---------------------------------------------------------------------------
# assicura_connessione: retry con backoff
# ---------------------------------------------------------------------------

def test_se_la_connessione_e_gia_sana_non_riconnette(monkeypatch, niente_attese, connessioni):
    monkeypatch.setattr(alphaedge, "mt5", MT5Finto(sano_dopo=0))
    assert alphaedge.assicura_connessione()
    assert connessioni == [], "nessuna riconnessione doveva essere tentata"


def test_riconnette_e_riesce_al_primo_tentativo(monkeypatch, niente_attese, connessioni):
    monkeypatch.setattr(alphaedge, "mt5", MT5Finto(sano_dopo=1))
    assert alphaedge.assicura_connessione()
    assert len(connessioni) == 1


def test_riconnette_e_riesce_al_secondo_tentativo(monkeypatch, niente_attese, connessioni):
    monkeypatch.setattr(alphaedge, "mt5", MT5Finto(sano_dopo=2))
    assert alphaedge.assicura_connessione()
    assert len(connessioni) == 2


def test_dopo_tutti_i_tentativi_falliti_il_ciclo_viene_saltato(monkeypatch, niente_attese, connessioni):
    """Il valore False e' quello che fa uscire run_alphaedge senza analizzare."""
    monkeypatch.setattr(alphaedge, "mt5", MT5Finto(sano_dopo=999))
    assert not alphaedge.assicura_connessione()
    assert len(connessioni) == alphaedge.MAX_TENTATIVI_CONNESSIONE


def test_non_tenta_piu_volte_del_massimo_configurato(monkeypatch, niente_attese, connessioni):
    monkeypatch.setattr(alphaedge, "mt5", MT5Finto(sano_dopo=999))
    alphaedge.assicura_connessione()
    assert len(connessioni) <= alphaedge.MAX_TENTATIVI_CONNESSIONE


def test_chiude_la_sessione_prima_di_ogni_riconnessione(monkeypatch, niente_attese, connessioni):
    """Riconnettersi senza shutdown lascia sessioni mezze aperte."""
    finto = MT5Finto(sano_dopo=999)
    monkeypatch.setattr(alphaedge, "mt5", finto)
    alphaedge.assicura_connessione()
    assert finto.shutdown_chiamato == alphaedge.MAX_TENTATIVI_CONNESSIONE


def test_un_errore_in_riconnessione_non_interrompe_i_tentativi(monkeypatch, niente_attese):
    """connect_mt5() che esplode deve essere assorbito, non propagato."""
    monkeypatch.setattr(alphaedge, "mt5", MT5Finto(sano_dopo=999))

    def connect_che_esplode():
        raise RuntimeError("credenziali rifiutate")

    monkeypatch.setattr(alphaedge, "connect_mt5", connect_che_esplode)
    assert not alphaedge.assicura_connessione()


def test_il_backoff_raddoppia_a_ogni_tentativo(monkeypatch, connessioni):
    attese = []
    monkeypatch.setattr(alphaedge.time, "sleep", attese.append)
    monkeypatch.setattr(alphaedge, "mt5", MT5Finto(sano_dopo=999))

    alphaedge.assicura_connessione()

    base = alphaedge.BACKOFF_CONNESSIONE_SECONDI
    assert attese == [base * (2 ** i) for i in range(len(attese))]
