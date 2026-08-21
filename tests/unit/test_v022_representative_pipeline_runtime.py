from __future__ import annotations

import io
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from typing import Any, cast

import pyarrow.parquet as pq  # type: ignore[import-untyped]
import pytest

from style_rotation.v022.dag import ClaimedGraphWork
from style_rotation.v022.incremental_runtime import (
    IncrementalExecutionContract,
    IncrementalRunPlan,
    PartitionWork,
)
from style_rotation.v022.payload_runtime import (
    LocalPayloadObjectStore,
    PublishedNodeOutput,
    PublishedNodeOutputBundle,
)
from style_rotation.v022.representative_pipeline_runtime import (
    AMIHUD_STAGE1_OUTPUTS,
    AMIHUD_STAGE1_WORK_KEY,
    CATALOG_STAGE1_FEATURE_KEYS,
    FINAL_FEATURE_KEYS,
    MATERIALIZED_FEATURE_KEYS,
    CatalogMultiOutputPublicationTarget,
    CatalogStage1PublicationTarget,
    CatalogStage2PublicationTarget,
    FrozenRawInputs,
    PublishedRawPayload,
    PublishedRawPayloadBundle,
    PublishedRepresentativeNodeOutputs,
    RawSnapshotPoint,
    RepresentativeProcessingMaterialization,
    _CompiledTerminalNode,
    _DatasetSnapshotContext,
    _prepare_raw_payload,
    _product_executable_assets,
    _RawFeatureContext,
    _resolve_asset_snapshot_proofs,
    _write_raw_payload,
    encode_representative_final_signal_parquet,
    execute_representative_snapshot,
    materialize_product_representative_processing,
    materialize_representative_processing,
    publish_catalog_multi_output_stage1_node,
    publish_catalog_stage1_node_outputs,
    publish_catalog_stage2_node_output,
    publish_partitioned_catalog_stage1_node_output,
)
from style_rotation.v022.runtime_contract import (
    V022RuntimeContractError,
    V022RuntimeDataError,
)


def test_product_raw_payload_uses_snapshot_semantics_and_binding(tmp_path: Any) -> None:
    security_id = uuid.uuid4()
    asset_id = uuid.uuid4()
    snapshot_id = uuid.uuid4()
    feature = _RawFeatureContext(
        feature_variant_key="adjusted_close",
        source_field="adj_close",
        unit="price",
        feature_version_id=uuid.uuid4(),
        feature_artifact_id=uuid.uuid4(),
        payload_contract_version_id=uuid.uuid4(),
        payload_contract_artifact_id=uuid.uuid4(),
        output_port_key="raw_numeric",
    )
    session = date(2025, 1, 2)
    close_at = datetime(2025, 1, 2, 21, tzinfo=UTC)
    context = _DatasetSnapshotContext(
        compiled_execution_data_context_id=uuid.uuid4(),
        execution_data_context_artifact_id=uuid.uuid4(),
        dataset_publication_id=uuid.uuid4(),
        dataset_artifact_id=uuid.uuid4(),
        catalog_release_id=uuid.uuid4(),
        encoding_id=uuid.uuid4(),
        encoding_artifact_id=uuid.uuid4(),
        assets={security_id: (asset_id, "aapl")},
        snapshots={security_id: (uuid.uuid4(), close_at, close_at)},
        session_closes={session: close_at},
        features=(feature,),
        coverage_start=session,
        coverage_end=session,
        product_input_snapshot_id=snapshot_id,
        product_input_snapshot_artifact_id=uuid.uuid4(),
    )
    prepared = _prepare_raw_payload(
        LocalPayloadObjectStore(tmp_path),
        context,
        feature,
        (
            cast(
                Any,
                {
                    "asset_id": asset_id,
                    "session_date": session,
                    "adj_close": Decimal("100"),
                },
            ),
        ),
    )
    assert prepared.snapshot_semantics["product_input_snapshot_id"] == str(snapshot_id)
    assert "compiled_execution_data_context_id" not in prepared.snapshot_semantics

    executed: list[tuple[str, dict[str, object] | None]] = []

    class RecordingConnection:
        def execute(
            self, statement: object, parameters: dict[str, object] | None = None
        ) -> None:
            executed.append((str(statement), parameters))

    _write_raw_payload(
        cast(Any, RecordingConnection()),
        uuid.uuid4(),
        context=context,
        prepared=prepared,
    )

    sql = "\n".join(item[0] for item in executed)
    assert "data.v022_product_input_payload_binding" in sql
    assert "data.v022_execution_context_payload_binding" not in sql
    binding_parameters = executed[-1][1]
    assert binding_parameters is not None
    assert binding_parameters["snapshot"] == snapshot_id


