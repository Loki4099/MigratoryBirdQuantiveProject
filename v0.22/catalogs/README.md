# Migratory Bird v0.22 Catalog

This directory is the machine-readable source for published v0.22 Catalog releases. A release
manifest references only the JSON files that passed review; draft files appearing beside them do
not automatically become visible or executable.

The current release is `catalog_release.v0.22.13.json` (`catalog_version=0.22.13`). It publishes:

- 9 governed raw inputs;
- 82 processing features implemented by 80 nodes across three processing stages;
- 9 deterministic or trainable aggregation families;
- 2 cross-sectional strategy variants and 5 parameter presets;
- 13 payload contracts and 1 physical encoding.

The manifest retains two legacy defense package identities for compatibility and historical
evidence, but publishes no executable defense timing or allocation policies. The active v0.22
research path therefore supports **no defense** only. Future defense research requires a new
versioned contract and must not reinterpret these identities.

## Commands

```text
.venv/Scripts/python.exe -m style_rotation.cli.v022_catalog lint <release-manifest>
.venv/Scripts/python.exe -m style_rotation.cli.v022_catalog diff <old> <new>
.venv/Scripts/python.exe -m style_rotation.cli.v022_catalog plan <release-manifest>
.venv/Scripts/python.exe -m style_rotation.cli.v022_catalog publish <release-manifest>
.venv/Scripts/python.exe -m style_rotation.cli.v022_catalog verify <release-artifact-id>
```

`lint`, `diff`, and `plan` do not write to the database. `publish` and `verify` use the configured
database. Development and test publication must target the isolated test database.

Publisher and reviewer identities come from the local trusted configuration
`STYLE_ROTATION_CATALOG_PUBLISHER_ACTOR`; a request or temporary parameter cannot self-assert them.
The actors declared by a Catalog file are expected publication policy and must match the trusted
configuration.

Published components, release membership, and evidence are append-only. Re-publishing an identical
identity with identical semantics must reuse it exactly. Reusing an identity for different semantics
must fail rather than repair or overwrite history.
