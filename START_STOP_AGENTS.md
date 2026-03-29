# 🎮 Start/Stop Guide for AI Agents

To ensure your brains are "always working" on the entire wishlist, you need to run the **Autonomous Scout**.

## 1. How to Start the System
You should have **four** terminals open for a full setup:

### Terminal 1: Setup Modern Environment (First Time Only)
> [!IMPORTANT]
> **ALWAYS run these commands from the root folder** (`D:\AI Agent Finance`), NEVER from inside `venv` or `Scripts`.

You should use **Python 3.11** for the best experience:
```powershell
# 1. Create a new environment using Python 3.11
py -3.11 -m venv venv311

# 2. Activate it
# If using Command Prompt (CMD):
.\venv311\Scripts\activate.bat

# If using PowerShell (and you get a Security/Execution error):
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope Process
.\venv311\Scripts\Activate.ps1

# 3. Install requirements
pip install -r requirements.txt
```

### Terminal 2: The Dashboard (Visible UI)
```powershell
# Always make sure to activate the environment first
.\venv311\Scripts\Activate.ps1
 
```

### Terminal 2: The Autonomous Scout (The "Worker")
**This is what keeps the brains trading on the whole wishlist while you aren't looking.**
```powershell
python -m market_agent.runner.autonomous_scout
```

### Terminal 3: Data Bridge (Live Prices)
```powershell
python market_agent/agent/test_live_sync.py
```

### Terminal 4: Cloudflare Tunnel (Mobile Access)
```powershell
cloudflared tunnel --url http://localhost:8501
```

---

## 2. How to Stop Everything
To stop the agents manually:
1. Click into each terminal window.
2. Press `Ctrl + C` once or twice.
3. The terminal will say `🛑 Scout stopped` or return to the prompt.

## 3. Why were reports saying "0 trades"?
The reports look for **resolved signals** in the database. 
- **Before**: Signals were only generated when you were looking at a specific stock in the Dashboard.
- **Now**: Running the **Autonomous Scout** ensures that every 10 minutes, the brains visit every stock in your wishlist (ITC, BTC, NVDA, etc.) and record their findings.

## 4. Wishlist Coverage
The brains currently monitor these assets in rotation:
- **NSE**: ITC, Bank Nifty, Reliance, Tata Motors, etc.
- **Crypto**: BTC-USD, ETH-USD.
- **Tech**: NVDA, AAPL, AMD, GOOGL.
- **Global**: Gold (XAU-USD), Forex (USDJPY).

> Keep Terminal 2 (Autonomous Scout) running in the background even if you close the Dashboard. It will keep the brains learning and trading.

## 5. Command Reference

### Core Agents
| Command | Description |
| :--- | :--- |
| `python -m market_agent.runner.autonomous_scout` | **The Worker.** Runs in the background. Scans the wishlist every 10m, generates signals, and saves them to the DB. |
| `streamlit run market_agent/dashboard/app.py` | **The UI.** Launch the web dashboard to see charts, signals, and agent insights. |
| `python market_agent/agent/test_live_sync.py` | **Data Bridge.** Connects to live market data (if configured) and ensures prices are fresh. |
| `cloudflared tunnel --url http://localhost:8501` | **Mobile/remote access.** Exposes the dashboard via a temporary Cloudflare URL. Run after the dashboard is up on port 8501. |

### Health Checks & Utilities
| Command | Description |
| :--- | :--- |
| `python check_db_items.py` | **DB Inspection.** Shows a summary of how many rows are in each database table. |
| `python check_brains.py` | **Brain Activity.** Shows the last few "thoughts" or logs from the AI Analyst. |
| `python check_db_signals.py` | **Signal Audit.** Checks raw signal entries in the database. |
| `python check_bearish_vitals.py` | **Bearish Scan.** specifically looks for "SELL" signals or negative sentiment. |
| `python verify_system_vitals.py` | **Daily Vitals.** A holistic check of how many signals, news items, and thoughts were generated *today*. |

## 6. Database Credentials
If you need to connect manually (using DBeaver, pgAdmin, or Excel):

- **Host**: `localhost`
- **Port**: `5433` (Note: It is 5433, not the default 5432)
- **Database**: `market_data`
- **Username**: `agent_user`
- **Password**: `agent_password`

These are defined in `market_agent/docker/docker-compose.yml`.
