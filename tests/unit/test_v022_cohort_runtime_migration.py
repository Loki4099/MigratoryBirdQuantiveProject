from __future__ import annotations

from pathlib import Path

MIGRATION = Path("migrations/versions/20260817_106_v022_cohort_runtime.py")


def test_m106_freezes_gate_mask_lifecycle_and_settlement() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    assert 'down_revision = "20260817_105_v022_dataset_gate"' in source
    assert "v022_evaluation_cohort_runtime_contract" in source
    assert "v022_cohort_runtime_mask_interval" in source
    assert "v022_cohort_settlement_instruction" in source
    assert "dataset_gate_fingerprint" in source
    assert "ordinary_index_removal" not in source
    assert "lifecycle_event_count+2" in source
    assert "DEFERRABLE INITIALLY DEFERRED" in source
    assert "reject_record_mutation" in source


def test_m106_downgrade_is_fail_closed_when_contracts_exist() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    assert "Cannot downgrade M106 with published Cohort runtime contracts" in source
