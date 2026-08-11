from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pandas as pd
import pytest
from sqlalchemy import create_engine

from style_rotation.core.canonical import sha256_hexdigest
from style_rotation.experiment.result_payload import CellResultPayloadStore
from style_rotation.storage.maintenance import (
    CacheAction,
    CacheDecision,
    CacheEntrySnapshot,
    CacheMaintenancePlan,
    CacheMaintenancePolicy,
    CellPayloadAction,
    CellPayloadMaintenancePolicy,
    ManifestRetentionClass,
    StorageMaintenancePlan,
    StorageMaintenanceService,
    _CellPayloadReferenceMarks,
    plan_cache_retention,
)

NOW = datetime(2026, 8, 9, 12, tzinfo=UTC)


def _entry(
    key_character: str,
    *,
    age: timedelta,
    size: int,
    product: bool = False,
    active: bool = False,
    path_valid: bool = True,
) -> CacheEntrySnapshot:
    key = key_character * 64
    return CacheEntrySnapshot(
        cache_key=key,
        storage_uri=f"/cache/{key}.parquet",
        created_at=NOW - age,
        last_accessed_at=NOW - age,
        parquet_bytes=size,
        manifest_bytes=0,
        product_referenced=product,
        active_referenced=active,
        path_valid=path_valid,
    )


def test_cache_planner_pins_then_applies_ttl_and_lru_quota() -> None:
    entries = (
        _entry("a", age=timedelta(days=30), size=80, product=True),
        _entry("b", age=timedelta(days=20), size=30),
        _entry("c", age=timedelta(days=5), size=40),
        _entry("d", age=timedelta(days=4), size=40),
    )

    plan = plan_cache_retention(
        entries,
        policy=CacheMaintenancePolicy(
            ttl=timedelta(days=14), quota_bytes=120, recent_access_grace=timedelta(0)
        ),
        now=NOW,
    )

    decisions = {item.cache_key: item for item in plan.decisions}
    assert decisions["a" * 64].action == CacheAction.KEEP
    assert decisions["a" * 64].reason == "product_reference"
    assert decisions["b" * 64].reason == "ttl_expired"
    assert decisions["c" * 64].reason == "lru_quota"
    assert decisions["d" * 64].action == CacheAction.KEEP
    assert plan.before_bytes == 190
    assert plan.after_bytes == 120
    assert plan.reclaim_bytes == 70


def test_cache_planner_fails_closed_for_incomplete_references_and_invalid_paths() -> None:
    entries = (
        _entry("a", age=timedelta(days=100), size=20),
        _entry("b", age=timedelta(days=100), size=20, path_valid=False),
    )

    incomplete = plan_cache_retention(
        entries,
        policy=CacheMaintenancePolicy(quota_bytes=0),
        now=NOW,
        inventory_complete=False,
    )
    assert not incomplete.delete_keys
    assert "cache_reference_inventory_incomplete" in incomplete.blocked_reasons
    assert "quota_exceeded_by_pinned_or_fail_closed_entries" in incomplete.blocked_reasons

    complete = plan_cache_retention(
        entries,
        policy=CacheMaintenancePolicy(quota_bytes=20),
        now=NOW,
    )
    decisions = {item.cache_key: item for item in complete.decisions}
    assert decisions["a" * 64].action == CacheAction.DELETE
    assert decisions["b" * 64].reason == "invalid_storage_path_fail_closed"


def test_archive_restore_verification_detects_content_and_metadata_tampering(
    tmp_path: Path,
) -> None:
    manifest = {
        "root_artifact_id": str(uuid.uuid4()),
        "artifacts": [{"artifact_id": str(uuid.uuid4()), "version_number": 1}],
        "dependencies": [],
    }
    record = {
        "lineage_manifest_id": str(uuid.uuid4()),
        "root_artifact_id": manifest["root_artifact_id"],
        "artifact_type": "v021_cell_result",
        "artifact_key": "old-result",
        "semantic_fingerprint": "a" * 64,
        "content_hash": "b" * 64,
        "manifest_hash": sha256_hexdigest(manifest),
        "canonical_version": "canonical_json_v1",
        "created_at": NOW.isoformat(),
        "manifest_json": __import__("json").dumps(
            manifest, sort_keys=True, separators=(",", ":")
        ),
    }
    path = tmp_path / "archive.parquet"
    pd.DataFrame.from_records([record]).to_parquet(
        path, engine="pyarrow", compression="zstd", index=False
    )
    digest = StorageMaintenanceService._sha256_file(path)
    StorageMaintenanceService._verify_archive(path, expected=[record], archive_sha256=digest)

    tampered = dict(record)
    tampered["artifact_key"] = "different"
    pd.DataFrame.from_records([tampered]).to_parquet(
        path, engine="pyarrow", compression="zstd", index=False
    )
    tampered_digest = StorageMaintenanceService._sha256_file(path)
    with pytest.raises(RuntimeError, match="metadata mismatch"):
        StorageMaintenanceService._verify_archive(
            path,
            expected=[record],
            archive_sha256=tampered_digest,
        )


