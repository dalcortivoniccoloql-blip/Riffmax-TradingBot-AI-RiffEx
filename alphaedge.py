import os
import sys
import time
import concurrent.futures
import logging
from datetime import datetime, timedelta, timezone
import pandas as pd
import numpy as np
import sys, os
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.append(os.path.join(PROJECT_ROOT, ".agents"))
import MetaTrader5 as mt5

# Set up logging to both console and file
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("alphaedge_trading.log", encoding="utf-8")
    ]
)
logger = logging.getLogger("AlphaEdge")

def load_environment_file() -> None:
    """Load local KEY=VALUE settings without adding another dependency."""
    environment_path = os.path.join(os.path.dirname(__file__), ".env")
    if not os.path.isfile(environment_path):
        return

    with open(environment_path, encoding="utf-8") as environment_file:
        for line in environment_file:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip())


load_environment_file()

MT5_CONFIG = {
    "login": int(os.environ.get("MT5_LOGIN") or 0),
    "password": os.environ.get("MT5_PASSWORD", ""),
    "server": os.environ.get("MT5_SERVER", ""),
}

SYMBOLS = [
    # Nomi come li espone il broker FTMO (gli indici e il petrolio hanno il suffisso .cash)
    "US100.cash", "XAUUSD",                                        # i due obiettivi principali
    "US30.cash", "US500.cash", "GER40.cash", "USOIL.cash", "XAGUSD",
    "EURUSD", "GBPUSD", "USDJPY", "USDCAD", "USDCHF", "AUDUSD", "EURGBP", "GBPJPY",
    "BTCUSD", "ETHUSD", "SOLUSD", "XRPUSD", "LTCUSD",
]
CRYPTO_KEYS = ("BTC", "ETH", "SOL", "XRP", "LTC")
# I limiti di rischio NON sono piu' definiti qui: unica fonte autorevole e'
# trading_bot_skills/risk_config.py (vedi P0-7).
PULLBACK_ATR_FRACTION = 0.25
PENDING_ORDER_EXPIRY_SECONDS = 7200

from trading_bot_skills.indicators import (
    calculate_bollinger_bands,
    calculate_rsi,
    calculate_ema,
    calculate_atr,
    find_support_resistance,
)
from trading_bot_skills.risk import assess_risk
from trading_bot_skills.trade_config import TELEGRAM_ENABLED, TELEGRAM_TOKEN, TELEGRAM_CHAT_ID
from trading_bot_skills.risk_config import (
    ALPHAEDGE_MAGIC,
    BACKOFF_CONNESSIONE_SECONDI,
    BASELINE_EQUITY_USD,
    DRY_RUN,
    ETICHETTA_ORDINE,
    MAX_DRAWDOWN_INTERNO_USD,
    MAX_PERDITA_GIORNALIERA_USD,
    MAX_POSIZIONI_APERTE,
    MAX_TENTATIVI_CONNESSIONE,
    RISCHIO_OVERRIDE_PER_SIMBOLO,
    RISCHIO_PER_TRADE_CAP_USD,
    RISCHIO_PER_TRADE_MIN_USD,
    RISCHIO_PER_TRADE_PCT,
    TARGET_PROFITTO_GIORNALIERO_USD,
)
from trading_bot_skills.risk_engine import (
    ProfiloRischio,
    SpecificheSimbolo,
    dimensiona,
    drawdown_interno_superato,
    perdita_giornaliera_superata,
    pnl_realizzato_da_deal,
)
from trading_bot_skills.order_validation import valida_ordine
from trading_bot_skills import dedup, process_lock

PROFILO_RISCHIO = ProfiloRischio(
    rischio_pct=RISCHIO_PER_TRADE_PCT,
    cap_usd=RISCHIO_PER_TRADE_CAP_USD,
    min_usd=RISCHIO_PER_TRADE_MIN_USD,
    override_per_simbolo=dict(RISCHIO_OVERRIDE_PER_SIMBOLO),
)
PERCORSO_LOCK = os.path.join(PROJECT_ROOT, "alphaedge.lock")
PERCORSO_DEDUP = os.path.join(PROJECT_ROOT, "alphaedge_dedup.json")
PERCORSO_DRY_RUN = os.path.join(PROJECT_ROOT, "dry_run_log.csv")

HISTORY_TIMEFRAMES = (mt5.TIMEFRAME_M5, mt5.TIMEFRAME_M30, mt5.TIMEFRAME_H1, mt5.TIMEFRAME_D1)
HISTORY_MAX_WAIT_SECONDS = 30


def history_is_aligned(symbol: str) -> bool:
    """Vero se l'ultima barra M30 copre l'ultimo tick ricevuto.

    MT5 scarica lo storico in modo asincrono: la prima lettura di un simbolo
    rimasto fermo per giorni restituisce la cache vecchia e solo dopo aggiorna.
    Confrontare barra e tick funziona anche a mercato chiuso, perche' in quel
    caso sono vecchi entrambi.
    """
    tick = mt5.symbol_info_tick(symbol)
    rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M30, 0, 1)
    if tick is None or not tick.time or rates is None or len(rates) == 0:
        return False
    ritardo = tick.time - int(rates[0]["time"])
    return -60 <= ritardo < 3600


