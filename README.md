# FINAL60 - Polymarket BTC 5-minute bot

A local Windows application for Polymarket BTC 5-minute UP/DOWN markets.

The strategy is fixed and intentionally simple:

- **First 4 minutes: no trade.** No confirmation signals.
- **Final 60 seconds: monitor UP and DOWN.** If `UP best ask >= 0.80`, submit a **BUY at the current market price**. Same for DOWN.
- `0.80` is a **trigger threshold, never a limit price**.
- Execution is WebSocket/event-driven. There are no polling sleeps or artificial delays in the trigger path.

Paper trading is the default. Live trading is implemented but disabled by default and guarded by explicit safety gates.

---

## Requirements

- Windows 10 or 11
- Python 3.12+ (the code is 3.11-compatible; this host was verified on 3.11.9)
- Node.js 20.19+ or 22.12+ (verified on 24.20.0)
- Internet access to `gamma-api.polymarket.com`, `clob.polymarket.com`, and the CLOB WebSocket
- A correctly synchronized system clock (Windows Settings > Time & Language > Sync now)

---

## Quick start

Three scripts control the app: `setup.bat` (one-time install), `start.bat` (start backend + dashboard), `stop.bat` (stop both). In PowerShell run them as `.\setup.bat`, `.\start.bat`, `.\stop.bat`.

`start.bat` runs setup automatically if `.venv`, `frontend\node_modules`, or `.env` is missing.

