from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from style_rotation.v022.aggregation_work_runtime import (
    AggregationCalculation,
    AggregationOutputPoint,
    SignalManifestPoint,
    VerifiedAggregationInput,
)
from style_rotation.v022.linear_trainable_aggregation import OrdinaryLeastSquaresAdapter
from style_rotation.v022.product_runtime_pipeline import (
    ProductMemberState,
    ProductStrategyContract,
    calculate_product_target,
)
from style_rotation.v022.product_runtime_worker import (
    _AggregationInputIdentity,
    _decision_document,
    _load_product_executable_asset_keys,
    _load_product_input_snapshot,
    _load_product_members,
    _local_aggregation_preset_key,
    _predict_product_ensemble,
    _ProductEnsembleState,
    _ProductModelMember,
    _RuntimeConfiguration,
)
from style_rotation.v022.runtime_contract import V022RuntimeContractError
from style_rotation.v022.trainable_aggregation import FeatureSchema, TrainingMatrixRow


def test_product_runtime_decision_document_is_single_configuration_target() -> None:
    decision_date = date(2026, 8, 14)
    cutoff = datetime(2026, 8, 14, 20, tzinfo=UTC)
    members = tuple(
        ProductMemberState(
            uuid.uuid5(uuid.NAMESPACE_URL, f"worker-member:{ordinal}"),
            f"asset_{ordinal}",
            True,
        )
        for ordinal in range(4)
    )
    aggregation = AggregationCalculation(
        "flat_equal_weight_mean",
        "signal_equal_v1",
        tuple(
            AggregationOutputPoint(
                item.asset_id,
                item.asset_key,
                decision_date,
                Decimal(10 - ordinal),
                cutoff,
                "a" * 64,
                None,
            )
            for ordinal, item in enumerate(members)
        ),
        "b" * 64,
    )
    calculation = calculate_product_target(
        aggregation,
        decision_date=decision_date,
        decision_cutoff_at=cutoff,
        members=members,
        strategy=ProductStrategyContract(
            "cross_section_rank_top_k_parity",
            2,
            "formal",
            "none",
            "none",
        ),
        defense=None,
    )
    branch_id = uuid.uuid4()

    document = _decision_document(
        calculation,
        compiled_strategy_branch_id=branch_id,
        decision_cutoff_at=cutoff,
    )

    assert document["decision_session"] == "2026-08-14"
    assert document["target_identity"]["compiled_strategy_branch_id"] == str(
        branch_id
    )
    assert len(document["ordered_net_asset_targets"]) == 2
    assert document["reserve_target"]["reserve_target_weight"] == "0"
    assert document["market_price_basis"] == "raw_open_at_execution"


def test_product_runtime_aggregation_preset_requires_owning_family() -> None:
    assert (
        _local_aggregation_preset_key(
            "flat_equal_weight_mean", "flat_equal_weight_mean__signal_equal_v1"
        )
        == "signal_equal_v1"
    )
    with pytest.raises(V022RuntimeContractError):
        _local_aggregation_preset_key(
            "flat_equal_weight_mean", "other_family__signal_equal_v1"
        )


def test_product_runtime_loads_member_count_from_exact_snapshot_members() -> None:
    snapshot_id = uuid.uuid4()
    enrollment_id = uuid.uuid4()
    session_id = uuid.uuid4()
    engine = MagicMock()
    connection = engine.connect.return_value.__enter__.return_value
    result = connection.execute.return_value.mappings.return_value
    result.one_or_none.return_value = {
        "product_input_snapshot_id": snapshot_id,
        "artifact_id": uuid.uuid4(),
        "snapshot_fingerprint": "a" * 64,
        "dataset_publication_id": uuid.uuid4(),
        "input_start": date(2015, 12, 31),
        "input_end": date(2026, 6, 26),
        "inputs_available_at": datetime(2026, 8, 17, tzinfo=UTC),
        "member_count": 503,
        "status": "published",
    }

    publication = _load_product_input_snapshot(
        engine,
        product_input_snapshot_id=snapshot_id,
        product_enrollment_id=enrollment_id,
        decision_session_id=session_id,
    )

    statement = str(connection.execute.call_args.args[0])
    assert "FROM product.v022_product_input_member member" in statement
    assert publication.member_count == 503


