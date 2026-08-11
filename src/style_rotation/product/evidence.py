from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import bindparam, text
from sqlalchemy.engine import Connection


@dataclass(frozen=True, slots=True)
class ExactQualificationEvidence:
    """Immutable Result dependencies pinned by one Product Qualification.

    The dependency edges on a published Qualification Artifact are the authority.
    The relational UUID arrays are duplicated query indexes and are therefore only
    checked for exact agreement with those dependencies.
    """

    qualification_bundle_id: uuid.UUID
    qualification_artifact_id: uuid.UUID
    source_suite_id: uuid.UUID
    source_suite_artifact_id: uuid.UUID
    compiled_strategy_version_id: uuid.UUID
    compiled_model_instance_id: uuid.UUID
    portfolio_result_artifact_ids: frozenset[uuid.UUID]
    portfolio_cell_artifact_ids: frozenset[uuid.UUID]
    predictive_result_artifact_ids: frozenset[uuid.UUID]
    predictive_cell_artifact_ids: frozenset[uuid.UUID]
    complete: bool
    reasons: tuple[str, ...]

    @property
    def result_artifact_ids(self) -> frozenset[uuid.UUID]:
        return self.portfolio_result_artifact_ids | self.predictive_result_artifact_ids

    @property
    def cell_artifact_ids(self) -> frozenset[uuid.UUID]:
        return self.portfolio_cell_artifact_ids | self.predictive_cell_artifact_ids


