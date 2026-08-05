from __future__ import annotations

import uuid
from collections.abc import Sequence
from decimal import Decimal
from typing import Any, cast

from sqlalchemy import Engine, bindparam, text
from sqlalchemy.engine import RowMapping

from style_rotation.experiment.compare import classify_comparison
from style_rotation.experiment.ranking import RankableValue, competition_ranks

_RANKING_METRICS = {
    "net_sharpe": ("strategy", "absolute", "sharpe_ratio", "higher_is_better"),
    "net_cagr": ("strategy", "absolute", "cagr", "higher_is_better"),
    "relative_wealth_growth": (
        "relative", "relative", "annualized_relative_wealth_growth", "higher_is_better"
    ),
    "maximum_drawdown": (
        "strategy", "absolute", "maximum_drawdown", "higher_is_better"
    ),
    "calmar": ("strategy", "absolute", "calmar_ratio", "higher_is_better"),
}

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

    def asset_catalog(self) -> dict[str, Any]:
        with self._engine.connect() as connection:
            connection.execute(text("SET TRANSACTION READ ONLY"))
            release = (
                connection.execute(
                    text(
                        """
                    SELECT release.master_data_release_id, release.artifact_id,
                           release.version_number, release.as_of_date
                    FROM catalog.master_data_release release
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
                raise LookupError("Published research-scope catalog not found")
            universe = (
                connection.execute(
                    text(
                        """
                    SELECT definition.universe_key, version.universe_version_id
                    FROM catalog.universe_version version
                    JOIN catalog.universe_definition definition
                      ON definition.universe_definition_id = version.universe_definition_id
                    JOIN lineage.artifact artifact ON artifact.artifact_id = version.artifact_id
                    WHERE artifact.status = 'published'
                    ORDER BY version.version_number DESC LIMIT 1
                    """
                    )
                )
                .mappings()
                .one_or_none()
            )
            if universe is None:
                raise LookupError("Published research universe not found")
            rows = (
                connection.execute(
                    text(
                        """
                    SELECT asset.asset_id, asset.asset_key, asset.name, asset.asset_type,
                           asset.status, symbol.symbol, listing.venue_mic, listing.currency,
                           listing.timezone, calendar.calendar_key,
                           member.role AS universe_role, member.ordinal AS universe_ordinal,
                           COALESCE(jsonb_object_agg(scheme.scheme_key, value.value_key)
                               FILTER (WHERE scheme.scheme_key IS NOT NULL), '{}'::jsonb)
                               AS classifications
                    FROM catalog.asset asset
                    JOIN catalog.asset_listing listing ON listing.asset_id = asset.asset_id
                    JOIN catalog.listing_symbol symbol
                      ON symbol.asset_listing_id = listing.asset_listing_id
                     AND symbol.symbol_type = 'ticker'
                    JOIN catalog.calendar_definition calendar
                      ON calendar.calendar_definition_id = listing.calendar_definition_id
                    LEFT JOIN catalog.asset_classification assignment
                      ON assignment.asset_id = asset.asset_id
                    LEFT JOIN catalog.classification_value value
                      ON value.classification_value_id = assignment.classification_value_id
                    LEFT JOIN catalog.classification_scheme scheme
                      ON scheme.classification_scheme_id = value.classification_scheme_id
                    LEFT JOIN catalog.universe_member member
                      ON member.asset_id = asset.asset_id
                     AND member.universe_version_id = :universe_version_id
                    WHERE asset.master_data_release_id = :release_id
                    GROUP BY asset.asset_id, symbol.symbol, listing.venue_mic, listing.currency,
                             listing.timezone, calendar.calendar_key, member.role, member.ordinal
                    ORDER BY member.ordinal NULLS LAST, asset.asset_key
                    """
                    ),
                    {
                        "release_id": release["master_data_release_id"],
                        "universe_version_id": universe["universe_version_id"],
                    },
                )
                .mappings()
                .all()
            )
            return {
                "release_artifact_id": release["artifact_id"],
                "release_version_number": release["version_number"],
                "as_of_date": release["as_of_date"].isoformat(),
                "universe_key": universe["universe_key"],
                "items": [dict(row) for row in rows],
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

    def experiment_overview(self) -> dict[str, Any]:
        """Return published experiment cells and their latest auditable state."""
        with self._engine.connect() as connection:
            connection.execute(text("SET TRANSACTION READ ONLY"))
            suites = connection.execute(text("""
                SELECT suite.artifact_id, suite.suite_key, suite.version_number, suite.name,
                       suite.description, suite.specification_count
                FROM experiment.experiment_suite suite
                JOIN lineage.artifact artifact ON artifact.artifact_id = suite.artifact_id
                WHERE artifact.status = 'published'
                ORDER BY suite.version_number DESC, suite.suite_key
            """)).mappings().all()
            specifications = self._experiment_specifications(connection)
            result_ids = [row["interval_performance_result_id"] for row in specifications
                          if row["interval_performance_result_id"] is not None]
            core_by_result: dict[uuid.UUID, dict[str, float | None]] = {
                result_id: {} for result_id in result_ids
            }
            if result_ids:
                metrics = connection.execute(text("""
                    SELECT value.interval_performance_result_id, value.series_role,
                           definition.metric_key, value.metric_value
                    FROM experiment.performance_metric_value value
                    JOIN experiment.performance_metric_definition definition ON
                         definition.performance_metric_definition_id =
                         value.performance_metric_definition_id
                    WHERE value.interval_performance_result_id IN :result_ids
                      AND ((value.series_role IN ('strategy','benchmark') AND
                            definition.metric_key IN ('cagr','sharpe_ratio','maximum_drawdown'))
                           OR (value.series_role = 'relative' AND
                               definition.metric_key = 'annualized_relative_wealth_growth'))
                """).bindparams(bindparam("result_ids", expanding=True)),
                    {"result_ids": tuple(result_ids)}).mappings().all()
                for metric in metrics:
                    key = f"{metric['series_role']}.{metric['metric_key']}"
                    value = metric["metric_value"]
                    core_by_result[metric["interval_performance_result_id"]][key] = (
                        float(value) if value is not None else None
                    )
        items = []
        for source in specifications:
            row = dict(source)
            result_id = row.pop("interval_performance_result_id")
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
            row["core_metrics"] = core_by_result.get(result_id, {})
            items.append(row)
        return {"suites": [dict(row) for row in suites], "specifications": items}

    def experiment_result(self, artifact_id: uuid.UUID) -> dict[str, Any]:
        """Return one accepted result with metrics, run events, checks, and artifacts."""
        with self._engine.connect() as connection:
            connection.execute(text("SET TRANSACTION READ ONLY"))
            rows = self._experiment_specifications(connection, result_artifact_id=artifact_id)
            if not rows:
                raise LookupError(f"Published Experiment result not found: {artifact_id}")
            specification = dict(rows[0])
            interval_id = specification.pop("interval_performance_result_id")
            specification.pop("run_status")
            specification["status"] = "accepted"
            specification["core_metrics"] = {}
            interval = connection.execute(text("""
                SELECT result.artifact_id AS interval_result_artifact_id,
                       result.requested_start, result.requested_end, result.resolved_start,
                       result.resolved_end, result.normalization_nav_date,
                       result.observation_count, result.metric_value_count
                FROM experiment.interval_performance_result result
                WHERE result.interval_performance_result_id = :result
            """), {"result": interval_id}).mappings().one()
            metrics = connection.execute(text("""
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
            """), {"result": interval_id}).mappings().all()
            run_id = specification["run_attempt_id"]
            run = connection.execute(text("""
                SELECT status AS run_status, started_at, completed_at
                FROM ops.run_attempt WHERE run_attempt_id = :run
            """), {"run": run_id}).mappings().one()
            events = connection.execute(text("""
                SELECT sequence_number, event_type, severity, message, occurred_at
                FROM ops.run_event WHERE run_attempt_id = :run ORDER BY sequence_number
            """), {"run": run_id}).mappings().all()
            checks = connection.execute(text("""
                SELECT check_key, scope_key, status, severity, message
                FROM ops.quality_check_result WHERE run_attempt_id = :run
                ORDER BY check_key, scope_key
            """), {"run": run_id}).mappings().all()
            artifacts = connection.execute(text("""
                SELECT link.artifact_id, link.role, artifact.artifact_type,
                       artifact.artifact_key
                FROM ops.run_artifact link
                JOIN lineage.artifact artifact ON artifact.artifact_id = link.artifact_id
                WHERE link.run_attempt_id = :run
                ORDER BY link.role, artifact.artifact_type, artifact.artifact_key
            """), {"run": run_id}).mappings().all()
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
            cohorts = connection.execute(text("""
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
            """)).mappings().all()
            if not cohorts:
                return {
                    "cohorts": [], "active_cohort_artifact_id": None,
                    "selected_metric": metric_key, "ranking_direction": direction,
                    "candidate_count": 0, "ranked_count": 0, "entries": [],
                }
            active = next(
                (row for row in cohorts if row["artifact_id"] == cohort_artifact_id), None
            ) if cohort_artifact_id else cohorts[0]
            if active is None:
                raise LookupError(f"Published Comparison Cohort not found: {cohort_artifact_id}")
            rows = connection.execute(text("""
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
            """), {
                "cohort": active["artifact_id"], "series_role": series_role,
                "metric_scope": metric_scope, "definition_key": definition_key,
            }).mappings().all()
            interval_ids = tuple(row["interval_performance_result_id"] for row in rows)
            core_by_interval: dict[uuid.UUID, dict[str, float | None]] = {
                interval_id: {} for interval_id in interval_ids
            }
            if interval_ids:
                core_rows = connection.execute(text("""
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
                    {"result_ids": interval_ids}).mappings().all()
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
        entries.sort(key=lambda item: (
            item["rank"] is None, item["rank"] or 0, item["product_key"],
            str(item["result_artifact_id"]),
        ))
        return {
            "cohorts": [dict(row) for row in cohorts],
            "active_cohort_artifact_id": active["artifact_id"],
            "selected_metric": metric_key, "ranking_direction": direction,
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
            rows = connection.execute(text("""
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
                {"artifacts": ordered_ids}).mappings().all()
            by_id = {row["result_artifact_id"]: row for row in rows}
            if len(by_id) != len(ordered_ids):
                raise LookupError("Every Product Compare result must be published and accepted")
            rows = [by_id[item] for item in ordered_ids]
            interval_ids = tuple(row["interval_performance_result_id"] for row in rows)
            metric_rows = connection.execute(text("""
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
                {"results": interval_ids}).mappings().all()
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
            "result_artifact_id", "product_key", "model_specification_key",
            "strategy_template_key", "variant_key", "target_k", "frequency",
            "cost_bps_per_side", "template_key", "initialization_policy",
            "availability_status", "quality_status", "resolved_start", "resolved_end",
        )
        for row in rows:
            entries.append({
                **{field: row[field] for field in visible_fields},
                "metrics": metrics[row["interval_performance_result_id"]],
            })
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
            context = connection.execute(text("""
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
            """), {"result": result_artifact_id}).mappings().one_or_none()
            if context is None:
                raise LookupError(f"Published accepted result not found: {result_artifact_id}")
            dates = tuple(connection.execute(text("""
                SELECT decision_date FROM strategy.portfolio_decision
                WHERE portfolio_target_path_id = :path ORDER BY decision_date DESC
            """), {"path": context["portfolio_target_path_id"]}).scalars())
            if not dates:
                raise LookupError("Accepted result target path has no decisions")
            selected_date = decision_date or dates[0]
            if selected_date not in dates:
                raise LookupError(
                    f"Decision date is not present in the target path: {selected_date}"
                )
            positions = connection.execute(text("""
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
            """), {"path": context["portfolio_target_path_id"], "date": selected_date}
            ).mappings().all()
            component_rows = connection.execute(text("""
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
            """), {"dataset": context["model_dataset_id"], "date": selected_date}
            ).mappings().all()
        components_by_asset: dict[uuid.UUID, list[dict[str, Any]]] = {}
        for source in component_rows:
            row = dict(source)
            asset_id = row.pop("asset_id")
            signal_score = Decimal(row["signal_score"])
            transformed = signal_score if row["input_transform"] == "identity" else Decimal(
                1 if signal_score > 0 else -1 if signal_score < 0 else 0
            )
            weighted = transformed * Decimal(row["component_weight"])
            exact = (
                weighted * Decimal(row["dimension_weight"])
                if context["method_key"] == "weighted_mean"
                and row.pop("dimension_transform") == "identity" else None
            )
            row["signal_score"] = float(signal_score)
            row["dimension_weight"] = float(row["dimension_weight"])
            row["component_weight"] = float(row["component_weight"])
            row["transformed_signal_score"] = float(transformed)
            row["weighted_component_input"] = float(weighted)
            row["overall_contribution"] = float(exact) if exact is not None else None
            components_by_asset.setdefault(asset_id, []).append(row)
        visible_context = {key: context[key] for key in (
            "result_artifact_id", "target_path_artifact_id", "model_dataset_artifact_id",
            "model_specification_artifact_id", "universe_artifact_id",
            "data_bundle_artifact_id", "eligibility_artifact_id",
        )}
        first = positions[0]
        return {
            **visible_context, "model_method_key": context["method_key"],
            "available_dates": list(dates), "selected_date": selected_date,
            "target_k": first["target_k"],
            "actual_holding_count": first["actual_holding_count"],
            "reserve_target_weight": float(first["reserve_target_weight"]),
            "positions": [{
                **{key: (float(row[key]) if key in {"model_score", "model_rank", "target_weight"}
                         else row[key]) for key in (
                    "asset_key", "symbol", "selected", "model_score", "model_rank",
                    "trend_state", "target_weight", "decision_reason")},
                "components": components_by_asset.get(row["asset_id"], []),
            } for row in positions],
        }

    @staticmethod
    def _experiment_specifications(
        connection: Any, *, result_artifact_id: uuid.UUID | None = None
    ) -> list[Any]:
        result_filter = (
            " AND publication.artifact_id = :result_artifact" if result_artifact_id else ""
        )
        statement = text("""
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
        """ + result_filter + " ORDER BY suite.version_number DESC, cell.ordinal")
        parameters = {"result_artifact": result_artifact_id} if result_artifact_id else {}
        return list(connection.execute(statement, parameters).mappings().all())

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
