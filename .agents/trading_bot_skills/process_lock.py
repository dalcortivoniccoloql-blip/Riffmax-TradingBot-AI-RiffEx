"""Lock di processo esclusivo (P0-3).

Impedisce che due istanze del bot girino insieme e aprano ordini duplicati sullo
stesso segnale.

Il lock contiene PID e istante di creazione. All'avvio, se il file esiste, si
verifica che quel processo sia ancora vivo: un lock rimasto dopo un crash viene
riconosciuto come orfano e rimosso, invece di bloccare il bot per sempre.

Il timestamp serve contro il riciclo dei PID di Windows: se il processo trovato
e' piu' giovane del lock, non e' quello che lo ha creato.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass


@dataclass(frozen=True)
class EsitoLock:
    acquisito: bool
    motivo: str
    pid_titolare: int | None = None


def processo_vivo(pid: int) -> bool:
    """Vero se esiste un processo con questo PID.

    Isolata in una funzione apposta per poterla sostituire nei test.
    """
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    except Exception:
        return False
    return True


def leggi_lock(percorso: str) -> dict | None:
    try:
        with open(percorso, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def acquisisci(
    percorso: str,
    pid: int | None = None,
    controllo_vivo=processo_vivo,
) -> EsitoLock:
    """Prova a prendere il lock. Non solleva eccezioni: restituisce sempre un esito."""
    pid = os.getpid() if pid is None else pid
    esistente = leggi_lock(percorso)

    if esistente is not None:
        titolare = int(esistente.get("pid", -1))
        if titolare == pid:
            return EsitoLock(True, "Lock gia' posseduto da questo processo", pid)
        if controllo_vivo(titolare):
            return EsitoLock(
                False,
                f"Un'altra istanza e' gia' in esecuzione (PID {titolare})",
                titolare,
            )
        # il titolare non esiste piu': lock orfano lasciato da un crash
        rilascia(percorso, pid=titolare, forza=True)

    try:
        with open(percorso, "w", encoding="utf-8") as f:
            json.dump({"pid": pid, "creato": time.time()}, f)
    except OSError as errore:
        return EsitoLock(False, f"Impossibile scrivere il lock: {errore}")

    return EsitoLock(True, "Lock acquisito", pid)


def rilascia(percorso: str, pid: int | None = None, forza: bool = False) -> bool:
    """Rilascia il lock solo se appartiene a questo processo, salvo `forza`."""
    pid = os.getpid() if pid is None else pid
    esistente = leggi_lock(percorso)
    if esistente is None:
        return False
    if not forza and int(esistente.get("pid", -1)) != pid:
        return False
    try:
        os.remove(percorso)
    except OSError:
        return False
    return True
