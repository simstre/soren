import asyncio
from datetime import datetime, timezone
from typing import Callable, Awaitable

import structlog

from app.broker.base import BrokerAdapter
from app.schemas.position import PositionRead
from app.config import settings

logger = structlog.get_logger()


class IBAdapter(BrokerAdapter):
    """Interactive Brokers adapter using ib_async."""

    def __init__(self):
        self._ib = None
        self._subscriptions: dict[str, object] = {}
        self._account: str = ""
        self._currency: str = "USD"  # detected from account on connect
        self._initial_balance: float = 0.0  # captured on connect

    async def connect(self) -> None:
        try:
            from ib_async import IB
            self._ib = IB()
            await self._ib.connectAsync(
                host=settings.ib_host,
                port=settings.ib_port,
                clientId=settings.ib_client_id,
            )
            accounts = self._ib.managedAccounts()
            self._account = accounts[0] if accounts else ""
            # Detect account base currency from populated account values
            for av in self._ib.accountValues():
                if av.tag == "NetLiquidation" and av.currency not in ("", "BASE"):
                    self._currency = av.currency
                    break
            # Capture initial balance for P&L calculation
            self._initial_balance = await self.get_account_balance()
            logger.info(
                "ib_connected", host=settings.ib_host, port=settings.ib_port,
                account=self._account, currency=self._currency,
                balance=self._initial_balance,
            )
        except Exception as e:
            logger.error("ib_connection_failed", error=str(e))
            raise

    async def disconnect(self) -> None:
        if self._ib and self._ib.isConnected():
            self._ib.disconnect()
            logger.info("ib_disconnected")

    async def is_connected(self) -> bool:
        return self._ib is not None and self._ib.isConnected()

    def get_starting_balance(self) -> float | None:
        return self._initial_balance if self._initial_balance > 0 else None

    async def place_order(
        self,
        symbol: str,
        side: str,
        quantity: int,
        order_type: str = "LIMIT",
        limit_price: float | None = None,
        stop_loss: float | None = None,
        profit_target: float | None = None,
    ) -> str:
        from ib_async import Stock, LimitOrder, MarketOrder, Order

        contract = Stock(symbol, "SMART", "USD")
        await self._ib.qualifyContractsAsync(contract)

        action = "BUY" if side == "BUY" else "SELL"

        if order_type == "LIMIT" and limit_price:
            order = LimitOrder(action, quantity, limit_price)
        else:
            order = MarketOrder(action, quantity)

        trade = self._ib.placeOrder(contract, order)
        order_id = str(trade.order.orderId)

        # Place bracket orders for stop loss and profit target
        if stop_loss and profit_target:
            reverse_action = "SELL" if action == "BUY" else "BUY"
            sl_order = Order(
                action=reverse_action,
                totalQuantity=quantity,
                orderType="STP",
                auxPrice=stop_loss,
                parentId=trade.order.orderId,
                transmit=False,
            )
            tp_order = Order(
                action=reverse_action,
                totalQuantity=quantity,
                orderType="LMT",
                lmtPrice=profit_target,
                parentId=trade.order.orderId,
                transmit=True,
            )
            self._ib.placeOrder(contract, sl_order)
            self._ib.placeOrder(contract, tp_order)

        logger.info("ib_order_placed", order_id=order_id, symbol=symbol, side=side, qty=quantity)
        return order_id

    async def close_position(self, symbol: str) -> str | None:
        """Close a single position, cancelling bracket orders (SL/TP) first."""
        # Cancel all open orders for this symbol (bracket SL/TP children)
        for trade in self._ib.openTrades():
            if trade.contract.symbol == symbol:
                self._ib.cancelOrder(trade.order)

        # Now close via market order
        positions = await self.get_positions()
        for pos in positions:
            if pos.symbol == symbol:
                from ib_async import Stock, MarketOrder
                contract = Stock(symbol, "SMART", "USD")
                await self._ib.qualifyContractsAsync(contract)
                action = "SELL" if pos.quantity > 0 else "BUY"
                order = MarketOrder(action, abs(pos.quantity))
                trade = self._ib.placeOrder(contract, order)
                order_id = str(trade.order.orderId)
                logger.info("ib_position_closed", symbol=symbol, side=action, qty=abs(pos.quantity), order_id=order_id)
                return order_id
        return None

    async def cancel_order(self, order_id: str) -> None:
        for trade in self._ib.openTrades():
            if str(trade.order.orderId) == order_id:
                self._ib.cancelOrder(trade.order)
                break

    async def get_positions(self) -> list[PositionRead]:
        positions = self._ib.positions()
        result = []
        for pos in positions:
            if pos.position == 0:
                continue
            contract = pos.contract
            # Get current market price
            tickers = await self._ib.reqTickersAsync(contract)
            current_price = tickers[0].marketPrice() if tickers else pos.avgCost

            qty = int(abs(pos.position))
            avg_cost = pos.avgCost
            pnl = round((current_price - avg_cost) * qty, 2)
            pnl_pct = round((current_price - avg_cost) / avg_cost * 100, 2) if avg_cost else 0.0

            result.append(
                PositionRead(
                    symbol=contract.symbol,
                    quantity=qty,
                    avg_cost=round(avg_cost, 2),
                    current_price=round(current_price, 2),
                    unrealized_pnl=pnl,
                    unrealized_pnl_pct=pnl_pct,
                    market_value=round(qty * current_price, 2),
                )
            )
        return result

    async def get_account_balance(self) -> float:
        for av in self._ib.accountValues():
            if av.tag == "NetLiquidation" and av.currency == self._currency:
                return float(av.value)
        return 0.0

    async def get_buying_power(self) -> float:
        for av in self._ib.accountValues():
            if av.tag == "BuyingPower" and av.currency == self._currency:
                return float(av.value)
        return 0.0

    async def subscribe_bars(
        self,
        symbol: str,
        callback: Callable[[dict], Awaitable[None]],
    ) -> None:
        from ib_async import Stock

        contract = Stock(symbol, "SMART", "USD")
        await self._ib.qualifyContractsAsync(contract)

        bars = self._ib.reqRealTimeBars(contract, 5, "TRADES", False)

        def on_bar_update(bars, has_new_bar):
            if has_new_bar and len(bars) > 0:
                bar = bars[-1]
                bar_data = {
                    "symbol": symbol,
                    "timestamp": bar.time.isoformat(),
                    "open": bar.open_,
                    "high": bar.high,
                    "low": bar.low,
                    "close": bar.close,
                    "volume": bar.volume,
                }
                asyncio.create_task(callback(bar_data))

        bars.updateEvent += on_bar_update
        self._subscriptions[symbol] = bars
        logger.info("ib_bars_subscribed", symbol=symbol)

    async def unsubscribe_bars(self, symbol: str) -> None:
        if symbol in self._subscriptions:
            self._ib.cancelRealTimeBars(self._subscriptions[symbol])
            del self._subscriptions[symbol]

    async def get_current_price(self, symbol: str) -> float:
        from ib_async import Stock

        contract = Stock(symbol, "SMART", "USD")
        await self._ib.qualifyContractsAsync(contract)
        tickers = await self._ib.reqTickersAsync(contract)
        if tickers:
            return tickers[0].marketPrice()
        return 0.0
