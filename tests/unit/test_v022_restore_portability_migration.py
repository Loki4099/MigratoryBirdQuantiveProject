from __future__ import annotations

import importlib.util
from pathlib import Path

MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "migrations"
    / "versions"
    / "20260817_115_v022_restore.py"
)


def _module():  # type: ignore[no-untyped-def]
    spec = importlib.util.spec_from_file_location("v022_restore_portability", MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_portable_fingerprint_function_owns_its_search_path() -> None:
    sql = _module()._portable_fingerprint_function()
    assert "SET search_path = pg_catalog, public, strategy" in sql
    assert "public.digest(" in sql
    assert "pg_catalog.convert_to(" in sql
    assert "pg_catalog.encode(" in sql
    assert "strategy.v022_canonical_jsonb(value)" in sql


def test_restore_portability_migration_is_single_head_successor() -> None:
    module = _module()
    assert module.revision == "20260817_115_v022_restore"
    assert module.down_revision == "20260817_114_v022_prod_raw_guard"
