from __future__ import annotations

import os
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
ROOT_DIR = BACKEND_DIR.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

TEST_DB = ROOT_DIR / "data" / "test_polymarket_bot.db"
for suffix in ("", "-wal", "-shm"):
    Path(str(TEST_DB) + suffix).unlink(missing_ok=True)

os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DB.as_posix()}"
os.environ["LIVE_TRADING_ENABLED"] = "false"
os.environ["POLYMARKET_GEO_VERIFIED"] = "false"
os.environ["POLYMARKET_PRIVATE_KEY"] = ""
os.environ["BTC_PRICE_ENABLED"] = "false"
os.environ["DISCOVERY_REFRESH_SECONDS"] = "3600"
os.environ["RESOLUTION_POLL_SECONDS"] = "3600"
# The shared test books carry a 0.50 bid; with the production stop-loss defaults (20 / 15 %) every
# bot-level test would be stopped out right after its buy. The stop-loss tests switch it on explicitly.
for _stage in range(4):
    os.environ[f"STAGE{_stage}_STOP_LOSS_PCT"] = "0"

import pytest  # noqa: E402

from app.database.models import Base  # noqa: E402
from app.database.session import get_engine, session_scope  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def schema():
    from app.database.migrate import ensure_schema

    ensure_schema()
    yield
    get_engine().dispose()
    for suffix in ("", "-wal", "-shm"):
        Path(str(TEST_DB) + suffix).unlink(missing_ok=True)


@pytest.fixture(autouse=True)
def clean_db(schema):
    with session_scope() as session:
        for table in reversed(Base.metadata.sorted_tables):
            session.execute(table.delete())
    yield
