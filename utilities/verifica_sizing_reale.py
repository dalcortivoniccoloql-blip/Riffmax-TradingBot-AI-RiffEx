"""Verifica del position sizing con gli SL REALI prodotti dalla strategia.

SOLA LETTURA. Non costruisce richieste d'ordine e non chiama mai esegui_o_valida().

Il punto di questo script e' non barare. Il rischio viene calcolato sulla
distanza fra l'ingresso e lo stop che la strategia AlphaEdge produce davvero
sui dati di mercato correnti, non su una distanza scelta a tavolino perche'
dava un risultato comodo.

Due casi possibili per ogni simbolo:

  SEGNALE ATTIVO   analyze_structural_edge() ha restituito BUY o SELL. Entry e
                   SL sono esattamente quelli che finirebbero nell'ordine.

  NESSUN SEGNALE   la strategia dice NEUTRAL. In questo caso NON si inventa un
                   esempio: si applicano le formule della strategia stessa
                   (SL = supporto - 1.0*ATR per il BUY, resistenza + 1.0*ATR
                   per il SELL; ingresso = chiusura -/+ 0.25*ATR) ai valori di
                   mercato di adesso, e il risultato viene etichettato come
                   ipotetico. Serve a sapere quanto rischierebbe quel simbolo
                   se il segnale arrivasse ora, non a fingere che sia arrivato.

Uso:  py utilities/verifica_sizing_reale.py
"""

import os
import sys
from datetime import datetime, timezone

import pandas as pd

RADICE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if RADICE not in sys.path:
    sys.path.insert(0, RADICE)
if os.path.join(RADICE, ".agents") not in sys.path:
    sys.path.insert(0, os.path.join(RADICE, ".agents"))

import MetaTrader5 as mt5                                    # noqa: E402

import alphaedge                                             # noqa: E402
from trading_bot_skills import risk_config as cfg            # noqa: E402
from trading_bot_skills.indicators import (                  # noqa: E402
    calculate_atr,
    calculate_bollinger_bands,
    calculate_rsi,
    find_support_resistance,
)
from trading_bot_skills.risk_engine import dimensiona        # noqa: E402

SIMBOLI = ["XAUUSD", "XAGUSD", "US100.cash", "EURUSD"]


def geometria_corrente(symbol: str):
    """Supporto, resistenza, ATR e chiusura correnti, con le stesse funzioni
    che usa la strategia. Nessuna approssimazione, nessun valore inventato."""
    rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M30, 0, 150)
    if rates is None or len(rates) < 50:
        return None
    df = pd.DataFrame(rates)
    df = calculate_bollinger_bands(df)
    df = calculate_rsi(df)
    df = calculate_atr(df)
    supporto, resistenza = find_support_resistance(df)
    return {
        "close": float(df["close"].iloc[-1]),
        "atr": float(df["atr"].iloc[-1]),
        "rsi": float(df["rsi"].iloc[-1]),
        "supporto": float(supporto),
        "resistenza": float(resistenza),
    }


def costo_spread_usd(info, specifiche, lotto: float) -> float:
    """Quanto costa lo spread, in dollari, sul lotto calcolato."""
    if lotto <= 0:
        return 0.0
    spread_in_prezzo = info.spread * info.point
    return (spread_in_prezzo / specifiche.tick_size) * specifiche.tick_value * lotto


def valuta(symbol: str, origine: str, direzione: str, entry: float, sl: float,
           equity: float, profilo, specifiche, info) -> dict:
    esito = dimensiona(equity, profilo, specifiche, entry, sl)
    distanza = abs(entry - sl)
    return {
        "simbolo": symbol,
        "origine": origine,
        "direzione": direzione,
        "entry": entry,
        "sl": sl,
        "distanza": distanza,
        "lotto": esito.lotto,
        "lotto_min": specifiche.volume_min,
        "rischio_usd": esito.rischio_effettivo_usd,
        "rischio_pct": (esito.rischio_effettivo_usd / equity * 100.0) if equity else 0.0,
        "ammesso_usd": esito.rischio_ammesso_usd,
        "spread_punti": info.spread,
        "spread_usd": costo_spread_usd(info, specifiche, esito.lotto),
        "consentito": esito.consentito,
        "motivo": esito.motivo,
    }


