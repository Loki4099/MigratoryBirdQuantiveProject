from __future__ import annotations

import csv
import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Any, cast

import pandas as pd

from style_rotation.v022.historical_universe import (
    HistoricalMembershipSnapshot,
    parse_fja_snapshot_csv,
)

_DATASET_VERSION = "sp500-pit-free-research-2013warmup-2018eval-2026-v3-final-candidate"
_SOURCE_RELATIVE_PATH = Path(
    "data/external/fja05680/sp500_historical_components_updated.csv"
)
_LICENSE_RELATIVE_PATH = Path("data/external/fja05680/LICENSE")
_SOURCE_README_RELATIVE_PATH = Path("data/external/fja05680/SOURCE_README.md")
_EXPECTED_SOURCE_LICENSE = "MIT License"


@dataclass(frozen=True, slots=True)
class Sp500CandidateDates:
    warmup_start: date
    evaluation_start: date
    evaluation_end: date
    required_warmup_sessions: int = 504

    def __post_init__(self) -> None:
        if not self.warmup_start < self.evaluation_start <= self.evaluation_end:
            raise ValueError("S&P 500 candidate dates must be strictly ordered")
        if self.required_warmup_sessions < 1:
            raise ValueError("Required warm-up sessions must be positive")


@dataclass(frozen=True, slots=True)
class ManifestIntegrity:
    expected_sha256: str
    actual_sha256: str
    object_count: int
    existing_count: int
    matching_size_count: int
    matching_sha256_count: int
    mismatched_logical_objects: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class MembershipEvidence:
    source_sha256: str
    license_sha256: str
    source_readme_sha256: str
    license_key: str
    source_snapshot_count: int
    source_coverage_start: date
    source_coverage_end: date
    candidate_snapshot_count: int
    candidate_initial_snapshot_date: date
    candidate_unique_source_symbols: int
    candidate_member_count_min: int
    candidate_member_count_max: int
    mapped_source_symbols: int
    unmapped_source_symbol_count: int
    unmapped_source_symbols: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class FrozenMarketEvidence:
    dataset_version: str
    freeze_status: str
    research_tier: str
    formal_eligible: bool
    price_coverage_start: date
    price_coverage_end: date
    price_row_count: int
    stable_security_count: int
    provider_counts: dict[str, int]
    unavailable_security_count: int


@dataclass(frozen=True, slots=True)
class HistoricalIdentityReviewItem:
    source_symbol: str
    first_observed_session: date
    last_observed_session: date
    observed_snapshot_count: int
    membership_episode_count: int
    resolution_status: str = "unresolved"
    reason_code: str = "historical_security_identity_missing"


@dataclass(frozen=True, slots=True)
class Sp500SeedAuditReport:
    schema_version: str
    candidate_dates: Sp500CandidateDates
    manifest: ManifestIntegrity
    membership: MembershipEvidence
    market: FrozenMarketEvidence
    source_membership_importable: bool
    derived_market_seed_directly_rankable: bool
    decision: str
    blockers: tuple[str, ...]
    next_actions: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return cast(dict[str, Any], json.loads(json.dumps(asdict(self), default=str)))


def audit_unmapped_historical_identities(
    *,
    runtime_root: Path,
    candidate_dates: Sp500CandidateDates,
) -> tuple[HistoricalIdentityReviewItem, ...]:
    """Build a deterministic review queue without inferring Security continuity."""
    runtime_root = runtime_root.resolve()
    source_path = runtime_root / _SOURCE_RELATIVE_PATH
    identity_resolution_path = (
        runtime_root
        / "data"
        / "quality"
        / _DATASET_VERSION
        / "security_identity_resolution.csv"
    )
    snapshots = parse_fja_snapshot_csv(source_path.read_text(encoding="utf-8-sig"))
    candidate = _candidate_snapshots(snapshots, candidate_dates)
    mapped = _mapped_source_symbols(identity_resolution_path)
    source_spellings = {
        symbol.casefold(): symbol for item in candidate for symbol in item.source_symbols
    }
    unresolved = tuple(sorted(set(source_spellings).difference(mapped)))
    items: list[HistoricalIdentityReviewItem] = []
    for folded in unresolved:
        observed = tuple(
            item.effective_session
            for item in candidate
            if any(symbol.casefold() == folded for symbol in item.source_symbols)
        )
        episode_count = 0
        previously_present = False
        for item in candidate:
            present = any(symbol.casefold() == folded for symbol in item.source_symbols)
            episode_count += int(present and not previously_present)
            previously_present = present
        items.append(
            HistoricalIdentityReviewItem(
                source_symbol=source_spellings[folded],
                first_observed_session=observed[0],
                last_observed_session=observed[-1],
                observed_snapshot_count=len(observed),
                membership_episode_count=episode_count,
            )
        )
    return tuple(sorted(items, key=lambda item: item.source_symbol.casefold()))


