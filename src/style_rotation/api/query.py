from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import Engine, bindparam, text
from sqlalchemy.engine import RowMapping


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
