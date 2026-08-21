from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import partial
from typing import Any

from sqlalchemy import Engine, text
from sqlalchemy.engine import Connection, RowMapping

from style_rotation.core.canonical import sha256_hexdigest
from style_rotation.lineage.service import ArtifactService, DependencyInput
from style_rotation.v022.shadow_coverage import ShadowComparisonService


@dataclass(frozen=True, slots=True)
class ComparatorField:
    canonical_key: str
    v021_path: tuple[str, ...]
    v022_path: tuple[str, ...]

    def validated(self) -> ComparatorField:
        if not self.canonical_key.strip() or not self.v021_path or not self.v022_path:
            raise ValueError("Comparator fields require a key and two nonempty paths")
        if any(not part.strip() for part in (*self.v021_path, *self.v022_path)):
            raise ValueError("Comparator path elements must be nonblank")
        return self


@dataclass(frozen=True, slots=True)
class ProjectedComparison:
    matched: bool
    comparison_document: dict[str, object]


@dataclass(frozen=True, slots=True)
class ComparatorPublication:
    shadow_comparator_version_id: uuid.UUID
    artifact_id: uuid.UUID
    comparator_fingerprint: str
    reused: bool


@dataclass(frozen=True, slots=True)
class V021ReferencePublication:
    shadow_v021_reference_decision_id: uuid.UUID
    artifact_id: uuid.UUID
    reference_fingerprint: str
    reused: bool


@dataclass(frozen=True, slots=True)
class ComparisonCoordinationResult:
    ready_pair_count: int
    published_comparison_count: int
    skipped_existing_count: int


def compare_projected_documents(
    fields: tuple[ComparatorField, ...],
    v021_document: dict[str, Any],
    v022_document: dict[str, Any],
) -> ProjectedComparison:
    if not fields:
        raise ValueError("Comparator policy requires at least one projected field")
    validated = tuple(item.validated() for item in fields)
    keys = [item.canonical_key for item in validated]
    if len(keys) != len(set(keys)):
        raise ValueError("Comparator canonical field keys must be unique")
    left: dict[str, object] = {}
    right: dict[str, object] = {}
    differences: list[str] = []
    missing: list[str] = []
    for field in validated:
        left_found, left_value = _resolve(v021_document, field.v021_path)
        right_found, right_value = _resolve(v022_document, field.v022_path)
        if left_found:
            left[field.canonical_key] = left_value
        if right_found:
            right[field.canonical_key] = right_value
        if not left_found or not right_found:
            missing.append(field.canonical_key)
        elif left_value != right_value:
            differences.append(field.canonical_key)
    matched = not missing and not differences
    return ProjectedComparison(
        matched,
        {
            "algorithm": "canonical_projection_equal_v1",
            "v021_projection": left,
            "v022_projection": right,
            "different_fields": differences,
            "missing_fields": missing,
            "matched": matched,
        },
    )


