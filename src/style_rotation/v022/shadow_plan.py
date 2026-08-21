from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from functools import partial
from typing import Literal

from sqlalchemy import Engine, text
from sqlalchemy.engine import Connection, RowMapping

from style_rotation.core.canonical import sha256_hexdigest
from style_rotation.lineage.service import ArtifactService, DependencyInput

Frequency = Literal["weekly", "monthly"]
ContextClass = Literal["etf", "large_cap", "other"]
RepresentativeRole = Literal["active_product_shadow", "shadow_only"]


@dataclass(frozen=True, slots=True)
class ShadowContext:
    asset_context_key: str
    asset_context_class: ContextClass
    frequency: Frequency


@dataclass(frozen=True, slots=True)
class ShadowRepresentative:
    context: ShadowContext
    product_enrollment_id: uuid.UUID
    representative_role: RepresentativeRole


@dataclass(frozen=True, slots=True)
class ShadowPlanPublication:
    shadow_plan_id: uuid.UUID
    artifact_id: uuid.UUID
    plan_fingerprint: str
    representative_count: int
    weekly_count: int
    monthly_count: int
    covers_etf: bool
    covers_large_cap: bool
    reused: bool


class ShadowPlanService:
    """Freezes representative Enrollment/Execution identity before observations accrue."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._artifacts = ArtifactService(engine)

    def publish(
        self,
        *,
        plan_key: str,
        version_number: int,
        representatives: tuple[ShadowRepresentative, ...],
    ) -> ShadowPlanPublication:
        plan_key = plan_key.strip()
        if not plan_key or version_number < 1 or not representatives:
            raise ValueError("Shadow Plan key, positive version, and representatives are required")
        contexts = [item.context for item in representatives]
        context_keys = [(item.asset_context_key, item.frequency) for item in contexts]
        enrollment_ids = [item.product_enrollment_id for item in representatives]
        if len(context_keys) != len(set(context_keys)):
            raise ValueError("Shadow Plan requires one representative per context and frequency")
        if len(enrollment_ids) != len(set(enrollment_ids)):
            raise ValueError("Shadow Plan cannot merge one Enrollment across context slots")
        resolved = tuple(self._resolve(item) for item in representatives)
        semantic_representatives = [
            {
                "ordinal": ordinal,
                "asset_context_key": item.context.asset_context_key.strip(),
                "asset_context_class": item.context.asset_context_class,
                "asset_context_fingerprint": row["asset_context_fingerprint"],
                "frequency": item.context.frequency,
                "representative_role": item.representative_role,
                "minimum_required_sessions": 12 if item.context.frequency == "weekly" else 3,
                "product_enrollment_id": str(item.product_enrollment_id),
                "enrollment_fingerprint": row["enrollment_fingerprint"],
                "execution_version_id": str(row["execution_version_id"]),
                "execution_fingerprint": row["execution_fingerprint"],
                "drives_formal_capital": False,
            }
            for ordinal, (item, row) in enumerate(
                zip(representatives, resolved, strict=True), start=1
            )
        ]
        if any(not item["asset_context_key"] for item in semantic_representatives):
            raise ValueError("Shadow asset context keys must be nonblank")
        semantic = {
            "contract_version": "v0.22.0",
            "plan_key": plan_key,
            "version_number": version_number,
            "representatives": semantic_representatives,
        }
        fingerprint = sha256_hexdigest(semantic)
        plan_id = uuid.uuid4()
        dependencies = tuple(
            DependencyInput(row["enrollment_artifact_id"], "shadow_enrollment", ordinal)
            for ordinal, row in enumerate(resolved)
        )
        publication = self._artifacts.publish(
            artifact_type="v022_shadow_plan",
            artifact_key=plan_key,
            version_number=version_number,
            semantic_payload=semantic,
            content_payload=semantic,
            dependencies=dependencies,
            reason="publish frozen v0.22 representative Shadow Plan",
            draft_writer=partial(
                self._write,
                plan_id=plan_id,
                plan_key=plan_key,
                version_number=version_number,
                representatives=semantic_representatives,
                fingerprint=fingerprint,
            ),
        )
        with self._engine.connect() as connection:
            row = connection.execute(
                text("SELECT * FROM workspace.v022_shadow_plan WHERE artifact_id=:artifact"),
                {"artifact": publication.artifact_id},
            ).mappings().one()
        return ShadowPlanPublication(
            row["shadow_plan_id"],
            row["artifact_id"],
            row["plan_fingerprint"],
            len(representatives),
            sum(item.context.frequency == "weekly" for item in representatives),
            sum(item.context.frequency == "monthly" for item in representatives),
            any(item.context.asset_context_class == "etf" for item in representatives),
            any(item.context.asset_context_class == "large_cap" for item in representatives),
            publication.reused,
        )

    def _resolve(self, representative: ShadowRepresentative) -> RowMapping:
        with self._engine.connect() as connection:
            row = connection.execute(
                text(
                    """
                    SELECT enrollment.product_enrollment_id,
                           enrollment.artifact_id AS enrollment_artifact_id,
                           enrollment.enrollment_fingerprint,enrollment.execution_version_id,
                           execution.execution_fingerprint,
                           configuration.semantic_identity_document->>'frequency' AS frequency,
                           configuration.semantic_identity_document->>'asset_context_fingerprint'
                             AS asset_context_fingerprint,
                           context.asset_context_document->>'asset_context_key'
                             AS asset_context_key,
                           context.asset_context_document->'members' AS asset_context_members
                      FROM product.v022_product_enrollment enrollment
                      JOIN lineage.artifact enrollment_artifact
                        ON enrollment_artifact.artifact_id=enrollment.artifact_id
                       AND enrollment_artifact.status='published'
                      JOIN product.v022_execution_version execution
                        ON execution.execution_version_id=enrollment.execution_version_id
                      JOIN lineage.artifact execution_artifact
                        ON execution_artifact.artifact_id=execution.artifact_id
                       AND execution_artifact.status='published'
                      JOIN experiment.v022_research_configuration_snapshot configuration
                        ON configuration.configuration_snapshot_id=
                           execution.configuration_snapshot_id
                      JOIN LATERAL (
                        SELECT draft.asset_context_document
                          FROM workspace.v022_graph_draft draft
                         WHERE draft.asset_context_fingerprint=
                           configuration.semantic_identity_document->>'asset_context_fingerprint'
                         ORDER BY draft.created_at LIMIT 1
                      ) context ON true
                     WHERE enrollment.product_enrollment_id=:enrollment
                    """
                ),
                {"enrollment": representative.product_enrollment_id},
            ).mappings().one_or_none()
        if row is None:
            raise ValueError("Shadow representative requires a published v0.22 Enrollment")
        if row["frequency"] != representative.context.frequency:
            raise ValueError("Shadow representative frequency does not match its Configuration")
        if row["asset_context_key"] != representative.context.asset_context_key:
            raise ValueError("Shadow representative key does not match its frozen Asset Context")
        members = row["asset_context_members"]
        if not isinstance(members, list) or not members:
            raise ValueError("Shadow representative Asset Context has no frozen members")
        all_etf = all(
            isinstance(item, dict) and "ETF" in str(item.get("instrument_type", "")).upper()
            for item in members
        )
        key = str(row["asset_context_key"]).lower()
        context_class: ContextClass = "other"
        if all_etf:
            context_class = "etf"
        elif "large_cap" in key or "large-cap" in key:
            context_class = "large_cap"
        if context_class != representative.context.asset_context_class:
            raise ValueError("Shadow representative class does not match its frozen Asset Context")
        return row

    @staticmethod
    def _write(
        connection: Connection,
        artifact_id: uuid.UUID,
        *,
        plan_id: uuid.UUID,
        plan_key: str,
        version_number: int,
        representatives: list[dict[str, object]],
        fingerprint: str,
    ) -> None:
        contexts = [
            {
                key: item[key]
                for key in ("asset_context_key", "asset_context_class", "frequency")
            }
            for item in representatives
        ]
        connection.execute(
            text(
                """
                INSERT INTO workspace.v022_shadow_plan (
                  shadow_plan_id,artifact_id,plan_key,version_number,
                  supported_context_document,plan_fingerprint
                ) VALUES (:id,:artifact,:key,:version,CAST(:contexts AS jsonb),:fingerprint)
                """
            ),
            {
                "id": plan_id,
                "artifact": artifact_id,
                "key": plan_key,
                "version": version_number,
                "contexts": json.dumps(contexts, sort_keys=True),
                "fingerprint": fingerprint,
            },
        )
        for item in representatives:
            connection.execute(
                text(
                    """
                    INSERT INTO workspace.v022_shadow_representative (
                      shadow_representative_id,shadow_plan_id,ordinal,product_enrollment_id,
                      execution_version_id,asset_context_key,asset_context_class,
                      asset_context_fingerprint,frequency,representative_role,
                      minimum_required_sessions,drives_formal_capital
                    ) VALUES (
                      :id,:plan,:ordinal,:enrollment,:execution,:context_key,:context_class,
                      :context_fingerprint,:frequency,:role,:minimum,false
                    )
                    """
                ),
                {
                    "id": uuid.uuid4(),
                    "plan": plan_id,
                    "ordinal": item["ordinal"],
                    "enrollment": item["product_enrollment_id"],
                    "execution": item["execution_version_id"],
                    "context_key": item["asset_context_key"],
                    "context_class": item["asset_context_class"],
                    "context_fingerprint": item["asset_context_fingerprint"],
                    "frequency": item["frequency"],
                    "role": item["representative_role"],
                    "minimum": item["minimum_required_sessions"],
                },
            )
