from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

PATH = Path("migrations/versions/20260821_136_v022_calc_ctx.py")


def _migration() -> ModuleType:
    spec = importlib.util.spec_from_file_location("v022_calc_context_migration", PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_m136_adds_frequency_neutral_context_and_exact_bindings(monkeypatch) -> None:
    module = _migration()
    statements: list[str] = []
    monkeypatch.setattr(module.op, "execute", statements.append)

    module.upgrade()

    sql = "\n".join(statements)
    assert module.revision == "20260821_136_v022_calc_ctx"
    assert len(module.revision) <= 32
    assert module.down_revision == "20260820_135_v022_cohort_gate"
    assert "processing.v022_calculation_context" in sql
    assert "processing.v022_compiled_context_calculation_binding" in sql
    assert "data.v022_calculation_context_payload_binding" in sql
    assert "dataset_publication_id" in sql
    assert "calendar_version_id" in sql
    assert "security_ids" in sql
    assert "raw_feature_version_ids" in sql
    assert "source_snapshot_artifact_ids" in sql
    assert "frequency" not in sql.lower()
    assert "processing_calculation_context" in sql
    assert "reject_record_mutation" in sql


def test_m136_downgrade_is_fail_closed(monkeypatch) -> None:
    module = _migration()
    statements: list[str] = []
    monkeypatch.setattr(module.op, "execute", statements.append)

    module.downgrade()

    sql = "\n".join(statements)
    assert "Cannot downgrade with Processing Calculation Context evidence" in sql
    assert "RAISE EXCEPTION" in sql
    assert sql.index("RAISE EXCEPTION") < sql.index(
        "DROP TABLE processing.v022_calculation_context"
    )
