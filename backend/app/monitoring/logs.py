from __future__ import annotations

import asyncio
import logging
from typing import Protocol

from sqlalchemy import delete, select

from app.database.models import LogEntry
from app.database.session import session_scope

logger = logging.getLogger(__name__)


class Publisher(Protocol):
    async def broadcast(self, event: dict) -> None: ...


class NullPublisher:
    async def broadcast(self, event: dict) -> None:
        return None


class LogService:
    """Non-blocking log pipeline: websocket/trigger events are enqueued and a
    worker persists them, so order handling never waits on SQLite."""

    def __init__(self, publisher: Publisher | None = None, max_rows: int = 20_000) -> None:
        self._publisher = publisher or NullPublisher()
        self._queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=5000)
        self._task: asyncio.Task | None = None
        self._running = False
        self._dropped = 0
        self._max_rows = max_rows

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._worker(), name="log-writer")

    async def stop(self) -> None:
        self._running = False
        if self._task:
            # Drain buffered records so start/stop events survive a restart.
            try:
                await asyncio.wait_for(self._queue.join(), timeout=2.0)
            except TimeoutError:
                pass
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    def log(
        self,
        level: str,
        category: str,
        message: str,
        payload: dict | None = None,
        mode: str | None = None,
        persist: bool = True,
    ) -> None:
        record = {
            "level": level.upper(),
            "category": category,
            "message": message,
            "payload": payload,
            "mode": mode,
            "persist": persist,
        }
        try:
            self._queue.put_nowait(record)
        except asyncio.QueueFull:
            self._dropped += 1

    async def _worker(self) -> None:
        while True:
            record = await self._queue.get()
            try:
                numeric_level = getattr(logging, record["level"], logging.INFO)
                await asyncio.to_thread(
                    logger.log, numeric_level, "[%s] %s", record["category"], record["message"]
                )
                if record["persist"]:
                    await asyncio.to_thread(self._persist, record)
                await self._publisher.broadcast(
                    {
                        "type": "log",
                        "data": {
                            "level": record["level"],
                            "category": record["category"],
                            "message": record["message"],
                            "payload": record["payload"],
                            "mode": record["mode"],
                        },
                    }
                )
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning("log pipeline failure: %s", exc)
            finally:
                self._queue.task_done()

    def _persist(self, record: dict) -> None:
        with session_scope() as session:
            session.add(
                LogEntry(
                    level=record["level"],
                    category=record["category"],
                    message=record["message"],
                    payload=record["payload"],
                    mode=record["mode"],
                )
            )
            count = session.execute(select(LogEntry.id).order_by(LogEntry.id.desc()).limit(1)).scalar_one_or_none()
            if count and count > self._max_rows:
                cutoff = count - self._max_rows
                session.execute(delete(LogEntry).where(LogEntry.id <= cutoff))
