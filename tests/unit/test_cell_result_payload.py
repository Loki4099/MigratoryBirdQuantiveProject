from __future__ import annotations

import uuid
from types import SimpleNamespace

import pyarrow.parquet as pq  # type: ignore[import-untyped]
import pytest
from sqlalchemy import create_engine

from style_rotation.experiment.result_payload import (
    PAYLOAD_SCHEMA_VERSION,
    PAYLOAD_STORAGE_FORMAT,
    CellResultPayloadError,
    CellResultPayloadStore,
    hydrate_cell_result_row,
)
from style_rotation.ops.worker import (
    CellExecutionOutput,
    CellExecutionRequest,
    CellResultMaterializer,
)


def test_external_payload_is_content_addressed_zstd_and_hydrates(tmp_path) -> None:
    store = CellResultPayloadStore(tmp_path)
    series = {
        "model_scores": [
            {"observation_date": "2026-08-07", "asset_key": "AAPL", "score": "1.25"},
            {"observation_date": "2026-08-07", "asset_key": "MSFT", "score": "0.75"},
        ],
        "model_input_audit": [{"asset_key": "AAPL", "inputs": [{"value": "1.25"}]}],
    }
    diagnostics = {
        "executor": "predictive_v1",
        "quality_checks": [{"check_key": "coverage", "status": "passed"}],
        "large_debug_trace": [{"ordinal": index, "value": f"trace-{index}"} for index in range(50)],
    }

    external = store.externalize(series=series, diagnostics=diagnostics)
    repeated = store.externalize(
        series=dict(reversed(list(series.items()))), diagnostics=diagnostics
    )

    assert repeated.content_hash == external.content_hash
    assert repeated.storage_uri == external.storage_uri
    assert external.storage_format == PAYLOAD_STORAGE_FORMAT
    assert external.schema_version == PAYLOAD_SCHEMA_VERSION
    assert external.series_summary["collections"]["model_scores"] == {
        "kind": "list",
        "count": 2,
    }
    assert "large_debug_trace" not in external.diagnostics_summary
    payload_path = tmp_path / f"{external.content_hash}.parquet"
    assert payload_path.stat().st_size == external.byte_size
    metadata = pq.ParquetFile(payload_path).metadata
    assert metadata.row_group(0).column(2).compression == "ZSTD"

    hydrated = hydrate_cell_result_row(
        {
            "artifact_id": "result-1",
            "series": external.series_summary,
            "diagnostics": external.diagnostics_summary,
            "payload_storage_uri": external.storage_uri,
            "payload_content_hash": external.content_hash,
            "payload_storage_format": external.storage_format,
            "payload_schema_version": external.schema_version,
            "payload_byte_size": external.byte_size,
        },
        store=store,
    )
    assert hydrated["artifact_id"] == "result-1"
    assert hydrated["series"] == series
    assert hydrated["diagnostics"] == diagnostics


def test_historical_inline_payload_remains_readable(tmp_path) -> None:
    row = {
        "series": {"nav_series": [{"nav_date": "2026-08-07", "strategy_wealth": 1.0}]},
        "diagnostics": {"quality_checks": []},
        "payload_storage_uri": None,
        "payload_content_hash": None,
    }

    hydrated = hydrate_cell_result_row(row, store=CellResultPayloadStore(tmp_path))

    assert hydrated["series"] == row["series"]
    assert hydrated["diagnostics"] == row["diagnostics"]
    assert hydrated is not row


def test_external_payload_reference_mismatch_fails_closed(tmp_path) -> None:
    store = CellResultPayloadStore(tmp_path)
    external = store.externalize(series={"model_scores": []}, diagnostics={})

    with pytest.raises(CellResultPayloadError, match="URI does not match"):
        store.load(
            storage_uri=f"cell-result://sha256/{'f' * 64}.parquet",
            content_hash=external.content_hash,
            storage_format=external.storage_format,
            schema_version=external.schema_version,
        )


def test_publication_lease_pins_payload_until_explicit_finalize(tmp_path) -> None:
    store = CellResultPayloadStore(tmp_path)
    work_item_id = uuid.uuid4()

    lease = store.stage_publication(
        series={"model_scores": []},
        diagnostics={"executor": "test"},
        owner_work_item_id=work_item_id,
    )

    marker = tmp_path / lease.marker_name
    assert marker.is_file()
    assert store.pending_content_hashes() == {lease.payload.content_hash}
    store.finalize_publication(lease)
    assert not marker.exists()
    store.finalize_publication(lease)


@pytest.mark.parametrize("publication_fails", [False, True])
def test_materializer_marker_spans_publication_and_clears_after_return_or_rollback(
    tmp_path, publication_fails: bool
) -> None:
    store = CellResultPayloadStore(tmp_path)
    materializer = CellResultMaterializer(create_engine("sqlite://"), payload_store=store)
    artifact_id = uuid.uuid4()

    class RecordingArtifactService:
        def publish(self, **_kwargs):
            assert len(store.pending_content_hashes()) == 1
            if publication_fails:
                raise RuntimeError("simulated transaction rollback")
            return SimpleNamespace(artifact_id=artifact_id)

    materializer._artifacts = RecordingArtifactService()  # type: ignore[assignment]
    request = CellExecutionRequest(
        work_item_id=uuid.uuid4(),
        cell_artifact_id=uuid.uuid4(),
        result_type="predictive",
        cell_specification={},
    )
    output = CellExecutionOutput(
        availability_status="accepted",
        quality_status="passed",
        metrics={},
        series={"model_scores": []},
        diagnostics={},
    )

    if publication_fails:
        with pytest.raises(RuntimeError, match="rollback"):
            materializer.complete(request=request, output=output, worker_id="worker")
    else:
        assert (
            materializer.complete(request=request, output=output, worker_id="worker")
            == artifact_id
        )
    assert not store.pending_content_hashes()