def warm_up_history(symbols, max_wait: int = HISTORY_MAX_WAIT_SECONDS) -> list:
    """Forza il download dello storico e aspetta che sia allineato ai tick.

    Restituisce i simboli che restano disallineati oltre max_wait: vanno
    esclusi dall'analisi, perche' darebbero indicatori calcolati su prezzi
    vecchi di giorni (RSI e bande completamente sbagliati).
    """
    scadenza = time.time() + max_wait
    da_controllare = list(symbols)
    while True:
        for symbol in da_controllare:
            for timeframe in HISTORY_TIMEFRAMES:
                mt5.copy_rates_from_pos(symbol, timeframe, 0, 150)
        da_controllare = [s for s in da_controllare if not history_is_aligned(s)]
        if not da_controllare or time.time() >= scadenza:
            return da_controllare
        logger.info("Storico non ancora allineato per " + ", ".join(da_controllare) + ": attendo...")
        time.sleep(2)


def ensure_symbols_available(symbols) -> list[str]:
    """Porta i simboli nel Market Watch e scarta quelli che il broker non offre.

    Senza symbol_select() MT5 non restituisce le quotazioni di un simbolo che
    non e' gia' nella finestra Market Watch, e la scansione resterebbe vuota.
    """
    available = []
    for name in symbols:
        info = mt5.symbol_info(name)
        if info is None:
            logger.warning(f"Simbolo {name} non disponibile su questo broker: ignorato.")
            continue
        if not info.visible and not mt5.symbol_select(name, True):
            logger.warning(f"Impossibile aggiungere {name} al Market Watch: ignorato.")
            continue
        available.append(name)
    return available


def send_telegram_alert(message: str):
    if not TELEGRAM_ENABLED:
        return
    import urllib.request
    import urllib.parse
    import json
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML"
    }
    try:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=5) as response:
            response.read()
    except Exception as e:
        logger.error(f"Failed to send Telegram alert: {e}")

def specifiche_simbolo(symbol: str):
    """Traduce mt5.symbol_info() nella struttura usata dal Risk Engine."""
    info = mt5.symbol_info(symbol)
    if info is None:
        return None
    return SpecificheSimbolo(
        nome=symbol,
        tick_size=info.trade_tick_size,
        tick_value=info.trade_tick_value_loss or info.trade_tick_value,
        volume_min=info.volume_min,
        volume_max=info.volume_max,
        volume_step=info.volume_step,
        digits=info.digits,
    )


def get_lot_size(symbol: str, sl_price: float = 0.0, entry_price: float = 0.0) -> float:
    """Lotto calcolato dal Risk Engine. Ritorna 0.0 quando il trade non e' ammesso.

    Non ripiega MAI sul lotto minimo: era esattamente il bug che rendeva il
    rischio per trade fino a 40 volte piu' alto su alcuni simboli.
    """
    specifiche = specifiche_simbolo(symbol)
    if specifiche is None:
        return 0.0
    conto = mt5.account_info()
    equity = conto.equity if conto else 0.0
    return dimensiona(equity, PROFILO_RISCHIO, specifiche, entry_price, sl_price).lotto

# Simple test lot size that respects the instrument's minimum volume
def get_test_lot(symbol: str) -> float:
    """Return the minimum allowable volume for the symbol, suitable for test trades.
    """
    info = mt5.symbol_info(symbol)
    if not info:
        return 0.01
    return max(info.volume_min, 0.01)

