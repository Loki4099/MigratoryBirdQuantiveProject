from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass
from decimal import Decimal
from typing import Any, Literal, cast

from sqlalchemy import Engine, text
from sqlalchemy.engine import Connection

from style_rotation.core.canonical import sha256_hexdigest
from style_rotation.experiment.v021_matrix import (
    PortfolioMatrixPolicy,
    build_fixed_portfolio_matrix,
)
from style_rotation.lineage.service import ArtifactService, DependencyInput
from style_rotation.ops.work_queue import WorkQueueService
from style_rotation.workspace.contracts import CompiledResearchSpec
from style_rotation.workspace.release_gates import ReleaseGateStatus, current_release_gates


class FormalSubmissionBlocked(RuntimeError):
    def __init__(self, reason_codes: tuple[str, ...]) -> None:
        self.reason_codes = reason_codes
        super().__init__("Formal Suite submission is blocked: " + ", ".join(reason_codes))


@dataclass(frozen=True, slots=True)
class FormalExecutionEvidence:
    comparison_context_fingerprint: str
    impact_policy_key: str
    impact_coefficient: Decimal
    impact_maximum_bps: Decimal
    comparison_context_artifact_id: uuid.UUID
    pit_gate_artifact_id: uuid.UUID | None
    terminal_gate_artifact_id: uuid.UUID | None
    impact_gate_artifact_id: uuid.UUID | None
    defensive_basket_version: str = "standard_defensive_basket_long_history_v1"
    suite_mode: Literal["formal", "exploratory"] = "formal"

    def document(self) -> dict[str, Any]:
        document: dict[str, Any] = {
            "suite_mode": self.suite_mode,
            "matrix": PortfolioMatrixPolicy().model_dump(mode="json"),
            "comparison_context_fingerprint": self.comparison_context_fingerprint,
            "impact_policy": {
                "policy_key": self.impact_policy_key,
                "coefficient": str(self.impact_coefficient),
                "maximum_bps": str(self.impact_maximum_bps),
                "p0_finalized": self.suite_mode == "formal",
                "enabled": self.suite_mode == "formal",
            },
            "comparison_context_artifact_id": str(self.comparison_context_artifact_id),
            "defensive_basket_version": self.defensive_basket_version,
        }
        if self.suite_mode == "formal":
            document["release_gate_artifact_ids"] = {
                "pit_universe": str(self.pit_gate_artifact_id),
                "terminal_event": str(self.terminal_gate_artifact_id),
                "impact_policy": str(self.impact_gate_artifact_id),
            }
        return document


@dataclass(frozen=True, slots=True)
class SuiteSubmission:
    research_suite_id: uuid.UUID
    suite_artifact_id: uuid.UUID
    suite_key: str
    suite_fingerprint: str
    predictive_cell_count: int
    portfolio_cell_count: int
    queued_work_item_count: int
    reused: bool
    suite_mode: Literal["formal", "exploratory"]

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["research_suite_id"] = str(self.research_suite_id)
        value["suite_artifact_id"] = str(self.suite_artifact_id)
        return value


