from __future__ import annotations

import io
import json
import uuid
import zipfile
from contextlib import nullcontext
from types import SimpleNamespace
from typing import Any

import pandas as pd

from style_rotation.signal.research_export import SignalResearchExportService


class _Result:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def mappings(self) -> _Result:
        return self

    def all(self) -> list[dict[str, Any]]:
        return self._rows


class _Connection:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def execute(self, *_args: Any, **_kwargs: Any) -> _Result:
        return _Result(self._rows)


class _Engine:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._connection = _Connection(rows)

    def connect(self) -> nullcontext[_Connection]:
        return nullcontext(self._connection)


class _Materializer:
    def __init__(self, asset_id: uuid.UUID, signal_key: str) -> None:
        self.asset_id = asset_id
        self.signal_key = signal_key
        self.bundle_version_id = uuid.uuid4()
        self.bundle_artifact_id = uuid.uuid4()

    def materialize(self, **kwargs: Any) -> SimpleNamespace:
        assert kwargs["signal_version_keys"] == (self.signal_key,)
        assert kwargs["asset_ids"] == (self.asset_id,)
        return SimpleNamespace(
            signals={self.signal_key: {(self.asset_id, "2026-08-03"): ("aapl", 0.25)}},
            bundle_version_id=self.bundle_version_id,
            cache_key="cache",
            cache_hit=False,
            bundle_artifact_id=self.bundle_artifact_id,
            metadata={self.signal_key: {"factor_variant_key": "total_return__w20"}},
        )


def test_signal_research_export_contains_only_selected_signals_and_input_lineage() -> None:
    security_id = uuid.uuid4()
    asset_id = uuid.uuid4()
    signal_key = "return_continuation__total_return__w20"
    rows = [
        {
            "security_id": security_id,
            "asset_id": asset_id,
            "asset_key": "aapl",
            "symbol": "AAPL",
        }
    ]
    service = object.__new__(SignalResearchExportService)
    service._engine = _Engine(rows)  # type: ignore[assignment]
    service._materializer = _Materializer(asset_id, signal_key)  # type: ignore[assignment]

    package = service.build(
        security_ids=(security_id,),
        asset_data_inputs={security_id: ("canonical_market_bars",)},
        signal_version_keys=(signal_key,),
        frequency="weekly",
        include_targets=False,
    )
    repeated = service.build(
        security_ids=(security_id,),
        asset_data_inputs={security_id: ("canonical_market_bars",)},
        signal_version_keys=(signal_key,),
        frequency="weekly",
        include_targets=False,
    )

    with zipfile.ZipFile(io.BytesIO(package.content)) as archive:
        frame = pd.read_parquet(io.BytesIO(archive.read("signals.parquet")))
        manifest = json.loads(archive.read("manifest.json"))
    assert list(frame.columns) == ["date", "asset_id", "symbol", signal_key]
    assert manifest["signals"] == [
        {"signal_version_key": signal_key, "factor_variant_key": "total_return__w20"}
    ]
    assert manifest["assets"][0]["selected_data_inputs"] == ["canonical_market_bars"]
    assert repeated.content == package.content
