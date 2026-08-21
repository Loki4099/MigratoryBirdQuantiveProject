from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import partial
from typing import Any, Literal

from sqlalchemy import Engine, text
from sqlalchemy.engine import Connection, RowMapping

from style_rotation.core.canonical import sha256_hexdigest
from style_rotation.lineage.service import ArtifactService, DependencyInput
from style_rotation.v022.shadow_comparator import V021ShadowReferenceService
from style_rotation.v022.shadow_v021_replay import V021ShadowReplayService

RuntimeContract = Literal["v0.21", "v0.22"]
_HASH = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True, slots=True)
class RuntimeCapability:
    runtime_contract: RuntimeContract
    compiler_version: str
    executor_version: str
    environment_fingerprint: str
    capability_key: str

    def validated(self) -> RuntimeCapability:
        if self.runtime_contract not in {"v0.21", "v0.22"}:
            raise ValueError(f"Unsupported runtime contract: {self.runtime_contract}")
        values = (self.compiler_version, self.executor_version, self.capability_key)
        if any(not value.strip() for value in values):
            raise ValueError("Runtime capability values must be nonblank")
        if _HASH.fullmatch(self.environment_fingerprint) is None:
            raise ValueError("Runtime capability environment fingerprint must be SHA-256")
        return self

    def identity(self) -> tuple[str, str, str, str, str]:
        return (
            self.runtime_contract,
            self.compiler_version,
            self.executor_version,
            self.environment_fingerprint,
            self.capability_key,
        )


@dataclass(frozen=True, slots=True)
class ShadowRuntimeBindingPublication:
    shadow_runtime_binding_id: uuid.UUID
    artifact_id: uuid.UUID
    binding_fingerprint: str
    reused: bool


@dataclass(frozen=True, slots=True)
class ShadowScheduleResult:
    shadow_runtime_binding_id: uuid.UUID
    eligible_session_count: int
    created_intent_count: int
    created_work_item_count: int


@dataclass(frozen=True, slots=True)
class ClaimedShadowWork:
    shadow_work_item_id: uuid.UUID
    shadow_dual_run_intent_id: uuid.UUID
    runtime_contract: RuntimeContract
    decision_session_id: uuid.UUID
    decision_cutoff_at: datetime
    fencing_token: int
    attempt_count: int


@dataclass(frozen=True, slots=True)
class ShadowV022DecisionOutcome:
    status: Literal["idle", "completed"]
    shadow_work_item_id: uuid.UUID | None = None
    product_decision_id: uuid.UUID | None = None


@dataclass(frozen=True, slots=True)
class ShadowV021ReferenceOutcome:
    status: Literal["idle", "completed"]
    shadow_work_item_id: uuid.UUID | None = None
    reference_artifact_id: uuid.UUID | None = None