# Simple trade logger to record trade details to a CSV file
def log_trade(symbol: str, action: str, price: float, sl: float, tp: float, quantity: float, comment: str = ""):
    """Append a trade record to 'trade_log.csv'."""
    import csv, os
    log_path = os.path.join(os.path.dirname(__file__), "trade_log.csv")
    file_exists = os.path.isfile(log_path)
    fields = ["timestamp", "symbol", "action", "price", "sl", "tp", "quantity", "comment"]
    with open(log_path, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        if not file_exists:
            writer.writeheader()
        writer.writerow({
            "timestamp": datetime.now().isoformat(),
            "symbol": symbol,
            "action": action,
            "price": price,
            "sl": sl,
            "tp": tp,
            "quantity": quantity,
            "comment": comment,
        })


def analyze_structural_edge(symbol: str):
    """
    AlphaEdge Core Strategy (M30 Timeframe):
    1. Checks if price is at a structural top or bottom.
       - Bottom: Close <= Lower BB OR Low <= Support Zone, and RSI <= 30 (Oversold)
       - Top: Close >= Upper BB OR High >= Resistance Zone, and RSI >= 70 (Overbought)
    2. Computes structural SL and TP:
       - BUY SL: Support - (1.0 * ATR)
       - BUY TP: Resistance - (0.5 * ATR) [Minimum 1:2 Risk/Reward ratio required]
       - SELL SL: Resistance + (1.0 * ATR)
       - SELL TP: Support + (0.5 * ATR)
    """
    try:
        rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M30, 0, 150)
        if rates is None or len(rates) < 50:
            return "NEUTRAL", 0.0, 0.0, 0.0, "Insufficient data"
            
        df = pd.DataFrame(rates)
        df = calculate_bollinger_bands(df)
        df = calculate_rsi(df)
        df = calculate_atr(df)
        
        last_close = df['close'].iloc[-1]
        last_high = df['high'].iloc[-1]
        last_low = df['low'].iloc[-1]
        last_rsi = df['rsi'].iloc[-1]
        last_atr = df['atr'].iloc[-1]
        
        bb_upper = df['bb_upper'].iloc[-1]
        bb_lower = df['bb_lower'].iloc[-1]
        
        support, resistance = find_support_resistance(df)
        
        # 3. M5 Sniper Reversal Confirmation Check (5/13 EMA Crossover)
        m5_rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M5, 0, 25)
        m5_confirmed_buy = False
        m5_confirmed_sell = False
        m5_status = "No Crossover"
        
        if m5_rates is not None and len(m5_rates) >= 15:
            df_m5 = pd.DataFrame(m5_rates)
            df_m5['ema5'] = calculate_ema(df_m5, 5)
            df_m5['ema13'] = calculate_ema(df_m5, 13)
            
            ema5 = df_m5['ema5'].values
            ema13 = df_m5['ema13'].values
            
            # Detect crossover in the last 3 candles (indices -1, -2, -3)
            for idx in [-1, -2, -3]:
                if ema5[idx] > ema13[idx] and ema5[idx-1] <= ema13[idx-1]:
                    m5_confirmed_buy = True
                    m5_status = "Bullish Cross (EMA 5 > 13)"
                    break
            
            for idx in [-1, -2, -3]:
                if ema5[idx] < ema13[idx] and ema5[idx-1] >= ema13[idx-1]:
                    m5_confirmed_sell = True
                    m5_status = "Bearish Cross (EMA 5 < 13)"
                    break
            
            if m5_status == "No Crossover":
                m5_status = f"No Cross (EMA5: {ema5[-1]:.5f}, EMA13: {ema13[-1]:.5f})"
        else:
            m5_confirmed_buy = True
            m5_confirmed_sell = True
            m5_status = "No M5 Data (Fallback Confirmed)"
            
        # 4. H1 Macro Trend Filter (Trend Alignment + RSI Extension)
        h1_rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_H1, 0, 50)
        h1_trend_aligned_buy = True
        h1_trend_aligned_sell = True
        h1_status = "N/A"
        
        if h1_rates is not None and len(h1_rates) >= 20:
            df_h1 = pd.DataFrame(h1_rates)
            df_h1['sma20'] = df_h1['close'].rolling(window=20).mean()
            df_h1 = calculate_rsi(df_h1)
            
            last_h1_close = df_h1['close'].iloc[-1]
            last_h1_sma20 = df_h1['sma20'].iloc[-1]
            last_h1_rsi = df_h1['rsi'].iloc[-1] if 'rsi' in df_h1 else 50.0
            
            # For BUY: H1 price should be above SMA20 (bullish macro) OR H1 RSI should be oversold <= 32
            if last_h1_close < last_h1_sma20 and last_h1_rsi > 32:
                h1_trend_aligned_buy = False
                h1_status = f"H1 Bearish (RSI: {last_h1_rsi:.1f})"
            else:
                h1_status = f"H1 Bullish/Oversold (RSI: {last_h1_rsi:.1f})"
                
            # For SELL: H1 price should be below SMA20 (bearish macro) OR H1 RSI should be overbought >= 68
            if last_h1_close > last_h1_sma20 and last_h1_rsi < 68:
                h1_trend_aligned_sell = False
                if not h1_trend_aligned_buy:
                    h1_status = f"H1 Bearish/Bullish Range (RSI: {last_h1_rsi:.1f})"
                else:
                    h1_status = f"H1 Bullish (RSI: {last_h1_rsi:.1f})"
            else:
                if h1_trend_aligned_sell:
                    h1_status = f"H1 Bearish/Overbought (RSI: {last_h1_rsi:.1f})"
        else:
            h1_status = "No H1 Data"

        d1_rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_D1, 0, 80)
        if d1_rates is None or len(d1_rates) < 50:
            return "NEUTRAL", 0.0, 0.0, 0.0, "Insufficient D1 data"
        df_d1 = pd.DataFrame(d1_rates)
        df_d1["ema20"] = calculate_ema(df_d1, 20)
        df_d1["ema50"] = calculate_ema(df_d1, 50)
        d1_close, d1_ema20, d1_ema50 = df_d1["close"].iloc[-1], df_d1["ema20"].iloc[-1], df_d1["ema50"].iloc[-1]
        d1_bullish = d1_close > d1_ema20 > d1_ema50
        d1_bearish = d1_close < d1_ema20 < d1_ema50
            
        is_bottom_zone = (last_close <= bb_lower or last_low <= (support + (0.5 * last_atr))) and last_rsi <= 32
        is_top_zone = (last_close >= bb_upper or last_high >= (resistance - (0.5 * last_atr))) and last_rsi >= 68
        
        is_bottom = is_bottom_zone and m5_confirmed_buy and d1_bullish
        is_top = is_top_zone and m5_confirmed_sell and d1_bearish
        
        action = "NEUTRAL"
        sl, tp = 0.0, 0.0
        details = f"RSI: {last_rsi:.1f} | BB Range: {bb_lower:.5f} - {bb_upper:.5f} | Price: {last_close:.5f} | H1: {h1_status}"
        
        if is_bottom_zone and not m5_confirmed_buy:
            details = f"Bottom Zone hit. Waiting for M5 EMA bullish crossover. RSI: {last_rsi:.1f} | M5: {m5_status} | H1: {h1_status}"
        elif is_top_zone and not m5_confirmed_sell:
            details = f"Top Zone hit. Waiting for M5 EMA bearish crossover. RSI: {last_rsi:.1f} | M5: {m5_status} | H1: {h1_status}"
        elif is_bottom_zone and not d1_bullish:
            details = f"Zona fondo + conferma M5, ma trend D1 non rialzista (serve close > EMA20 > EMA50). RSI: {last_rsi:.1f} | D1: close={d1_close:.5f} EMA20={d1_ema20:.5f} EMA50={d1_ema50:.5f}"
        elif is_top_zone and not d1_bearish:
            details = f"Zona cima + conferma M5, ma trend D1 non ribassista (serve close < EMA20 < EMA50). RSI: {last_rsi:.1f} | D1: close={d1_close:.5f} EMA20={d1_ema20:.5f} EMA50={d1_ema50:.5f}"
            
        if is_bottom:
            action = "BUY"
            last_close -= PULLBACK_ATR_FRACTION * last_atr
            sl = support - (1.0 * last_atr)
            tp = resistance - (0.2 * last_atr)
            # Adjust SL/TP based on risk appetite (neutral by default)
            sl, tp = assess_risk(action, sl, tp, last_close, last_atr, risk_level="neutral")
            risk = last_close - sl
            reward = tp - last_close
            if risk > 0 and (reward / risk) >= 1.5:
                details = f"Structural Bottom. Price: {last_close:.5f}. RSI: {last_rsi:.1f}. M5: {m5_status}. R:R = {reward/risk:.2f}"
            else:
                action = "NEUTRAL"
                details = f"Oversold but poor R:R ({reward/risk:.2f}). Support: {support:.5f}, Resistance: {resistance:.5f}"
                
        elif is_top:
            action = "SELL"
            last_close += PULLBACK_ATR_FRACTION * last_atr
            sl = resistance + (1.0 * last_atr)
            tp = support + (0.2 * last_atr)
            # Adjust SL/TP based on risk appetite (neutral by default)
            sl, tp = assess_risk(action, sl, tp, last_close, last_atr, risk_level="neutral")
            risk = sl - last_close
            reward = last_close - tp
            if risk > 0 and (reward / risk) >= 1.5:
                details = f"Structural Top. Price: {last_close:.5f}. RSI: {last_rsi:.1f}. M5: {m5_status}. R:R = {reward/risk:.2f}"
            else:
                action = "NEUTRAL"
                details = f"Overbought but poor R:R ({reward/risk:.2f}). Support: {support:.5f}, Resistance: {resistance:.5f}"
                
        return action, sl, tp, last_close, details
    except Exception as e:
        logger.error(f"Failed to analyze structural edge for {symbol}: {e}")
        return "NEUTRAL", 0.0, 0.0, 0.0, f"Error: {e}"

