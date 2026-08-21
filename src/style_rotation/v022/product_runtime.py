# ruff: noqa: E501
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import date, datetime
from functools import partial
from typing import Any, Literal, cast

from sqlalchemy import Engine, text
from sqlalchemy.engine import Connection, RowMapping

from style_rotation.core.canonical import sha256_hexdigest
from style_rotation.lineage.service import ArtifactService, DependencyInput

Frequency = Literal["weekly", "monthly"]
EvidenceClass = Literal["qualification_bridge", "historical_backfill", "prospective_oos"]
DecisionStatus = Literal["completed", "missing"]


@dataclass(frozen=True, slots=True)
class DecisionSessionInput:
    session_date: date
    decision_cutoff_at: datetime


@dataclass(frozen=True, slots=True)
class SchedulePublication:
    decision_schedule_version_id: uuid.UUID
    artifact_id: uuid.UUID
    schedule_fingerprint: str
    session_ids: tuple[uuid.UUID, ...]
    reused: bool


@dataclass(frozen=True, slots=True)
class EnrollmentPublication:
    product_enrollment_id: uuid.UUID
    artifact_id: uuid.UUID
    enrollment_fingerprint: str
    first_eligible_decision_session_id: uuid.UUID
    reused: bool


@dataclass(frozen=True, slots=True)
class RuntimeArtifactSet:
    input_manifest_artifact_id: uuid.UUID | None = None
    aggregation_run_artifact_id: uuid.UUID | None = None
    strategy_target_artifact_id: uuid.UUID | None = None
    defense_decision_artifact_id: uuid.UUID | None = None
    merged_target_artifact_id: uuid.UUID | None = None
    active_model_state_artifact_id: uuid.UUID | None = None


@dataclass(frozen=True, slots=True)
class ProductRuntimeBindingIdentity:
    product_input_snapshot_id: uuid.UUID
    product_runtime_execution_id: uuid.UUID
    aggregation_stage_id: uuid.UUID
    strategy_stage_id: uuid.UUID
    defense_stage_id: uuid.UUID | None
    merge_stage_id: uuid.UUID


@dataclass(frozen=True, slots=True)
class DecisionPublication:
    product_decision_id: uuid.UUID
    artifact_id: uuid.UUID
    decision_fingerprint: str
    decision_status: DecisionStatus
    oos_eligible: bool
    reused: bool