def test_storage_execution_requires_exact_plan_token_and_safe_cache_path(
    tmp_path: Path,
) -> None:
    service = StorageMaintenanceService(
        create_engine("sqlite://"),
        cache_directory=tmp_path,
        archive_directory=tmp_path / "archive",
    )
    cache_key = "a" * 64
    valid_path = tmp_path / f"{cache_key}.parquet"
    paths, valid = service._cache_paths(cache_key, valid_path.as_posix())
    assert valid
    assert paths[0] == valid_path.resolve()
    _paths, escaped = service._cache_paths(cache_key, (tmp_path.parent / "escape").as_posix())
    assert not escaped

    cache = CacheMaintenancePlan(
        policy=CacheMaintenancePolicy(),
        decisions=(
            CacheDecision(
                cache_key,
                CacheAction.DELETE,
                "ttl_expired",
                1,
                valid_path.as_posix(),
                NOW - timedelta(days=30),
            ),
        ),
        before_bytes=1,
        after_bytes=0,
    )
    plan = StorageMaintenancePlan(
        generated_at=NOW,
        plan_id="f" * 64,
        product_pinned_artifact_count=0,
        current_suite_ids=(),
        manifests=(),
        cache=cache,
    )
    with pytest.raises(PermissionError, match="exact dry-run"):
        service._require_confirmation(plan, "wrong")
    service._require_confirmation(plan, plan.confirmation_token)

    blocked = StorageMaintenancePlan(
        generated_at=NOW,
        plan_id="e" * 64,
        product_pinned_artifact_count=0,
        current_suite_ids=(),
        manifests=(),
        cache=cache,
        blocked_reasons=(ManifestRetentionClass.SHARED_OR_UNCLASSIFIED.value,),
    )
    with pytest.raises(RuntimeError, match="fail-closed"):
        service._require_confirmation(blocked, blocked.confirmation_token)


def _payload_service(tmp_path: Path) -> tuple[StorageMaintenanceService, CellResultPayloadStore]:
    directory = tmp_path / "cell-results"
    return (
        StorageMaintenanceService(
            create_engine("sqlite://"),
            cache_directory=tmp_path / "cache",
            archive_directory=tmp_path / "archive",
            cell_payload_directory=directory,
        ),
        CellResultPayloadStore(directory),
    )


def _set_old(path: Path) -> None:
    timestamp = (NOW - timedelta(days=30)).timestamp()
    os.utime(path, (timestamp, timestamp))


def test_cell_payload_plan_round_trip_quarantines_and_rolls_back_orphan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, store = _payload_service(tmp_path)
    work_item_id = uuid.uuid4()
    lease = store.stage_publication(
        series={"nav_series": [{"date": "2026-08-01", "nav": 1.0}]},
        diagnostics={"executor": "test"},
        owner_work_item_id=work_item_id,
    )
    payload_path = tmp_path / "cell-results" / f"{lease.payload.content_hash}.parquet"
    marker_path = tmp_path / "cell-results" / lease.marker_name
    _set_old(payload_path)
    _set_old(marker_path)
    monkeypatch.setattr(
        service,
        "_cell_payload_reference_marks",
        lambda _connection: _CellPayloadReferenceMarks(),
    )

    plan = service.dry_run_cell_payloads(
        policy=CellPayloadMaintenancePolicy(grace_period=timedelta(days=7)),
        now=NOW,
    )
    assert plan.quarantine_hashes == (lease.payload.content_hash,)
    assert plan.pending_markers[0].action == CellPayloadAction.QUARANTINE
    document_path = tmp_path / "maintenance-plan.json"
    service.write_cell_payload_plan(plan, document_path)
    loaded = service.load_cell_payload_plan(document_path)
    assert loaded == plan
    tampered_plan = plan.to_document()
    tampered_plan["decisions"][0]["byte_size"] += 1
    with pytest.raises(ValueError, match="hash mismatch"):
        type(plan).from_document(tampered_plan)
    with pytest.raises(PermissionError, match="exact full-plan"):
        service.execute_cell_payload_quarantine(loaded, confirmation_token="wrong")

    receipt = service.execute_cell_payload_quarantine(
        loaded, confirmation_token=loaded.confirmation_token
    )
    assert not payload_path.exists()
    assert not marker_path.exists()
    assert len(receipt.items) == 2
    assert all(Path(item.quarantine_path).is_file() for item in receipt.items)
    receipt_path = Path(receipt.quarantine_directory) / "receipt-copy.json"
    service.write_cell_payload_receipt(receipt, receipt_path)
    loaded_receipt = service.load_cell_payload_receipt(receipt_path)
    tampered_receipt = receipt.to_document()
    tampered_receipt["items"][0]["byte_size"] += 1
    with pytest.raises(ValueError, match="hash mismatch"):
        type(receipt).from_document(tampered_receipt)
    with pytest.raises(PermissionError, match="exact receipt"):
        service.rollback_cell_payload_quarantine(
            loaded_receipt, confirmation_token="wrong"
        )
    assert service.rollback_cell_payload_quarantine(
        loaded_receipt, confirmation_token=loaded_receipt.rollback_token
    ) == 2
    assert payload_path.is_file()
    assert marker_path.is_file()


