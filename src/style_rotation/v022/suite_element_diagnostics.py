from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Literal, cast

from sqlalchemy import Engine, bindparam, text
from sqlalchemy.engine import RowMapping

from style_rotation.data.forward_return_calculator import (
    ForwardOpen,
    ForwardReturnPoint,
    calculate_forward_returns,
)
from style_rotation.data.forward_return_contracts import ForwardReturnSeed
from style_rotation.v022.aggregation_work_runtime import (
    SignalManifestPoint,
    VerifiedSignalManifestReader,
)
from style_rotation.v022.element_diagnostic_publication import (
    ElementDiagnosticPublication,
    ElementDiagnosticPublicationService,
)
from style_rotation.v022.element_diagnostics import calculate_element_diagnostic
from style_rotation.v022.payload_runtime import LocalPayloadObjectStore
from style_rotation.v022.representative_pipeline_runtime import (
    PublishedFeatureManifest,
    read_intermediate_numeric_manifest,
)


@dataclass(frozen=True, slots=True)
class SuiteElementDiagnosticPublication:
    research_suite_id: uuid.UUID
    publications: tuple[ElementDiagnosticPublication, ...]


class SuiteElementDiagnosticService:
    """Calculate and freeze diagnostics for every materialized lineage element."""

    def __init__(self, engine: Engine, *, object_root: Path) -> None:
        self._engine = engine
        self._reader = VerifiedSignalManifestReader(engine, object_root.resolve())
        self._object_store = LocalPayloadObjectStore(object_root.resolve())
        self._publisher = ElementDiagnosticPublicationService(engine)

    def publish(self, research_suite_id: uuid.UUID) -> SuiteElementDiagnosticPublication:
        rows = self._rows(research_suite_id)
        if not rows:
            raise ValueError("Completed v0.22 Suite has no direct element inputs")
        publications: list[ElementDiagnosticPublication] = []
        return_cache: dict[tuple[object, ...], tuple[ForwardReturnPoint, ...]] = {}
        signal_cache: dict[uuid.UUID, tuple[SignalManifestPoint, ...]] = {}
        mask_cache: dict[
            tuple[object, ...], dict[date, frozenset[uuid.UUID]]
        ] = {}
        for row in rows:
            members = _asset_members(row["asset_context_document"])
            candidate_ids = frozenset(members)
            mask_key = (
                row["evaluation_cohort_version_id"],
                row["effective_start"],
                row["effective_end"],
                tuple(sorted(candidate_ids, key=str)),
            )
            candidate_mask = mask_cache.get(mask_key)
            if candidate_mask is None:
                candidate_mask = self._candidate_mask(
                    evaluation_cohort_version_id=row["evaluation_cohort_version_id"],
                    effective_start=row["effective_start"],
                    effective_end=row["effective_end"],
                    candidate_asset_ids=candidate_ids,
                )
                mask_cache[mask_key] = candidate_mask
            signal_points = signal_cache.get(row["payload_manifest_id"])
            if signal_points is None:
                signal_points = self._signal_points(
                    row, members, decision_dates=frozenset(candidate_mask)
                )
                signal_cache[row["payload_manifest_id"]] = signal_points
            target = _target(row)
            cache_key = (
                row["market_dataset_publication_id"],
                row["calendar_version_id"],
                row["frequency"],
                row["effective_start"],
                row["effective_end"],
                row["evaluation_cohort_version_id"],
                tuple(sorted(candidate_ids, key=str)),
            )
            forward_returns = return_cache.get(cache_key)
            if forward_returns is None:
                sessions, opens = self._market_inputs(
                    market_dataset_publication_id=row["market_dataset_publication_id"],
                    calendar_version_id=row["calendar_version_id"],
                    members=members,
                    decision_dates=frozenset(candidate_mask),
                    execution_lag_sessions=target.execution_lag_sessions,
                )
                forward_returns = calculate_forward_returns(
                    target,
                    sessions,
                    opens,
                    requested_start=row["effective_start"],
                    requested_end=row["effective_end"],
                    candidate_asset_ids_by_date=candidate_mask,
                    allow_missing_eligible_opens=True,
                ).points
                return_cache[cache_key] = forward_returns
            diagnostic = calculate_element_diagnostic(
                compiled_feature_occurrence_id=row["compiled_feature_occurrence_id"],
                feature_variant_key=row["feature_variant_key"],
                stage_no=row["stage_no"],
                payload_manifest_id=row["payload_manifest_id"],
                manifest_artifact_id=row["payload_manifest_artifact_id"],
                manifest_hash=row["manifest_hash"],
                research_direction=_research_direction(row["direction"]),
                target_key=target.key,
                target_version_id=row["target_version_id"],
                target_version_artifact_id=row["target_version_artifact_id"],
                frequency=cast(Literal["weekly", "monthly"], row["frequency"]),
                signal_points=signal_points,
                forward_returns=forward_returns,
                candidate_asset_ids=candidate_ids,
                candidate_asset_ids_by_date=candidate_mask,
                allow_missing_forward_returns=True,
            )
            publications.append(
                self._publisher.publish(
                    diagnostic,
                    result_artifact_id=row["result_artifact_id"],
                    configuration_snapshot_id=row["configuration_snapshot_id"],
                    market_dataset_publication_id=row["market_dataset_publication_id"],
                    market_dataset_artifact_id=row["market_dataset_artifact_id"],
                    calendar_version_id=row["calendar_version_id"],
                    calendar_artifact_id=row["calendar_artifact_id"],
                )
            )
        return SuiteElementDiagnosticPublication(research_suite_id, tuple(publications))

    def _signal_points(
        self,
        row: RowMapping,
        members: dict[uuid.UUID, str],
        *,
        decision_dates: frozenset[date],
    ) -> tuple[SignalManifestPoint, ...]:
        if row["payload_contract_key"] == "final_signal_numeric":
            return self._reader.read(
                payload_manifest_id=row["payload_manifest_id"],
                expected_manifest_hash=row["manifest_hash"],
                expected_artifact_id=row["payload_manifest_artifact_id"],
                catalog_release_id=row["catalog_release_id"],
                allowed_asset_keys=members,
                decision_dates=decision_dates,
            )
        if row["payload_contract_key"] != "intermediate_numeric_feature":
            raise ValueError("Element Diagnostic payload contract is unsupported")
        points = read_intermediate_numeric_manifest(
            self._engine,
            object_store=self._object_store,
            manifest=PublishedFeatureManifest(
                row["feature_variant_key"],
                row["payload_manifest_id"],
                row["payload_manifest_artifact_id"],
                row["manifest_hash"],
            ),
            asset_keys=members,
            session_dates=decision_dates,
        )
        return tuple(
            SignalManifestPoint(
                item.asset_id,
                item.asset_key,
                item.session_date,
                item.value,
                item.known_at,
                item.input_revision,
                item.missing_reason,
            )
            for item in points
        )

    def _rows(self, research_suite_id: uuid.UUID) -> list[RowMapping]:
        with self._engine.connect() as connection:
            rows = connection.execute(
                text(
                    """
                    WITH RECURSIVE selected_run AS (
                      SELECT binding.graph_run_id
                        FROM experiment.v022_research_suite_graph_run_binding binding
                       WHERE binding.research_suite_id=:suite
                       ORDER BY binding.binding_ordinal DESC
                       LIMIT 1
                    ), direct_manifest AS (
                      SELECT result.artifact_id AS result_artifact_id,
                             result.configuration_snapshot_id,
                             input.payload_manifest_id
                        FROM selected_run selected
                        JOIN experiment.v022_suite_runtime_plan plan
                          ON plan.graph_run_id=selected.graph_run_id
                        JOIN experiment.v022_portfolio_cell_work_spec cell_spec
                          ON cell_spec.suite_runtime_plan_id=plan.suite_runtime_plan_id
                        JOIN experiment.v022_portfolio_cell_runtime_result result
                          ON result.graph_work_item_id=cell_spec.graph_work_item_id
                        JOIN strategy.v022_strategy_target_work_spec strategy_spec
                          ON strategy_spec.suite_runtime_plan_id=plan.suite_runtime_plan_id
                         AND strategy_spec.research_suite_branch_id=
                             cell_spec.research_suite_branch_id
                        JOIN aggregation.graph_run_aggregation_binding aggregation_binding
                          ON aggregation_binding.graph_run_id=selected.graph_run_id
                         AND aggregation_binding.graph_work_item_id=
                             strategy_spec.source_aggregation_work_item_id
                        JOIN aggregation.aggregation_run_input input
                          ON input.aggregation_run_id=aggregation_binding.aggregation_run_id
                    ), manifest_lineage AS (
                      SELECT result_artifact_id,configuration_snapshot_id,
                             payload_manifest_id
                        FROM direct_manifest
                      UNION
                      SELECT lineage.result_artifact_id,
                             lineage.configuration_snapshot_id,
                             node_input.payload_manifest_id
                        FROM manifest_lineage lineage
                        JOIN processing.node_run_output node_output
                          ON node_output.payload_manifest_id=lineage.payload_manifest_id
                        JOIN processing.node_run_input node_input
                          ON node_input.node_run_id=node_output.node_run_id
                    )
                    SELECT DISTINCT lineage.result_artifact_id,
                           lineage.configuration_snapshot_id,
                           cohort_binding.evaluation_cohort_version_id,
                           result.effective_start,result.effective_end,
                           graph.frequency,graph.catalog_release_id,
                           occurrence.compiled_feature_occurrence_id,
                           occurrence.stage_no,variant.variant_key AS feature_variant_key,
                           family.direction,lineage.payload_manifest_id,
                           manifest.manifest_hash,
                           manifest.artifact_id AS payload_manifest_artifact_id,
                           contract_family.contract_key AS payload_contract_key,
                           context.asset_context_document,
                           context_input.dataset_publication_id AS market_dataset_publication_id,
                           context_input.dataset_artifact_id AS market_dataset_artifact_id,
                           context_input.calendar_version_id,
                           context_input.calendar_artifact_id,
                           target.forward_return_version_id AS target_version_id,
                           target.artifact_id AS target_version_artifact_id,
                           definition.target_key,target.version_number AS target_version_number,
                           target.frequency AS target_frequency,target.decision_rule,
                           target.decision_time,target.execution_policy,target.start_price,
                           target.end_price,target.execution_lag_sessions,
                           target.overlap_policy,target.calendar_key,target.included_member_roles
                       FROM experiment.v022_research_suite suite
                       JOIN experiment.v022_research_suite_evaluation_cohort_binding
                         cohort_binding
                         ON cohort_binding.research_suite_id=suite.research_suite_id
                      JOIN selected_run selected ON true
                      JOIN experiment.v022_suite_runtime_plan plan
                        ON plan.graph_run_id=selected.graph_run_id
                      JOIN workspace.v022_graph_run run
                        ON run.graph_run_id=selected.graph_run_id AND run.status='completed'
                      JOIN manifest_lineage lineage ON true
                      JOIN experiment.v022_portfolio_cell_runtime_result result
                        ON result.artifact_id=lineage.result_artifact_id
                       AND result.configuration_snapshot_id=
                           lineage.configuration_snapshot_id
                      JOIN processing.node_run_output node_output
                        ON node_output.payload_manifest_id=lineage.payload_manifest_id
                      JOIN processing.node_run node_run
                        ON node_run.node_run_id=node_output.node_run_id
                       AND node_run.status='completed'
                      JOIN processing.graph_run_node_binding node_binding
                        ON node_binding.node_run_id=node_run.node_run_id
                      JOIN workspace.compiled_feature_occurrence occurrence
                        ON occurrence.compiled_graph_node_id=
                           node_binding.compiled_graph_node_id
                       AND occurrence.output_port_key=node_output.output_port_key
                       AND occurrence.compiled_research_graph_id=
                           suite.compiled_research_graph_id
                      JOIN processing.feature_version feature
                        ON feature.feature_version_id=occurrence.feature_version_id
                      JOIN processing.feature_variant variant
                        ON variant.feature_variant_id=feature.feature_variant_id
                      JOIN processing.feature_family family
                        ON family.feature_family_id=variant.feature_family_id
                      JOIN data.payload_manifest manifest
                        ON manifest.payload_manifest_id=lineage.payload_manifest_id
                      JOIN data.payload_contract_version contract_version
                        ON contract_version.payload_contract_version_id=
                           manifest.payload_contract_version_id
                      JOIN data.payload_contract_family contract_family
                        ON contract_family.payload_contract_family_id=
                           contract_version.payload_contract_family_id
                      JOIN workspace.v022_compiled_execution_data_context context
                        ON context.compiled_execution_data_context_id=
                           plan.compiled_execution_data_context_id
                      JOIN workspace.v022_compiled_execution_data_input context_input
                        ON context_input.compiled_execution_data_context_id=
                           context.compiled_execution_data_context_id
                       AND context_input.input_key='canonical_market_bars'
                      JOIN workspace.compiled_research_graph graph
                        ON graph.compiled_research_graph_id=
                           suite.compiled_research_graph_id
                      JOIN data.forward_return_definition definition
                        ON definition.target_key=CASE graph.frequency
                          WHEN 'weekly' THEN 'weekly_next_open_to_next_open'
                          ELSE 'monthly_next_open_to_next_open' END
                      JOIN data.forward_return_version target
                        ON target.forward_return_definition_id=
                           definition.forward_return_definition_id
                       AND target.version_number=1
                      JOIN lineage.artifact target_artifact
                        ON target_artifact.artifact_id=target.artifact_id
                       AND target_artifact.status='published'
                     WHERE suite.research_suite_id=:suite
                       AND occurrence.stage_no BETWEEN 1 AND 3
                     ORDER BY lineage.result_artifact_id,occurrence.stage_no,
                              variant.variant_key,
                              occurrence.compiled_feature_occurrence_id
                    """
                ),
                {"suite": research_suite_id},
            ).mappings().all()
        return list(rows)

    def _candidate_mask(
        self,
        *,
        evaluation_cohort_version_id: uuid.UUID,
        effective_start: date,
        effective_end: date,
        candidate_asset_ids: frozenset[uuid.UUID],
    ) -> dict[date, frozenset[uuid.UUID]]:
        with self._engine.connect() as connection:
            rows = connection.execute(
                text(
                    """
                    SELECT session.session_date,eligibility.security_id
                      FROM experiment.v022_evaluation_cohort_session session
                      JOIN experiment.v022_cohort_eligibility_interval eligibility
                        ON eligibility.evaluation_cohort_version_id=
                           session.evaluation_cohort_version_id
                       AND session.session_date BETWEEN
                           eligibility.effective_start AND eligibility.effective_end
                     WHERE session.evaluation_cohort_version_id=:cohort
                       AND session.session_role='evaluation'
                       AND session.is_decision_session
                       AND eligibility.is_selectable
                       AND eligibility.security_id IN :candidate_asset_ids
                       AND session.session_date BETWEEN :effective_start AND :effective_end
                     ORDER BY session.session_date,eligibility.security_id
                    """
                ).bindparams(bindparam("candidate_asset_ids", expanding=True)),
                {
                    "cohort": evaluation_cohort_version_id,
                    "effective_start": effective_start,
                    "effective_end": effective_end,
                    "candidate_asset_ids": tuple(candidate_asset_ids),
                },
            ).mappings().all()
        return _candidate_mask_from_rows(
            cast(Sequence[Mapping[str, object]], rows), candidate_asset_ids
        )

    def _market_inputs(
        self,
        *,
        market_dataset_publication_id: uuid.UUID,
        calendar_version_id: uuid.UUID,
        members: dict[uuid.UUID, str],
        decision_dates: frozenset[date],
        execution_lag_sessions: int,
    ) -> tuple[tuple[date, ...], tuple[ForwardOpen, ...]]:
        with self._engine.connect() as connection:
            sessions = tuple(
                connection.scalars(
                    text(
                        "SELECT session_date FROM catalog.calendar_session "
                        "WHERE calendar_version_id=:calendar ORDER BY session_date"
                    ),
                    {"calendar": calendar_version_id},
                ).all()
            )
            session_index = {session: index for index, session in enumerate(sessions)}
            required_open_dates: set[date] = set()
            for decision_date in decision_dates:
                index = session_index.get(decision_date)
                if index is None or index + execution_lag_sessions >= len(sessions):
                    raise ValueError(
                        "Evaluation Cohort decision date lacks its execution session"
                    )
                required_open_dates.add(sessions[index + execution_lag_sessions])
            security_rows = connection.execute(
                text(
                    "SELECT security_id,legacy_asset_id FROM catalog.security "
                    "WHERE security_id IN :security_ids"
                ).bindparams(bindparam("security_ids", expanding=True)),
                {"security_ids": tuple(members)},
            ).mappings().all()
            legacy_by_security = {
                row["security_id"]: row["legacy_asset_id"] for row in security_rows
            }
            if set(legacy_by_security) != set(members) or any(
                item is None for item in legacy_by_security.values()
            ):
                raise ValueError("Element Diagnostic candidates lack market asset identities")
            security_by_legacy = {
                legacy: security for security, legacy in legacy_by_security.items()
            }
            bars = connection.execute(
                text(
                    "SELECT asset_id,session_date,open_adj FROM data.daily_bar "
                    "WHERE dataset_publication_id=:dataset AND asset_id IN :asset_ids "
                    "AND session_date IN :session_dates "
                    "ORDER BY asset_id,session_date"
                ).bindparams(
                    bindparam("asset_ids", expanding=True),
                    bindparam("session_dates", expanding=True),
                ),
                {
                    "dataset": market_dataset_publication_id,
                    "asset_ids": tuple(security_by_legacy),
                    "session_dates": tuple(sorted(required_open_dates)),
                },
            ).mappings().all()
        opens = tuple(
            ForwardOpen(
                security_by_legacy[row["asset_id"]],
                members[security_by_legacy[row["asset_id"]]],
                row["session_date"],
                row["open_adj"],
            )
            for row in bars
        )
        return cast(tuple[date, ...], sessions), opens


