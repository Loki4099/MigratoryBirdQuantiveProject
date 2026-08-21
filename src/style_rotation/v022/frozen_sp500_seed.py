from __future__ import annotations

import hashlib
import json
import re
import uuid
from contextlib import nullcontext
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, cast

import pyarrow.parquet as parquet  # type: ignore[import-untyped]
from sqlalchemy import Engine, text
from sqlalchemy.engine import Connection

from style_rotation.lineage.service import ArtifactService, DependencyInput
from style_rotation.v022.data_seed_import import (
    ExternalImportManifestPublication,
    ExternalImportManifestService,
    ExternalImportManifestSpec,
    ExternalImportObjectSpec,
    ProviderSecurityIdentityService,
)
from style_rotation.v022.historical_universe import (
    HistoricalMembershipSnapshot,
    HistoricalSp500UniversePublication,
    HistoricalSp500UniversePublicationService,
    HistoricalSp500UniverseSpec,
    MembershipSecurityMapping,
)

DATASET_VERSION = "sp500-pit-free-research-2004warmup-2007eval-2026-v4-candidate"
RAW_MEMBERSHIP_LOGICAL_KEY = "fja05680_sp500_historical_components"
CURATED_MEMBERSHIP_LOGICAL_KEY = (
    f"curated__{DATASET_VERSION}__membership.parquet"
)
_IDENTITY_CONTRACT = "v0.22.frozen_sp500_security_identity.v1"
_IDENTITY_RELEASE_KEY = "v022_sp500_frozen_asset_identity"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True, slots=True)
class FrozenSp500Paths:
    runtime_root: Path
    source_project_root: Path

    @property
    def data_root(self) -> Path:
        if (self.runtime_root / "manifests" / f"{DATASET_VERSION}.json").is_file():
            return self.runtime_root
        return self.runtime_root / "data"

    @property
    def curated_root(self) -> Path:
        return self.data_root / "curated" / DATASET_VERSION

    @property
    def quality_root(self) -> Path:
        return self.data_root / "quality" / DATASET_VERSION

    @property
    def manifest_path(self) -> Path:
        return self.data_root / "manifests" / f"{DATASET_VERSION}.json"

    @property
    def frozen_path(self) -> Path:
        return self.manifest_path

    @property
    def membership_path(self) -> Path:
        return (
            self.data_root
            / "external"
            / "fja05680"
            / "sp500_historical_components_updated.csv"
        )

    @property
    def membership_license_path(self) -> Path:
        return self.data_root / "external" / "fja05680" / "LICENSE"

    @property
    def membership_readme_path(self) -> Path:
        return self.data_root / "external" / "fja05680" / "SOURCE_README.md"


@dataclass(frozen=True, slots=True)
class FrozenSecurityIdentity:
    canonical_sid: str
    security_key: str
    provider_symbol: str
    provider_status: str


@dataclass(frozen=True, slots=True)
class FrozenSp500Seed:
    paths: FrozenSp500Paths
    manifest_sha256: str
    frozen_sha256: str
    frozen_at: datetime
    evaluation_start: date
    evaluation_end: date
    snapshots: tuple[HistoricalMembershipSnapshot, ...]
    identities: tuple[FrozenSecurityIdentity, ...]
    source_to_canonical_sid: tuple[tuple[str, str], ...]
    import_objects: tuple[ExternalImportObjectSpec, ...]
    universe_import_objects: tuple[ExternalImportObjectSpec, ...]
    price_row_count: int


@dataclass(frozen=True, slots=True)
class FrozenIdentityPublication:
    artifact_id: uuid.UUID
    master_data_release_id: uuid.UUID
    security_count: int
    created_security_count: int
    created_asset_count: int
    reused: bool


@dataclass(frozen=True, slots=True)
class FrozenSp500Preparation:
    import_manifest: ExternalImportManifestPublication
    universe_import_manifest: ExternalImportManifestPublication
    identities: FrozenIdentityPublication
    universe: HistoricalSp500UniversePublication
    provider_identifier_count: int


