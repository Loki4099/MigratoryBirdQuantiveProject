from __future__ import annotations

import json
import statistics
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from sqlalchemy import Engine, text
from sqlalchemy.engine import Connection

from style_rotation.core.canonical import sha256_hexdigest
from style_rotation.experiment.result_payload import hydrate_cell_result_row
from style_rotation.lineage.service import ArtifactService, DependencyInput
from style_rotation.ops.maintenance import suite_generation_guard
from style_rotation.product.qualification import (
    QualificationCell,
    evaluate_research_candidate_qualification,
)
from style_rotation.workspace.release_gates import ReleaseGateStatus, current_release_gates


@dataclass(frozen=True, slots=True)
class QualificationEvaluation:
    eligible: bool
    reason_codes: tuple[str, ...]
    warning_codes: tuple[str, ...]
    compiled_strategy_version_id: uuid.UUID | None
    source_suite_artifact_id: uuid.UUID | None
    comparison_context_id: uuid.UUID | None
    result_artifact_ids: tuple[uuid.UUID, ...]
    cell_artifact_ids: tuple[uuid.UUID, ...]
    selection_context: dict[str, Any]
    qualification_bundle_artifact_id: uuid.UUID | None = None
    predictive_result_artifact_ids: tuple[uuid.UUID, ...] = ()
    predictive_cell_artifact_ids: tuple[uuid.UUID, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        for key in (
            "compiled_strategy_version_id",
            "source_suite_artifact_id",
            "comparison_context_id",
            "qualification_bundle_artifact_id",
        ):
            if payload[key] is not None:
                payload[key] = str(payload[key])
        payload["result_artifact_ids"] = [str(value) for value in self.result_artifact_ids]
        payload["cell_artifact_ids"] = [str(value) for value in self.cell_artifact_ids]
        payload["predictive_result_artifact_ids"] = [
            str(value) for value in self.predictive_result_artifact_ids
        ]
        payload["predictive_cell_artifact_ids"] = [
            str(value) for value in self.predictive_cell_artifact_ids
        ]
        return payload


@dataclass(frozen=True, slots=True)
class PromotionResult:
    product_enrollment_id: uuid.UUID
    product_version_artifact_id: uuid.UUID
    qualification_bundle_artifact_id: uuid.UUID
    lifecycle: str
    revision: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "product_enrollment_id": str(self.product_enrollment_id),
            "product_version_artifact_id": str(self.product_version_artifact_id),
            "qualification_bundle_artifact_id": str(self.qualification_bundle_artifact_id),
            "lifecycle": self.lifecycle,
            "revision": self.revision,
        }