def connect_mt5() -> None:
    """Si collega a MetaTrader 5.

    Se MT5_LOGIN e' valorizzato nel .env fa il login esplicito; altrimenti si
    aggancia al terminale MT5 gia' aperto e gia' loggato (modo consigliato).
    """
    if MT5_CONFIG["login"]:
        ok = mt5.initialize(
            login=MT5_CONFIG["login"],
            password=MT5_CONFIG["password"],
            server=MT5_CONFIG["server"],
        )
    else:
        ok = mt5.initialize()
    if not ok:
        raise RuntimeError(
            f"Connessione a MT5 fallita: {mt5.last_error()}. "
            "Controlla che il terminale MetaTrader 5 sia aperto e loggato."
        )


def connessione_sana() -> bool:
    """Vero se il terminale e' connesso e il conto e' leggibile."""
    try:
        terminale = mt5.terminal_info()
        return bool(terminale and terminale.connected and mt5.account_info())
    except Exception:
        return False


def assicura_connessione() -> bool:
    """Riconnette con backoff esponenziale. Falso = il ciclo va saltato.

    Meglio saltare un ciclo che analizzare dati parziali: una disconnessione
    somiglia a un mercato tranquillo e produrrebbe NEUTRAL silenziosi.
    """
    if connessione_sana():
        return True
    attesa = BACKOFF_CONNESSIONE_SECONDI
    for tentativo in range(1, MAX_TENTATIVI_CONNESSIONE + 1):
        logger.warning(f"MT5 non raggiungibile: tentativo {tentativo}/{MAX_TENTATIVI_CONNESSIONE}")
        try:
            mt5.shutdown()
        except Exception:
            pass
        try:
            connect_mt5()
        except Exception as errore:
            logger.warning(f"Riconnessione fallita: {errore}")
        if connessione_sana():
            logger.info("Connessione a MT5 ripristinata.")
            return True
        time.sleep(attesa)
        attesa *= 2
    logger.error("MT5 irraggiungibile dopo tutti i tentativi: ciclo saltato.")
    return False