def audit_sp500_seed(
    *,
    runtime_root: Path,
    source_project_root: Path,
    candidate_dates: Sp500CandidateDates,
) -> Sp500SeedAuditReport:
    """Verify the external seed without writing files or connecting to a database."""
    runtime_root = runtime_root.resolve()
    source_project_root = source_project_root.resolve()
    data_root = runtime_root / "data"
    manifest_path = data_root / "manifests" / f"{_DATASET_VERSION}.json"
    freeze_path = source_project_root / "metadata" / "frozen_dataset" / "FROZEN.json"
    source_path = runtime_root / _SOURCE_RELATIVE_PATH
    license_path = runtime_root / _LICENSE_RELATIVE_PATH
    source_readme_path = runtime_root / _SOURCE_README_RELATIVE_PATH
    curated_root = data_root / "curated" / _DATASET_VERSION
    quality_root = data_root / "quality" / _DATASET_VERSION
    required = (
        manifest_path,
        freeze_path,
        source_path,
        license_path,
        source_readme_path,
        curated_root / "prices_daily.parquet",
        curated_root / "security_master.parquet",
        quality_root / "security_identity_resolution.csv",
    )
    missing = tuple(path.name for path in required if not path.is_file())
    if missing:
        raise FileNotFoundError(f"S&P 500 seed evidence is incomplete: {missing}")

    freeze = _json_object(freeze_path)
    manifest = _json_object(manifest_path)
    expected_manifest_sha = _required_text(
        cast(dict[str, Any], freeze.get("manifest", {})).get("sha256"),
        "freeze manifest SHA-256",
    )
    actual_manifest_sha = _sha256(manifest_path)
    integrity = _manifest_integrity(
        manifest,
        data_root=data_root,
        expected_sha256=expected_manifest_sha,
        actual_sha256=actual_manifest_sha,
    )
    membership = _membership_evidence(
        source_path,
        license_path,
        source_readme_path,
        quality_root / "security_identity_resolution.csv",
        candidate_dates,
    )
    market = _market_evidence(freeze, curated_root)

    source_importable = (
        integrity.expected_sha256 == integrity.actual_sha256
        and membership.license_key == "MIT"
        and membership.source_coverage_start <= candidate_dates.warmup_start
        and membership.source_coverage_end >= candidate_dates.evaluation_end
    )
    blockers: list[str] = []
    if integrity.matching_sha256_count != integrity.object_count:
        blockers.append(
            "frozen_manifest_object_drift: exact historical builder bytes are not all present"
        )
    if membership.unmapped_source_symbol_count:
        blockers.append(
            "historical_security_identity_incomplete: "
            f"{membership.unmapped_source_symbol_count} candidate symbols lack stable mappings"
        )
    if market.price_coverage_start > candidate_dates.warmup_start:
        blockers.append(
            "candidate_price_history_missing: frozen prices begin "
            f"{market.price_coverage_start.isoformat()}, after warmup_start "
            f"{candidate_dates.warmup_start.isoformat()}"
        )
    if market.price_coverage_end < candidate_dates.evaluation_end:
        blockers.append("candidate_price_history_ends_before_evaluation_end")
    if market.unavailable_security_count:
        blockers.append(
            "provider_unavailable_securities_require_uniform_exclusion_evidence: "
            f"{market.unavailable_security_count}"
        )
    if not market.formal_eligible:
        blockers.append("source_dataset_is_explicitly_not_formal_eligible")
    decision = "blocked_before_publication" if blockers else "eligible_for_publication_review"
    return Sp500SeedAuditReport(
        schema_version="v0.22.sp500_seed_audit.v1",
        candidate_dates=candidate_dates,
        manifest=integrity,
        membership=membership,
        market=market,
        source_membership_importable=source_importable,
        derived_market_seed_directly_rankable=False,
        decision=decision,
        blockers=tuple(blockers),
        next_actions=(
            "publish content-addressed membership, license, repair-ledger and raw-source evidence",
            (
                "create exact stable Security and canonical Asset identities "
                "for every candidate symbol"
            ),
            "acquire and freeze missing 2004-12-31 through 2012-12-31 Yahoo coverage",
            "rebuild the complete v0.22 market Dataset and run terminal/gap QA",
            "ask the user to confirm exact cohort dates only after the coverage report passes",
        ),
    )


def _manifest_integrity(
    manifest: dict[str, Any],
    *,
    data_root: Path,
    expected_sha256: str,
    actual_sha256: str,
) -> ManifestIntegrity:
    raw_records = manifest.get("files")
    if not isinstance(raw_records, list) or not raw_records:
        raise ValueError("Frozen manifest must contain file records")
    existing = 0
    sizes = 0
    hashes = 0
    mismatches: list[str] = []
    for ordinal, raw_record in enumerate(raw_records):
        if not isinstance(raw_record, dict):
            raise ValueError("Frozen manifest file record must be an object")
        recorded_path = Path(_required_text(raw_record.get("path"), "manifest file path"))
        resolved = recorded_path if recorded_path.is_absolute() else data_root / recorded_path
        logical_key = _logical_object_key(recorded_path, ordinal)
        if not resolved.is_file():
            mismatches.append(logical_key)
            continue
        existing += 1
        expected_size = int(raw_record.get("size_bytes", -1))
        expected_hash = _required_text(raw_record.get("sha256"), "manifest object SHA-256")
        size_matches = resolved.stat().st_size == expected_size
        hash_matches = _sha256(resolved) == expected_hash
        sizes += int(size_matches)
        hashes += int(hash_matches)
        if not size_matches or not hash_matches:
            mismatches.append(logical_key)
    return ManifestIntegrity(
        expected_sha256,
        actual_sha256,
        len(raw_records),
        existing,
        sizes,
        hashes,
        tuple(mismatches),
    )