def test_existing_raw_bundle_skips_full_market_history_reload(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    from style_rotation.v022 import representative_pipeline_runtime as runtime

    context = cast(Any, SimpleNamespace())
    bundle = PublishedRawPayloadBundle(
        compiled_execution_data_context_id=uuid.uuid4(),
        dataset_publication_id=uuid.uuid4(),
        snapshot_semantic_mode="back_adjusted_historical_research",
        outputs=(),
    )
    monkeypatch.setattr(
        runtime, "_existing_raw_payload_bundle", lambda *_args, **_kwargs: bundle
    )
    monkeypatch.setattr(
        runtime,
        "_load_market_rows",
        lambda *_args, **_kwargs: pytest.fail("cache hit must not load daily bars"),
    )

    assert runtime._publish_market_raw_payloads(
        cast(Any, object()),
        object_store=LocalPayloadObjectStore(tmp_path),
        context=context,
    ) is bundle


def test_frozen_raw_inputs_stream_rows_without_materializing_query_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from style_rotation.v022 import representative_pipeline_runtime as runtime

    security_id = uuid.uuid4()
    legacy_asset_id = uuid.uuid4()
    session = date(2025, 1, 2)
    close_at = datetime(2025, 1, 2, 21, tzinfo=UTC)
    context = cast(
        Any,
        SimpleNamespace(
            assets={security_id: (legacy_asset_id, "aapl")},
            session_closes={session: close_at},
            dataset_publication_id=uuid.uuid4(),
        ),
    )
    monkeypatch.setattr(runtime, "_load_dataset_snapshot_context", lambda *_a, **_k: context)
    monkeypatch.setattr(
        runtime,
        "_load_market_rows",
        lambda *_args, **_kwargs: pytest.fail("Processing must use the streaming reader"),
    )
    monkeypatch.setattr(
        runtime,
        "_iter_market_rows",
        lambda *_args, **_kwargs: iter(
            (
                {
                    "asset_id": legacy_asset_id,
                    "session_date": session,
                    "adj_close": Decimal("101"),
                    "close_raw": Decimal("102"),
                    "volume_raw": Decimal("1000"),
                },
            )
        ),
    )

    raw = runtime._load_frozen_raw_inputs(
        cast(Any, object()),
        compiled_execution_data_context_id=uuid.uuid4(),
        requested_start=session,
        requested_end=session,
    )

    assert [item.value for item in raw.adjusted_close] == [Decimal("101")]
    assert [item.value for item in raw.close_raw] == [Decimal("102")]
    assert [item.value for item in raw.volume_raw] == [Decimal("1000")]


def test_product_processing_rejects_range_drift_before_publishing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    from style_rotation.v022 import representative_pipeline_runtime as runtime

    published: list[object] = []
    snapshot_id = uuid.uuid4()
    graph_id = uuid.uuid4()
    monkeypatch.setattr(
        runtime,
        "_execution_context_for_graph",
        lambda *_args, **_kwargs: (uuid.uuid4(), uuid.uuid4()),
    )
    monkeypatch.setattr(
        runtime,
        "_product_input_range",
        lambda *_args, **_kwargs: (date(2025, 1, 1), date(2025, 1, 31)),
    )
    monkeypatch.setattr(
        runtime,
        "publish_product_market_raw_payloads",
        lambda *_args, **_kwargs: published.append(object()),
    )

    with pytest.raises(V022RuntimeContractError) as error:
        materialize_representative_processing(
            cast(Any, object()),
            object_store=LocalPayloadObjectStore(tmp_path),
            compiled_research_graph_id=graph_id,
            requested_start=date(2025, 1, 2),
            requested_end=date(2025, 1, 31),
            requested_by="test",
            executor_version="product-runtime-1",
            environment_fingerprint="1" * 64,
            product_input_snapshot_id=snapshot_id,
        )

    assert error.value.reason_code == "product_processing_range_mismatch"
    assert published == []


def test_product_processing_wrapper_uses_snapshot_range(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    from style_rotation.v022 import representative_pipeline_runtime as runtime

    snapshot_id = uuid.uuid4()
    graph_id = uuid.uuid4()
    captured: dict[str, object] = {}
    expected = cast(Any, object())
    monkeypatch.setattr(
        runtime,
        "_product_input_range",
        lambda *_args, **_kwargs: (date(2025, 1, 1), date(2025, 2, 3)),
    )

    def materialize(_engine: object, **kwargs: object) -> object:
        captured.update(kwargs)
        return expected

    monkeypatch.setattr(runtime, "materialize_representative_processing", materialize)

    result = materialize_product_representative_processing(
        cast(Any, object()),
        object_store=LocalPayloadObjectStore(tmp_path),
        product_input_snapshot_id=snapshot_id,
        compiled_research_graph_id=graph_id,
        requested_by="product-worker",
        executor_version="product-runtime-1",
        environment_fingerprint="1" * 64,
    )

    assert result is expected
    assert captured["requested_start"] == date(2025, 1, 1)
    assert captured["requested_end"] == date(2025, 2, 3)
    assert captured["product_input_snapshot_id"] == snapshot_id


def test_snapshot_proof_resolves_provider_ticker_through_catalog_listing() -> None:
    security_id = uuid.uuid4()
    asset_id = uuid.uuid4()
    artifact_id = uuid.uuid4()
    observed_at = datetime(2026, 8, 6, 22, tzinfo=UTC)

    resolved = _resolve_asset_snapshot_proofs(
        assets={security_id: (asset_id, "brk_b")},
        snapshot_rows=(
            {
                "asset_symbol": "BRK.B",
                "artifact_id": artifact_id,
                "fetched_at": observed_at,
                "as_of_at": observed_at,
                "status": "published",
            },
        ),
        security_identifier_rows=(
            {
                "security_id": security_id,
                "identifier_value": "BRK.B",
            },
        ),
    )

    assert resolved == {security_id: (artifact_id, observed_at, observed_at)}


def test_snapshot_proof_prefers_exact_source_subject_identity() -> None:
    security_id = uuid.uuid4()
    artifact_id = uuid.uuid4()
    observed_at = datetime(2026, 8, 6, 22, tzinfo=UTC)

    resolved = _resolve_asset_snapshot_proofs(
        assets={security_id: (uuid.uuid4(), "nktr")},
        snapshot_rows=(
            {
                "asset_symbol": "NKTR",
                "artifact_id": artifact_id,
                "fetched_at": observed_at,
                "as_of_at": observed_at,
                "status": "published",
                "subject_security_id": security_id,
                "subject_fetch_status": "fetched",
            },
        ),
        security_identifier_rows=(),
    )

    assert resolved == {security_id: (artifact_id, observed_at, observed_at)}


def test_product_market_inputs_preserve_but_do_not_execute_uniform_exclusions() -> None:
    executable_security = uuid.uuid4()
    excluded_security = uuid.uuid4()
    executable_asset = uuid.uuid4()

    assets = _product_executable_assets(
        (
            {
                "security_id": executable_security,
                "legacy_asset_id": executable_asset,
                "asset_key": "aapl",
                "is_uniformly_excluded": False,
            },
            {
                "security_id": excluded_security,
                "legacy_asset_id": uuid.uuid4(),
                "asset_key": "fdxf",
                "is_uniformly_excluded": True,
            },
        )
    )

    assert assets == {executable_security: (executable_asset, "aapl")}


def test_representative_snapshot_reaches_three_stage3_signal_payloads() -> None:
    raw = _raw_inputs()

    result = execute_representative_snapshot(raw)

    assert set(FINAL_FEATURE_KEYS).issubset(result.features)
    for key in FINAL_FEATURE_KEYS:
        points = result.features[key]
        assert len(points) == 4 * 205
        assert {item.asset_key for item in points} == {"iwf", "iwd", "iwo", "iwn"}
        assert all(item.known_at.date() == item.session_date for item in points)
        table = pq.read_table(
            io.BytesIO(encode_representative_final_signal_parquet(points))
        )
        assert table.num_rows == len(points)
        assert table.column_names == [
            "decision_date",
            "asset_id",
            "signal_value",
            "known_at",
            "input_revision",
            "missing_reason",
        ]


def test_representative_snapshot_fails_closed_when_one_raw_input_is_missing() -> None:
    complete = _raw_inputs()

    with pytest.raises(
        V022RuntimeDataError, match="representative_raw_input_missing"
    ):
        execute_representative_snapshot(
            FrozenRawInputs(complete.adjusted_close, complete.close_raw, ())
        )


def test_catalog_stage1_nodes_publish_intermediate_payloads(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    from style_rotation.v022 import representative_pipeline_runtime as runtime

    raw = _raw_inputs()
    sessions = tuple(sorted({point.session_date for point in raw.adjusted_close}))
    plan = IncrementalRunPlan(
        IncrementalExecutionContract(
            "full_recompute", ("session_date",), 200, 0, "windowed_forward"
        ),
        sessions,
        (
            PartitionWork(
                "a" * 64,
                {"session_date": "all"},
                sessions,
                sessions,
                "b" * 64,
                "execute",
                None,
            ),
        ),
    )
    targets = (
        CatalogStage1PublicationTarget(
            "total_return__w120",
            "style_rotation.v022.processing.total_return_v1",
            {"window": 120},
            uuid.uuid4(),
            "total_return",
            "decimal_return",
            plan,
        ),
        CatalogStage1PublicationTarget(
            "moving_average_ratio__s1_l200",
            "style_rotation.v022.processing.moving_average_ratio_v1",
            {"short_window": 1, "long_window": 200},
            uuid.uuid4(),
            "ma_ratio",
            "ratio_minus_one",
            plan,
        ),
        CatalogStage1PublicationTarget(
            "relative_dollar_volume__w20",
            "style_rotation.v022.processing.compat.relative_dollar_volume_v1",
            {"window": 20},
            uuid.uuid4(),
            "factor_value",
            "ratio",
            plan,
        ),
    )
    observed: dict[str, list[str]] = {}

    def publish(_engine: object, **kwargs: Any) -> PublishedNodeOutput:
        payload = kwargs["executed_payloads"][0]
        table = pq.read_table(io.BytesIO(payload.content))
        observed[kwargs["output_port_key"]] = table.column_names
        assert table.num_rows == len(raw.adjusted_close)
        assert set(table.column("unit").to_pylist()) == {
            next(
                item.output_unit
                for item in targets
                if item.output_port_key == kwargs["output_port_key"]
            )
        }
        return PublishedNodeOutput(
            kwargs["node_run_id"],
            uuid.uuid4(),
            uuid.uuid4(),
            (uuid.uuid4(),),
            1,
            0,
            False,
        )

    monkeypatch.setattr(runtime, "publish_node_output", publish)
    result = publish_catalog_stage1_node_outputs(
        cast(Any, object()),
        object_store=LocalPayloadObjectStore(tmp_path),
        raw=raw,
        targets=targets,
    )
    adjusted_only = publish_catalog_stage1_node_outputs(
        cast(Any, object()),
        object_store=LocalPayloadObjectStore(tmp_path),
        raw=FrozenRawInputs(raw.adjusted_close, (), ()),
        targets=(targets[0],),
    )

    assert set(result.outputs) == {
        "total_return__w120",
        "moving_average_ratio__s1_l200",
        "relative_dollar_volume__w20",
    }
    assert set(adjusted_only.outputs) == {"total_return__w120"}
    assert observed == {
        "total_return": [
            "session_date",
            "asset_id",
            "feature_value",
            "known_at",
            "input_revision",
            "missing_reason",
            "unit",
        ],
        "ma_ratio": [
            "session_date",
            "asset_id",
            "feature_value",
            "known_at",
            "input_revision",
            "missing_reason",
            "unit",
        ],
        "factor_value": [
            "session_date",
            "asset_id",
            "feature_value",
            "known_at",
            "input_revision",
            "missing_reason",
            "unit",
        ],
    }


def test_partitioned_catalog_stage1_loads_only_one_halo_partition_at_a_time(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    from style_rotation.v022 import representative_pipeline_runtime as runtime

    first_sessions = (date(2024, 12, 30), date(2024, 12, 31))
    second_sessions = (date(2024, 12, 31), date(2025, 1, 2))
    plan = IncrementalRunPlan(
        IncrementalExecutionContract(
            "full_recompute", ("session_date",), 1, 0, "windowed_forward"
        ),
        (date(2024, 12, 30), date(2024, 12, 31), date(2025, 1, 2)),
        (
            PartitionWork(
                "a" * 64,
                {"session_date": "calendar_year:2024"},
                first_sessions,
                first_sessions,
                "b" * 64,
                "execute",
                None,
            ),
            PartitionWork(
                "c" * 64,
                {"session_date": "calendar_year:2025"},
                (date(2025, 1, 2),),
                second_sessions,
                "d" * 64,
                "execute",
                None,
            ),
        ),
    )
    target = CatalogStage1PublicationTarget(
        "total_return__w60",
        "style_rotation.v022.processing.total_return_v1",
        {"window": 60},
        uuid.uuid4(),
        "total_return",
        "decimal_return",
        plan,
    )
    loads: list[tuple[date, date, frozenset[str], bool]] = []

    def load(_engine: object, **kwargs: Any) -> FrozenRawInputs:
        loads.append(
            (
                kwargs["requested_start"],
                kwargs["requested_end"],
                kwargs["required_features"],
                kwargs["require_all_assets"],
            )
        )
        return _raw_inputs()

    monkeypatch.setattr(
        runtime,
        "_load_dataset_snapshot_context",
        lambda *_args, **_kwargs: object(),
    )
    monkeypatch.setattr(runtime, "_load_raw_inputs_from_context", load)
    monkeypatch.setattr(
        runtime,
        "_execute_catalog_stage1_partition",
        lambda **kwargs: runtime.ExecutedPartitionPayload(
            kwargs["partition_key_hash"], b"partition", {}
        ),
    )
    monkeypatch.setattr(
        runtime,
        "publish_node_output",
        lambda _engine, **kwargs: PublishedNodeOutput(
            kwargs["node_run_id"],
            uuid.uuid4(),
            uuid.uuid4(),
            (uuid.uuid4(), uuid.uuid4()),
            2,
            0,
            False,
        ),
    )

    result = publish_partitioned_catalog_stage1_node_output(
        cast(Any, object()),
        object_store=LocalPayloadObjectStore(tmp_path),
        compiled_execution_data_context_id=uuid.uuid4(),
        target=target,
    )

    assert set(result.outputs) == {"total_return__w60"}
    assert loads == [
        (first_sessions[0], first_sessions[-1], frozenset({"adjusted_close"}), False),
        (second_sessions[0], second_sessions[-1], frozenset({"adjusted_close"}), False),
    ]


def test_catalog_amihud_node_publishes_three_outputs_atomically(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    from style_rotation.v022 import representative_pipeline_runtime as runtime

    raw = _raw_inputs()
    sessions = tuple(sorted({point.session_date for point in raw.adjusted_close}))
    plan = IncrementalRunPlan(
        IncrementalExecutionContract(
            "full_recompute", ("asset_id",), 1, 0, "from_revised_session_forward"
        ),
        sessions,
        (
            PartitionWork(
                "c" * 64,
                {"asset_id": "all"},
                sessions,
                sessions,
                "d" * 64,
                "execute",
                None,
            ),
        ),
    )
    target = CatalogMultiOutputPublicationTarget(
        uuid.uuid4(),
        "style_rotation.v022.processing.amihud_daily_primitives_v1",
        {},
        {
            "simple_return__amihud_daily": ("simple_return", "decimal_return"),
            "dollar_volume__close_times_volume": ("dollar_volume", "currency"),
            "daily_price_impact__amihud": (
                "daily_price_impact",
                "return_per_currency",
            ),
        },
        plan,
    )
    observed_ports: list[str] = []

    def publish_bundle(_engine: object, **kwargs: Any) -> PublishedNodeOutputBundle:
        outputs = kwargs["outputs"]
        observed_ports.extend(item.output_port_key for item in outputs)
        published = tuple(
            PublishedNodeOutput(
                target.node_run_id,
                uuid.uuid4(),
                uuid.uuid4(),
                (uuid.uuid4(),),
                1,
                0,
                False,
            )
            for _ in outputs
        )
        return PublishedNodeOutputBundle(
            target.node_run_id, uuid.uuid4(), uuid.uuid4(), published, False
        )

    monkeypatch.setattr(runtime, "publish_node_output_bundle", publish_bundle)
    result = publish_catalog_multi_output_stage1_node(
        cast(Any, object()),
        object_store=LocalPayloadObjectStore(tmp_path),
        raw=raw,
        target=target,
    )

    assert set(result.outputs) == set(target.outputs)
    assert observed_ports == ["daily_price_impact", "dollar_volume", "simple_return"]


def test_catalog_stage2_node_publishes_from_intermediate_points(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    from style_rotation.v022 import representative_pipeline_runtime as runtime

    raw = _raw_inputs()
    source = execute_representative_snapshot(raw).features[
        "moving_average_ratio__s1_l200"
    ]
    sessions = tuple(sorted({item.session_date for item in source}))
    plan = IncrementalRunPlan(
        IncrementalExecutionContract(
            "full_recompute", ("asset_id",), 1, 0, "from_revised_session_forward"
        ),
        sessions,
        (
            PartitionWork(
                "e" * 64,
                {"asset_id": "all"},
                sessions,
                sessions,
                "f" * 64,
                "execute",
                None,
            ),
        ),
    )
    target = CatalogStage2PublicationTarget(
        "price_cross_above_ma__s1_l200",
        "style_rotation.v022.processing.price_cross_above_ma_v1",
        {"short_window": 1, "long_window": 200},
        "ma_ratio",
        "event_score",
        "event_score",
        "final_signal_numeric",
        uuid.uuid4(),
        plan,
    )
    observed_rows: list[int] = []

    def publish(_engine: object, **kwargs: Any) -> PublishedNodeOutput:
        table = pq.read_table(io.BytesIO(kwargs["executed_payloads"][0].content))
        observed_rows.append(table.num_rows)
        return PublishedNodeOutput(
            target.node_run_id,
            uuid.uuid4(),
            uuid.uuid4(),
            (uuid.uuid4(),),
            1,
            0,
            False,
        )

    monkeypatch.setattr(runtime, "publish_node_output", publish)
    result = publish_catalog_stage2_node_output(
        cast(Any, object()),
        object_store=LocalPayloadObjectStore(tmp_path),
        inputs=source,
        target=target,
    )

    assert set(result.outputs) == {"price_cross_above_ma__s1_l200"}
    assert observed_rows == [len(source)]


def test_representative_stage2_plans_two_manifest_backed_node_runs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    from style_rotation.v022 import representative_pipeline_runtime as runtime

    work_ids = (uuid.uuid4(), uuid.uuid4())
    node_runs = {key: uuid.uuid4() for key in runtime.STAGE2_FEATURE_KEYS}
    contract = IncrementalExecutionContract(
        "full_recompute", ("asset_id",), 1, 0, "from_revised_session_forward"
    )
    compiled = tuple(
        _CompiledTerminalNode(
            key,
            uuid.uuid4(),
            uuid.uuid4(),
            uuid.uuid4(),
            (
                {"window": 20}
                if key == "amihud_illiquidity__w20"
                else {"short_window": 1, "long_window": 200}
            ),
            contract,
            "deterministic",
            "content_addressed",
            (
                "rolling_mean_impact"
                if key == "amihud_illiquidity__w20"
                else "event_score"
            ),
            character * 64,
            (
                "style_rotation.v022.processing.amihud_illiquidity_v1"
                if key == "amihud_illiquidity__w20"
                else "style_rotation.v022.processing.price_cross_above_ma_v1"
            ),
            "return_per_currency" if key == "amihud_illiquidity__w20" else "event_score",
        )
        for key, character in zip(runtime.STAGE2_FEATURE_KEYS, ("7", "8"), strict=True)
    )
    finished: list[uuid.UUID] = []

    class FakeDag:
        def __init__(self, _engine: object) -> None:
            self.index = 0

        def plan_run(self, **kwargs: Any) -> SimpleNamespace:
            assert len(kwargs["work"]) == 2
            return SimpleNamespace(
                graph_run_id=uuid.uuid4(), work_item_ids=work_ids, reused=False
            )

        def claim(self, _run: uuid.UUID, **_kwargs: Any) -> ClaimedGraphWork:
            work = work_ids[self.index]
            self.index += 1
            return ClaimedGraphWork(work, self.index, "node")

        def finish(self, claim: ClaimedGraphWork, **_kwargs: Any) -> None:
            finished.append(claim.graph_work_item_id)

        def renew(self, _claim: ClaimedGraphWork, **_kwargs: Any) -> None:
            return None

    monkeypatch.setattr(runtime, "GraphDagService", FakeDag)
    monkeypatch.setattr(runtime, "_graph_work_status", lambda *_args: "queued")
    monkeypatch.setattr(
        runtime, "_load_compiled_manifest_nodes", lambda *_args, **_kwargs: compiled
    )
    monkeypatch.setattr(
        runtime,
        "_publish_stage2_node_run",
        lambda *_args, compiled, **_kwargs: node_runs[compiled.feature_variant_key],
    )
    monkeypatch.setattr(runtime, "record_partition_plan", lambda *_args, **_kwargs: None)

    def execute(_engine: object, *, target: Any, **_kwargs: Any) -> Any:
        output = PublishedNodeOutput(
            target.node_run_id,
            uuid.uuid4(),
            uuid.uuid4(),
            (uuid.uuid4(),),
            1,
            0,
            False,
        )
        return PublishedRepresentativeNodeOutputs({target.feature_variant_key: output})

    monkeypatch.setattr(
        runtime, "execute_partitioned_catalog_stage2_from_manifest", execute
    )
    sources = {
        key: runtime.PublishedFeatureManifest(
            key, uuid.uuid4(), uuid.uuid4(), character * 64
        )
        for key, character in zip(
            runtime.STAGE2_INPUT_FEATURES.values(), ("a", "b"), strict=True
        )
    }
    sessions = (date(2025, 1, 1), date(2025, 1, 2))
    _run, outputs = runtime._materialize_representative_stage2(
        cast(Any, object()),
        object_store=LocalPayloadObjectStore(tmp_path),
        compiled_research_graph_id=uuid.uuid4(),
        processing_context_artifact_id=uuid.uuid4(),
        processing_context_role="processing_calculation_context",
        requested_range={"start": "2025-01-01", "end": "2025-01-02"},
        requested_by="test",
        executor_version="test-runtime",
        environment_fingerprint="1" * 64,
        sessions=sessions,
        source_revisions={session: "2" * 64 for session in sessions},
        sources=sources,
        asset_keys={},
        topology=runtime._CatalogLayerTopology(
            runtime.STAGE2_FEATURE_KEYS,
            runtime.STAGE2_INPUT_FEATURES,
            {
                "amihud_illiquidity__w20": "daily_price_impact",
                "price_cross_above_ma__s1_l200": "ma_ratio",
            },
            {
                "amihud_illiquidity__w20": "intermediate_numeric_feature",
                "price_cross_above_ma__s1_l200": "final_signal_numeric",
            },
        ),
    )

    assert set(outputs) == set(runtime.STAGE2_FEATURE_KEYS)
    assert set(finished) == set(work_ids)


def test_materialize_representative_processing_builds_stage1_and_terminal_node_targets(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    from style_rotation.v022 import representative_pipeline_runtime as runtime

    graph_id = uuid.uuid4()
    context_id = uuid.uuid4()
    context_artifact_id = uuid.uuid4()
    calculation_context_id = uuid.uuid4()
    calculation_context_artifact_id = uuid.uuid4()
    additional_stage1_keys = (
        "lagged_return__l120_s20",
        "maximum_drawdown__w60",
    )
    planned_keys = MATERIALIZED_FEATURE_KEYS + additional_stage1_keys
    work_ids = tuple(uuid.uuid4() for _ in planned_keys)
    node_run_ids = {
        key: uuid.uuid5(uuid.NAMESPACE_URL, f"test-node-run:{key}")
        for key in planned_keys
    }
    raw_bundle = PublishedRawPayloadBundle(
        compiled_execution_data_context_id=context_id,
        dataset_publication_id=uuid.uuid4(),
        snapshot_semantic_mode="back_adjusted_historical_research",
        outputs=tuple(
            PublishedRawPayload(
                feature_variant_key=key,
                feature_version_id=uuid.uuid4(),
                payload_manifest_id=uuid.uuid4(),
                manifest_artifact_id=uuid.uuid4(),
                manifest_hash=character * 64,
                reused_publication=False,
            )
            for key, character in zip(
                ("adjusted_close", "close_raw", "volume_raw"),
                ("a", "b", "c"),
                strict=True,
            )
        ),
        calculation_context_id=calculation_context_id,
        calculation_context_artifact_id=calculation_context_artifact_id,
        calculation_context_fingerprint="d" * 64,
    )
    contract = IncrementalExecutionContract(
        "full_recompute", ("session_date",), 0, 0, "same_cross_section"
    )
    stage1_compiled = (
        _CompiledTerminalNode(
            feature_variant_key="moving_average_ratio__s1_l200",
            compiled_graph_node_id=uuid.uuid4(),
            node_version_id=uuid.uuid4(),
            node_version_artifact_id=uuid.uuid4(),
            resolved_parameters={"short_window": 1, "long_window": 200},
            execution_contract=contract,
            determinism_policy="deterministic",
            cache_policy="content_addressed",
            output_port_key="ma_ratio",
            execution_fingerprint="g" * 64,
            implementation_key="style_rotation.v022.processing.moving_average_ratio_v1",
            output_unit="ratio_minus_one",
        ),
        _CompiledTerminalNode(
            feature_variant_key="total_return__w120",
            compiled_graph_node_id=uuid.uuid4(),
            node_version_id=uuid.uuid4(),
            node_version_artifact_id=uuid.uuid4(),
            resolved_parameters={"window": 120},
            execution_contract=contract,
            determinism_policy="deterministic",
            cache_policy="content_addressed",
            output_port_key="total_return",
            execution_fingerprint="h" * 64,
            implementation_key="style_rotation.v022.processing.total_return_v1",
            output_unit="decimal_return",
        ),
    )
    amihud_compiled = _CompiledTerminalNode(
        feature_variant_key=AMIHUD_STAGE1_WORK_KEY,
        compiled_graph_node_id=uuid.uuid4(),
        node_version_id=uuid.uuid4(),
        node_version_artifact_id=uuid.uuid4(),
        resolved_parameters={},
        execution_contract=contract,
        determinism_policy="deterministic",
        cache_policy="content_addressed",
        output_port_key="",
        execution_fingerprint="i" * 64,
        implementation_key="style_rotation.v022.processing.amihud_daily_primitives_v1",
        additional_outputs=AMIHUD_STAGE1_OUTPUTS,
    )
    parity_compiled = (
        _CompiledTerminalNode(
            feature_variant_key="lagged_return__l120_s20",
            compiled_graph_node_id=uuid.uuid4(),
            node_version_id=uuid.uuid4(),
            node_version_artifact_id=uuid.uuid4(),
            resolved_parameters={"long_window": 120, "skip_window": 20},
            execution_contract=contract,
            determinism_policy="deterministic",
            cache_policy="content_addressed",
            output_port_key="factor_value",
            execution_fingerprint="j" * 64,
            implementation_key="style_rotation.v022.processing.compat.lagged_return_v1",
            output_unit="decimal_return",
        ),
        _CompiledTerminalNode(
            feature_variant_key="maximum_drawdown__w60",
            compiled_graph_node_id=uuid.uuid4(),
            node_version_id=uuid.uuid4(),
            node_version_artifact_id=uuid.uuid4(),
            resolved_parameters={"window": 60},
            execution_contract=contract,
            determinism_policy="deterministic",
            cache_policy="content_addressed",
            output_port_key="factor_value",
            execution_fingerprint="k" * 64,
            implementation_key="style_rotation.v022.processing.compat.maximum_drawdown_v1",
            output_unit="decimal_drawdown",
        ),
    )
    compiled = stage1_compiled + (amihud_compiled,) + parity_compiled
    finished: list[uuid.UUID] = []
    recorded: list[uuid.UUID] = []

    class FakeDag:
        def __init__(self, _engine: object) -> None:
            self.next_claim = 0

        def plan_run(self, **kwargs: object) -> SimpleNamespace:
            assert len(kwargs["work"]) == len(planned_keys)  # type: ignore[arg-type]
            return SimpleNamespace(graph_run_id=uuid.uuid4(), work_item_ids=work_ids)

        def claim(self, _run: uuid.UUID, **_kwargs: object) -> ClaimedGraphWork:
            work_id = work_ids[self.next_claim]
            self.next_claim += 1
            return ClaimedGraphWork(work_id, self.next_claim, "node")

        def finish(self, claim: ClaimedGraphWork, **_kwargs: object) -> None:
            finished.append(claim.graph_work_item_id)

        def renew(self, _claim: ClaimedGraphWork, **_kwargs: object) -> None:
            return None

    monkeypatch.setattr(runtime, "GraphDagService", FakeDag)
    monkeypatch.setattr(
        runtime,
        "_execution_context_for_graph",
        lambda *_args: (context_id, context_artifact_id),
    )
    monkeypatch.setattr(
        runtime,
        "publish_frozen_market_raw_payloads",
        lambda *_args, **_kwargs: raw_bundle,
    )
    processing_identity: dict[str, object] = {}

    def load_nodes(*_args: object, **kwargs: object) -> tuple[_CompiledTerminalNode, ...]:
        processing_identity.update(kwargs)
        return compiled

    monkeypatch.setattr(runtime, "_load_compiled_terminal_nodes", load_nodes)
    monkeypatch.setattr(runtime, "_graph_work_status", lambda *_args: "queued")
    sessions = (date(2025, 1, 1), date(2025, 1, 2))
    monkeypatch.setattr(runtime, "_requested_sessions", lambda *_args, **_kwargs: sessions)
    monkeypatch.setattr(runtime, "_load_frozen_raw_inputs", lambda *_args, **_kwargs: _raw_inputs())
    monkeypatch.setattr(
        runtime,
        "_publish_node_run",
        lambda *_args, compiled, **_kwargs: node_run_ids[compiled.feature_variant_key],
    )
    monkeypatch.setattr(
        runtime,
        "record_partition_plan",
        lambda _engine, *, node_run_id, plan: recorded.append(node_run_id),
    )

    def publish_targets(
        _engine: object,
        *,
        targets: tuple[Any, ...] | None = None,
        target: Any | None = None,
        **_kwargs: object,
    ) -> PublishedRepresentativeNodeOutputs:
        resolved_targets = targets if targets is not None else (target,)
        return PublishedRepresentativeNodeOutputs(
            {
                item.feature_variant_key: PublishedNodeOutput(
                    node_run_id=item.node_run_id,
                    payload_manifest_id=uuid.uuid4(),
                    manifest_artifact_id=uuid.uuid4(),
                    payload_partition_ids=(uuid.uuid4(),),
                    executed_partition_count=1,
                    reused_partition_count=0,
                    reused_publication=False,
                )
                for item in resolved_targets
            }
        )

    monkeypatch.setattr(runtime, "_publish_representative_node_targets", publish_targets)
    monkeypatch.setattr(
        runtime, "publish_partitioned_catalog_stage1_node_output", publish_targets
    )
    monkeypatch.setattr(
        runtime,
        "_execution_context_asset_keys",
        lambda *_args, **_kwargs: {
            point.asset_id: point.asset_key for point in _raw_inputs().adjusted_close
        },
    )

    def publish_multi(
        _engine: object, *, target: Any, **_kwargs: object
    ) -> PublishedRepresentativeNodeOutputs:
        return PublishedRepresentativeNodeOutputs(
            {
                variant: PublishedNodeOutput(
                    target.node_run_id,
                    uuid.uuid4(),
                    uuid.uuid4(),
                    (uuid.uuid4(),),
                    1,
                    0,
                    False,
                )
                for variant in target.outputs
            }
        )

    monkeypatch.setattr(runtime, "publish_catalog_multi_output_stage1_node", publish_multi)
    monkeypatch.setattr(
        runtime,
        "_published_feature_manifest",
        lambda _engine, key, output: runtime.PublishedFeatureManifest(
            key, output.payload_manifest_id, output.manifest_artifact_id, "a" * 64
        ),
    )
    price_output = PublishedNodeOutput(
        uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), (uuid.uuid4(),), 1, 0, False
    )
    monkeypatch.setattr(
        runtime,
        "_materialize_representative_stage2",
        lambda *_args, **_kwargs: (
            uuid.uuid4(),
            {
                "price_cross_above_ma__s1_l200": price_output,
                "amihud_illiquidity__w20": PublishedNodeOutput(
                    uuid.uuid4(),
                    uuid.uuid4(),
                    uuid.uuid4(),
                    (uuid.uuid4(),),
                    1,
                    0,
                    False,
                ),
            },
        ),
    )
    stage3_outputs = {
        key: PublishedNodeOutput(
            uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), (uuid.uuid4(),), 1, 0, False
        )
        for key in runtime.STAGE3_FEATURE_KEYS
    }
    monkeypatch.setattr(
        runtime,
        "_materialize_representative_stage3",
        lambda *_args, **_kwargs: (uuid.uuid4(), stage3_outputs),
    )
    monkeypatch.setattr(
        runtime,
        "_aggregation_input_feature_keys",
        lambda *_args, **_kwargs: FINAL_FEATURE_KEYS,
    )
    result = materialize_representative_processing(
        cast(Any, object()),
        object_store=LocalPayloadObjectStore(tmp_path),
        compiled_research_graph_id=graph_id,
        requested_start=sessions[0],
        requested_end=sessions[-1],
        requested_by="test",
        executor_version="v022-first-slice-runtime-1",
        environment_fingerprint="1" * 64,
    )

    assert set(result.stage3_outputs) == set(FINAL_FEATURE_KEYS)
    assert set(CATALOG_STAGE1_FEATURE_KEYS).isdisjoint(result.stage3_outputs)
    assert set(recorded) == set(node_run_ids.values())
    assert set(finished) == set(work_ids)
    assert processing_identity["processing_context_artifact_id"] == (
        calculation_context_artifact_id
    )
    assert processing_identity["processing_context_role"] == (
        "processing_calculation_context"
    )


def test_empty_catalog_layer_reuses_predecessor_graph_run(tmp_path: Any) -> None:
    from style_rotation.v022 import representative_pipeline_runtime as runtime

    predecessor = uuid.uuid4()
    empty = runtime._CatalogLayerTopology((), {}, {}, {})
    common: dict[str, Any] = {
        "object_store": LocalPayloadObjectStore(tmp_path),
        "compiled_research_graph_id": uuid.uuid4(),
        "processing_context_artifact_id": uuid.uuid4(),
        "processing_context_role": "processing_calculation_context",
        "requested_range": {"start": "2025-01-01", "end": "2025-01-02"},
        "requested_by": "test",
        "executor_version": "test-runtime",
        "environment_fingerprint": "1" * 64,
        "sessions": (date(2025, 1, 1), date(2025, 1, 2)),
        "source_revisions": {},
        "sources": {},
        "asset_keys": {},
        "topology": empty,
        "fallback_graph_run_id": predecessor,
    }

    assert runtime._materialize_representative_stage2(
        cast(Any, object()), **common
    ) == (predecessor, {})
    assert runtime._materialize_representative_stage3(
        cast(Any, object()), **common
    ) == (predecessor, {})


def test_materialize_representative_processing_replays_exact_completed_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    from style_rotation.v022 import representative_pipeline_runtime as runtime

    graph_id = uuid.uuid4()
    context_id = uuid.uuid4()
    graph_run_id = uuid.uuid4()
    raw_bundle = PublishedRawPayloadBundle(
        compiled_execution_data_context_id=context_id,
        dataset_publication_id=uuid.uuid4(),
        snapshot_semantic_mode="back_adjusted_historical_research",
        outputs=tuple(
            PublishedRawPayload(
                feature_variant_key=key,
                feature_version_id=uuid.uuid4(),
                payload_manifest_id=uuid.uuid4(),
                manifest_artifact_id=uuid.uuid4(),
                manifest_hash=character * 64,
                reused_publication=True,
            )
            for key, character in zip(
                ("adjusted_close", "close_raw", "volume_raw"),
                ("a", "b", "c"),
                strict=True,
            )
        ),
    )
    contract = IncrementalExecutionContract(
        "full_recompute", ("session_date",), 0, 0, "same_cross_section"
    )
    compiled = tuple(
        _CompiledTerminalNode(
            feature_variant_key=key,
            compiled_graph_node_id=uuid.uuid4(),
            node_version_id=uuid.uuid4(),
            node_version_artifact_id=uuid.uuid4(),
            resolved_parameters={},
            execution_contract=contract,
            determinism_policy="deterministic",
            cache_policy="content_addressed",
            output_port_key="signal_score",
            execution_fingerprint=character * 64,
        )
        for key, character in zip(FINAL_FEATURE_KEYS, ("d", "e", "f"), strict=True)
    )
    class FakeDag:
        def __init__(self, _engine: object) -> None:
            pass

        def plan_run(self, **_kwargs: object) -> SimpleNamespace:
            return SimpleNamespace(graph_run_id=graph_run_id, reused=True)

    monkeypatch.setattr(runtime, "GraphDagService", FakeDag)
    monkeypatch.setattr(
        runtime, "_execution_context_for_graph", lambda *_: (context_id, uuid.uuid4())
    )
    monkeypatch.setattr(
        runtime,
        "publish_frozen_market_raw_payloads",
        lambda *_args, **_kwargs: raw_bundle,
    )
    monkeypatch.setattr(
        runtime,
        "_load_compiled_terminal_nodes",
        lambda *_args, **_kwargs: compiled,
    )
    monkeypatch.setattr(
        runtime,
        "_existing_representative_materialization",
        lambda *_args, **_kwargs: RepresentativeProcessingMaterialization(
            graph_run_id=graph_run_id,
            compiled_research_graph_id=graph_id,
            compiled_execution_data_context_id=context_id,
            requested_range={"start": "2025-01-01", "end": "2025-01-02"},
            raw_payloads=raw_bundle,
            stage3_outputs={},
        ),
    )
    monkeypatch.setattr(
        runtime,
        "_load_frozen_raw_inputs",
        lambda *_args, **_kwargs: pytest.fail("replay must not reload daily bars"),
    )

    result = materialize_representative_processing(
        cast(Any, object()),
        object_store=LocalPayloadObjectStore(tmp_path),
        compiled_research_graph_id=graph_id,
        requested_start=date(2025, 1, 1),
        requested_end=date(2025, 1, 2),
        requested_by="test",
        executor_version="v022-first-slice-runtime-1",
        environment_fingerprint="1" * 64,
    )

    assert result.graph_run_id == graph_run_id
    assert result.stage3_outputs == {}


def _raw_inputs() -> FrozenRawInputs:
    start = date(2025, 1, 1)
    adjusted: list[RawSnapshotPoint] = []
    close: list[RawSnapshotPoint] = []
    volume: list[RawSnapshotPoint] = []
    for offset in range(205):
        session = start + timedelta(days=offset)
        for _ordinal, (asset_key, slope, traded_volume) in enumerate(
            (
                ("iwf", Decimal("1.0"), Decimal("1000")),
                ("iwd", Decimal("0.5"), Decimal("2000")),
                ("iwo", Decimal("0.2"), Decimal("3000")),
                ("iwn", Decimal("0.1"), Decimal("4000")),
            )
        ):
            asset_id = uuid.uuid5(uuid.NAMESPACE_URL, f"test-security:{asset_key}")
            value = Decimal("100") + slope * Decimal(offset)
            common = {
                "asset_id": asset_id,
                "asset_key": asset_key,
                "session_date": session,
                "known_at": datetime(
                    session.year, session.month, session.day, 21, tzinfo=UTC
                ),
                "vintage_id": "dataset-publication:test",
            }
            adjusted.append(RawSnapshotPoint(value=value, unit="price", **common))
            close.append(RawSnapshotPoint(value=value, unit="price", **common))
            volume.append(
                RawSnapshotPoint(value=traded_volume, unit="shares", **common)
            )
    def sort_key(item: RawSnapshotPoint) -> tuple[date, str]:
        return item.session_date, str(item.asset_id)

    return FrozenRawInputs(
        tuple(sorted(adjusted, key=sort_key)),
        tuple(sorted(close, key=sort_key)),
        tuple(sorted(volume, key=sort_key)),
    )