class DecisionScheduleService:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def publish(
        self,
        *,
        schedule_key: str,
        version_number: int,
        frequency: Frequency,
        sessions: tuple[DecisionSessionInput, ...],
    ) -> SchedulePublication:
        schedule_key = schedule_key.strip()
        if not schedule_key or version_number < 1:
            raise ValueError("Schedule key must be nonblank and version positive")
        if frequency not in {"weekly", "monthly"}:
            raise ValueError(f"Unsupported Decision Schedule frequency: {frequency}")
        if not sessions:
            raise ValueError("Decision Schedule requires at least one session")
        if any(item.decision_cutoff_at.tzinfo is None for item in sessions):
            raise ValueError("Decision cutoffs must be timezone-aware")
        canonical = tuple(
            sorted(sessions, key=lambda item: (item.session_date, item.decision_cutoff_at))
        )
        if canonical != sessions or len({item.session_date for item in sessions}) != len(sessions):
            raise ValueError("Decision Schedule sessions must be unique and canonically ordered")
        if any(
            current.decision_cutoff_at >= following.decision_cutoff_at
            for current, following in zip(sessions, sessions[1:], strict=False)
        ):
            raise ValueError("Decision Schedule cutoffs must be strictly increasing")
        semantic = {
            "contract_version": "v0.22.0",
            "schedule_key": schedule_key,
            "version_number": version_number,
            "frequency": frequency,
            "sessions": [
                {
                    "ordinal": ordinal,
                    "session_date": item.session_date.isoformat(),
                    "decision_cutoff_at": item.decision_cutoff_at.isoformat(),
                }
                for ordinal, item in enumerate(sessions, 1)
            ],
        }
        fingerprint = sha256_hexdigest(semantic)
        existing = self._existing(schedule_key, version_number)
        if existing is not None:
            if existing.schedule_fingerprint != fingerprint:
                raise ValueError("Decision Schedule version is already bound to different sessions")
            return existing
        schedule_id = uuid.uuid5(uuid.NAMESPACE_URL, f"bird:v0.22:schedule:{fingerprint}")
        session_ids = tuple(
            uuid.uuid5(uuid.NAMESPACE_URL, f"bird:v0.22:decision-session:{fingerprint}:{ordinal}")
            for ordinal in range(1, len(sessions) + 1)
        )
        publication = ArtifactService(self._engine).publish(
            artifact_type="v022_decision_schedule_version",
            artifact_key=fingerprint,
            version_number=1,
            semantic_payload=semantic,
            content_payload=semantic,
            reason="publish immutable v0.22 Decision Schedule",
            draft_writer=partial(
                self._write,
                schedule_id=schedule_id,
                schedule_key=schedule_key,
                version_number=version_number,
                frequency=frequency,
                fingerprint=fingerprint,
                sessions=sessions,
                session_ids=session_ids,
            ),
        )
        return SchedulePublication(
            schedule_id, publication.artifact_id, fingerprint, session_ids, publication.reused
        )

    def _existing(self, schedule_key: str, version_number: int) -> SchedulePublication | None:
        with self._engine.connect() as connection:
            row = (
                connection.execute(
                    text(
                        "SELECT schedule.*,artifact.status FROM product.v022_decision_schedule_version schedule "
                        "JOIN lineage.artifact artifact ON artifact.artifact_id=schedule.artifact_id "
                        "WHERE schedule.schedule_key=:key AND schedule.version_number=:version"
                    ),
                    {"key": schedule_key, "version": version_number},
                )
                .mappings()
                .one_or_none()
            )
            if row is None:
                return None
            session_ids = tuple(
                connection.scalars(
                    text(
                        "SELECT decision_session_id FROM product.v022_decision_schedule_session "
                        "WHERE decision_schedule_version_id=:schedule ORDER BY ordinal"
                    ),
                    {"schedule": row["decision_schedule_version_id"]},
                )
            )
        if row["status"] != "published" or len(session_ids) != row["session_count"]:
            raise ValueError("Decision Schedule publication is incomplete")
        return SchedulePublication(
            row["decision_schedule_version_id"],
            row["artifact_id"],
            row["schedule_fingerprint"],
            session_ids,
            True,
        )

    @staticmethod
    def _write(
        connection: Connection,
        artifact_id: uuid.UUID,
        *,
        schedule_id: uuid.UUID,
        schedule_key: str,
        version_number: int,
        frequency: Frequency,
        fingerprint: str,
        sessions: tuple[DecisionSessionInput, ...],
        session_ids: tuple[uuid.UUID, ...],
    ) -> None:
        connection.execute(
            text(
                "INSERT INTO product.v022_decision_schedule_version "
                "(decision_schedule_version_id,artifact_id,schedule_key,version_number,frequency,"
                "schedule_fingerprint,session_count) VALUES "
                "(:id,:artifact,:key,:version,:frequency,:fingerprint,:count)"
            ),
            {
                "id": schedule_id,
                "artifact": artifact_id,
                "key": schedule_key,
                "version": version_number,
                "frequency": frequency,
                "fingerprint": fingerprint,
                "count": len(sessions),
            },
        )
        for ordinal, (session, session_id) in enumerate(zip(sessions, session_ids, strict=True), 1):
            connection.execute(
                text(
                    "INSERT INTO product.v022_decision_schedule_session "
                    "(decision_session_id,decision_schedule_version_id,ordinal,session_date,decision_cutoff_at) "
                    "VALUES (:id,:schedule,:ordinal,:session_date,:cutoff)"
                ),
                {
                    "id": session_id,
                    "schedule": schedule_id,
                    "ordinal": ordinal,
                    "session_date": session.session_date,
                    "cutoff": session.decision_cutoff_at,
                },
            )


