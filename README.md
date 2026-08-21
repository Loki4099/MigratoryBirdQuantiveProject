# Migratory Bird — Reproducible Quantitative Research Platform

Migratory Bird is a local-first, versioned research platform for cross-sectional US equity
signals. It is designed to make the path from market data to a ranked portfolio inspectable and
reproducible—not merely to produce another backtest chart.

Every experiment pins its dataset, historical universe, calendar, feature graph, model state,
strategy, execution assumptions, benchmark, and result evidence. v0.22 carries a research idea from
asset selection through three processing layers, aggregation, weekly/monthly experiments, ranking,
detailed backtest evidence, and optional Research Product monitoring.

**Current release:** v0.22, feature-complete and frozen for portfolio review.

> **Research software only.** This is not a live-trading system or investment advice. Large market
> datasets and runtime artifacts are deliberately excluded from Git.

## Why this project exists

Many quantitative prototypes mix data cleaning, feature engineering, model fitting, portfolio
construction, and performance reporting in one notebook. That makes it difficult to distinguish a
real result from survivorship bias, look-ahead leakage, mutable data, or a silently changed
configuration.

Migratory Bird treats those concerns as system contracts:

- immutable data, catalog, graph, model, and result identities;
- explicit point-in-time and `known_at` rules for research dependencies;
- frozen weekly and monthly comparison cohorts with identical evaluation boundaries;
- append-only lineage from source observations to each Portfolio Cell result;
- fail-closed compilation and runtime validation instead of silent fallback;
- strict separation between exploratory results and monitored Product candidates.

## Research workflow

```text
Historical universe and governed market data
        ↓
Asset selection and three processing layers
        ↓
Deterministic or trainable aggregation model
        ↓
Long-only cross-sectional Top-K strategy
        ↓
Weekly and monthly frozen experiments
        ↓
Leaderboard, diagnostics, charts, lineage, and Product promotion
```

The UI explains each selected input's financial meaning, formula, parameters, output semantics,
and upstream/downstream lineage. Calculations are performed by the backend and are never recomputed
in the browser.

## What v0.22 implements

### Governed research data

- Historical S&P 500 membership evidence, stable Security identities, ticker aliases, lifecycle
  events, and terminal settlement are represented separately from price observations.
- Split-normalized OHLCV, dividends, corporate actions, calendars, coverage, and quality findings are
  versioned and validated before a research cohort is published.
- Unresolved or unusable Securities are explicitly excluded rather than silently dropped during a
  backtest.
- The active free-data baseline freezes warm-up from **2004-12-31** and evaluation from
  **2007-01-03 to 2026-06-30**, with SPY as benchmark, a one-session execution delay, and a frozen
  transaction-cost policy.
- Selected asset histories can be exported as year-partitioned Parquet plus a manifest to the user's
  Downloads directory.

The validated local baseline contains 974 historical Security identities, more than 3 million equity
daily observations, and five ETF/benchmark series. Coverage and exclusion details remain published as
data-quality evidence rather than being hidden from the researcher.

### Layered factor research

- A persistent Workspace captures the exact asset universe, processing selections, aggregation
  configuration, strategy, and frequency.
- Three processing layers preserve the full path from governed inputs to aggregation-ready signals.
- The catalog covers momentum/trend, reversal, volatility, downside risk, drawdown, skewness,
  kurtosis, liquidity, relative strength, and technical-event families.
- Inputs only pass to later layers when the catalog explicitly permits that path; upstream values are
  not automatically treated as valid final signals.

### Aggregation and modelling

- Deterministic aggregation includes single-signal identity, flat equal-weight means,
  taxonomy-aware hierarchical weighting, and directional voting.
- Trainable cross-sectional regression supports OLS, Ridge, Random Forest, XGBoost, and LightGBM.
- H5, H10, and H21 forward-rank targets, expanding walk-forward folds, mature-label purging, exact
  feature schemas, fitted-model artifacts, and out-of-fold predictions are frozen into model identity.
- Multiple hyperparameter presets within one model family can form a controlled two-level ensemble;
  different model families remain independent experiment branches.

### Reproducible experiments

- Review and compile validate the graph before an experiment can launch.
- A controlled launch creates independent weekly and monthly Suites from the same source revision;
  the two frequencies never share a leaderboard.
- Durable workers persist progress outside the browser and reuse compatible upstream calculations.
- Each Portfolio Cell publishes its frozen configuration, typed metrics, data-quality evidence,
  element diagnostics, and lineage.
- Results rank within one exact comparison cohort by Sharpe, CAGR, excess CAGR, or maximum drawdown.
- Detail pages show strategy NAV versus SPY, excess NAV, drawdown, core metrics, exact factors/model/
  strategy, and immutable evidence links.

### Research-to-Product boundary