def ora_barra_corrente(symbol: str) -> int:
    """Ora di apertura della barra M30 in corso: e' la chiave di deduplica."""
    barre = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M30, 0, 1)
    if barre is None or len(barre) == 0:
        return 0
    return int(barre[0]["time"])


def ora_server_corrente() -> datetime:
    """Orologio del server del broker, senza contaminazioni dal fuso del PC.

    MT5 restituisce i timestamp gia' espressi nell'ora del server (GMT+3 su
    FTMO) ma confezionati come se fossero epoch UTC. datetime.fromtimestamp()
    ci aggiungerebbe sopra ANCHE l'offset locale della macchina: su questo PC
    (GMT+2) la giornata risultava spostata di due ore, e su un PC in un altro
    fuso sarebbe risultata spostata di un'altra quantita' ancora. Convertendo
    con tz=utc e togliendo poi il fuso si ottiene l'orologio del server, uguale
    ovunque giri il bot.

    ATTENZIONE, disallineamento noto e non risolto qui: la giornata del server
    (GMT+3) inizia un'ora prima della giornata FTMO, che si azzera alle 00:00
    CE(S)T. Il nostro limite giornaliero interno ($75) e' molto piu' stretto di
    quello FTMO ($500), quindi lo sfasamento non ci espone, ma resta una
    differenza da sanare prima di avvicinarsi ai limiti del broker.
    """
    tick = mt5.symbol_info_tick(SYMBOLS[0])
    if tick is None:
        return datetime.now(timezone.utc).replace(tzinfo=None)
    return datetime.fromtimestamp(tick.time, tz=timezone.utc).replace(tzinfo=None)


def registra_dry_run(request: dict, controllo) -> None:
    """Salva richiesta e risposta di order_check() per poterle rivedere."""
    import csv
    campi = ["timestamp", "symbol", "type", "volume", "price", "sl", "tp",
             "retcode", "comment_server", "margin", "equity_dopo"]
    esiste = os.path.isfile(PERCORSO_DRY_RUN)
    try:
        with open(PERCORSO_DRY_RUN, "a", newline="", encoding="utf-8") as fh:
            scrittore = csv.DictWriter(fh, fieldnames=campi)
            if not esiste:
                scrittore.writeheader()
            scrittore.writerow({
                "timestamp": datetime.now().isoformat(),
                "symbol": request.get("symbol"),
                "type": request.get("type"),
                "volume": request.get("volume"),
                "price": request.get("price"),
                "sl": request.get("sl"),
                "tp": request.get("tp"),
                "retcode": getattr(controllo, "retcode", None),
                "comment_server": getattr(controllo, "comment", None),
                "margin": getattr(controllo, "margin", None),
                "equity_dopo": getattr(controllo, "equity", None),
            })
    except Exception as errore:
        logger.error(f"Impossibile scrivere il log dry-run: {errore}")


def esegui_o_valida(request: dict):
    """In DRY_RUN valida con order_check(); altrimenti invia con order_send().

    order_check() interroga il server ma NON piazza nulla. Attenzione: non e'
    garantito che validi tutto cio' che valida order_send(), quindi un dry-run
    pulito non e' una prova che l'ordine reale passerebbe.
    """
    if DRY_RUN:
        # order_check() verifica margine e fondi di un ORDINE NUOVO. Su una
        # richiesta SLTP (sposta solo SL/TP di una posizione gia' aperta) non
        # ha nulla da validare: restituirebbe un retcode privo di significato
        # e scriverebbe una riga fasulla nel log dry-run. Qui basta l'intento.
        if request.get("action") == mt5.TRADE_ACTION_SLTP:
            logger.info(
                f"[DRY-RUN] {request.get('symbol')} posizione={request.get('position')} "
                f"-> SL NON spostato (target {request.get('sl')}). Nessun ordine inviato."
            )
            return None
        controllo = mt5.order_check(request)
        registra_dry_run(request, controllo)
        codice = getattr(controllo, "retcode", None)
        commento = getattr(controllo, "comment", "")
        logger.info(
            f"[DRY-RUN] {request.get('symbol')} volume={request.get('volume')} "
            f"-> retcode={codice} ({commento}). Nessun ordine inviato."
        )
        return None
    return mt5.order_send(request)