class ProductEnrollmentService:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def publish(
        self,
        *,
        execution_version_id: uuid.UUID,
        qualification_version_id: uuid.UUID,
        monitoring_policy_version_id: uuid.UUID,
        decision_schedule_version_id: uuid.UUID,
        oos_anchor_cutoff_at: datetime,
        activation_effective_at: datetime,
    ) -> EnrollmentPublication:
        if oos_anchor_cutoff_at.tzinfo is None or activation_effective_at.tzinfo is None:
            raise ValueError("Enrollment anchor timestamps must be timezone-aware")
        with self._engine.connect() as connection:
            execution = _business(
                connection,
                "product.v022_execution_version",
                "execution_version_id",
                execution_version_id,
            )
            qualification = _business(
                connection,
                "product.v022_qualification_version",
                "qualification_version_id",
                qualification_version_id,
            )
            monitoring = _business(
                connection,
                "product.v022_monitoring_policy_version",
                "monitoring_policy_version_id",
                monitoring_policy_version_id,
            )
            schedule = _business(
                connection,
                "product.v022_decision_schedule_version",
                "decision_schedule_version_id",
                decision_schedule_version_id,
            )
            first = (
                connection.execute(
                    text(
                        "SELECT * FROM product.v022_decision_schedule_session "
                        "WHERE decision_schedule_version_id=:schedule AND decision_cutoff_at>:threshold "
                        "ORDER BY ordinal LIMIT 1"
                    ),
                    {
                        "schedule": decision_schedule_version_id,
                        "threshold": max(oos_anchor_cutoff_at, activation_effective_at),
                    },
                )
                .mappings()
                .one_or_none()
            )
        if first is None:
            raise ValueError("Decision Schedule has no session after the Enrollment OOS anchor")
        if qualification["execution_version_id"] != execution_version_id:
            raise ValueError("Enrollment Qualification must bind the exact Execution Version")
        if monitoring["product_definition_id"] != execution["product_definition_id"]:
            raise ValueError("Enrollment Monitoring Policy belongs to another Product")
        semantic = {
            "contract_version": "v0.22.0",
            "execution_fingerprint": execution["execution_fingerprint"],
            "qualification_fingerprint": qualification["qualification_fingerprint"],
            "monitoring_policy_fingerprint": monitoring["monitoring_policy_fingerprint"],
            "schedule_fingerprint": schedule["schedule_fingerprint"],
            "oos_anchor_cutoff_at": oos_anchor_cutoff_at.isoformat(),
            "activation_effective_at": activation_effective_at.isoformat(),
            "first_eligible_decision_session_id": str(first["decision_session_id"]),
        }
        fingerprint = sha256_hexdigest(semantic)
        existing = self._existing(execution_version_id)
        if existing is not None:
            if existing.enrollment_fingerprint != fingerprint:
                raise ValueError("Execution Version is already bound to a different Enrollment")
            return existing
        enrollment_id = uuid.uuid5(uuid.NAMESPACE_URL, f"bird:v0.22:enrollment:{fingerprint}")
        publication = ArtifactService(self._engine).publish(
            artifact_type="v022_product_enrollment",
            artifact_key=fingerprint,
            version_number=1,
            semantic_payload=semantic,
            content_payload=semantic,
            dependencies=(
                DependencyInput(execution["artifact_id"], "execution_version", 0),
                DependencyInput(qualification["artifact_id"], "qualification_version", 1),
                DependencyInput(monitoring["artifact_id"], "monitoring_policy_version", 2),
                DependencyInput(schedule["artifact_id"], "decision_schedule_version", 3),
            ),
            reason="publish immutable v0.22 Product Enrollment",
            draft_writer=partial(
                self._write,
                enrollment_id=enrollment_id,
                execution_version_id=execution_version_id,
                qualification_version_id=qualification_version_id,
                monitoring_policy_version_id=monitoring_policy_version_id,
                schedule_version_id=decision_schedule_version_id,
                oos_anchor=oos_anchor_cutoff_at,
                activation=activation_effective_at,
                first_session_id=first["decision_session_id"],
                fingerprint=fingerprint,
            ),
        )
        return EnrollmentPublication(
            enrollment_id,
            publication.artifact_id,
            fingerprint,
            first["decision_session_id"],
            publication.reused,
        )

    def _existing(self, execution_version_id: uuid.UUID) -> EnrollmentPublication | None:
        with self._engine.connect() as connection:
            row = (
                connection.execute(
                    text(
                        "SELECT enrollment.*,artifact.status FROM product.v022_product_enrollment enrollment "
                        "JOIN lineage.artifact artifact ON artifact.artifact_id=enrollment.artifact_id "
                        "WHERE enrollment.execution_version_id=:execution"
                    ),
                    {"execution": execution_version_id},
                )
                .mappings()
                .one_or_none()
            )
        if row is None:
            return None
        if row["status"] != "published":
            raise ValueError("Product Enrollment Artifact is not published")
        return EnrollmentPublication(
            row["product_enrollment_id"],
            row["artifact_id"],
            row["enrollment_fingerprint"],
            row["first_eligible_decision_session_id"],
            True,
        )

    @staticmethod
    def _write(
        connection: Connection,
        artifact_id: uuid.UUID,
        *,
        enrollment_id: uuid.UUID,
        execution_version_id: uuid.UUID,
        qualification_version_id: uuid.UUID,
        monitoring_policy_version_id: uuid.UUID,
        schedule_version_id: uuid.UUID,
        oos_anchor: datetime,
        activation: datetime,
        first_session_id: uuid.UUID,
        fingerprint: str,
    ) -> None:
        connection.execute(
            text(
                "INSERT INTO product.v022_product_enrollment "
                "(product_enrollment_id,artifact_id,execution_version_id,qualification_version_id,"
                "monitoring_policy_version_id,decision_schedule_version_id,oos_anchor_cutoff_at,"
                "activation_effective_at,first_eligible_decision_session_id,enrollment_fingerprint) "
                "VALUES (:id,:artifact,:execution,:qualification,:monitoring,:schedule,:anchor,"
                ":activation,:first,:fingerprint)"
            ),
            {
                "id": enrollment_id,
                "artifact": artifact_id,
                "execution": execution_version_id,
                "qualification": qualification_version_id,
                "monitoring": monitoring_policy_version_id,
                "schedule": schedule_version_id,
                "anchor": oos_anchor,
                "activation": activation,
                "first": first_session_id,
                "fingerprint": fingerprint,
            },
        )


