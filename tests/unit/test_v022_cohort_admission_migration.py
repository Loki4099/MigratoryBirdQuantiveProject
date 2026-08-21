from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

PATH = Path("migrations/versions/20260820_135_v022_cohort_admission.py")


def _migration() -> ModuleType:
    spec = importlib.util.spec_from_file_location("v022_cohort_admission_migration", PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_m135_inherits_exact_risk_semantics_and_requires_usable_streak(
    monkeypatch,
) -> None:
    module = _migration()
    statements: list[str] = []
    monkeypatch.setattr(module.op, "execute", statements.append)

    module.upgrade()

    sql = "\n".join(statements)
    assert module.revision == "20260820_135_v022_cohort_gate"
    assert len(module.revision) <= 32
    assert module.down_revision == "20260820_134_v022_recon_v2"
    assert "THEN market_binding.price_semantics" in sql
    assert "ELSE reconciled.price_semantics" in sql
    assert "NEW.cohort_document->>'price_semantics'" in sql
    assert "bar.volume_raw>0" in sql
    assert "ROWS BETWEEN 503 PRECEDING AND CURRENT ROW" in sql
    assert "rolling_usable_count" in sql
    assert "UPDATE " not in sql.upper()
    assert "DELETE " not in sql.upper()


def test_m135_downgrade_preserves_non_yahoo_cohorts(monkeypatch) -> None:
    module = _migration()
    statements: list[str] = []
    monkeypatch.setattr(module.op, "execute", statements.append)

    module.downgrade()

    sql = "\n".join(statements)
    assert "Cannot downgrade with non-Yahoo Evaluation Cohort price semantics" in sql
    assert "RAISE EXCEPTION" in sql
    assert "ck_v022_eval_cohort_price_semantics_v1" in sql
