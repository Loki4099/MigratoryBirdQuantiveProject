from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest

from style_rotation.core.canonical import sha256_hexdigest
from style_rotation.v022.migration import load_migration_registry
from style_rotation.v022.parity_publication import load_and_validate_parity_evidence

REGISTRY = Path("v0.22/m4/migration-registry.v0.22.3.json")
EVIDENCE = Path("v0.22/m4/parity-evidence.v0.22.0.json")


def test_committed_parity_evidence_is_complete_and_bound_to_registry() -> None:
    registry = load_migration_registry(REGISTRY)
    evidence = load_and_validate_parity_evidence(EVIDENCE, registry)

    assert evidence["summary"]["passed"] is True
    assert evidence["summary"]["comparison_count"] == 158
    assert len(evidence["records"]) == 79


def test_parity_publication_rejects_rehashed_failed_comparison(tmp_path: Path) -> None:
    registry = load_migration_registry(REGISTRY)
    evidence = json.loads(EVIDENCE.read_text(encoding="utf-8"))
    changed = deepcopy(evidence)
    changed["records"][0]["comparisons"][0]["numeric_mismatch_count"] = 1
    changed["evidence_fingerprint"] = sha256_hexdigest(
        {key: value for key, value in changed.items() if key != "evidence_fingerprint"}
    )
    path = tmp_path / "failed.json"
    path.write_text(json.dumps(changed), encoding="utf-8")

    with pytest.raises(ValueError, match="Parity comparison failed"):
        load_and_validate_parity_evidence(path, registry)
