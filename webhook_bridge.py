# ---------------------------------------------------------------------------
# CANCELLO DI SICUREZZA P0 -- NON RIMUOVERE
# ---------------------------------------------------------------------------
# Questo file e' un entry point LEGACY. Invia ordini A MERCATO con volumi
# scritti a mano, e non passa da NIENTE di cio' che protegge alphaedge.py:
# niente Risk Engine, niente magic number, niente validazione SL/TP, niente
# deduplica, niente lock di processo, niente dry-run.
#
# Il controllo sta PRIMA di ogni altro import di proposito: cosi' il file si
# ferma anche se le sue dipendenze cambiano o vengono installate.
#
# Oggi importa metatrader_client, che NON e' installato e non e' in
# requirements.txt: da solo questo lo rende inerte. Ma e' un caso, non una
# garanzia. Il cancello e' la garanzia.
#
# Per eseguirlo davvero servono due variabili d'ambiente esplicite, insieme:
#   ALPHAEDGE_DRY_RUN=0   e   ALPHAEDGE_LEGACY_OK=1
import os as _os_guardia
import sys as _sys_guardia

if (_os_guardia.getenv("ALPHAEDGE_DRY_RUN", "1") != "0"
        or _os_guardia.getenv("ALPHAEDGE_LEGACY_OK") != "1"):
    _sys_guardia.exit(
        "BLOCCATO dal cancello di sicurezza P0: questo entry point legacy invia "
        "ordini a mercato senza Risk Engine e senza dry-run.\n"
        "Per usarlo consapevolmente servono insieme: "
        "ALPHAEDGE_DRY_RUN=0 e ALPHAEDGE_LEGACY_OK=1."
    )
# ---------------------------------------------------------------------------

import os
import re
import json
import logging
import asyncio
import MetaTrader5 as mt5
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel
from typing import Optional
from metatrader_client import MT5Client
from metatrader_client.order.send_order import send_order
from metatrader_client.types import TradeRequestActions, OrderType

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("TradingViewBridge")

app = FastAPI(title="TradingView MetaTrader 5 Webhook Bridge")

# Credenziali MT5: lette dalle variabili d'ambiente / .env (vuote finche' non configurate)
MT5_CONFIG = {
    "login": int(os.environ.get("MT5_LOGIN") or 0),
    "password": os.environ.get("MT5_PASSWORD", ""),
    "server": os.environ.get("MT5_SERVER", ""),
}

# Profit target to auto-close trades (in USD)
AUTO_CLOSE_PROFIT_TARGET = 10.0

client = MT5Client(MT5_CONFIG)

def get_lot_size(symbol: str) -> float:
    symbol_upper = symbol.upper()
    if "XAU" in symbol_upper or "GOLD" in symbol_upper:
        return 0.03  # Gold
    elif "BTC" in symbol_upper or "ETH" in symbol_upper:
        return 0.05  # Crypto
    else:
        return 0.1  # Currencies

def get_default_sltp_offsets(symbol: str, action: str, entry_price: float):
    symbol_upper = symbol.upper()
    action_upper = action.upper()
    
    # Query MT5 for point and digits info
    symbol_info = mt5.symbol_info(symbol)
    point = symbol_info.point if symbol_info else 0.00001
    
    if "XAU" in symbol_upper or "GOLD" in symbol_upper:
        offset_sl = 5.0
        offset_tp = 10.0
    elif "BTC" in symbol_upper:
        offset_sl = 300.0
        offset_tp = 600.0
    else:
        # Forex: 30 pips stop loss, 60 pips take profit (1 pip = 10 points)
        offset_sl = 30 * 10 * point
        offset_tp = 60 * 10 * point
        
    if action_upper == "BUY":
        sl = entry_price - offset_sl
        tp = entry_price + offset_tp
    else:
        sl = entry_price + offset_sl
        tp = entry_price - offset_tp
        
    return round(sl, 5), round(tp, 5)