class ProductDecisionService:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def publish(
        self,
        *,
        product_enrollment_id: uuid.UUID,
        decision_session_id: uuid.UUID,
        evidence_class: EvidenceClass,
        decision_status: DecisionStatus,
        decision_document: dict[str, Any],
        quality_document: dict[str, Any],
        runtime_artifacts: RuntimeArtifactSet | None = None,
        product_runtime_binding: ProductRuntimeBindingIdentity | None = None,
        reason_codes: tuple[str, ...] = (),
    ) -> DecisionPublication:
        if evidence_class not in {"qualification_bridge", "historical_backfill", "prospective_oos"}:
            raise ValueError(f"Unsupported Product Decision Evidence Class: {evidence_class}")
        if decision_status not in {"completed", "missing"}:
            raise ValueError(f"Unsupported Product Decision status: {decision_status}")
        if not decision_document or not quality_document:
            raise ValueError("Product Decision and quality documents must be nonempty")
        if any(not code.strip() for code in reason_codes) or len(reason_codes) != len(
            set(reason_codes)
        ):
            raise ValueError("Product Decision reason codes must be nonblank and unique")
        if decision_status == "completed" and (
            runtime_artifacts is None
            or reason_codes
            or any(item is None for item in _required_runtime_ids(runtime_artifacts))
        ):
            raise ValueError("Completed Product Decision requires runtime Artifacts and no reasons")
        if decision_status == "missing" and (
            runtime_artifacts is not None
            or product_runtime_binding is not None
            or not reason_codes
        ):
            raise ValueError("Missing Product Decision requires reasons and no runtime Artifacts")
        with self._engine.connect() as connection:
            enrollment = _business(
                connection,
                "product.v022_product_enrollment",
                "product_enrollment_id",
                product_enrollment_id,
            )
            session = _session(connection, decision_session_id)
            first = _session(connection, enrollment["first_eligible_decision_session_id"])
            execution = _business(
                connection,
                "product.v022_execution_version",
                "execution_version_id",
                enrollment["execution_version_id"],
            )
            runtime_rows = (
                _runtime_rows(connection, runtime_artifacts)
                if runtime_artifacts is not None
                else ()
            )
            binding_rows = (
                _product_runtime_binding_rows(
                    connection,
                    product_runtime_binding,
                    runtime_artifacts,
                    product_enrollment_id=product_enrollment_id,
                    execution_version_id=execution["execution_version_id"],
                    configuration_snapshot_id=execution["configuration_snapshot_id"],
                    decision_session_id=decision_session_id,
                )
                if product_runtime_binding is not None and runtime_artifacts is not None
                else None
            )
            configuration = connection.execute(
                text(
                    "SELECT semantic_identity_document "
                    "FROM experiment.v022_research_configuration_snapshot "
                    "WHERE configuration_snapshot_id=:configuration"
                ),
                {"configuration": execution["configuration_snapshot_id"]},
            ).mappings().one()
        if session["decision_schedule_version_id"] != enrollment["decision_schedule_version_id"]:
            raise ValueError("Product Decision Session belongs to another Enrollment Schedule")
        if (
            configuration["semantic_identity_document"]["aggregation"]["execution_mode"]
            == "deterministic"
            and runtime_artifacts is not None
            and runtime_artifacts.active_model_state_artifact_id is not None
        ):
            raise ValueError("Deterministic Product Decision must have NULL active Model State")
        defense_required = configuration["semantic_identity_document"].get("defense") is not None
        if (
            decision_status == "completed"
            and runtime_artifacts is not None
            and defense_required
            != (runtime_artifacts.defense_decision_artifact_id is not None)
        ):
            raise ValueError(
                "Product Decision Defense Artifact must match its Configuration"
            )
        uses_product_runtime = any(
            role == "aggregation_run"
            and row["artifact_type"] == "v022_product_aggregation_output"
            for role, row in runtime_rows
        )
        if decision_status == "completed" and (
            uses_product_runtime != (binding_rows is not None)
        ):
            raise ValueError(
                "New Product Runtime Decision requires its exact Product Runtime binding"
            )
        oos_eligible = (
            evidence_class == "prospective_oos" and session["ordinal"] >= first["ordinal"]
        )
        semantic = {
            "contract_version": "v0.22.0",
            "enrollment_fingerprint": enrollment["enrollment_fingerprint"],
            "execution_fingerprint": execution["execution_fingerprint"],
            "decision_session": {
                "id": str(decision_session_id),
                "ordinal": session["ordinal"],
                "session_date": session["session_date"].isoformat(),
                "decision_cutoff_at": session["decision_cutoff_at"].isoformat(),
            },
            "decision_status": decision_status,
            "evidence_class": evidence_class,
            "oos_eligible": oos_eligible,
            "runtime_artifact_fingerprints": [
                {"role": role, "fingerprint": row["semantic_fingerprint"]}
                for role, row in runtime_rows
            ],
            "product_runtime": (
                None
                if binding_rows is None
                else {
                    "product_input_snapshot_id": str(
                        binding_rows["product_input_snapshot_id"]
                    ),
                    "product_input_snapshot_fingerprint": binding_rows[
                        "snapshot_fingerprint"
                    ],
                    "product_runtime_execution_id": str(
                        binding_rows["product_runtime_execution_id"]
                    ),
                    "product_runtime_execution_fingerprint": binding_rows[
                        "execution_fingerprint"
                    ],
                    "aggregation_stage_id": str(binding_rows["aggregation_stage_id"]),
                    "strategy_stage_id": str(binding_rows["strategy_stage_id"]),
                    "defense_stage_id": _uuid(binding_rows["defense_stage_id"]),
                    "merge_stage_id": str(binding_rows["merge_stage_id"]),
                }
            ),
            "decision": decision_document,
            "quality": quality_document,
            "reason_codes": list(reason_codes),
        }
        fingerprint = sha256_hexdigest(semantic)
        existing = self._existing(execution["execution_version_id"], decision_session_id)
        if existing is not None:
            if existing.decision_fingerprint != fingerprint:
                raise ValueError(
                    "Execution Decision Session is already bound to different evidence"
                )
            return existing
        decision_id = uuid.uuid5(uuid.NAMESPACE_URL, f"bird:v0.22:decision:{fingerprint}")
        dependencies = [DependencyInput(enrollment["artifact_id"], "enrollment", 0)]
        runtime_dependency_offset = 1
        if binding_rows is not None:
            dependencies.extend(
                (
                    DependencyInput(
                        binding_rows["product_input_snapshot_artifact_id"],
                        "product_input_snapshot",
                        1,
                    ),
                    DependencyInput(
                        binding_rows["product_runtime_execution_artifact_id"],
                        "product_runtime_execution",
                        2,
                    ),
                )
            )
            runtime_dependency_offset = 3
        dependencies.extend(
            DependencyInput(
                row["artifact_id"], role, ordinal + runtime_dependency_offset
            )
            for ordinal, (role, row) in enumerate(runtime_rows)
        )
        publication = ArtifactService(self._engine).publish(
            artifact_type="v022_product_decision",
            artifact_key=fingerprint,
            version_number=1,
            semantic_payload=semantic,
            content_payload=semantic,
            dependencies=tuple(dependencies),
            reason="publish immutable v0.22 Product Decision",
            draft_writer=partial(
                self._write,
                decision_id=decision_id,
                enrollment_id=product_enrollment_id,
                execution_id=execution["execution_version_id"],
                session_id=decision_session_id,
                decision_status=decision_status,
                evidence_class=evidence_class,
                oos_eligible=oos_eligible,
                runtime_artifacts=runtime_artifacts,
                product_runtime_binding=product_runtime_binding,
                decision_document=decision_document,
                quality_document=quality_document,
                reason_codes=reason_codes,
                fingerprint=fingerprint,
            ),
        )
        return DecisionPublication(
            decision_id,
            publication.artifact_id,
            fingerprint,
            decision_status,
            oos_eligible,
            publication.reused,
        )

    def _existing(
        self, execution_id: uuid.UUID, session_id: uuid.UUID
    ) -> DecisionPublication | None:
        with self._engine.connect() as connection:
            row = (
                connection.execute(
                    text(
                        "SELECT decision.*,artifact.status FROM product.v022_product_decision decision "
                        "JOIN lineage.artifact artifact ON artifact.artifact_id=decision.artifact_id "
                        "WHERE decision.execution_version_id=:execution AND decision.decision_session_id=:session"
                    ),
                    {"execution": execution_id, "session": session_id},
                )
                .mappings()
                .one_or_none()
            )
        if row is None:
            return None
        if row["status"] != "published":
            raise ValueError("Product Decision Artifact is not published")
        return DecisionPublication(
            row["product_decision_id"],
            row["artifact_id"],
            row["decision_fingerprint"],
            cast(DecisionStatus, row["decision_status"]),
            row["oos_eligible"],
            True,
        )

    @staticmethod
    def _write(
        connection: Connection,
        artifact_id: uuid.UUID,
        *,
        decision_id: uuid.UUID,
        enrollment_id: uuid.UUID,
        execution_id: uuid.UUID,
        session_id: uuid.UUID,
        decision_status: DecisionStatus,
        evidence_class: EvidenceClass,
        oos_eligible: bool,
        runtime_artifacts: RuntimeArtifactSet | None,
        product_runtime_binding: ProductRuntimeBindingIdentity | None,
        decision_document: dict[str, Any],
        quality_document: dict[str, Any],
        reason_codes: tuple[str, ...],
        fingerprint: str,
    ) -> None:
        values = runtime_artifacts or RuntimeArtifactSet()
        connection.execute(
            text(
                "INSERT INTO product.v022_product_decision "
                "(product_decision_id,artifact_id,product_enrollment_id,execution_version_id,"
                "decision_session_id,decision_status,evidence_class,oos_eligible,"
                "input_manifest_artifact_id,active_model_state_artifact_id,aggregation_run_artifact_id,"
                "strategy_target_artifact_id,defense_decision_artifact_id,merged_target_artifact_id,"
                "decision_document,quality_document,reason_codes,decision_fingerprint) VALUES "
                "(:id,:artifact,:enrollment,:execution,:session,:decision_status,:evidence_class,"
                ":oos_eligible,:input,:model,:aggregation,:strategy,:defense,:merged,"
                "CAST(:decision AS jsonb),CAST(:quality AS jsonb),CAST(:reasons AS jsonb),:fingerprint)"
            ),
            {
                "id": decision_id,
                "artifact": artifact_id,
                "enrollment": enrollment_id,
                "execution": execution_id,
                "session": session_id,
                "decision_status": decision_status,
                "evidence_class": evidence_class,
                "oos_eligible": oos_eligible,
                "input": values.input_manifest_artifact_id,
                "model": values.active_model_state_artifact_id,
                "aggregation": values.aggregation_run_artifact_id,
                "strategy": values.strategy_target_artifact_id,
                "defense": values.defense_decision_artifact_id,
                "merged": values.merged_target_artifact_id,
                "decision": json.dumps(decision_document, sort_keys=True),
                "quality": json.dumps(quality_document, sort_keys=True),
                "reasons": json.dumps(reason_codes),
                "fingerprint": fingerprint,
            },
        )
        if product_runtime_binding is not None:
            binding_document = {
                "contract_version": "v0.22.product_decision_runtime_binding.v1",
                "product_decision_id": str(decision_id),
                "product_input_snapshot_id": str(
                    product_runtime_binding.product_input_snapshot_id
                ),
                "product_runtime_execution_id": str(
                    product_runtime_binding.product_runtime_execution_id
                ),
                "aggregation_stage_id": str(
                    product_runtime_binding.aggregation_stage_id
                ),
                "strategy_stage_id": str(product_runtime_binding.strategy_stage_id),
                "defense_stage_id": _uuid(product_runtime_binding.defense_stage_id),
                "merge_stage_id": str(product_runtime_binding.merge_stage_id),
            }
            connection.execute(
                text(
                    """
                    INSERT INTO product.v022_product_decision_runtime_binding (
                      product_decision_id,product_input_snapshot_id,
                      product_runtime_execution_id,aggregation_stage_id,
                      strategy_stage_id,defense_stage_id,merge_stage_id,
                      binding_document,binding_fingerprint
                    ) VALUES (
                      :decision,:snapshot,:execution,:aggregation,:strategy,:defense,:merge,
                      CAST(:document AS jsonb),:fingerprint
                    )
                    """
                ),
                {
                    "decision": decision_id,
                    "snapshot": product_runtime_binding.product_input_snapshot_id,
                    "execution": product_runtime_binding.product_runtime_execution_id,
                    "aggregation": product_runtime_binding.aggregation_stage_id,
                    "strategy": product_runtime_binding.strategy_stage_id,
                    "defense": product_runtime_binding.defense_stage_id,
                    "merge": product_runtime_binding.merge_stage_id,
                    "document": json.dumps(binding_document, sort_keys=True),
                    "fingerprint": sha256_hexdigest(binding_document),
                },
            )


