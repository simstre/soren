"""Replay broker adapter — simulates fills using historical candle prices."""

import asyncio
from datetime import datetime, timezone
from typing import Callable, Awaitable

import structlog

from app.broker.base import BrokerAdapter
from app.schemas.position import PositionRead

logger = structlog.get_logger()


class ReplayBrokerAdapter(BrokerAdapter):
    """Broker adapter for replay/backtest mode.

    Supports both long and short positions.
    - Long: BUY to open, position.quantity > 0
    - Short: SELL to open, position.quantity < 0 (stored as negative)

    Uses historical candle prices for order fills and stop/target checks.
    """

    def __init__(self, starting_balance: float = 100_000.0):
        self._connected = False
        self._starting_balance = starting_balance
        self._balance = starting_balance
        self._positions: dict[str, PositionRead] = {}
        self._open_orders: dict[str, dict] = {}
        self._next_order_id = 1
        self._current_prices: dict[str, float] = {}
        self._closed_trades: list[dict] = []

    async def connect(self) -> None:
        self._connected = True
        logger.info("replay_broker_connected")

    async def disconnect(self) -> None:
        self._connected = False
        logger.info("replay_broker_disconnected")

    async def is_connected(self) -> bool:
        return self._connected

    def set_current_price(self, symbol: str, price: float) -> None:
        self._current_prices[symbol] = price

    def reset(self) -> None:
        self._balance = self._starting_balance
        self._positions.clear()
        self._open_orders.clear()
        self._next_order_id = 1
        self._current_prices.clear()
        self._closed_trades.clear()

    def has_position(self, symbol: str) -> bool:
        return symbol in self._positions

    def get_position_side(self, symbol: str) -> str | None:
        """Return 'BUY' for long, 'SELL' for short, None if no position."""
        if symbol not in self._positions:
            return None
        pos = self._positions[symbol]
        return "BUY" if pos.quantity > 0 else "SELL"

    def close_position(self, symbol: str, price: float, exit_reason: str = "position_flip") -> dict | None:
        """Close an existing position at the given price. Returns trade dict or None."""
        if symbol not in self._positions:
            return None

        pos = self._positions[symbol]
        qty = abs(pos.quantity)
        side = "BUY" if pos.quantity > 0 else "SELL"

        if side == "BUY":
            # Closing a long: sell at price
            pnl = (price - pos.avg_cost) * qty
            self._balance += price * qty
        else:
            # Closing a short: buy to cover at price
            pnl = (pos.avg_cost - price) * qty
            self._balance += (2 * pos.avg_cost - price) * qty  # Return margin + pnl

        pnl_pct = (pnl / (pos.avg_cost * qty)) * 100 if pos.avg_cost > 0 else 0

        del self._positions[symbol]

        # Remove associated open orders
        orders_to_remove = [
            oid for oid, o in self._open_orders.items() if o["symbol"] == symbol
        ]
        order_id = orders_to_remove[0] if orders_to_remove else None
        for oid in orders_to_remove:
            self._open_orders.pop(oid, None)

        trade = {
            "order_id": order_id,
            "symbol": symbol,
            "side": side,
            "quantity": qty,
            "entry_price": pos.avg_cost,
            "exit_price": round(price, 2),
            "pnl": round(pnl, 2),
            "pnl_pct": round(pnl_pct, 2),
            "exit_reason": exit_reason,
        }
        self._closed_trades.append(trade)
        return trade

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

        fill_price = limit_price or self._current_prices.get(symbol, 0.0)

        if side == "BUY":
            # Open long position
            self._positions[symbol] = PositionRead(
                symbol=symbol,
                quantity=quantity,
                avg_cost=fill_price,
                current_price=fill_price,
                unrealized_pnl=0.0,
                unrealized_pnl_pct=0.0,
                market_value=round(quantity * fill_price, 2),
            )
            self._balance -= fill_price * quantity
        elif side == "SELL":
            # Open short position (quantity stored as negative)
            self._positions[symbol] = PositionRead(
                symbol=symbol,
                quantity=-quantity,
                avg_cost=fill_price,
                current_price=fill_price,
                unrealized_pnl=0.0,
                unrealized_pnl_pct=0.0,
                market_value=round(quantity * fill_price, 2),
            )
            self._balance -= fill_price * quantity  # Margin for short

        # Track open order for stop/target checking
        self._open_orders[order_id] = {
            "symbol": symbol,
            "side": side,
            "quantity": quantity,
            "entry_price": fill_price,
            "stop_loss": stop_loss,
            "profit_target": profit_target,
            "status": "FILLED",
        }

        logger.info(
            "replay_order_filled",
            order_id=order_id,
            symbol=symbol,
            side=side,
            qty=quantity,
            price=fill_price,
        )
        return order_id

    def check_stops_and_targets(
        self, symbol: str, bar_high: float, bar_low: float, bar_close: float
    ) -> list[dict]:
        """Check if any open positions hit stop loss or profit target."""
        closed = []
        orders_to_remove = []

        for order_id, order in self._open_orders.items():
            if order["symbol"] != symbol:
                continue
            if symbol not in self._positions:
                orders_to_remove.append(order_id)
                continue

            pos = self._positions[symbol]
            stop = order.get("stop_loss")
            target = order.get("profit_target")

            exit_price = None
            exit_reason = None

            if order["side"] == "BUY":
                # Long: stop hit if bar_low <= stop, target hit if bar_high >= target
                if stop and bar_low <= stop:
                    exit_price = stop
                    exit_reason = "stop_loss"
                elif target and bar_high >= target:
                    exit_price = target
                    exit_reason = "profit_target"
            elif order["side"] == "SELL":
                # Short: stop hit if bar_high >= stop, target hit if bar_low <= target
                if stop and bar_high >= stop:
                    exit_price = stop
                    exit_reason = "stop_loss"
                elif target and bar_low <= target:
                    exit_price = target
                    exit_reason = "profit_target"

            if exit_price is not None:
                qty = order["quantity"]
                entry = order["entry_price"]

                if order["side"] == "BUY":
                    pnl = (exit_price - entry) * qty
                    self._balance += exit_price * qty
                else:
                    pnl = (entry - exit_price) * qty
                    self._balance += (2 * entry - exit_price) * qty

                pnl_pct = (pnl / (entry * qty)) * 100 if entry > 0 else 0

                trade = {
                    "order_id": order_id,
                    "symbol": symbol,
                    "side": order["side"],
                    "quantity": qty,
                    "entry_price": entry,
                    "exit_price": round(exit_price, 2),
                    "pnl": round(pnl, 2),
                    "pnl_pct": round(pnl_pct, 2),
                    "exit_reason": exit_reason,
                }
                closed.append(trade)
                orders_to_remove.append(order_id)

                # Remove position
                if symbol in self._positions:
                    del self._positions[symbol]

                self._closed_trades.append(trade)

        for oid in orders_to_remove:
            self._open_orders.pop(oid, None)

        return closed

    def update_position_prices(self, symbol: str, price: float) -> None:
        """Update position current price for unrealized P&L tracking."""
        if symbol in self._positions:
            pos = self._positions[symbol]
            qty = abs(pos.quantity)
            if pos.quantity > 0:
                # Long
                pnl = (price - pos.avg_cost) * qty
            else:
                # Short
                pnl = (pos.avg_cost - price) * qty
            pnl_pct = (pnl / (pos.avg_cost * qty)) * 100 if pos.avg_cost > 0 else 0
            self._positions[symbol] = PositionRead(
                symbol=symbol,
                quantity=pos.quantity,
                avg_cost=pos.avg_cost,
                current_price=price,
                unrealized_pnl=round(pnl, 2),
                unrealized_pnl_pct=round(pnl_pct, 2),
                market_value=round(qty * price, 2),
            )

    async def cancel_order(self, order_id: str) -> None:
        self._open_orders.pop(order_id, None)

    async def get_positions(self) -> list[PositionRead]:
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
        # Not used in replay mode — bars are pushed by the replay engine
        pass

    async def unsubscribe_bars(self, symbol: str) -> None:
        pass

    async def get_current_price(self, symbol: str) -> float:
        return self._current_prices.get(symbol, 0.0)

    def get_closed_trades(self) -> list[dict]:
        return list(self._closed_trades)

    def get_starting_balance(self) -> float:
        return self._starting_balance
