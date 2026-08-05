from __future__ import annotations

import re
import uuid
from contextlib import nullcontext
from dataclasses import dataclass
from functools import partial
from typing import Any, cast

from sqlalchemy import Engine, bindparam, text
from sqlalchemy.engine import Connection, RowMapping

from style_rotation.core.canonical import sha256_hexdigest
from style_rotation.lineage.service import ArtifactService, DependencyInput


@dataclass(frozen=True, slots=True)
class WarmupPolicyPublication:
    artifact_id: uuid.UUID
    required_observations: int
    reused: bool


@dataclass(frozen=True, slots=True)
class ComparisonCohortPublication:
    artifact_id: uuid.UUID
    cohort_key: str
    version_number: int
    context_fingerprint: str
    member_count: int
    reused: bool


@dataclass(frozen=True, slots=True)
class ComparableResultGroup:
    context_fingerprint: str
    target_k: int
    frequency: str
    result_artifact_ids: tuple[uuid.UUID, ...]


def group_comparable_results(
    engine: Engine, result_artifact_ids: tuple[uuid.UUID, ...]
) -> tuple[ComparableResultGroup, ...]:
    """Partition eligible accepted results by the exact cohort market context.

    Excluded interval results deliberately remain part of their Experiment Suite but cannot
    enter a product ranking cohort.
    """
    ordered = tuple(sorted(set(result_artifact_ids), key=str))
    if not ordered:
        return ()
    with engine.connect() as connection:
        contexts = _load_result_contexts(connection, ordered)
    grouped: dict[tuple[str, int, str], list[uuid.UUID]] = {}
    for row in contexts:
        fingerprint = sha256_hexdigest(_context_payload(row))
        key = (fingerprint, int(row["target_k"]), str(row["frequency"]))
        grouped.setdefault(key, []).append(row["result_artifact_id"])
    return tuple(
        ComparableResultGroup(fingerprint, target_k, frequency, tuple(result_ids))
        for (fingerprint, target_k, frequency), result_ids in sorted(grouped.items())
    )


def publish_warmup_policy(
    engine: Engine,
    *,
    required_observations: int,
    version_number: int = 1,
) -> WarmupPolicyPublication:
    if required_observations < 0:
        raise ValueError("Warm-up observations cannot be negative")
    policy_key = "dependency_max_required_history"
    payload = {
        "policy_key": policy_key,
        "version_number": version_number,
        "resolution_method": "dependency_max_required_history",
        "required_observations": required_observations,
    }
    result = ArtifactService(engine).publish(
        artifact_type="warmup_policy_version",
        artifact_key=policy_key,
        version_number=version_number,
        semantic_payload=payload,
        content_payload={
            **payload,
            "description": "Use the longest declared upstream history requirement.",
        },
        reason=f"publish comparison warm-up policy v{version_number}",
        draft_writer=partial(
            _write_warmup_policy,
            policy_key=policy_key,
            version_number=version_number,
            required_observations=required_observations,
        ),
    )
    return WarmupPolicyPublication(result.artifact_id, required_observations, result.reused)


