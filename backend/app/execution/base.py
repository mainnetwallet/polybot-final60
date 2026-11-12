from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class FillPart:
    price: float
    size: float
    fee: float = 0.0
    exchange_trade_id: str | None = None


@dataclass
class OrderRequest:
    order_key: str
    client_order_id: str
    market_id: str
    side: str
    token_id: str
    notional: float
    max_price: float
    observed_at_ms: int
    order_min_size: float = 0.0
    asset_version: int = 1


@dataclass
class ExecutionReport:
    status: str  # SUBMITTED | PARTIALLY_FILLED | FILLED | REJECTED | CANCELED
    filled_size: float = 0.0
    avg_price: float | None = None
    fee: float = 0.0
    submitted_price: float | None = None
    exchange_order_id: str | None = None
    fills: list[FillPart] = field(default_factory=list)
    latency_ms: float | None = None
    error: str | None = None
    raw: dict | None = None


class ExecutionEngine(ABC):
    mode: str

    @abstractmethod
    async def place_buy(self, request: OrderRequest) -> ExecutionReport:
        """Submit a marketable BUY for `notional` USDC, never above max_price."""

    @abstractmethod
    async def get_balance(self) -> float | None:
        """Collateral balance in USDC, or None if unavailable."""

    async def close(self) -> None:  # pragma: no cover - default no-op
        return None
