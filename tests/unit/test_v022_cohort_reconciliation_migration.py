from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType


def _migration() -> ModuleType:
    path = Path("migrations/versions/20260818_117_v022_cohort_recon.py")
    spec = importlib.util.spec_from_file_location("v022_cohort_recon", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_m117_accepts_only_exact_reconciled_primary_quality_lineage(monkeypatch) -> None:
    module = _migration()
    statements: list[str] = []
    monkeypatch.setattr(module.op, "execute", statements.append)

    module.upgrade()

    sql = "\n".join(statements)
    assert module.down_revision == "20260818_116_v022_recon_guard"
    assert "data.v022_reconciled_market_dataset_binding reconciled" in sql
    assert "reconciled.dataset_artifact_id=NEW.dataset_artifact_id" in sql
    assert "reconciled.primary_dataset_publication_id" in sql
    assert "primary_binding.security_market_quality_report_id" in sql


def test_m117_downgrade_blocks_reconciled_cohorts(monkeypatch) -> None:
    module = _migration()
    statements: list[str] = []
    monkeypatch.setattr(module.op, "execute", statements.append)

    module.downgrade()

    sql = "\n".join(statements)
    assert "Cannot downgrade with reconciled Evaluation Cohorts" in sql
    assert "data.v022_reconciled_market_dataset_binding" not in statements[-1]
