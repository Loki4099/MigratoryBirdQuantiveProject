from __future__ import annotations

import csv
import hashlib
import json
import uuid
from contextlib import nullcontext
from dataclasses import asdict, dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, cast

from sqlalchemy import Engine, text
from sqlalchemy.engine import Connection

from style_rotation.data.calendar import (
    CalendarPublicationService,
    GeneratedCalendar,
    TradingSession,
)
from style_rotation.data.service import publish_data_contracts
from style_rotation.lineage.service import ArtifactService, DependencyInput
from style_rotation.v022.data_seed_import import (
    ExternalImportManifestService,
    ExternalImportManifestSpec,
    ExternalImportObjectSpec,
)
from style_rotation.v022.green_baseline_import import (
    FreshIdentity,
    GreenBaselineImportPlan,
    build_green_baseline_import_plan,
)
from style_rotation.v022.market_reconciliation import (
    V2_RECONSTRUCTION_POLICY,
    _price_semantics,
)

_FOUNDATION_CONTRACT = "migratory_bird_v022_green_baseline_foundation_v1"
_DATA_CONTRACT_CATALOG = Path("v0.2/catalogs/data_contracts.v0.2.0.json")


@dataclass(frozen=True, slots=True)
class GreenBaselineFoundationSpec:
    transfer_root: Path
    plan: GreenBaselineImportPlan
    created_by: str
    data_contract_catalog: Path = _DATA_CONTRACT_CATALOG


@dataclass(frozen=True, slots=True)
class GreenBaselineFoundationPublication:
    contract: str
    plan_fingerprint: str
    external_import_manifest_id: str
    external_import_artifact_id: str
    master_data_release_id: str
    master_data_artifact_id: str
    calendar_version_id: str
    calendar_artifact_id: str
    cleaning_version_id: str
    cleaning_artifact_id: str
    asset_count: int
    security_count: int
    security_identifier_count: int
    calendar_session_count: int
    reused_roles: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class _BoundConnection:
    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def begin(self) -> nullcontext[Connection]:
        return nullcontext(self._connection)


def _rows(root: Path, table: str) -> list[dict[str, str]]:
    with (root / "metadata" / f"{table}.csv").open(
        "r", encoding="utf-8", newline=""
    ) as source:
        return list(csv.DictReader(source))


def _identity(plan: GreenBaselineImportPlan, role: str) -> FreshIdentity:
    matches = [item for item in plan.identities if item.role == role]
    if len(matches) != 1:
        raise ValueError(f"green baseline plan must contain one {role} identity")
    return matches[0]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _external_import_spec(spec: GreenBaselineFoundationSpec) -> ExternalImportManifestSpec:
    root = spec.transfer_root.resolve()
    objects = tuple(
        ExternalImportObjectSpec(
            object_role=role,
            logical_key=name,
            media_type=media_type,
            content_sha256=_sha256(root / name),
            size_bytes=(root / name).stat().st_size,
            source_uri=f"content:sha256/{_sha256(root / name)}",
            license_key="free_research_source_evidence_v1",
            provenance_status="verified",
            usage_scope="local_research",
            metadata={
                "package_contract": "migratory_bird_v022_green_transfer_v2",
                "transitively_addresses_payloads": name == "manifest.jsonl",
            },
        )
        for role, name, media_type in (
            ("package_contract", "package.json", "application/json"),
            ("content_manifest", "manifest.jsonl", "application/x-ndjson"),
            ("checksum_manifest", "SHA256SUMS", "text/plain"),
        )
    )
    return ExternalImportManifestSpec(
        manifest_key="v022_green_transfer_baseline",
        version_number=1,
        source_project_key="migratory_bird_v022_blue",
        source_release_key=spec.plan.transfer_manifest_sha256,
        objects=objects,
        created_by=spec.created_by,
    )


