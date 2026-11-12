"""Environment-backed application settings.

Secrets (private key / API credentials) are read from the environment only and
are never returned by the API or written to the database.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
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
    log_level: str = "INFO"

    # --- database -------------------------------------------------------
    database_url: str = f"sqlite:///{(DATA_DIR / 'polymarket_bot.db').as_posix()}"

    # --- polymarket endpoints ------------------------------------------
    gamma_base_url: str = "https://gamma-api.polymarket.com"
    clob_base_url: str = "https://clob.polymarket.com"
    clob_ws_url: str = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
    series_slug: str = "btc-up-or-down-5m"
    event_slug_prefix: str = "btc-updown-5m"  # event slug = <prefix>-<window start unix>
    http_user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )

    # --- strategy -------------------------------------------------------
    trigger_threshold: float = 0.80
    final_window_seconds: int = 60
    max_execution_price: float = 0.99
    trade_amount_usdc: float = 5.0
    stale_data_max_age_ms: int = 2000
    max_open_positions: int = 1
    duplicate_policy: str = "one_per_market"  # one_per_market | one_per_side | allow
    conflict_policy: str = "up_first"  # up_first | down_first | highest_ask | block_both

    # --- risk -----------------------------------------------------------
    max_daily_loss: float = 5.0
    daily_profit_target: float = 10.0
    max_trades_per_day: int = 60
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
