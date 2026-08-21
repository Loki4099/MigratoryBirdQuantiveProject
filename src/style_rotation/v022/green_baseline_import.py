from __future__ import annotations

import csv
import hashlib
import json
import uuid
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Any, cast

_PLAN_NAMESPACE = uuid.UUID("7c689a70-4f22-46b8-96c6-2cb7003299de")
_PLAN_CONTRACT = "migratory_bird_v022_green_baseline_import_plan_v1"
_TRANSFER_CONTRACT = "migratory_bird_v022_green_transfer_v2"
RISK_DATASET_ID = "7b8940ed-85ea-5109-81c2-f8e5d7fffc78"
BENCHMARK_DATASET_ID = "528886d4-42ed-4564-a079-80379a82812b"


@dataclass(frozen=True, slots=True)
class FreshIdentity:
    role: str
    object_id: str
    artifact_type: str
    artifact_key: str
    version_number: int


@dataclass(frozen=True, slots=True)
class DatasetImportPlan:
    source_dataset_publication_id: str
    source_dataset_key: str
    source_dataset_version: int
    target_dataset_key: str
    target_dataset_version: int
    artifact_type: str
    artifact_key: str
    dataset_publication_id: str
    daily_bar_files: int
    daily_bar_rows: int
    corporate_action_files: int
    corporate_action_rows: int
    security_count: int
    asset_count: int
    coverage_start: str
    coverage_end: str


@dataclass(frozen=True, slots=True)
class GreenBaselineImportPlan:
    contract: str
    transfer_manifest_sha256: str
    plan_fingerprint: str
    source_package_name: str
    identities: tuple[FreshIdentity, ...]
    datasets: tuple[DatasetImportPlan, ...]
    calendar_source_id: str
    calendar_coverage_start: str
    calendar_coverage_end: str
    calendar_session_count: int
    scoped_security_count: int
    market_security_count: int
    membership_security_count: int
    lifecycle_security_count: int
    scoped_asset_count: int
    security_without_asset_count: int
    dependency_order: tuple[str, ...]
    forbidden_source_identity_domains: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _rows(root: Path, table: str) -> list[dict[str, str]]:
    path = root / "metadata" / f"{table}.csv"
    with path.open("r", encoding="utf-8", newline="") as source:
        return list(csv.DictReader(source))


def _nonempty(values: Iterable[str | None]) -> set[str]:
    return {value for value in values if value}


def _fresh_id(manifest_sha256: str, role: str) -> str:
    return str(uuid.uuid5(_PLAN_NAMESPACE, f"{manifest_sha256}:{role}"))


def _identity(
    manifest_sha256: str,
    role: str,
    artifact_type: str,
    artifact_key: str,
    version_number: int,
) -> FreshIdentity:
    return FreshIdentity(
        role=role,
        object_id=_fresh_id(manifest_sha256, f"object:{role}"),
        artifact_type=artifact_type,
        artifact_key=artifact_key,
        version_number=version_number,
    )


def _unique_by(
    rows: Iterable[Mapping[str, str]], key: str, label: str
) -> dict[str, Mapping[str, str]]:
    indexed: dict[str, Mapping[str, str]] = {}
    for row in rows:
        value = row.get(key, "")
        if not value:
            raise ValueError(f"{label} contains an empty {key}")
        if value in indexed and indexed[value] != row:
            raise ValueError(f"{label} contains conflicting {key}: {value}")
        indexed[value] = row
    return indexed


def _manifest_records(root: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in (root / "manifest.jsonl").read_text(encoding="utf-8").splitlines()
        if line
    ]


def _verified_transfer(root: Path, *, full_verify: bool) -> dict[str, Any]:
    if full_verify:
        from scripts.export_v022_green_transfer import verify

        return verify(root)
    package = json.loads((root / "package.json").read_text(encoding="utf-8"))
    manifest_payload = (root / "manifest.jsonl").read_bytes()
    manifest_sha256 = hashlib.sha256(manifest_payload).hexdigest()
    attestation_path = root / "verification.json"
    if not attestation_path.is_file():
        raise ValueError("transfer verification attestation is missing")
    attestation = json.loads(attestation_path.read_text(encoding="utf-8"))
    metadata_policy = package.get("metadata_policy")
    if (
        package.get("contract") != _TRANSFER_CONTRACT
        or package.get("source_datasets") != [RISK_DATASET_ID, BENCHMARK_DATASET_ID]
        or not isinstance(metadata_policy, dict)
        or metadata_policy.get("direct_copy_allowed") is not False
        or attestation.get("passed") is not True
        or attestation.get("errors") != []
        or package.get("manifest_sha256") != manifest_sha256
        or attestation.get("manifest_sha256") != manifest_sha256
    ):
        raise ValueError("transfer verification attestation does not match the package")
    return cast(dict[str, Any], attestation)


