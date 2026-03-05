from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    app_name: str = "Soren"
    debug: bool = True

    # Database
    database_url: str = "sqlite+aiosqlite:///./soren.db"

    # Broker
    broker_mode: str = "mock"  # "ib" or "mock"
    ib_host: str = "127.0.0.1"
    ib_port: int = 7497  # 7497 for TWS paper, 4002 for IB Gateway paper
    ib_client_id: int = 1

    # Trading
    starting_balance: float = 100000.0
    max_position_pct: float = 0.05  # 5% of account per trade
    stop_loss_pct: float = 0.02  # 2% stop loss (0 = no stop loss)
    max_exposure_pct: float = 0.25  # 25% total exposure
    risk_per_trade_pct: float = 0.01  # 1% risk per trade
    default_timeframe: str = "5m"

    # CORS
    cors_origins: list[str] = ["http://localhost:3000", "http://localhost:3001", "http://localhost:3010"]

    model_config = {"env_file": ".env", "env_prefix": "SOREN_"}


settings = Settings()
