"""Persistent schema. Paper and live money are separated by the `mode` column
on every financial table and must always be queried with an explicit mode."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


class Market(Base):
    __tablename__ = "markets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    market_id: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    slug: Mapped[str] = mapped_column(String(160), default="")
    event_slug: Mapped[str] = mapped_column(String(160), default="")
    series_id: Mapped[str] = mapped_column(String(32), default="")
    window_start: Mapped[datetime] = mapped_column(DateTime)
    window_end: Mapped[datetime] = mapped_column(DateTime, index=True)
    up_token_id: Mapped[str] = mapped_column(String(128), default="")
    down_token_id: Mapped[str] = mapped_column(String(128), default="")
    tick_size: Mapped[float] = mapped_column(Float, default=0.01)
    min_order_size: Mapped[float] = mapped_column(Float, default=5.0)
    status: Mapped[str] = mapped_column(String(24), default="active")  # active|closed|resolved
    resolved_outcome: Mapped[str | None] = mapped_column(String(8), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class PriceUpdate(Base):
    __tablename__ = "price_updates"
    __table_args__ = (Index("ix_price_updates_market_ts", "market_id", "ts"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    market_id: Mapped[str] = mapped_column(String(120), index=True)
    side: Mapped[str] = mapped_column(String(8))  # UP|DOWN
    best_bid: Mapped[float | None] = mapped_column(Float, nullable=True)
    best_ask: Mapped[float | None] = mapped_column(Float, nullable=True)
    bid_size: Mapped[float | None] = mapped_column(Float, nullable=True)
    ask_size: Mapped[float | None] = mapped_column(Float, nullable=True)
    source: Mapped[str] = mapped_column(String(24), default="clob_ws")
    ts: Mapped[int] = mapped_column(Integer)  # epoch ms (local receipt time)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class TriggerEvent(Base):
    __tablename__ = "trigger_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    market_id: Mapped[str] = mapped_column(String(120), index=True)
    side: Mapped[str] = mapped_column(String(8))
    best_ask: Mapped[float] = mapped_column(Float)
    ask_size: Mapped[float | None] = mapped_column(Float, nullable=True)
    remaining_seconds: Mapped[float] = mapped_column(Float)
    threshold: Mapped[float] = mapped_column(Float)
    triggered: Mapped[bool] = mapped_column(default=False)
    decision: Mapped[str] = mapped_column(String(32), default="observed")
    reason: Mapped[str] = mapped_column(String(200), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_key: Mapped[str] = mapped_column(String(200), unique=True, index=True)
    client_order_id: Mapped[str] = mapped_column(String(64), index=True)
    mode: Mapped[str] = mapped_column(String(8), index=True)
    market_id: Mapped[str] = mapped_column(String(120), index=True)
    side: Mapped[str] = mapped_column(String(8))
    token_id: Mapped[str] = mapped_column(String(128), default="")
    status: Mapped[str] = mapped_column(String(24), default="PENDING", index=True)
    requested_notional: Mapped[float] = mapped_column(Float)
    requested_price: Mapped[float] = mapped_column(Float)  # best ask observed at trigger
    submitted_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    filled_size: Mapped[float] = mapped_column(Float, default=0.0)
    avg_fill_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    fee: Mapped[float] = mapped_column(Float, default=0.0)
    exchange_order_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    latency_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    reason: Mapped[str] = mapped_column(String(300), default="")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class Fill(Base):
    __tablename__ = "fills"
    __table_args__ = (
        UniqueConstraint("order_id", "exchange_trade_id", name="uq_fill_order_trade"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"), index=True)
    mode: Mapped[str] = mapped_column(String(8), index=True)
    market_id: Mapped[str] = mapped_column(String(120), index=True)
    side: Mapped[str] = mapped_column(String(8))
    price: Mapped[float] = mapped_column(Float)
    size: Mapped[float] = mapped_column(Float)
    fee: Mapped[float] = mapped_column(Float, default=0.0)
    exchange_trade_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Position(Base):
    __tablename__ = "positions"
    __table_args__ = (UniqueConstraint("mode", "market_id", "side", name="uq_position_mode_market_side"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    mode: Mapped[str] = mapped_column(String(8), index=True)
    market_id: Mapped[str] = mapped_column(String(120), index=True)
    side: Mapped[str] = mapped_column(String(8))
    size: Mapped[float] = mapped_column(Float, default=0.0)
    avg_price: Mapped[float] = mapped_column(Float, default=0.0)
    cost_basis: Mapped[float] = mapped_column(Float, default=0.0)
    fees: Mapped[float] = mapped_column(Float, default=0.0)
    realized_pnl: Mapped[float] = mapped_column(Float, default=0.0)
    unrealized_pnl: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[str] = mapped_column(String(16), default="open", index=True)
    resolution: Mapped[str | None] = mapped_column(String(8), nullable=True)
    opened_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class Account(Base):
    __tablename__ = "accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    mode: Mapped[str] = mapped_column(String(8), unique=True, index=True)
    balance: Mapped[float] = mapped_column(Float)
    starting_balance: Mapped[float] = mapped_column(Float)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class BotSetting(Base):
    __tablename__ = "bot_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[object] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class AppState(Base):
    """Single-row bot state persisted across restarts."""

    __tablename__ = "app_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    status: Mapped[str] = mapped_column(String(16), default="STOPPED")
    mode: Mapped[str] = mapped_column(String(8), default="paper")
    kill_switch: Mapped[bool] = mapped_column(default=False)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class LogEntry(Base):
    __tablename__ = "logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    level: Mapped[str] = mapped_column(String(12), default="INFO")
    category: Mapped[str] = mapped_column(String(32), default="system", index=True)
    message: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    mode: Mapped[str | None] = mapped_column(String(8), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
