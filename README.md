# UT Bot MetaTrader 5 Webhook Bridge & AI Agent

Automate your TradingView Pine Script alerts directly into MetaTrader 5 (MT5) with custom Stop Loss (SL) and Take Profit (TP) levels using FastAPI, ngrok, and the Antigravity developer agent.

---

## 🌟 How It Works
```
[TradingView Strategy Alert] 
          │ (Triggers & sends JSON payload)
          ▼
[ngrok Tunnel (Public URL)]
          │ (Forwards to local machine)
          ▼
[FastAPI Webhook Server]
          │ (Parses payload & gets current price)
          ▼
[MetaTrader 5 Client]
          │ (Executes market order with SL & TP)
          ▼
   [MT5 Terminal]
```

---

## 📋 Prerequisites
1. **Windows OS** (required for the MetaTrader 5 Python SDK).
2. **Python 3.10+** installed.
3. **MetaTrader 5 Desktop Terminal** installed and logged into your broker account.
4. **Algorithmic Trading Enabled**:
   * Open MT5.
   * Go to **Tools** ➔ **Options** ➔ **Expert Advisors**.
   * Check **"Allow algorithmic trading"** and click **OK**.

---

## 🚀 Quick Start & Installation

### Step 1: Install Dependencies
Install the required Python packages:
```bash
pip install fastapi uvicorn metatrader-mcp-server pydantic
```

### Step 2: Configure Antigravity MCP Server
To allow Antigravity to check your balances, open positions, and manage trades using natural language, update your global Antigravity configuration file:

**File Path:** `%USERPROFILE%\.gemini\config\mcp_config.json`

Add the `"metatrader"` server configuration:
```json
{
  "mcpServers": {
    "metatrader": {
      "command": "metatrader-mcp-server",
      "args": [
        "--login", "YOUR_MT5_LOGIN",
        "--password", "YOUR_MT5_PASSWORD",
        "--server", "YOUR_MT5_SERVER",
        "--transport", "stdio"
      ]
    }
  }
}
```
*(Make sure to adjust the path to your python local-packages directory if it differs).*

---

### Step 3: Set Up the Webhook Bridge
Create the `webhook_bridge.py` file to receive and process alerts from TradingView. The script automatically handles symbol cleaning (removing exchange prefixes like `EXNESS:`) and resolves library-specific SL/TP validation bugs.

#### Running the Server
Run the webhook bridge on port `5001`:
```bash
python webhook_bridge.py
```

### Step 4: Expose the Port Using ngrok
Expose port `5001` to the internet to get a public URL for TradingView:
```bash
ngrok http 5001
```
Copy the generated `Forwarding` URL (e.g. `https://your-subdomain.ngrok-free.dev`).

---

## 📊 TradingView Integration

### Step 1: Save the Pine Script
Create a new Pine Editor script in TradingView and paste the code from [ut_bot_strategy.pine](ut_bot_strategy.pine). Click **Save** and **Add to chart**.

### Step 2: Set Up the Alert
1. Press `Alt + A` to open the **Create Alert** dialog.
2. **Condition**: Select `UT Bot Strategy – Buy & Sell with SL/TP`.
3. **Trigger**: Select `Alert() function calls only` (crucial for sending dynamic JSON parameters).
4. **Webhook URL**: Under the **Notifications** tab, check Webhook URL and paste your public ngrok URL with `/webhook` at the end:
   `https://your-subdomain.ngrok-free.dev/webhook`
5. **Alert Name**: `UT Bot MT5 Automation`.
6. Clear the **Message** box.
7. Click **Create**.

---

## 🔒 Security Recommendations
* **Demo First**: Always test with a demo/trial account before using real money.
* **Firewalling**: Use an authentication mechanism or limit ngrok access if deploying to a production VPS.
