from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import date
from decimal import Decimal
from typing import Any, cast

from sqlalchemy import Engine, bindparam, text
from sqlalchemy.engine import RowMapping

from style_rotation.experiment.compare import classify_comparison
from style_rotation.experiment.ranking import RankableValue, competition_ranks
from style_rotation.experiment.result_payload import hydrate_cell_result_row
from style_rotation.workspace.options import Frequency, build_workspace_options
from style_rotation.workspace.preview import build_compile_preview

_RANKING_METRICS = {
    "net_sharpe": ("strategy", "absolute", "sharpe_ratio", "higher_is_better"),
    "net_cagr": ("strategy", "absolute", "cagr", "higher_is_better"),
    "relative_wealth_growth": (
        "relative",
        "relative",
        "annualized_relative_wealth_growth",
        "higher_is_better",
    ),
    "maximum_drawdown": ("strategy", "absolute", "maximum_drawdown", "higher_is_better"),
    "calmar": ("strategy", "absolute", "calmar_ratio", "higher_is_better"),
}


def _v021_overview_item(source: Any) -> dict[str, Any]:
    row = dict(source)
    run_status = row.get("run_status")
    result_id = row.get("result_artifact_id")
    if result_id is not None and row.get("availability_status") == "accepted":
        status = "accepted"
    elif result_id is not None or run_status == "failed":
        status = "failed"
    elif run_status == "running":
        status = "running"
    else:
        status = "pending"
    return {
        key: row.get(key)
        for key in (
            "artifact_id",
            "result_artifact_id",
            "suite_artifact_id",
            "suite_mode",
            "cell_key",
            "ordinal",
            "product_key",
            "model_specification_key",
            "variant_key",
            "frequency",
            "benchmark_key",
            "benchmark_category",
            "cost_bps_per_side",
            "template_key",
            "initialization_policy",
            "as_of_date",
            "simulation_end",
            "availability_status",
            "quality_status",
            "attempt_number",
            "error_summary",
        )
    } | {"status": status, "core_metrics": _v021_core_metrics(row.get("metrics") or {})}


def _v021_core_metrics(metrics: dict[str, Any]) -> dict[str, float | None]:
    aliases = {
        "net_cagr": "strategy.cagr",
        "cagr": "strategy.cagr",
        "net_sharpe": "strategy.sharpe_ratio",
        "sharpe_ratio": "strategy.sharpe_ratio",
        "maximum_drawdown": "strategy.maximum_drawdown",
        "benchmark_cagr": "benchmark.cagr",
        "annualized_relative_wealth_growth": "relative.annualized_relative_wealth_growth",
        "mean_rank_ic": "predictive.mean_rank_ic",
        "target_period_coverage": "predictive.target_period_coverage",
        "nondegenerate_target_ratio": "predictive.nondegenerate_target_ratio",
        "target_period_count": "predictive.target_period_count",
        "aligned_target_period_count": "predictive.aligned_target_period_count",
    }
    result: dict[str, float | None] = {}
    for key, value in metrics.items():
        if isinstance(value, dict) and key in {"strategy", "benchmark", "relative"}:
            for nested_key, nested_value in value.items():
                if isinstance(nested_value, (int, float)) or nested_value is None:
                    result[f"{key}.{nested_key}"] = (
                        float(nested_value) if nested_value is not None else None
                    )
        elif isinstance(value, (int, float)) or value is None:
            normalized = key if "." in key else aliases.get(key)
            if normalized:
                result[normalized] = float(value) if value is not None else None
    return result


def _v021_metric_items(metrics: dict[str, Any]) -> list[dict[str, Any]]:
    core = _v021_core_metrics(metrics)
    items: list[dict[str, Any]] = []
    for composite, value in sorted(core.items()):
        role, key = composite.split(".", 1)
        items.append(
            {
                "series_role": role,
                "metric_scope": (
                    "predictive"
                    if role == "predictive"
                    else "relative"
                    if role == "relative"
                    else "absolute"
                ),
                "metric_key": key,
                "name": key.replace("_", " ").title(),
                "unit": "ratio",
                "value": value,
                "value_status": "defined" if value is not None else "undefined",
                "reason_code": None if value is not None else "metric_not_defined",
                "observation_count": int(metrics.get("observation_count", 0) or 0),
            }
        )
    return items


def _v021_nav_series(series: dict[str, Any]) -> list[dict[str, Any]]:
    raw = series.get("nav_series") or series.get("nav") or series.get("gross_nav_series", [])
    if not isinstance(raw, list):
        return []
    result: list[dict[str, Any]] = []
    peak = 0.0
    for point in raw:
        if not isinstance(point, dict) or not point.get("nav_date"):
            continue
        strategy = _numeric(
            point.get("strategy_wealth", point.get("net_nav", point.get("wealth"))), 1.0
        )
        benchmark = _numeric(point.get("benchmark_wealth", point.get("benchmark_nav")), 1.0)
        peak = max(peak, strategy)
        result.append(
            {
                "nav_date": point["nav_date"],
                "strategy_wealth": strategy,
                "benchmark_wealth": benchmark,
                "excess_wealth": float(point.get("excess_wealth", strategy - benchmark + 1.0)),
                "drawdown": float(
                    point.get("drawdown", strategy / peak - 1.0 if peak > 0 else 0.0)
                ),
            }
        )
    # Product and Experiment detail charts have a fixed-size SVG viewport.  Sending
    # every daily point made a normal 20-year Product response approach one MB and
    # added more than a second of JSON validation/transfer without adding visible
    # chart resolution.  Match the legacy result contract and retain endpoints.
    return _downsample_points(result, maximum=600)


def _numeric(value: Any, default: float) -> float:
    return float(value) if isinstance(value, (int, float)) else default


def _optional_date(value: Any) -> date | str | None:
    return value if isinstance(value, (date, str)) else None


def _search_terms(value: str | None) -> tuple[str, ...]:
    if not value:
        return ()
    normalized = value.replace(",", " ").replace("，", " ").casefold()
    return tuple(dict.fromkeys(term for term in normalized.split() if term))


_STRATEGY_TARGET_SUMMARY_SQL = """
    SELECT path.artifact_id, product.artifact_id AS product_artifact_id,
           definition.product_key, model_dataset.artifact_id AS model_dataset_artifact_id,
           specification.specification_key AS model_specification_key,
           variant.variant_key, variant.target_k, schedule.frequency,
           path.coverage_start, path.coverage_end, path.decision_count, path.position_count
    FROM strategy.portfolio_target_path path
    JOIN lineage.artifact artifact ON artifact.artifact_id = path.artifact_id
                                 AND artifact.status = 'published'
    JOIN strategy.model_strategy_target_path owner ON owner.portfolio_target_path_id =
                                                      path.portfolio_target_path_id
    JOIN strategy.strategy_product_version product ON product.strategy_product_version_id =
                                                      owner.strategy_product_version_id
    JOIN strategy.strategy_product_definition definition ON
         definition.strategy_product_definition_id = product.strategy_product_definition_id
    JOIN model.model_dataset model_dataset ON
         model_dataset.model_dataset_id = owner.model_dataset_id
    JOIN model.model_specification specification ON specification.model_specification_id =
                                                    model_dataset.model_specification_id
    JOIN strategy.strategy_variant variant ON
         variant.strategy_variant_id = product.strategy_variant_id
    JOIN ops.rebalance_schedule_version schedule ON schedule.rebalance_schedule_version_id =
                                                    product.rebalance_schedule_version_id
    WHERE true
"""

_WORKSPACE_ASSET_SELECTION_SQL = """
    SELECT security.security_id, profile.instrument_type, profile.tradability,
           profile.maturity,
           EXISTS (
             SELECT 1 FROM data.daily_bar available_bar
             JOIN data.dataset_publication available_publication
               ON available_publication.dataset_publication_id =
                  available_bar.dataset_publication_id
             JOIN lineage.artifact available_artifact
               ON available_artifact.artifact_id = available_publication.artifact_id
             WHERE available_bar.asset_id = security.legacy_asset_id
               AND available_artifact.status = 'published'
           ) AS canonical_data_available,
           (profile.tradability <> 'reference_only'
            AND profile.target_maturity IN (
                'research_ready','strategy_ready','product_eligible_input'
            )
            AND EXISTS (
              SELECT 1 FROM data.daily_bar bar
              JOIN data.dataset_publication publication
                ON publication.dataset_publication_id = bar.dataset_publication_id
              JOIN lineage.artifact data_artifact
                ON data_artifact.artifact_id = publication.artifact_id
              WHERE bar.asset_id = security.legacy_asset_id
                AND data_artifact.status = 'published'
            )) AS selectable,
           false AS pit_sector_available
    FROM catalog.security_profile profile
    JOIN catalog.security security ON security.security_id = profile.security_id
    JOIN catalog.asset_registry_release release
      ON release.asset_registry_release_id = profile.asset_registry_release_id
    JOIN lineage.artifact release_artifact ON release_artifact.artifact_id = release.artifact_id
    WHERE security.security_id = ANY(:security_ids) AND release_artifact.status = 'published'
      AND release.version_number = (
        SELECT max(latest.version_number) FROM catalog.asset_registry_release latest
        JOIN lineage.artifact latest_artifact ON latest_artifact.artifact_id = latest.artifact_id
        WHERE latest_artifact.status = 'published'
      )
    ORDER BY security.security_id
"""

_PRODUCT_CANDIDATE_SQL = """
    SELECT enrollment.product_enrollment_id AS enrollment_id,
           version.artifact_id AS product_artifact_id,
           qualification.artifact_id AS qualification_artifact_id,
           version.product_key, version.version_number, enrollment.name,
           model.preset_key AS model_preset_key,
           strategy.strategy_family_key, strategy.strategy_preset_key,
           compiled.asset_context_key, enrollment.lifecycle, enrollment.health,
           enrollment.revision,
           enrollment.activated_at, enrollment.monitoring_start_at, enrollment.updated_at,
           snapshot.as_of_session AS latest_as_of_session,
           snapshot.primary_nav, snapshot.stress_nav,
           COALESCE(snapshot.metrics, '{}'::jsonb) AS latest_metrics,
           COALESCE(qualification.gate_results -> 'warning_codes', '[]'::jsonb)
             AS warning_codes,
           (
             SELECT count(*)::integer FROM product.product_alert alert
             WHERE alert.product_enrollment_id = enrollment.product_enrollment_id
               AND COALESCE((
                 SELECT event.to_status FROM product.product_alert_event event
                 WHERE event.product_alert_id = alert.product_alert_id
                 ORDER BY event.sequence_number DESC LIMIT 1
               ), 'open') IN ('open','acknowledged')
           ) AS open_alert_count
    FROM product.product_enrollment enrollment
    JOIN product.product_version version
      ON version.product_version_id = enrollment.product_version_id
    JOIN experiment.qualification_bundle qualification
      ON qualification.qualification_bundle_id = version.qualification_bundle_id
    JOIN strategy.compiled_strategy_version strategy
      ON strategy.compiled_strategy_version_id = version.compiled_strategy_version_id
    JOIN workspace.compiled_model_instance model
      ON model.compiled_model_instance_id = strategy.compiled_model_instance_id
    JOIN workspace.compiled_research_spec compiled
      ON compiled.compiled_research_spec_id = strategy.compiled_research_spec_id
    LEFT JOIN LATERAL (
      SELECT monitoring.as_of_session, monitoring.primary_nav, monitoring.stress_nav,
             monitoring.metrics
      FROM product.monitoring_snapshot monitoring
      WHERE monitoring.product_enrollment_id = enrollment.product_enrollment_id
      ORDER BY monitoring.as_of_session DESC, monitoring.known_at DESC LIMIT 1
    ) snapshot ON true
    WHERE true
"""


def _downsample_points(points: list[dict[str, Any]], *, maximum: int) -> list[dict[str, Any]]:
    if len(points) <= maximum:
        return points
    indices = {round(index * (len(points) - 1) / (maximum - 1)) for index in range(maximum)}
    return [point for index, point in enumerate(points) if index in indices]


