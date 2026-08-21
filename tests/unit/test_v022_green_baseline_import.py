from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from style_rotation.v022 import green_baseline_import as subject


def _write_csv(root: Path, table: str, rows: list[dict[str, str]]) -> None:
    target = root / "metadata" / f"{table}.csv"
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _root(tmp_path: Path) -> Path:
    root = tmp_path / "transfer"
    root.mkdir()
    (root / "package.json").write_text(
        json.dumps({"manifest_sha256": "a" * 64}), encoding="utf-8"
    )
    records = [
        {
            "path": "dataset=risk_v5/security=aaa/year=2020/daily_bar.parquet",
            "kind": "daily_bar",
            "dataset_publication_id": subject.RISK_DATASET_ID,
            "security_id": "security-a",
            "min_date": "2020-01-02",
            "max_date": "2020-01-03",
            "row_count": 2,
        },
        {
            "path": "dataset=bench_v6/security=spy/year=2020/daily_bar.parquet",
            "kind": "daily_bar",
            "dataset_publication_id": subject.BENCHMARK_DATASET_ID,
            "security_id": "security-spy",
            "min_date": "2020-01-02",
            "max_date": "2020-01-03",
            "row_count": 2,
        },
    ]
    (root / "manifest.jsonl").write_text(
        "".join(json.dumps(item) + "\n" for item in records), encoding="utf-8"
    )
    manifest_hash = subject.hashlib.sha256((root / "manifest.jsonl").read_bytes()).hexdigest()
    (root / "package.json").write_text(
        json.dumps(
            {
                "contract": "migratory_bird_v022_green_transfer_v2",
                "manifest_sha256": manifest_hash,
                "source_datasets": [
                    subject.RISK_DATASET_ID,
                    subject.BENCHMARK_DATASET_ID,
                ],
                "metadata_policy": {"direct_copy_allowed": False},
            }
        ),
        encoding="utf-8",
    )
    (root / "verification.json").write_text(
        json.dumps({"passed": True, "errors": [], "manifest_sha256": manifest_hash}),
        encoding="utf-8",
    )
    _write_csv(
        root,
        "catalog.security",
        [
            {"security_id": "security-a", "legacy_asset_id": "asset-a"},
            {"security_id": "security-spy", "legacy_asset_id": "asset-spy"},
            {"security_id": "security-old", "legacy_asset_id": ""},
        ],
    )
    _write_csv(
        root,
        "catalog.asset",
        [
            {"asset_id": "asset-a", "asset_key": "aaa"},
            {"asset_id": "asset-spy", "asset_key": "spy"},
        ],
    )
    _write_csv(
        root,
        "catalog.v022_universe_membership_event",
        [{"security_id": "security-old"}],
    )
    _write_csv(
        root,
        "catalog.v022_security_lifecycle_event",
        [{"security_id": "security-a", "artifact_id": "old-lifecycle-artifact"}],
    )
    _write_csv(
        root,
        "catalog.v022_security_settlement_leg",
        [{"target_security_id": "security-spy"}],
    )
    _write_csv(
        root,
        "catalog.calendar_version",
        [
            {
                "calendar_version_id": "calendar-old",
                "artifact_id": "old-calendar-artifact",
                "version_number": "5",
                "library_name": "exchange_calendars",
                "library_version": "4.13.2",
                "coverage_start": "2020-01-02",
                "coverage_end": "2020-01-03",
                "session_count": "2",
            }
        ],
    )
    _write_csv(
        root,
        "catalog.calendar_session",
        [
            {"calendar_version_id": "calendar-old", "session_date": "2020-01-02"},
            {"calendar_version_id": "calendar-old", "session_date": "2020-01-03"},
        ],
    )
    _write_csv(
        root,
        "data.v022_external_import_manifest",
        [{"artifact_id": "old-import-artifact"}],
    )
    return root


def test_green_baseline_plan_scopes_market_membership_and_lifecycle(
    tmp_path: Path,
) -> None:
    root = _root(tmp_path)
    plan = subject.build_green_baseline_import_plan(root)

    assert plan.scoped_security_count == 3
    assert plan.market_security_count == 2
    assert plan.membership_security_count == 1
    assert plan.lifecycle_security_count == 2
    assert plan.scoped_asset_count == 2
    assert plan.security_without_asset_count == 1
    assert {
        (item.role, item.artifact_key, item.version_number)
        for item in plan.identities
    } == {
        (
            "transfer_manifest",
            "v022_external_import_manifest__v022_green_transfer_baseline",
            1,
        ),
        ("master_data", "research_scope", 22004),
        ("calendar", "XNYS", 1),
        ("cleaning", "adjusted_ohlc", 2),
    }
    assert [item.daily_bar_rows for item in plan.datasets] == [2, 2]
    assert {item.artifact_type for item in plan.datasets} == {"dataset_publication"}
    assert plan.calendar_source_id == "calendar-old"
    assert all("old" not in item.object_id for item in plan.identities)


def test_green_baseline_plan_rejects_market_security_without_asset(
    tmp_path: Path,
) -> None:
    root = _root(tmp_path)
    rows = subject._rows(root, "catalog.security")
    rows[0]["legacy_asset_id"] = ""
    _write_csv(root, "catalog.security", rows)
    with pytest.raises(ValueError, match="no stable Asset bridge"):
        subject.build_green_baseline_import_plan(root)


def test_green_baseline_plan_rejects_calendar_gap(
    tmp_path: Path,
) -> None:
    root = _root(tmp_path)
    _write_csv(
        root,
        "catalog.calendar_session",
        [{"calendar_version_id": "calendar-old", "session_date": "2020-01-03"}],
    )
    with pytest.raises(ValueError, match="session count"):
        subject.build_green_baseline_import_plan(root)


def test_green_baseline_plan_rejects_stale_verification_attestation(
    tmp_path: Path,
) -> None:
    root = _root(tmp_path)
    (root / "verification.json").write_text(
        json.dumps({"passed": True, "errors": [], "manifest_sha256": "0" * 64}),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="does not match"):
        subject.build_green_baseline_import_plan(root)