async def monitor_and_close_profitable_positions():
    """
    Background loop that monitors active positions.
    1. Tracks standard trades and closes individual positions at +$3.00 profit (standard) or +$2.00 (best trend).
    2. Tracks "BASKET_*" trade groups and closes the entire basket when the net profit >= $10.00.
    """
    logger.info("Starting Profit Monitoring Background Task...")
    while True:
        await asyncio.sleep(2) # Check every 2 seconds for faster response
        try:
            if not client._connection.is_connected:
                continue
                
            raw_positions = mt5.positions_get()
            if raw_positions is None or len(raw_positions) == 0:
                continue
                
            # Dictionary to group basket trades: {basket_id: [list of position objects]}
            baskets = {}
            
            for pos in raw_positions:
                pos_id = pos.ticket
                profit = pos.profit
                symbol = pos.symbol
                volume = pos.volume
                comment = getattr(pos, 'comment', '')
                
                # 1. Evaluate individual profit-locking and group basket trades
                if comment.startswith("BASKET_BEST_"):
                    if profit >= 2.00:
                        logger.info(f"Profit-lock hit for Best-Trend Position {pos_id} ({symbol}): ${profit:.2f}. Auto-closing...")
                        client.order.close_position(pos_id)
                        continue
                    baskets.setdefault(comment, []).append(pos)
                elif comment.startswith("BASKET_"):
                    if profit >= 3.00:
                        logger.info(f"Profit-lock hit for Standard Basket Position {pos_id} ({symbol}): ${profit:.2f}. Auto-closing...")
                        client.order.close_position(pos_id)
                        continue
                    baskets.setdefault(comment, []).append(pos)
                else:
                    # 2. Standard individual trade tracking: close if profit >= $10.00
                    if profit >= AUTO_CLOSE_PROFIT_TARGET:
                        logger.info(f"Target profit hit for Position {pos_id} ({symbol} {volume} lot): ${profit:.2f}. Auto-closing...")
                        close_result = client.order.close_position(pos_id)
                        logger.info(f"Auto-close result for Position {pos_id}: {close_result}")
            
            # 3. Evaluate basket profits
            for basket_id, pos_list in baskets.items():
                total_basket_profit = sum(p.profit for p in pos_list)
                logger.debug(f"Basket {basket_id} has {len(pos_list)} positions. Net Profit: ${total_basket_profit:.2f}")
                
                if total_basket_profit >= AUTO_CLOSE_PROFIT_TARGET:
                    logger.info(f"🎉 Basket {basket_id} profit target hit: ${total_basket_profit:.2f}! Closing all {len(pos_list)} trades...")
                    
                    for p in pos_list:
                        pos_id = p.ticket
                        symbol = p.symbol
                        volume = p.volume
                        p_profit = p.profit
                        logger.info(f"Closing basket trade {pos_id} ({symbol} {volume} lot) current profit: ${p_profit:.2f}")
                        client.order.close_position(pos_id)
                        
        except Exception as e:
            logger.error(f"Error in profit monitoring loop: {e}")

@app.on_event("startup")
def startup_event():
    try:
        logger.info("Connecting to MetaTrader 5...")
        client.connect()
        logger.info("Successfully connected to MetaTrader 5 terminal!")
        
        # Start the background profit monitoring task
        asyncio.create_task(monitor_and_close_profitable_positions())
        
    except Exception as e:
        logger.error(f"Failed to connect to MT5 during startup: {e}")

@app.on_event("shutdown")
def shutdown_event():
    try:
        logger.info("Disconnecting from MetaTrader 5...")
        client.disconnect()
        logger.info("Disconnected.")
    except Exception as e:
        logger.error(f"Error during disconnect: {e}")

def clean_symbol(symbol_str: str) -> str:
    if ":" in symbol_str:
        symbol_str = symbol_str.split(":")[-1]
    return symbol_str.upper().strip()