class ArtifactQueryService:
    """Read-only, domain-specific queries for the M1D API."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def database_revision(self) -> str | None:
        with self._engine.connect() as connection:
            connection.execute(text("SET TRANSACTION READ ONLY"))
            return connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one_or_none()

    def list_artifacts(
        self,
        *,
        statuses: Sequence[str],
        artifact_type: str | None,
        limit: int,
        offset: int,
    ) -> tuple[list[dict[str, Any]], int]:
        conditions = ["status IN :statuses"]
        parameters: dict[str, Any] = {
            "statuses": tuple(statuses),
            "limit": limit,
            "offset": offset,
        }
        if artifact_type:
            conditions.append("artifact_type = :artifact_type")
            parameters["artifact_type"] = artifact_type
        where_clause = " AND ".join(conditions)
        with self._engine.connect() as connection:
            connection.execute(text("SET TRANSACTION READ ONLY"))
            count_statement = text(
                f"SELECT count(*) FROM lineage.artifact WHERE {where_clause}"
            ).bindparams(bindparam("statuses", expanding=True))
            total = int(connection.execute(count_statement, parameters).scalar_one())
            rows_statement = text(
                f"""
                SELECT artifact_id, artifact_type, artifact_key, version_number, status,
                       semantic_fingerprint, content_hash, published_at, created_at
                FROM lineage.artifact WHERE {where_clause}
                ORDER BY artifact_type, artifact_key, version_number DESC, artifact_id
                LIMIT :limit OFFSET :offset
                """
            ).bindparams(bindparam("statuses", expanding=True))
            rows = connection.execute(rows_statement, parameters).mappings().all()
            return [dict(row) for row in rows], total

    def artifact_detail(self, artifact_id: uuid.UUID) -> dict[str, Any]:
        with self._engine.connect() as connection:
            connection.execute(text("SET TRANSACTION READ ONLY"))
            artifact = (
                connection.execute(
                    text(
                        """
                    SELECT artifact_id, artifact_type, artifact_key, version_number, status,
                           semantic_fingerprint, content_hash, published_at, created_at
                    FROM lineage.artifact WHERE artifact_id = :artifact_id
                    """
                    ),
                    {"artifact_id": artifact_id},
                )
                .mappings()
                .one_or_none()
            )
            if artifact is None:
                raise LookupError(f"Artifact not found: {artifact_id}")
            dependencies = (
                connection.execute(
                    text(
                        """
                    SELECT artifact_id, depends_on_artifact_id, role, ordinal
                    FROM lineage.artifact_dependency WHERE artifact_id = :artifact_id
                    ORDER BY role, ordinal, depends_on_artifact_id
                    """
                    ),
                    {"artifact_id": artifact_id},
                )
                .mappings()
                .all()
            )
            dependents = (
                connection.execute(
                    text(
                        """
                    SELECT artifact_id, depends_on_artifact_id, role, ordinal
                    FROM lineage.artifact_dependency WHERE depends_on_artifact_id = :artifact_id
                    ORDER BY role, ordinal, artifact_id
                    """
                    ),
                    {"artifact_id": artifact_id},
                )
                .mappings()
                .all()
            )
            has_manifest = connection.execute(
                text(
                    "SELECT EXISTS (SELECT 1 FROM lineage.lineage_manifest "
                    "WHERE root_artifact_id = :artifact_id)"
                ),
                {"artifact_id": artifact_id},
            ).scalar_one()
            return {
                "artifact": dict(artifact),
                "direct_dependencies": [dict(row) for row in dependencies],
                "direct_dependents": [dict(row) for row in dependents],
                "has_manifest": bool(has_manifest),
            }

    def lineage_manifest(self, artifact_id: uuid.UUID) -> dict[str, Any]:
        detail = self.artifact_detail(artifact_id)
        with self._engine.connect() as connection:
            connection.execute(text("SET TRANSACTION READ ONLY"))
            row: RowMapping | None = (
                connection.execute(
                    text(
                        """
                    SELECT manifest_hash, canonical_version, manifest, created_at
                    FROM lineage.lineage_manifest WHERE root_artifact_id = :artifact_id
                    """
                    ),
                    {"artifact_id": artifact_id},
                )
                .mappings()
                .one_or_none()
            )
            if row is None:
                raise LookupError(f"Lineage manifest not found: {artifact_id}")
            return {"artifact": detail["artifact"], **dict(row)}

    def asset_catalog(
        self,
        *,
        search: str | None,
        category: str | None,
        maturity: str | None,
        tradability: str | None,
        limit: int,
        offset: int,
    ) -> dict[str, Any]:
        with self._engine.connect() as connection:
            connection.execute(text("SET TRANSACTION READ ONLY"))
            release = (
                connection.execute(
                    text(
                        """
                    SELECT release.asset_registry_release_id, release.artifact_id,
                           release.version_number, release.catalog_version, release.as_of_date
                    FROM catalog.asset_registry_release release
                    JOIN lineage.artifact artifact ON artifact.artifact_id = release.artifact_id
                    WHERE artifact.status = 'published'
                    ORDER BY release.version_number DESC LIMIT 1
                    """
                    )
                )
                .mappings()
                .one_or_none()
            )
            if release is None:
                raise LookupError("Published v0.21 asset registry not found")
            release_id = release["asset_registry_release_id"]
            clauses = ["profile.asset_registry_release_id = :release_id"]
            parameters: dict[str, Any] = {"release_id": release_id}
            if category:
                clauses.append("category.category_key = :category")
                parameters["category"] = category
            if maturity:
                clauses.append("profile.maturity = :maturity")
                parameters["maturity"] = maturity
            if tradability:
                clauses.append("profile.tradability = :tradability")
                parameters["tradability"] = tradability
            for index, term in enumerate(_search_terms(search)):
                key = f"search_{index}"
                clauses.append(f"profile.search_document LIKE :{key}")
                parameters[key] = f"%{term}%"
            where_clause = " AND ".join(clauses)
            total = connection.execute(
                text(
                    "SELECT count(*) FROM catalog.security_profile profile "
                    "JOIN catalog.asset_category category "
                    "ON category.asset_category_id = profile.asset_category_id "
                    f"WHERE {where_clause}"
                ),
                parameters,
            ).scalar_one()
            row_parameters = {**parameters, "limit": limit, "offset": offset}
            rows = (
                connection.execute(
                    text(
                        f"""
                    SELECT security.security_id, security.legacy_asset_id AS asset_id,
                           security.security_key AS asset_key, security.name,
                           category.category_key, profile.asset_class,
                           profile.instrument_type, security.status, profile.symbol,
                           profile.aliases, profile.venue_mic, security.currency,
                           profile.calendar_key, profile.tradability, profile.tags,
                           profile.maturity, profile.target_maturity,
                           profile.missing_requirements,
                           EXISTS (
                               SELECT 1 FROM data.daily_bar bar
                               JOIN data.dataset_publication publication
                                 ON publication.dataset_publication_id = bar.dataset_publication_id
                               JOIN lineage.artifact dataset_artifact
                                 ON dataset_artifact.artifact_id = publication.artifact_id
                               WHERE bar.asset_id = security.legacy_asset_id
                                 AND dataset_artifact.status = 'published'
                           ) AS canonical_data_available,
                           (profile.tradability = 'tradable'
                            AND profile.target_maturity IN (
                                'research_ready','strategy_ready','product_eligible_input'
                            )) AS selectable
                    FROM catalog.security_profile profile
                    JOIN catalog.security security ON security.security_id = profile.security_id
                    JOIN catalog.asset_category category
                      ON category.asset_category_id = profile.asset_category_id
                    WHERE {where_clause}
                    ORDER BY profile.ordinal
                    LIMIT :limit OFFSET :offset
                    """
                    ),
                    row_parameters,
                )
                .mappings()
                .all()
            )
            categories = (
                connection.execute(
                    text(
                        """
                    SELECT category.category_key, category.name, category.description,
                           count(profile.security_profile_id) AS asset_count
                    FROM catalog.asset_category category
                    LEFT JOIN catalog.security_profile profile
                      ON profile.asset_category_id = category.asset_category_id
                    WHERE category.asset_registry_release_id = :release_id
                    GROUP BY category.asset_category_id
                    ORDER BY category.ordinal
                    """
                    ),
                    {"release_id": release_id},
                )
                .mappings()
                .all()
            )
            sets = (
                connection.execute(
                    text(
                        """
                    SELECT definition.set_key, definition.name, definition.set_type,
                           definition.maturity, definition.formal_eligible, definition.notes,
                           COALESCE(
                               array_agg(member.security_id ORDER BY member.ordinal)
                               FILTER (WHERE member.security_id IS NOT NULL), ARRAY[]::uuid[]
                           ) AS member_security_ids
                    FROM catalog.asset_set_definition definition
                    LEFT JOIN catalog.asset_set_member member
                      ON member.asset_set_definition_id = definition.asset_set_definition_id
                    WHERE definition.asset_registry_release_id = :release_id
                    GROUP BY definition.asset_set_definition_id
                    ORDER BY definition.set_key
                    """
                    ),
                    {"release_id": release_id},
                )
                .mappings()
                .all()
            )
            items = [dict(row) for row in rows]
            for item in items:
                item["selectable"] = bool(item["selectable"] and item["canonical_data_available"])
                item["data_inputs"] = [
                    {
                        "input_key": "canonical_market_bars",
                        "name": "Canonical OHLCV and adjusted prices",
                        "source_kind": "market",
                        "available": bool(item["canonical_data_available"]),
                        "selectable": bool(item["canonical_data_available"]),
                        "point_in_time": True,
                        "downstream_factor_keys": [
                            "open_raw", "high_raw", "low_raw", "close_raw",
                            "volume_raw", "open_adj", "close_adj",
                        ],
                        "status_note": "published canonical history"
                        if item["canonical_data_available"]
                        else "awaiting canonical history",
                    }
                ]
                if item["category_key"] == "stocks":
                    item["data_inputs"].append(
                        {
                            "input_key": "sec_filing_fundamentals",
                            "name": "Filed fundamental facts",
                            "source_kind": "fundamental",
                            "available": False,
                            "selectable": False,
                            "point_in_time": True,
                            "downstream_factor_keys": ["pe_ratio", "roe"],
                            "status_note": (
                                "planned: requires filed/accepted/available timestamps; "
                                "current ratios are never backfilled as history"
                            ),
                        }
                    )
                if item["canonical_data_available"]:
                    item["missing_requirements"] = [
                        requirement
                        for requirement in item["missing_requirements"]
                        if requirement != "canonical_history"
                    ]
            return {
                "release_artifact_id": release["artifact_id"],
                "release_version_number": release["version_number"],
                "catalog_version": release["catalog_version"],
                "as_of_date": release["as_of_date"].isoformat(),
                "total": total,
                "limit": limit,
                "offset": offset,
                "categories": [dict(row) for row in categories],
                "asset_sets": [dict(row) for row in sets],
                "items": items,
            }

    def asset_series(
        self, security_id: uuid.UUID, *, start: str | None, end: str | None
    ) -> dict[str, Any]:
        with self._engine.connect() as connection:
            connection.execute(text("SET TRANSACTION READ ONLY"))
            dataset = (
                connection.execute(
                    text(
                        """
                    SELECT security.security_id, security.security_key AS asset_key,
                           profile.symbol, publication.dataset_publication_id,
                           publication.artifact_id AS dataset_artifact_id,
                           publication.version_number, publication.coverage_start,
                           publication.coverage_end
                    FROM catalog.security security
                    JOIN catalog.security_profile profile
                      ON profile.security_id = security.security_id
                    JOIN catalog.asset_registry_release release
                      ON release.asset_registry_release_id = profile.asset_registry_release_id
                    JOIN lineage.artifact registry_artifact
                      ON registry_artifact.artifact_id = release.artifact_id
                    JOIN data.daily_bar bar ON bar.asset_id = security.legacy_asset_id
                    JOIN data.dataset_publication publication
                      ON publication.dataset_publication_id = bar.dataset_publication_id
                    JOIN lineage.artifact dataset_artifact
                      ON dataset_artifact.artifact_id = publication.artifact_id
                    WHERE security.security_id = :security_id
                      AND registry_artifact.status = 'published'
                      AND dataset_artifact.status = 'published'
                    ORDER BY release.version_number DESC, publication.version_number DESC
                    LIMIT 1
                    """
                    ),
                    {"security_id": security_id},
                )
                .mappings()
                .one_or_none()
            )
            if dataset is None:
                raise LookupError("Published canonical series not found for asset")
            clauses = [
                "bar.dataset_publication_id = :publication_id",
                "bar.asset_id = (SELECT legacy_asset_id FROM catalog.security "
                "WHERE security_id = :security_id)",
            ]
            parameters: dict[str, Any] = {
                "publication_id": dataset["dataset_publication_id"],
                "security_id": security_id,
            }
            if start:
                clauses.append("bar.session_date >= CAST(:start AS date)")
                parameters["start"] = start
            if end:
                clauses.append("bar.session_date <= CAST(:end AS date)")
                parameters["end"] = end
            points = (
                connection.execute(
                    text(
                        """
                    SELECT bar.session_date, bar.open_adj AS open, bar.high_adj AS high,
                           bar.low_adj AS low, bar.close_adj AS close,
                           bar.adj_close AS adjusted_close, bar.volume_raw AS volume
                    FROM data.daily_bar bar WHERE """
                        + " AND ".join(clauses)
                        + " ORDER BY bar.session_date"
                    ),
                    parameters,
                )
                .mappings()
                .all()
            )
            if not points:
                raise LookupError("Canonical series has no observations in the requested range")
            return {
                "security_id": dataset["security_id"],
                "asset_key": dataset["asset_key"],
                "symbol": dataset["symbol"],
                "dataset_artifact_id": dataset["dataset_artifact_id"],
                "dataset_version_number": dataset["version_number"],
                "coverage_start": points[0]["session_date"],
                "coverage_end": points[-1]["session_date"],
                "points": [dict(row) for row in points],
            }

    def workspace_options(
        self,
        *,
        frequency: str,
        selected_factor_variants: tuple[str, ...],
        selected_signals: tuple[str, ...],
        selected_models: tuple[str, ...] = (),
        selected_strategies: tuple[str, ...] = (),
        selected_assets: tuple[uuid.UUID, ...] = (),
        selected_asset_data_inputs: dict[str, tuple[str, ...]] | None = None,
    ) -> dict[str, Any]:
        if frequency not in {"weekly", "monthly"}:
            raise ValueError("Workspace frequency must be weekly or monthly")
        with self._engine.connect() as connection:
            connection.execute(text("SET TRANSACTION READ ONLY"))
            row = (
                connection.execute(
                    text(
                        """
                    SELECT catalog.artifact_id, catalog.document
                    FROM workspace.component_catalog catalog
                    JOIN lineage.artifact artifact ON artifact.artifact_id = catalog.artifact_id
                    WHERE artifact.status = 'published'
                    ORDER BY catalog.version_number DESC LIMIT 1
                    """
                    )
                )
                .mappings()
                .one_or_none()
            )
            if row is None:
                raise LookupError("Published Workspace component catalog not found")
            asset_rows: list[dict[str, Any]] = []
            if selected_assets:
                selected_rows = (
                    connection.execute(
                        text(_WORKSPACE_ASSET_SELECTION_SQL),
                        {"security_ids": list(selected_assets)},
                    )
                    .mappings()
                    .all()
                )
                asset_rows = [dict(item) for item in selected_rows]
                found = {item["security_id"] for item in selected_rows}
                asset_rows.extend(
                    {
                        "security_id": security_id,
                        "instrument_type": "unknown",
                        "selectable": False,
                        "pit_sector_available": False,
                    }
                    for security_id in selected_assets
                    if security_id not in found
                )
            options = build_workspace_options(
                cast(dict[str, Any], row["document"]),
                frequency=cast(Frequency, frequency),
                selected_factor_variants=selected_factor_variants,
                selected_signals=selected_signals,
                selected_models=selected_models,
                selected_strategies=selected_strategies,
                selected_assets=tuple(asset_rows),
                selected_asset_data_inputs=selected_asset_data_inputs,
            )
            return {"catalog_artifact_id": row["artifact_id"], **options}

    def workspace_compile_preview(
        self,
        *,
        frequency: str,
        asset_security_ids: tuple[uuid.UUID, ...],
        asset_data_inputs: dict[str, tuple[str, ...]],
        factor_variant_keys: tuple[str, ...],
        signal_version_keys: tuple[str, ...],
        model_preset_keys: tuple[str, ...],
        strategy_preset_keys: tuple[str, ...],
        model_target_keys: tuple[str, ...] = ("cross_sectional_relative_return__h5",),
    ) -> dict[str, Any]:
        if frequency not in {"weekly", "monthly"}:
            raise ValueError("Workspace frequency must be weekly or monthly")
        with self._engine.connect() as connection:
            connection.execute(text("SET TRANSACTION READ ONLY"))
            catalog = (
                connection.execute(
                    text("""
                SELECT catalog.artifact_id, catalog.document
                FROM workspace.component_catalog catalog
                JOIN lineage.artifact artifact ON artifact.artifact_id = catalog.artifact_id
                WHERE artifact.status = 'published'
                ORDER BY catalog.version_number DESC LIMIT 1
            """)
                )
                .mappings()
                .one_or_none()
            )
            if catalog is None:
                raise LookupError("Published Workspace component catalog not found")
            rows = (
                connection.execute(
                    text(_WORKSPACE_ASSET_SELECTION_SQL),
                    {"security_ids": list(asset_security_ids)},
                )
                .mappings()
                .all()
            )
        found = {row["security_id"] for row in rows}
        selected_assets = [dict(row) for row in rows]
        selected_assets.extend(
            {
                "security_id": item,
                "instrument_type": "unknown",
                "selectable": False,
                "pit_sector_available": False,
            }
            for item in asset_security_ids
            if item not in found
        )
        preview = build_compile_preview(
            cast(dict[str, Any], catalog["document"]),
            frequency=cast(Frequency, frequency),
            asset_security_ids=tuple(str(item) for item in asset_security_ids),
            asset_data_inputs=asset_data_inputs,
            selected_assets=tuple(selected_assets),
            factor_variant_keys=factor_variant_keys,
            signal_version_keys=signal_version_keys,
            model_preset_keys=model_preset_keys,
            model_target_keys=model_target_keys,
            strategy_preset_keys=strategy_preset_keys,
        )
        return {"catalog_artifact_id": catalog["artifact_id"], **preview}

    def factor_values(self, artifact_id: uuid.UUID) -> dict[str, Any]:
        with self._engine.connect() as connection:
            connection.execute(text("SET TRANSACTION READ ONLY"))
            dataset = (
                connection.execute(
                    text(
                        """
                    SELECT dataset.factor_dataset_id, variant.variant_key,
                           artifact.content_hash
                    FROM factor.factor_dataset dataset
                    JOIN factor.factor_variant variant
                      ON variant.factor_variant_id = dataset.factor_variant_id
                    JOIN lineage.artifact artifact ON artifact.artifact_id = dataset.artifact_id
                    WHERE dataset.artifact_id = :artifact_id AND artifact.status = 'published'
                    """
                    ),
                    {"artifact_id": artifact_id},
                )
                .mappings()
                .one_or_none()
            )
            if dataset is None:
                raise LookupError("Published Factor Dataset not found")
            rows = (
                connection.execute(
                    text(
                        """
                    SELECT value.observation_date, asset.asset_key, symbol.symbol,
                           value.value
                    FROM factor.factor_value value
                    JOIN catalog.asset asset ON asset.asset_id = value.asset_id
                    JOIN catalog.asset_listing listing ON listing.asset_id = asset.asset_id
                    JOIN catalog.listing_symbol symbol
                      ON symbol.asset_listing_id = listing.asset_listing_id
                     AND symbol.symbol_type = 'ticker'
                    WHERE value.factor_dataset_id = :dataset_id
                    ORDER BY value.observation_date, asset.asset_key
                    """
                    ),
                    {"dataset_id": dataset["factor_dataset_id"]},
                )
                .mappings()
                .all()
            )
            return {
                "variant_key": dataset["variant_key"],
                "content_hash": dataset["content_hash"],
                "rows": [dict(row) for row in rows],
            }

    def signal_values(self, version_key: str) -> dict[str, Any]:
        with self._engine.connect() as connection:
            connection.execute(text("SET TRANSACTION READ ONLY"))
            dataset = (
                connection.execute(
                    text(
                        """
                    SELECT dataset.signal_dataset_id, dataset.artifact_id,
                           artifact.content_hash
                    FROM signal.signal_dataset dataset
                    JOIN signal.signal_version version
                      ON version.signal_version_id = dataset.signal_version_id
                    JOIN signal.signal_definition definition
                      ON definition.signal_definition_id = version.signal_definition_id
                    JOIN lineage.artifact artifact ON artifact.artifact_id = dataset.artifact_id
                    WHERE definition.signal_key = :version_key
                      AND artifact.status = 'published'
                    ORDER BY dataset.created_at DESC LIMIT 1
                    """
                    ),
                    {"version_key": version_key},
                )
                .mappings()
                .one_or_none()
            )
            if dataset is None:
                raise LookupError(f"Published Signal Dataset not found: {version_key}")
            rows = (
                connection.execute(
                    text(
                        """
                    SELECT value.observation_date, asset.asset_key, symbol.symbol,
                           value.score, value.state, value.event
                    FROM signal.signal_value value
                    JOIN catalog.asset asset ON asset.asset_id = value.asset_id
                    JOIN catalog.asset_listing listing ON listing.asset_id = asset.asset_id
                    JOIN catalog.listing_symbol symbol
                      ON symbol.asset_listing_id = listing.asset_listing_id
                     AND symbol.symbol_type = 'ticker'
                    WHERE value.signal_dataset_id = :dataset_id
                    ORDER BY value.observation_date, asset.asset_key
                    """
                    ),
                    {"dataset_id": dataset["signal_dataset_id"]},
                )
                .mappings()
                .all()
            )
        return {
            "version_key": version_key,
            "artifact_id": dataset["artifact_id"],
            "content_hash": dataset["content_hash"],
            "rows": [dict(row) for row in rows],
        }

    def product_catalog(self) -> dict[str, Any]:
        with self._engine.connect() as connection:
            connection.execute(text("SET TRANSACTION READ ONLY"))
            rows = connection.execute(
                text(_PRODUCT_CANDIDATE_SQL + " ORDER BY enrollment.updated_at DESC")
            )
            return {"items": [dict(row) for row in rows.mappings().all()]}

    def product_detail(self, enrollment_id: uuid.UUID) -> dict[str, Any]:
        with self._engine.connect() as connection:
            connection.execute(text("SET TRANSACTION READ ONLY"))
            candidate = (
                connection.execute(
                    text(_PRODUCT_CANDIDATE_SQL + " AND enrollment.product_enrollment_id = :id"),
                    {"id": enrollment_id},
                )
                .mappings()
                .one_or_none()
            )
            if candidate is None:
                raise LookupError(f"Product Research Candidate not found: {enrollment_id}")
            enrollment = (
                connection.execute(
                    text("""
                SELECT enrollment.selection_reason, enrollment.note,
                       qualification.gate_results AS qualification_gate_results,
                       qualification.source_suite_artifact_id,
                       qualification.result_artifact_ids,
                       qualification.selection_context
                FROM product.product_enrollment enrollment
                JOIN product.product_version version
                  ON version.product_version_id = enrollment.product_version_id
                JOIN experiment.qualification_bundle qualification
                  ON qualification.qualification_bundle_id = version.qualification_bundle_id
                WHERE enrollment.product_enrollment_id = :id
            """),
                    {"id": enrollment_id},
                )
                .mappings()
                .one()
            )
            selection_context = dict(enrollment["selection_context"] or {})
            # New Products freeze one exact branch.  The normalized_selection
            # fallback keeps already-published Product records queryable.
            exact_selection = selection_context.get("exact_selection")
            normalized_selection = dict(
                exact_selection
                if isinstance(exact_selection, dict)
                else selection_context.get("normalized_selection") or {}
            )
            selected_model = dict(normalized_selection.get("model") or {})
            selected_strategy = dict(normalized_selection.get("strategy") or {})
            selected_result_value = selection_context.get("selected_result_artifact_id")
            selected_result_id = (
                uuid.UUID(str(selected_result_value)) if selected_result_value is not None else None
            )
            selected_asset_ids = [
                uuid.UUID(str(value))
                for value in normalized_selection.get("asset_security_ids", [])
            ]
            asset_rows: list[Any] = []
            if selected_asset_ids:
                asset_rows = list(
                    connection.execute(
                        text("""
                    SELECT DISTINCT ON (security.security_id)
                           security.security_id, security.security_key AS asset_key,
                           security.legacy_asset_id,
                           COALESCE(profile.symbol, security.security_key) AS symbol,
                           security.name, category.category_key
                    FROM catalog.security security
                    LEFT JOIN catalog.security_profile profile
                      ON profile.security_id = security.security_id
                    LEFT JOIN catalog.asset_registry_release release
                      ON release.asset_registry_release_id = profile.asset_registry_release_id
                    LEFT JOIN lineage.artifact release_artifact
                      ON release_artifact.artifact_id = release.artifact_id
                    LEFT JOIN catalog.asset_category category
                      ON category.asset_category_id = profile.asset_category_id
                    WHERE security.security_id = ANY(:security_ids)
                      AND (release_artifact.artifact_id IS NULL
                           OR release_artifact.status = 'published')
                    ORDER BY security.security_id, release.version_number DESC NULLS LAST
                """),
                        {"security_ids": selected_asset_ids},
                    )
                    .mappings()
                    .all()
                )
            assets_by_id = {row["security_id"]: dict(row) for row in asset_rows}
            research_assets = [
                {
                    key: value
                    for key, value in assets_by_id[security_id].items()
                    if key != "legacy_asset_id"
                }
                for security_id in selected_asset_ids
                if security_id in assets_by_id
            ]
            qualification_backtest_source = (
                self._v021_experiment_result(connection, selected_result_id)
                if selected_result_id is not None
                else None
            )
            events = (
                connection.execute(
                    text("""
                SELECT sequence_number, from_lifecycle, to_lifecycle, reason_code, reason,
                       researcher_id, requested_at, effective_at
                FROM product.product_lifecycle_event WHERE product_enrollment_id = :id
                ORDER BY sequence_number DESC
            """),
                    {"id": enrollment_id},
                )
                .mappings()
                .all()
            )
            alerts = (
                connection.execute(
                    text("""
                SELECT alert.product_alert_id AS alert_id, alert.alert_key, alert.alert_type,
                       alert.severity, alert.opened_at, alert.evidence,
                       COALESCE(latest.to_status, 'open') AS status
                FROM product.product_alert alert
                LEFT JOIN LATERAL (
                    SELECT event.to_status FROM product.product_alert_event event
                    WHERE event.product_alert_id = alert.product_alert_id
                    ORDER BY event.sequence_number DESC LIMIT 1
                ) latest ON true
                WHERE alert.product_enrollment_id = :id ORDER BY alert.opened_at DESC
            """),
                    {"id": enrollment_id},
                )
                .mappings()
                .all()
            )
            snapshots = (
                connection.execute(
                    text("""
                SELECT artifact_id, as_of_session, known_at, health, session_count,
                       decision_count, primary_nav, stress_nav, metrics, health_components
                FROM product.monitoring_snapshot WHERE product_enrollment_id = :id
                ORDER BY as_of_session DESC, known_at DESC LIMIT 400
            """),
                    {"id": enrollment_id},
                )
                .mappings()
                .all()
            )
            frozen_anchor_value = (
                qualification_backtest_source["resolved_end"]
                if qualification_backtest_source is not None
                else None
            )
            frozen_anchor_session = (
                date.fromisoformat(frozen_anchor_value)
                if isinstance(frozen_anchor_value, str)
                else frozen_anchor_value
            )
            legacy_asset_ids = tuple(
                row["legacy_asset_id"]
                for row in asset_rows
                if row["legacy_asset_id"] is not None
            )
            latest_bundle = None
            if legacy_asset_ids and len(legacy_asset_ids) == len(selected_asset_ids):
                latest_bundle = (
                    connection.execute(
                        text("""
                        SELECT bundle.coverage_end, artifact.created_at AS known_at,
                               calendar.calendar_version_id
                        FROM data.data_bundle_version bundle
                        JOIN lineage.artifact artifact
                          ON artifact.artifact_id = bundle.artifact_id
                         AND artifact.status = 'published'
                        JOIN data.data_bundle_member member
                          ON member.data_bundle_version_id = bundle.data_bundle_version_id
                         AND member.role = 'canonical_market'
                        JOIN data.data_bundle_member calendar
                          ON calendar.data_bundle_version_id = bundle.data_bundle_version_id
                         AND calendar.role = 'trading_calendar'
                        JOIN data.daily_bar bar
                          ON bar.dataset_publication_id = member.dataset_publication_id
                        WHERE bar.asset_id IN :asset_ids
                        GROUP BY bundle.data_bundle_version_id, artifact.created_at,
                                 calendar.calendar_version_id
                        HAVING count(DISTINCT bar.asset_id) = :asset_count
                        ORDER BY bundle.coverage_end DESC, artifact.created_at DESC
                        LIMIT 1
                    """).bindparams(bindparam("asset_ids", expanding=True)),
                        {
                            "asset_ids": legacy_asset_ids,
                            "asset_count": len(legacy_asset_ids),
                        },
                    )
                    .mappings()
                    .one_or_none()
                )
            latest_published_data_session = (
                latest_bundle["coverage_end"] if latest_bundle is not None else None
            )
            post_freeze_session_count = 0
            prospective_oos_session_count = 0
            if frozen_anchor_session is not None and latest_bundle is not None:
                counts = connection.execute(
                    text("""
                        SELECT
                            count(*) FILTER (
                                WHERE session_date > :frozen_anchor
                                  AND session_date <= :latest_data
                            ) AS post_freeze_count,
                            count(*) FILTER (
                                WHERE session_date > GREATEST(:frozen_anchor, :activation_session)
                                  AND session_date <= :latest_data
                            ) AS prospective_count
                        FROM catalog.calendar_session
                        WHERE calendar_version_id = :calendar_version_id
                    """),
                    {
                        "frozen_anchor": frozen_anchor_session,
                        "activation_session": candidate["activated_at"].date(),
                        "latest_data": latest_published_data_session,
                        "calendar_version_id": latest_bundle["calendar_version_id"],
                    },
                ).mappings().one()
                post_freeze_session_count = int(counts["post_freeze_count"] or 0)
                prospective_oos_session_count = int(counts["prospective_count"] or 0)
            if frozen_anchor_session is None:
                oos_status = "awaiting_frozen_anchor"
                oos_reason_codes = ["qualification_frozen_anchor_unavailable"]
            elif (
                latest_published_data_session is None
                or latest_published_data_session <= frozen_anchor_session
            ):
                oos_status = "awaiting_post_freeze_data"
                oos_reason_codes = ["published_data_has_not_passed_frozen_anchor"]
            elif not snapshots:
                oos_status = "awaiting_first_snapshot"
                oos_reason_codes = ["post_freeze_data_awaiting_monitoring_snapshot"]
            else:
                oos_status = "observing"
                oos_reason_codes = []
            reviews = (
                connection.execute(
                    text(
                        """
                        SELECT product_review_id, reviewed_at, researcher_id,
                               decision, reason, evidence
                        FROM product.product_review
                        WHERE product_enrollment_id = :id
                        ORDER BY reviewed_at DESC, product_review_id DESC
                        """
                    ),
                    {"id": enrollment_id},
                )
                .mappings()
                .all()
            )
        return {
            "candidate": dict(candidate),
            "qualification_gate_results": enrollment["qualification_gate_results"],
            "selection_reason": enrollment["selection_reason"],
            "note": enrollment["note"],
            "events": [dict(row) for row in events],
            "alerts": [dict(row) for row in alerts],
            "snapshots": [dict(row) for row in snapshots],
            "oos_window": {
                "frozen_anchor_session": frozen_anchor_session,
                "activation_session": candidate["activated_at"].date(),
                "latest_published_data_session": latest_published_data_session,
                "latest_published_data_known_at": (
                    latest_bundle["known_at"] if latest_bundle is not None else None
                ),
                "latest_snapshot_session": (
                    snapshots[0]["as_of_session"] if snapshots else None
                ),
                "post_freeze_session_count": post_freeze_session_count,
                "prospective_oos_session_count": prospective_oos_session_count,
                "status": oos_status,
                "reason_codes": oos_reason_codes,
            },
            "reviews": [dict(row) for row in reviews],
            "research_chain": (
                {
                    "source_suite_artifact_id": enrollment["source_suite_artifact_id"],
                    "selected_result_artifact_id": selected_result_id,
                    "selected_branch_key": str(
                        selected_strategy.get("branch_key")
                        or selection_context.get("selected_branch_key", "")
                    ),
                    "frequency": str(normalized_selection.get("frequency", "")),
                    "assets": research_assets,
                    "factor_variant_keys": list(
                        normalized_selection.get("factor_variant_keys", [])
                    ),
                    "signal_version_keys": list(
                        normalized_selection.get("signal_version_keys", [])
                    ),
                    "model_preset_keys": list(
                        [selected_model["preset_key"]]
                        if selected_model.get("preset_key") is not None
                        else normalized_selection.get("model_preset_keys", [])
                    ),
                    "model_target_keys": list(
                        [selected_model["target_key"]]
                        if selected_model.get("target_key") is not None
                        else normalized_selection.get("model_target_keys", [])
                    ),
                    "strategy_preset_keys": list(
                        [selected_strategy["preset_key"]]
                        if selected_strategy.get("preset_key") is not None
                        else normalized_selection.get("strategy_preset_keys", [])
                    ),
                    "qualification_result_artifact_ids": list(
                        enrollment["result_artifact_ids"] or []
                    ),
                }
                if selected_result_id is not None
                else None
            ),
            "qualification_backtest": (
                {
                    "result_artifact_id": qualification_backtest_source["result_artifact_id"],
                    "specification": qualification_backtest_source["specification"],
                    "resolved_start": qualification_backtest_source["resolved_start"],
                    "resolved_end": qualification_backtest_source["resolved_end"],
                    "observation_count": qualification_backtest_source["observation_count"],
                    "run_status": qualification_backtest_source["run_status"],
                    "metrics": qualification_backtest_source["metrics"],
                    "nav_series": qualification_backtest_source["nav_series"],
                    "quality_checks": qualification_backtest_source["quality_checks"],
                }
                if qualification_backtest_source is not None
                else None
            ),
        }

    def data_requirements(self) -> dict[str, Any]:
        with self._engine.connect() as connection:
            connection.execute(text("SET TRANSACTION READ ONLY"))
            version = (
                connection.execute(
                    text(
                        """
                    SELECT version.data_requirement_version_id, version.artifact_id,
                           version.version_number, definition.requirement_set_key
                    FROM catalog.data_requirement_version version
                    JOIN catalog.data_requirement_definition definition
                      ON definition.data_requirement_definition_id =
                         version.data_requirement_definition_id
                    JOIN lineage.artifact artifact ON artifact.artifact_id = version.artifact_id
                    WHERE artifact.status = 'published'
                    ORDER BY version.version_number DESC LIMIT 1
                    """
                    )
                )
                .mappings()
                .one_or_none()
            )
            if version is None:
                raise LookupError("Published data requirements not found")
            rows = (
                connection.execute(
                    text(
                        """
                    SELECT requirement_key, subject, series_key, fields, interval_unit,
                           interval_count, calendar_type, session_type, timestamp_semantics
                    FROM catalog.data_requirement_member
                    WHERE data_requirement_version_id = :version_id
                    ORDER BY requirement_key
                    """
                    ),
                    {"version_id": version["data_requirement_version_id"]},
                )
                .mappings()
                .all()
            )
            return {
                "artifact_id": version["artifact_id"],
                "requirement_set_key": version["requirement_set_key"],
                "version_number": version["version_number"],
                "items": [dict(row) for row in rows],
            }

    def data_overview(self) -> dict[str, Any]:
        """Return the published M2 data chain as one UI-oriented read model."""
        with self._engine.connect() as connection:
            connection.execute(text("SET TRANSACTION READ ONLY"))
            sources = (
                connection.execute(
                    text(
                        """
                    SELECT snapshot.artifact_id, definition.series_key,
                           provider.provider_key, snapshot.snapshot_key,
                           snapshot.fetched_at, snapshot.as_of_at,
                           snapshot.raw_size_bytes, snapshot.payload_hash
                    FROM data.source_snapshot snapshot
                    JOIN data.data_series_version series
                      ON series.data_series_version_id = snapshot.data_series_version_id
                    JOIN data.data_series_definition definition
                      ON definition.data_series_definition_id = series.data_series_definition_id
                    JOIN data.source_provider provider
                      ON provider.source_provider_id = series.source_provider_id
                    JOIN lineage.artifact artifact ON artifact.artifact_id = snapshot.artifact_id
                    WHERE artifact.status = 'published'
                    ORDER BY snapshot.fetched_at DESC, definition.series_key
                    LIMIT 50
                    """
                    )
                )
                .mappings()
                .all()
            )
            datasets = (
                connection.execute(
                    text(
                        """
                    SELECT publication.dataset_publication_id, publication.artifact_id,
                           publication.dataset_key, publication.version_number,
                           publication.dataset_kind, publication.value_kind,
                           publication.coverage_start, publication.coverage_end,
                           publication.row_count
                    FROM data.dataset_publication publication
                    JOIN lineage.artifact artifact
                      ON artifact.artifact_id = publication.artifact_id
                    WHERE artifact.status = 'published'
                    ORDER BY publication.dataset_key, publication.version_number DESC
                    """
                    )
                )
                .mappings()
                .all()
            )
            dataset_ids = [row["dataset_publication_id"] for row in datasets]
            coverage_by_id: dict[uuid.UUID, list[dict[str, Any]]] = {
                dataset_id: [] for dataset_id in dataset_ids
            }
            issues_by_id: dict[uuid.UUID, list[dict[str, Any]]] = {
                dataset_id: [] for dataset_id in dataset_ids
            }
            if dataset_ids:
                coverage_rows = (
                    connection.execute(
                        text(
                            """
                        SELECT coverage.dataset_publication_id, coverage.subject_key,
                               asset.asset_key, coverage.coverage_start, coverage.coverage_end,
                               coverage.observation_count, coverage.missing_count
                        FROM data.dataset_coverage coverage
                        LEFT JOIN catalog.asset asset ON asset.asset_id = coverage.asset_id
                        WHERE coverage.dataset_publication_id IN :dataset_ids
                        ORDER BY coverage.dataset_publication_id, coverage.subject_key
                        """
                        ).bindparams(bindparam("dataset_ids", expanding=True)),
                        {"dataset_ids": tuple(dataset_ids)},
                    )
                    .mappings()
                    .all()
                )
                for row in coverage_rows:
                    item = dict(row)
                    dataset_id = item.pop("dataset_publication_id")
                    coverage_by_id[dataset_id].append(item)
                issue_rows = (
                    connection.execute(
                        text(
                            """
                        SELECT issue.dataset_publication_id, issue.severity, issue.rule_code,
                               asset.asset_key, issue.event_date, issue.message, issue.details
                        FROM data.quality_issue issue
                        LEFT JOIN catalog.asset asset ON asset.asset_id = issue.asset_id
                        WHERE issue.dataset_publication_id IN :dataset_ids
                        ORDER BY issue.dataset_publication_id,
                                 CASE issue.severity WHEN 'error' THEN 0
                                      WHEN 'warning' THEN 1 ELSE 2 END,
                                 issue.rule_code, issue.event_date NULLS FIRST
                        """
                        ).bindparams(bindparam("dataset_ids", expanding=True)),
                        {"dataset_ids": tuple(dataset_ids)},
                    )
                    .mappings()
                    .all()
                )
                for row in issue_rows:
                    item = dict(row)
                    dataset_id = item.pop("dataset_publication_id")
                    issues_by_id[dataset_id].append(item)

            dataset_items: list[dict[str, Any]] = []
            for row in datasets:
                item = dict(row)
                dataset_id = item.pop("dataset_publication_id")
                item["coverage"] = coverage_by_id[dataset_id]
                item["issues"] = issues_by_id[dataset_id]
                dataset_items.append(item)

            bundle = self._latest_bundle(connection)
            eligibility = self._latest_eligibility(connection)
            return {
                "sources": [dict(row) for row in sources],
                "datasets": dataset_items,
                "bundle": bundle,
                "eligibility": eligibility,
            }

    def factor_overview(self) -> dict[str, Any]:
        """Return the latest published factor-layer diagnostic context."""
        with self._engine.connect() as connection:
            connection.execute(text("SET TRANSACTION READ ONLY"))
            diagnostic = (
                connection.execute(
                    text(
                        """
                    SELECT diagnostic.factor_diagnostic_set_id,
                           diagnostic.artifact_id AS diagnostic_artifact_id,
                           diagnostic.factor_catalog_artifact_id,
                           universe.artifact_id AS universe_artifact_id,
                           bundle.artifact_id AS data_bundle_artifact_id,
                           eligibility.artifact_id AS eligibility_artifact_id,
                           factor_engine.artifact_id AS factor_engine_artifact_id,
                           diagnostic_engine.artifact_id AS diagnostic_engine_artifact_id,
                           diagnostic.coverage_start, diagnostic.coverage_end,
                           diagnostic.dataset_count, diagnostic.asset_count,
                           diagnostic.observation_count, diagnostic.pair_count,
                           diagnostic.high_correlation_threshold
                    FROM factor.factor_diagnostic_set diagnostic
                    JOIN lineage.artifact artifact
                      ON artifact.artifact_id = diagnostic.artifact_id
                    JOIN catalog.universe_version universe
                      ON universe.universe_version_id = diagnostic.universe_version_id
                    JOIN data.data_bundle_version bundle
                      ON bundle.data_bundle_version_id = diagnostic.data_bundle_version_id
                    JOIN catalog.eligibility_snapshot eligibility
                      ON eligibility.eligibility_snapshot_id = diagnostic.eligibility_snapshot_id
                    JOIN ops.engine_version factor_engine
                      ON factor_engine.engine_version_id = diagnostic.factor_engine_version_id
                    JOIN ops.engine_version diagnostic_engine
                      ON diagnostic_engine.engine_version_id =
                         diagnostic.diagnostic_engine_version_id
                    WHERE artifact.status = 'published'
                    ORDER BY diagnostic.created_at DESC LIMIT 1
                    """
                    )
                )
                .mappings()
                .one_or_none()
            )
            if diagnostic is None:
                raise LookupError("Published factor diagnostics not found")
            diagnostic_set_id = diagnostic["factor_diagnostic_set_id"]
            datasets = (
                connection.execute(
                    text(
                        """
                    SELECT dataset.artifact_id AS factor_dataset_artifact_id,
                           definition.factor_key, definition.measurement_family,
                           version.formula, version.output_unit, variant.variant_key,
                           variant.parameters, variant.preset_type,
                           dataset.coverage_start, dataset.coverage_end, dataset.row_count,
                           summary.observation_count, summary.asset_count,
                           summary.missing_count, summary.mean,
                           summary.standard_deviation, summary.minimum, summary.p05,
                           summary.p25, summary.median, summary.p75, summary.p95,
                           summary.maximum, summary.zero_variance
                    FROM factor.factor_dataset_summary summary
                    JOIN factor.factor_dataset dataset
                      ON dataset.factor_dataset_id = summary.factor_dataset_id
                    JOIN factor.factor_variant variant
                      ON variant.factor_variant_id = dataset.factor_variant_id
                    JOIN factor.factor_definition_version version
                      ON version.factor_definition_version_id =
                         variant.factor_definition_version_id
                    JOIN factor.factor_definition definition
                      ON definition.factor_definition_id = version.factor_definition_id
                    WHERE summary.factor_diagnostic_set_id = :set_id
                    ORDER BY definition.measurement_family, definition.factor_key,
                             variant.variant_key
                    """
                    ),
                    {"set_id": diagnostic_set_id},
                )
                .mappings()
                .all()
            )
            correlations = (
                connection.execute(
                    text(
                        """
                    SELECT left_variant.variant_key AS left_variant_key,
                           right_variant.variant_key AS right_variant_key,
                           left_definition.factor_key AS left_factor_key,
                           right_definition.factor_key AS right_factor_key,
                           correlation.observation_count,
                           correlation.spearman_correlation,
                           correlation.same_definition, correlation.high_correlation
                    FROM factor.factor_pair_correlation correlation
                    JOIN factor.factor_dataset left_dataset
                      ON left_dataset.factor_dataset_id =
                         correlation.left_factor_dataset_id
                    JOIN factor.factor_variant left_variant
                      ON left_variant.factor_variant_id = left_dataset.factor_variant_id
                    JOIN factor.factor_definition_version left_version
                      ON left_version.factor_definition_version_id =
                         left_variant.factor_definition_version_id
                    JOIN factor.factor_definition left_definition
                      ON left_definition.factor_definition_id =
                         left_version.factor_definition_id
                    JOIN factor.factor_dataset right_dataset
                      ON right_dataset.factor_dataset_id =
                         correlation.right_factor_dataset_id
                    JOIN factor.factor_variant right_variant
                      ON right_variant.factor_variant_id = right_dataset.factor_variant_id
                    JOIN factor.factor_definition_version right_version
                      ON right_version.factor_definition_version_id =
                         right_variant.factor_definition_version_id
                    JOIN factor.factor_definition right_definition
                      ON right_definition.factor_definition_id =
                         right_version.factor_definition_id
                    WHERE correlation.factor_diagnostic_set_id = :set_id
                    ORDER BY correlation.high_correlation DESC,
                             abs(correlation.spearman_correlation) DESC NULLS LAST,
                             left_variant.variant_key, right_variant.variant_key
                    """
                    ),
                    {"set_id": diagnostic_set_id},
                )
                .mappings()
                .all()
            )
            issues = (
                connection.execute(
                    text(
                        """
                    SELECT variant.variant_key, issue.severity, issue.issue_code,
                           issue.message, issue.details
                    FROM factor.factor_diagnostic_issue issue
                    JOIN factor.factor_dataset dataset
                      ON dataset.factor_dataset_id = issue.factor_dataset_id
                    JOIN factor.factor_variant variant
                      ON variant.factor_variant_id = dataset.factor_variant_id
                    WHERE issue.factor_diagnostic_set_id = :set_id
                    ORDER BY CASE issue.severity WHEN 'error' THEN 0
                                  WHEN 'warning' THEN 1 ELSE 2 END,
                             variant.variant_key, issue.issue_code
                    """
                    ),
                    {"set_id": diagnostic_set_id},
                )
                .mappings()
                .all()
            )
        result = dict(diagnostic)
        result.pop("factor_diagnostic_set_id")
        result["datasets"] = [dict(row) for row in datasets]
        result["correlations"] = [dict(row) for row in correlations]
        result["issues"] = [dict(row) for row in issues]
        return result

    def signal_overview(self, frequency: str) -> dict[str, Any]:
        """Return the latest published Signal evaluation for one explicit target frequency."""
        if frequency not in {"weekly", "monthly"}:
            raise ValueError("Signal frequency must be weekly or monthly")
        with self._engine.connect() as connection:
            connection.execute(text("SET TRANSACTION READ ONLY"))
            evaluation = (
                connection.execute(
                    text(
                        """
                    SELECT evaluation.signal_evaluation_id,
                           evaluation.artifact_id AS evaluation_artifact_id,
                           evaluation.signal_catalog_artifact_id,
                           universe.artifact_id AS universe_artifact_id,
                           bundle.artifact_id AS data_bundle_artifact_id,
                           eligibility.artifact_id AS eligibility_artifact_id,
                           signal_engine.artifact_id AS signal_engine_artifact_id,
                           evaluation_engine.artifact_id AS evaluation_engine_artifact_id,
                           target.artifact_id AS forward_return_artifact_id,
                           target_definition.target_key, evaluation.frequency,
                           evaluation.coverage_start, evaluation.coverage_end,
                           evaluation.signal_count, evaluation.pair_count,
                           evaluation.high_correlation_threshold
                    FROM signal.signal_evaluation evaluation
                    JOIN lineage.artifact artifact
                      ON artifact.artifact_id = evaluation.artifact_id
                    JOIN catalog.universe_version universe
                      ON universe.universe_version_id = evaluation.universe_version_id
                    JOIN data.data_bundle_version bundle
                      ON bundle.data_bundle_version_id = evaluation.data_bundle_version_id
                    JOIN catalog.eligibility_snapshot eligibility
                      ON eligibility.eligibility_snapshot_id =
                         evaluation.eligibility_snapshot_id
                    JOIN ops.engine_version signal_engine
                      ON signal_engine.engine_version_id =
                         evaluation.signal_engine_version_id
                    JOIN ops.engine_version evaluation_engine
                      ON evaluation_engine.engine_version_id =
                         evaluation.evaluation_engine_version_id
                    JOIN data.forward_return_dataset target
                      ON target.forward_return_dataset_id =
                         evaluation.forward_return_dataset_id
                    JOIN data.forward_return_version target_version
                      ON target_version.forward_return_version_id =
                         target.forward_return_version_id
                    JOIN data.forward_return_definition target_definition
                      ON target_definition.forward_return_definition_id =
                         target_version.forward_return_definition_id
                    WHERE artifact.status = 'published'
                      AND evaluation.frequency = :frequency
                    ORDER BY evaluation.created_at DESC LIMIT 1
                    """
                    ),
                    {"frequency": frequency},
                )
                .mappings()
                .one_or_none()
            )
            if evaluation is None:
                raise LookupError(f"Published {frequency} Signal evaluation not found")
            evaluation_id = evaluation["signal_evaluation_id"]
            signal_rows = (
                connection.execute(
                    text(
                        """
                    SELECT dataset.artifact_id AS signal_dataset_artifact_id,
                           definition.signal_key, definition.template_key,
                           definition.economic_family, definition.rationale_type,
                           definition.rationale, definition.research_tier,
                           definition.product_eligible, version.direction,
                           version.normalization, version.output_type,
                           factor_variant.variant_key AS factor_variant_key,
                           metric.window_key, metric.window_start, metric.window_end,
                           metric.period_count, metric.valid_ic_count,
                           metric.undefined_ic_count, metric.mean_rank_ic,
                           metric.median_rank_ic, metric.positive_ic_ratio,
                           metric.information_ratio, metric.mean_top_bottom_spread,
                           metric.event_rate, metric.event_asset_concentration,
                           metric.non_neutral_rate, metric.mean_top2_turnover
                    FROM signal.signal_evaluation_metric metric
                    JOIN signal.signal_dataset dataset
                      ON dataset.signal_dataset_id = metric.signal_dataset_id
                    JOIN signal.signal_version version
                      ON version.signal_version_id = dataset.signal_version_id
                    JOIN signal.signal_definition definition
                      ON definition.signal_definition_id = version.signal_definition_id
                    JOIN factor.factor_variant factor_variant
                      ON factor_variant.factor_variant_id = version.factor_variant_id
                    WHERE metric.signal_evaluation_id = :evaluation
                    ORDER BY definition.economic_family, definition.signal_key,
                             metric.window_key
                    """
                    ),
                    {"evaluation": evaluation_id},
                )
                .mappings()
                .all()
            )
            pairs = (
                connection.execute(
                    text(
                        """
                    SELECT left_definition.signal_key AS left_signal_key,
                           right_definition.signal_key AS right_signal_key,
                           pair.score_observation_count, pair.score_spearman,
                           pair.spread_period_count, pair.spread_correlation,
                           pair.mean_top2_overlap, pair.high_correlation
                    FROM signal.signal_pair_diagnostic pair
                    JOIN signal.signal_dataset left_dataset
                      ON left_dataset.signal_dataset_id = pair.left_signal_dataset_id
                    JOIN signal.signal_version left_version
                      ON left_version.signal_version_id = left_dataset.signal_version_id
                    JOIN signal.signal_definition left_definition
                      ON left_definition.signal_definition_id =
                         left_version.signal_definition_id
                    JOIN signal.signal_dataset right_dataset
                      ON right_dataset.signal_dataset_id = pair.right_signal_dataset_id
                    JOIN signal.signal_version right_version
                      ON right_version.signal_version_id = right_dataset.signal_version_id
                    JOIN signal.signal_definition right_definition
                      ON right_definition.signal_definition_id =
                         right_version.signal_definition_id
                    WHERE pair.signal_evaluation_id = :evaluation
                    ORDER BY pair.high_correlation DESC,
                             abs(pair.score_spearman) DESC NULLS LAST,
                             left_definition.signal_key, right_definition.signal_key
                    """
                    ),
                    {"evaluation": evaluation_id},
                )
                .mappings()
                .all()
            )
            issues = (
                connection.execute(
                    text(
                        """
                    SELECT definition.signal_key, issue.severity, issue.issue_code,
                           issue.message, issue.details
                    FROM signal.signal_diagnostic_issue issue
                    JOIN signal.signal_dataset dataset
                      ON dataset.signal_dataset_id = issue.signal_dataset_id
                    JOIN signal.signal_version version
                      ON version.signal_version_id = dataset.signal_version_id
                    JOIN signal.signal_definition definition
                      ON definition.signal_definition_id = version.signal_definition_id
                    WHERE issue.signal_evaluation_id = :evaluation
                    ORDER BY CASE issue.severity WHEN 'error' THEN 0
                                  WHEN 'warning' THEN 1 ELSE 2 END,
                             definition.signal_key, issue.issue_code
                    """
                    ),
                    {"evaluation": evaluation_id},
                )
                .mappings()
                .all()
            )
        metric_fields = (
            "window_key",
            "window_start",
            "window_end",
            "period_count",
            "valid_ic_count",
            "undefined_ic_count",
            "mean_rank_ic",
            "median_rank_ic",
            "positive_ic_ratio",
            "information_ratio",
            "mean_top_bottom_spread",
            "event_rate",
            "event_asset_concentration",
            "non_neutral_rate",
            "mean_top2_turnover",
        )
        signals: dict[uuid.UUID, dict[str, Any]] = {}
        for row in signal_rows:
            artifact_id = row["signal_dataset_artifact_id"]
            metric = {field: row[field] for field in metric_fields}
            if artifact_id not in signals:
                signals[artifact_id] = {
                    key: row[key]
                    for key in (
                        "signal_dataset_artifact_id",
                        "signal_key",
                        "template_key",
                        "economic_family",
                        "rationale_type",
                        "rationale",
                        "research_tier",
                        "product_eligible",
                        "direction",
                        "normalization",
                        "output_type",
                        "factor_variant_key",
                    )
                }
                signals[artifact_id]["stability"] = []
            if row["window_key"] == "full":
                signals[artifact_id]["full"] = metric
            else:
                signals[artifact_id]["stability"].append(metric)
        if len(signals) != evaluation["signal_count"] or any(
            "full" not in item for item in signals.values()
        ):
            raise LookupError("Published Signal evaluation metrics are incomplete")
        common_periods = {item["full"]["period_count"] for item in signals.values()}
        if len(common_periods) != 1:
            raise LookupError("Published Signal evaluation does not share one common sample")
        result = dict(evaluation)
        result.pop("signal_evaluation_id")
        result["common_period_count"] = next(iter(common_periods))
        result["signals"] = list(signals.values())
        result["pairs"] = [dict(row) for row in pairs]
        result["issues"] = [dict(row) for row in issues]
        return result

    def model_overview(self, frequency: str) -> dict[str, Any]:
        """Return one published Model evaluation with definition and composition metadata."""
        if frequency not in {"weekly", "monthly"}:
            raise ValueError("Model frequency must be weekly or monthly")
        with self._engine.connect() as connection:
            connection.execute(text("SET TRANSACTION READ ONLY"))
            evaluation = (
                connection.execute(
                    text(
                        """
                    SELECT evaluation.model_evaluation_id,
                           evaluation.artifact_id AS evaluation_artifact_id,
                           evaluation.model_catalog_artifact_id,
                           universe.artifact_id AS universe_artifact_id,
                           bundle.artifact_id AS data_bundle_artifact_id,
                           eligibility.artifact_id AS eligibility_artifact_id,
                           model_engine.artifact_id AS model_engine_artifact_id,
                           evaluation_engine.artifact_id AS evaluation_engine_artifact_id,
                           target.artifact_id AS forward_return_artifact_id,
                           target_definition.target_key, evaluation.frequency,
                           evaluation.coverage_start, evaluation.coverage_end,
                           evaluation.model_count, evaluation.pair_count,
                           evaluation.ablation_count,
                           evaluation.high_correlation_threshold
                    FROM model.model_evaluation evaluation
                    JOIN lineage.artifact artifact
                      ON artifact.artifact_id = evaluation.artifact_id
                    JOIN catalog.universe_version universe
                      ON universe.universe_version_id = evaluation.universe_version_id
                    JOIN data.data_bundle_version bundle
                      ON bundle.data_bundle_version_id = evaluation.data_bundle_version_id
                    JOIN catalog.eligibility_snapshot eligibility
                      ON eligibility.eligibility_snapshot_id =
                         evaluation.eligibility_snapshot_id
                    JOIN ops.engine_version model_engine
                      ON model_engine.engine_version_id = evaluation.model_engine_version_id
                    JOIN ops.engine_version evaluation_engine
                      ON evaluation_engine.engine_version_id =
                         evaluation.evaluation_engine_version_id
                    JOIN data.forward_return_dataset target
                      ON target.forward_return_dataset_id =
                         evaluation.forward_return_dataset_id
                    JOIN data.forward_return_version target_version
                      ON target_version.forward_return_version_id =
                         target.forward_return_version_id
                    JOIN data.forward_return_definition target_definition
                      ON target_definition.forward_return_definition_id =
                         target_version.forward_return_definition_id
                    WHERE artifact.status = 'published'
                      AND evaluation.frequency = :frequency
                    ORDER BY evaluation.created_at DESC LIMIT 1
                    """
                    ),
                    {"frequency": frequency},
                )
                .mappings()
                .one_or_none()
            )
            if evaluation is None:
                raise LookupError(f"Published {frequency} Model evaluation not found")
            evaluation_id = evaluation["model_evaluation_id"]
            model_rows = (
                connection.execute(
                    text(
                        """
                    SELECT dataset.model_dataset_id,
                           dataset.artifact_id AS model_dataset_artifact_id,
                           specification.model_specification_id,
                           specification.specification_key,
                           specification.specification_type,
                           definition.model_key, definition.model_family,
                           definition.hypothesis,
                           method.method_key AS overall_method_key,
                           specification.tie_output, specification.output_type,
                           specification.active_dimension_count,
                           specification.component_count,
                           specification.research_tier,
                           metric.window_key, metric.window_start, metric.window_end,
                           metric.period_count, metric.valid_ic_count,
                           metric.undefined_ic_count, metric.mean_rank_ic,
                           metric.median_rank_ic, metric.positive_ic_ratio,
                           metric.information_ratio, metric.mean_top_bottom_spread,
                           metric.non_neutral_rate, metric.mean_top2_turnover,
                           metric.mean_score_dispersion, metric.mean_confidence
                    FROM model.model_evaluation_metric metric
                    JOIN model.model_dataset dataset
                      ON dataset.model_dataset_id = metric.model_dataset_id
                    JOIN model.model_specification specification
                      ON specification.model_specification_id =
                         dataset.model_specification_id
                    JOIN model.model_definition_version definition_version
                      ON definition_version.model_definition_version_id =
                         specification.model_definition_version_id
                    JOIN model.model_definition definition
                      ON definition.model_definition_id =
                         definition_version.model_definition_id
                    JOIN model.model_method_version method_version
                      ON method_version.model_method_version_id =
                         specification.overall_method_version_id
                    JOIN model.model_method_definition method
                      ON method.model_method_definition_id =
                         method_version.model_method_definition_id
                    WHERE metric.model_evaluation_id = :evaluation
                    ORDER BY specification.specification_type,
                             specification.specification_key, metric.window_key
                    """
                    ),
                    {"evaluation": evaluation_id},
                )
                .mappings()
                .all()
            )
            specification_ids = tuple(
                dict.fromkeys(row["model_specification_id"] for row in model_rows)
            )
            composition_rows = (
                connection.execute(
                    text(
                        """
                    SELECT dimension.model_specification_id,
                           dimension.model_dimension_id, dimension.dimension_key,
                           dimension.ordinal AS dimension_ordinal,
                           dimension.input_transform AS dimension_input_transform,
                           dimension.weight AS dimension_weight,
                           method.method_key AS dimension_method_key,
                           component.ordinal AS component_ordinal,
                           component.input_transform AS component_input_transform,
                           component.weight AS component_weight,
                           signal_definition.signal_key
                    FROM model.model_dimension dimension
                    JOIN model.model_method_version method_version
                      ON method_version.model_method_version_id =
                         dimension.method_version_id
                    JOIN model.model_method_definition method
                      ON method.model_method_definition_id =
                         method_version.model_method_definition_id
                    JOIN model.model_component component
                      ON component.model_dimension_id = dimension.model_dimension_id
                    JOIN signal.signal_version signal_version
                      ON signal_version.signal_version_id = component.signal_version_id
                    JOIN signal.signal_definition signal_definition
                      ON signal_definition.signal_definition_id =
                         signal_version.signal_definition_id
                    WHERE dimension.model_specification_id IN :specifications
                    ORDER BY dimension.model_specification_id, dimension.ordinal,
                             component.ordinal
                    """
                    ).bindparams(bindparam("specifications", expanding=True)),
                    {"specifications": specification_ids},
                )
                .mappings()
                .all()
            )
            pairs = (
                connection.execute(
                    text(
                        """
                    SELECT left_specification.specification_key AS left_specification_key,
                           right_specification.specification_key AS right_specification_key,
                           pair.score_observation_count, pair.score_spearman,
                           pair.spread_period_count, pair.spread_correlation,
                           pair.mean_top2_overlap, pair.high_correlation
                    FROM model.model_pair_diagnostic pair
                    JOIN model.model_dataset left_dataset
                      ON left_dataset.model_dataset_id = pair.left_model_dataset_id
                    JOIN model.model_specification left_specification
                      ON left_specification.model_specification_id =
                         left_dataset.model_specification_id
                    JOIN model.model_dataset right_dataset
                      ON right_dataset.model_dataset_id = pair.right_model_dataset_id
                    JOIN model.model_specification right_specification
                      ON right_specification.model_specification_id =
                         right_dataset.model_specification_id
                    WHERE pair.model_evaluation_id = :evaluation
                    ORDER BY pair.high_correlation DESC,
                             abs(pair.score_spearman) DESC NULLS LAST,
                             left_specification.specification_key,
                             right_specification.specification_key
                    """
                    ),
                    {"evaluation": evaluation_id},
                )
                .mappings()
                .all()
            )
            ablations = (
                connection.execute(
                    text(
                        """
                    SELECT full_specification.specification_key AS full_specification_key,
                           ablated_specification.specification_key AS
                               ablated_specification_key,
                           comparison.removed_dimension_key, comparison.window_key,
                           comparison.period_count, comparison.delta_mean_rank_ic,
                           comparison.delta_information_ratio,
                           comparison.delta_mean_top_bottom_spread
                    FROM model.model_ablation_comparison comparison
                    JOIN model.model_dataset full_dataset
                      ON full_dataset.model_dataset_id =
                         comparison.full_model_dataset_id
                    JOIN model.model_specification full_specification
                      ON full_specification.model_specification_id =
                         full_dataset.model_specification_id
                    JOIN model.model_dataset ablated_dataset
                      ON ablated_dataset.model_dataset_id =
                         comparison.ablated_model_dataset_id
                    JOIN model.model_specification ablated_specification
                      ON ablated_specification.model_specification_id =
                         ablated_dataset.model_specification_id
                    WHERE comparison.model_evaluation_id = :evaluation
                    ORDER BY comparison.window_key, full_specification.specification_key,
                             comparison.removed_dimension_key
                    """
                    ),
                    {"evaluation": evaluation_id},
                )
                .mappings()
                .all()
            )
            issues = (
                connection.execute(
                    text(
                        """
                    SELECT specification.specification_key, issue.severity,
                           issue.issue_code, issue.message, issue.details
                    FROM model.model_diagnostic_issue issue
                    JOIN model.model_dataset dataset
                      ON dataset.model_dataset_id = issue.model_dataset_id
                    JOIN model.model_specification specification
                      ON specification.model_specification_id =
                         dataset.model_specification_id
                    WHERE issue.model_evaluation_id = :evaluation
                    ORDER BY CASE issue.severity WHEN 'error' THEN 0
                                  WHEN 'warning' THEN 1 ELSE 2 END,
                             specification.specification_key, issue.issue_code
                    """
                    ),
                    {"evaluation": evaluation_id},
                )
                .mappings()
                .all()
            )
        metric_fields = (
            "window_key",
            "window_start",
            "window_end",
            "period_count",
            "valid_ic_count",
            "undefined_ic_count",
            "mean_rank_ic",
            "median_rank_ic",
            "positive_ic_ratio",
            "information_ratio",
            "mean_top_bottom_spread",
            "non_neutral_rate",
            "mean_top2_turnover",
            "mean_score_dispersion",
            "mean_confidence",
        )
        dimensions: dict[uuid.UUID, list[dict[str, Any]]] = {}
        current_dimension: dict[tuple[uuid.UUID, uuid.UUID], dict[str, Any]] = {}
        for row in composition_rows:
            specification_id = row["model_specification_id"]
            dimension_identity = (specification_id, row["model_dimension_id"])
            if dimension_identity not in current_dimension:
                dimension = {
                    "dimension_key": row["dimension_key"],
                    "method_key": row["dimension_method_key"],
                    "input_transform": row["dimension_input_transform"],
                    "weight": row["dimension_weight"],
                    "components": [],
                }
                current_dimension[dimension_identity] = dimension
                dimensions.setdefault(specification_id, []).append(dimension)
            current_dimension[dimension_identity]["components"].append(
                {
                    "signal_key": row["signal_key"],
                    "input_transform": row["component_input_transform"],
                    "weight": row["component_weight"],
                }
            )
        models: dict[uuid.UUID, dict[str, Any]] = {}
        metadata_fields = (
            "model_dataset_artifact_id",
            "specification_key",
            "specification_type",
            "model_key",
            "model_family",
            "hypothesis",
            "overall_method_key",
            "tie_output",
            "output_type",
            "active_dimension_count",
            "component_count",
            "research_tier",
        )
        for row in model_rows:
            dataset_id = row["model_dataset_id"]
            metric = {field: row[field] for field in metric_fields}
            if dataset_id not in models:
                models[dataset_id] = {field: row[field] for field in metadata_fields}
                models[dataset_id]["dimensions"] = dimensions.get(row["model_specification_id"], [])
                models[dataset_id]["stability"] = []
            if row["window_key"] == "full":
                models[dataset_id]["full"] = metric
            else:
                models[dataset_id]["stability"].append(metric)
        if len(models) != evaluation["model_count"] or any(
            "full" not in item for item in models.values()
        ):
            raise LookupError("Published Model evaluation metrics are incomplete")
        common_periods = {item["full"]["period_count"] for item in models.values()}
        if len(common_periods) != 1:
            raise LookupError("Published Model evaluation does not share one common sample")
        result = dict(evaluation)
        result.pop("model_evaluation_id")
        result["common_period_count"] = next(iter(common_periods))
        result["models"] = list(models.values())
        result["pairs"] = [dict(row) for row in pairs]
        result["ablations"] = [dict(row) for row in ablations]
        result["issues"] = [dict(row) for row in issues]
        return result

    def strategy_overview(self) -> dict[str, Any]:
        """Return published Strategy rules, complete product identities, and target summaries."""
        with self._engine.connect() as connection:
            connection.execute(text("SET TRANSACTION READ ONLY"))
            rule = (
                connection.execute(
                    text("""
                SELECT definition.artifact_id AS definition_artifact_id,
                       version.artifact_id AS version_artifact_id,
                       definition.strategy_key, definition.strategy_family, definition.hypothesis,
                       version.version_number, version.selection_contract,
                       version.allocation_contract, version.reserve_contract,
                       input.compatible_model_output_types, input.candidate_input_policy,
                       input.missing_input_policy, version.strategy_definition_version_id
                FROM strategy.strategy_definition_version version
                JOIN lineage.artifact artifact ON artifact.artifact_id = version.artifact_id
                                             AND artifact.status = 'published'
                JOIN strategy.strategy_definition definition ON definition.strategy_definition_id =
                                                                version.strategy_definition_id
                JOIN strategy.strategy_input_contract input ON
                     input.strategy_definition_version_id =
                     version.strategy_definition_version_id
                ORDER BY version.version_number DESC LIMIT 1
            """)
                )
                .mappings()
                .one_or_none()
            )
            if rule is None:
                raise LookupError("Published Strategy rule set not found")
            definition_version_id = rule["strategy_definition_version_id"]
            variants = (
                connection.execute(
                    text("""
                SELECT variant.artifact_id, variant.variant_key, variant.template_key,
                       variant.target_k, variant.research_tier, variant.selection_order,
                       variant.trend_filter, signal_definition.signal_key AS auxiliary_signal_key,
                       variant.auxiliary_eligible_state, variant.empty_slot_policy,
                       variant.tie_policy, variant.slot_weight_rule, variant.reserve_rule
                FROM strategy.strategy_variant variant
                JOIN lineage.artifact artifact ON artifact.artifact_id = variant.artifact_id
                                             AND artifact.status = 'published'
                LEFT JOIN signal.signal_version signal_version ON signal_version.signal_version_id =
                                                                 variant.auxiliary_signal_version_id
                LEFT JOIN signal.signal_definition signal_definition ON
                     signal_definition.signal_definition_id = signal_version.signal_definition_id
                WHERE variant.strategy_definition_version_id = :version
                ORDER BY variant.template_key, variant.target_k
            """),
                    {"version": definition_version_id},
                )
                .mappings()
                .all()
            )
            schedules = (
                connection.execute(
                    text("""
                SELECT version.artifact_id, definition.schedule_key, version.frequency,
                       version.decision_timing, version.decision_data_policy
                FROM ops.rebalance_schedule_version version
                JOIN lineage.artifact artifact ON artifact.artifact_id = version.artifact_id
                                             AND artifact.status = 'published'
                JOIN ops.rebalance_schedule_definition definition ON
                     definition.rebalance_schedule_definition_id =
                     version.rebalance_schedule_definition_id
                ORDER BY version.frequency
            """)
                )
                .mappings()
                .all()
            )
            execution = (
                connection.execute(
                    text("""
                SELECT version.artifact_id, definition.policy_key,
                       version.delay_common_sessions, version.execution_price,
                       version.missing_execution_policy
                FROM ops.execution_policy_version version
                JOIN lineage.artifact artifact ON artifact.artifact_id = version.artifact_id
                                             AND artifact.status = 'published'
                JOIN ops.execution_policy_definition definition ON
                     definition.execution_policy_definition_id =
                     version.execution_policy_definition_id
                ORDER BY version.version_number DESC LIMIT 1
            """)
                )
                .mappings()
                .one()
            )
            products = (
                connection.execute(
                    text("""
                SELECT product.artifact_id, definition.product_key, product.version_number,
                       specification.specification_key AS model_specification_key,
                       specification.specification_type AS model_specification_type,
                       specification.output_type AS model_output_type,
                       variant.variant_key, variant.target_k, variant.research_tier,
                       universe_definition.universe_key, schedule_definition.schedule_key,
                       schedule.frequency, execution_definition.policy_key AS execution_policy_key,
                       execution.execution_price,
                       count(target.portfolio_target_path_id)
                           FILTER (WHERE target_artifact.status = 'published')::integer
                           AS target_path_count
                FROM strategy.strategy_product_version product
                JOIN lineage.artifact artifact ON artifact.artifact_id = product.artifact_id
                                             AND artifact.status = 'published'
                JOIN strategy.strategy_product_definition definition ON
                     definition.strategy_product_definition_id =
                     product.strategy_product_definition_id
                JOIN model.model_specification specification ON
                     specification.model_specification_id = product.model_specification_id
                JOIN strategy.strategy_variant variant ON variant.strategy_variant_id =
                                                          product.strategy_variant_id
                JOIN catalog.universe_version universe ON universe.universe_version_id =
                                                          product.universe_version_id
                JOIN catalog.universe_definition universe_definition ON
                     universe_definition.universe_definition_id = universe.universe_definition_id
                JOIN ops.rebalance_schedule_version schedule ON
                     schedule.rebalance_schedule_version_id =
                     product.rebalance_schedule_version_id
                JOIN ops.rebalance_schedule_definition schedule_definition ON
                     schedule_definition.rebalance_schedule_definition_id =
                     schedule.rebalance_schedule_definition_id
                JOIN ops.execution_policy_version execution ON
                     execution.execution_policy_version_id =
                     product.execution_policy_version_id
                JOIN ops.execution_policy_definition execution_definition ON
                     execution_definition.execution_policy_definition_id =
                     execution.execution_policy_definition_id
                LEFT JOIN strategy.model_strategy_target_path target ON
                     target.strategy_product_version_id = product.strategy_product_version_id
                LEFT JOIN strategy.portfolio_target_path target_path ON
                     target_path.portfolio_target_path_id = target.portfolio_target_path_id
                LEFT JOIN lineage.artifact target_artifact ON
                     target_artifact.artifact_id = target_path.artifact_id
                GROUP BY product.artifact_id, definition.product_key, product.version_number,
                         specification.specification_key, specification.specification_type,
                         specification.output_type, variant.variant_key, variant.target_k,
                         variant.research_tier, universe_definition.universe_key,
                         schedule_definition.schedule_key, schedule.frequency,
                         execution_definition.policy_key, execution.execution_price
                ORDER BY schedule.frequency, variant.variant_key, specification.specification_key
            """)
                )
                .mappings()
                .all()
            )
            paths = (
                connection.execute(
                    text(
                        _STRATEGY_TARGET_SUMMARY_SQL
                        + " ORDER BY path.coverage_end DESC, definition.product_key"
                    )
                )
                .mappings()
                .all()
            )
        rule_payload = dict(rule)
        rule_payload.pop("strategy_definition_version_id")
        return {
            "rules": {
                **rule_payload,
                "variants": [dict(row) for row in variants],
                "schedules": [dict(row) for row in schedules],
                "execution_policy": dict(execution),
            },
            "products": [dict(row) for row in products],
            "target_paths": [dict(row) for row in paths],
        }

    def strategy_target_path(self, artifact_id: uuid.UUID) -> dict[str, Any]:
        """Return every candidate decision behind one immutable target path."""
        with self._engine.connect() as connection:
            connection.execute(text("SET TRANSACTION READ ONLY"))
            path = (
                connection.execute(
                    text(_STRATEGY_TARGET_SUMMARY_SQL + " AND path.artifact_id = :artifact"),
                    {"artifact": artifact_id},
                )
                .mappings()
                .one_or_none()
            )
            if path is None:
                raise LookupError(f"Published Strategy target path not found: {artifact_id}")
            context = (
                connection.execute(
                    text("""
                SELECT universe.artifact_id AS universe_artifact_id,
                       bundle.artifact_id AS data_bundle_artifact_id,
                       eligibility.artifact_id AS eligibility_artifact_id,
                       engine.artifact_id AS engine_artifact_id,
                       auxiliary.artifact_id AS auxiliary_signal_dataset_artifact_id
                FROM strategy.portfolio_target_path path
                JOIN catalog.universe_version universe ON
                     universe.universe_version_id = path.universe_version_id
                JOIN data.data_bundle_version bundle ON
                     bundle.data_bundle_version_id = path.data_bundle_version_id
                JOIN catalog.eligibility_snapshot eligibility ON
                     eligibility.eligibility_snapshot_id = path.eligibility_snapshot_id
                JOIN ops.engine_version engine ON engine.engine_version_id = path.engine_version_id
                LEFT JOIN strategy.target_path_auxiliary_input auxiliary_input ON
                     auxiliary_input.portfolio_target_path_id = path.portfolio_target_path_id
                LEFT JOIN signal.signal_dataset auxiliary ON
                     auxiliary.signal_dataset_id = auxiliary_input.signal_dataset_id
                WHERE path.artifact_id = :artifact
            """),
                    {"artifact": artifact_id},
                )
                .mappings()
                .one()
            )
            rows = (
                connection.execute(
                    text("""
                SELECT decision.portfolio_decision_id, decision.decision_date, decision.target_k,
                       decision.actual_holding_count, decision.boundary_tie_count,
                       decision.reserve_target_weight, asset.asset_key, symbol.symbol,
                       position.model_score, position.model_rank, position.selection_rank,
                       position.trend_state, position.strategy_eligible, position.selected,
                       position.target_weight, position.decision_reason
                FROM strategy.portfolio_target_path path
                JOIN strategy.portfolio_decision decision ON decision.portfolio_target_path_id =
                                                             path.portfolio_target_path_id
                JOIN strategy.target_asset_position position ON position.portfolio_decision_id =
                                                                decision.portfolio_decision_id
                JOIN catalog.asset asset ON asset.asset_id = position.asset_id
                JOIN LATERAL (
                    SELECT listing_symbol.symbol
                    FROM catalog.asset_listing listing
                    JOIN catalog.listing_symbol ON listing_symbol.asset_listing_id =
                                                   listing.asset_listing_id
                    WHERE listing.asset_id = asset.asset_id
                      AND listing_symbol.symbol_type = 'ticker'
                      AND (listing.valid_from IS NULL OR
                           listing.valid_from <= decision.decision_date)
                      AND (listing.valid_to IS NULL OR listing.valid_to > decision.decision_date)
                      AND (listing_symbol.valid_from IS NULL OR
                           listing_symbol.valid_from <= decision.decision_date)
                      AND (listing_symbol.valid_to IS NULL OR
                           listing_symbol.valid_to > decision.decision_date)
                    ORDER BY listing_symbol.valid_from DESC NULLS LAST
                    LIMIT 1
                ) symbol ON true
                WHERE path.artifact_id = :artifact
                ORDER BY decision.decision_date DESC, position.model_rank, asset.asset_key
            """),
                    {"artifact": artifact_id},
                )
                .mappings()
                .all()
            )
        decisions: dict[uuid.UUID, dict[str, Any]] = {}
        position_fields = (
            "asset_key",
            "symbol",
            "model_score",
            "model_rank",
            "selection_rank",
            "trend_state",
            "strategy_eligible",
            "selected",
            "target_weight",
            "decision_reason",
        )
        for row in rows:
            decision_id = row["portfolio_decision_id"]
            decisions.setdefault(
                decision_id,
                {
                    "decision_date": row["decision_date"],
                    "target_k": row["target_k"],
                    "actual_holding_count": row["actual_holding_count"],
                    "boundary_tie_count": row["boundary_tie_count"],
                    "reserve_target_weight": row["reserve_target_weight"],
                    "positions": [],
                },
            )["positions"].append({key: row[key] for key in position_fields})
        return {"target_path": dict(path), **dict(context), "decisions": list(decisions.values())}

    def experiment_overview(
        self,
        *,
        research_suite_id: uuid.UUID | None = None,
        status: str | None = None,
        template_key: str | None = None,
        frequency: str | None = None,
        cost_bps_per_side: float | None = None,
        ranking_metric: str = "strategy.sharpe_ratio",
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        """Return one server-filtered page of published experiment cells.

        The legacy v0.2 release contains thousands of cells.  Loading every metric for
        every cell made the page size cosmetic only, so filtering and metric ranking
        happen before the display metrics for the requested page are materialized.
        """
        if ranking_metric not in {
            "strategy.sharpe_ratio",
            "strategy.cagr",
            "strategy.maximum_drawdown",
            "relative.annualized_relative_wealth_growth",
            "predictive.mean_rank_ic",
        }:
            raise ValueError(f"Unsupported experiment ranking metric: {ranking_metric}")
        with self._engine.connect() as connection:
            connection.execute(text("SET TRANSACTION READ ONLY"))
            v021_suite_count = int(
                connection.execute(text("SELECT count(*) FROM experiment.research_suite"))
                .scalar_one()
            )
            visible_suite_id = research_suite_id
            if visible_suite_id is not None:
                exists = connection.execute(
                    text(
                        "SELECT 1 FROM experiment.research_suite "
                        "WHERE research_suite_id = :suite_id"
                    ),
                    {"suite_id": visible_suite_id},
                ).scalar_one_or_none()
                if exists is None:
                    raise LookupError(f"Research Suite not found: {visible_suite_id}")
            elif v021_suite_count:
                # Product qualification keeps its exact six Portfolio Cells and one
                # Predictive Cell as immutable evidence.  That evidence belongs on
                # the Product page, not in the ordinary Experiment workbench.  With
                # no explicit Suite URL, show only the latest non-Product Suite.
                visible_suite_id = connection.execute(
                    text("""
                        SELECT suite.research_suite_id
                        FROM experiment.research_suite suite
                        WHERE NOT EXISTS (
                            SELECT 1 FROM experiment.qualification_bundle qualification
                            WHERE qualification.source_suite_artifact_id = suite.artifact_id
                        )
                        ORDER BY suite.created_at DESC, suite.research_suite_id DESC
                        LIMIT 1
                    """)
                ).scalar_one_or_none()

            # Legacy v0.2 results remain a fallback only before the first targeted
            # v0.21 Suite exists.  Once v0.21 is active, mixing generations makes a
            # newly submitted run indistinguishable from retained Product evidence.
            suites = (
                connection.execute(
                    text("""
                SELECT suite.artifact_id, suite.suite_key, suite.version_number, suite.name,
                       suite.description, suite.specification_count
                FROM experiment.experiment_suite suite
                JOIN lineage.artifact artifact ON artifact.artifact_id = suite.artifact_id
                WHERE artifact.status = 'published'
                ORDER BY suite.version_number DESC, suite.suite_key
            """)
                )
                .mappings()
                .all()
            )
            if v021_suite_count:
                suites = []
                specifications = []
                legacy_counts = {
                    "accepted": 0,
                    "failed": 0,
                    "running": 0,
                    "pending": 0,
                    "total": 0,
                }
            else:
                specifications = self._experiment_specifications(
                    connection,
                    template_key=template_key,
                    frequency=frequency,
                    cost_bps_per_side=cost_bps_per_side,
                )
                legacy_counts = self._legacy_experiment_status_counts(connection)
            v021_specifications = (
                self._v021_experiment_specifications(connection, visible_suite_id)
                if visible_suite_id is not None
                else []
            )
            v021_suites = (
                connection.execute(
                    text("""
                SELECT research_suite_id, artifact_id, suite_key, version_number,
                       'v0.21 targeted research suite' AS name,
                       'Immutable compiled v0.21 research matrix' AS description,
                       predictive_cell_count + portfolio_cell_count AS specification_count
                FROM experiment.research_suite
                WHERE research_suite_id = :suite_id
                ORDER BY created_at DESC
            """),
                    {"suite_id": visible_suite_id},
                )
                .mappings()
                .all()
            )
            items: list[dict[str, Any]] = []
            interval_by_artifact: dict[uuid.UUID, uuid.UUID] = {}
            for source in specifications:
                row = dict(source)
                row["suite_mode"] = "legacy"
                interval_id = row.pop("interval_performance_result_id")
                run_status = row.pop("run_status")
                row.pop("run_attempt_id")
                if row["result_artifact_id"] is not None:
                    row["status"] = "accepted"
                elif run_status == "failed":
                    row["status"] = "failed"
                elif run_status in {"queued", "running"}:
                    row["status"] = "running"
                else:
                    row["status"] = "pending"
                row["core_metrics"] = {}
                if row["result_artifact_id"] is not None and interval_id is not None:
                    interval_by_artifact[row["result_artifact_id"]] = interval_id
                items.append(row)
            items.extend(_v021_overview_item(row) for row in v021_specifications)

            counts = {
                key: int(legacy_counts[key]) for key in ("accepted", "failed", "running", "pending")
            }
            for source in v021_specifications:
                counts[_v021_overview_item(source)["status"]] += 1
            filtered = [
                item
                for item in items
                if (status is None or item["status"] == status)
                and (template_key is None or item["template_key"] == template_key)
                and (frequency is None or item["frequency"] == frequency)
                and (
                    cost_bps_per_side is None
                    or float(item["cost_bps_per_side"]) == cost_bps_per_side
                )
            ]

            legacy_candidates = [
                interval_by_artifact[item["result_artifact_id"]]
                for item in filtered
                if item["result_artifact_id"] in interval_by_artifact
            ]
            ranking_values = self._experiment_core_metrics(
                connection,
                legacy_candidates,
                requested_keys=(ranking_metric,),
            )
            for item in filtered:
                result_artifact_id = item["result_artifact_id"]
                interval_id = interval_by_artifact.get(result_artifact_id)
                if interval_id is not None:
                    item["core_metrics"].update(ranking_values.get(interval_id, {}))
            filtered.sort(
                key=lambda item: (
                    item.get("suite_mode") in {"formal", "exploratory"},
                    item["core_metrics"].get(ranking_metric) is not None,
                    item["core_metrics"].get(ranking_metric)
                    if item["core_metrics"].get(ranking_metric) is not None
                    else float("-inf"),
                    str(item["artifact_id"]),
                ),
                reverse=True,
            )
            filtered_count = len(filtered)
            page = filtered[offset : offset + limit]

            page_intervals = [
                interval_by_artifact[item["result_artifact_id"]]
                for item in page
                if item["result_artifact_id"] in interval_by_artifact
            ]
            display_values = self._experiment_core_metrics(
                connection,
                page_intervals,
                requested_keys=(
                    "strategy.cagr",
                    "benchmark.cagr",
                    "strategy.sharpe_ratio",
                    "strategy.maximum_drawdown",
                    "relative.annualized_relative_wealth_growth",
                ),
            )
            for item in page:
                interval_id = interval_by_artifact.get(item["result_artifact_id"])
                if interval_id is not None:
                    item["core_metrics"].update(display_values.get(interval_id, {}))
        return {
            "suites": [dict(row) for row in suites] + [dict(row) for row in v021_suites],
            "specifications": page,
            "total_specification_count": int(legacy_counts["total"]) + len(v021_specifications),
            "filtered_specification_count": filtered_count,
            "accepted_count": counts["accepted"],
            "failed_count": counts["failed"],
            "running_count": counts["running"],
            "pending_count": counts["pending"],
            "limit": limit,
            "offset": offset,
        }

    @staticmethod
    def _experiment_core_metrics(
        connection: Any,
        result_ids: Sequence[uuid.UUID],
        *,
        requested_keys: Sequence[str],
    ) -> dict[uuid.UUID, dict[str, float | None]]:
        if not result_ids:
            return {}
        requested = set(requested_keys)
        rows = (
            connection.execute(
                text("""
                SELECT value.interval_performance_result_id, value.series_role,
                       definition.metric_key, value.metric_value
                FROM experiment.performance_metric_value value
                JOIN experiment.performance_metric_definition definition ON
                     definition.performance_metric_definition_id =
                     value.performance_metric_definition_id
                WHERE value.interval_performance_result_id IN :result_ids
                  AND (value.series_role || '.' || definition.metric_key) IN :metric_keys
            """)
                .bindparams(bindparam("result_ids", expanding=True))
                .bindparams(bindparam("metric_keys", expanding=True)),
                {"result_ids": tuple(set(result_ids)), "metric_keys": tuple(requested)},
            )
            .mappings()
            .all()
        )
        result: dict[uuid.UUID, dict[str, float | None]] = {}
        for row in rows:
            value = row["metric_value"]
            result.setdefault(row["interval_performance_result_id"], {})[
                f"{row['series_role']}.{row['metric_key']}"
            ] = float(value) if value is not None else None
        return result

    def experiment_result(self, artifact_id: uuid.UUID) -> dict[str, Any]:
        """Return one accepted result with metrics, run events, checks, and artifacts."""
        with self._engine.connect() as connection:
            connection.execute(text("SET TRANSACTION READ ONLY"))
            v021 = self._v021_experiment_result(connection, artifact_id)
            if v021 is not None:
                return v021
            rows = self._experiment_specifications(connection, result_artifact_id=artifact_id)
            if not rows:
                raise LookupError(f"Published Experiment result not found: {artifact_id}")
            specification = dict(rows[0])
            interval_id = specification.pop("interval_performance_result_id")
            specification.pop("run_status")
            specification["status"] = "accepted"
            specification["core_metrics"] = {}
            interval = (
                connection.execute(
                    text("""
                SELECT result.artifact_id AS interval_result_artifact_id,
                       result.requested_start, result.requested_end, result.resolved_start,
                       result.resolved_end, result.normalization_nav_date,
                       result.observation_count, result.metric_value_count
                FROM experiment.interval_performance_result result
                WHERE result.interval_performance_result_id = :result
            """),
                    {"result": interval_id},
                )
                .mappings()
                .one()
            )
            metrics = (
                connection.execute(
                    text("""
                SELECT value.series_role, definition.metric_scope, definition.metric_key,
                       definition.name, definition.unit, value.metric_value AS value,
                       value.value_status, value.reason_code, value.observation_count
                FROM experiment.performance_metric_value value
                JOIN experiment.performance_metric_definition definition ON
                     definition.performance_metric_definition_id =
                     value.performance_metric_definition_id
                WHERE value.interval_performance_result_id = :result
                ORDER BY CASE value.series_role WHEN 'strategy' THEN 0
                         WHEN 'benchmark' THEN 1 ELSE 2 END, definition.ordinal
            """),
                    {"result": interval_id},
                )
                .mappings()
                .all()
            )
            run_id = specification["run_attempt_id"]
            run = (
                connection.execute(
                    text("""
                SELECT status AS run_status, started_at, completed_at
                FROM ops.run_attempt WHERE run_attempt_id = :run
            """),
                    {"run": run_id},
                )
                .mappings()
                .one()
            )
            events = (
                connection.execute(
                    text("""
                SELECT sequence_number, event_type, severity, message, occurred_at
                FROM ops.run_event WHERE run_attempt_id = :run ORDER BY sequence_number
            """),
                    {"run": run_id},
                )
                .mappings()
                .all()
            )
            checks = (
                connection.execute(
                    text("""
                SELECT check_key, scope_key, status, severity, message
                FROM ops.quality_check_result WHERE run_attempt_id = :run
                ORDER BY check_key, scope_key
            """),
                    {"run": run_id},
                )
                .mappings()
                .all()
            )
            artifacts = (
                connection.execute(
                    text("""
                SELECT link.artifact_id, link.role, artifact.artifact_type,
                       artifact.artifact_key
                FROM ops.run_artifact link
                JOIN lineage.artifact artifact ON artifact.artifact_id = link.artifact_id
                WHERE link.run_attempt_id = :run
                ORDER BY link.role, artifact.artifact_type, artifact.artifact_key
            """),
                    {"run": run_id},
                )
                .mappings()
                .all()
            )
            nav_rows = (
                connection.execute(
                    text("""
                SELECT strategy.nav_date, strategy.net_nav AS strategy_nav,
                       benchmark.net_nav AS benchmark_nav
                FROM experiment.interval_performance_result result
                JOIN experiment.net_daily_nav strategy
                  ON strategy.net_cost_path_id = result.strategy_net_cost_path_id
                JOIN experiment.net_daily_nav benchmark
                  ON benchmark.net_cost_path_id = result.benchmark_net_cost_path_id
                 AND benchmark.nav_date = strategy.nav_date
                WHERE result.interval_performance_result_id = :result
                  AND strategy.nav_date BETWEEN result.resolved_start AND result.resolved_end
                ORDER BY strategy.nav_date
            """),
                    {"result": interval_id},
                )
                .mappings()
                .all()
            )
        nav_series: list[dict[str, Any]] = []
        if nav_rows:
            strategy_base = float(nav_rows[0]["strategy_nav"])
            benchmark_base = float(nav_rows[0]["benchmark_nav"])
            running_peak = 0.0
            for row in nav_rows:
                strategy_wealth = float(row["strategy_nav"]) / strategy_base
                benchmark_wealth = float(row["benchmark_nav"]) / benchmark_base
                running_peak = max(running_peak, strategy_wealth)
                nav_series.append(
                    {
                        "nav_date": row["nav_date"],
                        "strategy_wealth": strategy_wealth,
                        "benchmark_wealth": benchmark_wealth,
                        "excess_wealth": strategy_wealth / benchmark_wealth,
                        "drawdown": strategy_wealth / running_peak - 1.0,
                    }
                )
            nav_series = _downsample_points(nav_series, maximum=600)
        specification.pop("run_attempt_id")
        specification["attempt_number"] = specification.get("attempt_number")
        return {
            "result_artifact_id": artifact_id,
            "specification": specification,
            **dict(interval),
            "run_attempt_id": run_id,
            **dict(run),
            "metrics": [dict(row) for row in metrics],
            "events": [dict(row) for row in events],
            "quality_checks": [dict(row) for row in checks],
            "artifacts": [dict(row) for row in artifacts],
            "nav_series": nav_series,
            # Legacy v0.2 results cannot be promoted one-by-one. A v0.21 Research
            # Candidate is created only from one complete immutable six-cell bundle.
            "promotion_eligible": False,
            "promotion_reason_codes": [
                "v021_six_cell_qualification_bundle_missing",
                "pit_universe_gate_open",
                "terminal_event_gate_open",
                "impact_policy_gate_open",
            ],
            "qualification_bundle_artifact_id": None,
        }

    def product_ranking(
        self, *, cohort_artifact_id: uuid.UUID | None, metric_key: str
    ) -> dict[str, Any]:
        """Rank accepted product results inside one immutable strict comparison cohort."""
        metric = _RANKING_METRICS.get(metric_key)
        if metric is None:
            raise ValueError(f"Unsupported Product Ranking metric: {metric_key}")
        series_role, metric_scope, definition_key, direction = metric
        with self._engine.connect() as connection:
            connection.execute(text("SET TRANSACTION READ ONLY"))
            cohorts = (
                connection.execute(
                    text("""
                WITH latest_cohort AS (
                    SELECT DISTINCT ON (cohort_version.cohort_key)
                           cohort_version.comparison_cohort_version_id
                    FROM experiment.comparison_cohort_version cohort_version
                    JOIN lineage.artifact cohort_artifact ON
                         cohort_artifact.artifact_id = cohort_version.artifact_id
                         AND cohort_artifact.status = 'published'
                    ORDER BY cohort_version.cohort_key, cohort_version.version_number DESC
                )
                SELECT cohort.artifact_id, cohort.cohort_key, cohort.version_number,
                       cohort.name, cohort.description, cohort.context_fingerprint,
                       cohort.template_key, cohort.initialization_policy, cohort.as_of_date,
                       cohort.target_k, cohort.frequency,
                       cohort.common_data_ready_date, cohort.common_simulation_start,
                       cohort.common_metric_start, cohort.common_metric_end, cohort.currency,
                       cohort.member_count, benchmark_definition.benchmark_key,
                       cost.cost_bps_per_side,
                       warmup.required_observations AS required_warmup_observations
                FROM experiment.comparison_cohort_version cohort
                JOIN latest_cohort latest ON latest.comparison_cohort_version_id =
                                             cohort.comparison_cohort_version_id
                JOIN lineage.artifact artifact ON artifact.artifact_id = cohort.artifact_id
                                             AND artifact.status = 'published'
                JOIN experiment.benchmark_version benchmark ON
                     benchmark.benchmark_version_id = cohort.benchmark_version_id
                JOIN experiment.benchmark_definition benchmark_definition ON
                     benchmark_definition.benchmark_definition_id =
                     benchmark.benchmark_definition_id
                JOIN experiment.cost_scenario cost ON cost.cost_scenario_id =
                                                      cohort.cost_scenario_id
                JOIN experiment.warmup_policy_version warmup ON
                     warmup.warmup_policy_version_id = cohort.warmup_policy_version_id
                ORDER BY
                    CASE cohort.template_key
                        WHEN 'full_history' THEN 0
                        WHEN 'trailing_10_years' THEN 1
                        WHEN 'trailing_5_years' THEN 2
                        WHEN 'trailing_3_years' THEN 3
                        WHEN 'trailing_1_year' THEN 4
                        ELSE 5
                    END,
                    CASE cost.cost_bps_per_side WHEN 5 THEN 0 WHEN 2 THEN 1 ELSE 2 END,
                    CASE cohort.target_k WHEN 2 THEN 0 WHEN 1 THEN 1 ELSE 2 END,
                    cohort.common_simulation_start,
                    cohort.cohort_key
            """)
                )
                .mappings()
                .all()
            )
            if not cohorts:
                return {
                    "cohorts": [],
                    "active_cohort_artifact_id": None,
                    "selected_metric": metric_key,
                    "ranking_direction": direction,
                    "candidate_count": 0,
                    "ranked_count": 0,
                    "entries": [],
                }
            active = (
                next((row for row in cohorts if row["artifact_id"] == cohort_artifact_id), None)
                if cohort_artifact_id
                else cohorts[0]
            )
            if active is None:
                raise LookupError(f"Published Comparison Cohort not found: {cohort_artifact_id}")
            rows = (
                connection.execute(
                    text("""
                SELECT publication.artifact_id AS result_artifact_id,
                       product.artifact_id AS product_artifact_id,
                       product_definition.product_key,
                       model_specification.specification_key AS model_specification_key,
                       variant.variant_key, variant.target_k, schedule.frequency,
                       selected.metric_value, COALESCE(selected.value_status, 'undefined') AS
                       value_status, COALESCE(selected.reason_code, 'metric_not_published') AS
                       reason_code, COALESCE(selected.observation_count, 0) AS observation_count,
                       interval.interval_performance_result_id
                FROM experiment.comparison_cohort_version cohort
                JOIN experiment.comparison_cohort_member member ON
                     member.comparison_cohort_version_id = cohort.comparison_cohort_version_id
                JOIN experiment.result_publication publication ON
                     publication.result_publication_id = member.result_publication_id
                JOIN experiment.experiment_specification experiment_specification ON
                     experiment_specification.experiment_specification_id =
                     publication.experiment_specification_id
                JOIN strategy.model_strategy_target_path target ON
                     target.portfolio_target_path_id =
                     experiment_specification.strategy_target_path_id
                JOIN strategy.strategy_product_version product ON
                     product.strategy_product_version_id = target.strategy_product_version_id
                JOIN strategy.strategy_product_definition product_definition ON
                     product_definition.strategy_product_definition_id =
                     product.strategy_product_definition_id
                JOIN model.model_dataset model_dataset ON model_dataset.model_dataset_id =
                                                          target.model_dataset_id
                JOIN model.model_specification model_specification ON
                     model_specification.model_specification_id =
                     model_dataset.model_specification_id
                JOIN strategy.strategy_variant variant ON variant.strategy_variant_id =
                                                          product.strategy_variant_id
                JOIN ops.rebalance_schedule_version schedule ON
                     schedule.rebalance_schedule_version_id =
                     product.rebalance_schedule_version_id
                JOIN experiment.interval_performance_result interval ON
                     interval.interval_performance_result_id =
                     publication.interval_performance_result_id
                LEFT JOIN experiment.performance_metric_definition definition ON
                     definition.performance_metric_catalog_id =
                     interval.performance_metric_catalog_id
                     AND definition.metric_scope = :metric_scope
                     AND definition.metric_key = :definition_key
                LEFT JOIN experiment.performance_metric_value selected ON
                     selected.interval_performance_result_id =
                     interval.interval_performance_result_id
                     AND selected.performance_metric_definition_id =
                     definition.performance_metric_definition_id
                     AND selected.series_role = :series_role
                WHERE cohort.artifact_id = :cohort
                ORDER BY member.ordinal
            """),
                    {
                        "cohort": active["artifact_id"],
                        "series_role": series_role,
                        "metric_scope": metric_scope,
                        "definition_key": definition_key,
                    },
                )
                .mappings()
                .all()
            )
            interval_ids = tuple(row["interval_performance_result_id"] for row in rows)
            core_by_interval: dict[uuid.UUID, dict[str, float | None]] = {
                interval_id: {} for interval_id in interval_ids
            }
            if interval_ids:
                core_rows = (
                    connection.execute(
                        text("""
                    SELECT value.interval_performance_result_id, value.series_role,
                           definition.metric_key, value.metric_value
                    FROM experiment.performance_metric_value value
                    JOIN experiment.performance_metric_definition definition ON
                         definition.performance_metric_definition_id =
                         value.performance_metric_definition_id
                    WHERE value.interval_performance_result_id IN :result_ids
                      AND ((value.series_role IN ('strategy','benchmark') AND
                            definition.metric_key IN
                            ('cagr','sharpe_ratio','maximum_drawdown'))
                           OR (value.series_role = 'relative' AND definition.metric_key =
                               'annualized_relative_wealth_growth'))
                """).bindparams(bindparam("result_ids", expanding=True)),
                        {"result_ids": interval_ids},
                    )
                    .mappings()
                    .all()
                )
                for core in core_rows:
                    value = core["metric_value"]
                    core_by_interval[core["interval_performance_result_id"]][
                        f"{core['series_role']}.{core['metric_key']}"
                    ] = float(value) if value is not None else None
        rank_values = tuple(
            RankableValue(str(row["result_artifact_id"]), cast(Decimal | None, row["metric_value"]))
            for row in rows
        )
        ranks = competition_ranks(rank_values, cast(Any, direction))
        entries = []
        for source in rows:
            row = dict(source)
            interval_id = row.pop("interval_performance_result_id")
            value = row["metric_value"]
            row["metric_value"] = float(value) if value is not None else None
            if row["value_status"] == "defined":
                row["reason_code"] = None
            row["rank"] = ranks[str(row["result_artifact_id"])]
            row["core_metrics"] = core_by_interval[interval_id]
            entries.append(row)
        entries.sort(
            key=lambda item: (
                item["rank"] is None,
                item["rank"] or 0,
                item["product_key"],
                str(item["result_artifact_id"]),
            )
        )
        return {
            "cohorts": [dict(row) for row in cohorts],
            "active_cohort_artifact_id": active["artifact_id"],
            "selected_metric": metric_key,
            "ranking_direction": direction,
            "candidate_count": len(entries),
            "ranked_count": sum(item["rank"] is not None for item in entries),
            "entries": entries,
        }

    def product_compare(self, *, result_artifact_ids: tuple[uuid.UUID, ...]) -> dict[str, Any]:
        """Compare accepted results and prove whether exactly one research dimension changed."""
        ordered_ids = tuple(dict.fromkeys(result_artifact_ids))
        if len(ordered_ids) < 2 or len(ordered_ids) > 6:
            raise ValueError("Product Compare requires between two and six distinct results")
        with self._engine.connect() as connection:
            connection.execute(text("SET TRANSACTION READ ONLY"))
            rows = (
                connection.execute(
                    text("""
                SELECT publication.artifact_id AS result_artifact_id,
                       product_definition.product_key,
                       model_specification.specification_key AS model_specification_key,
                       model_specification.model_specification_id,
                       variant.template_key AS strategy_template_key, variant.variant_key,
                       concat_ws('|', variant.selection_order, variant.trend_filter,
                           variant.strategy_definition_version_id,
                           variant.strategy_input_contract_id,
                           variant.auxiliary_signal_version_id,
                           variant.auxiliary_eligible_state,
                           variant.empty_slot_policy, variant.tie_policy,
                           variant.slot_weight_rule, variant.reserve_rule) AS strategy_semantics,
                       variant.target_k, schedule.frequency,
                       cost.cost_model_version_id, cost.cost_bps_per_side,
                       specification.template_key, specification.initialization_policy,
                       concat_ws('|', specification.template_key,
                           specification.initialization_policy, specification.as_of_date,
                           specification.custom_start, specification.custom_end)
                           AS interval_semantics,
                       publication.availability_status, publication.quality_status,
                       interval.resolved_start, interval.resolved_end,
                       target.universe_version_id, target.data_bundle_version_id,
                       target.eligibility_snapshot_id, product.execution_policy_version_id,
                       strategy_gross.reserve_return_model_version_id,
                       specification.benchmark_version_id,
                       specification.performance_metric_catalog_id,
                       specification.accounting_engine_version_id,
                       specification.benchmark_engine_version_id,
                       specification.performance_engine_version_id, 'USD' AS currency,
                       publication.interval_performance_result_id
                FROM experiment.result_publication publication
                JOIN lineage.artifact artifact ON artifact.artifact_id = publication.artifact_id
                                             AND artifact.status = 'published'
                JOIN experiment.experiment_specification specification ON
                     specification.experiment_specification_id =
                     publication.experiment_specification_id
                JOIN experiment.interval_performance_result interval ON
                     interval.interval_performance_result_id =
                     publication.interval_performance_result_id
                JOIN experiment.net_cost_path strategy_net ON strategy_net.net_cost_path_id =
                                                              interval.strategy_net_cost_path_id
                JOIN experiment.gross_portfolio_path strategy_gross ON
                     strategy_gross.gross_portfolio_path_id = strategy_net.gross_portfolio_path_id
                JOIN strategy.portfolio_target_path target ON target.portfolio_target_path_id =
                                                              strategy_gross.portfolio_target_path_id
                JOIN strategy.model_strategy_target_path model_path ON
                     model_path.portfolio_target_path_id = target.portfolio_target_path_id
                JOIN strategy.strategy_product_version product ON
                     product.strategy_product_version_id = model_path.strategy_product_version_id
                JOIN strategy.strategy_product_definition product_definition ON
                     product_definition.strategy_product_definition_id =
                     product.strategy_product_definition_id
                JOIN model.model_specification model_specification ON
                     model_specification.model_specification_id = product.model_specification_id
                JOIN strategy.strategy_variant variant ON variant.strategy_variant_id =
                                                          product.strategy_variant_id
                JOIN ops.rebalance_schedule_version schedule ON
                     schedule.rebalance_schedule_version_id = product.rebalance_schedule_version_id
                JOIN experiment.cost_scenario cost ON cost.cost_scenario_id =
                                                      specification.cost_scenario_id
                WHERE publication.artifact_id IN :artifacts
            """).bindparams(bindparam("artifacts", expanding=True)),
                    {"artifacts": ordered_ids},
                )
                .mappings()
                .all()
            )
            by_id = {row["result_artifact_id"]: row for row in rows}
            if len(by_id) != len(ordered_ids):
                raise LookupError("Every Product Compare result must be published and accepted")
            rows = [by_id[item] for item in ordered_ids]
            interval_ids = tuple(row["interval_performance_result_id"] for row in rows)
            metric_rows = (
                connection.execute(
                    text("""
                SELECT value.interval_performance_result_id, value.series_role,
                       definition.metric_scope, definition.metric_key, definition.name,
                       definition.unit, value.metric_value, value.value_status,
                       value.reason_code, value.observation_count
                FROM experiment.performance_metric_value value
                JOIN experiment.performance_metric_definition definition ON
                     definition.performance_metric_definition_id =
                     value.performance_metric_definition_id
                WHERE value.interval_performance_result_id IN :results
                ORDER BY definition.ordinal, value.series_role
            """).bindparams(bindparam("results", expanding=True)),
                    {"results": interval_ids},
                )
                .mappings()
                .all()
            )
        metrics: dict[uuid.UUID, list[dict[str, Any]]] = {item: [] for item in interval_ids}
        for metric in metric_rows:
            payload = dict(metric)
            interval_id = payload.pop("interval_performance_result_id")
            value = payload.pop("metric_value")
            payload["value"] = float(value) if value is not None else None
            metrics[interval_id].append(payload)
        classification = classify_comparison([dict(row) for row in rows])
        entries = []
        visible_fields = (
            "result_artifact_id",
            "product_key",
            "model_specification_key",
            "strategy_template_key",
            "variant_key",
            "target_k",
            "frequency",
            "cost_bps_per_side",
            "template_key",
            "initialization_policy",
            "availability_status",
            "quality_status",
            "resolved_start",
            "resolved_end",
        )
        for row in rows:
            entries.append(
                {
                    **{field: row[field] for field in visible_fields},
                    "metrics": metrics[row["interval_performance_result_id"]],
                }
            )
        return {
            "mode": classification.mode,
            "changed_dimensions": list(classification.changed_dimensions),
            "blocking_context_fields": list(classification.blocking_context_fields),
            "entries": entries,
        }

    def decision_explorer(
        self, *, result_artifact_id: uuid.UUID, decision_date: Any | None
    ) -> dict[str, Any]:
        """Trace one accepted result decision through Model, Signal, Factor, and Data."""
        with self._engine.connect() as connection:
            connection.execute(text("SET TRANSACTION READ ONLY"))
            v021 = self._v021_decision_explorer(
                connection, result_artifact_id=result_artifact_id, decision_date=decision_date
            )
            if v021 is not None:
                return v021
            context = (
                connection.execute(
                    text("""
                SELECT publication.artifact_id AS result_artifact_id,
                       target.artifact_id AS target_path_artifact_id,
                       model_dataset.artifact_id AS model_dataset_artifact_id,
                       model_specification.artifact_id AS model_specification_artifact_id,
                       universe.artifact_id AS universe_artifact_id,
                       bundle.artifact_id AS data_bundle_artifact_id,
                       eligibility.artifact_id AS eligibility_artifact_id,
                       method_definition.method_key, target.portfolio_target_path_id,
                       model_dataset.model_dataset_id
                FROM experiment.result_publication publication
                JOIN lineage.artifact artifact ON artifact.artifact_id = publication.artifact_id
                                             AND artifact.status = 'published'
                JOIN experiment.experiment_specification specification ON
                     specification.experiment_specification_id =
                     publication.experiment_specification_id
                JOIN strategy.portfolio_target_path target ON target.portfolio_target_path_id =
                                                              specification.strategy_target_path_id
                JOIN strategy.model_strategy_target_path model_path ON
                     model_path.portfolio_target_path_id = target.portfolio_target_path_id
                JOIN model.model_dataset model_dataset ON model_dataset.model_dataset_id =
                                                         model_path.model_dataset_id
                JOIN model.model_specification model_specification ON
                     model_specification.model_specification_id =
                     model_dataset.model_specification_id
                JOIN model.model_method_version method_version ON
                     method_version.model_method_version_id =
                     model_specification.overall_method_version_id
                JOIN model.model_method_definition method_definition ON
                     method_definition.model_method_definition_id =
                     method_version.model_method_definition_id
                JOIN catalog.universe_version universe ON universe.universe_version_id =
                                                          target.universe_version_id
                JOIN data.data_bundle_version bundle ON bundle.data_bundle_version_id =
                                                        target.data_bundle_version_id
                JOIN catalog.eligibility_snapshot eligibility ON
                     eligibility.eligibility_snapshot_id = target.eligibility_snapshot_id
                WHERE publication.artifact_id = :result
            """),
                    {"result": result_artifact_id},
                )
                .mappings()
                .one_or_none()
            )
            if context is None:
                raise LookupError(f"Published accepted result not found: {result_artifact_id}")
            dates = tuple(
                connection.execute(
                    text("""
                SELECT decision_date FROM strategy.portfolio_decision
                WHERE portfolio_target_path_id = :path ORDER BY decision_date DESC
            """),
                    {"path": context["portfolio_target_path_id"]},
                ).scalars()
            )
            if not dates:
                raise LookupError("Accepted result target path has no decisions")
            selected_date = decision_date or dates[0]
            if selected_date not in dates:
                raise LookupError(
                    f"Decision date is not present in the target path: {selected_date}"
                )
            positions = (
                connection.execute(
                    text("""
                SELECT decision.target_k, decision.actual_holding_count,
                       decision.reserve_target_weight, asset.asset_id, asset.asset_key,
                       symbol.symbol, position.selected, position.model_score,
                       position.model_rank, position.trend_state, position.target_weight,
                       position.decision_reason
                FROM strategy.portfolio_decision decision
                JOIN strategy.target_asset_position position ON position.portfolio_decision_id =
                                                                decision.portfolio_decision_id
                JOIN catalog.asset asset ON asset.asset_id = position.asset_id
                JOIN LATERAL (
                    SELECT listing_symbol.symbol FROM catalog.asset_listing listing
                    JOIN catalog.listing_symbol ON listing_symbol.asset_listing_id =
                                                   listing.asset_listing_id
                    WHERE listing.asset_id = asset.asset_id
                      AND listing_symbol.symbol_type = 'ticker'
                    ORDER BY listing_symbol.valid_from DESC NULLS LAST LIMIT 1
                ) symbol ON true
                WHERE decision.portfolio_target_path_id = :path
                  AND decision.decision_date = :date
                ORDER BY position.model_rank, asset.asset_key
            """),
                    {"path": context["portfolio_target_path_id"], "date": selected_date},
                )
                .mappings()
                .all()
            )
            component_rows = (
                connection.execute(
                    text("""
                SELECT asset.asset_id, dimension.dimension_key,
                       dimension.weight AS dimension_weight,
                       dimension.input_transform AS dimension_transform,
                       signal_definition.signal_key,
                       signal_version.artifact_id AS signal_version_artifact_id,
                       signal_dataset.artifact_id AS signal_dataset_artifact_id,
                       signal_value.score AS signal_score, signal_value.state AS signal_state,
                       component.input_transform, component.weight AS component_weight,
                       factor_definition.factor_key,
                       factor_variant.variant_key AS factor_variant_key,
                       factor_dataset.artifact_id AS factor_dataset_artifact_id,
                       factor_value.value AS factor_value,
                       bundle.artifact_id AS data_bundle_artifact_id
                FROM model.model_dataset_input input
                JOIN model.model_component component ON component.model_component_id =
                                                        input.model_component_id
                JOIN model.model_dimension dimension ON dimension.model_dimension_id =
                                                        component.model_dimension_id
                JOIN signal.signal_dataset signal_dataset ON signal_dataset.signal_dataset_id =
                                                             input.signal_dataset_id
                JOIN signal.signal_version signal_version ON signal_version.signal_version_id =
                                                             signal_dataset.signal_version_id
                JOIN signal.signal_definition signal_definition ON
                     signal_definition.signal_definition_id = signal_version.signal_definition_id
                JOIN factor.factor_dataset factor_dataset ON factor_dataset.factor_dataset_id =
                                                             signal_dataset.factor_dataset_id
                JOIN factor.factor_variant factor_variant ON factor_variant.factor_variant_id =
                                                             factor_dataset.factor_variant_id
                JOIN factor.factor_definition_version factor_version ON
                     factor_version.factor_definition_version_id =
                     factor_variant.factor_definition_version_id
                JOIN factor.factor_definition factor_definition ON
                     factor_definition.factor_definition_id = factor_version.factor_definition_id
                JOIN data.data_bundle_version bundle ON bundle.data_bundle_version_id =
                                                        factor_dataset.data_bundle_version_id
                JOIN signal.signal_value signal_value ON signal_value.signal_dataset_id =
                                                         signal_dataset.signal_dataset_id
                                                     AND signal_value.observation_date = :date
                JOIN factor.factor_value factor_value ON factor_value.factor_dataset_id =
                                                         factor_dataset.factor_dataset_id
                                                     AND factor_value.asset_id =
                                                         signal_value.asset_id
                                                     AND factor_value.observation_date = :date
                JOIN catalog.asset asset ON asset.asset_id = signal_value.asset_id
                WHERE input.model_dataset_id = :dataset
                ORDER BY asset.asset_key, dimension.ordinal, component.ordinal
            """),
                    {"dataset": context["model_dataset_id"], "date": selected_date},
                )
                .mappings()
                .all()
            )
        components_by_asset: dict[uuid.UUID, list[dict[str, Any]]] = {}
        for source in component_rows:
            row = dict(source)
            asset_id = row.pop("asset_id")
            signal_score = Decimal(row["signal_score"])
            transformed = (
                signal_score
                if row["input_transform"] == "identity"
                else Decimal(1 if signal_score > 0 else -1 if signal_score < 0 else 0)
            )
            weighted = transformed * Decimal(row["component_weight"])
            exact = (
                weighted * Decimal(row["dimension_weight"])
                if context["method_key"] == "weighted_mean"
                and row["dimension_transform"] == "identity"
                else None
            )
            row["signal_score"] = float(signal_score)
            row["dimension_weight"] = float(row["dimension_weight"])
            row["component_weight"] = float(row["component_weight"])
            row["transformed_signal_score"] = float(transformed)
            row["weighted_component_input"] = float(weighted)
            row["overall_contribution"] = float(exact) if exact is not None else None
            components_by_asset.setdefault(asset_id, []).append(row)
        visible_context = {
            key: context[key]
            for key in (
                "result_artifact_id",
                "target_path_artifact_id",
                "model_dataset_artifact_id",
                "model_specification_artifact_id",
                "universe_artifact_id",
                "data_bundle_artifact_id",
                "eligibility_artifact_id",
            )
        }
        first = positions[0]
        return {
            **visible_context,
            "model_method_key": context["method_key"],
            "available_dates": list(dates),
            "selected_date": selected_date,
            "target_k": first["target_k"],
            "actual_holding_count": first["actual_holding_count"],
            "reserve_target_weight": float(first["reserve_target_weight"]),
            "positions": [
                {
                    **{
                        key: (
                            float(row[key])
                            if key in {"model_score", "model_rank", "target_weight"}
                            else row[key]
                        )
                        for key in (
                            "asset_key",
                            "symbol",
                            "selected",
                            "model_score",
                            "model_rank",
                            "trend_state",
                            "target_weight",
                            "decision_reason",
                        )
                    },
                    "components": components_by_asset.get(row["asset_id"], []),
                }
                for row in positions
            ],
        }

    @staticmethod
    def _v021_decision_explorer(
        connection: Any, *, result_artifact_id: uuid.UUID, decision_date: Any | None
    ) -> dict[str, Any] | None:
        row = (
            connection.execute(
                text(
                    """
                    SELECT result.series, result.diagnostics,
                           result.payload_storage_uri, result.payload_content_hash,
                           result.payload_storage_format, result.payload_schema_version,
                           result.payload_byte_size,
                           cell.artifact_id AS cell_artifact_id,
                           spec.artifact_id AS compiled_spec_artifact_id,
                           model.family_key AS model_method_key,
                           strategy.rule_graph -> 'parameters' ->> 'target_k' AS target_k,
                           context.universe_history_artifact_id,
                           context.data_bundle_artifact_id,
                           COALESCE(
                               pit.document ->> 'eligibility_snapshot_artifact_id',
                               context.universe_history_artifact_id::text
                           ) AS eligibility_artifact_id,
                           predictive.series AS predictive_series,
                           predictive.diagnostics AS predictive_diagnostics,
                           predictive.payload_storage_uri
                               AS predictive_payload_storage_uri,
                           predictive.payload_content_hash
                               AS predictive_payload_content_hash,
                           predictive.payload_storage_format
                               AS predictive_payload_storage_format,
                           predictive.payload_schema_version
                               AS predictive_payload_schema_version,
                           predictive.payload_byte_size AS predictive_payload_byte_size
                    FROM experiment.cell_result result
                    JOIN experiment.portfolio_cell_specification cell
                      ON cell.artifact_id = result.cell_artifact_id
                    JOIN strategy.compiled_strategy_version strategy
                      ON strategy.compiled_strategy_version_id = cell.compiled_strategy_version_id
                    JOIN workspace.compiled_model_instance model
                      ON model.compiled_model_instance_id = strategy.compiled_model_instance_id
                    JOIN workspace.compiled_research_spec spec
                      ON spec.compiled_research_spec_id = strategy.compiled_research_spec_id
                    JOIN experiment.research_suite suite
                      ON suite.research_suite_id = cell.research_suite_id
                    JOIN experiment.execution_policy_catalog policy
                      ON policy.execution_policy_catalog_id = suite.execution_policy_catalog_id
                    JOIN experiment.comparison_context context
                      ON context.artifact_id = CAST(
                         policy.document ->> 'comparison_context_artifact_id' AS uuid)
                    LEFT JOIN workspace.release_gate_evidence pit
                      ON pit.artifact_id = CAST(
                         policy.document #>> '{release_gate_artifact_ids,pit_universe}' AS uuid)
                    JOIN experiment.predictive_cell_specification predictive_cell
                      ON predictive_cell.research_suite_id = suite.research_suite_id
                     AND predictive_cell.compiled_model_instance_id =
                         model.compiled_model_instance_id
                    JOIN experiment.cell_result predictive
                      ON predictive.cell_artifact_id = predictive_cell.artifact_id
                     AND predictive.availability_status = 'accepted'
                    WHERE result.artifact_id = :result_id
                      AND result.result_type = 'portfolio'
                    """
                ),
                {"result_id": result_artifact_id},
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            return None
        predictive_result = hydrate_cell_result_row(
            {
                "series": row["predictive_series"],
                "diagnostics": row["predictive_diagnostics"],
                "payload_storage_uri": row["predictive_payload_storage_uri"],
                "payload_content_hash": row["predictive_payload_content_hash"],
                "payload_storage_format": row["predictive_payload_storage_format"],
                "payload_schema_version": row["predictive_payload_schema_version"],
                "payload_byte_size": row["predictive_payload_byte_size"],
            }
        )
        row = hydrate_cell_result_row(row)
        row["predictive_series"] = predictive_result["series"]
        decisions = row["series"].get("decisions", [])
        dates = tuple(date.fromisoformat(item["decision_date"]) for item in decisions)
        if not dates:
            raise LookupError("v0.21 Result has no frozen Decisions")
        selected_date = decision_date or dates[-1]
        if selected_date not in dates:
            raise LookupError(f"Decision date is not present in the Result: {selected_date}")
        decision = next(
            item for item in decisions if item["decision_date"] == selected_date.isoformat()
        )
        selected = {item["asset_key"]: item for item in decision["positions"]}
        scores = [
            item
            for item in row["predictive_series"].get("model_scores", [])
            if item["observation_date"] == selected_date.isoformat()
        ]
        ordered_scores = sorted((Decimal(item["score"]) for item in scores), reverse=True)
        rank_by_score = {
            score: 1 + sum(other > score for other in ordered_scores)
            for score in set(ordered_scores)
        }
        audits = {
            item["asset_key"]: item
            for item in row["predictive_series"].get("model_input_audit", [])
            if item["observation_date"] == selected_date.isoformat()
        }
        positions: list[dict[str, Any]] = []
        for score in sorted(scores, key=lambda item: Decimal(item["score"]), reverse=True):
            asset_key = score["asset_key"]
            components = []
            for source in audits.get(asset_key, {}).get("inputs", []):
                factor = (
                    connection.execute(
                        text(
                            """
                            SELECT definition.factor_key, variant.variant_key
                            FROM factor.factor_variant variant
                            JOIN factor.factor_definition_version version
                              ON version.factor_definition_version_id =
                                 variant.factor_definition_version_id
                            JOIN factor.factor_definition definition
                              ON definition.factor_definition_id = version.factor_definition_id
                            WHERE variant.artifact_id = :factor_variant_artifact_id
                            """
                        ),
                        {
                            "factor_variant_artifact_id": uuid.UUID(
                                source["factor_variant_artifact_id"]
                            ),
                        },
                    )
                    .mappings()
                    .one()
                )
                components.append(
                    {
                        "dimension_key": source["dimension_key"],
                        "dimension_weight": 1.0,
                        "dimension_transform": "common_valid_asset_cross_section",
                        "signal_key": source["signal_version_key"],
                        "signal_version_artifact_id": source["signal_version_artifact_id"],
                        "signal_dataset_artifact_id": source["signal_dataset_artifact_id"],
                        "signal_score": float(source["raw_signal_value"]),
                        "signal_state": None,
                        "input_transform": "branch_local_centered_percentile_rank_-1_1",
                        "component_weight": 1.0,
                        "transformed_signal_score": float(source["normalized_input_value"]),
                        "weighted_component_input": float(source["contribution"]),
                        "overall_contribution": float(source["contribution"]),
                        "factor_key": factor["factor_key"],
                        "factor_variant_key": factor["variant_key"],
                        # Ad-hoc Workspace Factor values are computed on demand rather
                        # than published as a canonical Factor Dataset snapshot.
                        "factor_dataset_artifact_id": source[
                            "factor_variant_artifact_id"
                        ],
                        "factor_value": None,
                        "data_bundle_artifact_id": row["data_bundle_artifact_id"],
                    }
                )
            model_score = Decimal(score["score"])
            selection = selected.get(asset_key)
            positions.append(
                {
                    "asset_key": asset_key,
                    "symbol": asset_key,
                    "selected": selection is not None,
                    "model_score": float(model_score),
                    "model_rank": float(rank_by_score[model_score]),
                    "trend_state": None,
                    "target_weight": float(selection["target_weight"]) if selection else 0.0,
                    "decision_reason": "selected_top_k" if selection else "outside_top_k",
                    "components": components,
                }
            )
        return {
            "result_artifact_id": result_artifact_id,
            "target_path_artifact_id": row["cell_artifact_id"],
            "model_dataset_artifact_id": row["compiled_spec_artifact_id"],
            "model_specification_artifact_id": row["compiled_spec_artifact_id"],
            "universe_artifact_id": row["universe_history_artifact_id"],
            "data_bundle_artifact_id": row["data_bundle_artifact_id"],
            "eligibility_artifact_id": uuid.UUID(row["eligibility_artifact_id"]),
            "model_method_key": row["model_method_key"],
            "available_dates": list(dates),
            "selected_date": selected_date,
            "target_k": int(row["target_k"]),
            "actual_holding_count": len(selected),
            "reserve_target_weight": float(
                decision.get("reserve_target_weight", decision["defense_budget"])
            ),
            "positions": positions,
        }

    @staticmethod
    def _v021_experiment_specifications(
        connection: Any, research_suite_id: uuid.UUID
    ) -> list[Any]:
        return list(
            connection.execute(
                text("""
                SELECT cell.artifact_id, result.artifact_id AS result_artifact_id,
                       suite.artifact_id AS suite_artifact_id, suite.suite_mode,
                       cell.cell_key, cell.ordinal,
                       strategy.branch_key AS product_key,
                       model.preset_key AS model_specification_key,
                       strategy.strategy_preset_key AS variant_key, spec.frequency,
                       'SPY' AS benchmark_key, 'equity' AS benchmark_category,
                       cell.cost_bps_per_side::float AS cost_bps_per_side,
                       CASE cell.window_key WHEN 'full_common_history' THEN 'full_history'
                            ELSE cell.window_key END AS template_key,
                       cell.initialization_policy, NULL::date AS as_of_date,
                       NULL::date AS simulation_end, result.availability_status,
                       result.quality_status, item.work_item_id AS run_attempt_id,
                       item.attempt_count AS attempt_number, item.status AS run_status,
                       item.failure_details ->> 'message' AS error_summary,
                       result.metrics
                FROM experiment.portfolio_cell_specification cell
                JOIN experiment.research_suite suite
                  ON suite.research_suite_id = cell.research_suite_id
                JOIN strategy.compiled_strategy_version strategy
                  ON strategy.compiled_strategy_version_id = cell.compiled_strategy_version_id
                JOIN workspace.compiled_model_instance model
                  ON model.compiled_model_instance_id = strategy.compiled_model_instance_id
                JOIN workspace.compiled_research_spec spec
                  ON spec.compiled_research_spec_id = strategy.compiled_research_spec_id
                LEFT JOIN experiment.research_suite_work_item link
                  ON link.research_suite_id = cell.research_suite_id
                 AND link.cell_artifact_id = cell.artifact_id
                LEFT JOIN ops.work_item item ON item.work_item_id = link.work_item_id
                LEFT JOIN experiment.cell_result result
                  ON result.cell_artifact_id = cell.artifact_id
                WHERE suite.research_suite_id = :suite_id
                UNION ALL
                SELECT cell.artifact_id, result.artifact_id AS result_artifact_id,
                       suite.artifact_id AS suite_artifact_id, suite.suite_mode,
                       cell.cell_key, 0 AS ordinal,
                       'predictive_diagnostic' AS product_key,
                       model.preset_key AS model_specification_key,
                       model.preset_key AS variant_key, cell.frequency,
                       cell.evaluation_target_key AS benchmark_key,
                       'predictive_target' AS benchmark_category,
                       0::float AS cost_bps_per_side,
                       'predictive_diagnostic' AS template_key,
                       'compiled_model_input' AS initialization_policy,
                       NULL::date AS as_of_date, NULL::date AS simulation_end,
                       result.availability_status, result.quality_status,
                       item.work_item_id AS run_attempt_id,
                       item.attempt_count AS attempt_number, item.status AS run_status,
                       item.failure_details ->> 'message' AS error_summary,
                       result.metrics
                FROM experiment.predictive_cell_specification cell
                JOIN experiment.research_suite suite
                  ON suite.research_suite_id = cell.research_suite_id
                JOIN workspace.compiled_model_instance model
                  ON model.compiled_model_instance_id = cell.compiled_model_instance_id
                LEFT JOIN experiment.research_suite_work_item link
                  ON link.research_suite_id = cell.research_suite_id
                 AND link.cell_artifact_id = cell.artifact_id
                LEFT JOIN ops.work_item item ON item.work_item_id = link.work_item_id
                LEFT JOIN experiment.cell_result result
                  ON result.cell_artifact_id = cell.artifact_id
                WHERE suite.research_suite_id = :suite_id
                ORDER BY suite_artifact_id DESC, product_key, ordinal
            """),
                {"suite_id": research_suite_id},
            )
            .mappings()
            .all()
        )

    @staticmethod
    def _v021_experiment_result(
        connection: Any, result_artifact_id: uuid.UUID
    ) -> dict[str, Any] | None:
        row = (
            connection.execute(
                text("""
            SELECT source.*, artifact.created_at AS result_created_at
            FROM (
                SELECT cell.artifact_id, result.artifact_id AS result_artifact_id,
                       suite.artifact_id AS suite_artifact_id, suite.suite_mode,
                       cell.cell_key, cell.ordinal,
                       strategy.branch_key AS product_key,
                       model.preset_key AS model_specification_key,
                       strategy.strategy_preset_key AS variant_key, spec.frequency,
                       'SPY' AS benchmark_key, 'equity' AS benchmark_category,
                       cell.cost_bps_per_side::float AS cost_bps_per_side,
                       CASE cell.window_key WHEN 'full_common_history' THEN 'full_history'
                            ELSE cell.window_key END AS template_key,
                       cell.initialization_policy, result.availability_status,
                       result.quality_status, item.work_item_id AS run_attempt_id,
                       item.attempt_count AS attempt_number, item.status AS run_status,
                       item.created_at AS started_at, item.updated_at AS completed_at,
                       item.failure_details ->> 'message' AS error_summary,
                        result.metrics, result.series, result.diagnostics,
                        result.payload_storage_uri, result.payload_content_hash,
                        result.payload_storage_format, result.payload_schema_version,
                        result.payload_byte_size
                FROM experiment.cell_result result
                JOIN experiment.portfolio_cell_specification cell
                  ON cell.artifact_id = result.cell_artifact_id
                JOIN experiment.research_suite suite
                  ON suite.research_suite_id = cell.research_suite_id
                JOIN strategy.compiled_strategy_version strategy
                  ON strategy.compiled_strategy_version_id = cell.compiled_strategy_version_id
                JOIN workspace.compiled_model_instance model
                  ON model.compiled_model_instance_id = strategy.compiled_model_instance_id
                JOIN workspace.compiled_research_spec spec
                  ON spec.compiled_research_spec_id = strategy.compiled_research_spec_id
                JOIN ops.work_item item ON item.work_item_id = result.work_item_id
                WHERE result.artifact_id = :result_id
                UNION ALL
                SELECT cell.artifact_id, result.artifact_id AS result_artifact_id,
                       suite.artifact_id AS suite_artifact_id, suite.suite_mode,
                       cell.cell_key, 0 AS ordinal,
                       'predictive_diagnostic' AS product_key,
                       model.preset_key AS model_specification_key,
                       model.preset_key AS variant_key, cell.frequency,
                       cell.evaluation_target_key AS benchmark_key,
                       'predictive_target' AS benchmark_category,
                       0::float AS cost_bps_per_side,
                       'predictive_diagnostic' AS template_key,
                       'compiled_model_input' AS initialization_policy,
                       result.availability_status, result.quality_status,
                       item.work_item_id AS run_attempt_id,
                       item.attempt_count AS attempt_number, item.status AS run_status,
                       item.created_at AS started_at, item.updated_at AS completed_at,
                       item.failure_details ->> 'message' AS error_summary,
                       result.metrics, result.series, result.diagnostics,
                       result.payload_storage_uri, result.payload_content_hash,
                       result.payload_storage_format, result.payload_schema_version,
                       result.payload_byte_size
                FROM experiment.cell_result result
                JOIN experiment.predictive_cell_specification cell
                  ON cell.artifact_id = result.cell_artifact_id
                JOIN experiment.research_suite suite
                  ON suite.research_suite_id = cell.research_suite_id
                JOIN workspace.compiled_model_instance model
                  ON model.compiled_model_instance_id = cell.compiled_model_instance_id
                JOIN ops.work_item item ON item.work_item_id = result.work_item_id
                WHERE result.artifact_id = :result_id
            ) source
            JOIN lineage.artifact artifact ON artifact.artifact_id = source.result_artifact_id
            WHERE artifact.status = 'published'
        """),
                {"result_id": result_artifact_id},
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            return None
        row = hydrate_cell_result_row(row)
        specification = _v021_overview_item(row)
        metrics = _v021_metric_items(row["metrics"] or {})
        nav_series = _v021_nav_series(row["series"] or {})
        diagnostics = row["diagnostics"] or {}
        events = [
            {
                "sequence_number": event["sequence_number"],
                "event_type": event["event_type"],
                "severity": "info",
                "message": (event["details"] or {}).get("message", event["event_type"]),
                "occurred_at": event["created_at"],
            }
            for event in connection.execute(
                text(
                    "SELECT sequence_number, event_type, details, occurred_at AS created_at "
                    "FROM ops.work_item_event WHERE work_item_id = :id "
                    "ORDER BY sequence_number"
                ),
                {"id": row["run_attempt_id"]},
            )
            .mappings()
            .all()
        ]
        checks = [
            {
                "check_key": check.get("check_key", f"quality_check_{index + 1}"),
                "scope_key": check.get("scope_key", "v021_cell"),
                "status": check.get("status", "unknown"),
                "severity": check.get("severity", "info"),
                "message": check.get("message", check.get("check_key", "quality check")),
            }
            for index, check in enumerate(diagnostics.get("quality_checks", []))
            if isinstance(check, dict)
        ]
        resolved_start = _optional_date(diagnostics.get("resolved_start"))
        resolved_end = _optional_date(diagnostics.get("resolved_end"))
        return {
            "result_artifact_id": row["result_artifact_id"],
            "specification": specification,
            "interval_result_artifact_id": row["result_artifact_id"],
            "requested_start": _optional_date(diagnostics.get("requested_start")),
            "requested_end": _optional_date(diagnostics.get("requested_end")),
            "resolved_start": resolved_start,
            "resolved_end": resolved_end,
            "normalization_nav_date": _optional_date(diagnostics.get("normalization_nav_date")),
            "observation_count": int(diagnostics.get("observation_count", len(nav_series))),
            "metric_value_count": len(metrics),
            "run_attempt_id": row["run_attempt_id"],
            "run_status": row["run_status"],
            "started_at": row["started_at"],
            "completed_at": row["completed_at"],
            "metrics": metrics,
            "events": events,
            "quality_checks": checks,
            "artifacts": [
                {
                    "artifact_id": row["result_artifact_id"],
                    "role": "result",
                    "artifact_type": "v021_cell_result",
                    "artifact_key": str(row["result_artifact_id"]),
                },
                {
                    "artifact_id": row["artifact_id"],
                    "role": "cell_specification",
                    "artifact_type": (
                        "predictive_cell_specification"
                        if row["template_key"] == "predictive_diagnostic"
                        else "portfolio_cell_specification"
                    ),
                    "artifact_key": row["cell_key"],
                },
            ],
            "nav_series": nav_series,
            "promotion_eligible": False,
            "promotion_reason_codes": [],
            "qualification_bundle_artifact_id": None,
        }

    @staticmethod
    def _experiment_specifications(
        connection: Any,
        *,
        result_artifact_id: uuid.UUID | None = None,
        template_key: str | None = None,
        frequency: str | None = None,
        cost_bps_per_side: float | None = None,
    ) -> list[Any]:
        filters: list[str] = []
        parameters: dict[str, Any] = {}
        if result_artifact_id is not None:
            filters.append("publication.artifact_id = :result_artifact")
            parameters["result_artifact"] = result_artifact_id
        if template_key is not None:
            filters.append("specification.template_key = :template_key")
            parameters["template_key"] = template_key
        if frequency is not None:
            filters.append("schedule.frequency = :frequency")
            parameters["frequency"] = frequency
        if cost_bps_per_side is not None:
            filters.append("cost.cost_bps_per_side = :cost_bps_per_side")
            parameters["cost_bps_per_side"] = cost_bps_per_side
        extra_filter = "".join(f" AND {condition}" for condition in filters)
        statement = text(
            """
            SELECT specification.artifact_id, publication.artifact_id AS result_artifact_id,
                   suite.artifact_id AS suite_artifact_id, cell.cell_key, cell.ordinal,
                   product_definition.product_key, model_specification.specification_key AS
                   model_specification_key, variant.variant_key, schedule.frequency,
                   benchmark_definition.benchmark_key,
                   benchmark_definition.category AS benchmark_category,
                   cost.cost_bps_per_side, specification.template_key,
                   specification.initialization_policy, specification.as_of_date,
                   specification.simulation_end, publication.availability_status,
                   publication.quality_status, latest.run_attempt_id,
                   latest.attempt_number, latest.status AS run_status, latest.error_summary,
                   publication.interval_performance_result_id
            FROM experiment.experiment_suite_cell cell
            JOIN experiment.experiment_suite suite ON suite.experiment_suite_id =
                                                     cell.experiment_suite_id
            JOIN lineage.artifact suite_artifact ON suite_artifact.artifact_id = suite.artifact_id
                                                AND suite_artifact.status = 'published'
            JOIN experiment.experiment_specification specification ON
                 specification.experiment_specification_id = cell.experiment_specification_id
            JOIN lineage.artifact specification_artifact ON
                 specification_artifact.artifact_id = specification.artifact_id
                 AND specification_artifact.status = 'published'
            JOIN strategy.model_strategy_target_path model_path ON
                 model_path.portfolio_target_path_id = specification.strategy_target_path_id
            JOIN strategy.strategy_product_version product ON
                 product.strategy_product_version_id = model_path.strategy_product_version_id
            JOIN strategy.strategy_product_definition product_definition ON
                 product_definition.strategy_product_definition_id =
                 product.strategy_product_definition_id
            JOIN model.model_specification model_specification ON
                 model_specification.model_specification_id = product.model_specification_id
            JOIN strategy.strategy_variant variant ON variant.strategy_variant_id =
                                                      product.strategy_variant_id
            JOIN ops.rebalance_schedule_version schedule ON
                 schedule.rebalance_schedule_version_id = product.rebalance_schedule_version_id
            JOIN experiment.benchmark_version benchmark ON benchmark.benchmark_version_id =
                                                           specification.benchmark_version_id
            JOIN experiment.benchmark_definition benchmark_definition ON
                 benchmark_definition.benchmark_definition_id = benchmark.benchmark_definition_id
            JOIN experiment.cost_scenario cost ON cost.cost_scenario_id =
                                                  specification.cost_scenario_id
            LEFT JOIN experiment.result_publication publication ON
                 publication.experiment_specification_id = specification.experiment_specification_id
            LEFT JOIN lineage.artifact publication_artifact ON
                 publication_artifact.artifact_id = publication.artifact_id
                 AND publication_artifact.status = 'published'
            LEFT JOIN LATERAL (
                SELECT attempt.run_attempt_id, attempt.attempt_number, attempt.status,
                       attempt.error_summary
                FROM ops.run_attempt attempt
                WHERE attempt.root_artifact_id = specification.artifact_id
                  AND attempt.run_type = 'experiment_specification'
                ORDER BY attempt.attempt_number DESC LIMIT 1
            ) latest ON true
            WHERE (publication.artifact_id IS NULL OR publication_artifact.artifact_id IS NOT NULL)
        """
            + extra_filter
            + " ORDER BY suite.version_number DESC, cell.ordinal"
        )
        return list(connection.execute(statement, parameters).mappings().all())

    @staticmethod
    def _legacy_experiment_status_counts(connection: Any) -> dict[str, int]:
        row = (
            connection.execute(
                text("""
                WITH latest AS (
                    SELECT DISTINCT ON (attempt.root_artifact_id)
                           attempt.root_artifact_id, attempt.status
                    FROM ops.run_attempt attempt
                    WHERE attempt.run_type = 'experiment_specification'
                    ORDER BY attempt.root_artifact_id, attempt.attempt_number DESC
                )
                SELECT count(*)::integer AS total,
                       count(*) FILTER (
                           WHERE publication.artifact_id IS NOT NULL
                       )::integer AS accepted,
                       count(*) FILTER (
                           WHERE publication.artifact_id IS NULL
                             AND latest.status = 'failed'
                       )::integer AS failed,
                       count(*) FILTER (
                           WHERE publication.artifact_id IS NULL
                             AND latest.status IN ('queued', 'running')
                       )::integer AS running,
                       count(*) FILTER (
                           WHERE publication.artifact_id IS NULL
                             AND (latest.status IS NULL OR latest.status NOT IN
                                  ('failed', 'queued', 'running'))
                       )::integer AS pending
                FROM experiment.experiment_suite_cell cell
                JOIN experiment.experiment_suite suite
                  ON suite.experiment_suite_id = cell.experiment_suite_id
                JOIN lineage.artifact suite_artifact
                  ON suite_artifact.artifact_id = suite.artifact_id
                 AND suite_artifact.status = 'published'
                JOIN experiment.experiment_specification specification
                  ON specification.experiment_specification_id =
                     cell.experiment_specification_id
                JOIN lineage.artifact specification_artifact
                  ON specification_artifact.artifact_id = specification.artifact_id
                 AND specification_artifact.status = 'published'
                LEFT JOIN experiment.result_publication publication
                  ON publication.experiment_specification_id =
                     specification.experiment_specification_id
                LEFT JOIN lineage.artifact publication_artifact
                  ON publication_artifact.artifact_id = publication.artifact_id
                 AND publication_artifact.status = 'published'
                LEFT JOIN latest ON latest.root_artifact_id = specification.artifact_id
                WHERE publication.artifact_id IS NULL
                   OR publication_artifact.artifact_id IS NOT NULL
            """)
            )
            .mappings()
            .one()
        )
        return {key: int(row[key]) for key in ("total", "accepted", "failed", "running", "pending")}

    @staticmethod
    def _latest_bundle(connection: Any) -> dict[str, Any] | None:
        row = (
            connection.execute(
                text(
                    """
                SELECT version.data_bundle_version_id, version.artifact_id,
                       definition.bundle_key, definition.name, version.version_number,
                       version.coverage_start, version.coverage_end, version.member_count
                FROM data.data_bundle_version version
                JOIN data.data_bundle_definition definition
                  ON definition.data_bundle_definition_id = version.data_bundle_definition_id
                JOIN lineage.artifact artifact ON artifact.artifact_id = version.artifact_id
                WHERE artifact.status = 'published'
                ORDER BY version.version_number DESC, version.created_at DESC LIMIT 1
                """
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            return None
        members = (
            connection.execute(
                text(
                    """
                SELECT member.role, member.ordinal, artifact.artifact_id,
                       artifact.artifact_type, artifact.artifact_key,
                       artifact.version_number
                FROM data.data_bundle_member member
                JOIN lineage.artifact artifact ON artifact.artifact_id = COALESCE(
                    (SELECT publication.artifact_id FROM data.dataset_publication publication
                     WHERE publication.dataset_publication_id = member.dataset_publication_id),
                    (SELECT calendar.artifact_id FROM catalog.calendar_version calendar
                     WHERE calendar.calendar_version_id = member.calendar_version_id)
                )
                WHERE member.data_bundle_version_id = :version_id
                ORDER BY member.ordinal, member.role
                """
                ),
                {"version_id": row["data_bundle_version_id"]},
            )
            .mappings()
            .all()
        )
        result = dict(row)
        result.pop("data_bundle_version_id")
        result["members"] = [dict(member) for member in members]
        return result

    @staticmethod
    def _latest_eligibility(connection: Any) -> dict[str, Any] | None:
        row = (
            connection.execute(
                text(
                    """
                SELECT snapshot.eligibility_snapshot_id, snapshot.artifact_id,
                       snapshot.snapshot_key, snapshot.requested_start,
                       snapshot.requested_end, snapshot.warmup_observations,
                       snapshot.member_count, snapshot.eligible_count
                FROM catalog.eligibility_snapshot snapshot
                JOIN lineage.artifact artifact ON artifact.artifact_id = snapshot.artifact_id
                WHERE artifact.status = 'published'
                ORDER BY artifact.version_number DESC, snapshot.created_at DESC LIMIT 1
                """
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            return None
        items = (
            connection.execute(
                text(
                    """
                SELECT item.eligibility_item_id, item.asset_id, asset.asset_key,
                       symbol.symbol, item.role, item.is_eligible, item.available_start,
                       item.available_end, item.data_ready_date, item.observation_count
                FROM catalog.eligibility_item item
                JOIN catalog.asset asset ON asset.asset_id = item.asset_id
                JOIN LATERAL (
                    SELECT listing_symbol.symbol
                    FROM catalog.asset_listing listing
                    JOIN catalog.listing_symbol
                      ON listing_symbol.asset_listing_id = listing.asset_listing_id
                    WHERE listing.asset_id = asset.asset_id
                      AND listing_symbol.symbol_type = 'ticker'
                    ORDER BY listing_symbol.valid_to NULLS FIRST,
                             listing_symbol.valid_from DESC NULLS LAST
                    LIMIT 1
                ) symbol ON true
                WHERE item.eligibility_snapshot_id = :snapshot_id
                ORDER BY item.role, asset.asset_key
                """
                ),
                {"snapshot_id": row["eligibility_snapshot_id"]},
            )
            .mappings()
            .all()
        )
        item_ids = [item["eligibility_item_id"] for item in items]
        issues_by_item: dict[uuid.UUID, list[dict[str, Any]]] = {
            item_id: [] for item_id in item_ids
        }
        if item_ids:
            issues = (
                connection.execute(
                    text(
                        """
                    SELECT eligibility_item_id, severity, issue_code, message, details
                    FROM catalog.eligibility_issue
                    WHERE eligibility_item_id IN :item_ids
                    ORDER BY eligibility_item_id, severity DESC, issue_code
                    """
                    ).bindparams(bindparam("item_ids", expanding=True)),
                    {"item_ids": tuple(item_ids)},
                )
                .mappings()
                .all()
            )
            for issue in issues:
                issue_item = dict(issue)
                item_id = issue_item.pop("eligibility_item_id")
                issues_by_item[item_id].append(issue_item)
        eligibility_items: list[dict[str, Any]] = []
        for item in items:
            item_payload = dict(item)
            item_id = item_payload.pop("eligibility_item_id")
            item_payload["issues"] = issues_by_item[item_id]
            eligibility_items.append(item_payload)
        result = dict(row)
        result.pop("eligibility_snapshot_id")
        result["items"] = eligibility_items
        return result