def load_frozen_sp500_seed(
    runtime_root: Path,
    source_project_root: Path,
    *,
    warmup_start: date = date(2004, 12, 31),
    evaluation_start: date = date(2007, 1, 3),
    evaluation_end: date = date(2026, 6, 30),
) -> FrozenSp500Seed:
    paths = FrozenSp500Paths(runtime_root.resolve(), source_project_root.resolve())
    raw_root = paths.data_root / "raw" / "yfinance" / "yahoo-2004-2026-v1"
    required = (
        paths.manifest_path,
        paths.curated_root / "security_master.parquet",
        paths.curated_root / "membership.parquet",
        paths.curated_root / "prices_daily.parquet",
        paths.curated_root / "calendar.parquet",
        paths.curated_root / "benchmark_daily.parquet",
        paths.curated_root / "universe_at_signal.parquet",
        paths.curated_root / "qa_summary.parquet",
        raw_root / "provider_prices.parquet",
        raw_root / "download_failures.csv",
        raw_root / "security_master.csv",
        raw_root / "membership.csv",
    )
    missing = tuple(str(item) for item in required if not item.is_file())
    if missing:
        raise FileNotFoundError(f"Frozen S&P seed is incomplete: {missing}")

    manifest = _json_object(paths.manifest_path)
    frozen_at = datetime.fromisoformat(
        str(manifest["created_at_utc"]).replace("Z", "+00:00")
    )
    if frozen_at.tzinfo is None:
        raise ValueError("Frozen S&P publication timestamp must be timezone-aware")
    manifest_sha256 = _sha256(paths.manifest_path)
    if str(manifest.get("dataset_version")) != DATASET_VERSION:
        raise ValueError("Frozen S&P dataset version is not the approved v4 candidate")
    request = cast(dict[str, object], manifest["request"])
    if date.fromisoformat(str(request["price_start"])) != warmup_start:
        raise ValueError("Frozen S&P warm-up start differs from the approved date")
    if date.fromisoformat(str(request["research_start"])) != evaluation_start:
        raise ValueError("Frozen S&P evaluation start differs from the approved date")
    if date.fromisoformat(str(request["end"])) != evaluation_end:
        raise ValueError("Frozen S&P evaluation end differs from the approved date")
    if manifest.get("status") != "invalid_data" or manifest.get("research_tier") != "prototype":
        raise ValueError("v4 candidate must enter through the v0.22 warning Gate")

    core_files = required[1:]
    _validate_manifest_objects(manifest, paths.data_root, core_files)
    snapshots = _curated_membership_snapshots(
        paths.curated_root / "membership.parquet",
        warmup_start=warmup_start,
        evaluation_end=evaluation_end,
    )
    identities = _security_identities(paths, {})
    identities = tuple(sorted(identities, key=lambda item: item.security_key))
    _validate_identity_uniqueness(identities)

    import_objects = _import_objects(paths, core_files)
    price_rows = parquet.ParquetFile(paths.curated_root / "prices_daily.parquet").metadata
    if price_rows is None or price_rows.num_rows != 4_881_338:
        raise ValueError("Frozen S&P price row count drifted from the approved evidence")
    return FrozenSp500Seed(
        paths=paths,
        manifest_sha256=manifest_sha256,
        frozen_sha256=manifest_sha256,
        frozen_at=frozen_at,
        evaluation_start=evaluation_start,
        evaluation_end=evaluation_end,
        snapshots=snapshots,
        identities=identities,
        source_to_canonical_sid=tuple(
            sorted(
                (
                    (item.provider_symbol, item.canonical_sid)
                    for item in identities
                ),
                key=lambda item: item[0].casefold(),
            )
        ),
        import_objects=import_objects,
        universe_import_objects=_universe_import_objects(paths),
        price_row_count=price_rows.num_rows,
    )


