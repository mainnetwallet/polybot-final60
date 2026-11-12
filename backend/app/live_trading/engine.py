"""Live Polymarket CLOB execution via py-clob-client-v2 (CLOB V2, pUSD collateral).

Polymarket's CLOB V2 went live on 2026-04-28; the legacy ``py-clob-client`` no
longer works against production. This engine uses ``py-clob-client-v2``.

Hard gates: LIVE_TRADING_ENABLED=true, credentials present, and
POLYMARKET_GEO_VERIFIED=true. The engine is only constructed when the operator
explicitly switches mode, and every order still passes the risk manager.

The SDK is imported lazily so paper-only installations still start if the
library is missing.

Trigger path: no REST round-trip happens before the order is posted. Tick size
and neg-risk flags are pre-warmed when a market opens (see ``prewarm``).
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable

from app.config.settings import Settings
from app.execution.base import ExecutionEngine, ExecutionReport, FillPart, OrderRequest

logger = logging.getLogger(__name__)

TERMINAL_STATUSES = {"MATCHED", "FILLED", "CANCELED", "CANCELLED", "EXPIRED", "UNMATCHED"}


class LiveTradingUnavailable(RuntimeError):
    pass


def _num(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


class LiveExecutionEngine(ExecutionEngine):
    mode = "live"

    def __init__(
        self, settings: Settings, balance_hint: Callable[[], float | None] | None = None
    ) -> None:
        self._settings = settings
        self._client = None
        self._creds_ready = False
        self._lock = asyncio.Lock()
        self._balance_hint = balance_hint

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
        from py_clob_client_v2 import ApiCreds, ClobClient

        client = ClobClient(
            host=self._settings.clob_base_url,
            chain_id=self._settings.polymarket_chain_id,
            key=self._settings.polymarket_private_key,
            signature_type=self._settings.polymarket_signature_type,
            funder=self._settings.polymarket_funder_address or None,
        )
        if (
            self._settings.polymarket_api_key
            and self._settings.polymarket_api_secret
            and self._settings.polymarket_api_passphrase
        ):
            creds = ApiCreds(
                api_key=self._settings.polymarket_api_key,
                api_secret=self._settings.polymarket_api_secret,
                api_passphrase=self._settings.polymarket_api_passphrase,
            )
        else:
            creds = client.create_or_derive_api_key()
        client.set_api_creds(creds)
        return client

    async def _get_client(self):
        async with self._lock:
            if self._client is None:
                self._client = await asyncio.to_thread(self._build_client)
                self._creds_ready = True
            return self._client

    # ------------------------------------------------------------------
    async def prewarm(self, token_ids: list[str]) -> None:
        """Cache tick size / neg-risk for a market so the first order has no lookups."""
        if self._client is None:
            return

        def _warm(client) -> None:
            for token_id in token_ids:
                try:
                    client.get_tick_size(token_id)
                    client.get_neg_risk(token_id)
                except Exception as exc:  # best effort only
                    logger.debug("live prewarm failed for %s: %s", token_id[:10], exc)

        try:
            await asyncio.to_thread(_warm, self._client)
        except Exception as exc:
            logger.debug("live prewarm error: %s", exc)

    # ------------------------------------------------------------------
    async def place_buy(self, request: OrderRequest) -> ExecutionReport:
        try:
            client = await self._get_client()
        except Exception as exc:
            return ExecutionReport(
                status="REJECTED",
                latency_ms=float(int(time.time() * 1000) - request.observed_at_ms),
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
        from py_clob_client_v2 import MarketOrderArgs, OrderType, Side

        balance = self._balance_hint() if self._balance_hint else None
        args = MarketOrderArgs(
            token_id=request.token_id,
            amount=round(request.notional, 6),
            side=Side.BUY,
            # price is the worst acceptable price (cap), not a resting limit
            price=round(request.max_price, 6),
            order_type=OrderType.FOK,
            user_usdc_balance=float(balance) if balance and balance > 0 else 0,
        )
        response = client.create_and_post_market_order(args, None, OrderType.FOK)
        if not isinstance(response, dict):
            return ExecutionReport(
                status="REJECTED", error=f"unexpected response: {response!r}"[:300]
            )

        order_id = response.get("orderID") or response.get("orderId")
        error_msg = str(response.get("errorMsg") or "")
        status_raw = str(response.get("status") or "").lower()
        success = response.get("success")

        if error_msg or success is False or not order_id:
            text = error_msg or f"unexpected response: {response}"
            killed = "fok" in text.lower() or "fully filled" in text.lower()
            return ExecutionReport(
                status="CANCELED" if killed else "REJECTED",
                exchange_order_id=order_id,
                error=text,
                raw=response,
            )

        if status_raw == "unmatched":
            return ExecutionReport(
                status="CANCELED",
                exchange_order_id=order_id,
                error="order not matched (FOK)",
                raw=response,
            )

        making = _num(response.get("makingAmount"))  # BUY: collateral paid
        taking = _num(response.get("takingAmount"))  # BUY: shares received
        if making > request.notional * 10 and taking > 0:
            # amounts reported in 6-decimal base units
            making /= 1_000_000.0
            taking /= 1_000_000.0

        if status_raw == "matched" and making > 0 and taking > 0:
            return self._report_from_amounts(order_id, making, taking, request, response)

        # delayed / live / missing amounts: confirm with the exchange.
        return self._reconcile(client, order_id, request, response)

    def _report_from_amounts(
        self, order_id: str, making: float, taking: float, request: OrderRequest, raw: dict
    ) -> ExecutionReport:
        price = round(making / taking, 6)
        status = "FILLED" if making >= 0.98 * request.notional else "PARTIALLY_FILLED"
        return ExecutionReport(
            status=status,
            filled_size=round(taking, 8),
            avg_price=price,
            fee=0.0,
            submitted_price=price,
            exchange_order_id=order_id,
            fills=[FillPart(price=price, size=round(taking, 8), fee=0.0, exchange_trade_id=None)],
            raw=raw,
        )

    def _reconcile(self, client, order_id: str, request: OrderRequest, raw: dict) -> ExecutionReport:
        size_matched = 0.0
        last_error = None
        status = ""
        for attempt in range(max(1, self._settings.live_poll_retries)):
            try:
                order = client.get_order(order_id)
            except Exception as exc:  # transient; keep polling within budget
                last_error = str(exc)
                order = None
            if order:
                status = str(order.get("status") or "").upper()
                size_matched = _num(order.get("size_matched") or order.get("sizeMatched"))
                if status in TERMINAL_STATUSES:
                    break
            if attempt + 1 < self._settings.live_poll_retries:
                time.sleep(self._settings.live_poll_interval_ms / 1000.0)

        if status not in TERMINAL_STATUSES:
            # FOK must not rest; cancel defensively only when it is not terminal.
            try:
                from py_clob_client_v2 import OrderPayload

                client.cancel_order(OrderPayload(orderID=order_id))
            except Exception:
                pass

        if size_matched <= 0:
            return ExecutionReport(
                status="CANCELED" if status in TERMINAL_STATUSES else "SUBMITTED",
                exchange_order_id=order_id,
                error=last_error or ("order not matched (FOK)" if status in TERMINAL_STATUSES else None),
                raw=raw,
            )

        # Exact average price from the exchange's own trades for this order.
        price = self._price_from_trades(client, order_id, size_matched)
        if price is None:
            # Never invent a fill price: fall back to the amounts the exchange returned.
            making = _num(raw.get("makingAmount"))
            taking = _num(raw.get("takingAmount"))
            if making > request.notional * 10 and taking > 0:
                making /= 1_000_000.0
                taking /= 1_000_000.0
            price = round(making / taking, 6) if making > 0 and taking > 0 else None
        if price is None:
            return ExecutionReport(
                status="SUBMITTED",
                filled_size=0.0,
                exchange_order_id=order_id,
                error="fill reported but price unavailable; awaiting reconciliation",
                raw=raw,
            )
        spent = size_matched * price
        return ExecutionReport(
            status="FILLED" if spent >= 0.98 * request.notional else "PARTIALLY_FILLED",
            filled_size=round(size_matched, 8),
            avg_price=price,
            fee=0.0,
            submitted_price=price,
            exchange_order_id=order_id,
            fills=[FillPart(price=price, size=round(size_matched, 8), fee=0.0)],
            raw=raw,
        )

    @staticmethod
    def _price_from_trades(client, order_id: str, size_hint: float) -> float | None:
        try:
            trades = client.get_trades()
        except Exception:
            return None
        cost = 0.0
        size = 0.0
        for trade in trades or []:
            if not isinstance(trade, dict) or trade.get("taker_order_id") != order_id:
                continue
            trade_size = _num(trade.get("size"))
            trade_price = _num(trade.get("price"))
            if trade_size > 0 and trade_price > 0:
                cost += trade_size * trade_price
                size += trade_size
        if size > 0 and abs(size - size_hint) <= max(0.01, 0.02 * size_hint):
            return round(cost / size, 6)
        return None

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
        except Exception as exc:
            logger.warning("cancel_all failed: %s", exc)
            return 0
        if isinstance(result, dict):
            canceled = result.get("canceled")
            if isinstance(canceled, list):
                return len(canceled)
            try:
                return int(canceled or 0)
            except (TypeError, ValueError):
                return 0
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

    @staticmethod
    def _to_units(raw) -> float | None:
        text = str(raw if raw is not None else "").strip()
        if not text:
            return None
        if "." in text:
            return float(text)
        return int(text) / 1_000_000.0

    def _collateral_response(self, client) -> dict:
        from py_clob_client_v2 import AssetType, BalanceAllowanceParams

        response = client.get_balance_allowance(
            BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
        )
        return response if isinstance(response, dict) else {}

    def _balance_sync(self, client) -> float | None:
        return self._to_units(self._collateral_response(client).get("balance", "0"))

    def _allowance_sync(self, client) -> float | None:
        response = self._collateral_response(client)
        if response.get("allowance") not in (None, ""):
            return self._to_units(response.get("allowance"))
        allowances = response.get("allowances")
        if isinstance(allowances, dict) and allowances:
            values = [self._to_units(v) for v in allowances.values()]
            values = [v for v in values if v is not None]
            return max(values) if values else None
        return None

    async def validate(self) -> dict:
        """Confirm the wallet can actually trade. Raises LiveTradingUnavailable."""
        try:
            client = await self._get_client()
        except LiveTradingUnavailable:
            raise
        except Exception as exc:
            raise LiveTradingUnavailable(f"could not initialize CLOB client: {exc}") from exc
        try:
            balance = await asyncio.to_thread(self._balance_sync, client)
            allowance = await asyncio.to_thread(self._allowance_sync, client)
        except Exception as exc:
            raise LiveTradingUnavailable(f"balance query failed: {exc}") from exc
        if balance is None:
            raise LiveTradingUnavailable("could not read collateral (pUSD) balance")
        if balance <= 0:
            raise LiveTradingUnavailable(
                "collateral (pUSD) balance is zero; wrap USDC.e into pUSD via the Collateral Onramp"
            )
        return {"balance": balance, "allowance": allowance}

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