class ProductPromotionService:
    def __init__(
        self,
        engine: Engine,
        *,
        gate_provider: Callable[[], ReleaseGateStatus] = current_release_gates,
    ) -> None:
        self._engine = engine
        self._artifacts = ArtifactService(engine)
        self._gate_provider = gate_provider

    def evaluate(self, result_artifact_id: uuid.UUID) -> QualificationEvaluation:
        with self._engine.connect() as connection:
            anchor = (
                connection.execute(
                    text(
                        """
                    SELECT strategy.compiled_strategy_version_id,
                           strategy.strategy_fingerprint,
                           suite.research_suite_id, suite.artifact_id AS suite_artifact_id,
                           suite.suite_fingerprint, suite.suite_mode,
                           spec.normalized_selection,
                           strategy.branch_key, strategy.strategy_family_key,
                           strategy.strategy_preset_key, strategy.schedule_key,
                           strategy.rule_graph,
                           model.instance_key AS model_instance_key,
                           model.preset_key AS model_preset_key,
                           model.target_key AS model_target_key,
                           model.slot_assignments AS model_slot_assignments,
                           model.parameters AS model_parameters,
                           policy.document ->> 'comparison_context_fingerprint'
                               AS comparison_context_fingerprint
                    FROM experiment.cell_result result
                    JOIN experiment.portfolio_cell_specification cell
                      ON cell.artifact_id = result.cell_artifact_id
                    JOIN strategy.compiled_strategy_version strategy
                      ON strategy.compiled_strategy_version_id =
                         cell.compiled_strategy_version_id
                    JOIN workspace.compiled_model_instance model
                      ON model.compiled_model_instance_id =
                         strategy.compiled_model_instance_id
                    JOIN experiment.research_suite suite
                      ON suite.research_suite_id = cell.research_suite_id
                    JOIN experiment.execution_policy_catalog policy
                      ON policy.execution_policy_catalog_id =
                         suite.execution_policy_catalog_id
                    JOIN workspace.compiled_research_spec spec
                      ON spec.compiled_research_spec_id = suite.compiled_research_spec_id
                    WHERE result.artifact_id = :result_artifact_id
                    """
                    ),
                    {"result_artifact_id": result_artifact_id},
                )
                .mappings()
                .one_or_none()
            )
            if anchor is None:
                return QualificationEvaluation(
                    False,
                    ("v021_portfolio_cell_result_required",),
                    (),
                    None,
                    None,
                    None,
                    (),
                    (),
                    {},
                )
            rows = (
                connection.execute(
                    text(
                        """
                    SELECT cell.artifact_id AS cell_artifact_id,
                           result.artifact_id AS result_artifact_id,
                           cell.cell_fingerprint, cell.window_key,
                           cell.cost_bps_per_side, result.availability_status,
                           result.quality_status, result.series, result.diagnostics,
                           result.payload_storage_uri, result.payload_content_hash,
                           result.payload_storage_format, result.payload_schema_version,
                           result.payload_byte_size,
                           result_artifact.status AS artifact_status
                    FROM experiment.portfolio_cell_specification cell
                    LEFT JOIN experiment.cell_result result
                      ON result.cell_artifact_id = cell.artifact_id
                    LEFT JOIN lineage.artifact result_artifact
                      ON result_artifact.artifact_id = result.artifact_id
                    WHERE cell.research_suite_id = :suite_id
                      AND cell.compiled_strategy_version_id = :strategy_id
                    ORDER BY cell.ordinal
                    """
                    ),
                    {
                        "suite_id": anchor["research_suite_id"],
                        "strategy_id": anchor["compiled_strategy_version_id"],
                    },
                )
                .mappings()
                .all()
            )
            context_id = connection.execute(
                text(
                    "SELECT comparison_context_id FROM experiment.comparison_context "
                    "WHERE context_fingerprint = :fingerprint"
                ),
                {"fingerprint": anchor["comparison_context_fingerprint"]},
            ).scalar_one_or_none()
            predictive_rows = (
                connection.execute(
                    text(
                        """
                    SELECT cell.artifact_id AS cell_artifact_id,
                           result.artifact_id AS result_artifact_id,
                           result.availability_status,
                           result_artifact.status AS artifact_status
                    FROM strategy.compiled_strategy_version strategy
                    JOIN experiment.predictive_cell_specification cell
                      ON cell.research_suite_id = :suite_id
                     AND cell.compiled_model_instance_id = strategy.compiled_model_instance_id
                    LEFT JOIN experiment.cell_result result
                      ON result.cell_artifact_id = cell.artifact_id
                    LEFT JOIN lineage.artifact result_artifact
                      ON result_artifact.artifact_id = result.artifact_id
                    WHERE strategy.compiled_strategy_version_id = :strategy_id
                    ORDER BY cell.created_at, cell.artifact_id
                    """
                    ),
                    {
                        "suite_id": anchor["research_suite_id"],
                        "strategy_id": anchor["compiled_strategy_version_id"],
                    },
                )
                .mappings()
                .all()
            )
        hydrated_rows = tuple(hydrate_cell_result_row(row) for row in rows)
        gates = self._gate_provider()
        cells = tuple(
            QualificationCell(
                cell_fingerprint=row["cell_fingerprint"],
                strategy_fingerprint=anchor["strategy_fingerprint"],
                suite_fingerprint=anchor["suite_fingerprint"],
                comparison_context_fingerprint=anchor["comparison_context_fingerprint"],
                window_key=row["window_key"],
                cost_bps_per_side=row["cost_bps_per_side"],
                status=_qualification_status(row),
                formal_eligible=row["artifact_status"] == "published",
            )
            for row in hydrated_rows
        )
        decision = evaluate_research_candidate_qualification(
            cells,
            pit_universe_gate_closed="pit_universe_gate_open" not in gates.reason_codes,
            terminal_event_gate_closed="terminal_event_gate_open" not in gates.reason_codes,
            impact_policy_gate_closed="impact_policy_gate_open" not in gates.reason_codes,
        )
        reasons = list(decision.reason_codes)
        warnings = list(decision.warning_codes)
        if any(
            _has_quality_warning(row, "capacity_adv_5_percent") for row in hydrated_rows
        ):
            warnings.append("candidate_capacity_not_100m_eligible")
        if anchor["suite_mode"] != "formal":
            warnings.append("candidate_exploratory_suite")
        if context_id is None:
            reasons.append("comparison_context_not_published")
        accepted_predictive = [
            row
            for row in predictive_rows
            if row["result_artifact_id"] is not None
            and row["availability_status"] == "accepted"
            and row["artifact_status"] == "published"
        ]
        if len(accepted_predictive) != 1:
            reasons.append("predictive_qualification_result_missing")
        return QualificationEvaluation(
            eligible=not reasons,
            reason_codes=tuple(dict.fromkeys(reasons)),
            warning_codes=tuple(dict.fromkeys(warnings)),
            compiled_strategy_version_id=anchor["compiled_strategy_version_id"],
            source_suite_artifact_id=anchor["suite_artifact_id"],
            comparison_context_id=context_id,
            result_artifact_ids=tuple(
                row["result_artifact_id"]
                for row in hydrated_rows
                if row["result_artifact_id"] is not None
            ),
            cell_artifact_ids=tuple(row["cell_artifact_id"] for row in hydrated_rows),
            selection_context={
                "selected_result_artifact_id": str(result_artifact_id),
                "selected_branch_key": anchor["branch_key"],
                "selection_mode": "manual_experiment_detail_promotion",
                "exact_selection": _exact_selection(anchor),
                "predictive_result_artifact_ids": [
                    str(row["result_artifact_id"]) for row in accepted_predictive
                ],
                "predictive_cell_artifact_ids": [
                    str(row["cell_artifact_id"]) for row in accepted_predictive
                ],
            },
            predictive_result_artifact_ids=tuple(
                row["result_artifact_id"] for row in accepted_predictive
            ),
            predictive_cell_artifact_ids=tuple(
                row["cell_artifact_id"] for row in accepted_predictive
            ),
        )

    def promote(
        self,
        result_artifact_id: uuid.UUID,
        *,
        name: str,
        researcher_id: str,
        selection_reason: str,
        note: str | None = None,
    ) -> PromotionResult:
        if not name.strip() or not researcher_id.strip() or not selection_reason.strip():
            raise ValueError("Promotion requires name, researcher, and selection reason")
        # Promotion publishes its evidence graph across several transactions.  Hold the
        # same session advisory guard used by Suite publication/retention so a prune
        # cannot observe the intermediate state after Qualification publication but
        # before the Product Version pins that Qualification.
        with suite_generation_guard(self._engine):
            evaluation = self.evaluate(result_artifact_id)
            if not evaluation.eligible:
                raise ValueError(
                    "Product promotion is blocked: " + ", ".join(evaluation.reason_codes)
                )
            assert evaluation.compiled_strategy_version_id is not None
            assert evaluation.source_suite_artifact_id is not None
            assert evaluation.comparison_context_id is not None
            qualification_id, qualification_artifact_id = self._publish_qualification(
                evaluation
            )
            monitoring_id, monitoring_artifact_id = self._publish_monitoring_policy(
                evaluation
            )
            return self._publish_product_and_enrollment(
                evaluation=evaluation,
                qualification_id=qualification_id,
                qualification_artifact_id=qualification_artifact_id,
                monitoring_id=monitoring_id,
                monitoring_artifact_id=monitoring_artifact_id,
                name=name.strip(),
                researcher_id=researcher_id.strip(),
                selection_reason=selection_reason.strip(),
                note=note,
            )

    def _publish_qualification(
        self, evaluation: QualificationEvaluation
    ) -> tuple[uuid.UUID, uuid.UUID]:
        assert evaluation.compiled_strategy_version_id is not None
        assert evaluation.source_suite_artifact_id is not None
        assert evaluation.comparison_context_id is not None
        payload = evaluation.to_dict()
        fingerprint = sha256_hexdigest(payload)

        def write(connection: Connection, artifact_id: uuid.UUID) -> None:
            connection.execute(
                text(
                    """
                    INSERT INTO experiment.qualification_bundle (
                        qualification_bundle_id, artifact_id,
                        compiled_strategy_version_id, source_suite_artifact_id,
                        comparison_context_id, portfolio_cell_count,
                        formal_eligible, product_eligible, gate_results,
                        result_artifact_ids, cell_artifact_ids, selection_context
                    ) VALUES (:id, :artifact_id, :strategy_id, :suite_artifact_id,
                              :context_id, 6, :formal_eligible, true, CAST(:gates AS jsonb),
                              CAST(:result_ids AS uuid[]), CAST(:cell_ids AS uuid[]),
                              CAST(:selection_context AS jsonb))
                    """
                ),
                {
                    "id": uuid.uuid4(),
                    "artifact_id": artifact_id,
                    "strategy_id": evaluation.compiled_strategy_version_id,
                    "suite_artifact_id": evaluation.source_suite_artifact_id,
                    "context_id": evaluation.comparison_context_id,
                    "formal_eligible": not evaluation.warning_codes,
                    "gates": _json(payload),
                    "result_ids": list(evaluation.result_artifact_ids),
                    "cell_ids": list(evaluation.cell_artifact_ids),
                    "selection_context": _json(evaluation.selection_context),
                },
            )

        result = self._artifacts.publish(
            artifact_type="qualification_bundle",
            artifact_key=fingerprint,
            version_number=1,
            semantic_payload=payload,
            content_payload=payload,
            dependencies=(
                DependencyInput(evaluation.source_suite_artifact_id, "source_suite"),
                *(
                    DependencyInput(result_id, "qualification_result")
                    for result_id in evaluation.result_artifact_ids
                ),
                *(
                    DependencyInput(result_id, "qualification_predictive_result")
                    for result_id in evaluation.predictive_result_artifact_ids
                ),
            ),
            draft_writer=write,
        )
        with self._engine.connect() as connection:
            qualification_id = connection.execute(
                text(
                    "SELECT qualification_bundle_id FROM experiment.qualification_bundle "
                    "WHERE artifact_id = :artifact_id"
                ),
                {"artifact_id": result.artifact_id},
            ).scalar_one()
        return qualification_id, result.artifact_id

    def _publish_monitoring_policy(
        self, evaluation: QualificationEvaluation
    ) -> tuple[uuid.UUID, uuid.UUID]:
        references = self._monitoring_references(evaluation)
        parameters = {
            "frequency_aware_predictive_minimum": {"weekly": 26, "monthly": 12},
            "performance_minimum_sessions": 126,
            "oos_performance_window_sessions": 126,
            "watch_percentile": 0.10,
            "warning_percentile": 0.05,
            "watch_consecutive_reviews": 2,
            "warning_consecutive_reviews": 3,
            **references,
            "automatic_retraining": False,
        }
        # Reference thresholds are frozen from the promoted Strategy's six-cell
        # qualification history, so they are part of this immutable policy identity.
        # A global fixed key would collide as soon as a second Strategy is promoted.
        policy_key = f"v021_oos_monitoring__{sha256_hexdigest(parameters)[:24]}"

        def write(connection: Connection, artifact_id: uuid.UUID) -> None:
            connection.execute(
                text(
                    """
                    INSERT INTO product.monitoring_policy (
                        monitoring_policy_id, artifact_id, policy_key,
                        version_number, parameters
                    ) VALUES (:id, :artifact_id, :key, 1, CAST(:parameters AS jsonb))
                    """
                ),
                {
                    "id": uuid.uuid4(),
                    "artifact_id": artifact_id,
                    "key": policy_key,
                    "parameters": _json(parameters),
                },
            )

        result = self._artifacts.publish(
            artifact_type="monitoring_policy",
            artifact_key=policy_key,
            version_number=1,
            semantic_payload=parameters,
            content_payload=parameters,
            draft_writer=write,
        )
        with self._engine.connect() as connection:
            monitoring_id = connection.execute(
                text(
                    "SELECT monitoring_policy_id FROM product.monitoring_policy "
                    "WHERE artifact_id = :artifact_id"
                ),
                {"artifact_id": result.artifact_id},
            ).scalar_one()
        return monitoring_id, result.artifact_id

    def _monitoring_references(self, evaluation: QualificationEvaluation) -> dict[str, Any]:
        with self._engine.connect() as connection:
            portfolio_rows = (
                connection.execute(
                    text(
                        "SELECT series, diagnostics, payload_storage_uri, "
                        "payload_content_hash, payload_storage_format, "
                        "payload_schema_version, payload_byte_size "
                        "FROM experiment.cell_result "
                        "WHERE artifact_id = ANY(CAST(:ids AS uuid[]))"
                    ),
                    {"ids": list(evaluation.result_artifact_ids)},
                )
                .mappings()
                .all()
            )
            predictive_rows = (
                connection.execute(
                    text(
                        "SELECT series, diagnostics, payload_storage_uri, "
                        "payload_content_hash, payload_storage_format, "
                        "payload_schema_version, payload_byte_size "
                        "FROM experiment.cell_result "
                        "WHERE artifact_id = ANY(CAST(:ids AS uuid[]))"
                    ),
                    {"ids": list(evaluation.predictive_result_artifact_ids)},
                )
                .mappings()
                .all()
            )
        hydrated_portfolio_rows = tuple(
            hydrate_cell_result_row(row) for row in portfolio_rows
        )
        hydrated_predictive_rows = tuple(
            hydrate_cell_result_row(row) for row in predictive_rows
        )
        performance: list[float] = []
        for row in hydrated_portfolio_rows:
            nav = row["series"].get("nav_series", [])
            for index in range(125, len(nav)):
                start = nav[index - 125]
                end = nav[index]
                performance.append(
                    (end["strategy_wealth"] / start["strategy_wealth"])
                    / (end["benchmark_wealth"] / start["benchmark_wealth"])
                    - 1
                )
        dispersion: list[float] = []
        for row in hydrated_predictive_rows:
            by_date: dict[str, list[float]] = {}
            for point in row["series"].get("model_scores", []):
                by_date.setdefault(point["observation_date"], []).append(float(point["score"]))
            dispersion.extend(
                statistics.pstdev(values) for values in by_date.values() if len(values) >= 2
            )
        return {
            "performance_reference_count": len(performance),
            "performance_watch_threshold": _percentile(performance, 0.10),
            "performance_warning_threshold": _percentile(performance, 0.05),
            "predictive_reference_count": len(dispersion),
            "predictive_watch_threshold": _percentile(dispersion, 0.10),
            "predictive_warning_threshold": _percentile(dispersion, 0.05),
        }

    def _publish_product_and_enrollment(
        self,
        *,
        evaluation: QualificationEvaluation,
        qualification_id: uuid.UUID,
        qualification_artifact_id: uuid.UUID,
        monitoring_id: uuid.UUID,
        monitoring_artifact_id: uuid.UUID,
        name: str,
        researcher_id: str,
        selection_reason: str,
        note: str | None,
    ) -> PromotionResult:
        assert evaluation.compiled_strategy_version_id is not None
        assert evaluation.comparison_context_id is not None
        with self._engine.connect() as connection:
            context = (
                connection.execute(
                    text(
                        """
                    SELECT context.benchmark_set_id, benchmark.artifact_id AS benchmark_artifact_id
                    FROM experiment.comparison_context context
                    JOIN experiment.benchmark_set benchmark
                      ON benchmark.benchmark_set_id = context.benchmark_set_id
                    WHERE context.comparison_context_id = :context_id
                    """
                    ),
                    {"context_id": evaluation.comparison_context_id},
                )
                .mappings()
                .one()
            )
            strategy = (
                connection.execute(
                    text(
                        "SELECT artifact_id, strategy_fingerprint "
                        "FROM strategy.compiled_strategy_version "
                        "WHERE compiled_strategy_version_id = :strategy_id"
                    ),
                    {"strategy_id": evaluation.compiled_strategy_version_id},
                )
                .mappings()
                .one()
            )
            suite_policy = connection.execute(
                text(
                    """
                    SELECT policy.artifact_id
                    FROM experiment.research_suite suite
                    JOIN experiment.execution_policy_catalog policy
                      ON policy.execution_policy_catalog_id = suite.execution_policy_catalog_id
                    WHERE suite.artifact_id = :suite_artifact_id
                    """
                ),
                {"suite_artifact_id": evaluation.source_suite_artifact_id},
            ).scalar_one()
        fingerprint = sha256_hexdigest(
            {
                "strategy_fingerprint": strategy["strategy_fingerprint"],
                "qualification_artifact_id": str(qualification_artifact_id),
                "monitoring_policy_artifact_id": str(monitoring_artifact_id),
            }
        )
        product_key = f"candidate__{fingerprint[:24]}"

        def write(connection: Connection, artifact_id: uuid.UUID) -> None:
            version_id = uuid.uuid4()
            connection.execute(
                text("""
                INSERT INTO product.product_version (
                    product_version_id, artifact_id, compiled_strategy_version_id,
                    qualification_bundle_id, monitoring_policy_id, benchmark_set_id,
                    capital_policy_artifact_id, cost_model_artifact_id,
                    product_key, version_number, product_fingerprint
                ) VALUES (:id, :artifact_id, :strategy_id, :qualification_id,
                          :monitoring_id, :benchmark_id, :execution_policy,
                          :execution_policy, :product_key, 1, :fingerprint)
            """),
                {
                    "id": version_id,
                    "artifact_id": artifact_id,
                    "strategy_id": evaluation.compiled_strategy_version_id,
                    "qualification_id": qualification_id,
                    "monitoring_id": monitoring_id,
                    "benchmark_id": context["benchmark_set_id"],
                    "execution_policy": suite_policy,
                    "product_key": product_key,
                    "fingerprint": fingerprint,
                },
            )

        payload = {
            "product_key": product_key,
            "product_fingerprint": fingerprint,
        }
        result = self._artifacts.publish(
            artifact_type="product_version",
            artifact_key=product_key,
            version_number=1,
            semantic_payload={"product_fingerprint": fingerprint},
            content_payload=payload,
            dependencies=(
                DependencyInput(strategy["artifact_id"], "compiled_strategy"),
                DependencyInput(qualification_artifact_id, "qualification_bundle"),
                DependencyInput(monitoring_artifact_id, "monitoring_policy"),
                DependencyInput(context["benchmark_artifact_id"], "benchmark_set"),
                DependencyInput(suite_policy, "execution_policy"),
            ),
            draft_writer=write,
        )
        with self._engine.begin() as connection:
            version_id = connection.execute(
                text(
                    "SELECT product_version_id FROM product.product_version "
                    "WHERE artifact_id = :artifact_id FOR UPDATE"
                ),
                {"artifact_id": result.artifact_id},
            ).scalar_one()
            existing = (
                connection.execute(
                    text("""
                SELECT product_enrollment_id, lifecycle, revision
                FROM product.product_enrollment
                WHERE product_version_id = :version_id
                  AND lifecycle IN ('active','suspended')
                ORDER BY activated_at DESC LIMIT 1 FOR UPDATE
            """),
                    {"version_id": version_id},
                )
                .mappings()
                .one_or_none()
            )
            if existing is not None:
                return PromotionResult(
                    existing["product_enrollment_id"],
                    result.artifact_id,
                    qualification_artifact_id,
                    existing["lifecycle"],
                    existing["revision"],
                )
            enrollment_id = uuid.uuid4()
            now = datetime.now(UTC)
            connection.execute(
                text("""
                INSERT INTO product.product_enrollment (
                    product_enrollment_id, product_version_id, strategy_fingerprint,
                    name, researcher_id, selection_reason, note, activated_at,
                    monitoring_start_at
                ) VALUES (:id, :version_id, :strategy_fingerprint, :name,
                          :researcher, :reason, :note, :now, NULL)
            """),
                {
                    "id": enrollment_id,
                    "version_id": version_id,
                    "strategy_fingerprint": strategy["strategy_fingerprint"],
                    "name": name,
                    "researcher": researcher_id,
                    "reason": selection_reason,
                    "note": note,
                    "now": now,
                },
            )
            connection.execute(
                text("""
                INSERT INTO product.product_lifecycle_event (
                    product_lifecycle_event_id, product_enrollment_id,
                    sequence_number, from_lifecycle, to_lifecycle, reason_code,
                    reason, researcher_id, requested_at, effective_at, applied_at
                ) VALUES (:event_id, :enrollment_id, 1, NULL, 'active',
                          'promoted_from_experiment', :reason, :researcher,
                          :now, :now, :now)
            """),
                {
                    "event_id": uuid.uuid4(),
                    "enrollment_id": enrollment_id,
                    "reason": selection_reason,
                    "researcher": researcher_id,
                    "now": now,
                },
            )
        return PromotionResult(
            enrollment_id, result.artifact_id, qualification_artifact_id, "active", 1
        )


