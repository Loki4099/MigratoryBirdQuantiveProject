from __future__ import annotations

import uuid
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict, dataclass
from datetime import date
from typing import Any

from sqlalchemy import Engine

from style_rotation.experiment.comparison import (
    ComparisonCohortPublication,
    group_comparable_results,
    publish_comparison_cohort,
    publish_warmup_policy,
)
from style_rotation.experiment.execution import (
    ExperimentExecutionPublication,
    ExperimentExecutionService,
)
from style_rotation.experiment.intervals import IntervalTemplateKey
from style_rotation.experiment.suite_publication import (
    ExperimentCellRequest,
    ExperimentSpecificationPublication,
    publish_experiment_suite,
)
from style_rotation.persistence.session import create_postgres_engine

FORMAL_INTERVALS: tuple[IntervalTemplateKey, ...] = (
    "full_history",
    "trailing_10_years",
    "trailing_5_years",
    "trailing_3_years",
    "trailing_1_year",
)
FORMAL_COSTS_BPS = (2, 5, 10)

_RELEASE_WORKER_SERVICE: ExperimentExecutionService | None = None
_RELEASE_WORKER_ORCHESTRATION_ID: uuid.UUID | None = None


def _initialize_release_worker(database_url: str, orchestration_engine_id: uuid.UUID) -> None:
    global _RELEASE_WORKER_SERVICE, _RELEASE_WORKER_ORCHESTRATION_ID
    _RELEASE_WORKER_SERVICE = ExperimentExecutionService(create_postgres_engine(database_url))
    _RELEASE_WORKER_ORCHESTRATION_ID = orchestration_engine_id


def _execute_release_specification(
    specification_artifact_id: uuid.UUID,
) -> ExperimentExecutionPublication:
    if _RELEASE_WORKER_SERVICE is None or _RELEASE_WORKER_ORCHESTRATION_ID is None:
        raise RuntimeError("Release worker was not initialized")
    return _RELEASE_WORKER_SERVICE.execute(
        specification_artifact_id, _RELEASE_WORKER_ORCHESTRATION_ID
    )


def _interleave_specifications_by_target(
    cells: tuple[ExperimentCellRequest, ...],
    specifications: tuple[ExperimentSpecificationPublication, ...],
) -> tuple[ExperimentSpecificationPublication, ...]:
    if len(cells) != len(specifications):
        raise ValueError("Release cells and specifications must have matching lengths")
    groups: dict[uuid.UUID, list[ExperimentSpecificationPublication]] = {}
    for cell, specification in zip(cells, specifications, strict=True):
        groups.setdefault(cell.strategy_target_artifact_id, []).append(specification)
    longest_group = max((len(group) for group in groups.values()), default=0)
    return tuple(
        group[ordinal]
        for ordinal in range(longest_group)
        for group in groups.values()
        if ordinal < len(group)
    )


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
    publish_cohorts: bool = True,
    max_workers: int = 1,
) -> ReleaseSuiteResult:
    if not 1 <= max_workers <= 16:
        raise ValueError("Release suite max_workers must be between 1 and 16")
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
    def execute(item: ExperimentSpecificationPublication) -> ExperimentExecutionPublication:
        return service.execute(item.artifact_id, orchestration_engine_artifact_id)

    if max_workers == 1:
        executions = tuple(execute(item) for item in suite.specifications)
    else:
        database_url = engine.url.render_as_string(hide_password=False)
        scheduled_specifications = _interleave_specifications_by_target(
            cells, suite.specifications
        )
        with ProcessPoolExecutor(
            max_workers=max_workers,
            initializer=_initialize_release_worker,
            initargs=(database_url, orchestration_engine_artifact_id),
        ) as executor:
            executions = tuple(
                executor.map(
                    _execute_release_specification,
                    (item.artifact_id for item in scheduled_specifications),
                )
            )
    cohorts: tuple[ComparisonCohortPublication, ...] = ()
    if publish_cohorts:
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
                cohort_key=(
                    f"{suite_key}.k{group.target_k}.{group.frequency}.context."
                    f"{group.context_fingerprint[:16]}"
                ),
                name=(
                    f"v0.2 Formal K={group.target_k} {group.frequency.title()} "
                    "Comparable Product Cohort"
                ),
                description=(
                    "Eligible accepted results sharing one exact published market context, "
                    "target K, and rebalance frequency."
                ),
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