def _choose_calendar(
    root: Path, *, coverage_start: date, coverage_end: date
) -> tuple[dict[str, str], int]:
    calendars = _rows(root, "catalog.calendar_version")
    eligible = [
        item
        for item in calendars
        if date.fromisoformat(item["coverage_start"]) <= coverage_start
        and date.fromisoformat(item["coverage_end"]) >= coverage_end
    ]
    if not eligible:
        raise ValueError("no transferred calendar covers both baseline datasets")
    selected = max(eligible, key=lambda item: int(item["version_number"]))
    calendar_id = selected["calendar_version_id"]
    sessions = [
        item
        for item in _rows(root, "catalog.calendar_session")
        if item["calendar_version_id"] == calendar_id
    ]
    dates = [item["session_date"] for item in sessions]
    if len(dates) != int(selected["session_count"]):
        raise ValueError("selected calendar session count does not match its source fact")
    if dates != sorted(dates) or len(dates) != len(set(dates)):
        raise ValueError("selected calendar sessions are not unique and ordered")
    if not dates or dates[0] != selected["coverage_start"]:
        raise ValueError("selected calendar does not start on its declared first session")
    return selected, len(dates)


def build_green_baseline_import_plan(
    root: Path, *, full_verify: bool = False
) -> GreenBaselineImportPlan:
    root = root.resolve()
    verification = _verified_transfer(root, full_verify=full_verify)
    manifest_sha256 = str(verification["manifest_sha256"])
    records = _manifest_records(root)
    market_records = [
        record for record in records if record["kind"] in {"daily_bar", "corporate_action"}
    ]
    if {record["dataset_publication_id"] for record in market_records} != {
        RISK_DATASET_ID,
        BENCHMARK_DATASET_ID,
    }:
        raise ValueError("transfer contains an unexpected market dataset identity")

    security_rows = _unique_by(
        _rows(root, "catalog.security"), "security_id", "Security source facts"
    )
    asset_rows = _unique_by(_rows(root, "catalog.asset"), "asset_id", "Asset source facts")
    market_security_ids = _nonempty(record.get("security_id") for record in market_records)
    membership_security_ids = _nonempty(
        row.get("security_id")
        for row in _rows(root, "catalog.v022_universe_membership_event")
    )
    lifecycle_rows = _rows(root, "catalog.v022_security_lifecycle_event")
    settlement_rows = _rows(root, "catalog.v022_security_settlement_leg")
    lifecycle_security_ids = _nonempty(row.get("security_id") for row in lifecycle_rows)
    lifecycle_security_ids.update(
        _nonempty(row.get("target_security_id") for row in settlement_rows)
    )
    scoped_security_ids = (
        market_security_ids | membership_security_ids | lifecycle_security_ids
    )
    missing_securities = sorted(scoped_security_ids - security_rows.keys())
    if missing_securities:
        raise ValueError(f"scoped Security source facts are missing: {missing_securities[:5]}")

    market_asset_ids: set[str] = set()
    for security_id in market_security_ids:
        asset_id = security_rows[security_id].get("legacy_asset_id", "")
        if not asset_id:
            raise ValueError(f"market Security has no stable Asset bridge: {security_id}")
        market_asset_ids.add(asset_id)
    scoped_asset_ids = _nonempty(
        security_rows[security_id].get("legacy_asset_id")
        for security_id in scoped_security_ids
    )
    missing_assets = sorted(scoped_asset_ids - asset_rows.keys())
    if missing_assets:
        raise ValueError(f"market Asset source facts are missing: {missing_assets[:5]}")

    source_identity_ids = {
        row["artifact_id"]
        for table in (
            "catalog.calendar_version",
            "catalog.v022_security_lifecycle_event",
            "data.v022_external_import_manifest",
        )
        for row in _rows(root, table)
        if row.get("artifact_id")
    }

    dataset_specs = {
        RISK_DATASET_ID: ("us_sp500_free_research_frozen_v5_baseline", 1),
        BENCHMARK_DATASET_ID: ("us_etf_daily_market_frozen_v6_baseline", 1),
    }
    dataset_plans: list[DatasetImportPlan] = []
    coverage_starts: list[date] = []
    coverage_ends: list[date] = []
    for source_id, (target_key, target_version) in dataset_specs.items():
        selected = [
            record
            for record in market_records
            if record["dataset_publication_id"] == source_id
        ]
        bars = [record for record in selected if record["kind"] == "daily_bar"]
        actions = [record for record in selected if record["kind"] == "corporate_action"]
        if not bars:
            raise ValueError(f"baseline Dataset has no daily bars: {source_id}")
        source_keys = {record["path"].split("/", 1)[0] for record in selected}
        if len(source_keys) != 1:
            raise ValueError(
                "baseline Dataset partitions disagree on source identity: "
                f"{source_id}"
            )
        starts = [date.fromisoformat(record["min_date"]) for record in bars]
        ends = [date.fromisoformat(record["max_date"]) for record in bars]
        coverage_start = min(starts)
        coverage_end = max(ends)
        coverage_starts.append(coverage_start)
        coverage_ends.append(coverage_end)
        dataset_security_ids = _nonempty(record.get("security_id") for record in selected)
        dataset_asset_ids = {
            str(security_rows[item]["legacy_asset_id"]) for item in dataset_security_ids
        }
        source_segment = next(iter(source_keys))
        source_key, source_version_text = source_segment.removeprefix("dataset=").rsplit("_v", 1)
        dataset_plans.append(
            DatasetImportPlan(
                source_dataset_publication_id=source_id,
                source_dataset_key=source_key,
                source_dataset_version=int(source_version_text),
                target_dataset_key=target_key,
                target_dataset_version=target_version,
                artifact_type="dataset_publication",
                artifact_key=target_key,
                dataset_publication_id=_fresh_id(manifest_sha256, f"object:dataset:{target_key}"),
                daily_bar_files=len(bars),
                daily_bar_rows=sum(int(record["row_count"]) for record in bars),
                corporate_action_files=len(actions),
                corporate_action_rows=sum(int(record["row_count"]) for record in actions),
                security_count=len(dataset_security_ids),
                asset_count=len(dataset_asset_ids),
                coverage_start=coverage_start.isoformat(),
                coverage_end=coverage_end.isoformat(),
            )
        )

    calendar, session_count = _choose_calendar(
        root,
        coverage_start=min(coverage_starts),
        coverage_end=max(coverage_ends),
    )
    identities = (
        _identity(
            manifest_sha256,
            "transfer_manifest",
            "v022_external_import_manifest",
            "v022_external_import_manifest__v022_green_transfer_baseline",
            1,
        ),
        _identity(
            manifest_sha256,
            "master_data",
            "catalog_master_data_release",
            "research_scope",
            22004,
        ),
        _identity(
            manifest_sha256,
            "calendar",
            "calendar_version",
            "XNYS",
            1,
        ),
        _identity(
            manifest_sha256,
            "cleaning",
            "cleaning_version",
            "adjusted_ohlc",
            2,
        ),
    )
    fresh_ids = {identity.object_id for identity in identities} | {
        dataset.dataset_publication_id for dataset in dataset_plans
    }
    if fresh_ids & source_identity_ids:
        raise ValueError("fresh green identity collides with transferred Artifact identity")

    fingerprint_payload = {
        "contract": _PLAN_CONTRACT,
        "manifest_sha256": manifest_sha256,
        "identities": [asdict(item) for item in identities],
        "datasets": [asdict(item) for item in dataset_plans],
        "calendar_source_id": calendar["calendar_version_id"],
        "scoped_security_ids": sorted(scoped_security_ids),
        "scoped_asset_ids": sorted(scoped_asset_ids),
    }
    fingerprint = hashlib.sha256(
        json.dumps(fingerprint_payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    without_asset = sum(
        not security_rows[security_id].get("legacy_asset_id")
        for security_id in scoped_security_ids
    )
    return GreenBaselineImportPlan(
        contract=_PLAN_CONTRACT,
        transfer_manifest_sha256=manifest_sha256,
        plan_fingerprint=fingerprint,
        source_package_name=root.name,
        identities=identities,
        datasets=tuple(dataset_plans),
        calendar_source_id=calendar["calendar_version_id"],
        calendar_coverage_start=calendar["coverage_start"],
        calendar_coverage_end=calendar["coverage_end"],
        calendar_session_count=session_count,
        scoped_security_count=len(scoped_security_ids),
        market_security_count=len(market_security_ids),
        membership_security_count=len(membership_security_ids),
        lifecycle_security_count=len(lifecycle_security_ids),
        scoped_asset_count=len(scoped_asset_ids),
        security_without_asset_count=without_asset,
        dependency_order=(
            "transfer_manifest",
            "master_data",
            "calendar",
            "cleaning",
            "risk_dataset",
            "benchmark_dataset",
            "universe_and_lifecycle_republication",
            "gate_cohort_runtime_registry",
        ),
        forbidden_source_identity_domains=(
            "lineage.artifact",
            "data.dataset_publication",
            "data.v022_dataset_gate_assessment",
            "experiment.v022_evaluation_cohort_version",
            "product.v022_product_enrollment",
        ),
    )


def write_green_baseline_import_plan(
    root: Path, output: Path, *, full_verify: bool = False
) -> GreenBaselineImportPlan:
    plan = build_green_baseline_import_plan(root, full_verify=full_verify)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(plan.to_dict(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return plan