- One accepted Portfolio Cell can be promoted without promoting the rest of its Suite.
- Product identity pins the originating result evidence and preserves free-data quality warnings.
- Sample-out monitoring remains separate from the frozen historical test and fails closed when a
  required input has not been published.
- v0.22 currently exposes **no defense** as the supported research baseline. Earlier fixed-allocation
  and moving-average defense prototypes were retired; future defense research will use a new
  versioned contract rather than silently changing v0.22 results.

## Free-data limitations

The local research baseline uses governed Yahoo Finance observations plus source-backed historical
membership and lifecycle evidence. It corrects known split-normalization errors, requires continuous
usable warm-up data, and records exclusions and unresolved events. It is **not equivalent to a
licensed institutional point-in-time database**.

- Historical prices are retrospective, back-adjusted snapshots.
- Some unavailable or irreparable former constituents are excluded, so residual survivorship and
  data-availability bias remain possible.
- Extreme adjusted daily returns above 50% are retained for review when they cannot be proven wrong.
- Product pages inherit these warnings; the software is not intended for capital deployment.

A fresh clone can run the code and tests, but reproducing the full local research database requires
publishing an independently obtained data baseline under the same contracts.

## Architecture

| Area | Technology / design |
| --- | --- |
| Backend | Python 3.12, FastAPI, Pydantic, SQLAlchemy |
| Persistence | PostgreSQL 16, Alembic, append-only immutable identities |
| Analytics | NumPy, pandas, PyArrow/Parquet, scikit-learn, LightGBM, XGBoost |
| Frontend | React 19, TypeScript, Vite, TanStack Query, bilingual UI |
| Runtime | Durable Suite, asset-export, Product, and GC workers with readiness/telemetry |
| Local infrastructure | Docker Compose and controlled PowerShell service scripts |
| Evidence | Content fingerprints, manifests, configuration snapshots, and lineage DAGs |

```text
src/style_rotation/       Backend, contracts, compilers, runtimes, and workers
frontend/                 React research workspace and result UI
migrations/               PostgreSQL/Alembic schema history
v0.22/catalogs/           Versioned processing, aggregation, strategy, and payload catalogs
v0.22/                    Frozen plans, ADRs, checkpoints, and validation reports
tests/                    Unit and PostgreSQL integration tests
scripts/                  Publication, services, backup, recovery, and migration tools
```

## Local development

### Install and build

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"

cd frontend
corepack enable
pnpm install --frozen-lockfile
pnpm run build
cd ..
```

### Create an isolated schema

```powershell
docker compose -p migratorybird-green -f compose.green.yaml up -d postgres-green
$env:STYLE_ROTATION_DATABASE_URL = "postgresql+psycopg://style_rotation:style_rotation@127.0.0.1:55433/style_rotation_green"
python -m alembic upgrade head
```

This creates the schema only. Research creation remains fail-closed until a governed Dataset, Gate,
weekly/monthly Evaluation Cohorts, Runtime Contracts, and Asset Registry have been published.

After publishing an eligible local baseline:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/start-v022-green-services.ps1
```

Open <http://127.0.0.1:8000>. The default frozen profile runs the API, Suite Worker, asset-export
worker, and Research Round GC worker. Product monitoring is opt-in.

## Verification

```powershell
python -m ruff check src tests
python -m mypy
python -m pytest tests/unit

cd frontend
pnpm run lint
pnpm run typecheck
pnpm run test
pnpm run build
```

Integration tests require a separately migrated PostgreSQL test database. The live API reports its
migration at `GET /api/v2/health`; Suite Worker readiness is exposed at
`GET /api/v2/workspace/graph-suite-runtime/readiness`.

## Documentation

- [v0.22 implemented release status](v0.22/RELEASE.md)
- [Original frozen v0.22 documentation index](v0.22/README.md)
- [Frozen v0.22 development plan](v0.22/候鸟v0.22最终开发计划.md)
- [Workspace information architecture](v0.22/候鸟v0.22_Workspace前端信息架构与交互协议.md)
- [Physical schema and compiler contracts](v0.22/候鸟v0.22物理Schema_编译契约与Workspace_API协议.md)
- [Historical S&P 500 and frozen experiment environment](v0.22/v0.22历史标普数据_统一实验环境与实验结果前端更新计划.md)
- [Post-freeze amendments](v0.22/post-freeze-amendments/README.md)
- [M8 release runbook](v0.22/m8/M8_RELEASE_RUNBOOK.md)
- [Local operation and frontend acceptance](v0.22/v0.22本地运行与前端验收手册.md)

## Disclaimer

This codebase is an educational and research portfolio project. Backtested performance is
hypothetical and may be affected by data limitations, transaction-cost assumptions, model risk, and
residual bias. Nothing in this repository is financial advice.
