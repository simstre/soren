from abc import ABC, abstractmethod
from typing import Callable, Awaitable

from app.schemas.position import PositionRead


class BrokerAdapter(ABC):
    """Abstract base class for broker integrations."""

    @abstractmethod
    async def connect(self) -> None:
        """Connect to the broker."""
        ...

    @abstractmethod
    async def disconnect(self) -> None:
        """Disconnect from the broker."""
        ...

    @abstractmethod
    async def is_connected(self) -> bool:
        """Check if connected to the broker."""
        ...

    @abstractmethod
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
        """Place an order. Returns the order ID."""
        ...

    @abstractmethod
    async def cancel_order(self, order_id: str) -> None:
        """Cancel an open order."""
        ...

    @abstractmethod
    async def get_positions(self) -> list[PositionRead]:
        """Get current open positions."""
        ...

    @abstractmethod
    async def get_account_balance(self) -> float:
        """Get current account balance (net liquidation value)."""
        ...

    @abstractmethod
    async def get_buying_power(self) -> float:
        """Get available buying power."""
        ...

    @abstractmethod
    async def subscribe_bars(
        self,
        symbol: str,
        callback: Callable[[dict], Awaitable[None]],
    ) -> None:
        """Subscribe to real-time bar data for a symbol."""
        ...

    @abstractmethod
    async def unsubscribe_bars(self, symbol: str) -> None:
        """Unsubscribe from real-time bar data."""
        ...

    @abstractmethod
    async def get_current_price(self, symbol: str) -> float:
        """Get the current market price for a symbol."""
        ...

    async def close_position(self, symbol: str) -> str | None:
        """Close a single open position by symbol. Returns order ID or None."""
        positions = await self.get_positions()
        for pos in positions:
            if pos.symbol == symbol:
                price = await self.get_current_price(symbol)
                side = "SELL" if pos.quantity > 0 else "BUY"
                oid = await self.place_order(
                    symbol=symbol,
                    side=side,
                    quantity=abs(pos.quantity),
                    order_type="MARKET",
                    limit_price=price,
                )
                return oid
        return None

    async def close_all_positions(self) -> list[str]:
        """Close all open positions. Returns list of order IDs."""
        positions = await self.get_positions()
        order_ids: list[str] = []
        for pos in positions:
            price = await self.get_current_price(pos.symbol)
            oid = await self.place_order(
                symbol=pos.symbol,
                side="SELL",
                quantity=pos.quantity,
                order_type="MARKET",
                limit_price=price,
            )
            order_ids.append(oid)
        return order_ids