class ShadowRuntimeBindingService:
    """Freezes both runtime legs before any prospective session is scheduled."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._artifacts = ArtifactService(engine)

    def publish(
        self,
        *,
        shadow_representative_id: uuid.UUID,
        v021_product_enrollment_id: uuid.UUID | None,
        v021_execution_spec_artifact_id: uuid.UUID,
        comparator_artifact_id: uuid.UUID,
        v021_capability: RuntimeCapability,
        v022_capability: RuntimeCapability,
    ) -> ShadowRuntimeBindingPublication:
        v021_capability.validated()
        v022_capability.validated()
        if v021_capability.runtime_contract != "v0.21":
            raise ValueError("The v0.21 Shadow leg requires a v0.21 capability")
        if v022_capability.runtime_contract != "v0.22":
            raise ValueError("The v0.22 Shadow leg requires a v0.22 capability")
        with self._engine.connect() as connection:
            identity = connection.execute(
                text(
                    """
                    SELECT representative.representative_role,
                           representative.execution_version_id,
                           representative.product_enrollment_id,
                           plan.artifact_id AS plan_artifact_id,
                           plan.plan_fingerprint,execution.execution_fingerprint,
                           plan_artifact.status AS plan_status,
                           v021_artifact.status AS v021_status,
                           comparator.status AS comparator_status
                      FROM workspace.v022_shadow_representative representative
                      JOIN workspace.v022_shadow_plan plan
                        ON plan.shadow_plan_id=representative.shadow_plan_id
                      JOIN lineage.artifact plan_artifact
                        ON plan_artifact.artifact_id=plan.artifact_id
                      JOIN product.v022_execution_version execution
                        ON execution.execution_version_id=representative.execution_version_id
                      JOIN lineage.artifact v021_artifact
                        ON v021_artifact.artifact_id=:v021_artifact
                      JOIN lineage.artifact comparator
                        ON comparator.artifact_id=:comparator
                     WHERE representative.shadow_representative_id=:representative
                    """
                ),
                {
                    "representative": shadow_representative_id,
                    "v021_artifact": v021_execution_spec_artifact_id,
                    "comparator": comparator_artifact_id,
                },
            ).mappings().one_or_none()
        if identity is None or any(
            identity[key] != "published"
            for key in ("plan_status", "v021_status", "comparator_status")
        ):
            raise ValueError("Shadow Runtime Binding requires published runtime identities")
        active_shadow = identity["representative_role"] == "active_product_shadow"
        if active_shadow != (v021_product_enrollment_id is not None):
            raise ValueError(
                "Only active-product representatives bind a formal v0.21 Enrollment"
            )
        semantic = {
            "contract_version": "v0.22.0",
            "shadow_representative_id": str(shadow_representative_id),
            "shadow_plan_fingerprint": identity["plan_fingerprint"],
            "v021_product_enrollment_id": (
                str(v021_product_enrollment_id) if v021_product_enrollment_id else None
            ),
            "v021_execution_spec_artifact_id": str(v021_execution_spec_artifact_id),
            "v022_product_enrollment_id": str(identity["product_enrollment_id"]),
            "v022_execution_version_id": str(identity["execution_version_id"]),
            "v022_execution_fingerprint": identity["execution_fingerprint"],
            "comparator_artifact_id": str(comparator_artifact_id),
            "runtime_capabilities": {
                "v0.21": _capability_document(v021_capability),
                "v0.22": _capability_document(v022_capability),
            },
        }
        fingerprint = sha256_hexdigest(semantic)
        existing = self._existing(shadow_representative_id)
        if existing is not None:
            if existing.binding_fingerprint != fingerprint:
                raise ValueError("Shadow representative already has a different Runtime Binding")
            return existing
        binding_id = uuid.uuid5(uuid.NAMESPACE_URL, f"bird:v0.22:shadow-binding:{fingerprint}")
        dependencies = [
            DependencyInput(identity["plan_artifact_id"], "shadow_plan", 0),
            DependencyInput(v021_execution_spec_artifact_id, "v021_execution_spec", 1),
            DependencyInput(comparator_artifact_id, "shadow_comparator", 2),
        ]
        publication = self._artifacts.publish(
            artifact_type="v022_shadow_runtime_binding",
            artifact_key=str(shadow_representative_id),
            version_number=1,
            semantic_payload=semantic,
            content_payload=semantic,
            dependencies=tuple(dependencies),
            reason="freeze exact v0.21/v0.22 Shadow runtime capabilities",
            draft_writer=partial(
                self._write,
                binding_id=binding_id,
                representative_id=shadow_representative_id,
                v021_enrollment_id=v021_product_enrollment_id,
                v021_spec_id=v021_execution_spec_artifact_id,
                comparator_id=comparator_artifact_id,
                v021_capability=v021_capability,
                v022_capability=v022_capability,
                fingerprint=fingerprint,
            ),
        )
        return ShadowRuntimeBindingPublication(
            binding_id, publication.artifact_id, fingerprint, publication.reused
        )

    def _existing(
        self, representative_id: uuid.UUID
    ) -> ShadowRuntimeBindingPublication | None:
        with self._engine.connect() as connection:
            row = connection.execute(
                text(
                    "SELECT binding.*,artifact.status "
                    "FROM workspace.v022_shadow_runtime_binding binding "
                    "JOIN lineage.artifact artifact "
                    "ON artifact.artifact_id=binding.artifact_id "
                    "WHERE binding.shadow_representative_id=:representative"
                ),
                {"representative": representative_id},
            ).mappings().one_or_none()
        if row is None:
            return None
        if row["status"] != "published":
            raise ValueError("Shadow Runtime Binding Artifact is not published")
        return ShadowRuntimeBindingPublication(
            row["shadow_runtime_binding_id"],
            row["artifact_id"],
            row["binding_fingerprint"],
            True,
        )

    @staticmethod
    def _write(
        connection: Connection,
        artifact_id: uuid.UUID,
        *,
        binding_id: uuid.UUID,
        representative_id: uuid.UUID,
        v021_enrollment_id: uuid.UUID | None,
        v021_spec_id: uuid.UUID,
        comparator_id: uuid.UUID,
        v021_capability: RuntimeCapability,
        v022_capability: RuntimeCapability,
        fingerprint: str,
    ) -> None:
        values: dict[str, object] = {
            "id": binding_id,
            "artifact": artifact_id,
            "representative": representative_id,
            "v021_enrollment": v021_enrollment_id,
            "v021_spec": v021_spec_id,
            "comparator": comparator_id,
            "fingerprint": fingerprint,
        }
        for prefix, capability in (("v021", v021_capability), ("v022", v022_capability)):
            values.update(
                {
                    f"{prefix}_compiler": capability.compiler_version,
                    f"{prefix}_executor": capability.executor_version,
                    f"{prefix}_environment": capability.environment_fingerprint,
                    f"{prefix}_capability": capability.capability_key,
                }
            )
        connection.execute(
            text(
                """
                INSERT INTO workspace.v022_shadow_runtime_binding (
                  shadow_runtime_binding_id,artifact_id,shadow_representative_id,
                  v021_product_enrollment_id,v021_execution_spec_artifact_id,
                  comparator_artifact_id,v021_compiler_version,v021_executor_version,
                  v021_environment_fingerprint,v021_capability_key,v022_compiler_version,
                  v022_executor_version,v022_environment_fingerprint,v022_capability_key,
                  binding_fingerprint
                ) VALUES (
                  :id,:artifact,:representative,:v021_enrollment,:v021_spec,:comparator,
                  :v021_compiler,:v021_executor,:v021_environment,:v021_capability,
                  :v022_compiler,:v022_executor,:v022_environment,:v022_capability,:fingerprint
                )
                """
            ),
            values,
        )


class ShadowDualRunScheduler:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def schedule_due(
        self, *, shadow_runtime_binding_id: uuid.UUID, scheduled_at: datetime | None = None
    ) -> ShadowScheduleResult:
        known_at = scheduled_at or datetime.now(UTC)
        if known_at.tzinfo is None:
            raise ValueError("Shadow scheduling timestamp must be timezone-aware")
        with self._engine.begin() as connection:
            state = connection.scalar(
                text(
                    "SELECT to_state FROM workspace.v022_release_transition "
                    "ORDER BY sequence_number DESC LIMIT 1"
                )
            )
            if state not in {"shadow", "explicit_eligible", "default"}:
                raise ValueError("v0.22 Shadow runtime is not enabled by Release Control")
            binding = connection.execute(
                text(
                    """
                    SELECT binding.*,representative.product_enrollment_id,
                           representative.representative_role,
                           enrollment.decision_schedule_version_id,
                           first_session.ordinal AS first_ordinal,
                           binding_artifact.status AS binding_status,
                           legacy_enrollment.lifecycle AS v021_enrollment_lifecycle
                      FROM workspace.v022_shadow_runtime_binding binding
                      JOIN lineage.artifact binding_artifact
                        ON binding_artifact.artifact_id=binding.artifact_id
                      JOIN workspace.v022_shadow_representative representative
                        ON representative.shadow_representative_id=binding.shadow_representative_id
                      JOIN product.v022_product_enrollment enrollment
                        ON enrollment.product_enrollment_id=representative.product_enrollment_id
                      JOIN product.v022_decision_schedule_session first_session
                        ON first_session.decision_session_id=
                           enrollment.first_eligible_decision_session_id
                      LEFT JOIN product.product_enrollment legacy_enrollment
                        ON legacy_enrollment.product_enrollment_id=
                           binding.v021_product_enrollment_id
                     WHERE binding.shadow_runtime_binding_id=:binding
                    """
                ),
                {"binding": shadow_runtime_binding_id},
            ).mappings().one_or_none()
            if binding is None or binding["binding_status"] != "published":
                raise ValueError("Shadow scheduling requires a published Runtime Binding")
            if (
                binding["representative_role"] == "active_product_shadow"
                and binding["v021_enrollment_lifecycle"] != "active"
            ):
                raise ValueError("Active v0.21 Enrollment is no longer eligible for dual-run")
            sessions = connection.execute(
                text(
                    """
                    SELECT decision_session_id,decision_cutoff_at
                      FROM product.v022_decision_schedule_session
                     WHERE decision_schedule_version_id=:schedule
                       AND ordinal>=:first_ordinal AND decision_cutoff_at<=:known_at
                     ORDER BY ordinal
                    """
                ),
                {
                    "schedule": binding["decision_schedule_version_id"],
                    "first_ordinal": binding["first_ordinal"],
                    "known_at": known_at,
                },
            ).mappings().all()
            created_intents = 0
            created_work = 0
            for session in sessions:
                created = self._schedule_session(connection, binding, session, known_at)
                created_intents += int(created)
                created_work += 2 * int(created)
        return ShadowScheduleResult(
            shadow_runtime_binding_id, len(sessions), created_intents, created_work
        )

    @staticmethod
    def _schedule_session(
        connection: Connection,
        binding: RowMapping,
        session: RowMapping,
        scheduled_at: datetime,
    ) -> bool:
        semantic = {
            "shadow_runtime_binding_id": str(binding["shadow_runtime_binding_id"]),
            "binding_fingerprint": binding["binding_fingerprint"],
            "shadow_representative_id": str(binding["shadow_representative_id"]),
            "decision_session_id": str(session["decision_session_id"]),
            "decision_cutoff_at": session["decision_cutoff_at"],
        }
        fingerprint = sha256_hexdigest(semantic)
        intent_id = uuid.uuid5(uuid.NAMESPACE_URL, f"bird:v0.22:shadow-intent:{fingerprint}")
        inserted = connection.execute(
            text(
                """
                INSERT INTO workspace.v022_shadow_dual_run_intent (
                  shadow_dual_run_intent_id,shadow_runtime_binding_id,shadow_representative_id,
                  decision_session_id,decision_cutoff_at,intent_fingerprint,scheduled_at
                ) VALUES (:id,:binding,:representative,:session,:cutoff,:fingerprint,:scheduled)
                ON CONFLICT (shadow_representative_id,decision_session_id) DO NOTHING
                RETURNING shadow_dual_run_intent_id
                """
            ),
            {
                "id": intent_id,
                "binding": binding["shadow_runtime_binding_id"],
                "representative": binding["shadow_representative_id"],
                "session": session["decision_session_id"],
                "cutoff": session["decision_cutoff_at"],
                "fingerprint": fingerprint,
                "scheduled": scheduled_at,
            },
        ).scalar_one_or_none()
        if inserted is None:
            return False
        for runtime, prefix in (("v0.21", "v021"), ("v0.22", "v022")):
            work_semantic = {
                "intent_fingerprint": fingerprint,
                "runtime_contract": runtime,
                "compiler_version": binding[f"{prefix}_compiler_version"],
                "executor_version": binding[f"{prefix}_executor_version"],
                "environment_fingerprint": binding[f"{prefix}_environment_fingerprint"],
                "capability_key": binding[f"{prefix}_capability_key"],
            }
            work_fingerprint = sha256_hexdigest(work_semantic)
            connection.execute(
                text(
                    """
                    INSERT INTO ops.v022_shadow_work_item (
                      shadow_work_item_id,shadow_dual_run_intent_id,runtime_contract,
                      compiler_version,executor_version,environment_fingerprint,
                      capability_key,work_fingerprint
                    ) VALUES (:id,:intent,:runtime,:compiler,:executor,:environment,
                              :capability,:fingerprint)
                    """
                ),
                {
                    "id": uuid.uuid5(
                        uuid.NAMESPACE_URL, f"bird:v0.22:shadow-work:{work_fingerprint}"
                    ),
                    "intent": intent_id,
                    "runtime": runtime,
                    "compiler": binding[f"{prefix}_compiler_version"],
                    "executor": binding[f"{prefix}_executor_version"],
                    "environment": binding[f"{prefix}_environment_fingerprint"],
                    "capability": binding[f"{prefix}_capability_key"],
                    "fingerprint": work_fingerprint,
                },
            )
        return True


class ShadowWorkerService:
    """Registers injected service-principal capabilities and claims only exact work."""

    def __init__(self, engine: Engine, *, service_principal: str) -> None:
        self._engine = engine
        self._service_principal = service_principal.strip()
        if not self._service_principal:
            raise ValueError("Worker service principal is required")

    def register(
        self,
        *,
        worker_id: str,
        capabilities: tuple[RuntimeCapability, ...],
        ttl_seconds: int = 300,
        registered_at: datetime | None = None,
    ) -> None:
        worker_id = worker_id.strip()
        known_at = registered_at or datetime.now(UTC)
        if not worker_id or known_at.tzinfo is None or ttl_seconds < 30:
            raise ValueError("Worker identity, aware timestamp, and TTL >= 30s are required")
        if not capabilities:
            raise ValueError("Worker must register at least one exact runtime capability")
        identities = [item.validated().identity() for item in capabilities]
        if len(identities) != len(set(identities)):
            raise ValueError("Worker capability registrations must be unique")
        expires_at = known_at + timedelta(seconds=ttl_seconds)
        with self._engine.begin() as connection:
            other_principal = connection.scalar(
                text(
                    "SELECT service_principal FROM ops.v022_worker_capability_lease "
                    "WHERE worker_id=:worker AND expires_at>:known_at "
                    "AND service_principal<>:principal LIMIT 1"
                ),
                {
                    "worker": worker_id,
                    "known_at": known_at,
                    "principal": self._service_principal,
                },
            )
            if other_principal is not None:
                raise ValueError("Worker id is leased by another service principal")
            for capability in capabilities:
                connection.execute(
                    text(
                        """
                        INSERT INTO ops.v022_worker_capability_lease (
                          worker_id,service_principal,runtime_contract,compiler_version,
                          executor_version,environment_fingerprint,capability_key,
                          registered_at,expires_at
                        ) VALUES (:worker,:principal,:runtime,:compiler,:executor,:environment,
                                  :capability,:registered,:expires)
                        ON CONFLICT (worker_id,runtime_contract,compiler_version,executor_version,
                                     environment_fingerprint,capability_key)
                        DO UPDATE SET service_principal=excluded.service_principal,
                                      registered_at=excluded.registered_at,
                                      expires_at=excluded.expires_at
                        """
                    ),
                    {
                        "worker": worker_id,
                        "principal": self._service_principal,
                        "runtime": capability.runtime_contract,
                        "compiler": capability.compiler_version,
                        "executor": capability.executor_version,
                        "environment": capability.environment_fingerprint,
                        "capability": capability.capability_key,
                        "registered": known_at,
                        "expires": expires_at,
                    },
                )

    def claim(
        self,
        *,
        worker_id: str,
        lease_seconds: int = 120,
        claimed_at: datetime | None = None,
        runtime_contract: RuntimeContract | None = None,
        require_v022_product_decision: bool = False,
        require_v021_monitoring_snapshot: bool = False,
    ) -> ClaimedShadowWork | None:
        worker_id = worker_id.strip()
        known_at = claimed_at or datetime.now(UTC)
        if not worker_id or known_at.tzinfo is None or lease_seconds < 10:
            raise ValueError("Worker identity, aware timestamp, and lease >= 10s are required")
        if runtime_contract is not None and runtime_contract not in {"v0.21", "v0.22"}:
            raise ValueError(f"Unsupported Shadow claim runtime: {runtime_contract}")
        if require_v022_product_decision and runtime_contract != "v0.22":
            raise ValueError("Ready Product Decision filtering requires the v0.22 runtime")
        if require_v021_monitoring_snapshot and runtime_contract != "v0.21":
            raise ValueError("Ready monitoring Snapshot filtering requires the v0.21 runtime")
        with self._engine.begin() as connection:
            release_state = connection.scalar(
                text(
                    "SELECT to_state FROM workspace.v022_release_transition "
                    "ORDER BY sequence_number DESC LIMIT 1"
                )
            )
            if release_state not in {"shadow", "explicit_eligible", "default"}:
                raise ValueError("Release Control does not allow Shadow work claims")
            row = connection.execute(
                text(
                    """
                    WITH candidate AS (
                      SELECT work.shadow_work_item_id
                        FROM ops.v022_shadow_work_item work
                        JOIN workspace.v022_shadow_dual_run_intent intent
                          ON intent.shadow_dual_run_intent_id=work.shadow_dual_run_intent_id
                        JOIN ops.v022_worker_capability_lease capability
                          ON capability.worker_id=:worker
                         AND capability.service_principal=:principal
                         AND capability.expires_at>:known_at
                         AND capability.runtime_contract=work.runtime_contract
                         AND capability.compiler_version=work.compiler_version
                         AND capability.executor_version=work.executor_version
                         AND capability.environment_fingerprint=work.environment_fingerprint
                         AND capability.capability_key=work.capability_key
                       WHERE (work.status='queued' OR
                              (work.status='running' AND work.lease_expires_at<=:known_at))
                         AND work.attempt_count<work.max_attempts
                         AND (CAST(:runtime_contract AS varchar) IS NULL OR
                              work.runtime_contract=:runtime_contract)
                         AND (NOT :require_v022_decision OR EXISTS (
                           SELECT 1
                             FROM product.v022_product_decision decision
                             JOIN lineage.artifact decision_artifact
                               ON decision_artifact.artifact_id=decision.artifact_id
                              AND decision_artifact.status='published'
                             JOIN workspace.v022_shadow_runtime_binding binding
                               ON binding.shadow_runtime_binding_id=
                                  intent.shadow_runtime_binding_id
                             JOIN workspace.v022_shadow_representative representative
                               ON representative.shadow_representative_id=
                                  binding.shadow_representative_id
                            WHERE decision.product_enrollment_id=
                                  representative.product_enrollment_id
                              AND decision.decision_session_id=intent.decision_session_id
                         ))
                         AND (NOT :require_v021_snapshot OR EXISTS (
                           SELECT 1
                             FROM workspace.v022_shadow_runtime_binding binding
                             JOIN workspace.v022_shadow_representative representative
                               ON representative.shadow_representative_id=
                                  binding.shadow_representative_id
                             JOIN product.v022_decision_schedule_session session
                               ON session.decision_session_id=intent.decision_session_id
                            WHERE binding.shadow_runtime_binding_id=
                                  intent.shadow_runtime_binding_id
                              AND (
                                (representative.representative_role='active_product_shadow'
                                 AND 1 = (
                                   SELECT count(*)
                                     FROM product.monitoring_snapshot snapshot
                                     JOIN lineage.artifact snapshot_artifact
                                       ON snapshot_artifact.artifact_id=snapshot.artifact_id
                                      AND snapshot_artifact.status='published'
                                    WHERE snapshot.product_enrollment_id=
                                          binding.v021_product_enrollment_id
                                      AND snapshot.as_of_session=session.session_date
                                      AND snapshot.known_at>=intent.decision_cutoff_at
                                      AND snapshot.known_at<=:known_at
                                 )) OR
                                (representative.representative_role='shadow_only'
                                 AND EXISTS (
                                   SELECT 1 FROM
                                     compatibility.v022_shadow_v021_execution_spec spec
                                     JOIN lineage.artifact spec_artifact
                                       ON spec_artifact.artifact_id=spec.artifact_id
                                      AND spec_artifact.status='published'
                                    WHERE spec.artifact_id=
                                          binding.v021_execution_spec_artifact_id
                                 ))
                              )
                         ))
                       ORDER BY intent.decision_cutoff_at,work.runtime_contract
                       FOR UPDATE OF work SKIP LOCKED LIMIT 1
                    )
                    UPDATE ops.v022_shadow_work_item work
                       SET status='running',attempt_count=attempt_count+1,
                           lease_owner=:worker,lease_service_principal=:principal,
                           lease_expires_at=:lease_expires,fencing_token=fencing_token+1,
                           updated_at=:known_at
                      FROM candidate
                     WHERE work.shadow_work_item_id=candidate.shadow_work_item_id
                    RETURNING work.*
                    """
                ),
                {
                    "worker": worker_id,
                    "principal": self._service_principal,
                    "known_at": known_at,
                    "lease_expires": known_at + timedelta(seconds=lease_seconds),
                    "runtime_contract": runtime_contract,
                    "require_v022_decision": require_v022_product_decision,
                    "require_v021_snapshot": require_v021_monitoring_snapshot,
                },
            ).mappings().one_or_none()
            if row is None:
                return None
            intent = connection.execute(
                text(
                    "SELECT decision_session_id,decision_cutoff_at "
                    "FROM workspace.v022_shadow_dual_run_intent "
                    "WHERE shadow_dual_run_intent_id=:intent"
                ),
                {"intent": row["shadow_dual_run_intent_id"]},
            ).mappings().one()
        return ClaimedShadowWork(
            row["shadow_work_item_id"],
            row["shadow_dual_run_intent_id"],
            row["runtime_contract"],
            intent["decision_session_id"],
            intent["decision_cutoff_at"],
            row["fencing_token"],
            row["attempt_count"],
        )

    def complete(
        self,
        claim: ClaimedShadowWork,
        *,
        worker_id: str,
        v021_reference_artifact_id: uuid.UUID | None = None,
        v022_product_decision_id: uuid.UUID | None = None,
        completed_at: datetime | None = None,
    ) -> None:
        known_at = completed_at or datetime.now(UTC)
        if known_at.tzinfo is None:
            raise ValueError("Shadow completion timestamp must be timezone-aware")
        if claim.runtime_contract == "v0.21":
            if v021_reference_artifact_id is None or v022_product_decision_id is not None:
                raise ValueError("v0.21 Shadow work requires only a reference Artifact")
        elif v022_product_decision_id is None or v021_reference_artifact_id is not None:
            raise ValueError("v0.22 Shadow work requires only a Product Decision")
        with self._engine.begin() as connection:
            updated = connection.execute(
                text(
                    """
                    UPDATE ops.v022_shadow_work_item
                       SET status='completed',lease_owner=NULL,lease_service_principal=NULL,
                           lease_expires_at=NULL,v021_reference_artifact_id=:v021_reference,
                           v022_product_decision_id=:v022_decision,updated_at=:known_at
                     WHERE shadow_work_item_id=:work AND status='running'
                       AND lease_owner=:worker AND lease_service_principal=:principal
                       AND fencing_token=:fence AND lease_expires_at>:known_at
                    """
                ),
                {
                    "work": claim.shadow_work_item_id,
                    "worker": worker_id.strip(),
                    "principal": self._service_principal,
                    "fence": claim.fencing_token,
                    "known_at": known_at,
                    "v021_reference": v021_reference_artifact_id,
                    "v022_decision": v022_product_decision_id,
                },
            ).rowcount
        if updated != 1:
            raise ValueError("Shadow work lease is stale or owned by another worker")

    def fail(
        self,
        claim: ClaimedShadowWork,
        *,
        worker_id: str,
        reason_code: str,
        failed_at: datetime | None = None,
    ) -> None:
        known_at = failed_at or datetime.now(UTC)
        reason_code = reason_code.strip()
        if known_at.tzinfo is None or not reason_code:
            raise ValueError("Shadow failure requires an aware timestamp and reason code")
        with self._engine.begin() as connection:
            updated = connection.execute(
                text(
                    """
                    UPDATE ops.v022_shadow_work_item
                       SET status='failed',lease_owner=NULL,lease_service_principal=NULL,
                           lease_expires_at=NULL,failure_document=CAST(:failure AS jsonb),
                           updated_at=:known_at
                     WHERE shadow_work_item_id=:work AND status='running'
                       AND lease_owner=:worker AND lease_service_principal=:principal
                       AND fencing_token=:fence
                    """
                ),
                {
                    "work": claim.shadow_work_item_id,
                    "worker": worker_id.strip(),
                    "principal": self._service_principal,
                    "fence": claim.fencing_token,
                    "known_at": known_at,
                    "failure": json.dumps({"reason_code": reason_code}, sort_keys=True),
                },
            ).rowcount
        if updated != 1:
            raise ValueError("Shadow work lease is stale or owned by another worker")


class ShadowV022DecisionWorker:
    """Attach exact published Product Decisions to their queued v0.22 Shadow leg."""

    def __init__(
        self,
        engine: Engine,
        *,
        service_principal: str,
        worker_id: str,
        capability: RuntimeCapability,
    ) -> None:
        if capability.validated().runtime_contract != "v0.22":
            raise ValueError("Shadow v0.22 Decision worker requires a v0.22 capability")
        self._engine = engine
        self._worker_id = worker_id.strip()
        if not self._worker_id:
            raise ValueError("Shadow v0.22 Decision worker id is required")
        self._capability = capability
        self._work = ShadowWorkerService(engine, service_principal=service_principal)

    def run_once(self, *, observed_at: datetime | None = None) -> ShadowV022DecisionOutcome:
        known_at = observed_at or datetime.now(UTC)
        if known_at.tzinfo is None:
            raise ValueError("Shadow v0.22 Decision worker timestamp must be timezone-aware")
        self._work.register(
            worker_id=self._worker_id,
            capabilities=(self._capability,),
            ttl_seconds=300,
            registered_at=known_at,
        )
        claim = self._work.claim(
            worker_id=self._worker_id,
            claimed_at=known_at,
            runtime_contract="v0.22",
            require_v022_product_decision=True,
        )
        if claim is None:
            return ShadowV022DecisionOutcome("idle")
        decision_id = self._decision_for_claim(claim)
        self._work.complete(
            claim,
            worker_id=self._worker_id,
            v022_product_decision_id=decision_id,
            completed_at=known_at,
        )
        return ShadowV022DecisionOutcome(
            "completed", claim.shadow_work_item_id, decision_id
        )

    def _decision_for_claim(self, claim: ClaimedShadowWork) -> uuid.UUID:
        with self._engine.connect() as connection:
            decision_id = connection.scalar(
                text(
                    """
                    SELECT decision.product_decision_id
                      FROM workspace.v022_shadow_dual_run_intent intent
                      JOIN workspace.v022_shadow_runtime_binding binding
                        ON binding.shadow_runtime_binding_id=intent.shadow_runtime_binding_id
                      JOIN workspace.v022_shadow_representative representative
                        ON representative.shadow_representative_id=
                           binding.shadow_representative_id
                      JOIN product.v022_product_decision decision
                        ON decision.product_enrollment_id=representative.product_enrollment_id
                       AND decision.decision_session_id=intent.decision_session_id
                      JOIN lineage.artifact artifact
                        ON artifact.artifact_id=decision.artifact_id
                       AND artifact.status='published'
                     WHERE intent.shadow_dual_run_intent_id=:intent
                    """
                ),
                {"intent": claim.shadow_dual_run_intent_id},
            )
        if not isinstance(decision_id, uuid.UUID):
            raise ValueError("Claimed v0.22 Shadow work lost its exact Product Decision")
        return decision_id


class ShadowV021ReferenceWorker:
    """Project exact formal v0.21 monitoring decisions into the Shadow contract."""

    def __init__(
        self,
        engine: Engine,
        *,
        service_principal: str,
        worker_id: str,
        capability: RuntimeCapability,
    ) -> None:
        if capability.validated().runtime_contract != "v0.21":
            raise ValueError("Shadow v0.21 Reference worker requires a v0.21 capability")
        self._engine = engine
        self._worker_id = worker_id.strip()
        if not self._worker_id:
            raise ValueError("Shadow v0.21 Reference worker id is required")
        self._capability = capability
        self._work = ShadowWorkerService(engine, service_principal=service_principal)
        self._references = V021ShadowReferenceService(engine)
        self._replay = V021ShadowReplayService(engine)

    def run_once(self, *, observed_at: datetime | None = None) -> ShadowV021ReferenceOutcome:
        known_at = observed_at or datetime.now(UTC)
        if known_at.tzinfo is None:
            raise ValueError("Shadow v0.21 Reference worker timestamp must be timezone-aware")
        self._work.register(
            worker_id=self._worker_id,
            capabilities=(self._capability,),
            ttl_seconds=300,
            registered_at=known_at,
        )
        claim = self._work.claim(
            worker_id=self._worker_id,
            claimed_at=known_at,
            runtime_contract="v0.21",
            require_v021_monitoring_snapshot=True,
        )
        if claim is None:
            return ShadowV021ReferenceOutcome("idle")
        identity = self._identity_for_claim(claim)
        if identity["representative_role"] == "shadow_only":
            replay = self._replay.replay(
                execution_spec_artifact_id=identity["v021_execution_spec_artifact_id"],
                decision_session=identity["session_date"],
                decision_cutoff_at=claim.decision_cutoff_at,
            )
            decision_document = replay.decision_document
            source_artifact_id = replay.source_artifact_id
            source_known_at = replay.known_at
            source_dependency_role = "v021_data_bundle"
        else:
            source = self._source_for_claim(claim, observed_at=known_at)
            decision_document = _v021_reference_document(source)
            source_artifact_id = source["artifact_id"]
            source_known_at = source["known_at"]
            source_dependency_role = "v021_monitoring_snapshot"
        reference = self._references.publish(
            shadow_runtime_binding_id=identity["shadow_runtime_binding_id"],
            decision_session_id=claim.decision_session_id,
            decision_document=decision_document,
            source_artifact_id=source_artifact_id,
            source_dependency_role=source_dependency_role,
            known_at=source_known_at,
        )
        self._work.complete(
            claim,
            worker_id=self._worker_id,
            v021_reference_artifact_id=reference.artifact_id,
            completed_at=known_at,
        )
        return ShadowV021ReferenceOutcome(
            "completed", claim.shadow_work_item_id, reference.artifact_id
        )

    def _identity_for_claim(self, claim: ClaimedShadowWork) -> dict[str, Any]:
        with self._engine.connect() as connection:
            row = connection.execute(
                text(
                    "SELECT binding.shadow_runtime_binding_id,"
                    "binding.v021_execution_spec_artifact_id,"
                    "representative.representative_role,session.session_date "
                    "FROM workspace.v022_shadow_dual_run_intent intent "
                    "JOIN workspace.v022_shadow_runtime_binding binding ON "
                    "binding.shadow_runtime_binding_id=intent.shadow_runtime_binding_id "
                    "JOIN workspace.v022_shadow_representative representative ON "
                    "representative.shadow_representative_id=binding.shadow_representative_id "
                    "JOIN product.v022_decision_schedule_session session ON "
                    "session.decision_session_id=intent.decision_session_id "
                    "WHERE intent.shadow_dual_run_intent_id=:intent"
                ),
                {"intent": claim.shadow_dual_run_intent_id},
            ).mappings().one_or_none()
        if row is None:
            raise ValueError("Claimed v0.21 Shadow work lost its Runtime Binding")
        return dict(row)

    def _source_for_claim(
        self, claim: ClaimedShadowWork, *, observed_at: datetime
    ) -> dict[str, Any]:
        with self._engine.connect() as connection:
            rows = connection.execute(
                text(
                    """
                    SELECT binding.shadow_runtime_binding_id,snapshot.artifact_id,
                           snapshot.data_bundle_artifact_id,snapshot.as_of_session,
                           snapshot.known_at,snapshot.health,snapshot.metrics,
                           snapshot.health_components,
                           next_session.session_date AS recommended_execution_date
                      FROM workspace.v022_shadow_dual_run_intent intent
                      JOIN workspace.v022_shadow_runtime_binding binding
                        ON binding.shadow_runtime_binding_id=intent.shadow_runtime_binding_id
                      JOIN workspace.v022_shadow_representative representative
                        ON representative.shadow_representative_id=
                           binding.shadow_representative_id
                       AND representative.representative_role='active_product_shadow'
                      JOIN product.v022_decision_schedule_session session
                        ON session.decision_session_id=intent.decision_session_id
                      JOIN product.monitoring_snapshot snapshot
                       ON snapshot.product_enrollment_id=binding.v021_product_enrollment_id
                       AND snapshot.as_of_session=session.session_date
                       AND snapshot.known_at>=intent.decision_cutoff_at
                       AND snapshot.known_at<=:observed_at
                      JOIN lineage.artifact snapshot_artifact
                        ON snapshot_artifact.artifact_id=snapshot.artifact_id
                       AND snapshot_artifact.status='published'
                      JOIN data.data_bundle_version bundle
                        ON bundle.artifact_id=snapshot.data_bundle_artifact_id
                      JOIN data.data_bundle_member calendar
                        ON calendar.data_bundle_version_id=bundle.data_bundle_version_id
                       AND calendar.role='trading_calendar'
                      LEFT JOIN LATERAL (
                        SELECT calendar_session.session_date
                          FROM catalog.calendar_session calendar_session
                         WHERE calendar_session.calendar_version_id=
                               calendar.calendar_version_id
                           AND calendar_session.session_date>snapshot.as_of_session
                         ORDER BY calendar_session.session_date LIMIT 1
                      ) next_session ON true
                     WHERE intent.shadow_dual_run_intent_id=:intent
                    """
                ),
                {
                    "intent": claim.shadow_dual_run_intent_id,
                    "observed_at": observed_at,
                },
            ).mappings().all()
        if len(rows) != 1:
            raise ValueError("v0.21 Shadow work requires one exact monitoring Snapshot")
        return dict(rows[0])


def _v021_reference_document(source: dict[str, Any]) -> dict[str, object]:
    metrics = dict(source["metrics"])
    components = dict(source["health_components"])
    session = source["as_of_session"]
    completed = metrics.get("pending_decision_date") == session.isoformat()
    execution = source["recommended_execution_date"] if completed else None
    return {
        "decision_status": "completed" if completed else "missing",
        "decision_session": session.isoformat(),
        "recommended_execution_date": execution.isoformat() if execution else None,
        "positions": list(metrics.get("pending_target_holdings") or []),
        "reason_codes": list(components.get("reason_codes") or []),
        "source_monitoring_snapshot_artifact_id": str(source["artifact_id"]),
        "source_data_bundle_artifact_id": str(source["data_bundle_artifact_id"]),
        "source_known_at": source["known_at"].isoformat(),
    }


def worker_supports(
    required: RuntimeCapability, advertised: tuple[RuntimeCapability, ...]
) -> bool:
    """Pure exact-match policy used by tests and non-database worker adapters."""

    required.validated()
    return required.identity() in {item.validated().identity() for item in advertised}


def _capability_document(capability: RuntimeCapability) -> dict[str, str]:
    return {
        "runtime_contract": capability.runtime_contract,
        "compiler_version": capability.compiler_version,
        "executor_version": capability.executor_version,
        "environment_fingerprint": capability.environment_fingerprint,
        "capability_key": capability.capability_key,
    }