class FrozenSp500PreparationService:
    """Publish immutable source, identity and membership prerequisites only."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def prepare(self, seed: FrozenSp500Seed, *, created_by: str) -> FrozenSp500Preparation:
        imported = ExternalImportManifestService(self._engine).publish(
            ExternalImportManifestSpec(
                manifest_key="sp500_free_research_v4_seed",
                version_number=2,
                source_project_key="momentum_reversion_method",
                source_release_key=DATASET_VERSION,
                objects=seed.import_objects,
                created_by=created_by,
            )
        )
        identities = self._publish_identities(seed, imported.artifact_id, created_by)
        universe_import = ExternalImportManifestService(self._engine).publish(
            ExternalImportManifestSpec(
                manifest_key="sp500_free_research_v4_universe_seed",
                version_number=2,
                source_project_key="momentum_reversion_method",
                source_release_key=DATASET_VERSION,
                objects=seed.universe_import_objects,
                created_by=created_by,
            )
        )
        by_key = self._security_ids(seed.identities)
        mappings = tuple(
            MembershipSecurityMapping(
                canonical_sid,
                by_key[_security_key(canonical_sid.removeprefix("sec::"))],
            )
            for canonical_sid in sorted(
                {symbol for snapshot in seed.snapshots for symbol in snapshot.source_symbols}
            )
        )
        universe = HistoricalSp500UniversePublicationService(self._engine).publish(
            HistoricalSp500UniverseSpec(
                external_import_manifest_artifact_id=universe_import.artifact_id,
                source_object_logical_key=CURATED_MEMBERSHIP_LOGICAL_KEY,
                universe_key="sp500_historical_free_research_v1",
                version_number=2,
                methodology_key="sp500_historical_membership_free_research",
                methodology_version=2,
                research_tier="rankable_research",
                snapshots=seed.snapshots,
                mappings=mappings,
                data_cutoff_at=seed.frozen_at,
                published_at=seed.frozen_at,
                created_by=created_by,
            )
        )
        identifiers = ProviderSecurityIdentityService(self._engine)
        registered = 0
        for identity in seed.identities:
            identifiers.register(
                security_id=by_key[identity.security_key],
                provider_scope="yahoo_yfinance",
                provider_symbol=identity.provider_symbol,
                valid_from=None,
                valid_to=None,
            )
            registered += 1
        return FrozenSp500Preparation(
            imported, universe_import, identities, universe, registered
        )

    def _publish_identities(
        self,
        seed: FrozenSp500Seed,
        source_artifact_id: uuid.UUID,
        created_by: str,
    ) -> FrozenIdentityPublication:
        document = {
            "contract_version": _IDENTITY_CONTRACT,
            "dataset_version": DATASET_VERSION,
            "manifest_sha256": seed.manifest_sha256,
            "frozen_sha256": seed.frozen_sha256,
            "securities": [
                {
                    "canonical_sid": item.canonical_sid,
                    "security_key": item.security_key,
                    "provider_symbol": item.provider_symbol,
                    "provider_status": item.provider_status,
                }
                for item in seed.identities
            ],
        }
        release_id = uuid.uuid5(
            uuid.NAMESPACE_URL, f"bird:v0.22:frozen-sp500-identity:{seed.manifest_sha256}"
        )
        counts = {"security": 0, "asset": 0}

        def writer(connection: Connection, artifact_id: uuid.UUID) -> None:
            connection.execute(
                text(
                    """
                    INSERT INTO catalog.master_data_release (
                      master_data_release_id,artifact_id,release_key,version_number,as_of_date
                    ) VALUES (:id,:artifact,:key,2,:as_of)
                    """
                ),
                {
                    "id": release_id,
                    "artifact": artifact_id,
                    "key": _IDENTITY_RELEASE_KEY,
                    "as_of": seed.evaluation_end,
                },
            )
            for item in seed.identities:
                asset_id = connection.execute(
                    text("SELECT asset_id FROM catalog.asset WHERE asset_key=:key"),
                    {"key": item.security_key},
                ).scalar_one_or_none()
                if asset_id is None:
                    asset_id = uuid.uuid5(
                        uuid.NAMESPACE_URL, f"bird:v0.22:asset:{item.security_key}"
                    )
                    connection.execute(
                        text(
                            """
                            INSERT INTO catalog.asset (
                              asset_id,master_data_release_id,asset_key,name,asset_type,status
                            ) VALUES (:id,:release,:key,:name,'equity','active')
                            """
                        ),
                        {
                            "id": asset_id,
                            "release": release_id,
                            "key": item.security_key,
                            "name": item.provider_symbol,
                        },
                    )
                    connection.execute(
                        text(
                            """
                            INSERT INTO catalog.asset_identifier (
                              asset_identifier_id,master_data_release_id,asset_id,
                              identifier_type,identifier_value
                            ) VALUES (:id,:release,:asset,'internal_key',:key)
                            """
                        ),
                        {
                            "id": uuid.uuid5(
                                uuid.NAMESPACE_URL,
                                f"bird:v0.22:asset-identifier:{item.security_key}",
                            ),
                            "release": release_id,
                            "asset": asset_id,
                            "key": item.security_key,
                        },
                    )
                    counts["asset"] += 1
                security = connection.execute(
                    text(
                        "SELECT security_id,legacy_asset_id FROM catalog.security "
                        "WHERE security_key=:key"
                    ),
                    {"key": item.security_key},
                ).mappings().one_or_none()
                if security is None:
                    security_id = uuid.uuid5(
                        uuid.NAMESPACE_URL, f"bird:v0.22:security:{item.security_key}"
                    )
                    connection.execute(
                        text(
                            """
                            INSERT INTO catalog.security (
                              security_id,legacy_asset_id,security_key,name,
                              instrument_type,currency,status
                            ) VALUES (:id,:asset,:key,:name,'Common Stock','USD','active')
                            """
                        ),
                        {
                            "id": security_id,
                            "asset": asset_id,
                            "key": item.security_key,
                            "name": item.provider_symbol,
                        },
                    )
                    counts["security"] += 1
                elif security["legacy_asset_id"] != asset_id:
                    raise ValueError(
                        f"Existing Security/Asset identity drift for {item.security_key}"
                    )

        with self._engine.begin() as connection:
            publication = ArtifactService(
                cast(Engine, _BoundConnection(connection))
            ).publish(
                artifact_type="catalog_master_data_release",
                artifact_key=_IDENTITY_RELEASE_KEY,
                version_number=2,
                semantic_payload=document,
                content_payload=document,
                dependencies=(DependencyInput(source_artifact_id, "external_import_manifest", 0),),
                reason="publish frozen S&P Security and canonical Asset identities",
                draft_writer=writer,
            )
        return FrozenIdentityPublication(
            publication.artifact_id,
            release_id,
            len(seed.identities),
            counts["security"],
            counts["asset"],
            publication.reused,
        )

    def _security_ids(
        self, identities: tuple[FrozenSecurityIdentity, ...]
    ) -> dict[str, uuid.UUID]:
        keys = tuple(item.security_key for item in identities)
        with self._engine.connect() as connection:
            rows = connection.execute(
                text(
                    "SELECT security_key,security_id FROM catalog.security "
                    "WHERE security_key=ANY(:keys)"
                ),
                {"keys": list(keys)},
            ).all()
        result = {str(key): cast(uuid.UUID, security_id) for key, security_id in rows}
        if set(result) != set(keys):
            raise LookupError("Frozen S&P Security identity publication is incomplete")
        return result


class _BoundConnection:
    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def begin(self) -> nullcontext[Connection]:
        return nullcontext(self._connection)


def _candidate_snapshots(
    snapshots: tuple[HistoricalMembershipSnapshot, ...],
    *,
    warmup_start: date,
    evaluation_end: date,
) -> tuple[HistoricalMembershipSnapshot, ...]:
    before = tuple(item for item in snapshots if item.effective_session <= warmup_start)
    if not before:
        raise ValueError("Historical membership does not cover the frozen warm-up start")
    initial = before[-1]
    result = (initial,) + tuple(
        item
        for item in snapshots
        if initial.effective_session < item.effective_session <= evaluation_end
    )
    if result[-1].effective_session > evaluation_end:
        raise ValueError("Historical membership exceeds the frozen evaluation interval")
    return result


def _curated_membership_snapshots(
    path: Path,
    *,
    warmup_start: date,
    evaluation_end: date,
) -> tuple[HistoricalMembershipSnapshot, ...]:
    rows = parquet.read_table(
        path, columns=["sid", "effective_from", "effective_to"]
    ).to_pylist()
    intervals: list[tuple[str, date, date | None]] = []
    change_sessions = {warmup_start}
    for row in rows:
        raw_sid = str(row["sid"])
        if raw_sid.startswith("yf_ticker::"):
            sid = f"sec::{_security_key(raw_sid.removeprefix('yf_ticker::')).upper()}"
        else:
            sid = raw_sid
        effective_from = cast(datetime, row["effective_from"]).date()
        raw_to = row["effective_to"]
        effective_to = cast(datetime, raw_to).date() if raw_to is not None else None
        if not sid.startswith("sec::"):
            raise ValueError("Curated membership contains a malformed Security identity")
        if effective_to is not None and effective_from >= effective_to:
            raise ValueError("Curated membership interval must be half-open")
        intervals.append((sid, effective_from, effective_to))
        if warmup_start < effective_from <= evaluation_end:
            change_sessions.add(effective_from)
        if effective_to is not None and warmup_start < effective_to <= evaluation_end:
            change_sessions.add(effective_to)
    snapshots: list[HistoricalMembershipSnapshot] = []
    for ordinal, session in enumerate(sorted(change_sessions), start=2):
        members = tuple(
            sorted(
                sid
                for sid, effective_from, effective_to in intervals
                if effective_from <= session
                and (effective_to is None or session < effective_to)
            )
        )
        snapshots.append(
            HistoricalMembershipSnapshot(
                effective_session=session,
                source_row_number=ordinal,
                source_symbols=members,
                reason_code="frozen_curated_membership_intervals",
            )
        )
    if not snapshots or not snapshots[0].source_symbols:
        raise ValueError("Curated membership does not cover the frozen warm-up start")
    return tuple(snapshots)


def _security_identities(
    paths: FrozenSp500Paths, _resolution: dict[str, str]
) -> tuple[FrozenSecurityIdentity, ...]:
    price_sids = {
        str(item)
        for item in parquet.read_table(
            paths.curated_root / "prices_daily.parquet", columns=["sid"]
        ).column("sid").unique().to_pylist()
    }
    table = parquet.read_table(
        paths.curated_root / "security_master.parquet",
        columns=["sid", "provider", "ticker"],
    )
    result: list[FrozenSecurityIdentity] = []
    for row in table.to_pylist():
        raw_sid = str(row["sid"])
        ticker = str(row["ticker"])
        canonical_sid = (
            f"sec::{_security_key(ticker).upper()}"
            if raw_sid.startswith("yf_ticker::")
            else raw_sid
        )
        if not canonical_sid.startswith("sec::") or not ticker:
            raise ValueError("Frozen Security master identity is malformed")
        result.append(
            FrozenSecurityIdentity(
                canonical_sid=canonical_sid,
                security_key=_security_key(canonical_sid.removeprefix("sec::")),
                provider_symbol=ticker,
                provider_status=(str(row["provider"]) if raw_sid in price_sids else "unavailable"),
            )
        )
    return tuple(result)


def _validate_identity_uniqueness(identities: tuple[FrozenSecurityIdentity, ...]) -> None:
    for label, values in (
        ("canonical SID", [item.canonical_sid.casefold() for item in identities]),
        ("Security key", [item.security_key for item in identities]),
        ("provider symbol", [item.provider_symbol.casefold() for item in identities]),
    ):
        if len(values) != len(set(values)):
            raise ValueError(f"Frozen S&P {label} values are not unique")


def _security_key(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "_", value.casefold()).strip("_")
    if not normalized or len(normalized) > 180:
        raise ValueError(f"Cannot form a stable Security key from {value!r}")
    return normalized


def _import_objects(
    paths: FrozenSp500Paths, core_files: tuple[Path, ...]
) -> tuple[ExternalImportObjectSpec, ...]:
    objects: list[ExternalImportObjectSpec] = []
    for path in core_files:
        if path == paths.membership_path:
            role = "membership_source"
            logical = RAW_MEMBERSHIP_LOGICAL_KEY
            license_key = "MIT"
            provenance = "verified"
            usage = "redistributable"
        elif path == paths.membership_license_path:
            role, logical, license_key, provenance, usage = (
                "source_license",
                "fja05680_license",
                "MIT",
                "verified",
                "redistributable",
            )
        elif path == paths.membership_readme_path:
            role, logical, license_key, provenance, usage = (
                "source_documentation",
                "fja05680_source_readme",
                "MIT",
                "verified",
                "redistributable",
            )
        else:
            relative = path.relative_to(paths.data_root).as_posix()
            role = "frozen_market_evidence"
            logical = relative.replace("/", "__")
            license_key = "local_research_evidence"
            provenance = "needs_review"
            usage = "local_research"
        digest = _sha256(path)
        objects.append(
            ExternalImportObjectSpec(
                object_role=role,
                logical_key=logical,
                media_type=_media_type(path),
                content_sha256=digest,
                size_bytes=path.stat().st_size,
                source_uri=f"content://sha256/{digest}",
                license_key=license_key,
                provenance_status=cast(Any, provenance),
                usage_scope=cast(Any, usage),
                metadata={"dataset_version": DATASET_VERSION, "file_name": path.name},
            )
        )
    for role, logical, path in (
        ("frozen_manifest", "frozen_dataset_manifest", paths.manifest_path),
        ("freeze_marker", "frozen_dataset_marker", paths.frozen_path),
    ):
        digest = _sha256(path)
        objects.append(
            ExternalImportObjectSpec(
                object_role=role,
                logical_key=logical,
                media_type="application/json",
                content_sha256=digest,
                size_bytes=path.stat().st_size,
                source_uri=f"content://sha256/{digest}",
                license_key="local_research_evidence",
                provenance_status="needs_review",
                usage_scope="local_research",
                metadata={"dataset_version": DATASET_VERSION, "file_name": path.name},
            )
        )
    objects.append(_fja_source_object())
    return tuple(objects)


def _universe_import_objects(paths: FrozenSp500Paths) -> tuple[ExternalImportObjectSpec, ...]:
    objects: list[ExternalImportObjectSpec] = []
    for role, logical, path in (
        (
            "curated_membership_source",
            CURATED_MEMBERSHIP_LOGICAL_KEY,
            paths.curated_root / "membership.parquet",
        ),
        ("frozen_manifest", "frozen_dataset_manifest", paths.manifest_path),
        ("freeze_marker", "frozen_dataset_marker", paths.frozen_path),
    ):
        digest = _sha256(path)
        objects.append(
            ExternalImportObjectSpec(
                object_role=role,
                logical_key=logical,
                media_type=_media_type(path),
                content_sha256=digest,
                size_bytes=path.stat().st_size,
                source_uri=f"content://sha256/{digest}",
                license_key="local_research_evidence",
                provenance_status="verified",
                usage_scope="local_research",
                metadata={
                    "dataset_version": DATASET_VERSION,
                    "file_name": path.name,
                    "formal_eligible": False,
                },
            )
        )
    objects.append(_fja_source_object())
    return tuple(objects)


def _fja_source_object() -> ExternalImportObjectSpec:
    return ExternalImportObjectSpec(
        object_role="membership_source",
        logical_key=RAW_MEMBERSHIP_LOGICAL_KEY,
        media_type="text/csv",
        content_sha256=(
            "39a9202c9ef69a74c0ff07e2113ad41fb6da7c8c5b6cd9541f0185fb4391e717"
        ),
        size_bytes=5_526_653,
        source_uri=(
            "https://raw.githubusercontent.com/fja05680/sp500/master/"
            "S%26P%20500%20Historical%20Components%20%26%20Changes%20(Updated).csv"
        ),
        license_key="MIT",
        provider_key="fja05680_sp500",
        provenance_status="verified",
        usage_scope="redistributable",
        metadata={
            "source_repository": "fja05680/sp500",
            "dataset_version": DATASET_VERSION,
            "historical_pit_claimed": False,
        },
    )


def _validate_manifest_objects(
    manifest: dict[str, Any], data_root: Path, required: tuple[Path, ...]
) -> None:
    records = manifest.get("files")
    if not isinstance(records, list):
        raise ValueError("Frozen S&P manifest file records are missing")
    expected: dict[Path, tuple[int, str]] = {}
    for item in records:
        if not isinstance(item, dict):
            continue
        raw_path = Path(str(item.get("path", "")))
        resolved = raw_path if raw_path.is_absolute() else data_root / raw_path
        expected[resolved.resolve()] = (int(item.get("size_bytes", -1)), str(item.get("sha256")))
    for path in required:
        record = expected.get(path.resolve())
        if record is None:
            raise ValueError(f"Frozen S&P manifest omits required object {path.name}")
        size_bytes, digest = record
        if not _SHA256.fullmatch(digest):
            raise ValueError("Frozen S&P manifest contains an invalid SHA-256")
        if path.stat().st_size != size_bytes or _sha256(path) != digest:
            raise ValueError(f"Frozen S&P object drift detected for {path.name}")


def _media_type(path: Path) -> str:
    return {
        ".csv": "text/csv",
        ".json": "application/json",
        ".md": "text/markdown",
        ".parquet": "application/vnd.apache.parquet",
    }.get(path.suffix.casefold(), "application/octet-stream")


def _json_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object in {path.name}")
    return cast(dict[str, Any], value)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