def print_scan_table(scan_results: dict) -> None:
    """Stampa una riga per ogni simbolo analizzato, con esito e motivo.

    Senza questa tabella la scansione mostra solo i segnali confermati, che
    sono quasi sempre zero: si vede "nessun segnale" senza capire il perche'.
    """
    def prezzo(symbol: str, value: float) -> str:
        if not value:
            return "-"
        info = mt5.symbol_info(symbol)
        return format(value, "." + str(info.digits if info else 5) + "f")

    def rapporto_rr(action: str, entry: float, sl: float, tp: float) -> str:
        if action == "BUY" and entry > sl:
            return format((tp - entry) / (entry - sl), ".2f")
        if action == "SELL" and sl > entry:
            return format((entry - tp) / (sl - entry), ".2f")
        return "-"

    intestazione = ("| {:<13} | {:<7} | {:>12} | {:>12} | {:>12} | {:>5} | {}")
    print()
    print("=== AlphaEdge - Scansione strutturale (M30) ===")
    print(intestazione.format("Simbolo", "Esito", "Prezzo", "Stop Loss", "Take Profit", "R:R", "Motivo"))
    print("|" + "-" * 15 + "|" + "-" * 9 + "|" + ("-" * 14 + "|") * 3 + "-" * 7 + "|" + "-" * 40)

    # prima i segnali confermati, poi tutto il resto in ordine alfabetico
    for symbol in sorted(scan_results, key=lambda s: (scan_results[s]["action"] == "NEUTRAL", s)):
        r = scan_results[symbol]
        action, sl, tp, entry = r["action"], r["sl"], r["tp"], r["entry_price"]
        etichetta = symbol if action == "NEUTRAL" else "-> " + symbol
        print(intestazione.format(
            etichetta, action,
            prezzo(symbol, entry), prezzo(symbol, sl), prezzo(symbol, tp),
            rapporto_rr(action, entry, sl, tp), r["details"],
        ))

    confermati = sum(1 for r in scan_results.values() if r["action"] != "NEUTRAL")
    print()
    print("  " + str(len(scan_results)) + " simboli analizzati, " + str(confermati) + " segnali confermati.")


