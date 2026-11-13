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
import math
import time
from collections.abc import Callable

from app.config.settings import Settings
from app.execution.base import ExecutionEngine, ExecutionReport, FillPart, OrderRequest, SellRequest

logger = logging.getLogger(__name__)

# Tick used when the market's real tick is not cached yet. 0.01 is the coarsest tick these
# markets use, so a price valid on it is valid on every finer tick as well.
DEFAULT_TICK_SIZE = 0.01

# HTTP statuses where the exchange may or may not have processed the request. Every other
# 4xx is a definite refusal: the order does not exist on the exchange.
_AMBIGUOUS_HTTP_STATUSES = {408}


class LiveTradingUnavailable(RuntimeError):
    pass


def _num(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def cap_price_to_tick(price: float, tick: float) -> float:
    """Largest price <= ``price`` that sits on the tick grid and inside ``[tick, 1 - tick]``.

    The CLOB (and the SDK's ``price_valid``) rejects any price above ``1 - tick``. With a
    0.01 tick a 0.991 cap is therefore refused outright, so the cap is rounded *down* to the
    grid: the order never pays more than the configured maximum.
    """
    if not 0 < tick < 0.5:
        return round(price, 6)
    steps = math.floor(round(price / tick, 6) + 1e-9)
    capped = min(max(steps * tick, tick), 1.0 - tick)
    return round(capped, 6)


def floor_size(size: float, decimals: int = 2) -> float:
    """Round a share count DOWN so a SELL never asks for more shares than are held."""
    factor = 10**decimals
    return math.floor(round(size * factor, 6) + 1e-9) / factor


def ceil_price_to_tick(price: float, tick: float) -> float:
    """Smallest price >= ``price`` on the tick grid and inside ``[tick, 1 - tick]``.

    Used for the minimum price of a SELL: rounding up never sells below the configured floor.
    """
    if not 0 < tick < 0.5:
        return round(price, 6)
    steps = math.ceil(round(price / tick, 6) - 1e-9)
    return round(min(max(steps * tick, tick), 1.0 - tick), 6)


def _make_client_class():
    """ClobClient subclass that does not poll for settlement hashes after posting.

    ``post_order`` (with ``deferExec=false``, the documented default) polls the trades API for
    up to 30s to attach transaction hashes to a matched order. Nothing here uses those hashes
    and the poll would hold the order lock, so it is skipped. If a future SDK renames the hook
    the override simply stops applying: orders still work, only slower.
    """
    from py_clob_client_v2 import ClobClient

    class LiveClobClient(ClobClient):
        def _resolve_transactions_hashes(self, response):
            return response

    return LiveClobClient


def _error_text(exc: Exception) -> str:
    detail = getattr(exc, "error_msg", None) or getattr(exc, "msg", None) or ""
    return str(detail)[:200]


def classify_post_exception(exc: Exception) -> ExecutionReport:
    """Map an SDK exception raised while building/posting an order to an ExecutionReport.

    * ``PolyApiException`` with a 4xx status: the exchange answered and refused the order.
      A FOK that could not be fully matched comes back this way too -> CANCELED.
    * ``PolyException`` (not an API error): raised by the SDK while building the order
      (invalid price / tick size, market info lookup), before anything was sent -> REJECTED.
    * Anything else (timeouts, connection errors, 5xx, 408): the order may have been accepted,
      so it stays SUBMITTED and is not retried until reconciled.
    """
    from py_clob_client_v2.exceptions import PolyApiException, PolyException

    if isinstance(exc, PolyApiException):
        status = getattr(exc, "status_code", None)
        if isinstance(status, int) and 400 <= status < 500 and status not in _AMBIGUOUS_HTTP_STATUSES:
            text = _error_text(exc)
            lowered = text.lower()
            if "fok" in lowered or "fully filled" in lowered:
                return ExecutionReport(
                    status="CANCELED",
                    error="FOK order was not filled",
                    raw={"http_status": status, "error": text},
                )
            return ExecutionReport(
                status="REJECTED",
                error=f"CLOB rejected the order (HTTP {status}): {text}" if text else f"CLOB rejected the order (HTTP {status})",
                raw={"http_status": status, "error": text},
            )
    elif isinstance(exc, PolyException):
        text = _error_text(exc)
        return ExecutionReport(
            status="REJECTED",
            error=f"order not sent: {text}" if text else "order not sent",
            raw={"error": text},
        )
    return ExecutionReport(
        status="SUBMITTED",
        error="order submission outcome is unknown; reconcile before retrying",
    )


class LiveExecutionEngine(ExecutionEngine):
    mode = "live"

    def __init__(
        self, settings: Settings, balance_hint: Callable[[], float | None] | None = None
    ) -> None:
        self._settings = settings
        self._client = None
        self._lock = asyncio.Lock()
        self._order_lock = asyncio.Lock()
        self._orders_allowed = False
        self._balance_hint = balance_hint
        # token_id -> (fee rate, exponent) as published by the CLOB market info (``fd``).
        self._fee_params: dict[str, tuple[float, float]] = {}

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
        signature_type = self._settings.polymarket_signature_type
        if signature_type not in (0, 1, 2, 3):
            raise LiveTradingUnavailable(
                "POLYMARKET_SIGNATURE_TYPE must be 0 (EOA), 1 (email/magic proxy), "
                "2 (browser/MetaMask proxy) or 3 (smart-contract / deposit wallet)"
            )
        if signature_type != 0 and not self._settings.polymarket_funder_address.strip():
            raise LiveTradingUnavailable(
                "POLYMARKET_FUNDER_ADDRESS is required when POLYMARKET_SIGNATURE_TYPE is 1, 2 or 3; "
                "use the wallet address shown on your Polymarket profile"
            )

    def _build_client(self):
        self._validate_gates()
        from py_clob_client_v2 import ApiCreds

        client = _make_client_class()(
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
            return self._client

    # ------------------------------------------------------------------
    def _load_fee_params(self, client, condition_id: str, token_ids: list[str]) -> None:
        """Cache the market's taker fee parameters (CLOB ``fd.r`` rate / ``fd.e`` exponent)."""
        info = client.get_clob_market_info(condition_id)
        fd = (info or {}).get("fd") or {}
        if "r" not in fd:
            return
        rate = float(fd["r"])
        exponent = float(fd.get("e", self._settings.paper_fee_exponent))
        for token_id in token_ids:
            self._fee_params[token_id] = (rate, exponent)

    def estimate_fee(self, token_id: str, size: float, price: float) -> float:
        """Taker fee in USDC: ``size * rate * (p * (1 - p)) ** exponent``, 5 decimals.

        docs.polymarket.com/trading/fees: fees are taker-only, charged in USDC at match
        time and rounded to 5 decimal places. The rate comes from the market's own CLOB
        parameters; when those are unavailable the documented crypto rate from the
        settings is used so the PnL never silently ignores fees.
        """
        rate, exponent = self._fee_params.get(
            token_id, (self._settings.paper_fee_rate, self._settings.paper_fee_exponent)
        )
        if size <= 0 or price <= 0 or price >= 1:
            return 0.0
        return round(size * rate * ((price * (1.0 - price)) ** exponent), 5)

    async def prewarm(self, token_ids: list[str], condition_id: str | None = None) -> None:
        """Cache tick size / neg-risk / fee parameters so the first order has no lookups."""
        if self._client is None:
            return

        def _warm(client) -> None:
            if condition_id:
                try:
                    self._load_fee_params(client, condition_id, token_ids)
                except Exception as exc:  # best effort only
                    logger.debug("live fee prewarm failed for %s (%s)", condition_id[:10], type(exc).__name__)
            for token_id in token_ids:
                try:
                    client.get_tick_size(token_id)
                    client.get_neg_risk(token_id)
                except Exception as exc:  # best effort only
                    logger.debug("live prewarm failed for %s (%s)", token_id[:10], type(exc).__name__)

        try:
            await asyncio.to_thread(_warm, self._client)
        except Exception as exc:
            logger.debug("live prewarm error (%s)", type(exc).__name__)

    # ------------------------------------------------------------------
    async def place_buy(self, request: OrderRequest) -> ExecutionReport:
        if not self._orders_allowed:
            return ExecutionReport(status="REJECTED", error="new live orders are blocked")
        async with self._order_lock:
            if not self._orders_allowed:
                return ExecutionReport(status="REJECTED", error="new live orders are blocked")
            report = await self._place_buy_locked(request)
            report.latency_ms = float(max(0, int(time.time() * 1000) - request.observed_at_ms))
            return report

    async def place_sell(self, request: SellRequest) -> ExecutionReport:
        if not self._orders_allowed:
            return ExecutionReport(status="REJECTED", error="new live orders are blocked")
        async with self._order_lock:
            if not self._orders_allowed:
                return ExecutionReport(status="REJECTED", error="new live orders are blocked")
            try:
                client = await self._get_client()
            except Exception as exc:
                return ExecutionReport(
                    status="REJECTED", error=f"live client unavailable: {type(exc).__name__}"
                )
            try:
                report = await asyncio.to_thread(self._place_sell_sync, client, request)
            except Exception as exc:
                logger.error("live sell failed (%s)", type(exc).__name__)
                report = ExecutionReport(
                    status="SUBMITTED",
                    error="sell submission outcome is unknown; reconcile before retrying",
                )
            report.latency_ms = float(max(0, int(time.time() * 1000) - request.observed_at_ms))
            return report

    def _ensure_conditional_allowance(self, client, token_id: str) -> None:
        """Best effort, once per token: let the CLOB re-sync the outcome-token balance/allowance.

        A SELL spends outcome tokens, which needs the conditional-token allowance (BUYs only need
        the collateral one). Failures are ignored; the sell itself reports a real problem.
        """
        done = self.__dict__.setdefault("_conditional_synced", set())
        if token_id in done:
            return
        try:
            from py_clob_client_v2 import AssetType, BalanceAllowanceParams

            client.update_balance_allowance(
                BalanceAllowanceParams(asset_type=AssetType.CONDITIONAL, token_id=token_id)
            )
            done.add(token_id)
        except Exception as exc:
            logger.warning("conditional allowance sync failed (%s)", type(exc).__name__)

    def _place_sell_sync(self, client, request: SellRequest) -> ExecutionReport:
        from py_clob_client_v2 import MarketOrderArgs, OrderType, Side

        try:
            tick = float(client.get_tick_size(request.token_id))
        except Exception:
            tick = DEFAULT_TICK_SIZE
        shares = floor_size(request.size)
        if shares <= 0:
            return ExecutionReport(status="REJECTED", error="sell size rounds down to zero")
        self._ensure_conditional_allowance(client, request.token_id)
        args = MarketOrderArgs(
            token_id=request.token_id if request.asset_version == 1 else None,
            amount=shares,  # SELL amount is in shares
            side=Side.SELL,
            # worst acceptable price (floor), not a resting limit
            price=ceil_price_to_tick(request.min_price, tick),
            order_type=OrderType.FOK,
            user_usdc_balance=0,
            position_id=request.token_id if request.asset_version == 2 else None,
        )
        try:
            response = client.create_and_post_market_order(args, None, OrderType.FOK, False)
        except Exception as exc:
            report = classify_post_exception(exc)
            if report.status == "SUBMITTED":
                report.error = "sell submission outcome is unknown; reconcile before retrying"
                logger.error("live sell submit outcome unknown (%s)", type(exc).__name__)
            else:
                logger.warning("live sell not accepted (%s): %s", type(exc).__name__, report.error)
            return report
        if not isinstance(response, dict):
            return ExecutionReport(status="REJECTED", error="unexpected CLOB response")

        order_id = response.get("orderID") or response.get("orderId")
        error_msg = str(response.get("errorMsg") or "")
        status_raw = str(response.get("status") or "").lower()
        if error_msg or response.get("success") is False or not order_id:
            killed = "fok" in error_msg.lower() or "fully filled" in error_msg.lower()
            return ExecutionReport(
                status="CANCELED" if killed else "REJECTED",
                exchange_order_id=order_id,
                error="FOK sell was not filled" if killed else "CLOB rejected the sell order",
                raw=response,
            )
        if status_raw == "unmatched":
            return ExecutionReport(
                status="CANCELED",
                exchange_order_id=order_id,
                error="sell not matched (FOK)",
                raw=response,
            )
        making = _num(response.get("makingAmount"))  # SELL: shares given up
        taking = _num(response.get("takingAmount"))  # SELL: collateral received
        if making > shares * 10 and taking > 0:
            making /= 1_000_000.0
            taking /= 1_000_000.0
        if status_raw == "matched" and making > 0 and taking > 0:
            price = round(taking / making, 6)
            size = round(making, 8)
            fee = self.estimate_fee(request.token_id, size, price)
            return ExecutionReport(
                status="FILLED" if making >= 0.98 * shares else "PARTIALLY_FILLED",
                filled_size=size,
                avg_price=price,
                fee=fee,
                submitted_price=price,
                exchange_order_id=order_id,
                fills=[FillPart(price=price, size=size, fee=fee, exchange_trade_id=None)],
                raw=response,
            )
        return ExecutionReport(
            status="SUBMITTED",
            exchange_order_id=order_id,
            error="exchange accepted the sell; fill details are pending reconciliation",
            raw=response,
        )

    def allow_new_orders(self) -> None:
        self._orders_allowed = True

    def block_new_orders(self) -> None:
        # Synchronous so a kill/stop request fences queued orders before it awaits.
        self._orders_allowed = False

    async def _place_buy_locked(self, request: OrderRequest) -> ExecutionReport:
        try:
            client = await self._get_client()
        except Exception as exc:
            return ExecutionReport(
                status="REJECTED",
                error=f"live client unavailable: {type(exc).__name__}",
            )

        try:
            report = await asyncio.to_thread(self._place_sync, client, request)
        except Exception as exc:
            logger.error("live order failed (%s)", type(exc).__name__)
            return ExecutionReport(
                status="SUBMITTED",
                error="order submission outcome is unknown; reconcile before retrying",
            )
        return report

    @staticmethod
    def _order_price(client, request: OrderRequest) -> float:
        """Worst acceptable price, clamped to what the market's tick size allows."""
        try:
            tick = float(client.get_tick_size(request.token_id))
        except Exception:
            tick = DEFAULT_TICK_SIZE
        return cap_price_to_tick(round(request.max_price, 6), tick)

    async def refresh_market_params(self, condition_id: str) -> None:
        """Re-read tick size / neg-risk after a ``tick_size_change`` event.

        The SDK caches tick sizes forever; without this a market whose tick gets finer
        (or coarser) mid-window keeps being priced against a stale tick.
        """
        client = self._client
        if client is None or not condition_id:
            return
        try:
            await asyncio.to_thread(client.get_clob_market_info, condition_id)
        except Exception as exc:
            logger.debug("market params refresh failed (%s)", type(exc).__name__)

    def _place_sync(self, client, request: OrderRequest) -> ExecutionReport:
        from py_clob_client_v2 import MarketOrderArgs, OrderType, Side

        balance = self._balance_hint() if self._balance_hint else None
        args = MarketOrderArgs(
            token_id=request.token_id if request.asset_version == 1 else None,
            amount=round(request.notional, 6),
            side=Side.BUY,
            # price is the worst acceptable price (cap), not a resting limit
            price=self._order_price(client, request),
            order_type=OrderType.FOK,
            user_usdc_balance=float(balance) if balance and balance > 0 else 0,
            position_id=request.token_id if request.asset_version == 2 else None,
        )
        try:
            # deferExec stays at the documented default (false). The SDK's post-submit
            # settlement-hash polling is disabled by the client subclass instead.
            response = client.create_and_post_market_order(args, None, OrderType.FOK, False)
        except Exception as exc:
            report = classify_post_exception(exc)
            if report.status == "SUBMITTED":
                # A transport exception can happen after Polymarket accepted the order.
                # Keep it active and fail closed so a retry cannot double-submit.
                logger.error("live order submit outcome unknown (%s)", type(exc).__name__)
            else:
                logger.warning(
                    "live order not accepted (%s): %s", type(exc).__name__, report.error
                )
            return report
        if not isinstance(response, dict):
            return ExecutionReport(status="REJECTED", error="unexpected CLOB response")

        order_id = response.get("orderID") or response.get("orderId")
        error_msg = str(response.get("errorMsg") or "")
        status_raw = str(response.get("status") or "").lower()
        success = response.get("success")

        if error_msg or success is False or not order_id:
            killed = "fok" in error_msg.lower() or "fully filled" in error_msg.lower()
            return ExecutionReport(
                status="CANCELED" if killed else "REJECTED",
                exchange_order_id=order_id,
                error="FOK order was not filled" if killed else "CLOB rejected the order",
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

        # Reconciliation runs on the maintenance path. Never poll or wait after
        # a successful post in the WebSocket-triggered trade path.
        return ExecutionReport(
            status="SUBMITTED",
            exchange_order_id=order_id,
            error="exchange accepted the order; fill details are pending reconciliation",
            raw=response,
        )

    def _report_from_amounts(
        self, order_id: str, making: float, taking: float, request: OrderRequest, raw: dict
    ) -> ExecutionReport:
        price = round(making / taking, 6)
        size = round(taking, 8)
        fee = self.estimate_fee(request.token_id, size, price)
        status = "FILLED" if making >= 0.98 * request.notional else "PARTIALLY_FILLED"
        return ExecutionReport(
            status=status,
            filled_size=size,
            avg_price=price,
            fee=fee,
            submitted_price=price,
            exchange_order_id=order_id,
            fills=[FillPart(price=price, size=size, fee=fee, exchange_trade_id=None)],
            raw=raw,
        )

    @staticmethod
    def _price_from_trades(client, order_id: str, trade_ids: list[str], size_hint: float) -> float | None:
        from py_clob_client_v2 import TradeParams

        try:
            trades = []
            for trade_id in trade_ids:
                trades.extend(client.get_trades(TradeParams(id=trade_id), only_first_page=True) or [])
        except Exception:
            return None
        cost = 0.0
        size = 0.0
        for trade in trades or []:
            if not isinstance(trade, dict) or trade.get("taker_order_id") != order_id:
                continue
            trade_size = _num(trade.get("size"))
            trade_price = _num(trade.get("price"))
            if trade_size > size_hint * 10:
                trade_size /= 1_000_000.0
            if trade_size > 0 and trade_price > 0:
                cost += trade_size * trade_price
                size += trade_size
        if size > 0 and abs(size - size_hint) <= max(0.01, 0.02 * size_hint):
            return round(cost / size, 6)
        return None

    async def fetch_fill_price(self, order_id: str, trade_ids: list[str], size_hint: float) -> float | None:
        if not trade_ids:
            return None
        try:
            client = await self._get_client()
        except Exception:
            return None
        return await asyncio.to_thread(
            self._price_from_trades, client, order_id, trade_ids, size_hint
        )

    async def fetch_order(self, order_id: str) -> dict | None:
        if not order_id:
            return None
        try:
            client = await self._get_client()
            return await asyncio.to_thread(client.get_order, order_id)
        except Exception as exc:
            logger.warning("live order fetch failed (%s)", type(exc).__name__)
            return None

    async def cancel_order(self, order_id: str) -> bool:
        if not order_id:
            return False
        try:
            client = await self._get_client()
        except Exception as exc:
            logger.warning("live order cancel client unavailable (%s)", type(exc).__name__)
            return False
        async with self._order_lock:
            try:
                from py_clob_client_v2 import OrderPayload

                response = await asyncio.to_thread(
                    client.cancel_order, OrderPayload(orderID=order_id)
                )
            except Exception as exc:
                logger.warning("live order cancel failed (%s)", type(exc).__name__)
                return False
        return isinstance(response, dict) and bool(response.get("canceled"))

    async def get_balance(self) -> float | None:
        try:
            client = await self._get_client()
        except Exception as exc:
            logger.warning("live balance unavailable (%s)", type(exc).__name__)
            return None
        try:
            return await asyncio.to_thread(self._balance_sync, client)
        except Exception as exc:
            logger.warning("live balance query failed (%s)", type(exc).__name__)
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

    def _refresh_collateral_sync(self, client) -> None:
        """Ask the CLOB to re-sync its cached collateral balance/allowance (best effort)."""
        from py_clob_client_v2 import AssetType, BalanceAllowanceParams

        try:
            client.update_balance_allowance(
                BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
            )
        except Exception:
            logger.debug("collateral balance refresh failed", exc_info=True)

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
            raise LiveTradingUnavailable(
                f"could not initialize CLOB client ({type(exc).__name__})"
            ) from exc
        await asyncio.to_thread(self._refresh_collateral_sync, client)
        try:
            balance = await asyncio.to_thread(self._balance_sync, client)
            allowance = await asyncio.to_thread(self._allowance_sync, client)
        except Exception as exc:
            raise LiveTradingUnavailable(
                f"balance query failed ({type(exc).__name__})"
            ) from exc
        if balance is None:
            raise LiveTradingUnavailable("could not read collateral (pUSD) balance")
        if balance <= 0:
            raise LiveTradingUnavailable(
                (
                "collateral (pUSD) balance is zero for this wallet "
                f"(POLYMARKET_SIGNATURE_TYPE={self._settings.polymarket_signature_type}); "
                "check the signature type / private key match your Polymarket account "
                "(run scripts\\check_wallet.py), or wrap USDC.e into pUSD via the Collateral Onramp"
            )
            )
        if allowance is None or allowance <= 0:
            raise LiveTradingUnavailable(
                "CLOB reports no pUSD collateral allowance; approve the Polymarket trading contracts before starting"
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
                positions: list[dict] = []
                cursor: str | None = None
                seen_cursors: set[str] = set()
                while True:
                    params = {
                        "user": funder,
                        "filter_type": "TOKENS",
                        "filter_amount": "0.01",
                        "limit": "1000",
                    }
                    if cursor:
                        params["cursor"] = cursor
                    response = await client.get(
                        "https://data-api.polymarket.com/v2/positions",
                        params=params,
                        headers={"User-Agent": self._settings.http_user_agent},
                    )
                    response.raise_for_status()
                    payload = response.json()
                    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
                        return None
                    positions.extend(item for item in payload["data"] if isinstance(item, dict))
                    pagination = payload.get("pagination") or {}
                    next_cursor = pagination.get("next_cursor") if isinstance(pagination, dict) else None
                    if not next_cursor or next_cursor in seen_cursors:
                        return positions
                    seen_cursors.add(next_cursor)
                    cursor = str(next_cursor)
        except Exception as exc:
            logger.warning("on-chain position fetch failed (%s)", type(exc).__name__)
            return None

    async def redeemable_condition_ids(self) -> set[str] | None:
        """Condition ids of wallet positions the Data API flags ``redeemable``."""
        positions = await self.fetch_onchain_positions()
        if positions is None:
            return None
        found: set[str] = set()
        for item in positions:
            cid = item.get("conditionId") or item.get("condition_id")
            size = _num(item.get("current_size") or item.get("size"))
            if cid and item.get("redeemable") and size > 0:
                found.add(str(cid).lower())
        return found

    async def wallet_resolutions(self) -> dict[str, str] | None:
        """Winners of resolved markets, read from the funder wallet's Data API positions.

        Once a market is resolved on-chain, the wallet's tokens are flagged ``redeemable``
        and priced 1 (winner) or 0 (loser). That happens before Gamma publishes its own
        outcome, so it is an independent source for settling live positions. Returns
        ``{condition_id_lower: "UP" | "DOWN"}``; ``None`` when the wallet cannot be read.
        """
        positions = await self.fetch_onchain_positions()
        if positions is None:
            return None
        found: dict[str, str] = {}
        for item in positions:
            if not item.get("redeemable"):
                continue
            cid = str(item.get("conditionId") or item.get("condition_id") or "").lower()
            outcome = str(item.get("outcome") or "").strip().upper()
            if not cid or outcome not in {"UP", "DOWN"}:
                continue
            price = None
            for key in ("curPrice", "cur_price", "current_price", "currentPrice", "price"):
                if item.get(key) is not None:
                    try:
                        price = float(item[key])
                    except (TypeError, ValueError):
                        price = None
                    break
            if price is None:
                continue
            if price >= 0.99:
                found[cid] = outcome
            elif price <= 0.01:
                found.setdefault(cid, "DOWN" if outcome == "UP" else "UP")
        return found

    async def redeem_market(self, condition_id: str) -> dict:
        """Redeem winning tokens of a resolved market into pUSD.

        Uses the official ``polymarket-client`` SDK ``redeem_positions`` (gasless via a
        Relayer API key, otherwise an EOA transaction). Redemption has no amount: it
        redeems the wallet's balances for both outcomes; losing tokens pay 0.
        Raises LiveTradingUnavailable when it cannot be attempted.
        """
        self._validate_gates()
        if not self._settings.live_auto_redeem:
            raise LiveTradingUnavailable("LIVE_AUTO_REDEEM is false")
        redeemable = await self.redeemable_condition_ids()
        if redeemable is not None and condition_id.lower() not in redeemable:
            return {"status": "not_redeemable_yet"}
        try:
            from polymarket import AsyncSecureClient, RelayerApiKey
        except ImportError as exc:
            raise LiveTradingUnavailable(
                "auto-redeem needs the official SDK: pip install polymarket-client"
            ) from exc

        relayer = None
        if self._settings.polymarket_relayer_api_key and self._settings.polymarket_relayer_api_key_address:
            relayer = RelayerApiKey(
                key=self._settings.polymarket_relayer_api_key,
                address=self._settings.polymarket_relayer_api_key_address,
            )
        client = await AsyncSecureClient.create(
            private_key=self._settings.polymarket_private_key,
            wallet=self._settings.polymarket_funder_address or None,
            api_key=relayer,
        )
        async with client:
            handle = await client.redeem_positions(
                condition_id=condition_id, metadata="polybot final-60s auto-redeem"
            )
            outcome = await handle.wait()
        tx_hash = getattr(outcome, "transaction_hash", None)
        return {"status": "redeemed", "transaction_hash": str(tx_hash) if tx_hash else None}

    async def close(self) -> None:
        self._client = None