def load_product_qualification_evidence(
    connection: Connection,
) -> tuple[ExactQualificationEvidence, ...]:
    """Load and validate the exact seven-Result evidence graph for every Product.

    Any mismatch is returned as ``complete=False``.  Retention callers must fail
    closed for the entire source Suite rather than trying to infer replacement
    evidence from mutable relational queries.
    """

    qualifications = tuple(
        connection.execute(
            text(
                """
                SELECT DISTINCT qualification.qualification_bundle_id,
                       qualification.artifact_id AS qualification_artifact_id,
                       qualification.source_suite_artifact_id,
                       qualification.compiled_strategy_version_id,
                       qualification.portfolio_cell_count,
                       qualification.result_artifact_ids,
                       qualification.cell_artifact_ids,
                       suite.research_suite_id,
                       strategy.compiled_model_instance_id
                FROM product.product_version product_version
                JOIN experiment.qualification_bundle qualification
                  ON qualification.qualification_bundle_id =
                     product_version.qualification_bundle_id
                JOIN experiment.research_suite suite
                  ON suite.artifact_id = qualification.source_suite_artifact_id
                JOIN strategy.compiled_strategy_version strategy
                  ON strategy.compiled_strategy_version_id =
                     qualification.compiled_strategy_version_id
                ORDER BY qualification.qualification_bundle_id
                """
            )
        ).mappings()
    )
    result: list[ExactQualificationEvidence] = []
    for qualification in qualifications:
        dependency_rows = tuple(
            connection.execute(
                text(
                    """
                    SELECT dependency.role, dependency.depends_on_artifact_id,
                           artifact.status
                    FROM lineage.artifact_dependency dependency
                    JOIN lineage.artifact artifact
                      ON artifact.artifact_id = dependency.depends_on_artifact_id
                    WHERE dependency.artifact_id = :qualification_artifact_id
                      AND dependency.role IN (
                          'source_suite',
                          'qualification_result',
                          'qualification_predictive_result'
                      )
                    ORDER BY dependency.role, dependency.ordinal NULLS LAST,
                             dependency.depends_on_artifact_id
                    """
                ),
                {
                    "qualification_artifact_id": qualification[
                        "qualification_artifact_id"
                    ]
                },
            ).mappings()
        )
        by_role: dict[str, list[uuid.UUID]] = {}
        dependency_statuses: dict[uuid.UUID, str] = {}
        for dependency in dependency_rows:
            artifact_id = dependency["depends_on_artifact_id"]
            by_role.setdefault(str(dependency["role"]), []).append(artifact_id)
            dependency_statuses[artifact_id] = str(dependency["status"])

        portfolio_result_ids = frozenset(by_role.get("qualification_result", ()))
        predictive_result_ids = frozenset(
            by_role.get("qualification_predictive_result", ())
        )
        all_result_ids = portfolio_result_ids | predictive_result_ids
        result_rows = (
            tuple(
                connection.execute(
                    text(
                        """
                        SELECT result.artifact_id AS result_artifact_id,
                               result.result_type, result.cell_artifact_id,
                               portfolio.research_suite_id AS portfolio_suite_id,
                               portfolio.compiled_strategy_version_id,
                               predictive.research_suite_id AS predictive_suite_id,
                               predictive.compiled_model_instance_id
                        FROM experiment.cell_result result
                        LEFT JOIN experiment.portfolio_cell_specification portfolio
                          ON portfolio.artifact_id = result.cell_artifact_id
                        LEFT JOIN experiment.predictive_cell_specification predictive
                          ON predictive.artifact_id = result.cell_artifact_id
                        WHERE result.artifact_id IN :result_ids
                        ORDER BY result.artifact_id
                        """
                    ).bindparams(bindparam("result_ids", expanding=True)),
                    {"result_ids": tuple(all_result_ids)},
                ).mappings()
            )
            if all_result_ids
            else ()
        )
        rows_by_result = {row["result_artifact_id"]: row for row in result_rows}
        portfolio_cell_ids = frozenset(
            row["cell_artifact_id"]
            for result_id in portfolio_result_ids
            if (row := rows_by_result.get(result_id)) is not None
        )
        predictive_cell_ids = frozenset(
            row["cell_artifact_id"]
            for result_id in predictive_result_ids
            if (row := rows_by_result.get(result_id)) is not None
        )

        reasons: list[str] = []
        source_dependencies = by_role.get("source_suite", ())
        if source_dependencies != [qualification["source_suite_artifact_id"]]:
            reasons.append("source_suite_dependency_mismatch")
        if int(qualification["portfolio_cell_count"]) != 6:
            reasons.append("portfolio_cell_count_not_six")
        if len(by_role.get("qualification_result", ())) != 6:
            reasons.append("qualification_result_dependency_count_not_six")
        if len(portfolio_result_ids) != 6:
            reasons.append("qualification_result_dependency_duplicate")
        if len(by_role.get("qualification_predictive_result", ())) != 1:
            reasons.append("qualification_predictive_dependency_count_not_one")
        if len(predictive_result_ids) != 1:
            reasons.append("qualification_predictive_dependency_duplicate")
        if len(result_rows) != 7:
            reasons.append("qualification_result_row_count_not_seven")
        if any(dependency_statuses.get(value) != "published" for value in all_result_ids):
            reasons.append("qualification_result_dependency_not_published")

        suite_id = qualification["research_suite_id"]
        strategy_id = qualification["compiled_strategy_version_id"]
        model_id = qualification["compiled_model_instance_id"]
        for result_id in portfolio_result_ids:
            row = rows_by_result.get(result_id)
            if (
                row is None
                or row["result_type"] != "portfolio"
                or row["portfolio_suite_id"] != suite_id
                or row["compiled_strategy_version_id"] != strategy_id
                or row["predictive_suite_id"] is not None
            ):
                reasons.append("qualification_portfolio_result_contract_mismatch")
                break
        for result_id in predictive_result_ids:
            row = rows_by_result.get(result_id)
            if (
                row is None
                or row["result_type"] != "predictive"
                or row["predictive_suite_id"] != suite_id
                or row["compiled_model_instance_id"] != model_id
                or row["portfolio_suite_id"] is not None
            ):
                reasons.append("qualification_predictive_result_contract_mismatch")
                break

        frozen_result_ids = tuple(qualification["result_artifact_ids"] or ())
        frozen_cell_ids = tuple(qualification["cell_artifact_ids"] or ())
        if len(frozen_result_ids) != 6 or set(frozen_result_ids) != portfolio_result_ids:
            reasons.append("qualification_result_array_mismatch")
        if len(frozen_cell_ids) != 6 or set(frozen_cell_ids) != portfolio_cell_ids:
            reasons.append("qualification_cell_array_mismatch")

        result.append(
            ExactQualificationEvidence(
                qualification_bundle_id=qualification["qualification_bundle_id"],
                qualification_artifact_id=qualification["qualification_artifact_id"],
                source_suite_id=suite_id,
                source_suite_artifact_id=qualification["source_suite_artifact_id"],
                compiled_strategy_version_id=strategy_id,
                compiled_model_instance_id=model_id,
                portfolio_result_artifact_ids=portfolio_result_ids,
                portfolio_cell_artifact_ids=portfolio_cell_ids,
                predictive_result_artifact_ids=predictive_result_ids,
                predictive_cell_artifact_ids=predictive_cell_ids,
                complete=not reasons,
                reasons=tuple(dict.fromkeys(reasons)),
            )
        )
    return tuple(result)
