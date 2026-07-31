# Style Rotation Factor Engine v0.1

Deterministic factor research and backtesting platform for IWF, IWD, IWO and IWN,
with a rebalanced equal-weight benchmark, SPY market reference, and DGS3MO reserve return.

The frozen research protocol is stored in `v0.1/`. Formal calculations do not use an LLM,
Agent, automatic parameter selection, machine learning, leverage, shorting, or live trading.

## Current development stage

Phases 1 through 6 establish:

- validated application settings;
- versioned data-contract definitions;
- deterministic configuration fingerprints;
- PostgreSQL metadata models and migrations;
- experiment, run, event, and archive lifecycles;
- Yahoo Finance OHLCV/action ingestion and FRED DGS3MO ingestion;
- immutable raw snapshots, adjusted-price cleaning, reserve daily returns, and data-quality gates;
- a frozen registry of 11 factor definitions and 24 independent parameter variants;
- pure daily factor calculations, factor dataset publication, and deterministic reuse;
- weekly/monthly rebalance calendars, next-session execution mapping, stable rankings,
  Top 2 and strict SMA200-filtered target portfolios;
- adjusted-open execution, overnight weight drift, single-sided turnover, 2/5/10 bps costs,
  daily gross/net NAV and end-of-day positions;
- same-frequency four-ETF equal-weight and SPY buy-and-hold benchmarks;
- versioned, fingerprinted, atomically published backtest runs with deterministic reuse;
- shared Rank IC and Top 2-Bottom 2 factor diagnostics for each factor/frequency pair;
- gross/net return, risk, risk-adjusted, relative benchmark, turnover, cost, and reserve metrics;
- versioned metric methodology, explicit undefined reason codes, input manifests, and reuse;
- unit and PostgreSQL integration tests for the deterministic core and phases 1 through 6.

The read-only API and front-end are intentionally deferred to phase 7.

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
style-rotation-factor-update
style-rotation-signal-update
style-rotation-backtest-update
style-rotation-metrics-update
```

The end date is inclusive at the application boundary. The pipeline pins provider request
parameters, stores raw rows before cleaning, publishes only datasets that pass the quality
gate, and creates a new `data_version` whenever source content changes.

Yahoo Finance data is intended for personal research use. The project stores source metadata
and hashes because historical adjusted values may be revised by the provider.

`style-rotation-factor-update` uses the latest published clean dataset unless both explicit
version identifiers are supplied. It stores unranked factor values only; direction normalization,
Top 2 selection, and target weights belong to the signal layer.

`style-rotation-signal-update` uses the latest published factor dataset unless all three upstream
version identifiers are supplied. It publishes target weights only; it does not assume trades have
filled or calculate turnover, costs, holdings, returns, or NAV.

`style-rotation-backtest-update` uses the latest published signal dataset unless all four upstream
version identifiers are supplied. The formal matrix contains 24 variants, two frequencies, two
strategy templates, and three cost scenarios (288 runs). `--variant-key` may be repeated for a
scoped development or verification run. Completed fingerprints are reused without recalculation.

`style-rotation-metrics-update` selects the latest clean, complete 288-run matrix by default. It
publishes 48 shared factor/frequency diagnostic sets and one performance publication per run.
Metric formulas, code, dependencies, Git commit, units, sample counts, and undefined reasons are
versioned; a repeated completed metric version is reused without recalculation.

## Design rule

Database access is isolated behind persistence/repository boundaries. Factor, ranking,
portfolio, cost, and metric functions remain pure deterministic calculations.
