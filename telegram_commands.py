import json
import os
import time
import urllib.parse
import urllib.request

import MetaTrader5 as mt5

import alphaedge
from trading_bot_skills.trade_config import TELEGRAM_CHAT_ID, TELEGRAM_TOKEN

PAUSE_FILE = "alphaedge.paused"


def send_message(message: str) -> None:
    payload = urllib.parse.urlencode({"chat_id": TELEGRAM_CHAT_ID, "text": message}).encode()
    request = urllib.request.Request(
        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
        data=payload,
    )
    with urllib.request.urlopen(request, timeout=15):
        pass


def status_message() -> str:
    try:
        alphaedge.connect_mt5()
    except Exception as error:
        return f"MT5 disconnesso: {error}"
    account = mt5.account_info()
    positions = mt5.positions_get() or []
    mt5.shutdown()
    if account is None:
        return "MT5 connected but account details are unavailable."
    state = "PAUSED" if os.path.exists(PAUSE_FILE) else "LIVE"
    return f"AlphaEdge: {state}\nBalance: ${account.balance:.2f}\nEquity: ${account.equity:.2f}\nOpen positions: {len(positions)}"


def positions_message() -> str:
    try:
        alphaedge.connect_mt5()
    except Exception as error:
        return f"MT5 disconnesso: {error}"
    positions = mt5.positions_get() or []
    mt5.shutdown()
    if not positions:
        return "No open MT5 positions."
    return "\n".join(f"{position.symbol} | {'BUY' if position.type == 0 else 'SELL'} | {position.volume} | P/L ${position.profit:.2f}" for position in positions)


def handle_command(command: str) -> str:
    command = command.split()[0].lower()
    if command in {"/help", "/start"}:
        return "/status — account and bot status\n/positions — open positions\n/scan — run AlphaEdge now\n/pause — stop new scans\n/resume — restart scans\n/help — command list"
    if command == "/status":
        return status_message()
    if command == "/positions":
        return positions_message()
    if command == "/pause":
        open(PAUSE_FILE, "w", encoding="utf-8").close()
        return "AlphaEdge paused. No new trades will be opened."
    if command == "/resume":
        if os.path.exists(PAUSE_FILE):
            os.remove(PAUSE_FILE)
        return "AlphaEdge resumed."
    if command == "/scan":
        alphaedge.run_alphaedge(execute_orders=True)
        return "AlphaEdge scan completed. Check alerts for any executed trade."
    return "Unknown command. Send /help."


def main() -> None:
    offset = None
    while True:
        try:
            query = {"timeout": 30}
            if offset is not None:
                query["offset"] = offset
            with urllib.request.urlopen(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates?{urllib.parse.urlencode(query)}", timeout=40) as response:
                updates = json.load(response).get("result", [])
            for update in updates:
                offset = update["update_id"] + 1
                message = update.get("message", {})
                if str(message.get("chat", {}).get("id")) != str(TELEGRAM_CHAT_ID):
                    continue
                text = message.get("text", "")
                if text.startswith("/"):
                    send_message(handle_command(text))
        except Exception:
            time.sleep(5)


if __name__ == "__main__":
    main()
