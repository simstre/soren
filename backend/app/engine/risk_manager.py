"""Risk management: position sizing, exposure limits, and trade validation."""

from dataclasses import dataclass

import structlog

from app.engine.signal_generator import TradingSignal
from app.config import settings

logger = structlog.get_logger()


@dataclass
class RiskDecision:
    approved: bool
    quantity: int
    reason: str
    adjusted_stop: float | None = None
    adjusted_target: float | None = None


def validate_signal(
    signal: TradingSignal,
    account_balance: float,
    buying_power: float,
    open_position_symbols: list[str],
    total_exposure: float,
    position_size_pct: float | None = None,
    max_exposure_pct: float | None = None,
) -> RiskDecision:
    """
    Validate a trading signal against risk management rules.

    Args:
        position_size_pct: If set, use a fixed % of account for position sizing
                           (e.g. 0.05 = 5%) instead of risk-based sizing.
        max_exposure_pct: Override for max total exposure (e.g. 1.0 = 100%).
                          If None, uses settings.max_exposure_pct.

    Rules:
    1. No duplicate positions in the same symbol
    2. Max position size (default 5% of account)
    3. Max total exposure (default 25% of account)
    4. Risk per trade (stop distance * qty <= 1% of account)
    5. Minimum risk:reward ratio of 1.5:1
    """
    effective_exposure_pct = max_exposure_pct if max_exposure_pct is not None else settings.max_exposure_pct

    # Rule 1: No duplicate positions
    if signal.symbol in open_position_symbols:
        return RiskDecision(
            approved=False,
            quantity=0,
            reason=f"Already have an open position in {signal.symbol}",
        )

    # Fixed position sizing mode (e.g. replay/backtest/live with explicit size)
    if position_size_pct is not None:
        # Exposure cap check
        remaining_exposure = (account_balance * effective_exposure_pct) - total_exposure
        if remaining_exposure <= 0:
            return RiskDecision(
                approved=False,
                quantity=0,
                reason="Maximum total exposure reached",
            )

        position_value = account_balance * position_size_pct
        # Cap by remaining exposure and buying power
        position_value = min(position_value, remaining_exposure, buying_power)

        if position_value <= 0 or signal.entry_price <= 0:
            return RiskDecision(
                approved=False,
                quantity=0,
                reason="Insufficient buying power",
            )

        quantity = int(position_value / signal.entry_price)
        if quantity <= 0:
            return RiskDecision(
                approved=False,
                quantity=0,
                reason="Calculated position size is zero",
            )

        logger.info(
            "risk_approved_fixed_size",
            symbol=signal.symbol,
            quantity=quantity,
            position_pct=position_size_pct,
            value=round(quantity * signal.entry_price, 2),
        )
        return RiskDecision(
            approved=True,
            quantity=quantity,
            reason="Signal approved",
        )

    # Rule 2 & 3: Exposure limits
    max_position_value = account_balance * settings.max_position_pct
    remaining_exposure = (account_balance * effective_exposure_pct) - total_exposure

    if remaining_exposure <= 0:
        return RiskDecision(
            approved=False,
            quantity=0,
            reason="Maximum total exposure reached",
        )

    position_limit = min(max_position_value, remaining_exposure, buying_power)

    if position_limit <= 0:
        return RiskDecision(
            approved=False,
            quantity=0,
            reason="Insufficient buying power",
        )

    # Rule 4: Position sizing based on risk per trade
    stop_distance = abs(signal.entry_price - signal.stop_loss)
    if stop_distance <= 0:
        return RiskDecision(
            approved=False,
            quantity=0,
            reason="Invalid stop loss (no distance from entry)",
        )

    max_risk = account_balance * settings.risk_per_trade_pct
    risk_based_qty = int(max_risk / stop_distance)

    # Also limit by position value
    value_based_qty = int(position_limit / signal.entry_price)

    quantity = min(risk_based_qty, value_based_qty)

    if quantity <= 0:
        return RiskDecision(
            approved=False,
            quantity=0,
            reason="Calculated position size is zero (stop too wide or insufficient capital)",
        )

    # Rule 5: Risk:Reward ratio
    target_distance = abs(signal.profit_target - signal.entry_price)
    if stop_distance > 0:
        rr_ratio = target_distance / stop_distance
        if rr_ratio < 1.5:
            logger.info(
                "poor_risk_reward",
                symbol=signal.symbol,
                rr=round(rr_ratio, 2),
            )
            return RiskDecision(
                approved=False,
                quantity=0,
                reason=f"Risk:reward ratio {rr_ratio:.2f} below minimum 1.5",
            )

    trade_risk = stop_distance * quantity
    logger.info(
        "risk_approved",
        symbol=signal.symbol,
        quantity=quantity,
        risk=round(trade_risk, 2),
        rr_ratio=round(target_distance / stop_distance, 2),
    )

    return RiskDecision(
        approved=True,
        quantity=quantity,
        reason="Signal approved",
    )
