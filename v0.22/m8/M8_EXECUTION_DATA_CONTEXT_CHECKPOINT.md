# M8 Compiled Execution Data Context Checkpoint

Date: 2026-08-12

## Outcome

Every newly compiled current-catalog Graph Draft now publishes one immutable
Compiled Execution Data Context. It freezes the complete documents and physical
identities needed to reconstruct the graph's data boundary:

- the exact fixed Asset Context and ordered securities;
- every resolved Dataset Publication and its published Artifact;
- the exact coverage interval and ordered security binding;
- the optional Calendar Version and Calendar Artifact;
- direct lineage from the Context Artifact to the Compiled Graph, Asset Registry,
  Dataset Publications, and deduplicated Calendars.

The Context is a one-to-one downstream projection of a Compiled Graph. It does not
change the existing Graph fingerprint or reinterpret an older Graph.

## Compatibility and admission

Catalog releases before the execution-context capability retain their original Graph,
Branch, Artifact, and command-response identities. An exact historical compile response
can still be replayed, including during maintenance, but its absent Context identity is
not presented as a current executable compile in the UI.

For the current Catalog capability, a compile must provide both the frozen Asset Context
and Resolved Data Binding documents. Partial documents, fingerprint drift, unpublished or
mismatched physical identities, incomplete ordered inputs, and incomplete lineage all
fail closed. The four public Context response fields are either wholly present or wholly
absent.

A new Suite-to-Graph-Run binding is admitted only when the Graph has its exact published
Context. This is necessary but not sufficient to start a v0.22 experiment.

## Runtime boundary

The public Graph Suite submit endpoint remains disabled. This checkpoint does not add
Strategy, Defense, sleeve merge, or Portfolio Cell work to the DAG, and it does not claim
that a processing/aggregation Graph Run is a running experiment.

The next runtime slice must add explicit typed work and immutable outputs for:

1. Strategy target generation from each exact Strategy Branch and parameter preset;
2. optional Defense decisions and their exact defensive allocation universe;
3. sleeve merge into one executable target;
4. Portfolio Cell evaluation for each frozen Suite evaluation context;
5. terminal status, retry, fencing, cancellation, and evidence derived from real Cell work.
