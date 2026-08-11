from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from pathlib import Path

import pandas as pd

from style_rotation.workspace.materialization import WorkspaceSignalMaterializer


def test_cached_signal_view_reads_only_requested_observation_window(tmp_path: Path) -> None:
    cache_key = "a" * 64
    signal_keys = ("momentum", "skew")
    asset_id = uuid.uuid4()
    rows = [
        {
            "signal_version_key": signal_key,
            "asset_id": str(asset_id),
            "asset_key": "aapl",
            "date": observation_date,
            "score": score,
        }
        for signal_key in signal_keys
        for observation_date, score in (
            ("2026-07-17", "0.1"),
            ("2026-07-31", "0.2"),
            ("2026-08-01", "0.3"),
        )
    ]
    pd.DataFrame(rows).to_parquet(tmp_path / f"{cache_key}.parquet", index=False)
    specifications = [
        {
            "signal_key": key,
            "economic_family": key,
            "signal_artifact_id": uuid.uuid4(),
            "factor_artifact_id": uuid.uuid4(),
            "version_number": 1,
            "normalization": "cross_sectional_rank",
            "tie_policy": "average",
            "direction": "higher_is_better",
        }
        for key in signal_keys
    ]
    observed_cache_rows: list[int] = []
    materializer = object.__new__(WorkspaceSignalMaterializer)
    materializer._cache_directory = tmp_path
    materializer._record_cache = lambda **values: observed_cache_rows.append(  # type: ignore[method-assign]
        int(values["row_count"])
    )

    result = materializer._read_cache(
        cache_key,
        uuid.uuid4(),
        uuid.uuid4(),
        specifications,
        {},
        uuid.uuid4(),
        observation_start=date(2026, 7, 24),
        observation_end=date(2026, 7, 31),
    )

    assert result is not None
    assert result.cache_hit is True
    assert observed_cache_rows == [2]
    assert set(result.signals) == set(signal_keys)
    for points in result.signals.values():
        assert points == {(asset_id, date(2026, 7, 31)): ("aapl", Decimal("0.2"))}
