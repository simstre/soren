"""Engine state management."""

from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class EngineState:
    running: bool = False
    started_at: datetime | None = None
    active_tickers: list[str] = field(default_factory=list)
    timeframe: str = "5m"
    strategy: str = "refined_rsi"
    scan_tactic: str = "market_scan"
    error: str | None = None
    last_rescan_at: datetime | None = None
    rescan_error: str | None = None

    def start(self, tickers: list[str], timeframe: str = "5m", strategy: str = "refined_rsi", scan_tactic: str = "market_scan"):
        self.running = True
        self.started_at = datetime.now(timezone.utc)
        self.active_tickers = tickers
        self.timeframe = timeframe
        self.strategy = strategy
        self.scan_tactic = scan_tactic
        self.error = None

    def stop(self):
        self.running = False
        self.started_at = None
        self.active_tickers = []
        self.scan_tactic = "market_scan"
        self.last_rescan_at = None
        self.rescan_error = None

    def to_dict(self) -> dict:
        now = datetime.now(timezone.utc)
        uptime = 0.0
        if self.running and self.started_at:
            uptime = (now - self.started_at).total_seconds()
        return {
            "running": self.running,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "active_tickers": self.active_tickers,
            "uptime_seconds": uptime,
            "timeframe": self.timeframe,
            "strategy": self.strategy,
            "scan_tactic": self.scan_tactic,
            "last_rescan_at": self.last_rescan_at.isoformat() if self.last_rescan_at else None,
            "rescan_error": self.rescan_error,
        }
