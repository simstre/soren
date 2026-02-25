"""Adaptive multi-timeframe RSI divergence tracker.

Tracks which higher timeframes produce the most effective divergence signals
and adjusts weights over time based on trade outcomes.
"""

from dataclasses import dataclass, field

import structlog

logger = structlog.get_logger()

# Use the same timeframe for divergence discovery as the trading cadence.
CANDIDATE_TIMEFRAMES: dict[str, list[str]] = {
    "1m": ["1m"],
    "5m": ["5m"],
    "15m": ["15m"],
    "1h": ["1h"],
    "1D": ["1D"],
}


@dataclass
class DivergenceSignal:
    """A divergence detected on a specific timeframe."""
    timeframe: str
    direction: str       # "bullish" or "bearish"
    rsi_current: float
    description: str
    weight: float        # current effectiveness weight for this timeframe
    rsi_spread: float = 0.0  # absolute RSI spread between divergence points


@dataclass
class TimeframeScore:
    """Running effectiveness stats for one timeframe."""
    timeframe: str
    total_signals: int = 0
    profitable_signals: int = 0
    weight: float = 1.0

    @property
    def win_rate(self) -> float:
        if self.total_signals == 0:
            return 0.0
        return self.profitable_signals / self.total_signals


class DivergenceTracker:
    """Learns which divergence timeframes are most effective."""

    MIN_WEIGHT = 0.1   # Floor — no TF is ever fully excluded
    WARMUP = 3          # Minimum signals before win_rate affects weight

    def __init__(self, candidate_timeframes: list[str]) -> None:
        self._scores: dict[str, TimeframeScore] = {
            tf: TimeframeScore(timeframe=tf) for tf in candidate_timeframes
        }

    def get_weights(self) -> dict[str, float]:
        return {tf: s.weight for tf, s in self._scores.items()}

    def record_outcome(self, timeframe: str, profitable: bool) -> None:
        """Record whether a trade triggered by this TF's divergence was profitable."""
        score = self._scores.get(timeframe)
        if not score:
            return

        score.total_signals += 1
        if profitable:
            score.profitable_signals += 1

        self._recalculate_weights()

        logger.debug(
            "divergence_outcome",
            timeframe=timeframe,
            profitable=profitable,
            win_rate=round(score.win_rate, 2),
            new_weight=round(score.weight, 3),
            total=score.total_signals,
        )

    def _recalculate_weights(self) -> None:
        """Recalculate all weights based on current stats."""
        for score in self._scores.values():
            if score.total_signals < self.WARMUP:
                # Not enough data yet — keep default weight
                score.weight = 1.0
                continue

            # Win rate component (70% of weight)
            wr = score.win_rate

            # Signal volume component (30% of weight) — normalized
            # More signals = more data = more trustworthy
            max_signals = max(s.total_signals for s in self._scores.values())
            volume_factor = score.total_signals / max_signals if max_signals > 0 else 0.5

            raw_weight = (wr * 0.7) + (volume_factor * 0.3)
            score.weight = max(self.MIN_WEIGHT, raw_weight)

    def get_best_divergence(
        self,
        divergences: dict[str, DivergenceSignal | None],
    ) -> DivergenceSignal | None:
        """Pick the best divergence from all timeframes, weighted by effectiveness.

        If multiple timeframes agree on direction, the best-weighted one wins
        and its confidence is boosted.
        """
        valid = {tf: d for tf, d in divergences.items() if d is not None}
        if not valid:
            return None

        # Count how many TFs agree per direction
        direction_counts: dict[str, int] = {}
        for d in valid.values():
            direction_counts[d.direction] = direction_counts.get(d.direction, 0) + 1

        # Pick the divergence with the highest weight
        best_tf = max(valid, key=lambda tf: valid[tf].weight)
        best = valid[best_tf]

        # If multiple TFs agree, boost the weight
        agreement = direction_counts.get(best.direction, 1)
        if agreement > 1:
            best = DivergenceSignal(
                timeframe=best.timeframe,
                direction=best.direction,
                rsi_current=best.rsi_current,
                description=best.description + f" [{agreement} TFs agree]",
                weight=min(1.0, best.weight * (1 + 0.2 * (agreement - 1))),
                rsi_spread=best.rsi_spread,
            )

        return best

    def reset(self) -> None:
        """Clear all history (called on new backtest)."""
        for score in self._scores.values():
            score.total_signals = 0
            score.profitable_signals = 0
            score.weight = 1.0