def publish_comparison_cohort(
    engine: Engine,
    *,
    cohort_key: str,
    name: str,
    description: str,
    warmup_policy_artifact_id: uuid.UUID,
    result_artifact_ids: tuple[uuid.UUID, ...],
) -> ComparisonCohortPublication:
    if not re.fullmatch(r"[a-z0-9][a-z0-9_.:-]{0,139}", cohort_key):
        raise ValueError("Comparison cohort key must be a stable lowercase identifier")
    if not name.strip() or not description.strip():
        raise ValueError("Comparison cohort requires name and description")
    ordered_results = tuple(sorted(set(result_artifact_ids), key=str))
    if not ordered_results:
        raise ValueError("Comparison cohort requires at least one accepted result")
    with engine.begin() as connection:
        warmup = _published_warmup(connection, warmup_policy_artifact_id)
        contexts = _load_result_contexts(connection, ordered_results)
        if len(contexts) != len(ordered_results):
            raise ValueError("Every comparison member must be a published eligible result")
        context_payloads = tuple(_context_payload(row) for row in contexts)
        if any(payload != context_payloads[0] for payload in context_payloads[1:]):
            raise ValueError("Comparison members do not share one strict market context")
        context = contexts[0]
        context_payload = {
            **context_payloads[0],
            "warmup_policy_artifact_id": str(warmup_policy_artifact_id),
            "required_observations": warmup["required_observations"],
        }
        context_fingerprint = sha256_hexdigest(context_payload)
        version_number = _resolve_version(connection, cohort_key, ordered_results)
        semantic = {
            "cohort_key": cohort_key,
            "version_number": version_number,
            "context": context_payload,
            "result_artifact_ids": tuple(str(item) for item in ordered_results),
        }
        service = ArtifactService(cast(Engine, _BoundConnection(connection)))
        result = service.publish(
            artifact_type="comparison_cohort_version",
            artifact_key=cohort_key,
            version_number=version_number,
            semantic_payload=semantic,
            content_payload={**semantic, "name": name.strip(), "description": description.strip()},
            dependencies=(
                DependencyInput(warmup_policy_artifact_id, "warmup_policy", 0),
                *(DependencyInput(item, "accepted_result", ordinal + 1)
                  for ordinal, item in enumerate(ordered_results)),
            ),
            reason=f"publish comparison cohort {cohort_key} v{version_number}",
            draft_writer=partial(
                _write_cohort,
                warmup=warmup,
                context=context,
                cohort_key=cohort_key,
                version_number=version_number,
                name=name.strip(),
                description=description.strip(),
                context_fingerprint=context_fingerprint,
                result_ids=tuple(row["result_publication_id"] for row in contexts),
            ),
        )
    return ComparisonCohortPublication(
        result.artifact_id, cohort_key, version_number, context_fingerprint,
        len(ordered_results), result.reused,
    )


def _published_warmup(connection: Connection, artifact_id: uuid.UUID) -> RowMapping:
    row = connection.execute(text("""
        SELECT policy.* FROM experiment.warmup_policy_version policy
        JOIN lineage.artifact artifact ON artifact.artifact_id = policy.artifact_id
                                    AND artifact.status = 'published'
        WHERE policy.artifact_id = :artifact
    """), {"artifact": artifact_id}).mappings().one_or_none()
    if row is None:
        raise ValueError("Published warm-up policy not found")
    return row