class ResearchSuiteSubmissionService:
    """Publishes one immutable compiled Suite and idempotently enqueues every cell."""

    def __init__(
        self,
        engine: Engine,
        *,
        gate_provider: Callable[[], ReleaseGateStatus] = current_release_gates,
    ) -> None:
        self._engine = engine
        self._artifacts = ArtifactService(engine)
        self._queue = WorkQueueService(engine)
        self._gate_provider = gate_provider

    def submit(
        self,
        *,
        compiled: CompiledResearchSpec,
        normalized_selection: dict[str, Any],
        evidence: FormalExecutionEvidence,
        submission_key: str | None = None,
    ) -> SuiteSubmission:
        gates = self._gate_provider()
        if evidence.suite_mode == "formal" and not gates.formal_enabled:
            raise FormalSubmissionBlocked(gates.reason_codes)
        if not compiled.runnable or compiled.issues:
            raise ValueError("Only a runnable, issue-free compiled specification may submit")
        if compiled.portfolio_cell_count != len(compiled.strategy_branches) * 6:
            raise ValueError("Compiled Portfolio cell count violates the fixed six-cell matrix")
        self._validate_evidence(evidence)

        policy_id = self._publish_execution_policy(evidence)
        compiled_id, compiled_artifact_id, model_ids = self._publish_compiled_spec(
            compiled, normalized_selection
        )
        strategy_rows = self._publish_strategies(
            compiled, compiled_id, compiled_artifact_id, model_ids
        )
        suite_fingerprint = sha256_hexdigest(
            {
                "compiled_specification_fingerprint": compiled.specification_fingerprint,
                "execution_policy": evidence.document(),
                "submission_key": submission_key,
            }
        )
        suite_key = f"suite__{suite_fingerprint[:24]}"
        suite_id, suite_artifact_id, reused = self._publish_suite(
            compiled=compiled,
            compiled_id=compiled_id,
            compiled_artifact_id=compiled_artifact_id,
            policy_id=policy_id,
            suite_key=suite_key,
            suite_fingerprint=suite_fingerprint,
            evidence=evidence,
        )
        cell_artifacts = self._publish_cells(
            compiled=compiled,
            suite_id=suite_id,
            suite_artifact_id=suite_artifact_id,
            model_ids=model_ids,
            strategy_rows=strategy_rows,
            evidence=evidence,
        )
        queued = self._enqueue_cells(suite_id, cell_artifacts)
        return SuiteSubmission(
            research_suite_id=suite_id,
            suite_artifact_id=suite_artifact_id,
            suite_key=suite_key,
            suite_fingerprint=suite_fingerprint,
            predictive_cell_count=compiled.predictive_cell_count,
            portfolio_cell_count=compiled.portfolio_cell_count,
            queued_work_item_count=queued,
            reused=reused,
            suite_mode=evidence.suite_mode,
        )

    def cancel(self, research_suite_id: uuid.UUID) -> int:
        with self._engine.connect() as connection:
            item_ids = (
                connection.execute(
                    text(
                        "SELECT work_item_id FROM experiment.research_suite_work_item "
                        "WHERE research_suite_id = :suite_id"
                    ),
                    {"suite_id": research_suite_id},
                )
                .scalars()
                .all()
            )
        for item_id in item_ids:
            self._queue.request_cancel(item_id)
        return len(item_ids)

    def status(self, research_suite_id: uuid.UUID) -> dict[str, Any]:
        with self._engine.connect() as connection:
            rows = (
                connection.execute(
                    text(
                        """
                    SELECT item.status, count(*) AS item_count
                    FROM experiment.research_suite_work_item link
                    JOIN ops.work_item item ON item.work_item_id = link.work_item_id
                    WHERE link.research_suite_id = :suite_id
                    GROUP BY item.status
                    """
                    ),
                    {"suite_id": research_suite_id},
                )
                .mappings()
                .all()
            )
            suite_mode = connection.execute(
                text(
                    "SELECT suite_mode FROM experiment.research_suite WHERE research_suite_id = :id"
                ),
                {"id": research_suite_id},
            ).scalar_one_or_none()
        if suite_mode is None:
            raise LookupError(f"Research Suite not found: {research_suite_id}")
        counts = {str(row["status"]): int(row["item_count"]) for row in rows}
        total = sum(counts.values())
        terminal = sum(counts.get(key, 0) for key in ("completed", "failed", "cancelled", "reused"))
        return {
            "research_suite_id": str(research_suite_id),
            "total": total,
            "terminal": terminal,
            "complete": total > 0 and terminal == total,
            "status_counts": counts,
            "suite_mode": str(suite_mode),
        }

    def _validate_evidence(self, evidence: FormalExecutionEvidence) -> None:
        if len(evidence.comparison_context_fingerprint) != 64:
            raise ValueError("Comparison context fingerprint must be SHA-256")
        if evidence.impact_coefficient <= 0 or evidence.impact_maximum_bps <= 0:
            raise ValueError("Finalized impact parameters must be positive")
        with self._engine.connect() as connection:
            context = (
                connection.execute(
                    text("""
                SELECT context.context_fingerprint, artifact.status
                FROM experiment.comparison_context context
                JOIN lineage.artifact artifact ON artifact.artifact_id = context.artifact_id
                WHERE context.artifact_id = :artifact_id
            """),
                    {"artifact_id": evidence.comparison_context_artifact_id},
                )
                .mappings()
                .one_or_none()
            )
            gates = (
                connection.execute(
                    text("""
                SELECT gate.gate_key, gate.artifact_id
                FROM workspace.release_gate_evidence gate
                JOIN lineage.artifact artifact ON artifact.artifact_id = gate.artifact_id
                WHERE gate.artifact_id IN (:pit, :terminal, :impact)
                  AND gate.active AND artifact.status = 'published'
            """),
                    {
                        "pit": evidence.pit_gate_artifact_id,
                        "terminal": evidence.terminal_gate_artifact_id,
                        "impact": evidence.impact_gate_artifact_id,
                    },
                )
                .mappings()
                .all()
            )
        if (
            context is None
            or context["status"] != "published"
            or context["context_fingerprint"] != evidence.comparison_context_fingerprint
        ):
            raise ValueError("Formal evidence requires the exact published Comparison Context")
        if evidence.suite_mode == "exploratory":
            return
        if not all(
            (
                evidence.pit_gate_artifact_id,
                evidence.terminal_gate_artifact_id,
                evidence.impact_gate_artifact_id,
            )
        ):
            raise ValueError("Formal evidence must pin all Release Gate artifacts")
        expected = {
            "pit_universe": evidence.pit_gate_artifact_id,
            "terminal_event": evidence.terminal_gate_artifact_id,
            "impact_policy": evidence.impact_gate_artifact_id,
        }
        if {row["gate_key"]: row["artifact_id"] for row in gates} != expected:
            raise ValueError("Formal evidence does not pin all active Release Gate artifacts")

    def _ensure_materialized_id(
        self,
        *,
        artifact_id: uuid.UUID,
        row_name: str,
        select_sql: str,
        writer: Callable[[Connection, uuid.UUID], None],
    ) -> uuid.UUID:
        """Repair a legacy typed-row tombstone for an immutable Artifact.

        Older retention code removed relational catalog rows while deliberately
        retaining their published lineage Artifact.  ``ArtifactService.publish``
        correctly reuses that immutable Artifact and therefore does not invoke
        its draft writer again.  Serialize the one-time reconstruction by
        Artifact id so concurrent submissions cannot insert the same typed row
        twice.
        """

        with self._engine.begin() as connection:
            connection.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
                {"key": f"v021-rematerialize:{artifact_id}"},
            )
            row_id = connection.execute(
                text(select_sql), {"artifact_id": artifact_id}
            ).scalar_one_or_none()
            if row_id is None:
                writer(connection, artifact_id)
                row_id = connection.execute(
                    text(select_sql), {"artifact_id": artifact_id}
                ).scalar_one_or_none()
            if row_id is None:
                raise RuntimeError(
                    f"Published {row_name} Artifact could not be materialized: {artifact_id}"
                )
            return cast(uuid.UUID, row_id)

    def _publish_execution_policy(self, evidence: FormalExecutionEvidence) -> uuid.UUID:
        document = evidence.document()
        policy_key = (
            f"v021_{evidence.suite_mode}_execution_policy__{sha256_hexdigest(document)[:16]}"
        )

        def write(connection: Connection, artifact_id: uuid.UUID) -> None:
            connection.execute(
                text(
                    """
                    INSERT INTO experiment.execution_policy_catalog (
                        execution_policy_catalog_id, artifact_id, policy_key,
                        version_number, document
                    ) VALUES (:id, :artifact_id, :key, 1, CAST(:document AS jsonb))
                    """
                ),
                {
                    "id": uuid.uuid4(),
                    "artifact_id": artifact_id,
                    "key": policy_key,
                    "document": _json(document),
                },
            )

        dependencies = [
            DependencyInput(evidence.comparison_context_artifact_id, "comparison_context")
        ]
        if evidence.suite_mode == "formal":
            assert evidence.pit_gate_artifact_id is not None
            assert evidence.terminal_gate_artifact_id is not None
            assert evidence.impact_gate_artifact_id is not None
            dependencies.extend(
                (
                    DependencyInput(evidence.pit_gate_artifact_id, "pit_universe_gate"),
                    DependencyInput(evidence.terminal_gate_artifact_id, "terminal_event_gate"),
                    DependencyInput(evidence.impact_gate_artifact_id, "impact_policy_gate"),
                )
            )
        result = self._artifacts.publish(
            artifact_type="v021_execution_policy",
            artifact_key=policy_key,
            version_number=1,
            semantic_payload=document,
            content_payload=document,
            dependencies=tuple(dependencies),
            draft_writer=write,
        )
        return self._ensure_materialized_id(
            artifact_id=result.artifact_id,
            row_name="Execution Policy",
            select_sql=(
                "SELECT execution_policy_catalog_id "
                "FROM experiment.execution_policy_catalog "
                "WHERE artifact_id = :artifact_id"
            ),
            writer=write,
        )

    def _publish_compiled_spec(
        self, compiled: CompiledResearchSpec, selection: dict[str, Any]
    ) -> tuple[uuid.UUID, uuid.UUID, dict[str, uuid.UUID]]:
        payload = compiled.model_dump(mode="json")

        def write_model(
            connection: Connection, compiled_id: uuid.UUID, model: Any
        ) -> None:
            connection.execute(
                text(
                    """
                    INSERT INTO workspace.compiled_model_instance (
                        compiled_model_instance_id, compiled_research_spec_id,
                        instance_key, preset_key, family_key, output_type, frequency,
                        slot_assignments, parameters, target_key, instance_fingerprint
                    ) VALUES (
                        :id, :compiled_id, :instance_key, :preset_key, :family_key,
                        :output_type, :frequency, CAST(:slots AS jsonb),
                        CAST(:parameters AS jsonb), :target_key, :fingerprint
                    )
                    """
                ),
                {
                    "id": uuid.uuid4(),
                    "compiled_id": compiled_id,
                    "instance_key": model.instance_key,
                    "preset_key": model.preset_key,
                    "family_key": model.family_key,
                    "output_type": model.output_type,
                    "frequency": model.frequency,
                    "slots": _json(
                        [item.model_dump(mode="json") for item in model.slot_assignments]
                    ),
                    "parameters": _json(model.parameters),
                    "target_key": model.target_key,
                    "fingerprint": sha256_hexdigest(
                        {
                            "spec": compiled.specification_fingerprint,
                            "model": model.model_dump(mode="json"),
                        }
                    ),
                },
            )

        def write(connection: Connection, artifact_id: uuid.UUID) -> None:
            compiled_id = uuid.uuid4()
            connection.execute(
                text(
                    """
                    INSERT INTO workspace.compiled_research_spec (
                        compiled_research_spec_id, artifact_id, specification_fingerprint,
                        asset_context_key, frequency, normalized_selection,
                        model_instance_count, strategy_branch_count,
                        predictive_cell_count, portfolio_cell_count
                    ) VALUES (
                        :id, :artifact_id, :fingerprint, :asset_context, :frequency,
                        CAST(:selection AS jsonb), :models, :branches, :predictive, :portfolio
                    )
                    """
                ),
                {
                    "id": compiled_id,
                    "artifact_id": artifact_id,
                    "fingerprint": compiled.specification_fingerprint,
                    "asset_context": compiled.asset_context_key,
                    "frequency": compiled.model_instances[0].frequency,
                    "selection": _json(selection),
                    "models": len(compiled.model_instances),
                    "branches": len(compiled.strategy_branches),
                    "predictive": compiled.predictive_cell_count,
                    "portfolio": compiled.portfolio_cell_count,
                },
            )
            for model in compiled.model_instances:
                write_model(connection, compiled_id, model)

        result = self._artifacts.publish(
            artifact_type="compiled_research_spec",
            artifact_key=compiled.specification_fingerprint,
            version_number=1,
            semantic_payload=payload,
            content_payload=selection,
            draft_writer=write,
        )
        with self._engine.begin() as connection:
            connection.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
                {"key": f"v021-rematerialize:{result.artifact_id}"},
            )
            row = connection.execute(
                text(
                    """
                    SELECT compiled_research_spec_id
                    FROM workspace.compiled_research_spec
                    WHERE artifact_id = :artifact_id
                    """
                ),
                {"artifact_id": result.artifact_id},
            ).scalar_one_or_none()
            if row is None:
                write(connection, result.artifact_id)
                row = connection.execute(
                    text(
                        """
                        SELECT compiled_research_spec_id
                        FROM workspace.compiled_research_spec
                        WHERE artifact_id = :artifact_id
                        """
                    ),
                    {"artifact_id": result.artifact_id},
                ).scalar_one()
            for model in compiled.model_instances:
                present = connection.execute(
                    text(
                        """
                        SELECT compiled_model_instance_id
                        FROM workspace.compiled_model_instance
                        WHERE compiled_research_spec_id = :compiled_id
                          AND instance_key = :instance_key
                        """
                    ),
                    {"compiled_id": row, "instance_key": model.instance_key},
                ).scalar_one_or_none()
                if present is None:
                    write_model(connection, row, model)
            models = (
                connection.execute(
                    text(
                        """
                        SELECT instance_key, compiled_model_instance_id
                        FROM workspace.compiled_model_instance
                        WHERE compiled_research_spec_id = :id
                        """
                    ),
                    {"id": row},
                )
                .mappings()
                .all()
            )
            expected_model_keys = {model.instance_key for model in compiled.model_instances}
            materialized_model_keys = {str(item["instance_key"]) for item in models}
            if materialized_model_keys != expected_model_keys:
                raise RuntimeError(
                    "Published Compiled Research Spec has inconsistent Model instances"
                )
        return (
            row,
            result.artifact_id,
            {item["instance_key"]: item["compiled_model_instance_id"] for item in models},
        )

    def _publish_strategies(
        self,
        compiled: CompiledResearchSpec,
        compiled_id: uuid.UUID,
        compiled_artifact_id: uuid.UUID,
        model_ids: dict[str, uuid.UUID],
    ) -> dict[str, tuple[uuid.UUID, uuid.UUID]]:
        rows: dict[str, tuple[uuid.UUID, uuid.UUID]] = {}
        for branch in compiled.strategy_branches:
            payload = branch.model_dump(mode="json")
            fingerprint = sha256_hexdigest(
                {"spec": compiled.specification_fingerprint, "branch": payload}
            )

            def write(
                connection: Connection,
                artifact_id: uuid.UUID,
                *,
                branch: Any = branch,
                payload: dict[str, Any] = payload,
                fingerprint: str = fingerprint,
            ) -> None:
                connection.execute(
                    text(
                        """
                        INSERT INTO strategy.compiled_strategy_version (
                            compiled_strategy_version_id, artifact_id, compiled_research_spec_id,
                            compiled_model_instance_id, branch_key, strategy_family_key,
                            strategy_preset_key, schedule_key, rule_graph, strategy_fingerprint
                        ) VALUES (
                            :id, :artifact_id, :compiled_id, :model_id, :branch_key,
                            :family_key, :preset_key, :schedule_key,
                            CAST(:rule_graph AS jsonb), :fingerprint
                        )
                        """
                    ),
                    {
                        "id": uuid.uuid4(),
                        "artifact_id": artifact_id,
                        "compiled_id": compiled_id,
                        "model_id": model_ids[branch.model_instance_key],
                        "branch_key": branch.branch_key,
                        "family_key": branch.strategy_family_key,
                        "preset_key": branch.strategy_preset_key,
                        "schedule_key": branch.frequency,
                        "rule_graph": _json(payload),
                        "fingerprint": fingerprint,
                    },
                )

            result = self._artifacts.publish(
                artifact_type="compiled_strategy_version",
                artifact_key=fingerprint,
                version_number=1,
                semantic_payload=payload,
                content_payload=payload,
                dependencies=(DependencyInput(compiled_artifact_id, "compiled_research_spec"),),
                draft_writer=write,
            )
            strategy_id = self._ensure_materialized_id(
                artifact_id=result.artifact_id,
                row_name="Compiled Strategy",
                select_sql=(
                    "SELECT compiled_strategy_version_id "
                    "FROM strategy.compiled_strategy_version "
                    "WHERE artifact_id = :artifact_id"
                ),
                writer=write,
            )
            rows[branch.branch_key] = (strategy_id, result.artifact_id)
        return rows

    def _publish_suite(
        self,
        *,
        compiled: CompiledResearchSpec,
        compiled_id: uuid.UUID,
        compiled_artifact_id: uuid.UUID,
        policy_id: uuid.UUID,
        suite_key: str,
        suite_fingerprint: str,
        evidence: FormalExecutionEvidence,
    ) -> tuple[uuid.UUID, uuid.UUID, bool]:
        with self._engine.connect() as connection:
            policy_artifact_id = connection.execute(
                text(
                    """
                    SELECT artifact_id
                    FROM experiment.execution_policy_catalog
                    WHERE execution_policy_catalog_id = :id
                    """
                ),
                {"id": policy_id},
            ).scalar_one()

        def write(connection: Connection, artifact_id: uuid.UUID) -> None:
            connection.execute(
                text(
                    """
                    INSERT INTO experiment.research_suite (
                        research_suite_id, artifact_id, compiled_research_spec_id,
                        execution_policy_catalog_id, suite_key, version_number,
                        suite_fingerprint, predictive_cell_count, portfolio_cell_count,
                        suite_mode
                    ) VALUES (:id, :artifact_id, :compiled_id, :policy_id, :suite_key,
                              1, :fingerprint, :predictive, :portfolio, :suite_mode)
                    """
                ),
                {
                    "id": uuid.uuid4(),
                    "artifact_id": artifact_id,
                    "compiled_id": compiled_id,
                    "policy_id": policy_id,
                    "suite_key": suite_key,
                    "fingerprint": suite_fingerprint,
                    "predictive": compiled.predictive_cell_count,
                    "portfolio": compiled.portfolio_cell_count,
                    "suite_mode": evidence.suite_mode,
                },
            )

        payload = {
            "suite_fingerprint": suite_fingerprint,
            "compiled": compiled.specification_fingerprint,
            "execution_policy": evidence.document(),
        }
        result = self._artifacts.publish(
            artifact_type="research_suite",
            artifact_key=suite_key,
            version_number=1,
            semantic_payload=payload,
            content_payload=payload,
            dependencies=(
                DependencyInput(compiled_artifact_id, "compiled_research_spec"),
                DependencyInput(policy_artifact_id, "execution_policy"),
            ),
            draft_writer=write,
        )
        with self._engine.connect() as connection:
            suite_id = connection.execute(
                text(
                    """
                    SELECT research_suite_id
                    FROM experiment.research_suite
                    WHERE artifact_id = :id
                    """
                ),
                {"id": result.artifact_id},
            ).scalar_one()
        return suite_id, result.artifact_id, result.reused

    def _publish_cells(
        self,
        *,
        compiled: CompiledResearchSpec,
        suite_id: uuid.UUID,
        suite_artifact_id: uuid.UUID,
        model_ids: dict[str, uuid.UUID],
        strategy_rows: dict[str, tuple[uuid.UUID, uuid.UUID]],
        evidence: FormalExecutionEvidence,
    ) -> list[tuple[uuid.UUID, str, str]]:
        cells: list[tuple[uuid.UUID, str, str]] = []
        for model in compiled.model_instances:
            target = model.target_key or (
                "weekly_next_open_to_next_open"
                if model.frequency == "weekly"
                else "monthly_next_open_to_next_open"
            )
            fingerprint = sha256_hexdigest(
                {
                    "suite": str(suite_artifact_id),
                    "model": model.model_dump(mode="json"),
                    "target": target,
                }
            )
            cell_key = f"predictive__{model.instance_key}__{target}"

            def write_predictive(
                connection: Connection,
                artifact_id: uuid.UUID,
                *,
                model: Any = model,
                target: str = target,
                fingerprint: str = fingerprint,
                cell_key: str = cell_key,
            ) -> None:
                connection.execute(
                    text("""
                    INSERT INTO experiment.predictive_cell_specification (
                        predictive_cell_specification_id, artifact_id, research_suite_id,
                        compiled_model_instance_id, cell_key, frequency,
                        evaluation_target_key, cell_fingerprint
                    ) VALUES (:id, :artifact_id, :suite_id, :model_id, :cell_key,
                              :frequency, :target, :fingerprint)
                """),
                    {
                        "id": uuid.uuid4(),
                        "artifact_id": artifact_id,
                        "suite_id": suite_id,
                        "model_id": model_ids[model.instance_key],
                        "cell_key": cell_key,
                        "frequency": model.frequency,
                        "target": target,
                        "fingerprint": fingerprint,
                    },
                )

            result = self._artifacts.publish(
                artifact_type="predictive_cell_specification",
                artifact_key=fingerprint,
                version_number=1,
                semantic_payload={"cell_key": cell_key, "target": target},
                content_payload=model.model_dump(mode="json"),
                dependencies=(DependencyInput(suite_artifact_id, "research_suite"),),
                draft_writer=write_predictive,
            )
            cells.append((result.artifact_id, fingerprint, "predictive"))

        matrix = build_fixed_portfolio_matrix(
            compiled.strategy_branches,
            comparison_context_fingerprint=evidence.comparison_context_fingerprint,
        )
        for ordinal, cell in enumerate(matrix):
            strategy_id, strategy_artifact_id = strategy_rows[cell.branch_key]
            cell_fingerprint = sha256_hexdigest(
                {"suite": str(suite_artifact_id), "cell": asdict(cell)}
            )

            def write_portfolio(
                connection: Connection,
                artifact_id: uuid.UUID,
                *,
                cell: Any = cell,
                ordinal: int = ordinal,
                strategy_id: uuid.UUID = strategy_id,
                cell_fingerprint: str = cell_fingerprint,
            ) -> None:
                connection.execute(
                    text("""
                    INSERT INTO experiment.portfolio_cell_specification (
                        portfolio_cell_specification_id, artifact_id, research_suite_id,
                        compiled_strategy_version_id, cell_key, ordinal, window_key,
                        cost_key, cost_bps_per_side, initial_capital_usd,
                        initialization_policy, state_reset, capacity_adv_limit,
                        cell_fingerprint
                    ) VALUES (:id, :artifact_id, :suite_id, :strategy_id, :cell_key,
                              :ordinal, :window_key, :cost_key, :bps, :capital,
                              :initialization, true, :capacity, :fingerprint)
                """),
                    {
                        "id": uuid.uuid4(),
                        "artifact_id": artifact_id,
                        "suite_id": suite_id,
                        "strategy_id": strategy_id,
                        "cell_key": cell.cell_key,
                        "ordinal": ordinal,
                        "window_key": cell.window_key,
                        "cost_key": cell.cost_key,
                        "bps": cell.cost_bps_per_side,
                        "capital": cell.initial_capital_usd,
                        "initialization": cell.initialization_policy,
                        "capacity": PortfolioMatrixPolicy().capacity_adv_limit,
                        "fingerprint": cell_fingerprint,
                    },
                )

            result = self._artifacts.publish(
                artifact_type="portfolio_cell_specification",
                artifact_key=cell_fingerprint,
                version_number=1,
                semantic_payload=asdict(cell),
                content_payload=asdict(cell),
                dependencies=(
                    DependencyInput(suite_artifact_id, "research_suite"),
                    DependencyInput(strategy_artifact_id, "compiled_strategy"),
                ),
                draft_writer=write_portfolio,
            )
            cells.append((result.artifact_id, cell_fingerprint, "portfolio"))
        return cells

    def _enqueue_cells(self, suite_id: uuid.UUID, cells: list[tuple[uuid.UUID, str, str]]) -> int:
        queued = 0
        for artifact_id, fingerprint, cell_type in cells:
            enqueue = self._queue.enqueue(
                specification_fingerprint=fingerprint,
                work_type="predictive" if cell_type == "predictive" else "portfolio",
            )
            with self._engine.begin() as connection:
                connection.execute(
                    text(
                        """
                        INSERT INTO experiment.research_suite_work_item (
                            research_suite_id, cell_artifact_id, work_item_id, cell_type
                        ) VALUES (:suite_id, :artifact_id, :work_item_id, :cell_type)
                        ON CONFLICT (research_suite_id, cell_artifact_id) DO UPDATE
                        SET work_item_id = EXCLUDED.work_item_id,
                            cell_type = EXCLUDED.cell_type
                        """
                    ),
                    {
                        "suite_id": suite_id,
                        "artifact_id": artifact_id,
                        "work_item_id": enqueue.item.work_item_id,
                        "cell_type": cell_type,
                    },
                )
            queued += int(enqueue.item.status in {"queued", "running"})
        return queued


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