def parse_plain_text_signal(text: str):
    logger.info(f"Attempting to parse plain text signal: {text}")
    
    action = None
    if "BUY" in text.upper():
        action = "BUY"
    elif "SELL" in text.upper():
        action = "SELL"
        
    if not action:
        return None
        
    symbol = None
    parts = [p.strip() for p in text.split("|")]
    if len(parts) >= 3:
        symbol = clean_symbol(parts[2])
    else:
        match = re.search(r'(?:BUY|SELL)\s*\|\s*([A-Z0-9_:]+)', text, re.IGNORECASE)
        if match:
            symbol = clean_symbol(match.group(1))
            
    if action and symbol:
        return {
            "action": action,
            "symbol": symbol,
            "volume": get_lot_size(symbol),
            "sl": None,
            "tp": None
        }
    return None

@app.post("/webhook")
async def receive_webhook(request: Request):
    body_bytes = await request.body()
    body_str = body_bytes.decode("utf-8").strip()
    logger.info(f"Raw webhook payload received: {body_str}")
    
    parsed_data = None
    
    try:
        data = json.loads(body_str)
        action_val = data.get("action")
        symbol_val = data.get("symbol")
        
        if action_val and symbol_val:
            symbol_cleaned = clean_symbol(str(symbol_val))
            parsed_data = {
                "action": str(action_val).upper().strip(),
                "symbol": symbol_cleaned,
                "volume": get_lot_size(symbol_cleaned),
                "sl": float(data.get("sl")) if data.get("sl") is not None else None,
                "tp": float(data.get("tp")) if data.get("tp") is not None else None
            }
    except Exception as e:
        logger.info(f"Payload is not JSON or failed JSON parsing: {e}")
        
    if not parsed_data:
        parsed_data = parse_plain_text_signal(body_str)
        
    if not parsed_data:
        logger.error("Could not parse action and symbol from webhook payload.")
        raise HTTPException(status_code=400, detail="Invalid signal format. Could not parse action and symbol.")
        
    action = parsed_data["action"]
    symbol = parsed_data["symbol"]
    volume = parsed_data["volume"]
    
    if action not in ["BUY", "SELL"]:
        raise HTTPException(status_code=400, detail="Action must be BUY or SELL")
        
    try:
        if not client._connection.is_connected:
            logger.info("MT5 disconnected. Attempting to reconnect...")
            client.connect()
            
        # Get current market price
        price_info = client.market.get_symbol_price(symbol)
        price = price_info["ask"] if action == "BUY" else price_info["bid"]
        
        # Safety net: If SL or TP are missing, calculate default levels
        sl = parsed_data["sl"]
        tp = parsed_data["tp"]
        if sl is None or tp is None:
            default_sl, default_tp = get_default_sltp_offsets(symbol, action, price)
            sl = default_sl if sl is None else sl
            tp = default_tp if tp is None else tp
            logger.info(f"SL/TP missing in signal. Applying defaults: SL={sl}, TP={tp}")
        
        logger.info(f"Executing {action} order for {volume} lots of {symbol} at current price {price} with SL={sl}, TP={tp}")
        
        result = send_order(
            client._connection,
            action=TradeRequestActions.DEAL,
            order_type=OrderType.BUY if action == "BUY" else OrderType.SELL,
            symbol=symbol,
            volume=volume,
            price=price,
            stop_loss=sl,
            take_profit=tp,
        )
        
        logger.info(f"Order result: {result}")
        if not result.get("success"):
            raise Exception(result.get("message"))
            
        return {"status": "success", "result": result, "parsed_signal": parsed_data}
        
    except Exception as e:
        logger.error(f"Trade execution failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/health")
def health_check():
    connected = False
    try:
        connected = client._connection.is_connected
    except Exception:
        pass
    return {"status": "ok", "mt5_connected": connected}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5001)