def _business(
    connection: Connection, table: str, id_column: str, identity: uuid.UUID
) -> RowMapping:
    row = (
        connection.execute(
            text(
                f"SELECT business.*,artifact.status FROM {table} business JOIN lineage.artifact artifact "
                f"ON artifact.artifact_id=business.artifact_id WHERE business.{id_column}=:identity"
            ),
            {"identity": identity},
        )
        .mappings()
        .one_or_none()
    )
    if row is None or row["status"] != "published":
        raise ValueError(f"Expected published {table} identity: {identity}")
    return row


def _session(connection: Connection, session_id: uuid.UUID) -> RowMapping:
    row = (
        connection.execute(
            text(
                "SELECT * FROM product.v022_decision_schedule_session WHERE decision_session_id=:session"
            ),
            {"session": session_id},
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise LookupError(f"Decision Session not found: {session_id}")
    return row


def _runtime_rows(
    connection: Connection, artifacts: RuntimeArtifactSet
) -> tuple[tuple[str, RowMapping], ...]:
    ordered = (
        ("input_manifest", artifacts.input_manifest_artifact_id),
        ("active_model_state", artifacts.active_model_state_artifact_id),
        ("aggregation_run", artifacts.aggregation_run_artifact_id),
        ("strategy_target", artifacts.strategy_target_artifact_id),
        ("defense_decision", artifacts.defense_decision_artifact_id),
        ("merged_target", artifacts.merged_target_artifact_id),
    )
    present = tuple((role, item) for role, item in ordered if item is not None)
    artifact_ids = tuple(item for _, item in present)
    if len(artifact_ids) != len(set(artifact_ids)):
        raise ValueError("Product Decision runtime Artifacts must be distinct")
    rows = []
    for role, artifact_id in present:
        row = (
            connection.execute(
                text(
                    "SELECT * FROM lineage.artifact WHERE artifact_id=:artifact AND status='published'"
                ),
                {"artifact": artifact_id},
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise ValueError(f"Product Decision runtime Artifact is not published: {artifact_id}")
        rows.append((role, row))
    return tuple(rows)


def _product_runtime_binding_rows(
    connection: Connection,
    identity: ProductRuntimeBindingIdentity,
    artifacts: RuntimeArtifactSet,
    *,
    product_enrollment_id: uuid.UUID,
    execution_version_id: uuid.UUID,
    configuration_snapshot_id: uuid.UUID,
    decision_session_id: uuid.UUID,
) -> RowMapping:
    row = (
        connection.execute(
            text(
                """
                SELECT snapshot.product_input_snapshot_id,snapshot.product_enrollment_id,
                       snapshot.execution_version_id,snapshot.decision_session_id,
                       snapshot.artifact_id AS product_input_snapshot_artifact_id,
                       snapshot.snapshot_fingerprint,snapshot_artifact.status AS snapshot_status,
                       execution.product_runtime_execution_id,
                       execution.product_input_snapshot_id AS execution_snapshot_id,
                       execution.configuration_snapshot_id,
                       execution.decision_session_id AS execution_session_id,
                       execution.artifact_id AS product_runtime_execution_artifact_id,
                       execution.execution_fingerprint,
                       execution_artifact.status AS execution_status,
                       aggregation.product_runtime_stage_id AS aggregation_stage_id,
                       aggregation.product_runtime_execution_id AS aggregation_execution_id,
                       aggregation.stage_kind AS aggregation_kind,
                       aggregation.artifact_id AS aggregation_artifact_id,
                       strategy.product_runtime_stage_id AS strategy_stage_id,
                       strategy.product_runtime_execution_id AS strategy_execution_id,
                       strategy.stage_kind AS strategy_kind,
                       strategy.artifact_id AS strategy_artifact_id,
                       defense.product_runtime_stage_id AS defense_stage_id,
                       defense.product_runtime_execution_id AS defense_execution_id,
                       defense.stage_kind AS defense_kind,
                       defense.artifact_id AS defense_artifact_id,
                       merge.product_runtime_stage_id AS merge_stage_id,
                       merge.product_runtime_execution_id AS merge_execution_id,
                       merge.stage_kind AS merge_kind,
                       merge.artifact_id AS merge_artifact_id,
                       EXISTS (
                         SELECT 1 FROM product.v022_product_runtime_stage_input input
                          WHERE input.product_runtime_stage_id=
                                aggregation.product_runtime_stage_id
                            AND input.role='processing_manifest'
                            AND input.input_artifact_id=:input_manifest
                       ) AS input_manifest_bound,
                       EXISTS (
                         SELECT 1 FROM product.v022_product_runtime_stage_input input
                          WHERE input.product_runtime_stage_id=
                                aggregation.product_runtime_stage_id
                            AND input.role='active_model_state'
                            AND input.input_artifact_id=:active_model_state
                       ) AS active_model_state_bound
                  FROM product.v022_product_input_snapshot snapshot
                  JOIN lineage.artifact snapshot_artifact
                    ON snapshot_artifact.artifact_id=snapshot.artifact_id
                  JOIN product.v022_product_runtime_execution execution
                    ON execution.product_runtime_execution_id=:runtime_execution
                  JOIN lineage.artifact execution_artifact
                    ON execution_artifact.artifact_id=execution.artifact_id
                  JOIN product.v022_product_runtime_stage aggregation
                    ON aggregation.product_runtime_stage_id=:aggregation_stage
                  JOIN product.v022_product_runtime_stage strategy
                    ON strategy.product_runtime_stage_id=:strategy_stage
                  LEFT JOIN product.v022_product_runtime_stage defense
                    ON defense.product_runtime_stage_id=:defense_stage
                  JOIN product.v022_product_runtime_stage merge
                    ON merge.product_runtime_stage_id=:merge_stage
                 WHERE snapshot.product_input_snapshot_id=:snapshot
                """
            ),
            {
                "snapshot": identity.product_input_snapshot_id,
                "runtime_execution": identity.product_runtime_execution_id,
                "aggregation_stage": identity.aggregation_stage_id,
                "strategy_stage": identity.strategy_stage_id,
                "defense_stage": identity.defense_stage_id,
                "merge_stage": identity.merge_stage_id,
                "input_manifest": artifacts.input_manifest_artifact_id,
                "active_model_state": artifacts.active_model_state_artifact_id,
            },
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise ValueError("Product Decision Product Runtime binding identities were not found")
    if (
        row["snapshot_status"] != "published"
        or row["execution_status"] != "published"
        or row["product_enrollment_id"] != product_enrollment_id
        or row["execution_version_id"] != execution_version_id
        or row["decision_session_id"] != decision_session_id
        or row["execution_snapshot_id"] != identity.product_input_snapshot_id
        or row["configuration_snapshot_id"] != configuration_snapshot_id
        or row["execution_session_id"] != decision_session_id
        or row["aggregation_execution_id"] != identity.product_runtime_execution_id
        or row["aggregation_kind"] != "aggregation"
        or row["aggregation_artifact_id"] != artifacts.aggregation_run_artifact_id
        or row["strategy_execution_id"] != identity.product_runtime_execution_id
        or row["strategy_kind"] != "strategy"
        or row["strategy_artifact_id"] != artifacts.strategy_target_artifact_id
        or row["merge_execution_id"] != identity.product_runtime_execution_id
        or row["merge_kind"] != "merge"
        or row["merge_artifact_id"] != artifacts.merged_target_artifact_id
        or not row["input_manifest_bound"]
        or row["active_model_state_bound"]
        != (artifacts.active_model_state_artifact_id is not None)
    ):
        raise ValueError("Product Decision exact Product Runtime binding is inconsistent")
    if identity.defense_stage_id is None:
        if artifacts.defense_decision_artifact_id is not None:
            raise ValueError("None Defense cannot bind a Defense Artifact")
    elif (
        row["defense_execution_id"] != identity.product_runtime_execution_id
        or row["defense_kind"] != "defense"
        or row["defense_artifact_id"] != artifacts.defense_decision_artifact_id
    ):
        raise ValueError("Product Decision Defense Stage binding is inconsistent")
    return row


def _required_runtime_ids(artifacts: RuntimeArtifactSet) -> tuple[uuid.UUID | None, ...]:
    return (
        artifacts.input_manifest_artifact_id,
        artifacts.aggregation_run_artifact_id,
        artifacts.strategy_target_artifact_id,
        artifacts.merged_target_artifact_id,
    )


def _uuid(value: object) -> str | None:
    return str(value) if value is not None else None
