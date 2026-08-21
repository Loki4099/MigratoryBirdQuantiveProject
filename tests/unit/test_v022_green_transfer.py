from __future__ import annotations

import pytest

from scripts.export_v022_green_transfer import (
    FORBIDDEN_METADATA_DOMAINS,
    SOURCE_FACT_TABLES,
    _validate_transfer_contract,
)


def _records() -> list[dict[str, str]]:
    return [
        {"kind": "metadata", "path": f"metadata/{table}.csv"}
        for table in SOURCE_FACT_TABLES
    ]


def _package() -> dict[str, object]:
    return {
        "contract": "migratory_bird_v022_green_transfer_v2",
        "metadata_policy": {
            "mode": "source_facts_only",
            "tables": list(SOURCE_FACT_TABLES),
            "forbidden_prefixes": list(FORBIDDEN_METADATA_DOMAINS),
            "direct_copy_allowed": False,
        },
    }


def test_green_transfer_contract_accepts_only_source_facts() -> None:
    _validate_transfer_contract(_package(), _records())


def test_green_transfer_contract_rejects_old_runtime_projection() -> None:
    package = _package()
    policy = dict(package["metadata_policy"])  # type: ignore[arg-type]
    policy["tables"] = [*SOURCE_FACT_TABLES, "experiment.v022_evaluation_cohort_version"]
    package["metadata_policy"] = policy

    with pytest.raises(ValueError, match="allowlist mismatch"):
        _validate_transfer_contract(package, _records())


def test_green_transfer_contract_rejects_legacy_v1_package() -> None:
    package = _package()
    package["contract"] = "migratory_bird_v022_green_transfer_v1"

    with pytest.raises(ValueError, match="legacy green transfer"):
        _validate_transfer_contract(package, _records())


def test_green_transfer_contract_rejects_missing_source_fact_file() -> None:
    with pytest.raises(ValueError, match="metadata file allowlist mismatch"):
        _validate_transfer_contract(_package(), _records()[:-1])
