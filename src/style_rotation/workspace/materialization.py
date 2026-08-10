from __future__ import annotations

import json
import math
import os
import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

import pandas as pd
from sqlalchemy import Engine, bindparam, text

from style_rotation.core.canonical import sha256_hexdigest
from style_rotation.data.bundle import publish_data_bundle
from style_rotation.factor.calculator import IMPLEMENTATIONS, FactorBar
from style_rotation.lineage.service import ArtifactService, DependencyInput
from style_rotation.signal.calculator import (
    FactorValueInput,
    SignalVersionInput,
    calculate_signal,
)

MATERIALIZER_VERSION = "workspace_signal_materializer_v1"


@dataclass(frozen=True, slots=True)
class MaterializedSignals:
    bundle_version_id: uuid.UUID
    bundle_artifact_id: uuid.UUID
    cache_key: str
    cache_hit: bool
    signals: dict[str, dict[tuple[uuid.UUID, date], tuple[str, Decimal]]]
    dimensions: dict[str, str]
    source_ids: list[str]
    metadata: dict[str, dict[str, Any]]
    materialization_artifact_id: uuid.UUID


class WorkspaceSignalMaterializer:
    """Calculate the selected Factor -> Signal graph from one frozen market bundle.

    The relational Factor/Signal dataset tables remain the publication boundary for
    canonical releases. Ad-hoc Workspace selections are cached as content-addressed
    Parquet instead of creating a new database snapshot after every click.
    """

    def __init__(self, engine: Engine, cache_directory: Path | None = None) -> None:
        self._engine = engine
        self._artifacts = ArtifactService(engine)
        self._cache_directory = cache_directory or Path(
            os.environ.get(
                "STYLE_ROTATION_RESEARCH_CACHE_DIRECTORY",
                "artifacts/research_materialization_cache",
            )
        )

    def latest_compatible_bundle(
        self, asset_ids: tuple[uuid.UUID, ...]
    ) -> tuple[uuid.UUID, uuid.UUID]:
        if not asset_ids:
            raise ValueError("Materialization requires selected assets")
        with self._engine.connect() as connection:
            market = (
                connection.execute(
                    text(
                        """
                        SELECT publication.dataset_publication_id,
                               publication.artifact_id, artifact.created_at
                        FROM data.dataset_publication publication
                        JOIN lineage.artifact artifact
                          ON artifact.artifact_id = publication.artifact_id
                         AND artifact.status = 'published'
                        JOIN data.daily_bar bar
                          ON bar.dataset_publication_id = publication.dataset_publication_id
                        WHERE bar.asset_id IN :asset_ids
                        GROUP BY publication.dataset_publication_id,
                                 publication.artifact_id, artifact.created_at
                        HAVING count(DISTINCT bar.asset_id) = :asset_count
                        ORDER BY artifact.created_at DESC
                        LIMIT 1
                        """
                    ).bindparams(bindparam("asset_ids", expanding=True)),
                    {"asset_ids": asset_ids, "asset_count": len(asset_ids)},
                )
                .mappings()
                .one_or_none()
            )
            if market is None:
                raise LookupError("No published market dataset covers every selected asset")
            existing = (
                connection.execute(
                    text(
                        """
                        SELECT bundle.data_bundle_version_id, bundle.artifact_id
                        FROM data.data_bundle_version bundle
                        JOIN lineage.artifact artifact
                          ON artifact.artifact_id = bundle.artifact_id
                         AND artifact.status = 'published'
                        JOIN data.data_bundle_member member
                          ON member.data_bundle_version_id = bundle.data_bundle_version_id
                         AND member.role = 'canonical_market'
                        WHERE member.dataset_publication_id = :publication_id
                        ORDER BY artifact.created_at DESC LIMIT 1
                        """
                    ),
                    {"publication_id": market["dataset_publication_id"]},
                )
                .mappings()
                .one_or_none()
            )
            if existing is not None:
                return existing["data_bundle_version_id"], existing["artifact_id"]
            dependencies = connection.execute(
                text(
                    """
                    SELECT
                      (SELECT publication.artifact_id
                       FROM data.dataset_publication publication
                       JOIN lineage.artifact artifact
                         ON artifact.artifact_id = publication.artifact_id
                        AND artifact.status = 'published'
                       WHERE publication.dataset_key = 'dgs3mo_canonical'
                       ORDER BY artifact.created_at DESC LIMIT 1) AS rate_artifact_id,
                      (SELECT publication.artifact_id
                       FROM data.dataset_publication publication
                       JOIN lineage.artifact artifact
                         ON artifact.artifact_id = publication.artifact_id
                        AND artifact.status = 'published'
                       WHERE publication.dataset_key = 'dgs3mo_reserve_return'
                       ORDER BY artifact.created_at DESC LIMIT 1) AS reserve_artifact_id,
                      (SELECT version.artifact_id
                       FROM catalog.calendar_version version
                       JOIN lineage.artifact artifact
                         ON artifact.artifact_id = version.artifact_id
                        AND artifact.status = 'published'
                       ORDER BY artifact.created_at DESC LIMIT 1) AS calendar_artifact_id,
                      (SELECT COALESCE(max(version_number), 0) + 1
                       FROM data.data_bundle_version) AS next_version
                    """
                )
            ).mappings().one()
        if any(dependencies[key] is None for key in (
            "rate_artifact_id", "reserve_artifact_id", "calendar_artifact_id"
        )):
            raise LookupError("Cannot freeze research bundle: calendar or reserve inputs missing")
        _definition, version = publish_data_bundle(
            self._engine,
            market["artifact_id"],
            dependencies["rate_artifact_id"],
            dependencies["reserve_artifact_id"],
            dependencies["calendar_artifact_id"],
            version_number=dependencies["next_version"],
        )
        with self._engine.connect() as connection:
            bundle_id = connection.execute(
                text(
                    "SELECT data_bundle_version_id FROM data.data_bundle_version "
                    "WHERE artifact_id = :artifact"
                ),
                {"artifact": version.artifact_id},
            ).scalar_one()
        return bundle_id, version.artifact_id

    def materialize(
        self,
        *,
        signal_version_keys: tuple[str, ...],
        asset_ids: tuple[uuid.UUID, ...],
        frequency: Literal["weekly", "monthly"],
        bundle_version_id: uuid.UUID | None = None,
        observation_start: date | None = None,
        observation_end: date | None = None,
        allow_calculation: bool = True,
    ) -> MaterializedSignals:
        if not signal_version_keys or not asset_ids:
            raise ValueError("Materialization requires selected assets and Signals")
        if len(signal_version_keys) != len(set(signal_version_keys)):
            raise ValueError("Signal selections must be unique")
        if len(asset_ids) != len(set(asset_ids)):
            raise ValueError("Asset selections must be unique")
        if (observation_start is None) != (observation_end is None):
            raise ValueError("Materialization observation bounds must be provided together")
        if (
            observation_start is not None
            and observation_end is not None
            and observation_start > observation_end
        ):
            raise ValueError("Materialization observation start must not exceed its end")
        with self._engine.connect() as connection:
            if bundle_version_id is None:
                bundle_version_id, bundle_artifact_id = self.latest_compatible_bundle(asset_ids)
            else:
                bundle_artifact_id = connection.execute(
                    text(
                        "SELECT artifact_id FROM data.data_bundle_version "
                        "WHERE data_bundle_version_id = :bundle"
                    ),
                    {"bundle": bundle_version_id},
                ).scalar_one()
            bundle_hash = connection.execute(
                text("SELECT content_hash FROM lineage.artifact WHERE artifact_id = :artifact"),
                {"artifact": bundle_artifact_id},
            ).scalar_one()
            specifications = self._signal_specifications(connection, signal_version_keys)
            semantic = {
                "materializer_version": MATERIALIZER_VERSION,
                "bundle_content_hash": bundle_hash,
                "asset_ids": sorted(str(item) for item in asset_ids),
                "frequency": frequency,
                "signals": [
                    {
                        "key": item["signal_key"],
                        "signal_artifact_hash": item["signal_artifact_hash"],
                        "factor_artifact_hash": item["factor_artifact_hash"],
                        "missing_policy": "null_preserved_no_cross_asset_fill",
                    }
                    for item in specifications
                ],
            }
            cache_key = sha256_hexdigest(semantic)
            materialization_artifact_id = self._publish_materialization_recipe(
                cache_key=cache_key,
                semantic=semantic,
                bundle_artifact_id=bundle_artifact_id,
                specifications=specifications,
            )
            cached = self._read_cache(
                cache_key,
                bundle_version_id,
                bundle_artifact_id,
                specifications,
                semantic,
                materialization_artifact_id,
                observation_start=observation_start,
                observation_end=observation_end,
            )
            if cached is not None:
                return cached
            if not allow_calculation:
                raise LookupError(
                    "Cached Product Signal materialization is unavailable; "
                    "a background monitoring run must rebuild it"
                )
            bars_by_asset = self._bars(connection, bundle_version_id, asset_ids)
        result = self._calculate(
            specifications=specifications,
            bars_by_asset=bars_by_asset,
            bundle_version_id=bundle_version_id,
            bundle_artifact_id=bundle_artifact_id,
            cache_key=cache_key,
            materialization_artifact_id=materialization_artifact_id,
        )
        self._write_cache(result, semantic)
        return result

    def _publish_materialization_recipe(
        self,
        *,
        cache_key: str,
        semantic: dict[str, Any],
        bundle_artifact_id: uuid.UUID,
        specifications: list[dict[str, Any]],
    ) -> uuid.UUID:
        """Publish the reproducible recipe, not a fake canonical Signal Dataset."""

        dependencies: list[DependencyInput] = [
            DependencyInput(bundle_artifact_id, "data_bundle", 0)
        ]
        for ordinal, item in enumerate(specifications, start=1):
            dependencies.append(
                DependencyInput(item["signal_artifact_id"], "signal_version", ordinal)
            )
            dependencies.append(
                DependencyInput(
                    item["factor_artifact_id"],
                    "factor_variant",
                    ordinal + len(specifications),
                )
            )
        result = self._artifacts.publish(
            artifact_type="workspace_signal_materialization",
            artifact_key=cache_key,
            version_number=1,
            semantic_payload=semantic,
            content_payload={
                "cache_key": cache_key,
                "materializer_version": MATERIALIZER_VERSION,
                "storage_contract": "content_addressed_parquet_recomputable_cache",
            },
            dependencies=tuple(dependencies),
            reason="publish reproducible Workspace Signal materialization recipe",
        )
        return result.artifact_id

    @staticmethod
    def _signal_specifications(
        connection: Any, signal_version_keys: tuple[str, ...]
    ) -> list[dict[str, Any]]:
        rows = connection.execute(
            text(
                """
                SELECT DISTINCT ON (definition.signal_key)
                       definition.signal_key, definition.economic_family,
                       version.signal_version_id, version.artifact_id AS signal_artifact_id,
                       version.version_number, version.direction, version.normalization,
                       version.extreme_policy, version.missing_policy, version.tie_policy,
                       version.output_type, version.rule,
                       signal_artifact.content_hash AS signal_artifact_hash,
                       variant.factor_variant_id, variant.artifact_id AS factor_artifact_id,
                       variant.variant_key, variant.parameters,
                       factor_version.implementation_key,
                       factor_artifact.content_hash AS factor_artifact_hash
                FROM signal.signal_definition definition
                JOIN signal.signal_version version
                  ON version.signal_definition_id = definition.signal_definition_id
                JOIN lineage.artifact signal_artifact
                  ON signal_artifact.artifact_id = version.artifact_id
                 AND signal_artifact.status = 'published'
                JOIN factor.factor_variant variant
                  ON variant.factor_variant_id = version.factor_variant_id
                JOIN factor.factor_definition_version factor_version
                  ON factor_version.factor_definition_version_id =
                     variant.factor_definition_version_id
                JOIN lineage.artifact factor_artifact
                  ON factor_artifact.artifact_id = variant.artifact_id
                 AND factor_artifact.status = 'published'
                WHERE definition.signal_key IN :keys
                ORDER BY definition.signal_key, version.version_number DESC
                """
            ).bindparams(bindparam("keys", expanding=True)),
            {"keys": signal_version_keys},
        ).mappings().all()
        found = {row["signal_key"] for row in rows}
        missing = sorted(set(signal_version_keys).difference(found))
        if missing:
            raise LookupError(f"Published Signal definitions not found: {', '.join(missing)}")
        indexed = {row["signal_key"]: dict(row) for row in rows}
        return [indexed[key] for key in signal_version_keys]

    @staticmethod
    def _bars(
        connection: Any,
        bundle_version_id: uuid.UUID,
        asset_ids: tuple[uuid.UUID, ...],
    ) -> dict[uuid.UUID, tuple[FactorBar, ...]]:
        rows = connection.execute(
            text(
                """
                SELECT bar.asset_id, asset.asset_key, bar.session_date,
                       bar.close_adj, bar.close_raw, bar.volume_raw,
                       bar.open_raw, bar.high_raw, bar.low_raw,
                       bar.open_adj, bar.high_adj, bar.low_adj
                FROM data.data_bundle_member member
                JOIN data.daily_bar bar
                  ON bar.dataset_publication_id = member.dataset_publication_id
                JOIN catalog.asset asset ON asset.asset_id = bar.asset_id
                WHERE member.data_bundle_version_id = :bundle
                  AND member.role = 'canonical_market'
                  AND bar.asset_id IN :assets
                ORDER BY bar.asset_id, bar.session_date
                """
            ).bindparams(bindparam("assets", expanding=True)),
            {"bundle": bundle_version_id, "assets": asset_ids},
        ).mappings().all()
        grouped: dict[uuid.UUID, list[FactorBar]] = defaultdict(list)
        for row in rows:
            grouped[row["asset_id"]].append(
                FactorBar(
                    row["asset_id"],
                    row["asset_key"],
                    row["session_date"],
                    row["close_adj"],
                    row["close_raw"],
                    row["volume_raw"],
                    row["open_raw"],
                    row["high_raw"],
                    row["low_raw"],
                    row["open_adj"],
                    row["high_adj"],
                    row["low_adj"],
                )
            )
        missing = sorted(str(item) for item in set(asset_ids).difference(grouped))
        if missing:
            raise LookupError(
                f"Selected assets have no market bars in the frozen bundle: {missing}"
            )
        return {key: tuple(value) for key, value in grouped.items()}

    def _calculate(
        self,
        *,
        specifications: list[dict[str, Any]],
        bars_by_asset: dict[uuid.UUID, tuple[FactorBar, ...]],
        bundle_version_id: uuid.UUID,
        bundle_artifact_id: uuid.UUID,
        cache_key: str,
        materialization_artifact_id: uuid.UUID,
    ) -> MaterializedSignals:
        factor_cache: dict[uuid.UUID, tuple[FactorValueInput, ...]] = {}
        signals: dict[str, dict[tuple[uuid.UUID, date], tuple[str, Decimal]]] = {}
        dimensions: dict[str, str] = {}
        metadata: dict[str, dict[str, Any]] = {}
        for item in specifications:
            variant_id = item["factor_variant_id"]
            factor_points = factor_cache.get(variant_id)
            if factor_points is None:
                calculator = IMPLEMENTATIONS.get(item["implementation_key"])
                if calculator is None:
                    raise LookupError(
                        f"Unsupported Factor implementation: {item['implementation_key']}"
                    )
                calculated: list[FactorValueInput] = []
                for asset_id, bars in bars_by_asset.items():
                    values = calculator(bars, item["parameters"])
                    calculated.extend(
                        FactorValueInput(asset_id, bar.asset_key, bar.session_date, value)
                        for bar, value in zip(bars, values, strict=True)
                        if value is not None and math.isfinite(value)
                    )
                factor_points = tuple(calculated)
                factor_cache[variant_id] = factor_points
            version = SignalVersionInput(
                item["signal_version_id"],
                item["signal_artifact_id"],
                item["signal_key"],
                item["factor_variant_id"],
                item["direction"],
                item["normalization"],
                item["extreme_policy"],
                item["missing_policy"],
                item["tie_policy"],
                item["output_type"],
                item["rule"],
            )
            calculation = calculate_signal(version, factor_points)
            signals[item["signal_key"]] = {
                (point.asset_id, point.observation_date): (point.asset_key, point.score)
                for point in calculation.points
            }
            dimensions[item["signal_key"]] = item["economic_family"]
            metadata[item["signal_key"]] = {
                "source": "workspace_on_demand_materialization",
                "signal_version_artifact_id": str(item["signal_artifact_id"]),
                "factor_variant_artifact_id": str(item["factor_artifact_id"]),
                "version_number": item["version_number"],
                "published_normalization": item["normalization"],
                "published_tie_policy": item["tie_policy"],
                "direction": item["direction"],
                "materialization_cache_key": cache_key,
                "materialization_artifact_id": str(materialization_artifact_id),
            }
        return MaterializedSignals(
            bundle_version_id,
            bundle_artifact_id,
            cache_key,
            False,
            signals,
            dimensions,
            [str(materialization_artifact_id)],
            metadata,
            materialization_artifact_id,
        )

    def _read_cache(
        self,
        cache_key: str,
        bundle_version_id: uuid.UUID,
        bundle_artifact_id: uuid.UUID,
        specifications: list[dict[str, Any]],
        semantic: dict[str, Any],
        materialization_artifact_id: uuid.UUID,
        *,
        observation_start: date | None = None,
        observation_end: date | None = None,
    ) -> MaterializedSignals | None:
        parquet_path = self._cache_directory / f"{cache_key}.parquet"
        if not parquet_path.exists():
            return None
        filters = (
            [
                ("date", ">=", observation_start.isoformat()),
                ("date", "<=", observation_end.isoformat()),
            ]
            if observation_start is not None and observation_end is not None
            else None
        )
        frame = pd.read_parquet(parquet_path, filters=filters)
        self._record_cache(
            cache_key=cache_key,
            bundle_artifact_id=bundle_artifact_id,
            parquet_path=parquet_path,
            row_count=len(frame),
            semantic=semantic,
        )
        signals: dict[str, dict[tuple[uuid.UUID, date], tuple[str, Decimal]]] = defaultdict(dict)
        for row in frame.to_dict(orient="records"):
            signals[str(row["signal_version_key"])][
                (uuid.UUID(str(row["asset_id"])), date.fromisoformat(str(row["date"])))
            ] = (str(row["asset_key"]), Decimal(str(row["score"])))
        dimensions = {item["signal_key"]: item["economic_family"] for item in specifications}
        metadata = {
            item["signal_key"]: {
                "source": "workspace_content_addressed_cache",
                "signal_version_artifact_id": str(item["signal_artifact_id"]),
                "factor_variant_artifact_id": str(item["factor_artifact_id"]),
                "version_number": item["version_number"],
                "published_normalization": item["normalization"],
                "published_tie_policy": item["tie_policy"],
                "direction": item["direction"],
                "materialization_cache_key": cache_key,
                "materialization_artifact_id": str(materialization_artifact_id),
            }
            for item in specifications
        }
        return MaterializedSignals(
            bundle_version_id,
            bundle_artifact_id,
            cache_key,
            True,
            dict(signals),
            dimensions,
            [str(materialization_artifact_id)],
            metadata,
            materialization_artifact_id,
        )

    def _write_cache(self, result: MaterializedSignals, semantic: dict[str, Any]) -> None:
        self._cache_directory.mkdir(parents=True, exist_ok=True)
        rows = [
            {
                "signal_version_key": key,
                "asset_id": str(asset_id),
                "asset_key": asset_key,
                "date": day.isoformat(),
                "score": str(score),
            }
            for key, points in result.signals.items()
            for (asset_id, day), (asset_key, score) in points.items()
        ]
        parquet_path = self._cache_directory / f"{result.cache_key}.parquet"
        temporary_path = self._cache_directory / f"{result.cache_key}.{uuid.uuid4().hex}.tmp"
        pd.DataFrame(rows).to_parquet(
            temporary_path, engine="pyarrow", compression="zstd", index=False
        )
        temporary_path.replace(parquet_path)
        manifest_path = self._cache_directory / f"{result.cache_key}.manifest.json"
        manifest_path.write_text(
            json.dumps(
                {
                    "schema_version": MATERIALIZER_VERSION,
                    "cache_key": result.cache_key,
                    "row_count": len(rows),
                    "semantic": semantic,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        self._record_cache(
            cache_key=result.cache_key,
            bundle_artifact_id=result.bundle_artifact_id,
            parquet_path=parquet_path,
            row_count=len(rows),
            semantic=semantic,
        )

    def _record_cache(
        self,
        *,
        cache_key: str,
        bundle_artifact_id: uuid.UUID,
        parquet_path: Path,
        row_count: int,
        semantic: dict[str, Any],
    ) -> None:
        with self._engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO workspace.research_materialization_cache (
                        cache_key, data_bundle_artifact_id, materializer_version,
                        storage_uri, row_count, semantic
                    ) VALUES (
                        :cache_key, :bundle_artifact_id, :version,
                        :storage_uri, :row_count, CAST(:semantic AS jsonb)
                    )
                    ON CONFLICT (cache_key) DO UPDATE SET
                        last_accessed_at = now()
                    """
                ),
                {
                    "cache_key": cache_key,
                    "bundle_artifact_id": bundle_artifact_id,
                    "version": MATERIALIZER_VERSION,
                    "storage_uri": parquet_path.as_posix(),
                    "row_count": row_count,
                    "semantic": json.dumps(semantic, ensure_ascii=False),
                },
            )
