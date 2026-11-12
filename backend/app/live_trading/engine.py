"""Live Polymarket CLOB execution via py-clob-client.

Hard gates: LIVE_TRADING_ENABLED=true, credentials present, and
POLYMARKET_GEO_VERIFIED=true. The engine is only constructed when the operator
explicitly switches mode, and every order still passes the risk manager.

This module is imported by the app but py-clob-client is imported lazily so
paper-only installations still start if the library is missing.
"""

from __future__ import annotations

import asyncio
import logging
import time

from app.config.settings import Settings
from app.execution.base import ExecutionEngine, ExecutionReport, FillPart, OrderRequest

logger = logging.getLogger(__name__)


class LiveTradingUnavailable(RuntimeError):
    pass


class LiveExecutionEngine(ExecutionEngine):
    mode = "live"

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client = None
        self._creds_ready = False
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------
    def _validate_gates(self) -> None:
        if not self._settings.live_trading_enabled:
            raise LiveTradingUnavailable("LIVE_TRADING_ENABLED is false")
        if not self._settings.polymarket_geo_verified:
            raise LiveTradingUnavailable(
                "POLYMARKET_GEO_VERIFIED is false; live eligibility has not been verified"
            )
        if not self._settings.polymarket_private_key.strip():
            raise LiveTradingUnavailable("POLYMARKET_PRIVATE_KEY is not configured")

    def _build_client(self):
        self._validate_gates()
        from py_clob_client.client import ClobClient

        client = ClobClient(
            self._settings.clob_base_url,
            key=self._settings.polymarket_private_key,
            chain_id=self._settings.polymarket_chain_id,
            signature_type=self._settings.polymarket_signature_type,
            funder=self._settings.polymarket_funder_address or None,
        )
        if (
            self._settings.polymarket_api_key
            and self._settings.polymarket_api_secret
            and self._settings.polymarket_api_passphrase
        ):
            from py_clob_client.clob_types import ApiCreds

            creds = ApiCreds(
                api_key=self._settings.polymarket_api_key,
                api_secret=self._settings.polymarket_api_secret,
                api_passphrase=self._settings.polymarket_api_passphrase,
            )
        else:
            creds = client.create_or_derive_api_creds()
        client.set_api_creds(creds)
        return client

    async def _get_client(self):
        async with self._lock:
            if self._client is None:
                self._client = await asyncio.to_thread(self._build_client)
                self._creds_ready = True
            return self._client

    # ------------------------------------------------------------------
    async def place_buy(self, request: OrderRequest) -> ExecutionReport:
        started_ms = int(time.time() * 1000)
        try:
            client = await self._get_client()
        except Exception as exc:
            return ExecutionReport(
                status="REJECTED",
                latency_ms=float(started_ms - request.observed_at_ms),
                error=f"live client unavailable: {exc}",
            )

        try:
            report = await asyncio.to_thread(self._place_sync, client, request)
        except Exception as exc:
            logger.exception("live order failed")
            return ExecutionReport(
                status="REJECTED",
                latency_ms=float(int(time.time() * 1000) - request.observed_at_ms),
                error=f"{type(exc).__name__}: {exc}",
            )
        report.latency_ms = float(int(time.time() * 1000) - request.observed_at_ms)
        return report

    def _place_sync(self, client, request: OrderRequest) -> ExecutionReport:
        from py_clob_client.clob_types import MarketOrderArgs, OrderType
        from py_clob_client.order_builder.constants import BUY

        book = client.get_order_book(request.token_id)
        best_ask = None
        for ask in getattr(book, "asks", []) or []:
            price = float(getattr(ask, "price", ask.get("price") if isinstance(ask, dict) else 0))
            if best_ask is None or price < best_ask:
                best_ask = price
        if best_ask is None:
            return ExecutionReport(status="REJECTED", error="no asks on live book")
        if best_ask > request.max_price + 1e-9:
            return ExecutionReport(
                status="REJECTED",
                submitted_price=best_ask,
                error=f"best ask {best_ask:.4f} above max execution price {request.max_price:.4f}",
            )

        signed = client.create_market_order(
            MarketOrderArgs(
                token_id=request.token_id,
                amount=round(request.notional, 6),
                side=BUY,
                price=round(request.max_price, 6),
            )
        )
        response = client.post_order(signed, OrderType.FOK)
        order_id = response.get("orderID") or response.get("orderId")
        error_msg = response.get("errorMsg") or ""
        status_raw = str(response.get("status") or "").lower()

        if error_msg or not order_id:
            return ExecutionReport(
                status="REJECTED",
                submitted_price=best_ask,
                exchange_order_id=order_id,
                error=error_msg or f"unexpected response: {response}",
                raw=response,
            )

        if status_raw == "unmatched":
            return ExecutionReport(
                status="CANCELED",
                submitted_price=best_ask,
                exchange_order_id=order_id,
                error="order not matched (FOK)",
                raw=response,
            )

        fill = self._reconcile(client, order_id, best_ask, request.notional)
        fill.submitted_price = best_ask
        fill.raw = response
        return fill

    def _reconcile(
        self, client, order_id: str, fallback_price: float, notional: float
    ) -> ExecutionReport:
        size_matched = 0.0
        price = fallback_price
        last_error = None
        for _ in range(max(1, self._settings.live_poll_retries)):
            try:
                order = client.get_order(order_id)
            except Exception as exc:  # transient; keep polling within budget
                last_error = str(exc)
                time.sleep(self._settings.live_poll_interval_ms / 1000.0)
                continue
            if not order:
                break
            status = str(order.get("status") or "").upper()
            try:
                size_matched = float(order.get("size_matched") or order.get("sizeMatched") or 0)
            except (TypeError, ValueError):
                size_matched = 0.0
            try:
                price = float(order.get("price") or fallback_price)
            except (TypeError, ValueError):
                price = fallback_price
            if status in {"MATCHED", "FILLED", "CANCELED", "CANCELLED", "EXPIRED", "UNMATCHED"}:
                break
            time.sleep(self._settings.live_poll_interval_ms / 1000.0)

        fills: list[FillPart] = []
        if size_matched > 0:
            fills.append(FillPart(price=price, size=round(size_matched, 8), fee=0.0))
        spent = size_matched * price
        if size_matched > 0:
            # FOK is all-or-nothing, but a small remainder can occur; report it honestly.
            status = "FILLED" if spent >= 0.98 * notional else "PARTIALLY_FILLED"
        else:
            status = "SUBMITTED"

        # FOK orders should not rest. Cancel any remainder defensively.
        try:
            client.cancel(order_id)
        except Exception:
            pass

        total = sum(f.size for f in fills)
        avg = round(sum(f.price * f.size for f in fills) / total, 6) if total > 0 else None
        return ExecutionReport(
            status=status,
            filled_size=total,
            avg_price=avg,
            fee=0.0,
            exchange_order_id=order_id,
            fills=fills,
            error=last_error,
        )

    async def fetch_order(self, order_id: str) -> dict | None:
        client = self._client
        if client is None or not order_id:
            return None
        try:
            return await asyncio.to_thread(client.get_order, order_id)
        except Exception as exc:
            logger.warning("live order fetch failed: %s", exc)
            return None

    async def cancel_open_orders(self) -> int:
        client = self._client
        if client is None:
            return 0
        try:
            result = await asyncio.to_thread(client.cancel_all)
            return int(result.get("canceled") or 0) if isinstance(result, dict) else 0
        except Exception as exc:
            logger.warning("cancel_all failed: %s", exc)
            return 0

    async def get_balance(self) -> float | None:
        try:
            client = await self._get_client()
        except Exception as exc:
            logger.warning("live balance unavailable: %s", exc)
            return None
        try:
            return await asyncio.to_thread(self._balance_sync, client)
        except Exception as exc:
            logger.warning("live balance query failed: %s", exc)
            return None

    def _balance_sync(self, client) -> float | None:
        from py_clob_client.clob_types import AssetType, BalanceAllowanceParams

        response = client.get_balance_allowance(
            BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
        )
        raw = str(response.get("balance", "0"))
        if "." in raw:
            return float(raw)
        return int(raw) / 1_000_000.0

    async def validate(self) -> dict:
        """Confirm the wallet can actually trade. Raises LiveTradingUnavailable."""
        try:
            client = await self._get_client()
        except LiveTradingUnavailable:
            raise
        except Exception as exc:
            raise LiveTradingUnavailable(f"could not initialize CLOB client: {exc}") from exc
        balance = await asyncio.to_thread(self._balance_sync, client)
        allowance = await asyncio.to_thread(self._allowance_sync, client)
        if balance is None:
            raise LiveTradingUnavailable("could not read collateral balance")
        if balance <= 0:
            raise LiveTradingUnavailable("collateral balance is zero")
        return {"balance": balance, "allowance": allowance}

    def _allowance_sync(self, client) -> float | None:
        from py_clob_client.clob_types import AssetType, BalanceAllowanceParams

        response = client.get_balance_allowance(
            BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
        )
        raw = str(response.get("allowance", "0"))
        if not raw:
            return None
        if "." in raw:
            return float(raw)
        return int(raw) / 1_000_000.0

    async def fetch_onchain_positions(self) -> list[dict] | None:
        """Best-effort read of Polymarket Data API positions for the funder wallet."""
        funder = self._settings.polymarket_funder_address.strip()
        if not funder:
            return None
        import httpx

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.get(
                    "https://data-api.polymarket.com/positions",
                    params={"user": funder, "sizeThreshold": "0.01"},
                    headers={"User-Agent": self._settings.http_user_agent},
                )
                response.raise_for_status()
                data = response.json()
                return data if isinstance(data, list) else None
        except Exception as exc:
            logger.warning("on-chain position fetch failed: %s", exc)
            return None

    async def close(self) -> None:
        self._client = None
