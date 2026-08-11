import uuid
from datetime import date

import pytest

from style_rotation.experiment.release import (
    FORMAL_INTERVALS,
    _interleave_specifications_by_target,
    build_release_cells,
    run_release_suite,
)
from style_rotation.experiment.suite_publication import ExperimentSpecificationPublication


def test_release_cells_expand_targets_costs_and_all_five_intervals() -> None:
    targets = (uuid.uuid4(), uuid.uuid4())
    benchmark = uuid.uuid4()
    costs = {2: uuid.uuid4(), 5: uuid.uuid4(), 10: uuid.uuid4()}
    cells = build_release_cells(
        target_path_artifact_ids=targets,
        benchmark_version_artifact_id=benchmark,
        cost_scenario_artifacts=costs,
        metric_catalog_artifact_id=uuid.uuid4(),
        accounting_engine_artifact_id=uuid.uuid4(),
        benchmark_engine_artifact_id=uuid.uuid4(),
        performance_engine_artifact_id=uuid.uuid4(),
        as_of_date=date(2026, 8, 3),
    )
    assert len(cells) == 2 * 3 * 5
    assert {cell.template_key for cell in cells} == set(FORMAL_INTERVALS)
    assert {cell.cost_scenario_artifact_id for cell in cells} == set(costs.values())
    assert {cell.strategy_target_artifact_id for cell in cells} == set(targets)


def test_release_cells_reject_missing_formal_cost_artifact() -> None:
    with pytest.raises(ValueError, match="published 2/5/10"):
        build_release_cells(
            target_path_artifact_ids=(uuid.uuid4(),),
            benchmark_version_artifact_id=uuid.uuid4(),
            cost_scenario_artifacts={2: uuid.uuid4()},
            metric_catalog_artifact_id=uuid.uuid4(),
            accounting_engine_artifact_id=uuid.uuid4(),
            benchmark_engine_artifact_id=uuid.uuid4(),
            performance_engine_artifact_id=uuid.uuid4(),
            as_of_date=date(2026, 8, 3),
        )


@pytest.mark.parametrize("workers", [0, 17])
def test_release_suite_rejects_unsafe_worker_count(workers: int) -> None:
    with pytest.raises(ValueError, match="max_workers must be between 1 and 16"):
        run_release_suite(
            None,  # type: ignore[arg-type]
            suite_key="v02_formal",
            cells=(),
            orchestration_engine_artifact_id=uuid.uuid4(),
            required_warmup_observations=253,
            max_workers=workers,
        )


def test_parallel_release_schedule_round_robins_distinct_targets() -> None:
    targets = (uuid.uuid4(), uuid.uuid4(), uuid.uuid4())
    cells = tuple(
        _cell
        for target in targets
        for _cell in build_release_cells(
            target_path_artifact_ids=(target,),
            benchmark_version_artifact_id=uuid.uuid4(),
            cost_scenario_artifacts={2: uuid.uuid4(), 5: uuid.uuid4(), 10: uuid.uuid4()},
            metric_catalog_artifact_id=uuid.uuid4(),
            accounting_engine_artifact_id=uuid.uuid4(),
            benchmark_engine_artifact_id=uuid.uuid4(),
            performance_engine_artifact_id=uuid.uuid4(),
            as_of_date=date(2026, 8, 3),
        )
    )
    specifications = tuple(
        ExperimentSpecificationPublication(uuid.uuid4(), str(index), False)
        for index in range(len(cells))
    )

    scheduled = _interleave_specifications_by_target(cells, specifications)
    target_by_specification = {
        specification.artifact_id: cell.strategy_target_artifact_id
        for cell, specification in zip(cells, specifications, strict=True)
    }

    assert tuple(target_by_specification[item.artifact_id] for item in scheduled[:3]) == targets
    assert set(scheduled) == set(specifications)
