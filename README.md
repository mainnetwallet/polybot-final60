# Polybot - Polymarket BTC market bot

A local Windows application for Polymarket BTC up/down markets.

The entry rules are intentionally simple. The window and trigger values below are defaults and can be changed in Settings:

- Before the configured final window, no order is created.
- During the configured final window (default last 80s), monitor UP and DOWN. If a best ask reaches `TRIGGER_THRESHOLD` (default `0.80`), submit a **BUY at the current market price**. If the ask is not there when the window opens, the bot keeps watching and buys the moment it gets there, any time before the market ends (stage 1).
- Two optional add-on entries per market (stages 2 and 3): once `STAGE2_SECONDS` (default `50`) or `STAGE3_SECONDS` (default `30`) or less remain and a best ask is at least `STAGE2_THRESHOLD` / `STAGE3_THRESHOLD` (default `0.90`), the bot places one more buy for that stage. Each stage buys at most once per market, so a market can have up to 3 buys.
- **Stage 0 (early buy + take-profit sell, on by default):** from `STAGE0_START_SECONDS` (default `200`) left until the final window starts, the bot buys a side whose best ask is between `STAGE0_MIN_ASK` (default `0.60`) and `STAGE0_MAX_ASK` (default `0.95`) **and** whose BTC price gap beyond the price to beat is at least `STAGE0_MIN_PRICE_GAP_USD` (default `$50`, in the bought direction). It buys at most once per market and never pays above `STAGE0_MAX_ASK`. The shares it bought are then **sold** (marketable, never below the sell price) as soon as the best bid reaches the sell price for what was paid per share: bought at `0.60`-`0.70` → sell at `STAGE0_SELL_PRICE_LOW` (`0.80`); bought at `0.70`-`0.80` → `STAGE0_SELL_PRICE_MID` (`0.90`); bought at `0.80` or more → `STAGE0_SELL_PRICE` (`0.98`). This can happen any time before the market ends. If the bid never gets there the position is held and settles like any other. Stage 0 is independent of stages 1-3 and its holding does not count against `MAX_OPEN_POSITIONS` for them.
- **Stage 1 take-profit (on by default):** stage 1 shares are sold (marketable, never below the sell price) the moment the best bid reaches the sell price for what they cost per share: bought at up to `STAGE1_SELL_MAX_BUY` (`0.90`, so `0.80`-`0.90`) → `STAGE1_SELL_PRICE` (`0.95`); bought above `0.90` → `STAGE1_SELL_PRICE_HIGH` (`0.99`). Only stage 1's own shares are sold. Set `STAGE1_SELL_ENABLED=false` to turn it off.
- **Stage 2 take-profit (on by default):** stage 2 shares are sold the moment the best bid reaches `STAGE2_SELL_PRICE` (`0.99`), whatever they cost. Stage 3 shares are held to settlement. Set `STAGE2_SELL_ENABLED=false` to turn it off.
- **Stop-loss (on by default):** every stage watches its own shares. When the best bid is at or below the average price paid per share minus `STAGEn_STOP_LOSS_PCT` percent, those shares are **sold at the market** (no price floor, so the exit is not refused when the bid keeps falling). Defaults: stage 0 = `20` %, stages 1, 2 and 3 = `15` %. Example: stage 0 bought at `0.70` with `20` → sold once the best bid is `0.56` or lower. Each stage sells only its own shares, a stopped-out stage is not bought again in that market, and `0` turns the stop-loss off for that stage. Stage 3 has no take-profit, so without a stop-loss it is held to settlement. The stop compares the best **bid** with the buy price, so a wide bid/ask spread right after a buy can trigger it early.
- If an entry is blocked (e.g. stale book) or fails (e.g. FOK not filled), the same stage is tried again on the next book event, and every 0.5s while the book is quiet, for as long as its condition still holds.
- `0.80` / `0.90` are **trigger thresholds, never limit prices**.
- Live orders use FOK and the configured maximum execution price as the worst acceptable price.
- Execution is WebSocket/event-driven. There are no polling sleeps or artificial delays in the trigger path (a 0.5s retry heartbeat only re-tries blocked/failed entries when the book is quiet).

