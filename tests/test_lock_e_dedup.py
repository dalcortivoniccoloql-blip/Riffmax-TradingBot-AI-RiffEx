"""Test di lock di processo e deduplica (P0-3).

Usano tmp_path, quindi non toccano nulla del progetto reale.
"""

import json
import time

import pytest

from trading_bot_skills import dedup, process_lock


# ==========================================================================
# LOCK DI PROCESSO
# ==========================================================================

def test_lock_acquisito_su_file_inesistente(tmp_path):
    esito = process_lock.acquisisci(str(tmp_path / "bot.lock"), pid=111)
    assert esito.acquisito


def test_seconda_istanza_bloccata_se_la_prima_e_viva(tmp_path):
    percorso = str(tmp_path / "bot.lock")
    process_lock.acquisisci(percorso, pid=111, controllo_vivo=lambda pid: True)

    esito = process_lock.acquisisci(percorso, pid=222, controllo_vivo=lambda pid: True)

    assert not esito.acquisito
    assert esito.pid_titolare == 111
    assert "gia' in esecuzione" in esito.motivo


def test_lock_orfano_rimosso_se_il_titolare_e_morto(tmp_path):
    """Dopo un crash il lock resta sul disco: non deve bloccare per sempre."""
    percorso = str(tmp_path / "bot.lock")
    process_lock.acquisisci(percorso, pid=111, controllo_vivo=lambda pid: True)

    esito = process_lock.acquisisci(percorso, pid=222, controllo_vivo=lambda pid: False)

    assert esito.acquisito
    assert process_lock.leggi_lock(percorso)["pid"] == 222


def test_stesso_processo_riacquisisce_senza_errori(tmp_path):
    percorso = str(tmp_path / "bot.lock")
    process_lock.acquisisci(percorso, pid=111)
    assert process_lock.acquisisci(percorso, pid=111).acquisito


def test_lock_contiene_pid_e_timestamp(tmp_path):
    percorso = str(tmp_path / "bot.lock")
    process_lock.acquisisci(percorso, pid=111)
    contenuto = json.loads((tmp_path / "bot.lock").read_text())
    assert contenuto["pid"] == 111
    assert contenuto["creato"] > 0


def test_rilascio_solo_dal_proprietario(tmp_path):
    percorso = str(tmp_path / "bot.lock")
    process_lock.acquisisci(percorso, pid=111)

    assert not process_lock.rilascia(percorso, pid=222)
    assert process_lock.leggi_lock(percorso) is not None

    assert process_lock.rilascia(percorso, pid=111)
    assert process_lock.leggi_lock(percorso) is None


def test_lock_corrotto_trattato_come_assente(tmp_path):
    percorso = tmp_path / "bot.lock"
    percorso.write_text("non e' json")
    assert process_lock.acquisisci(str(percorso), pid=111).acquisito


def test_processo_vivo_riconosce_se_stesso():
    import os
    assert process_lock.processo_vivo(os.getpid())


@pytest.mark.parametrize("pid", [0, -1])
def test_processo_vivo_rifiuta_pid_non_validi(pid):
    assert not process_lock.processo_vivo(pid)


# ==========================================================================
# DEDUPLICA
# ==========================================================================

def test_chiave_dipende_da_simbolo_direzione_e_barra():
    a = dedup.costruisci_chiave("US100.cash", "BUY", 1_786_000_000)
    b = dedup.costruisci_chiave("US100.cash", "SELL", 1_786_000_000)
    c = dedup.costruisci_chiave("XAUUSD", "BUY", 1_786_000_000)
    d = dedup.costruisci_chiave("US100.cash", "BUY", 1_786_001_800)
    assert len({a, b, c, d}) == 4


def test_primo_invio_consentito_secondo_bloccato(tmp_path):
    percorso = str(tmp_path / "dedup.json")
    chiave = dedup.costruisci_chiave("US100.cash", "BUY", 1_786_000_000)

    assert dedup.registra(percorso, chiave) is True
    assert dedup.registra(percorso, chiave) is False
    assert dedup.gia_inviato(percorso, chiave)


def test_la_barra_successiva_e_di_nuovo_ammessa(tmp_path):
    """Stessa coppia simbolo/direzione, candela M30 diversa: si puo' rientrare."""
    percorso = str(tmp_path / "dedup.json")
    dedup.registra(percorso, dedup.costruisci_chiave("US100.cash", "BUY", 1_786_000_000))

    nuova = dedup.costruisci_chiave("US100.cash", "BUY", 1_786_001_800)
    assert dedup.registra(percorso, nuova) is True


def test_lo_stato_sopravvive_al_riavvio(tmp_path):
    """Nessuno stato in memoria: rileggendo il file la chiave e' ancora nota."""
    percorso = str(tmp_path / "dedup.json")
    chiave = dedup.costruisci_chiave("EURUSD", "SELL", 1_786_000_000)
    dedup.registra(percorso, chiave)

    # simula un processo nuovo: nessuna variabile condivisa, solo il file
    assert dedup.gia_inviato(percorso, chiave)
    assert dedup.registra(percorso, chiave) is False


def test_dimentica_riapre_il_segnale(tmp_path):
    """Se l'invio fallisce la chiave va liberata, altrimenti il segnale si perde."""
    percorso = str(tmp_path / "dedup.json")
    chiave = dedup.costruisci_chiave("US100.cash", "BUY", 1_786_000_000)
    dedup.registra(percorso, chiave)

    assert dedup.dimentica(percorso, chiave)
    assert dedup.registra(percorso, chiave) is True


def test_pulizia_delle_voci_vecchie():
    adesso = 1_786_000_000.0
    dati = {
        "recente": adesso - 3_600,
        "vecchia": adesso - 30 * dedup.SECONDI_IN_UN_GIORNO,
    }
    ripulito = dedup.pulisci(dati, giorni=7, adesso=adesso)
    assert "recente" in ripulito
    assert "vecchia" not in ripulito


def test_la_registrazione_pulisce_il_file(tmp_path):
    percorso = str(tmp_path / "dedup.json")
    dedup.salva(percorso, {"vecchissima": time.time() - 90 * dedup.SECONDI_IN_UN_GIORNO})
    dedup.registra(percorso, "nuova")
    assert "vecchissima" not in dedup.carica(percorso)


def test_file_corrotto_trattato_come_vuoto(tmp_path):
    percorso = tmp_path / "dedup.json"
    percorso.write_text("{rotto")
    assert dedup.carica(str(percorso)) == {}
    assert dedup.registra(str(percorso), "chiave") is True
