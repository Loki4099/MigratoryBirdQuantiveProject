from __future__ import annotations

import io
import json
import uuid
import zipfile
from dataclasses import dataclass
from typing import Any, Literal

import pandas as pd
from sqlalchemy import Engine, bindparam, text

from style_rotation.ops.v021_execution import V021DatabaseExecutor
from style_rotation.workspace.materialization import WorkspaceSignalMaterializer


@dataclass(frozen=True, slots=True)
class SignalResearchExport:
    content: bytes
    filename: str


class SignalResearchExportService:
    """Build a model-research package containing only selected Signal inputs and labels."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._materializer = WorkspaceSignalMaterializer(engine)

    def build(
        self,
        *,
        security_ids: tuple[uuid.UUID, ...],
        asset_data_inputs: dict[uuid.UUID, tuple[str, ...]],
        signal_version_keys: tuple[str, ...],
        frequency: Literal["weekly", "monthly"],
        include_targets: bool,
    ) -> SignalResearchExport:
        if not security_ids or not signal_version_keys:
            raise ValueError("Signal export requires selected assets and selected Signals")
        if len(security_ids) != len(set(security_ids)) or len(signal_version_keys) != len(
            set(signal_version_keys)
        ):
            raise ValueError("Signal export selections must be unique")
        if set(asset_data_inputs) != set(security_ids):
            raise ValueError("Signal export data inputs must exactly match selected assets")
        with self._engine.connect() as connection:
            assets = connection.execute(
                text(
                    """
                    SELECT security.security_id, asset.asset_id, asset.asset_key,
                           COALESCE(symbol.symbol, asset.asset_key) AS symbol
                    FROM catalog.security security
                    JOIN catalog.asset asset ON asset.asset_id = security.legacy_asset_id
                    LEFT JOIN LATERAL (
                      SELECT listing_symbol.symbol FROM catalog.asset_listing listing
                      JOIN catalog.listing_symbol
                        ON listing_symbol.asset_listing_id = listing.asset_listing_id
                      WHERE listing.asset_id = asset.asset_id
                        AND listing_symbol.symbol_type = 'ticker'
                      ORDER BY listing_symbol.valid_to NULLS FIRST,
                               listing_symbol.valid_from DESC NULLS LAST LIMIT 1
                    ) symbol ON true
                    WHERE security.security_id IN :security_ids
                    ORDER BY asset.asset_key
                    """
                ).bindparams(bindparam("security_ids", expanding=True)),
                {"security_ids": security_ids},
            ).mappings().all()
            if len(assets) != len(security_ids):
                raise ValueError("Every exported Security must map to canonical market data")
            asset_ids = tuple(row["asset_id"] for row in assets)
            asset_metadata = {row["asset_id"]: dict(row) for row in assets}
        materialized = self._materializer.materialize(
            signal_version_keys=signal_version_keys,
            asset_ids=asset_ids,
            frequency=frequency,
        )
        dataset_rows = [
            {
                "date": day,
                "asset_id": str(asset_id),
                "symbol": asset_metadata[asset_id]["symbol"],
                "signal_version_key": version_key,
                "value": float(score),
            }
            for version_key, points in materialized.signals.items()
            for (asset_id, day), (_asset_key, score) in points.items()
        ]
        if not dataset_rows:
            raise LookupError("Selected Signals have no values for the selected assets")
        signal_frame = pd.DataFrame(dataset_rows).pivot(
            index=["date", "asset_id", "symbol"],
            columns="signal_version_key",
            values="value",
        ).reset_index()
        signal_frame.columns.name = None
        signal_frame = signal_frame.sort_values(["date", "symbol", "asset_id"])
        bundle_id = materialized.bundle_version_id
        target_frame = pd.DataFrame(columns=["decision_date", "asset_id", "symbol"])
        if include_targets:
            target_rows: dict[tuple[Any, str, str], dict[str, Any]] = {}
            executor = V021DatabaseExecutor(self._engine)
            for kind in ("future_return", "cross_sectional_relative_return"):
                for horizon in (5, 21, 63):
                    target_key = f"{kind}__h{horizon}"
                    points, _source = executor._session_horizon_return_points(
                        target_key, bundle_id, asset_ids, frequency=frequency
                    )
                    for day, target_values in points.items():
                        for asset_id, value in target_values.items():
                            identity = (
                                day,
                                str(asset_id),
                                str(asset_metadata[asset_id]["symbol"]),
                            )
                            target_rows.setdefault(
                                identity,
                                {
                                    "decision_date": day,
                                    "asset_id": str(asset_id),
                                    "symbol": asset_metadata[asset_id]["symbol"],
                                },
                            )[target_key] = float(value)
            if target_rows:
                target_frame = pd.DataFrame(target_rows.values()).sort_values(
                    ["decision_date", "symbol", "asset_id"]
                )
        manifest = {
            "schema_version": "signal_research_export_v1",
            "frequency": frequency,
            "signal_columns_only": True,
            "targets_are_labels_not_model_inputs": True,
            "asset_count": len(assets),
            "signal_count": len(signal_version_keys),
            "assets": [
                {
                    "security_id": str(row["security_id"]),
                    "asset_id": str(row["asset_id"]),
                    "asset_key": row["asset_key"],
                    "symbol": row["symbol"],
                    "selected_data_inputs": list(asset_data_inputs[row["security_id"]]),
                }
                for row in assets
            ],
            "signals": [
                {
                    "signal_version_key": key,
                    **materialized.metadata[key],
                }
                for key in signal_version_keys
            ],
            "materialization": {
                "cache_key": materialized.cache_key,
                "bundle_artifact_id": str(materialized.bundle_artifact_id),
            },
            "target_keys": (
                [
                    f"{kind}__h{horizon}"
                    for kind in ("future_return", "cross_sectional_relative_return")
                    for horizon in (5, 21, 63)
                ]
                if include_targets
                else []
            ),
            "missing_policy": "null_preserved_no_cross_asset_fill",
            "warnings": [
                "current_snapshot_asset_selection_may_have_survivorship_bias",
                "terminal_event_coverage_is_not_formal",
            ],
        }
        dictionary = {
            "identity_columns": ["date/decision_date", "asset_id", "symbol"],
            "signal_columns": list(signal_version_keys),
            "target_columns": manifest["target_keys"],
        }
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            _write_zip_member(archive, "signals.parquet", _parquet_bytes(signal_frame))
            if include_targets:
                _write_zip_member(archive, "targets.parquet", _parquet_bytes(target_frame))
            _write_zip_member(
                archive,
                "manifest.json",
                json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8"),
            )
            _write_zip_member(
                archive,
                "data_dictionary.json",
                json.dumps(dictionary, ensure_ascii=False, indent=2, sort_keys=True).encode(
                    "utf-8"
                ),
            )
        return SignalResearchExport(output.getvalue(), "migratory_bird_signal_research.zip")


def _parquet_bytes(frame: pd.DataFrame) -> bytes:
    output = io.BytesIO()
    frame.to_parquet(output, engine="pyarrow", compression="zstd", index=False)
    return output.getvalue()


def _write_zip_member(archive: zipfile.ZipFile, filename: str, content: bytes) -> None:
    member = zipfile.ZipInfo(filename, date_time=(1980, 1, 1, 0, 0, 0))
    member.compress_type = zipfile.ZIP_DEFLATED
    member.create_system = 3
    member.external_attr = 0o644 << 16
    archive.writestr(member, content)