Paper trading is the default. Live trading is implemented but disabled by default and guarded by explicit safety gates.

---

## Requirements

- Windows 10 or 11
- Python 3.11 or newer (3.11 and 3.12 both supported; full test suite passes on 3.11.18)
- Node.js 20.19+ or 22.12+ (verified on 24.20.0)
- Internet access to `gamma-api.polymarket.com`, `clob.polymarket.com`, and the CLOB WebSocket
- A correctly synchronized system clock (Windows Settings > Time & Language > Sync now)

---

## Restarting

`start.bat` stays open after startup: press **Ctrl+C** (or **Q**) in that window to stop everything, or run `stop.bat` from another window. Use `start.bat -Detach` to start the servers in the background and get the prompt back immediately.

Running `start.bat` again (even from a new terminal) always does a **fresh restart**: it first stops the previous
instance (bot stopped gracefully through the API, then only processes that belong to this project folder are killed),
then starts the API and dashboard again with the current code. After a restart the bot is `STOPPED`; open positions
are re-tracked and settled automatically. Use `start.bat -NoRestart` to keep an already-running instance instead.

## Settlement & balance after a market ends

When a market ends, the position shows **settling…** until Gamma marks the event closed and publishes
the winning outcome price. The bot keeps checking tracked markets until the winner is known, including
after a restart. The winner is read from the CLOB (token id or outcome label), then Gamma, and in live mode
also from the wallet's `redeemable` tokens, so a position no longer stays on settling… while Polymarket
already lets you redeem. If a market is still unsettled 3 minutes after it ended, a WARNING in the Logs page
(`settlement`) shows exactly what CLOB and Gamma returned. Then:

- **Paper:** the position closes, realized PnL is booked and the balance is credited (`+1 USDC` per winning share).
- **Live:** per the official docs (`trading/positions/manage`) winning tokens become pUSD only after
  **redemption**. Either claim on polymarket.com (Portfolio), or set `LIVE_AUTO_REDEEM=true`,
  `pip install -r backend/requirements-redeem.txt` (official `polymarket-client` SDK) and optionally a
  Relayer API key for gasless redeem (otherwise an EOA transaction that needs a little POL). The live balance
  is refreshed right after a successful redeem.

## Quick start

Three scripts control the app: `setup.bat` (one-time install), `start.bat` (start backend + dashboard), `stop.bat` (stop both). In PowerShell run them as `.\setup.bat`, `.\start.bat`, `.\stop.bat`.

`start.bat` runs setup automatically if `.venv`, `frontend\node_modules`, or `.env` is missing.

Copy-paste commands (PowerShell):

First-time install (once, after `git clone`):

```powershell
cd .\polybot-final60          # go into the project folder
.\setup.bat                   # one-time install
```

Update and start (every time):

```powershell
cd .\polybot-final60          # go into the project folder (after git clone)
git pull
.\start.bat
```

Stop:

```powershell
.\stop.bat                    # stop everything
```

Create `.env` manually from the example (only needed if setup did not create it, or you deleted it):

```powershell
Copy-Item .env.example .env            # PowerShell
copy .env.example .env                 # cmd.exe
```

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
python --version     # 3.11 or newer
node --version       # 20.19+ or 22.12+
```

If one of them is missing, install it, then **close and reopen PowerShell** so PATH is refreshed.

### 1. Clone the repository

Open PowerShell in any folder where you want the project (the folder does not matter), then run:

```powershell
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

`.env` is created automatically by setup. If it is missing, create it from the example (does nothing if it already exists):

```powershell
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
```

