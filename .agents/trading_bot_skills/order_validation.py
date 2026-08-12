"""Cancello di validazione eseguito subito prima di inviare o validare un ordine.

Funzioni pure, nessuna chiamata a MetaTrader5.

Regola non negoziabile: stop loss e take profit sono SEMPRE obbligatori. Un
ordine senza stop, o con lo stop dalla parte sbagliata del prezzo, non deve mai
raggiungere il broker. Se una sola condizione fallisce il risultato e' NO TRADE.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

BUY = "BUY"
SELL = "SELL"

MOTIVO_OK = "OK"


@dataclass(frozen=True)
class EsitoValidazione:
    valido: bool
    motivo: str
    prezzo: float = 0.0
    sl: float = 0.0
    tp: float = 0.0
    volume: float = 0.0


def _multiplo_del_passo(volume: float, passo: float) -> bool:
    if passo <= 0:
        return True
    return abs(volume / passo - round(volume / passo)) < 1e-6


def valida_ordine(
    direzione: str,
    prezzo: float,
    sl: float,
    tp: float,
    volume: float,
    digits: int,
    volume_min: float,
    volume_max: float,
    volume_step: float,
    distanza_minima_stop: float = 0.0,
) -> EsitoValidazione:
    """Verifica un ordine completo e restituisce i valori arrotondati.

    `distanza_minima_stop` e' gia' espressa in unita' di prezzo
    (trade_stops_level * point), non in punti.
    """
    if direzione not in (BUY, SELL):
        return EsitoValidazione(False, f"Direzione non valida: {direzione!r}")

    if prezzo <= 0 or not math.isfinite(prezzo):
        return EsitoValidazione(False, "Prezzo di ingresso non valido")

    # --- SL e TP obbligatori ------------------------------------------------
    if not sl or not math.isfinite(sl):
        return EsitoValidazione(False, "Stop loss obbligatorio e mancante")
    if not tp or not math.isfinite(tp):
        return EsitoValidazione(False, "Take profit obbligatorio e mancante")

    # --- lato corretto ------------------------------------------------------
    if direzione == BUY:
        if sl >= prezzo:
            return EsitoValidazione(False, "BUY: lo stop loss deve stare sotto il prezzo")
        if tp <= prezzo:
            return EsitoValidazione(False, "BUY: il take profit deve stare sopra il prezzo")
    else:
        if sl <= prezzo:
            return EsitoValidazione(False, "SELL: lo stop loss deve stare sopra il prezzo")
        if tp >= prezzo:
            return EsitoValidazione(False, "SELL: il take profit deve stare sotto il prezzo")

    # --- distanza minima imposta dal broker ---------------------------------
    if distanza_minima_stop > 0:
        if abs(prezzo - sl) < distanza_minima_stop:
            return EsitoValidazione(False, "Stop loss troppo vicino al prezzo")
        if abs(tp - prezzo) < distanza_minima_stop:
            return EsitoValidazione(False, "Take profit troppo vicino al prezzo")

    # --- volume -------------------------------------------------------------
    if volume <= 0:
        return EsitoValidazione(False, "Volume nullo o negativo")
    if volume < volume_min:
        return EsitoValidazione(False, f"Volume {volume} sotto il minimo {volume_min}")
    if volume > volume_max:
        return EsitoValidazione(False, f"Volume {volume} sopra il massimo {volume_max}")
    if not _multiplo_del_passo(volume, volume_step):
        return EsitoValidazione(False, f"Volume {volume} non e' multiplo di {volume_step}")

    return EsitoValidazione(
        True,
        MOTIVO_OK,
        prezzo=round(prezzo, digits),
        sl=round(sl, digits),
        tp=round(tp, digits),
        volume=volume,
    )
