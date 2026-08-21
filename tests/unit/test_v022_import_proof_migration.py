from __future__ import annotations

import importlib.util
from pathlib import Path

PATH = Path("migrations/versions/20260821_137_v022_import_proof.py")


def test_import_proof_migration_contract() -> None:
    spec = importlib.util.spec_from_file_location("m137", PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.revision == "20260821_137_v022_import_proof"
    assert module.down_revision == "20260821_136_v022_calc_ctx"
    source = PATH.read_text(encoding="utf-8")
    assert "dependency.role='external_import_manifest'" in source
    assert "import_artifact.status='published'" in source
    assert "Cannot downgrade while Calculation Context uses import source proofs" in source
