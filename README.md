# Style Rotation Research Platform v0.2

Versioned research platform for explainable US style rotation across IWF, IWD, IWO, and IWN, with SPY as the product benchmark and a DGS3MO-based synthetic reserve sleeve.

## Status

v0.2 is being rebuilt in short, independently verified milestones. M0 through M6C are complete. The platform now has immutable publication, typed market data, deterministic Factor/Signal/Model calculation and independent diagnostics, complete Strategy Product identities and Target Paths, plus read-only bilingual research pages through the Strategies layer. Experiment accounting and multi-range evaluation follow in M7.

The authoritative plan is [v0.2/正式开发方案.md](v0.2/正式开发方案.md). Detailed decisions and database rationale are stored in [v0.2/设计决策记录.md](v0.2/设计决策记录.md) and [v0.2/数据库设计.md](v0.2/数据库设计.md).

v0.1 is not migrated or reproduced by v0.2. Its implementation remains available through Git history and its documentation remains under `v0.1/`.

## Research chain

```text
Catalog / Data
→ Factor
→ Signal
→ Model
→ Strategy Product
→ Experiment Result
```

Definitions express reusable mathematical or financial logic. Published datasets and experiment specifications bind that logic to assets, data snapshots, dates, execution, costs, and benchmarks.

## Local setup

1. Copy `.env.example` to `.env` and keep real secrets outside Git.
2. Start PostgreSQL with `docker compose up -d postgres` when the active milestone requires it.
3. Create a Python 3.12 virtual environment.
4. Install with `pip install -e ".[dev]"`.
5. Run `pytest tests/unit`.

The Alembic chain now starts from the clean v0.2 foundation. It intentionally does not migrate the v0.1 public-schema database.

For isolated migration tests, use `docker compose up -d postgres-test`. It exposes only the project test database on localhost port55432.

## Unified CLI

v0.2 exposes one command:

```powershell
style-rotation --version
style-rotation modules
style-rotation db status
style-rotation db upgrade
```

Destructive local rebuilds require an exact database-name confirmation:

```powershell
style-rotation db reset --confirm-database style_rotation_test
```

Database commands plus `bootstrap catalogs`, `artifact list/show/invalidate`, `lineage show`, and the loopback-only `api` server are implemented. Later domain commands are registered early so the interface remains stable, but return an explicit nonzero “not implemented” result until their delivery milestone. They never silently invoke v0.1 calculations.

Planned commands are `db`, `bootstrap`, `data`, `factor`, `signal`, `model`, `strategy`, `experiment`, `lineage`, `artifact`, `backup`, and `api`.

## Machine-readable research catalog

The v0.2.0 seed catalog is under `v0.2/catalogs/`. Validate it with:

```powershell
.\.venv\Scripts\python.exe v0.2/tools/validate_catalogs.py
```

The frozen M0 baseline contains 12 factor definitions, 28 factor variants, 51 generated signals, 31 non-empty dimension-subset patterns, and 86 concrete seed model specifications.

Publish the catalogs idempotently and inspect their immutable lineage:

```powershell
style-rotation bootstrap catalogs
style-rotation bootstrap scope
style-rotation bootstrap data-contracts
style-rotation data calendar --start 2000-01-01 --end 2026-12-31
style-rotation data fetch --start 2000-01-01 --end 2026-08-03
style-rotation data publish-market --snapshot-artifact-id <uuid> --calendar-artifact-id <uuid> --version 1
style-rotation data publish-rate --snapshot-artifact-id <uuid> --version 1
style-rotation bootstrap reserve-model
style-rotation data publish-reserve --rate-dataset-artifact-id <uuid> --calendar-artifact-id <uuid> --model-artifact-id <uuid> --version 1
style-rotation data publish-bundle --market-artifact-id <uuid> --rate-artifact-id <uuid> --reserve-artifact-id <uuid> --calendar-artifact-id <uuid> --version 1
style-rotation data publish-eligibility --universe-artifact-id <uuid> --requirement-artifact-id <uuid> --bundle-artifact-id <uuid> --start 2010-01-01 --end 2026-08-03 --warmup-observations 253 --version 1
style-rotation factor bootstrap --catalog-file v0.2/catalogs/factors.v0.2.0.json
style-rotation factor bootstrap-engine --git-commit <hex-commit> --dependency-lock-file requirements.lock --version 1
style-rotation factor publish --factor-catalog-artifact-id <uuid> --bundle-artifact-id <uuid> --eligibility-artifact-id <uuid> --engine-artifact-id <uuid>
style-rotation factor bootstrap-diagnostic-engine --git-commit <hex-commit> --dependency-lock-file requirements.lock --version 1
style-rotation factor diagnose --factor-catalog-artifact-id <uuid> --bundle-artifact-id <uuid> --eligibility-artifact-id <uuid> --factor-engine-artifact-id <uuid> --diagnostic-engine-artifact-id <uuid>
style-rotation signal bootstrap --catalog-file v0.2/catalogs/signals.v0.2.0.json
style-rotation signal bootstrap-engine --git-commit <hex-commit> --dependency-lock-file requirements.lock --version 1
style-rotation signal publish --signal-catalog-artifact-id <uuid> --factor-catalog-artifact-id <uuid> --bundle-artifact-id <uuid> --eligibility-artifact-id <uuid> --factor-engine-artifact-id <uuid> --signal-engine-artifact-id <uuid>
style-rotation data bootstrap-forward-returns --catalog-file v0.2/catalogs/forward_returns.v0.2.0.json
style-rotation data bootstrap-forward-return-engine --git-commit <hex-commit> --dependency-lock-file requirements.lock --version 1
style-rotation data publish-forward-returns --catalog-artifact-id <uuid> --universe-artifact-id <uuid> --bundle-artifact-id <uuid> --engine-artifact-id <uuid> --start 2010-01-01 --end 2026-08-03
style-rotation signal bootstrap-evaluation-engine --git-commit <hex-commit> --dependency-lock-file requirements.lock --version 1
style-rotation signal evaluate --signal-catalog-artifact-id <uuid> --forward-return-artifact-id <uuid> --signal-engine-artifact-id <uuid> --evaluation-engine-artifact-id <uuid>
style-rotation model bootstrap --catalog-file v0.2/catalogs/models.v0.2.0.json
style-rotation model bootstrap-engine --git-commit <hex-commit> --dependency-lock-file requirements.lock --version 1
style-rotation model publish --model-catalog-artifact-id <uuid> --signal-catalog-artifact-id <uuid> --bundle-artifact-id <uuid> --eligibility-artifact-id <uuid> --signal-engine-artifact-id <uuid> --model-engine-artifact-id <uuid>
style-rotation strategy bootstrap --catalog-file v0.2/catalogs/strategies.v0.2.0.json
style-rotation strategy publish-product --strategy-catalog-artifact-id <uuid> --model-catalog-artifact-id <uuid> --universe-artifact-id <uuid> --model-specification-key <key> --strategy-variant-key <key> --schedule-key <key>
style-rotation strategy bootstrap-target-engine --git-commit <hex-commit> --dependency-lock-file requirements.lock --version 1
style-rotation strategy publish-target --product-artifact-id <uuid> --model-dataset-artifact-id <uuid> --target-engine-artifact-id <uuid> [--auxiliary-signal-dataset-artifact-id <uuid>]
style-rotation artifact list
style-rotation lineage show <artifact-uuid>
```

`data fetch` performs real network requests and publishes immutable source snapshots. The explicit
`publish-market` and `publish-rate` parse source evidence into typed canonical datasets. Reserve
accrual, bundles, and eligibility are separate explicit publications, so formal research never
selects a runtime `latest` dataset or silently changes its warmup requirement.

Build the React application and run the combined local service:

```powershell
cd frontend
pnpm install --frozen-lockfile
pnpm run generate:api
pnpm run lint
pnpm run typecheck
pnpm run test
pnpm run build
cd ..
style-rotation api
```

The application is then available at `http://127.0.0.1:8000/`, with OpenAPI docs at `/api/v2/docs`. The unauthenticated server refuses non-loopback bind addresses.
The Data, Factors, Signals, Models, and Strategies pages read published overview endpoints. Strategies separates frozen rules, complete product identities, and per-decision candidate target weights. The browser does not recompute research statistics or display not-yet-produced strategy performance.

## Development rules

- Published v0.2 artifacts are versioned, immutable, traceable, and never silently overwritten.
- Formal runs pin exact data, catalog, engine, and policy versions instead of resolving `latest` during calculation.
- Pure calculators do not access the database; services orchestrate; repositories persist; API and CLI do not contain financial algorithms.
- The frontend displays published backend results and does not recompute financial metrics.
- Every milestone includes tests, documentation, a repeatable verification command, and a short application-oriented design note.