def _load_result_contexts(
    connection: Connection, artifact_ids: tuple[uuid.UUID, ...]
) -> list[RowMapping]:
    return list(connection.execute(text("""
        SELECT publication.result_publication_id,
               publication.artifact_id AS result_artifact_id,
               target.universe_version_id, universe.artifact_id AS universe_artifact_id,
               target.data_bundle_version_id, bundle.artifact_id AS data_bundle_artifact_id,
               target.eligibility_snapshot_id,
               eligibility.artifact_id AS eligibility_artifact_id,
               strategy_gross.execution_policy_version_id,
               execution.artifact_id AS execution_policy_artifact_id,
               strategy_gross.reserve_return_model_version_id,
               reserve.artifact_id AS reserve_model_artifact_id,
               specification.benchmark_version_id,
               benchmark.artifact_id AS benchmark_artifact_id,
               specification.cost_scenario_id, cost.artifact_id AS cost_artifact_id,
               specification.performance_metric_catalog_id,
               metric.artifact_id AS metric_catalog_artifact_id,
               specification.accounting_engine_version_id,
               accounting.artifact_id AS accounting_engine_artifact_id,
               specification.benchmark_engine_version_id,
               benchmark_engine.artifact_id AS benchmark_engine_artifact_id,
               specification.performance_engine_version_id,
               performance.artifact_id AS performance_engine_artifact_id,
               specification.template_key, specification.initialization_policy,
               specification.as_of_date,
               variant.target_k, schedule.frequency,
               ready.common_data_ready_date,
               strategy_gross.effective_nav_start AS common_simulation_start,
               interval.resolved_start AS common_metric_start,
               interval.resolved_end AS common_metric_end
        FROM experiment.result_publication publication
        JOIN lineage.artifact result_artifact ON
             result_artifact.artifact_id = publication.artifact_id
                                             AND result_artifact.status = 'published'
        JOIN experiment.experiment_specification specification ON
             specification.experiment_specification_id = publication.experiment_specification_id
        JOIN experiment.interval_performance_result interval ON
             interval.interval_performance_result_id = publication.interval_performance_result_id
        JOIN experiment.net_cost_path strategy_net ON strategy_net.net_cost_path_id =
                                                     interval.strategy_net_cost_path_id
        JOIN experiment.gross_portfolio_path strategy_gross ON
             strategy_gross.gross_portfolio_path_id = strategy_net.gross_portfolio_path_id
        JOIN strategy.portfolio_target_path target ON target.portfolio_target_path_id =
                                                      strategy_gross.portfolio_target_path_id
        JOIN strategy.model_strategy_target_path model_path ON
             model_path.portfolio_target_path_id = target.portfolio_target_path_id
        JOIN strategy.strategy_product_version product ON
             product.strategy_product_version_id = model_path.strategy_product_version_id
        JOIN strategy.strategy_variant variant ON
             variant.strategy_variant_id = product.strategy_variant_id
        JOIN ops.rebalance_schedule_version schedule ON
             schedule.rebalance_schedule_version_id = product.rebalance_schedule_version_id
        JOIN catalog.universe_version universe ON
             universe.universe_version_id = target.universe_version_id
        JOIN data.data_bundle_version bundle ON
             bundle.data_bundle_version_id = target.data_bundle_version_id
        JOIN catalog.eligibility_snapshot eligibility ON eligibility.eligibility_snapshot_id =
                                                          target.eligibility_snapshot_id
        JOIN ops.execution_policy_version execution ON execution.execution_policy_version_id =
                                                       strategy_gross.execution_policy_version_id
        JOIN experiment.reserve_return_model_version reserve ON
             reserve.reserve_return_model_version_id =
             strategy_gross.reserve_return_model_version_id
        JOIN experiment.benchmark_version benchmark ON benchmark.benchmark_version_id =
                                                       specification.benchmark_version_id
        JOIN experiment.benchmark_definition benchmark_definition ON
             benchmark_definition.benchmark_definition_id = benchmark.benchmark_definition_id
        JOIN experiment.cost_scenario cost ON cost.cost_scenario_id = specification.cost_scenario_id
        JOIN experiment.performance_metric_catalog metric ON
             metric.performance_metric_catalog_id = specification.performance_metric_catalog_id
        JOIN ops.engine_version accounting ON accounting.engine_version_id =
                                              specification.accounting_engine_version_id
        JOIN ops.engine_version benchmark_engine ON benchmark_engine.engine_version_id =
                                                    specification.benchmark_engine_version_id
        JOIN ops.engine_version performance ON performance.engine_version_id =
                                               specification.performance_engine_version_id
        JOIN LATERAL (
            SELECT max(item.data_ready_date) AS common_data_ready_date
            FROM catalog.eligibility_item item
            WHERE item.eligibility_snapshot_id = target.eligibility_snapshot_id
              AND item.role = 'candidate' AND item.is_eligible
        ) ready ON true
        WHERE publication.artifact_id IN :artifacts
          AND publication.availability_status = 'eligible'
          AND benchmark_definition.category = 'product_primary'
          AND interval.resolved_start IS NOT NULL AND interval.resolved_end IS NOT NULL
        ORDER BY publication.artifact_id
    """).bindparams(bindparam("artifacts", expanding=True)),
        {"artifacts": artifact_ids}).mappings().all())


def _context_payload(row: RowMapping) -> dict[str, Any]:
    return {
        "universe_artifact_id": str(row["universe_artifact_id"]),
        "data_bundle_artifact_id": str(row["data_bundle_artifact_id"]),
        "eligibility_artifact_id": str(row["eligibility_artifact_id"]),
        "execution_policy_artifact_id": str(row["execution_policy_artifact_id"]),
        "reserve_model_artifact_id": str(row["reserve_model_artifact_id"]),
        "benchmark_artifact_id": str(row["benchmark_artifact_id"]),
        "cost_artifact_id": str(row["cost_artifact_id"]),
        "metric_catalog_artifact_id": str(row["metric_catalog_artifact_id"]),
        "accounting_engine_artifact_id": str(row["accounting_engine_artifact_id"]),
        "benchmark_engine_artifact_id": str(row["benchmark_engine_artifact_id"]),
        "performance_engine_artifact_id": str(row["performance_engine_artifact_id"]),
        "template_key": row["template_key"],
        "initialization_policy": row["initialization_policy"],
        "target_k": int(row["target_k"]),
        "frequency": row["frequency"],
        "as_of_date": row["as_of_date"],
        "common_data_ready_date": row["common_data_ready_date"],
        "common_simulation_start": row["common_simulation_start"],
        "common_metric_start": row["common_metric_start"],
        "common_metric_end": row["common_metric_end"],
        "currency": "USD",
    }