Open it in Notepad:

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
POLYMARKET_SIGNATURE_TYPE=0           # 0 = EOA, 1 = email/magic proxy, 2 = browser proxy, 3 = smart-contract wallet
POLYMARKET_API_KEY=<optional, if you have one>
POLYMARKET_API_SECRET=<optional>
POLYMARKET_API_PASSPHRASE=<optional>
```

**Logged in to Polymarket with MetaMask (browser wallet)?** Use:

```text
POLYMARKET_PRIVATE_KEY=<private key of the MetaMask account you log in with>
POLYMARKET_FUNDER_ADDRESS=<the proxy wallet address shown on your Polymarket profile / deposit page, NOT your MetaMask address>
POLYMARKET_SIGNATURE_TYPE=2
```

Not sure which `POLYMARKET_SIGNATURE_TYPE` your account needs (0 = EOA, 1 = email/Magic, 2 = MetaMask proxy, 3 = smart-contract wallet)? Fill in the key and funder, then run the read-only checker, it shows the pUSD balance the CLOB sees for each type:

```text
.venv\Scripts\python.exe scripts\check_wallet.py
```

Your pUSD must sit in that proxy (funder) wallet. With `SIGNATURE_TYPE=1`, `2` or `3` the funder address is mandatory; the live check now fails with a clear message if it is empty.

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
cd .\polybot-final60          # the folder where you cloned it
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
cd .\polybot-final60          # the folder where you cloned it
.\stop.bat
git pull
.\setup.bat      # re-installs dependencies if they changed; keeps your .env and database
.\start.bat
```

### 7. Using the dashboard (quick guide)

1. Open `http://127.0.0.1:5173`.
2. **Overview**: check that the market is found, the feed is healthy, and the UP/DOWN asks are updating.
3. **Settings**: review the mode (it should say PAPER), trigger, trade amount, and risk limits.
4. Start the bot. It waits until the configured final window, then buys a side when its best ask reaches the configured trigger.
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
active BTC up/down market + its UP/DOWN asset ids
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
| `strategy` | Configured final window and ask-threshold evaluation |
| `risk_manager` | Daily loss/target, trade count, open positions, kill switch |
| `order_manager` | Order decisions, idempotency, duplicate/conflict policy |
| `paper_trading` | Simulated immediate-market BUY, walking the ask ladder |
| `live_trading` | Real CLOB V1/V2 marketable FOK BUY; delayed fills are reconciled from order/trade data |
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

Live trading uses Polymarket CLOB V1 or Protocol V2 markets through `py-clob-client-v2`. Protocol V2 markets use position IDs; V1 markets use CLOB token IDs. Collateral is **pUSD**: API-only wallets must wrap USDC.e into pUSD via the Collateral Onramp before the balance check passes. Live fees are estimated from each market's CLOB fee parameters and follow the documented fee formula and precision.

Live mode is off by default and is never switched on automatically. Enabling it requires all of the following:

1. `LIVE_TRADING_ENABLED=true` in `.env`
2. `POLYMARKET_GEO_VERIFIED=true` in `.env` (set this only after confirming your jurisdiction/polymarket eligibility)
3. Private key present in `.env`, with funded pUSD and a positive CLOB collateral allowance
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
| `FINAL_WINDOW_SECONDS` | `80` | Trading window at the end of the market |
| `STAGE0_ENABLED` | `true` | Switches stage 0 (early buy + take-profit sell) on or off |
| `STAGE0_START_SECONDS` | `200` | Stage 0 may buy from this many seconds before expiry until the final window starts |
| `STAGE0_MIN_ASK` | `0.60` | Stage 0 buys only while the best ask is at or above this |
| `STAGE0_MAX_ASK` | `0.95` | Stage 0 buys only while the best ask is at or below this; also its max price |
| `STAGE0_MIN_PRICE_GAP_USD` | `50` | Stage 0 price-to-beat filter (BTC beyond the price to beat in the bought direction); `0` disables it; missing/stale BTC data blocks the buy |
| `STAGE0_SELL_PRICE` | `0.98` | Stage 0 sell price for shares bought at `STAGE0_HIGH_FROM_ASK` or more (must be above `STAGE0_MAX_ASK`) |
| `STAGE0_MID_FROM_ASK` / `STAGE0_HIGH_FROM_ASK` | `0.70` / `0.80` | Buy-price tier borders: below the first = low tier, below the second = mid tier, else high tier |
| `STAGE0_SELL_PRICE_LOW` / `STAGE0_SELL_PRICE_MID` | `0.80` / `0.90` | Stage 0 sell price for the low tier (bought below `0.70`) and the mid tier (bought `0.70`-`0.80`) |
| `STAGE1_SELL_ENABLED` | `true` | Switches the stage 1 take-profit sell on or off |
| `STAGE1_SELL_MAX_BUY` | `0.90` | Stage 1 shares that cost up to this per share use `STAGE1_SELL_PRICE`, dearer ones use `STAGE1_SELL_PRICE_HIGH` |
| `STAGE1_SELL_PRICE` / `STAGE1_SELL_PRICE_HIGH` | `0.95` / `0.99` | Best bid at which stage 1 sells (cost up to `STAGE1_SELL_MAX_BUY` / above it); `STAGE1_SELL_PRICE` must be above `STAGE1_SELL_MAX_BUY` |
| `STAGE2_SELL_ENABLED` | `true` | Switches the stage 2 take-profit sell on or off |
| `STAGE2_SELL_PRICE` | `0.99` | Best bid at which stage 2 sells its shares (must be above `STAGE2_THRESHOLD`) |
| `STAGE0_STOP_LOSS_PCT` | `20` | Stage 0 stop-loss: sell its shares at the market once the best bid is this many percent below the average buy price (`0` = off) |
| `STAGE1_STOP_LOSS_PCT` / `STAGE2_STOP_LOSS_PCT` / `STAGE3_STOP_LOSS_PCT` | `15` / `15` / `15` | Same stop-loss for stage 1, 2 and 3 shares (`0` = off for that stage) |
| `STAGE2_SECONDS` / `STAGE2_THRESHOLD` | `50` / `0.90` | Stage 2 add-on: time left and ask level |
| `STAGE3_SECONDS` / `STAGE3_THRESHOLD` | `30` / `0.90` | Stage 3 add-on: time left and ask level |
| `MAX_EXECUTION_PRICE` | `0.99` | Hard cap on the price paid (clamped to `1 - tick size` at order time) |
| `MIN_PRICE_GAP_USD` | `20` | Price-to-beat filter: an entry needs the current BTC price (Chainlink TWAP60) at least this many USD beyond the price to beat in the bought direction (UP: above, DOWN: below). `0` disables it. This is the stage 1 gap; missing/stale BTC data blocks the buy |
| `STAGE2_MIN_PRICE_GAP_USD` / `STAGE3_MIN_PRICE_GAP_USD` | `25` / `30` | Same price-to-beat filter for stage 2 and stage 3 entries (`0` disables it for that stage) |
| `TRADE_AMOUNT_USDC` | `1.0` | Requested notional per trade |
| `STALE_DATA_MAX_AGE_MS` | `5000` | Maximum book age for execution |
| `ENTRY_MAX_ATTEMPTS` / `ENTRY_RETRY_GAP_MS` | `5` / `1000` | Per stage and market: max tries for a failed/cancelled entry, and minimum gap between tries |
| `MAX_OPEN_POSITIONS` | `1` | Concurrent position cap per market (stage 2/3 may add to a side already held) |
| `DUPLICATE_POLICY` | `one_per_market` | `one_per_market`, `one_per_side`, `allow` |
| `CONFLICT_POLICY` | `up_first` | `up_first`, `down_first`, `highest_ask`, `block_both` |
| `MAX_DAILY_LOSS` | `5.0` | Daily loss stop (USDC) |
| `DAILY_PROFIT_TARGET` | `10.0` | Daily profit target (USDC) |
| `MAX_TRADES_PER_DAY` | `600` | Trade count cap |
| `PAPER_STARTING_BALANCE` | `10.0` | Paper bankroll |
| `PAPER_FILL_RATIO` | `1.0` | Simulated fill fraction (partial fills) |
| `PAPER_SLIPPAGE_BPS` | `0` | Simulated slippage |
| `PAPER_FEE_RATE` | `0.07` | Approximate fee rate |
| `PAPER_FEE_EXPONENT` | `1.0` | Fee curve exponent |
| `LIVE_TRADING_ENABLED` | `false` | Master live switch |
| `POLYMARKET_GEO_VERIFIED` | `false` | Set true only after verifying eligibility |