def test_product_aggregation_context_excludes_uniform_provider_exclusions() -> None:
    security_id = uuid.uuid4()
    engine = MagicMock()
    connection = engine.connect.return_value.__enter__.return_value
    connection.execute.return_value.mappings.return_value = [
        {"security_id": security_id, "asset_key": "aapl"}
    ]

    asset_keys = _load_product_executable_asset_keys(engine, uuid.uuid4())

    statement = str(connection.execute.call_args.args[0])
    assert "AND NOT is_uniformly_excluded" in statement
    assert asset_keys == {security_id: "aapl"}


def test_product_strategy_panel_excludes_uniform_provider_exclusions() -> None:
    security_id = uuid.uuid4()
    engine = MagicMock()
    connection = engine.connect.return_value.__enter__.return_value
    connection.execute.return_value.mappings.return_value = [
        {
            "security_id": security_id,
            "asset_key": "aapl",
            "is_selectable": True,
        }
    ]
    connection.scalars.return_value = []

    members = _load_product_members(engine, uuid.uuid4(), uuid.uuid4())

    statement = str(connection.execute.call_args.args[0])
    assert "AND NOT member.is_uniformly_excluded" in statement
    assert len(members) == 1
    assert members[0].asset_id == security_id


def test_product_supervised_inference_uses_complete_frozen_ensemble_state() -> None:
    cutoff = datetime(2026, 8, 14, 20, tzinfo=UTC)
    decision_date = cutoff.date()
    assets = tuple(uuid.uuid5(uuid.NAMESPACE_URL, f"product-ml:{item}") for item in range(3))
    schema = FeatureSchema(("return_continuation__w120",))
    training_rows = tuple(
        TrainingMatrixRow(
            asset,
            f"asset_{ordinal}",
            date(2026, 8, 10 + day),
            cutoff,
            (Decimal(ordinal + day),),
            Decimal(ordinal + day),
            cutoff,
            date(2026, 8, 10 + day),
            date(2026, 8, 10 + day),
        )
        for day in range(2)
        for ordinal, asset in enumerate(assets)
    )
    fitted = OrdinaryLeastSquaresAdapter().fit(
        training_rows,
        feature_schema=schema,
        seed=0,
        hyperparameters={},
    )
    state = _ProductEnsembleState(
        uuid.uuid4(),
        "a" * 64,
        (
            _ProductModelMember(
                0,
                "forward_rank_h5",
                "ols_daily_expanding_v1",
                fitted.adapter_key,
                fitted.adapter_version,
                fitted.feature_schema_fingerprint,
                "b" * 64,
                fitted,
            ),
        ),
    )
    points = tuple(
        SignalManifestPoint(
            asset,
            f"asset_{ordinal}",
            decision_date,
            Decimal(ordinal),
            cutoff,
            "c" * 64,
            None,
        )
        for ordinal, asset in enumerate(assets)
    )
    input_ = VerifiedAggregationInput(
        uuid.uuid4(),
        "return_continuation__w120",
        "signal_000",
        0,
        uuid.uuid4(),
        uuid.uuid4(),
        "d" * 64,
        points,
    )
    configuration = _RuntimeConfiguration(
        uuid.uuid4(),
        uuid.uuid4(),
        "ols_cross_sectional_regression",
        None,
        (
            _AggregationInputIdentity(
                input_.compiled_feature_occurrence_id,
                input_.feature_variant_key,
                input_.slot_key,
                0,
            ),
        ),
        None,
        ProductStrategyContract(
            "cross_section_rank_top_k_parity", 2, "formal", "none", "none"
        ),
        None,
        None,
        None,
        None,
        "supervised",
    )

    calculation = _predict_product_ensemble(
        configuration,
        (input_,),
        state,
        decision_date=decision_date,
        decision_cutoff_at=cutoff,
    )

    assert calculation.family_key == "ols_cross_sectional_regression"
    assert {item.asset_id: item.signal_value for item in calculation.points} == {
        assets[0]: Decimal("-1.000000000000000000"),
        assets[1]: Decimal("0E-18"),
        assets[2]: Decimal("1.000000000000000000"),
    }
    assert all(item.input_revision == state.state_fingerprint for item in calculation.points)
