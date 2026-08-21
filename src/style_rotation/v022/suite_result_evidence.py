from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, cast

from sqlalchemy import Engine, text

from style_rotation.lineage.service import DependencyInput
from style_rotation.v022.experiment_identity import (
    CommonEvaluationPanelService,
    PanelObservation,
    ResultEvidencePublication,
    ResultEvidenceService,
)


@dataclass(frozen=True, slots=True)
class SuiteResultEvidencePublication:
    research_suite_id: uuid.UUID
    publications: tuple[ResultEvidencePublication, ...]


class SuiteResultEvidenceService:
    """Promote completed typed Cell results into immutable v0.22 research evidence."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._panels = CommonEvaluationPanelService(engine)
        self._evidence = ResultEvidenceService(engine)

    def publish(self, research_suite_id: uuid.UUID) -> SuiteResultEvidencePublication:
        with self._engine.connect() as connection:
            cohort = (
                connection.execute(
                    text(
                        """
                        SELECT cohort.evaluation_cohort_version_id,cohort.artifact_id,
                               cohort.cohort_fingerprint,cohort.research_tier,
                               cohort.frequency,cohort.evaluation_start,
                               cohort.evaluation_end,artifact.status
                          FROM experiment.v022_research_suite_evaluation_cohort_binding binding
                          JOIN experiment.v022_evaluation_cohort_version cohort
                            ON cohort.evaluation_cohort_version_id=
                               binding.evaluation_cohort_version_id
                          JOIN lineage.artifact artifact
                            ON artifact.artifact_id=cohort.artifact_id
                         WHERE binding.research_suite_id=:suite
                        """
                    ),
                    {"suite": research_suite_id},
                )
                .mappings()
                .one_or_none()
            )
            if cohort is None or cohort["status"] != "published":
                raise ValueError("Result Evidence requires an exact published Evaluation Cohort")
            existing_panel = self._panels.existing_for_evaluation_cohort(
                evaluation_cohort_version_id=cohort["evaluation_cohort_version_id"],
                evaluation_cohort_fingerprint=cohort["cohort_fingerprint"],
            )
            panel_rows = (
                connection.execute(
                    text(
                        """
                        SELECT session.session_date,security.security_key
                          FROM experiment.v022_evaluation_cohort_session session
                          JOIN experiment.v022_cohort_eligibility_interval eligibility
                            ON eligibility.evaluation_cohort_version_id=
                               session.evaluation_cohort_version_id
                           AND session.session_date BETWEEN
                               eligibility.effective_start AND eligibility.effective_end
                          JOIN catalog.security security
                            ON security.security_id=eligibility.security_id
                         WHERE session.evaluation_cohort_version_id=:cohort
                           AND session.session_role='evaluation'
                           AND session.is_decision_session
                           AND eligibility.is_selectable
                         ORDER BY session.session_date,security.security_key
                        """
                    ),
                    {"cohort": cohort["evaluation_cohort_version_id"]},
                )
                .mappings()
                .all()
                if existing_panel is None
                else []
            )
            rows = (
                connection.execute(
                    text(
                        """
                        SELECT run.status AS run_status,
                               result.artifact_id AS result_artifact_id,
                               result.payload_manifest_artifact_id,
                               result.configuration_snapshot_id,
                               result.effective_start,result.effective_end,
                               result.result_fingerprint,
                               result.logical_payload_fingerprint,
                               result.manifest_hash,result.outcome,
                               result.quality_status,result.result_document,
                               trainable_diagnostic.artifact_id AS
                                 trainable_diagnostic_artifact_id,
                               trainable_diagnostic.diagnostic_fingerprint AS
                                 trainable_diagnostic_fingerprint,
                               trainable_diagnostic.diagnostic_document AS
                                 trainable_diagnostic_document,
                               data_context.artifact_id AS data_context_artifact_id,
                               data_context.context_fingerprint,
                               data_context.asset_context_document,
                               evaluation.artifact_id AS evaluation_artifact_id,
                               evaluation.context_fingerprint AS evaluation_fingerprint
                          FROM experiment.v022_research_suite_graph_run_binding suite_run
                          JOIN workspace.v022_graph_run run
                            ON run.graph_run_id=suite_run.graph_run_id
                          JOIN experiment.v022_suite_runtime_plan plan
                            ON plan.graph_run_id=run.graph_run_id
                          JOIN experiment.v022_portfolio_cell_work_spec spec
                            ON spec.suite_runtime_plan_id=plan.suite_runtime_plan_id
                          JOIN strategy.v022_compiled_strategy_branch branch
                            ON branch.compiled_strategy_branch_id=
                               spec.compiled_strategy_branch_id
                          JOIN aggregation.graph_run_aggregation_binding aggregation_binding
                            ON aggregation_binding.graph_run_id=run.graph_run_id
                           AND aggregation_binding.compiled_aggregation_instance_id=
                               branch.compiled_aggregation_instance_id
                          LEFT JOIN aggregation.v022_trainable_aggregation_diagnostic
                            trainable_diagnostic
                            ON trainable_diagnostic.aggregation_run_id=
                               aggregation_binding.aggregation_run_id
                          JOIN experiment.v022_portfolio_cell_runtime_result result
                            ON result.graph_work_item_id=spec.graph_work_item_id
                          JOIN workspace.v022_compiled_execution_data_context data_context
                            ON data_context.compiled_execution_data_context_id=
                               plan.compiled_execution_data_context_id
                          JOIN experiment.v022_portfolio_evaluation_data_context evaluation
                            ON evaluation.portfolio_evaluation_data_context_id=
                               spec.portfolio_evaluation_data_context_id
                         WHERE suite_run.research_suite_id=:suite
                         ORDER BY result.configuration_snapshot_id
                        """
                    ),
                    {"suite": research_suite_id},
                )
                .mappings()
                .all()
            )
        if not rows:
            raise ValueError("Completed v0.22 Suite has no typed Portfolio Cell results")
        if any(row["run_status"] != "completed" for row in rows):
            raise ValueError("Result Evidence requires a completed v0.22 Graph Run")
        panel = existing_panel
        if panel is None:
            observations = tuple(
                PanelObservation(item["session_date"], item["security_key"])
                for item in panel_rows
            )
            if not observations:
                raise ValueError("Evaluation Cohort has no selectable decision panel")
            panel = self._panels.publish(
                evidence_class="locked_historical_test",
                observations=observations,
                panel_document={
                    "mask_policy": "exact_evaluation_cohort_eligibility_v1",
                    "evaluation_cohort_version_id": str(
                        cohort["evaluation_cohort_version_id"]
                    ),
                    "evaluation_cohort_fingerprint": cohort["cohort_fingerprint"],
                    "research_tier": cohort["research_tier"],
                    "frequency": cohort["frequency"],
                    "evaluation_start": cohort["evaluation_start"].isoformat(),
                    "evaluation_end": cohort["evaluation_end"].isoformat(),
                },
                dependencies=(
                    DependencyInput(cohort["artifact_id"], "evaluation_cohort", 0),
                ),
                evaluation_cohort_version_id=cohort["evaluation_cohort_version_id"],
                evaluation_cohort_fingerprint=cohort["cohort_fingerprint"],
            )

        publications: list[ResultEvidencePublication] = []
        for row in rows:
            document = cast(dict[str, Any], row["result_document"])
            quality = cast(dict[str, Any], document["quality"])
            net_path = cast(list[dict[str, Any]], document["net_path"])
            if (
                row["effective_start"] != cohort["evaluation_start"]
                or row["effective_end"] != cohort["evaluation_end"]
                or not net_path
                or net_path[0]["session_date"] != cohort["evaluation_start"].isoformat()
                or net_path[-1]["session_date"] != cohort["evaluation_end"].isoformat()
            ):
                raise ValueError(
                    "Portfolio result does not cover its exact Evaluation Cohort range"
                )
            diagnostic_document = (
                cast(dict[str, Any], row["trainable_diagnostic_document"])
                if row["trainable_diagnostic_document"] is not None
                else None
            )
            evidence_document: dict[str, Any] = {
                "interval": [
                    row["effective_start"].isoformat(),
                    row["effective_end"].isoformat(),
                ],
                "result_fingerprint": row["result_fingerprint"],
                "logical_payload_fingerprint": row["logical_payload_fingerprint"],
                "manifest_hash": row["manifest_hash"],
                "portfolio_evaluation_data_context_fingerprint": row["evaluation_fingerprint"],
                "evaluation_cohort_version_id": str(cohort["evaluation_cohort_version_id"]),
                "evaluation_cohort_fingerprint": cohort["cohort_fingerprint"],
                "frequency": cohort["frequency"],
            }
            runtime_dependencies = [
                DependencyInput(row["payload_manifest_artifact_id"], "payload_manifest", 0),
                DependencyInput(row["evaluation_artifact_id"], "evaluation_context", 0),
                DependencyInput(cohort["artifact_id"], "evaluation_cohort", 0),
            ]
            if diagnostic_document is not None:
                evidence_document["trainable_aggregation_diagnostic"] = {
                    "diagnostic_fingerprint": row["trainable_diagnostic_fingerprint"],
                    "diagnostic_document": diagnostic_document,
                }
                runtime_dependencies.append(
                    DependencyInput(
                        row["trainable_diagnostic_artifact_id"],
                        "trainable_aggregation_diagnostic",
                        0,
                    )
                )
            publications.append(
                self._evidence.publish(
                    result_artifact_id=row["result_artifact_id"],
                    configuration_snapshot_id=row["configuration_snapshot_id"],
                    common_evaluation_panel_id=panel.common_evaluation_panel_id,
                    evidence_class="locked_historical_test",
                    evidence_document=evidence_document,
                    quality_document={
                        "state": row["quality_status"],
                        "outcome": row["outcome"],
                        "reason_code": quality["reason_code"],
                        "details": quality["details"],
                        "metric_document": quality["metric_document"],
                    },
                    runtime_dependencies=tuple(runtime_dependencies),
                    evaluation_cohort_version_id=cohort["evaluation_cohort_version_id"],
                    evaluation_cohort_fingerprint=cohort["cohort_fingerprint"],
                )
            )
        return SuiteResultEvidencePublication(research_suite_id, tuple(publications))
