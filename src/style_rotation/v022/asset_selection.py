from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import Any, cast

from sqlalchemy import Engine, bindparam, text
from sqlalchemy.engine import Connection

from style_rotation.core.canonical import sha256_hexdigest
from style_rotation.lineage.service import ArtifactService, DependencyInput


@dataclass(frozen=True, slots=True)
class ExplicitAssetSelectionPublication:
    selection_id: uuid.UUID
    artifact_id: uuid.UUID
    selection_fingerprint: str
    asset_context_document: dict[str, Any]
    reused: bool


class ExplicitAssetSelectionService:
    """Publish one immutable homogeneous candidate-asset selection."""

    def publish(
        self,
        connection: Connection,
        *,
        asset_registry_release_id: uuid.UUID,
        security_ids: tuple[uuid.UUID, ...],
        created_by: str,
    ) -> ExplicitAssetSelectionPublication:
        canonical_ids = tuple(sorted(set(security_ids), key=str))
        if len(canonical_ids) != len(security_ids):
            raise ValueError("Explicit Asset Selection contains duplicate Securities")
        if len(canonical_ids) < 2:
            raise ValueError("Explicit Asset Selection requires at least two candidates")
        if not created_by.strip():
            raise ValueError("Explicit Asset Selection creator is required")
        release = connection.execute(
            text(
                """
                SELECT release.asset_registry_release_id,release.artifact_id,
                       release.catalog_version
                 FROM catalog.asset_registry_release release
                  JOIN lineage.artifact artifact ON artifact.artifact_id=release.artifact_id
                 WHERE artifact.status='published'
                   AND release.asset_registry_release_id=:release
                """
            ),
            {"release": asset_registry_release_id},
        ).mappings().one_or_none()
        if release is None:
            raise LookupError("Published Asset Registry Release not found")
        rows = connection.execute(
            text(
                """
                SELECT security.security_id,security.security_key,
                       security.legacy_asset_id,profile.instrument_type,
                       profile.tradability,
                       EXISTS (
                         SELECT 1 FROM data.daily_bar bar
                         JOIN data.dataset_publication publication
                           ON publication.dataset_publication_id=bar.dataset_publication_id
                         JOIN lineage.artifact artifact
                           ON artifact.artifact_id=publication.artifact_id
                          AND artifact.status='published'
                        WHERE bar.asset_id=security.legacy_asset_id
                       ) AS canonical_data_available
                  FROM catalog.security security
                  JOIN catalog.security_profile profile
                    ON profile.asset_registry_release_id=:release
                   AND profile.security_id=security.security_id
                 WHERE security.security_id IN :security_ids
                 ORDER BY security.security_id
                """
            ).bindparams(bindparam("security_ids", expanding=True)),
            {"release": release["asset_registry_release_id"], "security_ids": canonical_ids},
        ).mappings().all()
        if len(rows) != len(canonical_ids):
            raise ValueError("Explicit Asset Selection contains an unknown Security")
        if any(
            row["legacy_asset_id"] is None
            or row["tradability"] != "tradable"
            or not row["canonical_data_available"]
            for row in rows
        ):
            raise ValueError(
                "Explicit Asset Selection requires tradable Securities with published daily bars"
            )
        groups = {_selection_group(str(row["instrument_type"])) for row in rows}
        if None in groups or len(groups) != 1:
            raise ValueError(
                "Explicit Asset Selection must contain only stocks/ADRs or only funds/ETPs"
            )
        selection_group = cast(str, next(iter(groups)))
        member_document = [
            {
                "ordinal": ordinal,
                "security_id": str(row["security_id"]),
                "security_key": row["security_key"],
                "instrument_type": row["instrument_type"],
            }
            for ordinal, row in enumerate(rows)
        ]
        identity_payload = {
            "contract_version": "v0.22.0",
            "selection_kind": "explicit_security_selection",
            "asset_registry_release_id": str(release["asset_registry_release_id"]),
            "asset_registry_artifact_id": str(release["artifact_id"]),
            "asset_registry_catalog_version": release["catalog_version"],
            "selection_group": selection_group,
            "members": member_document,
        }
        selection_fingerprint = sha256_hexdigest(identity_payload)
        selection_id = uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"bird:v0.22:explicit-asset-selection:{selection_fingerprint}",
        )
        asset_document = {
            **identity_payload,
            "asset_context_key": f"explicit_{selection_fingerprint[:16]}",
            "explicit_asset_selection_id": str(selection_id),
        }
        service = ArtifactService(cast(Engine, _BoundConnection(connection)))

        def write_projection(bound: Connection, artifact_id: uuid.UUID) -> None:
            full_document = {
                **asset_document,
                "explicit_asset_selection_artifact_id": str(artifact_id),
            }
            bound.execute(
                text(
                    """
                    INSERT INTO workspace.v022_explicit_asset_selection (
                      explicit_asset_selection_id,artifact_id,contract_version,
                      asset_registry_release_id,asset_registry_artifact_id,
                      selection_group,member_count,selection_document,
                      selection_fingerprint,created_by
                    ) VALUES (
                      :id,:artifact,'v0.22.0',:release,:registry_artifact,
                      :selection_group,:member_count,CAST(:document AS jsonb),
                      :fingerprint,:created_by
                    )
                    """
                ),
                {
                    "id": selection_id,
                    "artifact": artifact_id,
                    "release": release["asset_registry_release_id"],
                    "registry_artifact": release["artifact_id"],
                    "selection_group": selection_group,
                    "member_count": len(rows),
                    "document": json.dumps(full_document, sort_keys=True),
                    "fingerprint": selection_fingerprint,
                    "created_by": created_by,
                },
            )
            for ordinal, row in enumerate(rows):
                bound.execute(
                    text(
                        """
                        INSERT INTO workspace.v022_explicit_asset_selection_member (
                          explicit_asset_selection_id,ordinal,security_id,security_key,
                          instrument_type,member_role
                        ) VALUES (:selection,:ordinal,:security,:security_key,
                                  :instrument_type,'candidate')
                        """
                    ),
                    {
                        "selection": selection_id,
                        "ordinal": ordinal,
                        "security": row["security_id"],
                        "security_key": row["security_key"],
                        "instrument_type": row["instrument_type"],
                    },
                )

        publication = service.publish(
            artifact_type="v022_explicit_asset_selection",
            artifact_key=f"explicit_asset_selection__{selection_fingerprint}",
            version_number=1,
            semantic_payload=identity_payload,
            content_payload=identity_payload,
            dependencies=(DependencyInput(release["artifact_id"], "asset_registry_release", 0),),
            reason="publish immutable v0.22 explicit Asset selection",
            draft_writer=write_projection,
        )
        return ExplicitAssetSelectionPublication(
            selection_id,
            publication.artifact_id,
            selection_fingerprint,
            {
                **asset_document,
                "explicit_asset_selection_artifact_id": str(publication.artifact_id),
            },
            publication.reused,
        )


def _selection_group(instrument_type: str) -> str | None:
    normalized = instrument_type.casefold().replace("-", " ")
    if normalized in {"common stock", "adr"}:
        return "stock"
    if "etf" in normalized or "etp" in normalized:
        return "fund"
    return None


class _BoundTransaction:
    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def __enter__(self) -> Connection:
        return self._connection

    def __exit__(self, *_args: object) -> None:
        return None


class _BoundConnection:
    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def begin(self) -> _BoundTransaction:
        return _BoundTransaction(self._connection)
