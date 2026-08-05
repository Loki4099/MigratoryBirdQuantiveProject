from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass
from datetime import date
from typing import Any

from sqlalchemy import Engine

from style_rotation.experiment.comparison import (
    group_comparable_results,
    publish_comparison_cohort,
    publish_warmup_policy,
)
from style_rotation.experiment.execution import ExperimentExecutionService
from style_rotation.experiment.intervals import IntervalTemplateKey
from style_rotation.experiment.suite_publication import (
    ExperimentCellRequest,
    publish_experiment_suite,
)

FORMAL_INTERVALS: tuple[IntervalTemplateKey, ...] = (
    "full_history",
    "trailing_10_years",
    "trailing_5_years",
    "trailing_3_years",
    "trailing_1_year",
)
FORMAL_COSTS_BPS = (2, 5, 10)


@dataclass(frozen=True, slots=True)
class ReleaseSuiteResult:
    suite_artifact_id: uuid.UUID
    specification_count: int
    accepted_count: int
    eligible_count: int
    excluded_count: int
    cohort_count: int
    cohort_member_count: int

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["suite_artifact_id"] = str(self.suite_artifact_id)
        return payload


def build_release_cells(
    *,
    target_path_artifact_ids: tuple[uuid.UUID, ...],
    benchmark_version_artifact_id: uuid.UUID,
    cost_scenario_artifacts: dict[int, uuid.UUID],
    metric_catalog_artifact_id: uuid.UUID,
    accounting_engine_artifact_id: uuid.UUID,
    benchmark_engine_artifact_id: uuid.UUID,
    performance_engine_artifact_id: uuid.UUID,
    as_of_date: date,
    intervals: tuple[IntervalTemplateKey, ...] = FORMAL_INTERVALS,
    costs_bps: tuple[int, ...] = FORMAL_COSTS_BPS,
) -> tuple[ExperimentCellRequest, ...]:
    targets = tuple(dict.fromkeys(target_path_artifact_ids))
    if not targets:
        raise ValueError("Release suite requires at least one Strategy Target Path")
    if not intervals or len(set(intervals)) != len(intervals):
        raise ValueError("Release intervals must be non-empty and unique")
    if not costs_bps or len(set(costs_bps)) != len(costs_bps):
        raise ValueError("Release costs must be non-empty and unique")
    unsupported = set(costs_bps) - set(FORMAL_COSTS_BPS)
    missing = set(costs_bps) - set(cost_scenario_artifacts)
    if unsupported or missing:
        raise ValueError("Release suite costs must resolve to published 2/5/10 bps scenarios")
    cells: list[ExperimentCellRequest] = []
    for target in targets:
        target_key = target.hex[:16]
        for cost_bps in costs_bps:
            for interval in intervals:
                cells.append(
                    ExperimentCellRequest(
                        cell_key=f"target_{target_key}.{cost_bps}bps.{interval}",
                        strategy_target_artifact_id=target,
                        benchmark_version_artifact_id=benchmark_version_artifact_id,
                        cost_scenario_artifact_id=cost_scenario_artifacts[cost_bps],
                        metric_catalog_artifact_id=metric_catalog_artifact_id,
                        accounting_engine_artifact_id=accounting_engine_artifact_id,
                        benchmark_engine_artifact_id=benchmark_engine_artifact_id,
                        performance_engine_artifact_id=performance_engine_artifact_id,
                        template_key=interval,
                        as_of_date=as_of_date,
                    )
                )
    return tuple(cells)


def run_release_suite(
    engine: Engine,
    *,
    suite_key: str,
    cells: tuple[ExperimentCellRequest, ...],
    orchestration_engine_artifact_id: uuid.UUID,
    required_warmup_observations: int,
    version_number: int = 1,
) -> ReleaseSuiteResult:
    suite = publish_experiment_suite(
        engine,
        suite_key=suite_key,
        name="v0.2 Formal Release Experiment Suite",
        description=(
            "Formal SPY-benchmarked matrix across published Strategy Targets, costs, and "
            "carry-in interval templates."
        ),
        cells=cells,
        version_number=version_number,
    )
    service = ExperimentExecutionService(engine)
    executions = tuple(
        service.execute(item.artifact_id, orchestration_engine_artifact_id)
        for item in suite.specifications
    )
    warmup = publish_warmup_policy(
        engine,
        required_observations=required_warmup_observations,
        version_number=version_number,
    )
    groups = group_comparable_results(
        engine, tuple(item.result_artifact_id for item in executions)
    )
    cohorts = tuple(
        publish_comparison_cohort(
            engine,
            cohort_key=f"{suite_key}.context.{group.context_fingerprint[:16]}",
            name="v0.2 Formal Comparable Product Cohort",
            description="Eligible accepted results sharing one exact published market context.",
            warmup_policy_artifact_id=warmup.artifact_id,
            result_artifact_ids=group.result_artifact_ids,
        )
        for group in groups
    )
    eligible_count = sum(item.availability_status == "eligible" for item in executions)
    return ReleaseSuiteResult(
        suite.artifact_id,
        suite.specification_count,
        len(executions),
        eligible_count,
        len(executions) - eligible_count,
        len(cohorts),
        sum(item.member_count for item in cohorts),
    )