New to the project? Follow the [Step-by-step guide (PowerShell)](#step-by-step-guide-powershell) below: clone, first setup, `.env`, start, and how to run it the second time.

URLs:

- Dashboard: `http://127.0.0.1:5173`
- API: `http://127.0.0.1:8000`
- API docs: `http://127.0.0.1:8000/docs`

The launcher refuses to start a second copy when ports 8000/5173 are already served by this app, and fails clearly when an unrelated process holds a port. PIDs are tracked in `logs\pids`.

---

## Step-by-step guide (PowerShell)

Open **PowerShell** (Start menu > type `PowerShell`). All commands below are run there. In PowerShell a `.bat` file must be started with `.\` in front, for example `.\setup.bat`.

### 0. Prerequisites (one time)

Check that the tools are installed and on PATH:

```powershell
git --version
python --version     # 3.11+ (3.12+ recommended)
node --version       # 20.19+ or 22.12+
```

If one of them is missing, install it, then **close and reopen PowerShell** so PATH is refreshed.

### 1. Clone the repository

```powershell
cd $HOME\Documents
git clone https://github.com/mainnetwallet/polybot-final60.git
cd polybot-final60
```

- If the repository is private, Git opens a browser/login window the first time (Git Credential Manager). Sign in with your GitHub account, or paste a Personal Access Token when it asks for a password.
- Do **not** put your token inside the clone URL. It would be saved in `.git\config` in plain text.

### 2. First-time setup

```powershell
.\setup.bat
```

Setup does everything in one go and is safe to run again:

1. checks Python and Node.js versions
2. creates the `.venv` virtual environment
3. installs backend packages (`backend\requirements.txt`) and dashboard packages (`npm install`)
4. creates `.env` from `.env.example` (only if `.env` does not exist yet)
5. creates the `data\` and `logs\` folders and initializes the SQLite database

Wait for `Setup complete.` before going to the next step.

### 3. Set up `.env`

`.env` is created automatically by setup. Open it in Notepad:

```powershell
notepad .env
```

**Paper mode (default) needs no edits.** You can run the bot as it is and it will only simulate trades.

Common things to change:

| Goal | Edit in `.env` |
| --- | --- |
| Bigger/smaller paper trades | `TRADE_AMOUNT_USDC=5.0` |
| Paper bankroll | `PAPER_STARTING_BALANCE=10.0` |
| Daily loss stop / profit target | `MAX_DAILY_LOSS`, `DAILY_PROFIT_TARGET` |
| Trigger level | `TRIGGER_THRESHOLD=0.80` |

To enable **live trading** (real money, optional, read the Risk warning first), all of these must be set:

```text
LIVE_TRADING_ENABLED=true
POLYMARKET_GEO_VERIFIED=true          # only after you confirmed your jurisdiction is eligible
POLYMARKET_PRIVATE_KEY=<your key>
POLYMARKET_FUNDER_ADDRESS=<your funder address>
POLYMARKET_SIGNATURE_TYPE=0           # 0 = EOA, 1 = email/magic proxy, 2 = browser proxy
POLYMARKET_API_KEY=<optional, if you have one>
POLYMARKET_API_SECRET=<optional>
POLYMARKET_API_PASSPHRASE=<optional>
```

Rules for `.env`:

- Save the file, then **restart** the bot (`.\stop.bat`, then `.\start.bat`). Changes are only read at startup.
- `.env` is listed in `.gitignore`, so it is never committed. Never share it or paste your private key anywhere.
- Write values without quotes and without spaces around `=`.
- Even with the keys set, going live still requires the dashboard confirmation phrase `GO LIVE`.

### 4. Start the bot

```powershell
.\start.bat
```

This starts the API (port 8000) and the dashboard (port 5173) and opens the dashboard in your browser: `http://127.0.0.1:5173`.

The bot always starts in the **STOPPED** state. Open the dashboard and start it from there when you are ready. The API and dashboard run as hidden background processes, so you can close the PowerShell window and the dashboard tab; they keep running until you run `.\stop.bat`. Logs are written to the `logs\` folder.

To start without opening the browser:

```powershell
.\start.bat -NoBrowser
```

### 5. Running it a second time (and every time after)

Setup is **not** needed again. Next time you just do:

```powershell
cd $HOME\Documents\polybot-final60
.\start.bat
```

What to know about repeat runs:

- Your orders, positions, analytics, and settings overrides are kept in `data\polymarket_bot.db`, so they are still there after a restart.
- The bot comes back **STOPPED**. Start it again from the dashboard.
- If the app is already running, `start.bat` detects it and does not start a second copy.
- `start.bat` runs setup by itself if `.venv`, `frontend\node_modules`, or `.env` is missing.
- Stop everything when you are done:

```powershell
.\stop.bat
```

### 6. Updating to the latest version

```powershell
cd $HOME\Documents\polybot-final60
.\stop.bat
git pull
.\setup.bat      # re-installs dependencies if they changed; keeps your .env and database
.\start.bat
```

### 7. Using the dashboard (quick guide)

1. Open `http://127.0.0.1:5173`.
2. **Overview**: check that the market is found, the feed is healthy, and the UP/DOWN asks are updating.
3. **Settings**: review the mode (it should say PAPER), trigger, trade amount, and risk limits.
4. Start the bot. During the first 4 minutes of each 5-minute market nothing happens by design. In the final 60 seconds it buys a side when its best ask reaches the trigger.
5. **Trading** shows orders, fills, and positions; **Strategy** shows why each trigger was taken or blocked; **Analytics** shows PnL and win rate; **Logs** shows live server logs.
6. Stop the bot from the dashboard before changing mode or resetting paper data.

### 8. Clean reinstall (if something is badly broken)

```powershell
.\stop.bat
Remove-Item -Recurse -Force .venv, frontend\node_modules
.\setup.bat
.\start.bat
```

This keeps `.env` and `data\polymarket_bot.db`. To clear paper history only, use **Settings > Paper reset** in the dashboard (bot must be stopped); live records are not touched.

---

## How it works

```text
Gamma API (market discovery, 3 s refresh)
        |
        v
active 5-minute BTC market + UP/DOWN token ids
        |
        v
CLOB WebSocket (book, price_change, best_bid_ask)
        |
        v
strategy engine: in final window? ask >= trigger?
        |
        v
execution guards: fresh data, price cap, risk, duplicate/conflict policy, expiration
        |
        v
paper engine (simulated fills)  or  live engine (real CLOB FOK order)
        |
        v
SQLite (orders, fills, positions, triggers, settings) + portfolio analytics
        |
        v
WebSocket push on every book event (tick) + state snapshots -> React dashboard
```

Key properties:

- Market validation is against the active event slug for the current window (`btc-updown-5m-<window_start_unix>`).
- Orders are never submitted after market expiration.
- A marketable BUY uses the **current ask**, capped by `MAX_EXECUTION_PRICE`; it is not a resting order.
- Duplicate protection is enforced with per-market locks, an idempotency key, and `DUPLICATE_POLICY`.
- Conflicting simultaneous side triggers are resolved by `CONFLICT_POLICY`.
- Orders, fills, positions, trigger decisions, and settings overrides are persisted in `data\polymarket_bot.db`.
- Restart recovery keeps the bot **STOPPED** and reloads persisted trades/positions/settings.

### Backend modules

| Module | Responsibility |
| --- | --- |
| `market_discovery` | Gamma series/event lookup, active market, resolution |
| `market_data` | CLOB WebSocket client, book state, freshness |
| `strategy` | Final-60s window and ask-threshold evaluation |
| `risk_manager` | Daily loss/target, trade count, open positions, kill switch |
| `order_manager` | Order decisions, idempotency, duplicate/conflict policy |
| `paper_trading` | Simulated immediate-market BUY, walking the ask ladder |
| `live_trading` | Real CLOB V2 (`py-clob-client-v2`) marketable FOK BUY; fill price taken from the exchange response |
| `portfolio` | Balances, positions, settlement, analytics |
| `monitoring` | In-memory log stream and persisted file logs |
| `services.bot` | Orchestration, state snapshot, WebSocket broadcast |
| `api` | FastAPI routes and `/api/ws` |

### Dashboard pages

- **Overview** - market phase, countdown, UP/DOWN asks, account envelope, feed health, recent orders
- **Strategy** - final-window rules, live evaluation meters, trigger audit trail
- **Trading** - mode banner, performance stats, open orders, order history, positions
- **Analytics** - mode-separated PnL, win rate, volume, fees
- **Settings** - runtime-tunable values, live readiness, paper reset
- **Logs** - live server log stream with level/category/search filters

---

## Paper vs live

### Paper (default)

- Uses live Polymarket data from the CLOB WebSocket.
- Simulates the current-market BUY against the ask ladder with configurable slippage, fill ratio (partial fills), and an approximate fee.
- Never sends real orders.
- Paper balances and statistics are stored separately from live.

### Live

Live trading uses **Polymarket CLOB V2** (live since 2026-04-28) through `py-clob-client-v2`; the legacy `py-clob-client` no longer works on production. Collateral is **pUSD**: API-only wallets must wrap USDC.e into pUSD via the Collateral Onramp before the balance check passes. Fees are charged by the protocol at match time, so the live `fee` column is not populated from the exchange yet.

Live mode is off by default and is never switched on automatically. Enabling it requires all of the following:

1. `LIVE_TRADING_ENABLED=true` in `.env`
2. `POLYMARKET_GEO_VERIFIED=true` in `.env` (set this only after confirming your jurisdiction/polymarket eligibility)
3. Private key and funder address present in `.env`
4. The bot stopped with no outstanding orders
5. Switching mode in the dashboard and typing the confirmation phrase (`GO LIVE`)
6. Passing the live validation/start flow

The backend enforces these gates; the frontend mirrors them but is not the security boundary.

Private keys and API secrets are read from environment variables only. They are never returned by the API and never sent to the browser.

Kill switch behavior: it blocks new orders and stops the bot. It cannot reverse fills that already happened.

---

## Configuration

Edit `.env` (created from `.env.example` by setup). Changes to `.env` require a restart.

Strategy and risk defaults:

| Key | Default | Meaning |
| --- | --- | --- |
| `TRIGGER_THRESHOLD` | `0.80` | Ask level that triggers a BUY |
| `FINAL_WINDOW_SECONDS` | `60` | Trading window at the end of the market |
| `MAX_EXECUTION_PRICE` | `0.99` | Hard cap on the price paid |
| `TRADE_AMOUNT_USDC` | `5.0` | Requested notional per trade |
| `STALE_DATA_MAX_AGE_MS` | `2000` | Maximum book age for execution |
| `MAX_OPEN_POSITIONS` | `1` | Concurrent position cap |
| `DUPLICATE_POLICY` | `one_per_market` | `one_per_market`, `one_per_side`, `allow` |
| `CONFLICT_POLICY` | `up_first` | `up_first`, `down_first`, `highest_ask`, `block_both` |
| `MAX_DAILY_LOSS` | `5.0` | Daily loss stop (USDC) |
| `DAILY_PROFIT_TARGET` | `10.0` | Daily profit target (USDC) |
| `MAX_TRADES_PER_DAY` | `60` | Trade count cap |
| `PAPER_STARTING_BALANCE` | `10.0` | Paper bankroll |
| `PAPER_FILL_RATIO` | `1.0` | Simulated fill fraction (partial fills) |
| `PAPER_SLIPPAGE_BPS` | `0` | Simulated slippage |
| `PAPER_FEE_RATE` | `0.07` | Approximate fee rate |
| `PAPER_FEE_EXPONENT` | `1.0` | Fee curve exponent |
| `LIVE_TRADING_ENABLED` | `false` | Master live switch |
| `POLYMARKET_GEO_VERIFIED` | `false` | Set true only after verifying eligibility |

The dashboard Settings page can change a validated subset without editing `.env`; overrides persist in SQLite:

`trigger_threshold`, `final_window_seconds`, `max_execution_price`, `trade_amount_usdc`,
`max_daily_loss`, `daily_profit_target`, `stale_data_max_age_ms`, `duplicate_policy`,
`conflict_policy`, `paper_slippage_bps`, `paper_fee_rate`, `max_trades_per_day`, `max_open_positions`.

Secrets, `paper_fill_ratio`, `paper_fee_exponent`, `paper_starting_balance`, `live_trading_enabled`, and `geo_verified` are environment-only.

---

## Data and logs

| Path | Contents |
| --- | --- |
| `data\polymarket_bot.db` | Permanent SQLite database (WAL mode) |
| `logs\backend.log` | Rotating application log (5 MB x 5) |
| `logs\backend.out.log` / `.err.log` | Uvicorn stdout/stderr |
| `logs\frontend.out.log` / `.err.log` | Vite stdout/stderr |
| `logs\pids\*.pid` | PIDs of launcher-started services |

Paper reset is available in Settings only while the bot is stopped. Reset deletes paper orders/positions and restores the configured starting balance; live records are untouched.

---

## Testing

Backend suite (strategy, risk, order flow, API, portfolio):

```bat
cd backend
..\.venv\Scripts\python.exe -m pytest -q
```

Frontend type-check and production build:

```bat
cd frontend
npm run build
```

Results on this machine (2026-11-12):

- `pytest`: **50 passed** (3.2 s)
- `npm run build`: PASS (TypeScript strict, Vite 53 modules)
- End-to-end paper evidence: UP ask reached `0.80` with `59.394 s` left in the final window; order filled `6.25` shares at `0.80`, fee `0.07`, measured latency `4 ms`; a second trigger was blocked by `blocked_duplicate`; the market resolved UP for `+1.18 USDC` realized; a stop/start cycle reloaded identical order, position, and analytics records.

These are paper-execution results. See limitations below.

---

## Limitations (honest list)

- **Live execution was not exercised with real funds** and the CLOB V2 order path was only unit-tested against mocked SDK responses (no network access to Polymarket from the build environment). The live path, gates, and order construction are implemented and unit-tested, but no real order has been submitted from this machine.
- **Polymarket geo eligibility is unverified.** `POLYMARKET_GEO_VERIFIED` stays `false` by default; do not set it without checking your jurisdiction.
- **Paper fees are approximate.** The fee formula is an approximation of the verified schedule and should be reviewed before relying on absolute paper PnL.
- **Gamma resolution delay.** Gamma may keep `closed:false` (with near-1/0 prices) for a while after expiry. Resolution requires `closed:true` and published 1/0 prices, so a paper position can remain open across a restart until Gamma publishes the result.
- **On-chain reconciliation is best-effort.** Live fills depend on the CLOB API responses; there is no separate on-chain balance reconciler.
- **Single market family.** Discovery targets the `btc-up-or-down-5m` series only.
- **Clock skew matters.** Windows clock drift can make books look stale and block trades. Fix time sync rather than relaxing `STALE_DATA_MAX_AGE_MS`.
- **Python version.** The project targets 3.12+; the verified host runs 3.11.9.

---

## Troubleshooting

**`setup.bat` / `start.bat` is not recognized (PowerShell)** - PowerShell does not run files from the current folder without a prefix. Use `.\setup.bat`, `.\start.bat`, `.\stop.bat`.

**`python` / `node` / `git` is not recognized** - install it, then close and reopen PowerShell so PATH updates. Re-run `.\setup.bat`.

**My `.env` change had no effect** - `.env` is only read at startup. Run `.\stop.bat`, then `.\start.bat`.

**Dashboard opens but the bot is STOPPED after a restart** - this is intended. Restart recovery always keeps the bot stopped; start it from the dashboard.

**"Port 8000/5173 is already in use"** - close the other process, or run `stop.bat` if it is a previous copy of this app.

**Startup failed** - read `logs\backend.err.log`, `logs\frontend.err.log`, then `logs\backend.log`. `start.bat` stops only the services it started this run.

**Bot does not trade** - confirm the bot is RUNNING, the market is in its final 60 seconds, the ask actually reaches the trigger, book data is fresh, the daily risk limits are intact, and no duplicate/conflict rule blocked the trigger (see the Strategy page audit trail).

**Paper reset does nothing** - stop the bot first; in-memory state would otherwise overwrite the reset.

**Live options are disabled** - this is the intended default. See the live gates above.

---

## Risk warning

This software can place real orders if you enable live trading. Prediction markets are risky and you can lose your entire balance. Use paper mode first, verify your jurisdiction and API credentials, and start with small sizes. This project is not financial advice.
