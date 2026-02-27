# Soren

Soren is an automated stock trading platform that combines real-time technical analysis with algorithmic signal generation and execution. It features a Python backend powered by FastAPI and an interactive Next.js dashboard with live candlestick charting, strategy management, and a full replay/backtesting engine.

---

## Table of Contents

- [Features](#features)
- [Tech Stack](#tech-stack)
- [Project Structure](#project-structure)
- [Prerequisites](#prerequisites)
- [Installation](#installation)
- [Configuration](#configuration)
- [Usage](#usage)
  - [Starting the Application](#starting-the-application)
  - [Trading Strategies](#trading-strategies)
  - [Replay / Backtesting](#replay--backtesting)
  - [Market Scanner](#market-scanner)
- [API Reference](#api-reference)
- [Architecture Overview](#architecture-overview)

---

## Features

- **Live Trading Engine** -- Connects to Interactive Brokers (via `ib_async`) for real-time order execution with bracket orders (stop loss + profit target).
- **Multi-Strategy Framework** -- Ships with five built-in strategies including RSI Divergence, VWAP Bounce, Mean Reversion, Momentum Breakout, and Exhaustion Reversal.
- **Technical Analysis Pipeline** -- Automated detection of support/resistance levels, price channels, chart patterns (double top/bottom, head and shoulders, triangles), RSI divergence, Bollinger Bands, VWAP, and ATR.
- **Real-Time Dashboard** -- Interactive candlestick charting (TradingView Lightweight Charts) with S/R overlays, trade markers, and live WebSocket streaming.
- **Replay / Backtesting** -- Bar-by-bar historical replay using Yahoo Finance data with simulated order fills, equity curve tracking, and detailed performance metrics.
- **Market Scanner** -- Two-phase fitness scanner that pre-filters the US equity universe on daily metrics and then runs mini-backtests on 1-minute data to rank tickers by strategy fitness.
- **Risk Management** -- Configurable position sizing, max exposure limits, per-trade risk caps, and minimum risk:reward ratio enforcement.
- **Mock Broker** -- Full-featured simulated broker for development and testing without requiring an IB connection.

---

## Tech Stack

### Backend

| Component       | Technology                                      |
| --------------- | ----------------------------------------------- |
| Framework       | FastAPI 0.115+                                  |
| Language        | Python 3.12                                     |
| Database        | SQLite (async via aiosqlite + SQLAlchemy 2.0)   |
| Broker          | Interactive Brokers (`ib_async`)                |
| Market Data     | Yahoo Finance (`yfinance`)                      |
| Analysis        | pandas, NumPy, SciPy                            |
| WebSocket       | FastAPI WebSocket + `websockets`                |
| Logging         | structlog                                       |
| HTTP Client     | httpx                                           |

### Frontend

| Component       | Technology                                      |
| --------------- | ----------------------------------------------- |
| Framework       | Next.js 16 (App Router)                         |
| Language        | TypeScript 5                                    |
| UI              | React 19, Tailwind CSS 4                        |
| Charting        | TradingView Lightweight Charts 5                |
| State           | Zustand 5                                       |
| Data Tables     | TanStack React Table 8                          |
| Icons           | Lucide React                                    |

---

## Project Structure

```
soren/
├── backend/
│   ├── app/
│   │   ├── main.py                 # FastAPI application entry point
│   │   ├── config.py               # Pydantic settings (env-based config)
│   │   ├── dependencies.py         # Dependency injection (broker selection)
│   │   ├── api/
│   │   │   ├── rest/               # REST endpoints
│   │   │   │   ├── candles.py      #   Historical candle data
│   │   │   │   ├── trades.py       #   Trade CRUD
│   │   │   │   ├── positions.py    #   Open positions
│   │   │   │   ├── metrics.py      #   Performance metrics
│   │   │   │   ├── engine.py       #   Start/stop trading engine
│   │   │   │   ├── scanner.py      #   Market scanner
│   │   │   │   ├── replay.py       #   Replay/backtest controls
│   │   │   │   ├── analysis.py     #   Technical analysis results
│   │   │   │   ├── strategies.py   #   Strategy listing
│   │   │   │   └── tickers.py      #   Ticker info lookup
│   │   │   └── ws/                 # WebSocket
│   │   │       ├── handlers.py     #   WS route handler
│   │   │       └── manager.py      #   Connection manager + broadcast
│   │   ├── analysis/               # Technical analysis modules
│   │   │   ├── pipeline.py         #   Orchestrates S/R, channels, RSI
│   │   │   ├── indicators.py       #   ATR, SMA, EMA, RSI, VWAP, Bollinger
│   │   │   ├── support_resistance.py  # S/R detection via local extrema
│   │   │   ├── channels.py         #   Channel detection (linear regression)
│   │   │   └── patterns.py         #   Chart patterns (H&S, triangles, etc.)
│   │   ├── broker/                 # Broker adapters (strategy pattern)
│   │   │   ├── base.py             #   Abstract BrokerAdapter
│   │   │   ├── ib_adapter.py       #   Interactive Brokers live adapter
│   │   │   ├── mock_adapter.py     #   Simulated broker for dev/testing
│   │   │   └── replay_adapter.py   #   Replay broker with stop/target sim
│   │   ├── engine/                 # Trading engine core
│   │   │   ├── trading_engine.py   #   Main live trading loop
│   │   │   ├── replay_engine.py    #   Historical bar-by-bar replay
│   │   │   ├── signal_generator.py #   Signal generation (all strategies)
│   │   │   ├── strategies.py       #   Strategy registry and definitions
│   │   │   ├── risk_manager.py     #   Position sizing + exposure limits
│   │   │   ├── divergence_tracker.py  # Multi-TF RSI divergence tracking
│   │   │   ├── candle_cache.py     #   Local candle data caching
│   │   │   └── state.py            #   Engine state management
│   │   ├── scanner/                # Market scanning
│   │   │   ├── runner.py           #   Scanner execution (preset-based)
│   │   │   ├── fitness.py          #   Two-phase fitness scoring
│   │   │   ├── presets.py          #   Scanner preset configs
│   │   │   ├── universe.py         #   US equity universe fetching
│   │   │   ├── percentile_scanner.py  # Percentile-based scanning
│   │   │   └── backtest_scorer.py  #   Real backtest-based scoring
│   │   ├── models/                 # SQLAlchemy ORM models
│   │   │   ├── trade.py
│   │   │   ├── signal.py
│   │   │   └── account_snapshot.py
│   │   ├── schemas/                # Pydantic schemas
│   │   │   ├── candle.py
│   │   │   ├── trade.py
│   │   │   ├── position.py
│   │   │   ├── metrics.py
│   │   │   └── ws_messages.py
│   │   └── db/                     # Database layer
│   │       ├── session.py
│   │       ├── base.py
│   │       ├── trade_repo.py
│   │       └── account_repo.py
│   ├── backtest_results/           # Saved backtest output (CSV, JSON, MD)
│   └── pyproject.toml              # Python project metadata + dependencies
│
├── frontend/
│   ├── src/
│   │   ├── app/
│   │   │   ├── page.tsx            # Main dashboard page
│   │   │   ├── layout.tsx          # Root layout (dark theme)
│   │   │   └── globals.css         # Tailwind global styles
│   │   ├── components/
│   │   │   ├── chart/              # Candlestick chart + overlays
│   │   │   ├── layout/             # Header, sidebar, dashboard grid
│   │   │   ├── controls/           # Engine toggle, trading controls, replay
│   │   │   ├── metrics/            # Performance info panel
│   │   │   ├── positions/          # Current positions display
│   │   │   └── trades/             # Trade history table
│   │   ├── hooks/                  # React hooks (candles, WS, engine, etc.)
│   │   ├── stores/                 # Zustand global state
│   │   └── lib/                    # API client, types, formatters
│   ├── package.json
│   ├── tsconfig.json
│   ├── next.config.ts
│   └── postcss.config.mjs
│
├── .env.example                    # Environment variable template
├── .python-version                 # Python 3.12.8
├── start.sh                        # Dev startup script (backend + frontend)
└── .gitignore
```

---

## Prerequisites

- **Python 3.12+**
- **Node.js 18+** (with npm)
- **Interactive Brokers TWS or IB Gateway** (optional -- only needed for live trading; the mock broker works without it)

---

## Installation

### 1. Clone the repository

```bash
git clone https://github.com/simstre/soren.git
cd soren
```

### 2. Backend setup

```bash
cd backend

# Create a virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -e .

# (Optional) Install dev dependencies
pip install -e ".[dev]"
```

### 3. Frontend setup

```bash
cd frontend
npm install
```

### 4. Environment configuration

```bash
# From the project root
cp .env.example backend/.env
```

Edit `backend/.env` as needed (see [Configuration](#configuration) below).

---

## Configuration

All backend settings are controlled via environment variables with the `SOREN_` prefix. Copy `.env.example` to `backend/.env` and adjust:

| Variable                    | Default        | Description                                          |
| --------------------------- | -------------- | ---------------------------------------------------- |
| `SOREN_BROKER_MODE`        | `mock`         | `mock` for development, `ib` for Interactive Brokers |
| `SOREN_DEBUG`              | `true`         | Enable debug logging                                 |
| `SOREN_STARTING_BALANCE`   | `100000.0`     | Starting account balance (mock/replay mode)          |
| `SOREN_IB_HOST`            | `127.0.0.1`    | IB TWS/Gateway host                                  |
| `SOREN_IB_PORT`            | `7497`         | IB port (7497 TWS paper, 4002 Gateway paper)        |
| `SOREN_IB_CLIENT_ID`       | `1`            | IB API client ID                                     |
| `SOREN_MAX_POSITION_PCT`   | `0.05`         | Max position size as fraction of account (5%)        |
| `SOREN_MAX_EXPOSURE_PCT`   | `0.25`         | Max total exposure as fraction of account (25%)      |
| `SOREN_RISK_PER_TRADE_PCT` | `0.01`         | Max risk per trade as fraction of account (1%)       |
| `SOREN_DEFAULT_TIMEFRAME`  | `5m`           | Default candle timeframe                             |

Frontend configuration (optional, in `frontend/.env.local`):

| Variable                  | Default                         |
| ------------------------- | ------------------------------- |
| `NEXT_PUBLIC_API_URL`     | `http://localhost:8000`         |
| `NEXT_PUBLIC_WS_URL`     | `ws://localhost:8000/ws/stream` |

---

## Usage

### Starting the Application

The easiest way to start both servers is with the included startup script:

```bash
./start.sh
```

This launches:
- **Backend** at `http://localhost:8000` (FastAPI with auto-reload)
- **Frontend** at `http://localhost:3000` (Next.js dev server)
- **API Docs** at `http://localhost:8000/docs` (Swagger UI)

Alternatively, start each server manually:

```bash
# Terminal 1: Backend
cd backend
PYTHONPATH=. .venv/bin/uvicorn app.main:app --port 8000 --reload

# Terminal 2: Frontend
cd frontend
npm run dev
```

### Trading Strategies

Soren ships with five strategies, selectable from the sidebar:

| Strategy              | ID                     | Description                                                        |
| --------------------- | ---------------------- | ------------------------------------------------------------------ |
| RSI Divergence Smart  | `refined_rsi`          | Timeframe-adaptive RSI divergence + S/R proximity, ATR-based stops |
| RSI Divergence PCT    | `rsi_divergence_pct`   | Percentage-based stops/targets (2% SL, 6% target), extended hold   |
| RSI Divergence + S/R  | `rsi_divergence_sr`    | RSI divergence + support/resistance multi-timeframe                |
| VWAP Bounce           | `vwap_bounce`          | VWAP pullback bounce with RSI confirmation                         |
| Mean Reversion        | `mean_reversion`       | Bollinger Band touch + RSI extreme                                 |

Each strategy defines its own entry rules, exit conditions, position sizing, and risk parameters.

### Replay / Backtesting

The replay engine lets you backtest any strategy against historical data:

1. Open the **Replay Panel** in the dashboard sidebar.
2. Select one or more ticker symbols, a date range, timeframe, and strategy.
3. Click **Start Replay** to begin the bar-by-bar simulation.
4. Watch trades execute in real time on the chart with entry/exit markers.
5. Review results including equity curve, win rate, P&L, max drawdown, and individual trades.

The replay engine supports both long and short positions, bracket orders with stop loss and profit target, and multiple concurrent positions across different symbols.

### Market Scanner

The built-in scanner helps identify high-fitness tickers for each strategy:

- **Preset Scans** -- Gap up, gap down, high volume, etc.
- **Strategy Fitness Scan** -- Two-phase scoring:
  1. **Phase 1**: Daily pre-filter (price > $10, volume > 500K, ATR% thresholds)
  2. **Phase 2**: Mini-backtest on 7 days of 1-minute data, simulating strategy entries and scoring by win rate and total P&L
- **Market Scan** -- Scans the full US equity universe (~7,000 symbols from NASDAQ FTP) through the fitness pipeline to find optimal trading candidates.

---

## API Reference

The backend exposes a REST API and a WebSocket endpoint. Full interactive documentation is available at `http://localhost:8000/docs` when the server is running.

### Key Endpoints

| Method | Path                          | Description                       |
| ------ | ----------------------------- | --------------------------------- |
| GET    | `/api/health`                 | Health check                      |
| GET    | `/api/candles/{symbol}`       | Historical candle data            |
| GET    | `/api/analysis/{symbol}`      | Technical analysis (S/R, channels)|
| GET    | `/api/trades`                 | Trade history                     |
| GET    | `/api/positions`              | Current open positions            |
| GET    | `/api/metrics`                | Performance metrics               |
| POST   | `/api/engine/start`           | Start the live trading engine     |
| POST   | `/api/engine/stop`            | Stop the trading engine           |
| GET    | `/api/engine/status`          | Engine status                     |
| GET    | `/api/strategies/`            | List available strategies         |
| POST   | `/api/scanner/run`            | Run a scanner preset              |
| GET    | `/api/scanner/fitness`        | Run fitness scan                  |
| POST   | `/api/replay/start`           | Start historical replay           |
| POST   | `/api/replay/stop`            | Stop replay                       |
| GET    | `/api/replay/results`         | Get replay/backtest results       |
| GET    | `/api/settings/trading`       | Current trading risk parameters   |
| WS     | `/ws/stream`                  | Real-time WebSocket stream        |

### WebSocket Messages

The WebSocket stream (`/ws/stream`) broadcasts the following message types:

- `bar_update` -- New candle bar data
- `trade_opened` / `trade_closed` -- Live trade events
- `replay_trade` -- Replay/backtest trade events
- `replay_state` -- Replay progress updates
- `replay_candle` -- Replay candle data for chart rendering

---

## Architecture Overview

```
                ┌─────────────────────────────────┐
                │        Next.js Frontend         │
                │   (Charts, Controls, Metrics)   │
                └───────────┬─────────────────────┘
                            │ REST + WebSocket
                ┌───────────▼─────────────────────┐
                │        FastAPI Backend          │
                │                                 │
                │  ┌──────────┐  ┌─────────────┐  │
                │  │ REST API │  │ WebSocket   │  │
                │  └────┬─────┘  └──────┬──────┘  │
                │       │               │         │
                │  ┌────▼───────────────▼──────┐  │
                │  │     Trading Engine        │  │
                │  │  (Signal Gen + Risk Mgr)  │  │
                │  └────┬──────────────────────┘  │
                │       │                         │
                │  ┌────▼──────┐  ┌────────────┐  │
                │  │ Analysis  │  │  Scanner   │  │
                │  │ Pipeline  │  │  (Fitness) │  │
                │  └───────────┘  └────────────┘  │
                │       │                         │
                │  ┌────▼──────────────────────┐  │
                │  │    Broker Adapters        │  │
                │  │  IB / Mock / Replay       │  │
                │  └───────────────────────────┘  │
                │                                 │
                │  ┌───────────────────────────┐  │
                │  │  SQLite DB (trades, etc.) │  │
                │  └───────────────────────────┘  │
                └─────────────────────────────────┘
                            │
                   ┌────────▼────────┐
                   │ Interactive     │
                   │ Brokers TWS/GW  │
                   └─────────────────┘
```

The system follows a layered architecture:

1. **Broker Layer** -- Abstract adapter pattern allowing seamless switching between live (Interactive Brokers), mock (simulated), and replay (historical) brokers.
2. **Analysis Layer** -- Stateless pipeline that computes S/R levels, channels, chart patterns, and technical indicators from raw candle data.
3. **Engine Layer** -- Orchestrates the trading loop: fetches data, runs analysis, generates signals, validates risk, and executes orders. Includes a separate replay engine for backtesting.
4. **API Layer** -- REST endpoints for CRUD operations and WebSocket for real-time streaming to the frontend.
5. **Frontend Layer** -- Single-page dashboard with live charting, strategy selection, trade history, and performance analytics.