class ShadowComparatorVersionService:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._artifacts = ArtifactService(engine)

    def publish(
        self,
        *,
        comparator_key: str,
        version_number: int,
        fields: tuple[ComparatorField, ...],
    ) -> ComparatorPublication:
        comparator_key = comparator_key.strip()
        if not comparator_key or version_number < 1:
            raise ValueError("Comparator key and positive version are required")
        validated = tuple(item.validated() for item in fields)
        if not validated:
            raise ValueError("Comparator requires at least one field")
        keys = [item.canonical_key for item in validated]
        if len(keys) != len(set(keys)):
            raise ValueError("Comparator canonical field keys must be unique")
        policy: dict[str, object] = {
            "algorithm": "canonical_projection_equal_v1",
            "fields": [
                {
                    "canonical_key": item.canonical_key,
                    "v021_path": list(item.v021_path),
                    "v022_path": list(item.v022_path),
                }
                for item in validated
            ],
        }
        semantic = {
            "contract_version": "v0.22.0",
            "comparator_key": comparator_key,
            "version_number": version_number,
            "policy": policy,
        }
        fingerprint = sha256_hexdigest(semantic)
        existing = self._existing(comparator_key, version_number)
        if existing is not None:
            if existing.comparator_fingerprint != fingerprint:
                raise ValueError("Comparator version is already bound to different semantics")
            return existing
        comparator_id = uuid.uuid5(
            uuid.NAMESPACE_URL, f"bird:v0.22:shadow-comparator:{fingerprint}"
        )
        publication = self._artifacts.publish(
            artifact_type="v022_shadow_comparator_version",
            artifact_key=comparator_key,
            version_number=version_number,
            semantic_payload=semantic,
            content_payload=semantic,
            reason="publish formal v0.22 Shadow Comparator Version",
            draft_writer=partial(
                self._write,
                comparator_id=comparator_id,
                comparator_key=comparator_key,
                version_number=version_number,
                policy=policy,
                fingerprint=fingerprint,
            ),
        )
        return ComparatorPublication(
            comparator_id, publication.artifact_id, fingerprint, publication.reused
        )

    def _existing(self, key: str, version: int) -> ComparatorPublication | None:
        with self._engine.connect() as connection:
            row = connection.execute(
                text(
                    "SELECT comparator.*,artifact.status "
                    "FROM workspace.v022_shadow_comparator_version comparator "
                    "JOIN lineage.artifact artifact ON artifact.artifact_id=comparator.artifact_id "
                    "WHERE comparator.comparator_key=:key AND comparator.version_number=:version"
                ),
                {"key": key, "version": version},
            ).mappings().one_or_none()
        if row is None:
            return None
        if row["status"] != "published":
            raise ValueError("Comparator Artifact is not published")
        return ComparatorPublication(
            row["shadow_comparator_version_id"],
            row["artifact_id"],
            row["comparator_fingerprint"],
            True,
        )

    @staticmethod
    def _write(
        connection: Connection,
        artifact_id: uuid.UUID,
        *,
        comparator_id: uuid.UUID,
        comparator_key: str,
        version_number: int,
        policy: dict[str, object],
        fingerprint: str,
    ) -> None:
        connection.execute(
            text(
                """
                INSERT INTO workspace.v022_shadow_comparator_version (
                  shadow_comparator_version_id,artifact_id,comparator_key,version_number,
                  algorithm_key,policy_document,comparator_fingerprint
                ) VALUES (:id,:artifact,:key,:version,'canonical_projection_equal_v1',
                          CAST(:policy AS jsonb),:fingerprint)
                """
            ),
            {
                "id": comparator_id,
                "artifact": artifact_id,
                "key": comparator_key,
                "version": version_number,
                "policy": json.dumps(policy, sort_keys=True),
                "fingerprint": fingerprint,
            },
        )


