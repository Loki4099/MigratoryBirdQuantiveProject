from __future__ import annotations

import uuid
from dataclasses import dataclass
from decimal import Decimal, localcontext
from functools import partial
from typing import Any, cast

from sqlalchemy import Engine, text
from sqlalchemy.engine import Connection, RowMapping

from style_rotation.core.canonical import sha256_hexdigest
from style_rotation.lineage.service import ArtifactService, DependencyInput


@dataclass(frozen=True, slots=True)
class RankingMember:
    result_evidence_snapshot_id: uuid.UUID
    result_evidence_artifact_id: uuid.UUID
    result_artifact_id: uuid.UUID
    configuration_snapshot_id: uuid.UUID
    cagr: Decimal
    benchmark_cagr: Decimal
    cagr_spread: Decimal
    sharpe_ratio: Decimal
    maximum_drawdown: Decimal
    member_fingerprint: str


@dataclass(frozen=True, slots=True)
class RankingCohortPublication:
    ranking_cohort_release_id: uuid.UUID
    artifact_id: uuid.UUID
    evaluation_cohort_version_id: uuid.UUID
    frequency: str
    version_number: int
    member_count: int
    release_fingerprint: str
    reused: bool


class RankingCohortService:
    """Freeze all accepted Results from one exact Evaluation Cohort into one release."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def publish_for_suite(
        self, research_suite_id: uuid.UUID, *, released_by: str
    ) -> RankingCohortPublication:
        with self._engine.connect() as connection:
            identity = connection.execute(
                text(
                    "SELECT cohort.evaluation_cohort_version_id,"
                    "batch_round.research_round_id "
                    "FROM experiment.v022_research_suite_evaluation_cohort_binding cohort "
                    "JOIN experiment.v022_suite_launch_batch_child child "
                    "ON child.research_suite_id=cohort.research_suite_id "
                    "JOIN experiment.v022_suite_launch_batch_round batch_round "
                    "ON batch_round.suite_launch_batch_id=child.suite_launch_batch_id "
                    "JOIN workspace.v022_research_round research_round "
                    "ON research_round.research_round_id=batch_round.research_round_id "
                    "AND research_round.status='active' "
                    "WHERE cohort.research_suite_id=:suite"
                ),
                {"suite": research_suite_id},
            ).mappings().one_or_none()
        if identity is None:
            raise ValueError(
                "Ranking requires an exact active Research Round and Evaluation Cohort"
            )
        return self.publish(
            cast(uuid.UUID, identity["evaluation_cohort_version_id"]),
            research_round_id=cast(uuid.UUID, identity["research_round_id"]),
            released_by=released_by,
        )

    def publish(
        self,
        evaluation_cohort_version_id: uuid.UUID,
        *,
        research_round_id: uuid.UUID,
        released_by: str,
    ) -> RankingCohortPublication:
        if not released_by.strip():
            raise ValueError("Ranking Cohort publisher is required")
        with self._engine.connect() as connection:
            cohort = self._cohort(connection, evaluation_cohort_version_id)
            members = self._members(
                connection, evaluation_cohort_version_id, research_round_id
            )
            if not members:
                raise ValueError("Ranking Cohort requires at least one accepted Result Evidence")
            semantic = {
                "contract_version": "v0.22.0",
                "evaluation_cohort_version_id": evaluation_cohort_version_id,
                "evaluation_cohort_fingerprint": cohort["cohort_fingerprint"],
                "research_round_id": research_round_id,
                "frequency": cohort["frequency"],
                "members": [item.member_fingerprint for item in members],
            }
            fingerprint = sha256_hexdigest(semantic)
            existing = self._existing_by_fingerprint(connection, fingerprint)
            if existing is not None:
                return existing
            version_number = int(
                connection.scalar(
                    text(
                        "SELECT COALESCE(max(version_number),0)+1 FROM "
                        "experiment.v022_ranking_cohort_release "
                        "WHERE evaluation_cohort_version_id=:cohort"
                    ),
                    {"cohort": evaluation_cohort_version_id},
                )
            )

        release_id = uuid.uuid5(
            uuid.NAMESPACE_URL, f"bird:v0.22:ranking-cohort:{fingerprint}"
        )
        dependencies = (
            DependencyInput(cohort["artifact_id"], "evaluation_cohort", 0),
            *(
                DependencyInput(item.result_evidence_artifact_id, "result_evidence", ordinal + 1)
                for ordinal, item in enumerate(members)
            ),
        )
        publication = ArtifactService(self._engine).publish(
            artifact_type="v022_ranking_cohort_release",
            artifact_key=f"v022_ranking_cohort__{cohort['cohort_key']}",
            version_number=version_number,
            semantic_payload=semantic,
            content_payload={
                **semantic,
                "metrics": [self._member_document(item) for item in members],
            },
            dependencies=dependencies,
            reason="publish immutable v0.22 Ranking Cohort Release",
            draft_writer=partial(
                self._write,
                release_id=release_id,
                cohort=cohort,
                version_number=version_number,
                members=members,
                research_round_id=research_round_id,
                release_fingerprint=fingerprint,
                released_by=released_by,
            ),
        )
        if publication.reused:
            with self._engine.connect() as connection:
                frozen = self._existing_by_fingerprint(connection, fingerprint)
            if frozen is None:
                raise ValueError("Reused Ranking Cohort Artifact has no release projection")
            return frozen
        return RankingCohortPublication(
            release_id,
            publication.artifact_id,
            evaluation_cohort_version_id,
            cast(str, cohort["frequency"]),
            version_number,
            len(members),
            fingerprint,
            False,
        )

    @staticmethod
    def _cohort(connection: Connection, cohort_id: uuid.UUID) -> RowMapping:
        row = (
            connection.execute(
                text(
                    """
                    SELECT cohort.*,artifact.status,
                           runtime_contract.evaluation_cohort_runtime_contract_id,
                           runtime_contract.runtime_fingerprint,
                           runtime_contract.ranking_eligibility AS gate_ranking_eligibility,
                           runtime_artifact.status AS runtime_contract_status
                      FROM experiment.v022_evaluation_cohort_version cohort
                      JOIN lineage.artifact artifact ON artifact.artifact_id=cohort.artifact_id
                      LEFT JOIN experiment.v022_evaluation_cohort_runtime_contract
                        runtime_contract ON runtime_contract.evaluation_cohort_version_id=
                          cohort.evaluation_cohort_version_id
                      LEFT JOIN lineage.artifact runtime_artifact
                        ON runtime_artifact.artifact_id=runtime_contract.artifact_id
                     WHERE cohort.evaluation_cohort_version_id=:cohort
                    """
                ),
                {"cohort": cohort_id},
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise LookupError(f"Evaluation Cohort not found: {cohort_id}")
        if not (
            row["status"] == "published"
            and row["research_tier"] == "rankable_research"
            and row["runtime_contract_status"] == "published"
            and row["gate_ranking_eligibility"] == "rankable_research"
            and row["evaluation_cohort_runtime_contract_id"] is not None
        ):
            raise ValueError(
                "Ranking requires a published M106 Cohort runtime contract with a rankable Gate"
            )
        return row

    @classmethod
    def _members(
        cls,
        connection: Connection,
        cohort_id: uuid.UUID,
        research_round_id: uuid.UUID,
    ) -> tuple[RankingMember, ...]:
        rows = connection.execute(
            text(
                """
                SELECT evidence.result_evidence_snapshot_id,
                       evidence.artifact_id AS evidence_artifact_id,
                       evidence.result_artifact_id,evidence.configuration_snapshot_id,
                       evidence.quality_document
                  FROM experiment.v022_result_evidence_snapshot evidence
                  JOIN lineage.artifact artifact ON artifact.artifact_id=evidence.artifact_id
                 WHERE evidence.evaluation_cohort_version_id=:cohort
                   AND artifact.status='published'
                   AND evidence.quality_document->>'state'='passed'
                   AND evidence.quality_document->>'outcome'='accepted'
                   AND EXISTS (
                     SELECT 1
                       FROM experiment.v022_portfolio_cell_runtime_result result
                       JOIN workspace.v022_graph_work_consumer consumer
                         ON consumer.graph_work_item_id=result.graph_work_item_id
                       JOIN experiment.v022_research_suite_graph_run_binding suite_run
                         ON suite_run.graph_run_id=consumer.graph_run_id
                       JOIN experiment.v022_suite_launch_batch_child launch_child
                         ON launch_child.research_suite_id=suite_run.research_suite_id
                       JOIN experiment.v022_suite_launch_batch_round batch_round
                         ON batch_round.suite_launch_batch_id=
                            launch_child.suite_launch_batch_id
                      WHERE result.artifact_id=evidence.result_artifact_id
                        AND batch_round.research_round_id=:round
                   )
                 ORDER BY evidence.result_evidence_snapshot_id
                """
            ),
            {"cohort": cohort_id, "round": research_round_id},
        ).mappings()
        return tuple(cls._member(row) for row in rows)

    @classmethod
    def _member(cls, row: RowMapping) -> RankingMember:
        quality = cast(dict[str, Any], row["quality_document"])
        metrics = cast(dict[str, list[dict[str, Any]]], quality["metric_document"])
        absolute = cls._metric_map(metrics["absolute_metrics"])
        relative = cls._metric_map(metrics["relative_metrics"])
        cagr = cls._required_metric(absolute, "cagr")
        spread = cls._required_metric(relative, "cagr_spread")
        with localcontext() as context:
            context.prec = 60
            benchmark_cagr = cagr - spread
        member_semantic = {
            "result_evidence_snapshot_id": row["result_evidence_snapshot_id"],
            "result_artifact_id": row["result_artifact_id"],
            "configuration_snapshot_id": row["configuration_snapshot_id"],
            "cagr": cagr,
            "benchmark_cagr": benchmark_cagr,
            "cagr_spread": spread,
            "sharpe_ratio": cls._required_metric(absolute, "sharpe_ratio"),
            "maximum_drawdown": cls._required_metric(absolute, "maximum_drawdown"),
        }
        return RankingMember(
            row["result_evidence_snapshot_id"],
            row["evidence_artifact_id"],
            row["result_artifact_id"],
            row["configuration_snapshot_id"],
            cagr,
            benchmark_cagr,
            spread,
            cast(Decimal, member_semantic["sharpe_ratio"]),
            cast(Decimal, member_semantic["maximum_drawdown"]),
            sha256_hexdigest(member_semantic),
        )

    @staticmethod
    def _metric_map(rows: list[dict[str, Any]]) -> dict[str, Decimal | None]:
        return {
            cast(str, item["metric_key"]): (
                Decimal(cast(str, item["value"])) if item.get("value") is not None else None
            )
            for item in rows
        }

    @staticmethod
    def _required_metric(values: dict[str, Decimal | None], key: str) -> Decimal:
        value = values.get(key)
        if value is None or not value.is_finite():
            raise ValueError(f"Rankable Result Evidence is missing metric: {key}")
        return value

    @staticmethod
    def _member_document(item: RankingMember) -> dict[str, Any]:
        return {
            "result_evidence_snapshot_id": item.result_evidence_snapshot_id,
            "result_artifact_id": item.result_artifact_id,
            "configuration_snapshot_id": item.configuration_snapshot_id,
            "cagr": item.cagr,
            "benchmark_cagr": item.benchmark_cagr,
            "cagr_spread": item.cagr_spread,
            "sharpe_ratio": item.sharpe_ratio,
            "maximum_drawdown": item.maximum_drawdown,
            "member_fingerprint": item.member_fingerprint,
        }

    @staticmethod
    def _existing_by_fingerprint(
        connection: Connection, fingerprint: str
    ) -> RankingCohortPublication | None:
        row = (
            connection.execute(
                text(
                    """
                    SELECT release.*,artifact.status
                      FROM experiment.v022_ranking_cohort_release release
                      JOIN lineage.artifact artifact ON artifact.artifact_id=release.artifact_id
                     WHERE release.release_fingerprint=:fingerprint
                    """
                ),
                {"fingerprint": fingerprint},
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            return None
        if row["status"] != "published":
            raise ValueError("Ranking Cohort Release Artifact is not published")
        return RankingCohortPublication(
            row["ranking_cohort_release_id"],
            row["artifact_id"],
            row["evaluation_cohort_version_id"],
            row["frequency"],
            row["version_number"],
            row["member_count"],
            row["release_fingerprint"],
            True,
        )

    @staticmethod
    def _write(
        connection: Connection,
        artifact_id: uuid.UUID,
        *,
        release_id: uuid.UUID,
        cohort: RowMapping,
        version_number: int,
        members: tuple[RankingMember, ...],
        research_round_id: uuid.UUID,
        release_fingerprint: str,
        released_by: str,
    ) -> None:
        connection.execute(
            text(
                """
                INSERT INTO experiment.v022_ranking_cohort_release (
                  ranking_cohort_release_id,artifact_id,evaluation_cohort_version_id,
                  evaluation_cohort_artifact_id,evaluation_cohort_fingerprint,
                  cohort_key,frequency,version_number,member_count,release_fingerprint,
                  released_by
                ) VALUES (:id,:artifact,:cohort,:cohort_artifact,:cohort_fingerprint,
                          :cohort_key,:frequency,:version,:count,:fingerprint,:released_by)
                """
            ),
            {
                "id": release_id,
                "artifact": artifact_id,
                "cohort": cohort["evaluation_cohort_version_id"],
                "cohort_artifact": cohort["artifact_id"],
                "cohort_fingerprint": cohort["cohort_fingerprint"],
                "cohort_key": cohort["cohort_key"],
                "frequency": cohort["frequency"],
                "version": version_number,
                "count": len(members),
                "fingerprint": release_fingerprint,
                "released_by": released_by,
            },
        )
        connection.execute(
            text(
                "INSERT INTO experiment.v022_ranking_cohort_release_round ("
                "ranking_cohort_release_id,research_round_id) VALUES (:release,:round)"
            ),
            {"release": release_id, "round": research_round_id},
        )
        for ordinal, item in enumerate(members):
            connection.execute(
                text(
                    """
                    INSERT INTO experiment.v022_ranking_cohort_member (
                      ranking_cohort_release_id,ordinal,result_evidence_snapshot_id,
                      result_evidence_artifact_id,result_artifact_id,
                      configuration_snapshot_id,cagr,benchmark_cagr,cagr_spread,
                      sharpe_ratio,maximum_drawdown,member_fingerprint
                    ) VALUES (:release,:ordinal,:evidence,:evidence_artifact,:result,
                              :configuration,:cagr,:benchmark_cagr,:spread,:sharpe,
                              :drawdown,:fingerprint)
                    """
                ),
                {
                    "release": release_id,
                    "ordinal": ordinal,
                    "evidence": item.result_evidence_snapshot_id,
                    "evidence_artifact": item.result_evidence_artifact_id,
                    "result": item.result_artifact_id,
                    "configuration": item.configuration_snapshot_id,
                    "cagr": item.cagr,
                    "benchmark_cagr": item.benchmark_cagr,
                    "spread": item.cagr_spread,
                    "sharpe": item.sharpe_ratio,
                    "drawdown": item.maximum_drawdown,
                    "fingerprint": item.member_fingerprint,
                },
            )
