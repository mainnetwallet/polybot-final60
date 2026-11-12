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

    def _place_sync(self, client, request: OrderRequest) -> ExecutionReport:
        from py_clob_client_v2 import MarketOrderArgs, OrderType, Side

        balance = self._balance_hint() if self._balance_hint else None
        args = MarketOrderArgs(
            token_id=request.token_id if request.asset_version == 1 else None,
            amount=round(request.notional, 6),
            side=Side.BUY,
            # price is the worst acceptable price (cap), not a resting limit
            price=round(request.max_price, 6),
            order_type=OrderType.FOK,
            user_usdc_balance=float(balance) if balance and balance > 0 else 0,
            position_id=request.token_id if request.asset_version == 2 else None,
        )
        try:
            # defer_exec skips the SDK's post-submit transaction/trade polling.
            response = client.create_and_post_market_order(args, None, OrderType.FOK, True)
        except Exception as exc:
            # A transport exception can happen after Polymarket accepted the order.
            # Keep it active and fail closed so a retry cannot double-submit.
            logger.error("live order submit outcome unknown (%s)", type(exc).__name__)
            return ExecutionReport(
                status="SUBMITTED",
                error="order submission outcome is unknown; reconcile before retrying",
            )
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
                "collateral (pUSD) balance is zero; wrap USDC.e into pUSD via the Collateral Onramp"
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