class V021ShadowReferenceService:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._artifacts = ArtifactService(engine)

    def publish(
        self,
        *,
        shadow_runtime_binding_id: uuid.UUID,
        decision_session_id: uuid.UUID,
        decision_document: dict[str, object],
        source_artifact_id: uuid.UUID | None = None,
        source_dependency_role: str = "v021_monitoring_snapshot",
        known_at: datetime | None = None,
    ) -> V021ReferencePublication:
        occurred_at = known_at or datetime.now(UTC)
        if occurred_at.tzinfo is None or not decision_document:
            raise ValueError("v0.21 Reference requires an aware known-at and decision document")
        with self._engine.connect() as connection:
            identity = connection.execute(
                text(
                    """
                    SELECT binding.shadow_representative_id,binding.binding_fingerprint,
                           binding.artifact_id AS binding_artifact_id,
                           binding.v021_execution_spec_artifact_id,
                           session.decision_cutoff_at
                      FROM workspace.v022_shadow_runtime_binding binding
                      JOIN lineage.artifact artifact ON artifact.artifact_id=binding.artifact_id
                       AND artifact.status='published'
                      JOIN workspace.v022_shadow_representative representative
                        ON representative.shadow_representative_id=binding.shadow_representative_id
                      JOIN product.v022_product_enrollment enrollment
                        ON enrollment.product_enrollment_id=representative.product_enrollment_id
                      JOIN product.v022_decision_schedule_session session
                        ON session.decision_schedule_version_id=
                           enrollment.decision_schedule_version_id
                     WHERE binding.shadow_runtime_binding_id=:binding
                       AND session.decision_session_id=:session
                    """
                ),
                {"binding": shadow_runtime_binding_id, "session": decision_session_id},
            ).mappings().one_or_none()
        if identity is None or occurred_at < identity["decision_cutoff_at"]:
            raise ValueError("v0.21 Reference requires its exact due Binding Session")
        semantic: dict[str, object] = {
            "contract_version": "v0.21-shadow-reference-v1",
            "shadow_runtime_binding_id": str(shadow_runtime_binding_id),
            "binding_fingerprint": identity["binding_fingerprint"],
            "shadow_representative_id": str(identity["shadow_representative_id"]),
            "decision_session_id": str(decision_session_id),
            "decision_document": decision_document,
            "known_at": occurred_at,
        }
        if source_artifact_id is not None:
            semantic["source_artifact_id"] = str(source_artifact_id)
        fingerprint = sha256_hexdigest(semantic)
        existing = self._existing(identity["shadow_representative_id"], decision_session_id)
        if existing is not None:
            if existing.reference_fingerprint != fingerprint:
                raise ValueError("v0.21 Reference Session already has different semantics")
            return existing
        reference_id = uuid.uuid5(
            uuid.NAMESPACE_URL, f"bird:v0.22:v021-shadow-reference:{fingerprint}"
        )
        dependencies = [
            DependencyInput(identity["binding_artifact_id"], "shadow_runtime_binding", 0),
            DependencyInput(
                identity["v021_execution_spec_artifact_id"], "v021_execution_spec", 1
            ),
        ]
        if source_artifact_id is not None:
            source_dependency_role = source_dependency_role.strip()
            if not source_dependency_role:
                raise ValueError("v0.21 Reference source dependency role is required")
            dependencies.append(
                DependencyInput(source_artifact_id, source_dependency_role, 2)
            )
        publication = self._artifacts.publish(
            artifact_type="v021_shadow_reference_decision",
            artifact_key=fingerprint,
            version_number=1,
            semantic_payload=semantic,
            content_payload=semantic,
            dependencies=tuple(dependencies),
            reason="publish exact-session v0.21 Shadow Reference Decision",
            draft_writer=partial(
                self._write,
                reference_id=reference_id,
                binding_id=shadow_runtime_binding_id,
                representative_id=identity["shadow_representative_id"],
                session_id=decision_session_id,
                decision_document=decision_document,
                known_at=occurred_at,
                fingerprint=fingerprint,
            ),
        )
        return V021ReferencePublication(
            reference_id, publication.artifact_id, fingerprint, publication.reused
        )

    def _existing(
        self, representative_id: uuid.UUID, session_id: uuid.UUID
    ) -> V021ReferencePublication | None:
        with self._engine.connect() as connection:
            row = connection.execute(
                text(
                    "SELECT reference.*,artifact.status "
                    "FROM workspace.v022_shadow_v021_reference_decision reference "
                    "JOIN lineage.artifact artifact ON artifact.artifact_id=reference.artifact_id "
                    "WHERE reference.shadow_representative_id=:representative "
                    "AND reference.decision_session_id=:session"
                ),
                {"representative": representative_id, "session": session_id},
            ).mappings().one_or_none()
        if row is None:
            return None
        if row["status"] != "published":
            raise ValueError("v0.21 Reference Artifact is not published")
        return V021ReferencePublication(
            row["shadow_v021_reference_decision_id"],
            row["artifact_id"],
            row["reference_fingerprint"],
            True,
        )

    @staticmethod
    def _write(
        connection: Connection,
        artifact_id: uuid.UUID,
        *,
        reference_id: uuid.UUID,
        binding_id: uuid.UUID,
        representative_id: uuid.UUID,
        session_id: uuid.UUID,
        decision_document: dict[str, object],
        known_at: datetime,
        fingerprint: str,
    ) -> None:
        connection.execute(
            text(
                """
                INSERT INTO workspace.v022_shadow_v021_reference_decision (
                  shadow_v021_reference_decision_id,artifact_id,shadow_runtime_binding_id,
                  shadow_representative_id,decision_session_id,decision_document,known_at,
                  reference_fingerprint
                ) VALUES (:id,:artifact,:binding,:representative,:session,
                          CAST(:document AS jsonb),:known_at,:fingerprint)
                """
            ),
            {
                "id": reference_id,
                "artifact": artifact_id,
                "binding": binding_id,
                "representative": representative_id,
                "session": session_id,
                "document": json.dumps(decision_document, sort_keys=True),
                "known_at": known_at,
                "fingerprint": fingerprint,
            },
        )


