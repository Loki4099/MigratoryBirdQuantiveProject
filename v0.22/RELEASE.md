# Migratory Bird v0.22 release status

This file records the current implemented release without modifying the original frozen manifest.

## Current state

- Application version: `0.22.0`
- Status: **feature-complete / frozen for portfolio review**
- Migration head: `20260821_142_asset_export`
- Active research baseline: clean v5 free-data import with weekly/monthly v11 cohorts
- Supported defense baseline: `none`; legacy fixed-allocation and MA200 prototypes are retired

v0.22 is feature-complete and explicitly eligible in the local research workspace. The operational
release-control record has not redefined v0.21's historical default-contract field, so this status
must not be described as a silent compatibility cutover. v0.21 and earlier material is migration
context and must not be used to infer current UI, data, experiment, or Product behavior.

## Implemented workflow

```text
Assets → Processing 1 → Processing 2 → Processing 3
       → Aggregation/model → Strategy → Compile
       → weekly/monthly Suites → Portfolio Cell evidence
       → leaderboard/detail → optional Product promotion
```

The release includes governed S&P 500 research data, Security lifecycle and settlement evidence, a
three-layer processing graph, deterministic and supervised aggregation, cross-sectional Top-K
strategies, durable experiment workers, strict comparison cohorts, result charts and diagnostics,
Product identity, asset data export, Research Round reset/GC, and backup/recovery tooling.

## Normative documents

1. [`候鸟v0.22最终开发计划.md`](候鸟v0.22最终开发计划.md) — original frozen baseline.
2. [`contract-decisions.v0.22.0.json`](contract-decisions.v0.22.0.json) — machine-readable decisions.
3. [`freeze-manifest.v0.22.0.json`](freeze-manifest.v0.22.0.json) — original freeze record.
4. [`post-freeze-amendments/`](post-freeze-amendments/) — approved post-freeze changes.
5. [`m8/M8_RELEASE_RUNBOOK.md`](m8/M8_RELEASE_RUNBOOK.md) — operations and recovery.

When the original baseline conflicts with an approved amendment, the amendment controls. New semantic
changes require a new ADR/contract version; published identities are never reinterpreted in place.

## Public repository boundary

Source code, catalogs, migrations, tests, plans, and compact validation reports belong in Git. Market
data, database dumps, Parquet payloads, fitted models, exports, service state, and credentials do not.
Users must obtain and publish their own legally permitted market-data baseline.
