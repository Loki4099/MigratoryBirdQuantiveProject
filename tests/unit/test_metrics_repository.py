from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal

import pytest

from style_rotation.metrics.repository import (
    FORMAL_COSTS,
    FORMAL_FREQUENCIES,
    FORMAL_RUN_CONFIGURATION,
    FORMAL_TEMPLATES,
    validate_formal_run_matrix,
)
from style_rotation.metrics.types import SourceRunDescriptor


def _formal_runs() -> tuple[SourceRunDescriptor, ...]:
    experiment_id = uuid.uuid4()
    data_version_id = uuid.uuid4()
    cleaning_version_id = uuid.uuid4()
    factor_version_id = uuid.uuid4()
    strategy_version_id = uuid.uuid4()
    engine_version_id = uuid.uuid4()
    runs: list[SourceRunDescriptor] = []
    for variant_index in range(24):
        variant_key = f"variant_{variant_index:02d}"
        variant_id = uuid.uuid5(uuid.NAMESPACE_URL, variant_key)
        for frequency in sorted(FORMAL_FREQUENCIES):
            signal_date = date(2001, 7, 31) + (
                timedelta(days=3) if frequency == "weekly" else timedelta(0)
            )
            execution_date = signal_date + timedelta(days=3)
            for template in sorted(FORMAL_TEMPLATES):
                for cost in sorted(FORMAL_COSTS):
                    identity = f"{variant_key}:{frequency}:{template}:{cost}"
                    runs.append(
                        SourceRunDescriptor(
                            run_id=uuid.uuid5(uuid.NAMESPACE_OID, identity),
                            experiment_id=experiment_id,
                            data_version_id=data_version_id,
                            cleaning_version_id=cleaning_version_id,
                            factor_version_id=factor_version_id,
                            strategy_version_id=strategy_version_id,
                            source_engine_version_id=engine_version_id,
                            factor_variant_id=variant_id,
                            factor_variant_key=variant_key,
                            rebalance_frequency=frequency,
                            strategy_template=template,
                            transaction_cost_bps=cost,
                            official_signal_start_date=signal_date,
                            first_execution_date=execution_date,
                            official_end_date=date(2026, 7, 30),
                            configuration=dict(FORMAL_RUN_CONFIGURATION),
                            run_fingerprint=uuid.uuid5(
                                uuid.NAMESPACE_X500, identity
                            ).hex.ljust(64, "0"),
                        )
                    )
    return tuple(runs)


def test_formal_matrix_validator_accepts_exact_cartesian_product() -> None:
    validate_formal_run_matrix(_formal_runs())


def test_formal_matrix_validator_rejects_missing_and_unexpected_cell() -> None:
    runs = list(_formal_runs())
    runs[0] = replace(runs[0], transaction_cost_bps=Decimal(3))
    with pytest.raises(LookupError, match="exact 24 x 2 x 2 x 3 Cartesian product"):
        validate_formal_run_matrix(tuple(runs))


def test_formal_matrix_validator_rejects_duplicate_cell() -> None:
    runs = list(_formal_runs())
    runs[-1] = replace(
        runs[0],
        run_id=uuid.uuid4(),
        run_fingerprint="f" * 64,
    )
    with pytest.raises(LookupError, match="duplicate parameter cells"):
        validate_formal_run_matrix(tuple(runs))


def test_formal_matrix_validator_rejects_inconsistent_frequency_dates() -> None:
    runs = list(_formal_runs())
    runs[0] = replace(
        runs[0],
        official_signal_start_date=runs[0].official_signal_start_date + timedelta(days=1),
    )
    with pytest.raises(LookupError, match="official signal start date"):
        validate_formal_run_matrix(tuple(runs))


def test_formal_matrix_validator_rejects_changed_cost_model() -> None:
    runs = list(_formal_runs())
    runs[0] = replace(runs[0], configuration={"cost_model": "round_trip"})
    with pytest.raises(LookupError, match="invalid frozen configuration value for cost_model"):
        validate_formal_run_matrix(tuple(runs))