def _scoped_source_rows(
    spec: GreenBaselineFoundationSpec,
) -> tuple[
    list[dict[str, str]],
    list[dict[str, str]],
    list[dict[str, str]],
    list[dict[str, str]],
]:
    root = spec.transfer_root.resolve()
    manifest = [
        json.loads(line)
        for line in (root / "manifest.jsonl").read_text(encoding="utf-8").splitlines()
        if line
    ]
    market_security_ids = {
        str(item["security_id"])
        for item in manifest
        if item["kind"] in {"daily_bar", "corporate_action"}
    }
    membership_security_ids = {
        row["security_id"]
        for row in _rows(root, "catalog.v022_universe_membership_event")
        if row["security_id"]
    }
    lifecycle_rows = _rows(root, "catalog.v022_security_lifecycle_event")
    settlement_rows = _rows(root, "catalog.v022_security_settlement_leg")
    lifecycle_security_ids = {
        row["security_id"] for row in lifecycle_rows if row["security_id"]
    } | {
        row["target_security_id"]
        for row in settlement_rows
        if row["target_security_id"]
    }
    scoped_security_ids = (
        market_security_ids | membership_security_ids | lifecycle_security_ids
    )
    securities = [
        row
        for row in _rows(root, "catalog.security")
        if row["security_id"] in scoped_security_ids
    ]
    scoped_asset_ids = {
        row["legacy_asset_id"] for row in securities if row["legacy_asset_id"]
    }
    assets = [
        row for row in _rows(root, "catalog.asset") if row["asset_id"] in scoped_asset_ids
    ]
    asset_identifiers = [
        row
        for row in _rows(root, "catalog.asset_identifier")
        if row["asset_id"] in scoped_asset_ids
    ]
    security_identifiers = [
        row
        for row in _rows(root, "catalog.security_identifier")
        if row["security_id"] in scoped_security_ids
    ]
    if len(securities) != spec.plan.scoped_security_count:
        raise ValueError("foundation Security scope differs from the frozen import plan")
    if len(assets) != spec.plan.scoped_asset_count:
        raise ValueError("foundation Asset scope differs from the frozen import plan")
    return assets, asset_identifiers, securities, security_identifiers


def _calendar(spec: GreenBaselineFoundationSpec) -> GeneratedCalendar:
    root = spec.transfer_root.resolve()
    versions = {
        row["calendar_version_id"]: row
        for row in _rows(root, "catalog.calendar_version")
    }
    source = versions.get(spec.plan.calendar_source_id)
    if source is None:
        raise ValueError("frozen source Calendar is missing")
    sessions = tuple(
        TradingSession(
            session_date=date.fromisoformat(row["session_date"]),
            open_at_utc=datetime.fromisoformat(row["open_at_utc"]),
            close_at_utc=datetime.fromisoformat(row["close_at_utc"]),
            is_early_close=row["is_early_close"].lower() in {"t", "true", "1"},
        )
        for row in _rows(root, "catalog.calendar_session")
        if row["calendar_version_id"] == spec.plan.calendar_source_id
    )
    if len(sessions) != spec.plan.calendar_session_count:
        raise ValueError("frozen Calendar sessions differ from the import plan")
    return GeneratedCalendar(
        calendar_key="XNYS",
        library_name=source["library_name"],
        library_version=source["library_version"],
        coverage_start=date.fromisoformat(source["coverage_start"]),
        coverage_end=date.fromisoformat(source["coverage_end"]),
        sessions=sessions,
    )