def main() -> None:
    if not mt5.initialize():
        print(f"MT5 non raggiungibile: {mt5.last_error()}")
        return

    conto = mt5.account_info()
    if conto is None:
        print("account_info() non disponibile.")
        mt5.shutdown()
        return

    equity = conto.equity
    profilo = alphaedge.PROFILO_RISCHIO

    print("=" * 100)
    print("VERIFICA POSITION SIZING CON SL REALI DELLA STRATEGIA")
    print("=" * 100)
    print(f"Momento della misura : {datetime.now(timezone.utc):%Y-%m-%d %H:%M:%S} UTC")
    print(f"Conto                : {conto.login} su {conto.server}")
    print(f"Equity               : {equity:.2f} {conto.currency}")
    print(f"Profilo di rischio   : {profilo.rischio_pct}% con tetto {profilo.cap_usd} USD")
    print(f"Rischio ammesso ora  : {min(equity * profilo.rischio_pct / 100.0, profilo.cap_usd):.2f} USD")
    print(f"Algo Trading attivo  : {mt5.terminal_info().trade_allowed}")
    print(f"DRY_RUN              : {cfg.DRY_RUN}")
    print()

    righe = []

    for symbol in SIMBOLI:
        info = mt5.symbol_info(symbol)
        specifiche = alphaedge.specifiche_simbolo(symbol)
        if info is None or specifiche is None:
            print(f"{symbol}: specifiche non disponibili, salto.")
            continue

        azione, sl, tp, entry, dettagli = alphaedge.analyze_structural_edge(symbol)
        print(f"--- {symbol} ---")
        print(f"    strategia: {azione} | {dettagli}")

        if azione in ("BUY", "SELL"):
            righe.append(valuta(symbol, "SEGNALE ATTIVO", azione, entry, sl,
                                equity, profilo, specifiche, info))
            continue

        geo = geometria_corrente(symbol)
        if geo is None:
            print("    dati insufficienti: nessun SL calcolabile, riga omessa.")
            continue

        print(f"    nessun segnale. ATR={geo['atr']:.5f} RSI={geo['rsi']:.1f} "
              f"supporto={geo['supporto']:.5f} resistenza={geo['resistenza']:.5f}")

        entry_buy = geo["close"] - 0.25 * geo["atr"]
        sl_buy = geo["supporto"] - 1.0 * geo["atr"]
        righe.append(valuta(symbol, "IPOTETICO (no segnale)", "BUY", entry_buy, sl_buy,
                            equity, profilo, specifiche, info))

        entry_sell = geo["close"] + 0.25 * geo["atr"]
        sl_sell = geo["resistenza"] + 1.0 * geo["atr"]
        righe.append(valuta(symbol, "IPOTETICO (no segnale)", "SELL", entry_sell, sl_sell,
                            equity, profilo, specifiche, info))

    print()
    print("=" * 100)
    print("RISULTATI")
    print("=" * 100)

    for r in righe:
        esito = "TRADE AMMESSO" if r["consentito"] else "NO TRADE"
        print()
        print(f"{r['simbolo']}  [{r['direzione']}]  {r['origine']}")
        print(f"    entry                  : {r['entry']:.5f}")
        print(f"    stop loss              : {r['sl']:.5f}")
        print(f"    distanza SL            : {r['distanza']:.5f}")
        print(f"    lotto calcolato        : {r['lotto']}")
        print(f"    lotto minimo broker    : {r['lotto_min']}")
        print(f"    rischio monetario      : {r['rischio_usd']:.2f} USD")
        print(f"    rischio percentuale    : {r['rischio_pct']:.4f}% dell'equity")
        print(f"    rischio ammesso        : {r['ammesso_usd']:.2f} USD")
        print(f"    spread ora             : {r['spread_punti']} punti = {r['spread_usd']:.2f} USD sul lotto calcolato")
        print(f"    ESITO                  : {esito} -- {r['motivo']}")

    print()
    print("=" * 100)
    print("NOTE CHE CONTANO PER LEGGERE QUESTA TABELLA")
    print("=" * 100)
    print("1. Lo spread NON va sommato al rischio, e la colonna qui sopra e' solo")
    print("   informativa. Il motivo: AlphaEdge entra con ordini LIMITE. Un BUY")
    print("   LIMIT a P viene eseguito quando l'ask tocca P, quindi si compra a P;")
    print("   lo stop di un long scatta sul bid a S, quindi si esce a S. La perdita")
    print("   e' P - S, cioe' esattamente la distanza usata per il sizing. Lo stesso")
    print("   vale al contrario per il SELL LIMIT. Lo spread e' gia' dentro.")
    print("2. Quello che NON e' compreso e' commissioni e swap: il sizing non li")
    print("   vede, quindi la perdita reale a stop e' superiore di quella quota.")
    print("3. Dove lo spread pesa davvero e' sulla PROBABILITA' di essere stoppati,")
    print("   non sulla dimensione della perdita: su uno stop da 5,6 pip con 2,1 pip")
    print("   di spread il margine di manovra e' minimo. Guardare il rapporto fra la")
    print("   colonna spread e la distanza SL, non la sua somma col rischio.")
    print("4. Lo spread notturno e' molto piu' largo di quello di sessione: se")
    print("   l'orario della misura qui sopra e' fuori sessione, il valore in punti")
    print("   NON e' rappresentativo.")
    print("5. Le righe IPOTETICO non sono segnali: sono le formule della strategia")
    print("   applicate al mercato di adesso, per sapere quanto rischierebbe quel")
    print("   simbolo se il segnale arrivasse.")

    mt5.shutdown()


if __name__ == "__main__":
    main()
