# M8 Graph Suite Identity Checkpoint

Date: 2026-08-12

## Outcome

The first post-cutover Suite slice establishes an independent v0.22 identity boundary
from an immutable `compiled_research_graph_id`. It does not reinterpret a Graph Draft and
does not reuse the v0.21 six-cell Suite schema.

The schema publishes these append-only identities:

- one server-owned Evaluation Matrix Policy and its ordered contexts;
- one Research Suite for the complete Compiled Graph;
- one Suite Branch for every compiled Strategy Branch, each bound to an immutable
  Configuration Snapshot;
- one Research Cell for every Branch × Evaluation Context pair;
- a future Suite-to-Graph-Run binding whose database guard requires the same published
  Compiled Graph on both sides.

The initial exploratory policy deliberately contains one full-common-history context.
That means the first slice produces one Cell per compiled Branch. This is a versioned
server policy, not an inherited v0.21 six-cell assumption.

## Safety boundary

The public `POST /api/v2/workspace/graph-suites` contract accepts only an immutable
Compiled Graph identity plus an authenticated actor claim, idempotency key, and the
`exploratory` mode. It never falls back to `POST /api/v2/workspace/suites`.

The endpoint remains fail-closed with HTTP 503 while the real Strategy, Defense, and
Portfolio Cell runtime is not enabled. A processing/aggregation-only Graph Run must not
be presented as a started experiment. Exact idempotency replay is checked before the
current release mutation gate; a new command must pass the authoritative
`v022_research` admission gate before any runtime publication.

## Next slice

Enable the Suite endpoint only after all of the following are true:

1. the work DAG owns explicit Strategy, Defense, and Portfolio Cell work kinds;
2. every Cell is planned from its frozen Configuration Snapshot and Evaluation Context;
3. Strategy and Defense outputs are published as typed immutable artifacts;
4. portfolio execution/backtest results reach a real terminal status;
5. Suite status is derived from Cell work and cannot report `ready` or `running` for a
   graph-only admission;
6. retry, cancellation, fencing, failure propagation, resource admission, and shared-work
   reuse tests pass on PostgreSQL.

Before that runtime slice, Strategy parameter presets (including the exact Top-K value)
and the exact execution data context must become immutable compiled identities. A Branch
that names only a Strategy family/version is not sufficient to execute an experiment.