def _qualification_status(
    row: Any,
) -> Literal["accepted", "capacity_rejected", "quality_failed", "failed", "running", "queued"]:
    if row["availability_status"] is None:
        return "queued"
    if row["availability_status"] == "capacity_rejected":
        return "capacity_rejected"
    if row["availability_status"] == "accepted":
        return "accepted"
    return "quality_failed"


def _has_quality_warning(row: Any, check_key: str) -> bool:
    diagnostics = row.get("diagnostics") or {}
    checks = diagnostics.get("quality_checks", []) if isinstance(diagnostics, dict) else []
    return any(
        isinstance(check, dict)
        and check.get("check_key") == check_key
        and check.get("status") == "warning"
        for check in checks
    )


def _percentile(values: list[float], probability: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = probability * (len(ordered) - 1)
    lower = int(index)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = index - lower
    return ordered[lower] * (1 - fraction) + ordered[upper] * fraction


def _exact_selection(anchor: Any) -> dict[str, Any]:
    """Freeze only the selected Product branch, never sibling Model/Strategy choices."""

    normalized = dict(anchor["normalized_selection"] or {})
    asset_ids = [str(value) for value in normalized.get("asset_security_ids", ())]
    raw_asset_inputs = normalized.get("asset_data_inputs")
    if isinstance(raw_asset_inputs, dict):
        asset_inputs = {
            str(security_id): [str(value) for value in input_keys]
            for security_id, input_keys in raw_asset_inputs.items()
            if isinstance(input_keys, list | tuple)
        }
    else:
        # Historical drafts pre-dating per-Asset input selection used canonical
        # market bars implicitly.  Make that old contract explicit in the Product.
        asset_inputs = {
            security_id: ["canonical_market_bars"] for security_id in asset_ids
        }
    rule_graph = dict(anchor["rule_graph"] or {})
    return {
        "schema_version": "product_exact_selection_v1",
        "asset_security_ids": asset_ids,
        "asset_data_inputs": asset_inputs,
        "factor_variant_keys": [
            str(value) for value in normalized.get("factor_variant_keys", ())
        ],
        "signal_version_keys": [
            str(value) for value in normalized.get("signal_version_keys", ())
        ],
        "frequency": str(anchor["schedule_key"]),
        "model": {
            "instance_key": str(anchor["model_instance_key"]),
            "preset_key": str(anchor["model_preset_key"]),
            "target_key": (
                str(anchor["model_target_key"])
                if anchor["model_target_key"] is not None
                else None
            ),
            "slot_assignments": list(anchor["model_slot_assignments"] or ()),
            "parameters": dict(anchor["model_parameters"] or {}),
        },
        "strategy": {
            "branch_key": str(anchor["branch_key"]),
            "family_key": str(anchor["strategy_family_key"]),
            "preset_key": str(anchor["strategy_preset_key"]),
            "rule_graph": rule_graph,
            "parameters": dict(rule_graph.get("parameters") or {}),
        },
    }


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
