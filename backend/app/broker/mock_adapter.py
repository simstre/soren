import asyncio
import random
from datetime import datetime, timezone
from typing import Callable, Awaitable

import structlog

from app.broker.base import BrokerAdapter
from app.schemas.position import PositionRead

logger = structlog.get_logger()


class MockAdapter(BrokerAdapter):
    """Mock broker adapter for development and testing."""

    def __init__(self):
        self._connected = False
        self._balance = 100000.0
        self._positions: dict[str, PositionRead] = {}
        self._orders: dict[str, dict] = {}
        self._subscriptions: dict[str, asyncio.Task] = {}
        self._next_order_id = 1
        self._prices: dict[str, float] = {
            "AAPL": 264.58,
            "MSFT": 415.20,
            "NVDA": 890.00,
            "TSLA": 248.75,
            "AMD": 178.20,
        }

    async def connect(self) -> None:
        self._connected = True
        logger.info("mock_broker_connected")

    async def disconnect(self) -> None:
        for task in self._subscriptions.values():
            task.cancel()
        self._subscriptions.clear()
        self._connected = False
        logger.info("mock_broker_disconnected")

    async def is_connected(self) -> bool:
        return self._connected

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
        order_id = str(self._next_order_id)
        self._next_order_id += 1

        price = limit_price or self._prices.get(symbol, 100.0)
        self._orders[order_id] = {
            "symbol": symbol,
            "side": side,
            "quantity": quantity,
            "price": price,
            "stop_loss": stop_loss,
            "profit_target": profit_target,
            "status": "FILLED",
        }

        # Simulate position
        if side == "BUY":
            if symbol in self._positions:
                pos = self._positions[symbol]
                total_qty = pos.quantity + quantity
                avg = (pos.avg_cost * pos.quantity + price * quantity) / total_qty
                self._positions[symbol] = PositionRead(
                    symbol=symbol,
                    quantity=total_qty,
                    avg_cost=round(avg, 2),
                    current_price=price,
                    unrealized_pnl=0.0,
                    unrealized_pnl_pct=0.0,
                    market_value=round(total_qty * price, 2),
                )
            else:
                self._positions[symbol] = PositionRead(
                    symbol=symbol,
                    quantity=quantity,
                    avg_cost=price,
                    current_price=price,
                    unrealized_pnl=0.0,
                    unrealized_pnl_pct=0.0,
                    market_value=round(quantity * price, 2),
                )
            self._balance -= price * quantity
        elif side == "SELL":
            if symbol in self._positions:
                pos = self._positions[symbol]
                remaining = pos.quantity - quantity
                if remaining <= 0:
                    pnl = (price - pos.avg_cost) * pos.quantity
                    self._balance += price * pos.quantity
                    del self._positions[symbol]
                else:
                    pnl = (price - pos.avg_cost) * quantity
                    self._balance += price * quantity
                    self._positions[symbol] = PositionRead(
                        symbol=symbol,
                        quantity=remaining,
                        avg_cost=pos.avg_cost,
                        current_price=price,
                        unrealized_pnl=round((price - pos.avg_cost) * remaining, 2),
                        unrealized_pnl_pct=round((price - pos.avg_cost) / pos.avg_cost * 100, 2),
                        market_value=round(remaining * price, 2),
                    )

        logger.info("mock_order_placed", order_id=order_id, symbol=symbol, side=side, qty=quantity, price=price)
        return order_id

    async def cancel_order(self, order_id: str) -> None:
        if order_id in self._orders:
            self._orders[order_id]["status"] = "CANCELLED"

    async def get_positions(self) -> list[PositionRead]:
        # Update current prices with small random movement
        for symbol, pos in self._positions.items():
            new_price = round(pos.current_price * (1 + random.uniform(-0.002, 0.002)), 2)
            self._prices[symbol] = new_price
            pnl = round((new_price - pos.avg_cost) * pos.quantity, 2)
            pnl_pct = round((new_price - pos.avg_cost) / pos.avg_cost * 100, 2)
            self._positions[symbol] = PositionRead(
                symbol=symbol,
                quantity=pos.quantity,
                avg_cost=pos.avg_cost,
                current_price=new_price,
                unrealized_pnl=pnl,
                unrealized_pnl_pct=pnl_pct,
                market_value=round(pos.quantity * new_price, 2),
            )
        return list(self._positions.values())

    async def get_account_balance(self) -> float:
        positions_value = sum(p.market_value for p in self._positions.values())
        return round(self._balance + positions_value, 2)

    async def get_buying_power(self) -> float:
        return round(self._balance, 2)

    async def subscribe_bars(
        self,
        symbol: str,
        callback: Callable[[dict], Awaitable[None]],
    ) -> None:
        if symbol in self._subscriptions:
            return

        async def _stream():
            price = self._prices.get(symbol, 100.0)
            while True:
                await asyncio.sleep(5)
                change = random.uniform(-0.5, 0.5)
                o = price
                c = round(price + change, 2)
                h = round(max(o, c) + random.uniform(0, 0.3), 2)
                l = round(min(o, c) - random.uniform(0, 0.3), 2)
                price = c
                self._prices[symbol] = price

                bar = {
                    "symbol": symbol,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "open": o,
                    "high": h,
                    "low": l,
                    "close": c,
                    "volume": random.randint(1000, 50000),
                }
                await callback(bar)

        self._subscriptions[symbol] = asyncio.create_task(_stream())
        logger.info("mock_bars_subscribed", symbol=symbol)

    async def unsubscribe_bars(self, symbol: str) -> None:
        if symbol in self._subscriptions:
            self._subscriptions[symbol].cancel()
            del self._subscriptions[symbol]

    async def get_current_price(self, symbol: str) -> float:
        return self._prices.get(symbol, 100.0)