def test_stale_marker_for_committed_payload_is_quarantined_without_payload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, store = _payload_service(tmp_path)
    lease = store.stage_publication(
        series={"model_scores": []},
        diagnostics={"executor": "test"},
        owner_work_item_id=uuid.uuid4(),
    )
    payload_path = tmp_path / "cell-results" / f"{lease.payload.content_hash}.parquet"
    marker_path = tmp_path / "cell-results" / lease.marker_name
    _set_old(payload_path)
    _set_old(marker_path)
    referenced = _CellPayloadReferenceMarks(referenced={lease.payload.content_hash})
    monkeypatch.setattr(
        service, "_cell_payload_reference_marks", lambda _connection: referenced
    )

    plan = service.dry_run_cell_payloads(now=NOW)
    assert not plan.quarantine_hashes
    assert plan.pending_markers[0].reason == "stale_marker_reference_committed"
    receipt = service.execute_cell_payload_quarantine(
        plan, confirmation_token=plan.confirmation_token
    )
    assert payload_path.is_file()
    assert not marker_path.exists()
    assert [item.kind for item in receipt.items] == ["pending_marker"]


def test_active_or_fresh_pending_marker_prevents_orphan_quarantine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, store = _payload_service(tmp_path)
    work_item_id = uuid.uuid4()
    lease = store.stage_publication(
        series={"model_scores": []},
        diagnostics={"executor": "test"},
        owner_work_item_id=work_item_id,
    )
    payload_path = tmp_path / "cell-results" / f"{lease.payload.content_hash}.parquet"
    marker_path = tmp_path / "cell-results" / lease.marker_name
    _set_old(payload_path)
    _set_old(marker_path)
    marks = _CellPayloadReferenceMarks(active_work_items={work_item_id})
    monkeypatch.setattr(service, "_cell_payload_reference_marks", lambda _c: marks)

    active_plan = service.dry_run_cell_payloads(now=NOW)
    assert not active_plan.quarantine_hashes
    assert active_plan.pending_markers[0].reason == "active_work_publication"

    recent = (NOW - timedelta(days=1)).timestamp()
    os.utime(marker_path, (recent, recent))
    monkeypatch.setattr(
        service,
        "_cell_payload_reference_marks",
        lambda _c: _CellPayloadReferenceMarks(),
    )
    fresh_plan = service.dry_run_cell_payloads(now=NOW)
    assert not fresh_plan.quarantine_hashes
    assert fresh_plan.pending_markers[0].reason == "publication_in_progress"


def test_cell_payload_execute_aborts_if_reference_appears_after_plan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, store = _payload_service(tmp_path)
    external = store.externalize(series={"model_scores": []}, diagnostics={})
    payload_path = tmp_path / "cell-results" / f"{external.content_hash}.parquet"
    _set_old(payload_path)
    monkeypatch.setattr(
        service,
        "_cell_payload_reference_marks",
        lambda _c: _CellPayloadReferenceMarks(),
    )
    plan = service.dry_run_cell_payloads(now=NOW)
    assert plan.quarantine_hashes == (external.content_hash,)

    monkeypatch.setattr(
        service,
        "_cell_payload_reference_marks",
        lambda _c: _CellPayloadReferenceMarks(referenced={external.content_hash}),
    )
    with pytest.raises(RuntimeError, match="became referenced"):
        service.execute_cell_payload_quarantine(
            plan, confirmation_token=plan.confirmation_token
        )
    assert payload_path.is_file()


def test_cell_payload_reference_query_failure_is_fail_closed(tmp_path: Path) -> None:
    service, store = _payload_service(tmp_path)
    external = store.externalize(series={"model_scores": []}, diagnostics={})
    payload_path = tmp_path / "cell-results" / f"{external.content_hash}.parquet"
    _set_old(payload_path)

    plan = service.dry_run_cell_payloads(now=NOW)

    assert not plan.quarantine_hashes
    assert "cell_payload_reference_inventory_incomplete" in plan.blocked_reasons
    assert any(
        reason.startswith("cell_payload_reference_query_failed:")
        for reason in plan.blocked_reasons
    )
