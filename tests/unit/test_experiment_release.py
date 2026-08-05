import uuid
from datetime import date

import pytest

from style_rotation.experiment.release import FORMAL_INTERVALS, build_release_cells


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