class ShadowComparisonCoordinator:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._comparisons = ShadowComparisonService(engine)

    def publish_ready(self, *, known_at: datetime | None = None) -> ComparisonCoordinationResult:
        occurred_at = known_at or datetime.now(UTC)
        if occurred_at.tzinfo is None:
            raise ValueError("Comparison coordination timestamp must be timezone-aware")
        with self._engine.connect() as connection:
            release_state = connection.scalar(
                text(
                    "SELECT to_state FROM workspace.v022_release_transition "
                    "ORDER BY sequence_number DESC LIMIT 1"
                )
            )
            if release_state not in {"shadow", "explicit_eligible", "default"}:
                raise ValueError("Release Control does not allow Shadow Comparison publication")
            ready = tuple(self._ready_rows(connection, occurred_at))
        published = 0
        skipped = 0
        for row in ready:
            if self._publish_one(row, occurred_at):
                published += 1
            else:
                skipped += 1
        return ComparisonCoordinationResult(len(ready), published, skipped)

    @staticmethod
    def _ready_rows(connection: Connection, known_at: datetime) -> tuple[RowMapping, ...]:
        return tuple(
            connection.execute(
                text(
                    """
                    SELECT intent.shadow_dual_run_intent_id,intent.decision_session_id,
                           intent.decision_cutoff_at,binding.shadow_representative_id,
                           binding.comparator_artifact_id,
                           reference.artifact_id AS reference_artifact_id,
                           reference.decision_document AS v021_document,
                           decision.product_decision_id,decision.decision_status,
                           decision.decision_document AS v022_document,
                           comparator.policy_document
                      FROM workspace.v022_shadow_dual_run_intent intent
                      JOIN workspace.v022_shadow_runtime_binding binding
                        ON binding.shadow_runtime_binding_id=intent.shadow_runtime_binding_id
                      JOIN workspace.v022_shadow_comparator_version comparator
                        ON comparator.artifact_id=binding.comparator_artifact_id
                      JOIN ops.v022_shadow_work_item v021_work
                        ON v021_work.shadow_dual_run_intent_id=intent.shadow_dual_run_intent_id
                       AND v021_work.runtime_contract='v0.21' AND v021_work.status='completed'
                      JOIN workspace.v022_shadow_v021_reference_decision reference
                        ON reference.artifact_id=v021_work.v021_reference_artifact_id
                      JOIN ops.v022_shadow_work_item v022_work
                        ON v022_work.shadow_dual_run_intent_id=intent.shadow_dual_run_intent_id
                       AND v022_work.runtime_contract='v0.22' AND v022_work.status='completed'
                      JOIN product.v022_product_decision decision
                        ON decision.product_decision_id=v022_work.v022_product_decision_id
                     WHERE intent.decision_cutoff_at<=:known_at
                       AND NOT EXISTS (
                         SELECT 1 FROM workspace.v022_shadow_decision_comparison comparison
                          WHERE comparison.shadow_representative_id=
                                binding.shadow_representative_id
                            AND comparison.decision_session_id=intent.decision_session_id
                            AND comparison.comparator_artifact_id=binding.comparator_artifact_id
                       )
                     ORDER BY intent.decision_cutoff_at,intent.shadow_dual_run_intent_id
                    """
                ),
                {"known_at": known_at},
            ).mappings()
        )

    def _publish_one(self, row: RowMapping, known_at: datetime) -> bool:
        lock_key = f"v022-shadow-comparison:{row['shadow_dual_run_intent_id']}"
        with self._engine.connect() as lock_connection:
            lock_connection.execute(
                text("SELECT pg_advisory_lock(hashtextextended(:key,0))"), {"key": lock_key}
            )
            try:
                exists = lock_connection.scalar(
                    text(
                        "SELECT count(*) FROM workspace.v022_shadow_decision_comparison "
                        "WHERE shadow_representative_id=:representative AND "
                        "decision_session_id=:session AND comparator_artifact_id=:comparator"
                    ),
                    {
                        "representative": row["shadow_representative_id"],
                        "session": row["decision_session_id"],
                        "comparator": row["comparator_artifact_id"],
                    },
                )
                if exists:
                    return False
                fields = _policy_fields(row["policy_document"])
                compared = compare_projected_documents(
                    fields, row["v021_document"], row["v022_document"]
                )
                if row["decision_status"] == "missing":
                    compared = ProjectedComparison(
                        False,
                        {
                            **compared.comparison_document,
                            "matched": False,
                            "runtime_blocker": "missing_v022_decision",
                        },
                    )
                self._comparisons.publish(
                    shadow_representative_id=row["shadow_representative_id"],
                    v022_product_decision_id=row["product_decision_id"],
                    comparator_artifact_id=row["comparator_artifact_id"],
                    v021_reference_artifact_id=row["reference_artifact_id"],
                    outcome="matched" if compared.matched else "different",
                    comparison_document=compared.comparison_document,
                    known_at=known_at,
                )
                return True
            finally:
                lock_connection.execute(
                    text("SELECT pg_advisory_unlock(hashtextextended(:key,0))"),
                    {"key": lock_key},
                )