def _publish_master_data(
    engine: Engine,
    spec: GreenBaselineFoundationSpec,
    import_artifact_id: uuid.UUID,
) -> tuple[uuid.UUID, uuid.UUID, bool, int, int, int]:
    identity = _identity(spec.plan, "master_data")
    release_id = uuid.UUID(identity.object_id)
    assets, asset_identifiers, securities, security_identifiers = _scoped_source_rows(spec)
    calendar_definition_id = uuid.uuid5(
        release_id, "calendar-definition:XNYS"
    )
    listing_rows = [
        row
        for row in _rows(spec.transfer_root, "catalog.asset_listing")
        if row["asset_id"] in {item["asset_id"] for item in assets}
    ]
    semantic = {
        "contract": _FOUNDATION_CONTRACT,
        "transfer_manifest_sha256": spec.plan.transfer_manifest_sha256,
        "plan_fingerprint": spec.plan.plan_fingerprint,
        "release_key": "research_scope",
        "version_number": identity.version_number,
        "asset_count": len(assets),
        "security_count": len(securities),
        "security_identifier_count": len(security_identifiers),
        "calendar_key": "XNYS",
    }

    def writer(connection: Connection, artifact_id: uuid.UUID) -> None:
        connection.execute(
            text(
                "INSERT INTO catalog.master_data_release "
                "(master_data_release_id,artifact_id,release_key,version_number,as_of_date) "
                "VALUES (:id,:artifact,'research_scope',:version,:as_of)"
            ),
            {
                "id": release_id,
                "artifact": artifact_id,
                "version": identity.version_number,
                "as_of": max(item.coverage_end for item in spec.plan.datasets),
            },
        )
        connection.execute(
            text(
                "INSERT INTO catalog.calendar_definition "
                "(calendar_definition_id,master_data_release_id,calendar_key,name,"
                "timezone,venue_mic) "
                "VALUES (:id,:release,'XNYS','New York Stock Exchange sessions',"
                "'America/New_York','XNYS')"
            ),
            {"id": calendar_definition_id, "release": release_id},
        )
        connection.execute(
            text(
                "INSERT INTO catalog.asset "
                "(asset_id,master_data_release_id,asset_key,name,asset_type,status) "
                "VALUES (:asset_id,:release,:asset_key,:name,:asset_type,:status)"
            ),
            [
                {
                    "asset_id": row["asset_id"],
                    "release": release_id,
                    "asset_key": row["asset_key"],
                    "name": row["name"],
                    "asset_type": row["asset_type"],
                    "status": row["status"],
                }
                for row in assets
            ],
        )
        connection.execute(
            text(
                "INSERT INTO catalog.asset_identifier "
                "(asset_identifier_id,master_data_release_id,asset_id,identifier_type,"
                "identifier_value,valid_from,valid_to) VALUES "
                "(:id,:release,:asset,:type,:value,:valid_from,:valid_to)"
            ),
            [
                {
                    "id": row["asset_identifier_id"],
                    "release": release_id,
                    "asset": row["asset_id"],
                    "type": row["identifier_type"],
                    "value": row["identifier_value"],
                    "valid_from": row["valid_from"] or None,
                    "valid_to": row["valid_to"] or None,
                }
                for row in asset_identifiers
            ],
        )
        if listing_rows:
            connection.execute(
                text(
                    "INSERT INTO catalog.asset_listing "
                    "(asset_listing_id,master_data_release_id,asset_id,calendar_definition_id,"
                    "listing_key,venue_mic,currency,timezone,valid_from,valid_to) VALUES "
                    "(:id,:release,:asset,:calendar,:key,:mic,:currency,:timezone,"
                    ":valid_from,:valid_to)"
                ),
                [
                    {
                        "id": row["asset_listing_id"],
                        "release": release_id,
                        "asset": row["asset_id"],
                        "calendar": calendar_definition_id,
                        "key": row["listing_key"],
                        "mic": row["venue_mic"],
                        "currency": row["currency"],
                        "timezone": row["timezone"],
                        "valid_from": row["valid_from"] or None,
                        "valid_to": row["valid_to"] or None,
                    }
                    for row in listing_rows
                ],
            )
        connection.execute(
            text(
                "INSERT INTO catalog.security "
                "(security_id,issuer_id,legacy_asset_id,security_key,name,instrument_type,"
                "currency,status) VALUES (:id,NULL,:asset,:key,:name,:instrument_type,"
                ":currency,:status)"
            ),
            [
                {
                    "id": row["security_id"],
                    "asset": row["legacy_asset_id"] or None,
                    "key": row["security_key"],
                    "name": row["name"],
                    "instrument_type": row["instrument_type"],
                    "currency": row["currency"] or None,
                    "status": row["status"],
                }
                for row in securities
            ],
        )
        connection.execute(
            text(
                "INSERT INTO catalog.security_identifier "
                "(security_identifier_id,security_id,identifier_type,identifier_value,"
                "valid_from,valid_to,provider_scope) VALUES "
                "(:id,:security,:type,:value,:valid_from,:valid_to,:provider_scope)"
            ),
            [
                {
                    "id": row["security_identifier_id"],
                    "security": row["security_id"],
                    "type": row["identifier_type"],
                    "value": row["identifier_value"],
                    "valid_from": row["valid_from"] or None,
                    "valid_to": row["valid_to"] or None,
                    "provider_scope": row["provider_scope"],
                }
                for row in security_identifiers
            ],
        )

    with engine.begin() as connection:
        publication = ArtifactService(cast(Engine, _BoundConnection(connection))).publish(
            artifact_type=identity.artifact_type,
            artifact_key=identity.artifact_key,
            version_number=identity.version_number,
            semantic_payload=semantic,
            content_payload=semantic,
            dependencies=(
                DependencyInput(import_artifact_id, "external_import_manifest", 0),
            ),
            reason="publish clean-green v0.22 baseline master data",
            draft_writer=writer,
        )
    return (
        release_id,
        publication.artifact_id,
        publication.reused,
        len(assets),
        len(securities),
        len(security_identifiers),
    )


