"""Deduplica dei segnali (P0-3).

Lo stesso segnale non deve mai produrre due ordini, nemmeno dopo un riavvio del
processo o un secondo ciclo di scansione sulla stessa candela.

La chiave e' (simbolo, direzione, ora di apertura della barra M30). Finche' la
barra corrente e' la stessa, un segnale gia' inviato non viene ripetuto; alla
barra successiva la stessa coppia simbolo/direzione torna ammessa.

Lo stato e' su file perche' deve sopravvivere a un riavvio.
"""

from __future__ import annotations

import json
import os
import time

SECONDI_IN_UN_GIORNO = 86_400


def costruisci_chiave(simbolo: str, direzione: str, ora_barra: int | float) -> str:
    return f"{simbolo}|{direzione}|{int(ora_barra)}"


def carica(percorso: str) -> dict[str, float]:
    try:
        with open(percorso, encoding="utf-8") as f:
            dati = json.load(f)
    except (OSError, ValueError):
        return {}
    return dati if isinstance(dati, dict) else {}


def salva(percorso: str, dati: dict[str, float]) -> bool:
    try:
        with open(percorso, "w", encoding="utf-8") as f:
            json.dump(dati, f)
    except OSError:
        return False
    return True


def pulisci(dati: dict[str, float], giorni: int = 7, adesso: float | None = None) -> dict[str, float]:
    """Toglie le voci piu' vecchie di `giorni`, cosi' il file non cresce all'infinito."""
    adesso = time.time() if adesso is None else adesso
    limite = adesso - giorni * SECONDI_IN_UN_GIORNO
    return {chiave: quando for chiave, quando in dati.items() if quando >= limite}


def gia_inviato(percorso: str, chiave: str) -> bool:
    return chiave in carica(percorso)


def registra(percorso: str, chiave: str, adesso: float | None = None) -> bool:
    """Registra la chiave come gia' usata. Restituisce False se era gia' presente.

    Il valore di ritorno permette al chiamante di trattare la registrazione come
    un "prendi il posto": solo chi ottiene True puo' procedere con l'ordine.
    """
    dati = carica(percorso)
    if chiave in dati:
        return False
    dati[chiave] = time.time() if adesso is None else adesso
    salva(percorso, pulisci(dati, adesso=adesso))
    return True


def dimentica(percorso: str, chiave: str) -> bool:
    """Rimuove una chiave: serve quando l'invio fallisce e il segnale resta valido."""
    dati = carica(percorso)
    if chiave not in dati:
        return False
    del dati[chiave]
    return salva(percorso, dati)


def elimina_file(percorso: str) -> None:
    try:
        os.remove(percorso)
    except OSError:
        pass