Stage chain (always on, not configurable): stage 1 can only buy while more than `STAGE3_SECONDS` remain (after that, stages 1 and 2 are off for the market); stage 2 needs stage 1 to have bought *before* `STAGE2_SECONDS` remained; stage 3 is independent and buys whenever its own rules (ask, price gap, ...) are met. Up to 3 buys per 5m market.

Other `.env` keys (all present in `.env.example`):

| Key | Default | Meaning |
| --- | --- | --- |
| `HOST` | `127.0.0.1` | API bind address |
| `PORT` | `8000` | API port |
| `CORS_ORIGINS` | `http://localhost:5173,http://127.0.0.1:5173` | Allowed dashboard origins |
| `API_TOKEN` | _(empty)_ | Optional shared secret for the control API (`X-API-Key` / `Authorization: Bearer`, `?token=` on the WebSocket; `/api/health` stays open). Set it before binding `HOST` to a non-loopback address. In the dashboard: `localStorage.setItem("polybot_api_token", "<token>")` |
| `LOG_LEVEL` | `INFO` | Log verbosity |
| `DATABASE_URL` | `sqlite:///data/polymarket_bot.db` | Database location (relative paths resolve against the repo root) |
| `LIVE_CONFIRMATION_PHRASE` | `GO LIVE` | Phrase required in the dashboard to enable live mode |
| `LIVE_AUTO_REDEEM` | `false` | Bot redeems resolved winning positions itself (needs `backend/requirements-redeem.txt`) |
| `POLYMARKET_RELAYER_API_KEY` | empty | Optional Relayer key for gasless redeem |
| `POLYMARKET_RELAYER_API_KEY_ADDRESS` | empty | Address that belongs to the Relayer key |
| `POLYMARKET_PRIVATE_KEY` | empty | Wallet private key (live only, never exposed by the API) |
| `POLYMARKET_FUNDER_ADDRESS` | empty | Funder address (live only) |
| `POLYMARKET_SIGNATURE_TYPE` | `0` | `0` = EOA, `1` = email/magic proxy, `2` = browser proxy, `3` = smart-contract wallet |
| `POLYMARKET_CHAIN_ID` | `137` | Polygon mainnet |
| `POLYMARKET_API_KEY` / `POLYMARKET_API_SECRET` / `POLYMARKET_API_PASSPHRASE` | empty | Optional CLOB API credentials |
| `GAMMA_BASE_URL` | `https://gamma-api.polymarket.com` | Gamma API endpoint |
| `CLOB_BASE_URL` | `https://clob.polymarket.com` | CLOB REST endpoint |
| `CLOB_WS_URL` | `wss://ws-subscriptions-clob.polymarket.com/ws/market` | CLOB WebSocket endpoint |
| `SERIES_SLUG` | `btc-up-or-down-5m` | Market series to trade |
| `EVENT_SLUG_PREFIX` | `btc-updown-5m` | Prefix of the per-window event slug (`<prefix>-<window start unix>`) used to find the active market |
| `DISCOVERY_REFRESH_SECONDS` | `3` | Market discovery refresh interval |
| `RESOLUTION_POLL_SECONDS` | `5` | Settlement/resolution check interval |

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

