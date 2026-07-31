# Style Rotation Factor Engine v0.1

Deterministic factor research and backtesting platform for IWF, IWD, IWO and IWN,
with a rebalanced equal-weight benchmark, SPY market reference, and DGS3MO reserve return.

The frozen research protocol is stored in `v0.1/`. Formal calculations do not use an LLM,
Agent, automatic parameter selection, machine learning, leverage, shorting, or live trading.

## Current development stage

Phases 1 and 2 establish:

- validated application settings;
- versioned data-contract definitions;
- deterministic configuration fingerprints;
- PostgreSQL metadata models and migrations;
- experiment, run, event, and archive lifecycles;
- Yahoo Finance OHLCV/action ingestion and FRED DGS3MO ingestion;
- immutable raw snapshots, adjusted-price cleaning, reserve daily returns, and data-quality gates;
- unit and PostgreSQL integration tests for the deterministic core and data pipeline.

Factor calculations and backtesting are intentionally deferred to phase 3 and later.

## Local setup

1. Copy `.env.example` to `.env` and change secrets outside local development.
2. Start PostgreSQL with `docker compose up -d postgres`.
3. Create a Python 3.12 virtual environment.
4. Install with `pip install -e ".[dev]"`.
5. Apply migrations with `alembic upgrade head`.
6. Run tests with `pytest`.

## Data update

After PostgreSQL is running and migrations are current:

```powershell
style-rotation-data-update --start 1999-01-01 --end 2026-07-30
```

The end date is inclusive at the application boundary. The pipeline pins provider request
parameters, stores raw rows before cleaning, publishes only datasets that pass the quality
gate, and creates a new `data_version` whenever source content changes.

Yahoo Finance data is intended for personal research use. The project stores source metadata
and hashes because historical adjusted values may be revised by the provider.

## Design rule

Database access is isolated behind persistence/repository boundaries. Factor, ranking,
portfolio, cost, and metric functions remain pure deterministic calculations.