def _asset_members(value: object) -> dict[uuid.UUID, str]:
    if not isinstance(value, dict) or not isinstance(value.get("members"), list):
        raise ValueError("Element Diagnostic Asset Context has no members")
    result: dict[uuid.UUID, str] = {}
    for raw in value["members"]:
        if not isinstance(raw, dict):
            raise ValueError("Element Diagnostic Asset Context member is malformed")
        asset_id = uuid.UUID(str(raw["security_id"]))
        asset_key = str(raw["security_key"])
        if asset_id in result or not asset_key.strip():
            raise ValueError("Element Diagnostic Asset Context member is duplicated")
        result[asset_id] = asset_key
    if not result:
        raise ValueError("Element Diagnostic Asset Context is empty")
    return result


def _candidate_mask_from_rows(
    rows: Sequence[Mapping[str, object]],
    candidate_asset_ids: frozenset[uuid.UUID],
) -> dict[date, frozenset[uuid.UUID]]:
    grouped: dict[date, set[uuid.UUID]] = {}
    for row in rows:
        session_date = cast(date, row["session_date"])
        security_id = cast(uuid.UUID, row["security_id"])
        if security_id not in candidate_asset_ids:
            raise ValueError("Evaluation Cohort contains an unfrozen candidate")
        assets = grouped.setdefault(session_date, set())
        if security_id in assets:
            raise ValueError("Evaluation Cohort contains a duplicate selectable candidate")
        assets.add(security_id)
    if not grouped:
        raise ValueError("Evaluation Cohort has no selectable decision observations")
    return {day: frozenset(assets) for day, assets in grouped.items()}


def _target(row: RowMapping) -> ForwardReturnSeed:
    target = ForwardReturnSeed(
        key=row["target_key"],
        version_number=row["target_version_number"],
        frequency=row["target_frequency"],
        decision_rule=row["decision_rule"],
        decision_time=row["decision_time"],
        execution_policy=row["execution_policy"],
        start_price=row["start_price"],
        end_price=row["end_price"],
        execution_lag_sessions=row["execution_lag_sessions"],
        overlap_policy=row["overlap_policy"],
        calendar_key=row["calendar_key"],
        included_member_roles=row["included_member_roles"],
    )
    if target.frequency != row["frequency"]:
        raise ValueError("Suite frequency and Evaluation Target differ")
    return target


def _research_direction(
    value: str,
) -> Literal["positive", "negative", "unsigned"]:
    if value in {"higher_is_better", "higher_is_bullish"}:
        return "positive"
    if value in {"lower_is_better", "higher_is_bearish"}:
        return "negative"
    if value == "not_applicable":
        return "unsigned"
    raise ValueError(f"Direct Element has no predictive research direction: {value}")
