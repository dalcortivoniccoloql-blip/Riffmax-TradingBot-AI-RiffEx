import os
import sys
import time
import concurrent.futures
import logging
from datetime import datetime
import pandas as pd
import numpy as np
import sys, os
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.append(os.path.join(PROJECT_ROOT, ".agents"))
from metatrader_client import MT5Client
from metatrader_client.order.send_order import send_order
from metatrader_client.types import TradeRequestActions, OrderType
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
    # Refined Top Performers Only (including ETHUSD and US500)
    "GBPUSD", "USDCAD", "EURGBP", "USTEC", "XAUUSD", "XAGUSD", "BTCUSD", "US30", "USOIL", "USDCHF", "ETHUSD", "US500",
    "EURUSD", "USDJPY", "AUDUSD", "GBPJPY", "DE30", "SOLUSD", "XRPUSD", "LTCUSD"
]
MAX_DAILY_LOSS_USD = 5.0
DAILY_PROFIT_TARGET_USD = 50.0
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

def get_lot_size(symbol: str, sl_price: float = 0.0, entry_price: float = 0.0) -> float:
    """Return the minimum allowable volume for the symbol to place a micro trade."""
    symbol_info = mt5.symbol_info(symbol)
    if not symbol_info:
        return 0.01
    return symbol_info.volume_min

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

def run_alphaedge(execute_orders: bool = False, approved_symbols: set[str] | None = None):
    client = MT5Client(MT5_CONFIG)
    try:
        client.connect()
        logger.info("AlphaEdge Strategy initialized.")
    except Exception as e:
        logger.error(f"MT5 connection failed: {e}")
        raise RuntimeError(f"MT5 connection failed: {e}")
        
    # 1. Check Daily Drawdown Limit (-$50) for active bots only
    now = datetime.now()
    today_start = datetime(now.year, now.month, now.day, 0, 0, 0)
    deals = mt5.history_deals_get(today_start, now)
    daily_profit = 0.0
    if deals:
        df_deals = pd.DataFrame(list(deals), columns=deals[0]._asdict().keys())
        our_pos_ids = df_deals[df_deals['comment'].isin(["ALPHAEDGE_TRADE", "SHERIFNEW_UT"]) & (df_deals['entry'] == 0)]['position_id'].unique()
        exits_today = df_deals[df_deals['entry'].isin([1, 3]) & df_deals['position_id'].isin(our_pos_ids)]
        if not exits_today.empty:
            daily_profit = exits_today['profit'].sum() + exits_today['commission'].sum() + exits_today['swap'].sum()
            
    if daily_profit <= -MAX_DAILY_LOSS_USD or daily_profit >= DAILY_PROFIT_TARGET_USD:
        logger.warning(f"Daily guardrail reached: ${daily_profit:+.2f}. No new orders today.")
        client.disconnect()
        return []
    #     logger.warning(f"Prop Firm Limit hit (Drawdown: -$200 / Target: +$400). Today's Net P&L: ${daily_profit:+.2f}. Disabling trades for today.")
    #     client.disconnect()
    #     return

    # 2. Manage Breakeven for Active Positions
    open_positions = mt5.positions_get()
    if execute_orders and open_positions:
        for pos in open_positions:
            if getattr(pos, 'comment', '') == "ALPHAEDGE_TRADE":
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
                            res = mt5.order_send(request)
                            if res and res.retcode == mt5.TRADE_RETCODE_DONE:
                                msg = f"🔒 <b>[AlphaEdge SL Adjusted]</b>\nSymbol: {symbol}\nPosition: {pos.ticket}\nAction: Moved SL to SECURE PROFIT ({target_sl}) (1.0x ATR profit reached)"
                                logger.info(f"Moved SL to SECURE PROFIT ({target_sl}) for {symbol} position {pos.ticket} (1.0x ATR profit reached)")
                                send_telegram_alert(msg)
                            else:
                                logger.error(f"Failed to adjust SL to breakeven for {symbol}: {res.retcode if res else 'N/A'} ({res.comment if res else ''})")

    # Check if weekend (Saturday=5, Sunday=6) to filter for Crypto only
    current_day = datetime.now().weekday()
    is_weekend = current_day in [5, 6]
    
    if is_weekend:
        # Scan only major crypto symbols on weekends
        active_symbols = [s.name for s in mt5.symbols_get() if "BTC" in s.name or "ETH" in s.name]
        print(f"\n[Weekend Mode] Forex and Gold markets are closed. Scanning Crypto only: {active_symbols}\n")
    else:
        # Scan all visible symbols that are in our custom SYMBOLS list on weekdays
        active_symbols = [s.name for s in mt5.symbols_get() if s.visible and s.name in SYMBOLS]
        print(f"\n[Weekday Mode] Scanning active filtered symbols: {active_symbols}\n")

    print("=== -> AlphaEdge Structural Tops/Bottoms Scan (M30 Timeframe) ===")
    print("| Symbol | Setup | Price | Stop Loss | Take Profit | R:R | Analysis Details |")
    print("| :--- | :--- | :--- | :--- | :--- | :--- | :--- |")
    
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
        client.disconnect()
        return []

    if not execute_orders:
        print("\n=== -> Approval Required: no orders submitted ===")
        for symbol, action, sl, tp, entry_price in triggers:
            print(f"PENDING | {symbol} | {action} | Entry {entry_price:.5f} | SL {sl:.5f} | TP {tp:.5f}")
        client.disconnect()
        return triggers

    print("\n=== -> Executing Structural Edge Orders ===")
    open_positions = mt5.positions_get()
    active_symbols = []
    if open_positions:
        active_symbols = [pos.symbol for pos in open_positions if getattr(pos, 'comment', '') == "ALPHAEDGE_TRADE"]
    pending_orders = mt5.orders_get() or []
    active_symbols.extend(order.symbol for order in pending_orders if getattr(order, 'comment', '') == "ALPHAEDGE_TRADE")

    for symbol, action, sl, tp, entry_price in triggers:
        if approved_symbols is not None and symbol not in approved_symbols:
            logger.info(f"Skipping {symbol}: not in the approved symbol list.")
            continue
        if symbol in active_symbols:
            logger.info(f"Skipping execution for {symbol}: A trade is already active on this symbol.")
            continue
            
        volume = get_lot_size(symbol, sl, entry_price)
        order_type = mt5.ORDER_TYPE_BUY_LIMIT if action == "BUY" else mt5.ORDER_TYPE_SELL_LIMIT
        
        try:
            request = {"action": mt5.TRADE_ACTION_PENDING, "symbol": symbol, "volume": volume, "type": order_type, "price": round(entry_price, 5), "sl": round(sl, 5), "tp": round(tp, 5), "type_time": mt5.ORDER_TIME_SPECIFIED, "expiration": int(time.time()) + PENDING_ORDER_EXPIRY_SECONDS, "comment": "ALPHAEDGE_TRADE"}
            result = mt5.order_send(request)
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
            
    client.disconnect()

if __name__ == "__main__":
    run_alphaedge()