def _publish_v2_cleaning(
    engine: Engine,
    spec: GreenBaselineFoundationSpec,
    import_artifact_id: uuid.UUID,
) -> tuple[uuid.UUID, uuid.UUID, bool]:
    identity = _identity(spec.plan, "cleaning")
    cleaning_version_id = uuid.UUID(identity.object_id)
    semantic = {
        "key": identity.artifact_key,
        "version_number": identity.version_number,
        "implementation_key": (
            "style_rotation.v022.cleaning."
            "split_normalized_ohlcv_dividends_backward_total_return"
        ),
        "implementation_version": "2",
        "configuration": {
            "reconstruction_policy": V2_RECONSTRUCTION_POLICY,
            "price_semantics": _price_semantics(V2_RECONSTRUCTION_POLICY),
            "split_treatment": "evidence_only_source_ohlc_already_split_normalized",
            "cash_dividend_treatment": "backward_total_return_same_share_basis",
            "volume_basis": "raw",
            "transfer_manifest_sha256": spec.plan.transfer_manifest_sha256,
        },
    }

    def writer(connection: Connection, artifact_id: uuid.UUID) -> None:
        definition_id = connection.execute(
            text(
                "SELECT definition.cleaning_definition_id FROM data.cleaning_definition definition "
                "JOIN data.data_contract_release release ON "
                "release.data_contract_release_id=definition.data_contract_release_id "
                "JOIN lineage.artifact artifact ON artifact.artifact_id=release.artifact_id "
                "WHERE definition.cleaning_key='adjusted_ohlc' AND artifact.status='published' "
                "ORDER BY artifact.version_number DESC LIMIT 1"
            )
        ).scalar_one()
        connection.execute(
            text(
                "INSERT INTO data.cleaning_version "
                "(cleaning_version_id,cleaning_definition_id,artifact_id,version_number,"
                "implementation_key,implementation_version,configuration) VALUES "
                "(:id,:definition,:artifact,2,:implementation_key,'2',"
                "CAST(:configuration AS jsonb))"
            ),
            {
                "id": cleaning_version_id,
                "definition": definition_id,
                "artifact": artifact_id,
                "implementation_key": semantic["implementation_key"],
                "configuration": json.dumps(semantic["configuration"], sort_keys=True),
            },
        )

    with engine.begin() as connection:
        contract_artifact_id = connection.execute(
            text(
                "SELECT artifact_id FROM lineage.artifact WHERE "
                "artifact_type='data_contract_release' AND artifact_key='data_contracts' "
                "AND status='published' ORDER BY version_number DESC LIMIT 1"
            )
        ).scalar_one()
        publication = ArtifactService(cast(Engine, _BoundConnection(connection))).publish(
            artifact_type=identity.artifact_type,
            artifact_key=identity.artifact_key,
            version_number=identity.version_number,
            semantic_payload=semantic,
            content_payload=semantic,
            dependencies=(
                DependencyInput(contract_artifact_id, "data_contract_release", 0),
                DependencyInput(import_artifact_id, "external_import_manifest", 1),
            ),
            reason="publish split-normalized retrospective total-return cleaning v2",
            draft_writer=writer,
        )
    return cleaning_version_id, publication.artifact_id, publication.reused


def publish_green_baseline_foundation(
    engine: Engine, spec: GreenBaselineFoundationSpec
) -> GreenBaselineFoundationPublication:
    expected = build_green_baseline_import_plan(spec.transfer_root)
    if expected.to_dict() != spec.plan.to_dict():
        raise ValueError("green baseline import plan is stale or does not match the transfer")
    import_publication = ExternalImportManifestService(engine).publish(
        _external_import_spec(spec)
    )
    (
        master_id,
        master_artifact_id,
        master_reused,
        asset_count,
        security_count,
        identifier_count,
    ) = _publish_master_data(engine, spec, import_publication.artifact_id)
    publish_data_contracts(engine, spec.data_contract_catalog)
    calendar_publication = CalendarPublicationService(engine).publish(_calendar(spec))
    with engine.connect() as connection:
        calendar_version_id = connection.execute(
            text(
                "SELECT calendar_version_id FROM catalog.calendar_version "
                "WHERE artifact_id=:artifact"
            ),
            {"artifact": calendar_publication.artifact_id},
        ).scalar_one()
    cleaning_id, cleaning_artifact_id, cleaning_reused = _publish_v2_cleaning(
        engine, spec, import_publication.artifact_id
    )
    reused = []
    if import_publication.reused:
        reused.append("transfer_manifest")
    if master_reused:
        reused.append("master_data")
    if calendar_publication.reused:
        reused.append("calendar")
    if cleaning_reused:
        reused.append("cleaning")
    return GreenBaselineFoundationPublication(
        contract=_FOUNDATION_CONTRACT,
        plan_fingerprint=spec.plan.plan_fingerprint,
        external_import_manifest_id=str(import_publication.external_import_manifest_id),
        external_import_artifact_id=str(import_publication.artifact_id),
        master_data_release_id=str(master_id),
        master_data_artifact_id=str(master_artifact_id),
        calendar_version_id=str(calendar_version_id),
        calendar_artifact_id=str(calendar_publication.artifact_id),
        cleaning_version_id=str(cleaning_id),
        cleaning_artifact_id=str(cleaning_artifact_id),
        asset_count=asset_count,
        security_count=security_count,
        security_identifier_count=identifier_count,
        calendar_session_count=spec.plan.calendar_session_count,
        reused_roles=tuple(reused),
    )