### Reset options (Settings > Danger zone)

| Option | What it does | Bot must be stopped? |
| --- | --- | --- |
| **Reset paper account** | Deletes paper orders, fills, positions and PnL and recreates the paper balance. Live data is untouched. | Yes |
| **Reset daily trades & profit/loss** | Sets today's trade count and daily profit/loss back to zero for the current mode and lifts any daily loss/profit halt. No history or balance is deleted. | No |
| **Full server reset** | Wipes the entire database (orders, fills, positions, markets, price history, triggers, logs, dashboard setting overrides), returns to paper mode with the bot stopped, and goes back to `.env` defaults. Requires typing `RESET ALL`. Refused while live positions/orders are still open. | Yes |

API equivalents: `POST /api/daily/reset` (`{"confirm": true}`) and `POST /api/server/reset` (`{"confirm": true, "confirmation_phrase": "RESET ALL"}`).

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

Results on this machine (2026-11-12; captured after the review changes):

- `pytest`: **88 passed**.
- `npm run build`: PASS (TypeScript strict, Vite 55 modules).

These checks cover automated behavior and frontend compilation. They do not validate an authenticated exchange session.

---

## Limitations (honest list)

- **Live execution was not exercised with real funds.** SDK construction and response handling were checked against the installed SDK and official documentation; no authenticated live order or redemption was submitted.
- **Polymarket geo eligibility is unverified.** `POLYMARKET_GEO_VERIFIED` stays `false` by default; do not set it without checking your jurisdiction.
- **Paper fees are approximate.** The paper engine uses the configured fee rate and exponent; the live engine reads market fee parameters when available.
- **Gamma resolution delay.** Gamma may keep `closed:false` (with near-1/0 prices) for a while after expiry. Resolution requires `closed:true` and published 1/0 prices, so a paper position can remain open across a restart until Gamma publishes the result.
- **On-chain reconciliation is best-effort.** Live fills depend on the CLOB API responses; there is no separate on-chain balance reconciler.
- **Single market family.** Discovery targets the configured series slug, which defaults to `btc-up-or-down-5m`.
- **Clock skew matters.** Windows clock drift can make books look stale and block trades. Fix time sync rather than relaxing `STALE_DATA_MAX_AGE_MS`.
- **Python version.** Python 3.11+ is supported (tests pass on 3.11 and 3.12).

---

## Troubleshooting

**`setup.bat` / `start.bat` is not recognized (PowerShell)** - PowerShell does not run files from the current folder without a prefix. Use `.\setup.bat`, `.\start.bat`, `.\stop.bat`.

**`python` / `node` / `git` is not recognized** - install it, then close and reopen PowerShell so PATH updates. Re-run `.\setup.bat`.

**My `.env` change had no effect** - `.env` is only read at startup. Run `.\stop.bat`, then `.\start.bat`.

**Dashboard opens but the bot is STOPPED after a restart** - this is intended. Restart recovery always keeps the bot stopped; start it from the dashboard.

**"Port 8000/5173 is already in use"** - close the other process, or run `stop.bat` if it is a previous copy of this app.

**Startup failed** - read `logs\backend.err.log`, `logs\frontend.err.log`, then `logs\backend.log`. `start.bat` stops only the services it started this run.

**Bot does not trade** - confirm the bot is RUNNING, the market is in its configured final window, the ask reaches the trigger, book data is fresh, daily risk limits are intact, and no duplicate/conflict rule blocked the trigger (see the Strategy page audit trail).

**Paper reset does nothing** - stop the bot first; in-memory state would otherwise overwrite the reset.

**Live options are disabled** - this is the intended default. See the live gates above.

---

## Risk warning

This software can place real orders if you enable live trading. Prediction markets are risky and you can lose your entire balance. Use paper mode first, verify your jurisdiction and API credentials, and start with small sizes. This project is not financial advice.