def _policy_fields(policy: object) -> tuple[ComparatorField, ...]:
    if not isinstance(policy, dict) or policy.get("algorithm") != "canonical_projection_equal_v1":
        raise ValueError("Unsupported or malformed Shadow Comparator policy")
    raw_fields = policy.get("fields")
    if not isinstance(raw_fields, list):
        raise ValueError("Shadow Comparator policy fields must be an array")
    fields: list[ComparatorField] = []
    for raw in raw_fields:
        if not isinstance(raw, dict):
            raise ValueError("Shadow Comparator field must be an object")
        key = raw.get("canonical_key")
        v021_path = raw.get("v021_path")
        v022_path = raw.get("v022_path")
        if not isinstance(key, str) or not isinstance(v021_path, list) or not isinstance(
            v022_path, list
        ):
            raise ValueError("Shadow Comparator field identity is malformed")
        if not all(isinstance(item, str) for item in (*v021_path, *v022_path)):
            raise ValueError("Shadow Comparator paths must contain strings")
        fields.append(ComparatorField(key, tuple(v021_path), tuple(v022_path)))
    return tuple(fields)


def _resolve(document: dict[str, Any], path: tuple[str, ...]) -> tuple[bool, object]:
    value: object = document
    for part in path:
        if not isinstance(value, dict) or part not in value:
            return False, None
        value = value[part]
    return True, value