def run_alphaedge(execute_orders: bool = False, approved_symbols: set[str] | None = None):
    try:
        connect_mt5()
    except Exception as errore:
        logger.error(f"Connessione iniziale fallita: {errore}")
    if not assicura_connessione():
        return []
    logger.info("AlphaEdge Strategy initialized.")
        
    # 1. Guardrail giornaliero: P&L REALIZZATO dei soli deal con il nostro magic.
    #    La giornata e' quella del server del broker (GMT+3 su FTMO), non quella
    #    locale: usare datetime.now() sfasava la finestra di 1-2 ore.
    adesso_server = ora_server_corrente()
    inizio_giornata = datetime(adesso_server.year, adesso_server.month, adesso_server.day)
    deals = mt5.history_deals_get(inizio_giornata, adesso_server + timedelta(days=1))
    daily_profit = pnl_realizzato_da_deal(deals, ALPHAEDGE_MAGIC)

    superato, motivo = perdita_giornaliera_superata(
        daily_profit, MAX_PERDITA_GIORNALIERA_USD, TARGET_PROFITTO_GIORNALIERO_USD
    )
    if superato:
        logger.warning(f"{motivo}. Nessun nuovo ordine oggi.")
        mt5.shutdown()
        return []

    # 1-bis. Guardrail sul capitale: si misura sull'EQUITY, quindi vede anche il
    #    flottante delle posizioni aperte e le operazioni fatte a mano, che il
    #    controllo giornaliero qui sopra non vede.
    conto_guardrail = mt5.account_info()
    equity_corrente = conto_guardrail.equity if conto_guardrail else 0.0
    dd_superato, motivo_dd = drawdown_interno_superato(
        equity_corrente, BASELINE_EQUITY_USD, MAX_DRAWDOWN_INTERNO_USD
    )
    if dd_superato:
        logger.warning(f"{motivo_dd}. Nessun nuovo ordine.")
        mt5.shutdown()
        return []

    # 2. Manage Breakeven for Active Positions
    open_positions = mt5.positions_get()
    if execute_orders and open_positions:
        for pos in open_positions:
            if getattr(pos, 'magic', 0) == ALPHAEDGE_MAGIC:
                symbol = pos.symbol
                rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M30, 0, 20)
                if rates is not None and len(rates) >= 14:
                    df_rates = pd.DataFrame(rates)
                    df_rates = calculate_atr(df_rates)
                    atr = df_rates['atr'].iloc[-1]
                    
                    is_in_profit = False
                    if pos.type == mt5.ORDER_TYPE_BUY and pos.price_current >= (pos.price_open + 1.0 * atr):
                        is_in_profit = True
                    elif pos.type == mt5.ORDER_TYPE_SELL and pos.price_current <= (pos.price_open - 1.0 * atr):
                        is_in_profit = True
                        
                    if is_in_profit:
                        lock_in_profit = 0.5 * atr
                        target_sl = round(pos.price_open + lock_in_profit if pos.type == mt5.ORDER_TYPE_BUY else pos.price_open - lock_in_profit, 5)
                        
                        needs_adjustment = False
                        if pos.type == mt5.ORDER_TYPE_BUY and (pos.sl < target_sl or pos.sl == 0):
                            needs_adjustment = True
                        elif pos.type == mt5.ORDER_TYPE_SELL and (pos.sl > target_sl or pos.sl == 0):
                            needs_adjustment = True
                            
                        if needs_adjustment:
                            request = {
                                "action": mt5.TRADE_ACTION_SLTP,
                                "position": pos.ticket,
                                "symbol": pos.symbol,
                                "sl": target_sl,
                                "tp": pos.tp
                            }
                            res = esegui_o_valida(request)
                            if res is None:
                                # In DRY_RUN il wrapper ha gia' loggato e non ha
                                # inviato nulla. Fuori da DRY_RUN un None e'
                                # invece un fallimento vero di order_send().
                                if not DRY_RUN:
                                    logger.error(f"Failed to adjust SL to breakeven for {symbol}: nessuna risposta da order_send()")
                            elif res.retcode == mt5.TRADE_RETCODE_DONE:
                                msg = f"🔒 <b>[AlphaEdge SL Adjusted]</b>\nSymbol: {symbol}\nPosition: {pos.ticket}\nAction: Moved SL to SECURE PROFIT ({target_sl}) (1.0x ATR profit reached)"
                                logger.info(f"Moved SL to SECURE PROFIT ({target_sl}) for {symbol} position {pos.ticket} (1.0x ATR profit reached)")
                                send_telegram_alert(msg)
                            else:
                                logger.error(f"Failed to adjust SL to breakeven for {symbol}: {res.retcode} ({res.comment})")

    # Check if weekend (Saturday=5, Sunday=6) to filter for Crypto only
    current_day = datetime.now().weekday()
    is_weekend = current_day in [5, 6]
    
    if is_weekend:
        # Scan only major crypto symbols on weekends
        active_symbols = ensure_symbols_available([s for s in SYMBOLS if any(k in s for k in CRYPTO_KEYS)])
        print(f"\n[Weekend Mode] Forex and Gold markets are closed. Scanning Crypto only: {active_symbols}\n")
    else:
        # Scan all visible symbols that are in our custom SYMBOLS list on weekdays
        active_symbols = ensure_symbols_available(SYMBOLS)
        print(f"\n[Weekday Mode] Scanning active filtered symbols: {active_symbols}\n")

    non_allineati = warm_up_history(active_symbols)
    if non_allineati:
        logger.warning("Storico non affidabile, simboli esclusi da questa scansione: " + ", ".join(non_allineati))
        active_symbols = [s for s in active_symbols if s not in non_allineati]

    print("Analisi di " + str(len(active_symbols)) + " simboli in corso...")
    
    open_symbols = [p.symbol for p in open_positions] if open_positions else []
    
    scan_results = {}
    
    def scan_symbol(symbol):
        symbol_info = mt5.symbol_info(symbol)
        if not symbol_info or not symbol_info.visible:
            return symbol, ("NEUTRAL", 0.0, 0.0, 0.0, "Symbol not available/visible")
        try:
            res = analyze_structural_edge(symbol)
            return symbol, res
        except Exception as e:
            return symbol, ("NEUTRAL", 0.0, 0.0, 0.0, f"Error: {e}")

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        futures = {executor.submit(scan_symbol, s): s for s in active_symbols}
        for future in concurrent.futures.as_completed(futures):
            symbol, (action, sl, tp, entry_price, details) = future.result()
            scan_results[symbol] = {
                "action": action,
                "sl": sl,
                "tp": tp,
                "entry_price": entry_price,
                "details": details
            }
        
    # Apply Metals Correlation Filter (XAUUSD and XAGUSD)
    xau = scan_results.get("XAUUSD")
    xag = scan_results.get("XAGUSD")
    if xau and xag:
        xau_action = xau["action"]
        xag_action = xag["action"]
        if (xau_action in ["BUY", "SELL"] or xag_action in ["BUY", "SELL"]) and (xau_action != xag_action):
            if xau_action in ["BUY", "SELL"]:
                xau["details"] = f"Blocked by Metals Correlation. Gold triggered {xau_action} but Silver is {xag_action}."
                xau["action"] = "NEUTRAL"
                xau["sl"] = 0.0
                xau["tp"] = 0.0
            if xag_action in ["BUY", "SELL"]:
                xag["details"] = f"Blocked by Metals Correlation. Silver triggered {xag_action} but Gold is {xau_action}."
                xag["action"] = "NEUTRAL"
                xag["sl"] = 0.0
                xag["tp"] = 0.0
                
    print_scan_table(scan_results)

    # --- Autonomous trading: place orders only when the structural edge strategy signals BUY or SELL ---
    triggers = []
    for symbol, result in scan_results.items():
        action = result["action"]
        if action not in ["BUY", "SELL"]:
            continue
        sl = result["sl"]
        tp = result["tp"]
        entry_price = result["entry_price"]
        logger.info(f"Strategy signals {action} for {symbol} at {entry_price:.5f} (SL={sl:.5f}, TP={tp:.5f})")
        triggers.append((symbol, action, sl, tp, entry_price))

    if not triggers:
        print("\n-> **No structural tops/bottoms confirmed for entry.** (Waiting for price to hit extreme S/R zones).")
        mt5.shutdown()
        return []

    if not execute_orders:
        print("\n=== -> Approval Required: no orders submitted ===")
        for symbol, action, sl, tp, entry_price in triggers:
            print(f"PENDING | {symbol} | {action} | Entry {entry_price:.5f} | SL {sl:.5f} | TP {tp:.5f}")
        mt5.shutdown()
        return triggers

    print("\n=== -> Executing Structural Edge Orders ===")
    open_positions = mt5.positions_get()
    active_symbols = []
    if open_positions:
        active_symbols = [pos.symbol for pos in open_positions if getattr(pos, 'magic', 0) == ALPHAEDGE_MAGIC]
    pending_orders = mt5.orders_get() or []
    active_symbols.extend(order.symbol for order in pending_orders if getattr(order, 'magic', 0) == ALPHAEDGE_MAGIC)

    for symbol, action, sl, tp, entry_price in triggers:
        if approved_symbols is not None and symbol not in approved_symbols:
            logger.info(f"Skipping {symbol}: not in the approved symbol list.")
            continue
        if symbol in active_symbols:
            logger.info(f"Skipping execution for {symbol}: A trade is already active on this symbol.")
            continue
            
        if len(active_symbols) >= MAX_POSIZIONI_APERTE:
            logger.warning(f"Raggiunto il massimo di {MAX_POSIZIONI_APERTE} posizioni: nessun nuovo ordine.")
            break

        # --- P0-1: il lotto viene dal Risk Engine, mai dal lotto minimo ---
        specifiche = specifiche_simbolo(symbol)
        if specifiche is None:
            logger.error(f"{symbol}: specifiche non disponibili, nessun ordine.")
            continue
        conto = mt5.account_info()
        esito_sizing = dimensiona(
            conto.equity if conto else 0.0, PROFILO_RISCHIO, specifiche, entry_price, sl
        )
        if not esito_sizing.consentito:
            logger.warning(
                f"{symbol}: {esito_sizing.motivo} "
                f"(al lotto minimo si rischierebbero {esito_sizing.rischio_effettivo_usd:.2f} USD, "
                f"ammessi {esito_sizing.rischio_ammesso_usd:.2f})"
            )
            continue
        volume = esito_sizing.lotto

        # --- P0-6: SL e TP obbligatori e coerenti, sempre ---
        info = mt5.symbol_info(symbol)
        validazione = valida_ordine(
            action, entry_price, sl, tp, volume,
            digits=info.digits,
            volume_min=info.volume_min,
            volume_max=info.volume_max,
            volume_step=info.volume_step,
            distanza_minima_stop=info.trade_stops_level * info.point,
        )
        if not validazione.valido:
            logger.error(f"{symbol}: ordine rifiutato dalla validazione: {validazione.motivo}")
            continue

        # --- P0-3: stesso segnale sulla stessa barra, una volta sola ---
        chiave = dedup.costruisci_chiave(symbol, action, ora_barra_corrente(symbol))
        if not dedup.registra(PERCORSO_DEDUP, chiave):
            logger.info(f"{symbol}: segnale gia' trattato su questa barra, salto.")
            continue

        order_type = mt5.ORDER_TYPE_BUY_LIMIT if action == "BUY" else mt5.ORDER_TYPE_SELL_LIMIT

        try:
            request = {
                "action": mt5.TRADE_ACTION_PENDING,
                "symbol": symbol,
                "volume": validazione.volume,
                "type": order_type,
                "price": validazione.prezzo,
                "sl": validazione.sl,
                "tp": validazione.tp,
                "type_time": mt5.ORDER_TIME_SPECIFIED,
                "expiration": int(time.time()) + PENDING_ORDER_EXPIRY_SECONDS,
                "magic": ALPHAEDGE_MAGIC,
                "comment": ETICHETTA_ORDINE,
            }
            result = esegui_o_valida(request)
            if result is None:
                # dry-run: nessun ordine inviato, il segnale resta disponibile
                dedup.dimentica(PERCORSO_DEDUP, chiave)
                continue
            if result and result.retcode == mt5.TRADE_RETCODE_DONE:
                ticket = result.order
                logger.info(f"Successfully entered {action} on {symbol} (Ticket: {ticket}, SL: {sl:.5f}, TP: {tp:.5f})")
                msg = f"🚀 <b>[AlphaEdge Position Opened]</b>\nSymbol: {symbol}\nAction: {action}\nLot Size: {volume}\nEntry Price: {entry_price:.5f}\nSL: {sl:.5f}\nTP: {tp:.5f}"
                send_telegram_alert(msg)
                # Log the trade entry to Excel
                try:
                    log_trade(symbol, action, entry_price, sl, tp, volume, "ALPHAEDGE_TRADE")
                except Exception as log_err:
                    logger.error(f"Failed to log trade for {symbol}: {log_err}")
            else:
                logger.error(f"Failed to place {action} limit order on {symbol}: {getattr(result, 'comment', mt5.last_error())}")
        except Exception as e:
            logger.error(f"Order send error on {symbol}: {e}")
            
    mt5.shutdown()

if __name__ == "__main__":
    run_alphaedge()