def _membership_evidence(
    source_path: Path,
    license_path: Path,
    source_readme_path: Path,
    identity_resolution_path: Path,
    candidate_dates: Sp500CandidateDates,
) -> MembershipEvidence:
    payload = source_path.read_text(encoding="utf-8-sig")
    snapshots = parse_fja_snapshot_csv(payload)
    if not snapshots:
        raise ValueError("Historical membership source is empty")
    license_text = license_path.read_text(encoding="utf-8-sig")
    if not license_text.startswith(_EXPECTED_SOURCE_LICENSE):
        raise ValueError(
            "Historical membership source is not accompanied by the expected MIT License"
        )
    candidate = _candidate_snapshots(snapshots, candidate_dates)
    symbols = {symbol.casefold(): symbol for item in candidate for symbol in item.source_symbols}
    mapped = _mapped_source_symbols(identity_resolution_path)
    unmapped = tuple(
        sorted(
            (symbols[key] for key in set(symbols).difference(mapped)),
            key=str.casefold,
        )
    )
    counts = tuple(len(item.source_symbols) for item in candidate)
    return MembershipEvidence(
        _sha256(source_path),
        _sha256(license_path),
        _sha256(source_readme_path),
        "MIT",
        len(snapshots),
        snapshots[0].effective_session,
        snapshots[-1].effective_session,
        len(candidate),
        candidate[0].effective_session,
        len(symbols),
        min(counts),
        max(counts),
        len(set(symbols).intersection(mapped)),
        len(unmapped),
        unmapped,
    )


def _candidate_snapshots(
    snapshots: tuple[HistoricalMembershipSnapshot, ...],
    candidate_dates: Sp500CandidateDates,
) -> tuple[HistoricalMembershipSnapshot, ...]:
    initial = tuple(
        item for item in snapshots if item.effective_session <= candidate_dates.warmup_start
    )
    if not initial:
        raise ValueError("Historical membership lacks a snapshot at or before warmup_start")
    return (initial[-1],) + tuple(
        item
        for item in snapshots
        if candidate_dates.warmup_start
        < item.effective_session
        <= candidate_dates.evaluation_end
    )


def _mapped_source_symbols(path: Path) -> frozenset[str]:
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        rows = csv.DictReader(stream)
        if rows.fieldnames is None or "source_sid" not in rows.fieldnames:
            raise ValueError("Security identity resolution schema is invalid")
        return frozenset(
            value[len("yf_ticker::") :].casefold()
            for row in rows
            if (value := str(row["source_sid"])).startswith("yf_ticker::")
        )


def _market_evidence(freeze: dict[str, Any], curated_root: Path) -> FrozenMarketEvidence:
    prices = pd.read_parquet(curated_root / "prices_daily.parquet", columns=["date", "sid"])
    security_master = pd.read_parquet(
        curated_root / "security_master.parquet", columns=["sid", "provider"]
    )
    if prices.empty or security_master.empty:
        raise ValueError("Frozen market seed is empty")
    provider_counts = {
        str(key): int(value)
        for key, value in security_master["provider"].value_counts(dropna=False).items()
    }
    return FrozenMarketEvidence(
        _required_text(freeze.get("dataset_version"), "frozen dataset version"),
        _required_text(freeze.get("freeze_status"), "freeze status"),
        _required_text(freeze.get("research_tier"), "research tier"),
        bool(freeze.get("formal_eligible")),
        cast(date, pd.Timestamp(prices["date"].min()).date()),
        cast(date, pd.Timestamp(prices["date"].max()).date()),
        len(prices),
        int(security_master["sid"].nunique()),
        provider_counts,
        provider_counts.get("unavailable", 0),
    )


def _json_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object: {path.name}")
    return cast(dict[str, Any], value)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def _required_text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} is required")
    return value.strip()


def _logical_object_key(path: Path, ordinal: int) -> str:
    parts = tuple(part.casefold() for part in path.parts)
    if "scripts" in parts or "src" in parts or path.name == "pyproject.toml":
        category = "builder_source"
    elif "curated" in parts:
        category = "curated_data"
    elif "raw" in parts:
        category = "raw_data"
    elif "quality" in parts or "input" in parts:
        category = "quality_evidence"
    elif "manifests" in parts:
        category = "parent_manifest"
    else:
        category = "other"
    return f"{ordinal}:{category}:{path.name}"