def _resolve_version(
    connection: Connection, cohort_key: str, result_ids: tuple[uuid.UUID, ...]
) -> int:
    latest = connection.execute(text("""
        SELECT comparison_cohort_version_id, version_number
        FROM experiment.comparison_cohort_version
        WHERE cohort_key = :key ORDER BY version_number DESC LIMIT 1
    """), {"key": cohort_key}).mappings().one_or_none()
    if latest is None:
        return 1
    members = tuple(connection.execute(text("""
        SELECT publication.artifact_id
        FROM experiment.comparison_cohort_member member
        JOIN experiment.result_publication publication ON publication.result_publication_id =
                                                          member.result_publication_id
        WHERE member.comparison_cohort_version_id = :cohort ORDER BY publication.artifact_id
    """), {"cohort": latest["comparison_cohort_version_id"]}).scalars())
    if members == result_ids:
        return int(latest["version_number"])
    return int(latest["version_number"]) + 1


def _write_warmup_policy(
    connection: Connection, artifact_id: uuid.UUID, *, policy_key: str,
    version_number: int, required_observations: int,
) -> None:
    connection.execute(text("""
        INSERT INTO experiment.warmup_policy_version (
            warmup_policy_version_id, artifact_id, policy_key, version_number,
            resolution_method, required_observations, description
        ) VALUES (:id, :artifact, :key, :version, 'dependency_max_required_history',
                  :observations, :description)
    """), {"id": uuid.uuid4(), "artifact": artifact_id, "key": policy_key,
             "version": version_number, "observations": required_observations,
             "description": "Use the longest declared upstream history requirement."})


def _write_cohort(
    connection: Connection, artifact_id: uuid.UUID, *, warmup: RowMapping,
    context: RowMapping, cohort_key: str, version_number: int, name: str,
    description: str, context_fingerprint: str, result_ids: tuple[uuid.UUID, ...],
) -> None:
    cohort_id = uuid.uuid4()
    connection.execute(text("""
        INSERT INTO experiment.comparison_cohort_version (
            comparison_cohort_version_id, artifact_id, warmup_policy_version_id,
            universe_version_id, data_bundle_version_id, eligibility_snapshot_id,
            execution_policy_version_id, reserve_return_model_version_id,
            benchmark_version_id, cost_scenario_id, performance_metric_catalog_id,
            accounting_engine_version_id, benchmark_engine_version_id,
            performance_engine_version_id, cohort_key, version_number, name, description,
            context_fingerprint, template_key, initialization_policy, as_of_date,
            target_k, frequency,
            common_data_ready_date, common_simulation_start, common_metric_start,
            common_metric_end, currency, member_count
        ) VALUES (
            :id, :artifact, :warmup, :universe, :bundle, :eligibility, :execution,
            :reserve, :benchmark, :cost, :metric, :accounting, :benchmark_engine,
            :performance, :key, :version, :name, :description, :fingerprint,
            :template, :initialization, :as_of, :target_k, :frequency,
            :data_ready, :simulation_start,
            :metric_start, :metric_end, 'USD', :member_count)
    """), {
        "id": cohort_id, "artifact": artifact_id,
        "warmup": warmup["warmup_policy_version_id"],
        "universe": context["universe_version_id"], "bundle": context["data_bundle_version_id"],
        "eligibility": context["eligibility_snapshot_id"],
        "execution": context["execution_policy_version_id"],
        "reserve": context["reserve_return_model_version_id"],
        "benchmark": context["benchmark_version_id"], "cost": context["cost_scenario_id"],
        "metric": context["performance_metric_catalog_id"],
        "accounting": context["accounting_engine_version_id"],
        "benchmark_engine": context["benchmark_engine_version_id"],
        "performance": context["performance_engine_version_id"], "key": cohort_key,
        "version": version_number, "name": name, "description": description,
        "fingerprint": context_fingerprint, "template": context["template_key"],
        "initialization": context["initialization_policy"], "as_of": context["as_of_date"],
        "target_k": context["target_k"], "frequency": context["frequency"],
        "data_ready": context["common_data_ready_date"],
        "simulation_start": context["common_simulation_start"],
        "metric_start": context["common_metric_start"], "metric_end": context["common_metric_end"],
        "member_count": len(result_ids),
    })
    connection.execute(text("""
        INSERT INTO experiment.comparison_cohort_member (
            comparison_cohort_version_id, result_publication_id, ordinal
        ) VALUES (:cohort, :result, :ordinal)
    """), [{"cohort": cohort_id, "result": result_id, "ordinal": ordinal}
             for ordinal, result_id in enumerate(result_ids)])


class _BoundConnection:
    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def begin(self) -> nullcontext[Connection]:
        return nullcontext(self._connection)
