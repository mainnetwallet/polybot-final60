"""Environment-backed application settings.

Secrets (private key / API credentials) are read from the environment only and
are never returned by the API or written to the database.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT_DIR = Path(__file__).resolve().parents[3]
DATA_DIR = ROOT_DIR / "data"
LOG_DIR = ROOT_DIR / "logs"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(ROOT_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- server ---------------------------------------------------------
    host: str = "127.0.0.1"
    port: int = 8000
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    # Optional shared secret for the control API (X-API-Key / Bearer header, ``?token=`` on the
    # WebSocket). Empty = no auth, fine on 127.0.0.1; set it before binding HOST to 0.0.0.0.
    api_token: str = ""
    # Dashboard login. One admin account; the password lives in .env only. Empty ADMIN_PASSWORD = login off.
    admin_username: str = "admin"
    admin_password: str = ""
    # Optional extra signing secret for session cookies (changing it logs every browser out).
    auth_secret: str = ""
    # How long one login stays valid in a browser (sliding: it renews whenever the dashboard is open).
    auth_session_days: int = 365
    log_level: str = "INFO"

    # --- database -------------------------------------------------------
    database_url: str = f"sqlite:///{(DATA_DIR / 'polymarket_bot.db').as_posix()}"

    # --- polymarket endpoints ------------------------------------------
    gamma_base_url: str = "https://gamma-api.polymarket.com"
    clob_base_url: str = "https://clob.polymarket.com"
    clob_ws_url: str = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
    rtds_ws_url: str = "wss://ws-live-data.polymarket.com"
    # Since 2026-08-14 Polymarket's 5-minute crypto markets settle on the Chainlink 60-second TWAP (both
    # the price to beat and the final price); the bot streams only that official series (no spot fallback).
    # Polymarket website's own open-price endpoint: last-resort Price To Beat when Gamma has not published it
    # and the RTDS tick at the window start was missed (e.g. the bot was started mid-window)
    crypto_price_api_url: str = "https://polymarket.com/api/crypto/crypto-price"
    btc_price_enabled: bool = True  # dashboard-only official Polymarket BTC price / price-to-beat
    series_slug: str = "btc-up-or-down-5m"
    event_slug_prefix: str = "btc-updown-5m"  # event slug = <prefix>-<window start unix>
    http_user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )

    # --- strategy -------------------------------------------------------
    trigger_threshold: float = 0.80
    final_window_seconds: int = 80
    # Extra staged entries inside the final window: once `remaining <= stageN_seconds`
    # and a best ask >= stageN_threshold, one more buy per stage (same market).
    stage2_seconds: int = 50
    stage2_threshold: float = 0.90
    stage3_seconds: int = 30
    stage3_threshold: float = 0.90
    max_execution_price: float = 0.99
    trade_amount_usdc: float = 1.0
    # Book age only resets on a websocket book event, so a stable price on a quiet book looks
    # "old" without being wrong, so too low a limit blocks stage 2/3 (armed by the clock) on a quiet
    # book. 2s: a signal is never built on a book older than that. Exits do not check book age.
    stale_data_max_age_ms: int = 2000
    # Retry guard for failed/cancelled entries (per stage, per market)
    # A FOK entry that did not fill leaves nothing on the book, so it is retried almost at once.
    # 15 attempts x 250 ms keeps a retry window of a few seconds.
    entry_max_attempts: int = 15
    entry_retry_gap_ms: int = 250
    # Gap between two sell attempts of the same exit (take-profit / stop-loss) when the previous
    # FOK sell did not fill. Kept short: an exit must not wait a full second for the next try.
    exit_retry_gap_ms: int = 50
    max_open_positions: int = 1
    # Price-to-beat filter: a buy needs the current BTC price at least this many USD beyond the
    # price to beat in the bought direction (UP: current >= beat + gap, DOWN: current <= beat - gap).
    # 0 disables the rule. With the rule on, missing/stale BTC data blocks the buy (fail closed).
    min_price_gap_usd: float = 20.0  # stage 1
    # Each add-on stage can demand a bigger gap: stage 2 = 25 USD, stage 3 = 30 USD by default.
    stage2_min_price_gap_usd: float = 25.0
    stage3_min_price_gap_usd: float = 30.0
    # Stage 0: an early, independent entry with its own take-profit SELL. While `stage0_start_seconds`
    # (or fewer) remain and the final window has not started yet, buy a side whose best ask is
    # <= stage0_max_ask and whose BTC price gap beyond the price to beat is >= stage0_min_price_gap_usd.
    # The shares are sold again as soon as the best bid is the tier's take-profit percentage above the buy price.
    stage0_enabled: bool = True
    stage0_start_seconds: int = 200
    stage0_min_ask: float = 0.60
    stage0_max_ask: float = 0.85
    stage0_min_price_gap_usd: float = 80.0
    # Take-profit for stage 0: sell once the best bid is this many percent ABOVE the average price paid per
    # share (cost * (1 + pct / 100), capped at 0.99). The percentage depends on what stage 0 paid per share:
    #   cost <  stage0_mid_from_ask   -> stage0_take_profit_pct_low  (default 0.60-0.70 buy -> +15 %)
    #   cost <  stage0_high_from_ask  -> stage0_take_profit_pct_mid  (default 0.70-0.80 buy -> +10 %)
    #   otherwise                     -> stage0_take_profit_pct_high (default 0.80-0.85 buy -> +5 %)
    # 0 = no take-profit for that tier: the shares are held to settlement.
    stage0_mid_from_ask: float = 0.70
    stage0_high_from_ask: float = 0.80
    stage0_take_profit_pct_low: float = 15.0
    stage0_take_profit_pct_mid: float = 10.0
    stage0_take_profit_pct_high: float = 5.0
    # Stage 1 take-profit: stage 1 shares are sold again as soon as the best bid reaches a price that
    # depends on what they cost per share:
    #   cost <= stage1_sell_max_buy (0.86) -> cost * (1 + stage1_take_profit_pct / 100), i.e. +10 %
    #                                         (capped at 0.99; 0 = no take-profit for these shares)
    #   cost above it                      -> stage1_sell_price_high (0.99).
    stage1_sell_enabled: bool = True
    stage1_sell_max_buy: float = 0.86
    stage1_take_profit_pct: float = 10.0
    stage1_sell_price_high: float = 0.99
    # Stage 2 take-profit: stage 2 shares are sold as soon as the best bid reaches stage2_sell_price.
    stage2_sell_enabled: bool = True
    stage2_sell_price: float = 0.99
    # Stop-loss per entry stage, in percent of the average price paid per share: once the best bid
    # is at or below cost * (1 - pct / 100), that stage's own shares are sold at the market.
    # 0 switches the stop-loss off for that stage. Stage 0 = 50 %, stage 1 = 15 %, stage 2 = 9 %, stage 3 = 5 % by default.
    stage0_stop_loss_pct: float = 50.0
    stage1_stop_loss_pct: float = 15.0
    stage2_stop_loss_pct: float = 9.0
    stage3_stop_loss_pct: float = 5.0
    duplicate_policy: str = "one_per_market"  # one_per_market | one_per_side | allow
    conflict_policy: str = "up_first"  # up_first | down_first | highest_ask | block_both

    # --- risk -----------------------------------------------------------
    max_daily_loss: float = 5.0
    daily_profit_target: float = 10.0
    max_trades_per_day: int = 600
    kill_switch: bool = False

    # --- paper ----------------------------------------------------------
    paper_starting_balance: float = 10.0
    paper_fill_ratio: float = 1.0
    paper_slippage_bps: float = 0.0
    paper_fee_rate: float = 0.07
    paper_fee_exponent: float = 1.0

    # --- live -----------------------------------------------------------
    live_trading_enabled: bool = False
    live_confirmation_phrase: str = "GO LIVE"
    polymarket_private_key: str = ""
    polymarket_funder_address: str = ""
    polymarket_signature_type: int = 0
    polymarket_chain_id: int = 137
    polymarket_api_key: str = ""
    polymarket_api_secret: str = ""
    polymarket_api_passphrase: str = ""
    polymarket_geo_verified: bool = False
    # After a market resolves, winning tokens only become pUSD once redeemed on-chain
    # (docs.polymarket.com/trading/positions/manage). Opt-in: submits real transactions.
    live_auto_redeem: bool = False
    polymarket_relayer_api_key: str = ""  # gasless redeem; empty = EOA tx paid in POL
    polymarket_relayer_api_key_address: str = ""

    # --- runtime loops --------------------------------------------------
    discovery_refresh_seconds: float = 3.0
    resolution_poll_seconds: float = 5.0
    ws_heartbeat_seconds: float = 10.0
    ws_price_persist_interval_ms: int = 500

    @field_validator("database_url")
    @classmethod
    def _absolutize_sqlite(cls, value: str) -> str:
        if value.startswith("sqlite:///") and not value.startswith("sqlite:////"):
            raw = value[len("sqlite:///"):]
            path = Path(raw)
            if not path.is_absolute():
                path = (ROOT_DIR / raw).resolve()
            return f"sqlite:///{path.as_posix()}"
        return value

    @field_validator("log_level")
    @classmethod
    def _normalise_level(cls, value: str) -> str:
        return value.upper()

    @property
    def api_exposed_without_token(self) -> bool:
        loopback = {"127.0.0.1", "localhost", "::1"}
        return (
            self.host.strip().lower() not in loopback
            and not self.api_token.strip()
            and not self.admin_password
        )

    @property
    def auth_enabled(self) -> bool:
        return bool(self.admin_password)

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def live_credentials_present(self) -> bool:
        return bool(self.polymarket_private_key.strip())


@lru_cache
def get_settings() -> Settings:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    return Settings()
