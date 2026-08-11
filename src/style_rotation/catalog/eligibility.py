from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass
from datetime import date
from typing import Any

from sqlalchemy import Engine, text
from sqlalchemy.engine import Connection, RowMapping

from style_rotation.lineage.service import ArtifactService, DependencyInput, PublicationResult


@dataclass(frozen=True, slots=True)
class EligibilityProblem:
    severity: str
    issue_code: str
    message: str
    details: dict[str, object]


@dataclass(frozen=True, slots=True)
class AssetEligibility:
    asset_id: uuid.UUID
    asset_key: str
    role: str
    is_eligible: bool
    available_start: date | None
    available_end: date | None
    data_ready_date: date | None
    observation_count: int
    issues: tuple[EligibilityProblem, ...]


class EligibilityPublicationService:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._artifacts = ArtifactService(engine)

    def publish(
        self,
        universe_artifact_id: uuid.UUID,
        requirement_artifact_id: uuid.UUID,
        bundle_artifact_id: uuid.UUID,
        *,
        requested_start: date,
        requested_end: date,
        warmup_observations: int = 253,
        version_number: int,
    ) -> PublicationResult:
        if requested_start > requested_end:
            raise ValueError("Eligibility start must not be after end")
        if warmup_observations < 1:
            raise ValueError("Eligibility warmup observations must be positive")
        context = self._context(universe_artifact_id, requirement_artifact_id, bundle_artifact_id)
        items = self._evaluate(
            context,
            requested_start=requested_start,
            requested_end=requested_end,
            warmup_observations=warmup_observations,
        )
        snapshot_key = (
            f"{context['universe_key']}:{requested_start}:{requested_end}:"
            f"w{warmup_observations}:v{version_number}"
        )
        semantic = {
            "snapshot_key": snapshot_key,
            "version_number": version_number,
            "universe_artifact_id": str(universe_artifact_id),
            "requirement_artifact_id": str(requirement_artifact_id),
            "bundle_artifact_id": str(bundle_artifact_id),
            "requested_start": requested_start,
            "requested_end": requested_end,
            "warmup_observations": warmup_observations,
        }
        return self._artifacts.publish(
            artifact_type="eligibility_snapshot",
            artifact_key=f"{context['universe_key']}_eligibility",
            version_number=version_number,
            semantic_payload=semantic,
            content_payload={**semantic, "items": [asdict(item) for item in items]},
            dependencies=(
                DependencyInput(universe_artifact_id, "universe_version", 0),
                DependencyInput(requirement_artifact_id, "data_requirements", 1),
                DependencyInput(bundle_artifact_id, "data_bundle", 2),
            ),
            reason=f"publish universe eligibility snapshot v{version_number}",
            draft_writer=lambda connection, artifact_id: _write_eligibility(
                connection,
                artifact_id,
                snapshot_key,
                context,
                requested_start,
                requested_end,
                warmup_observations,
                items,
            ),
        )

    def _context(
        self,
        universe_artifact_id: uuid.UUID,
        requirement_artifact_id: uuid.UUID,
        bundle_artifact_id: uuid.UUID,
    ) -> dict[str, Any]:
        with self._engine.connect() as connection:
            universe = _published_row(
                connection,
                "catalog.universe_version",
                "universe_version_id",
                universe_artifact_id,
            )
            requirement = _published_row(
                connection,
                "catalog.data_requirement_version",
                "data_requirement_version_id",
                requirement_artifact_id,
            )
            bundle = _published_row(
                connection,
                "data.data_bundle_version",
                "data_bundle_version_id",
                bundle_artifact_id,
            )
            universe_key = connection.execute(
                text(
                    "SELECT definition.universe_key FROM catalog.universe_definition definition "
                    "JOIN catalog.universe_version version ON version.universe_definition_id = "
                    "definition.universe_definition_id WHERE version.universe_version_id = :id"
                ),
                {"id": universe["universe_version_id"]},
            ).scalar_one()
            members = connection.execute(
                text(
                    "SELECT member.asset_id, asset.asset_key, member.role, member.ordinal "
                    "FROM catalog.universe_member member JOIN catalog.asset asset "
                    "ON asset.asset_id = member.asset_id WHERE member.universe_version_id = :id "
                    "ORDER BY member.ordinal"
                ),
                {"id": universe["universe_version_id"]},
            ).mappings()
            bundle_members = connection.execute(
                text(
                    "SELECT member.role, member.dataset_publication_id, member.calendar_version_id "
                    "FROM data.data_bundle_member member WHERE member.data_bundle_version_id = :id"
                ),
                {"id": bundle["data_bundle_version_id"]},
            ).mappings()
            by_role = {row["role"]: dict(row) for row in bundle_members}
            expected_roles = {
                "canonical_market",
                "canonical_rate",
                "reserve_return",
                "trading_calendar",
            }
            if set(by_role) != expected_roles:
                raise ValueError("Data bundle does not contain the required v0.2 member roles")
            calendar_sessions = connection.execute(
                text(
                    "SELECT session_date FROM catalog.calendar_session "
                    "WHERE calendar_version_id = :id ORDER BY session_date"
                ),
                {"id": by_role["trading_calendar"]["calendar_version_id"]},
            ).scalars()
            bars = connection.execute(
                text(
                    "SELECT asset_id, session_date FROM data.daily_bar "
                    "WHERE dataset_publication_id = :id ORDER BY asset_id, session_date"
                ),
                {"id": by_role["canonical_market"]["dataset_publication_id"]},
            ).mappings()
            reserve_coverage = (
                connection.execute(
                    text(
                        "SELECT coverage_start, coverage_end FROM data.dataset_publication "
                        "WHERE dataset_publication_id = :id"
                    ),
                    {"id": by_role["reserve_return"]["dataset_publication_id"]},
                )
                .mappings()
                .one()
            )
        dates_by_asset: dict[uuid.UUID, list[date]] = {}
        for row in bars:
            dates_by_asset.setdefault(row["asset_id"], []).append(row["session_date"])
        return {
            "universe_version_id": universe["universe_version_id"],
            "requirement_version_id": requirement["data_requirement_version_id"],
            "bundle_version_id": bundle["data_bundle_version_id"],
            "bundle_coverage_start": bundle["coverage_start"],
            "bundle_coverage_end": bundle["coverage_end"],
            "universe_key": str(universe_key),
            "members": tuple(members),
            "calendar_sessions": tuple(calendar_sessions),
            "dates_by_asset": dates_by_asset,
            "reserve_coverage_start": reserve_coverage["coverage_start"],
            "reserve_coverage_end": reserve_coverage["coverage_end"],
        }

    @staticmethod
    def _evaluate(
        context: dict[str, Any],
        *,
        requested_start: date,
        requested_end: date,
        warmup_observations: int,
    ) -> tuple[AssetEligibility, ...]:
        requested_sessions = {
            item
            for item in context["calendar_sessions"]
            if requested_start <= item <= requested_end
        }
        global_problems: list[EligibilityProblem] = []
        if not requested_sessions:
            global_problems.append(
                EligibilityProblem(
                    "error", "empty_requested_calendar", "No XNYS sessions in range", {}
                )
            )
        if (
            context["bundle_coverage_start"] > requested_start
            or context["bundle_coverage_end"] < requested_end
        ):
            global_problems.append(
                EligibilityProblem(
                    "error",
                    "bundle_coverage_gap",
                    "Data bundle does not cover the requested range",
                    {
                        "coverage_start": str(context["bundle_coverage_start"]),
                        "coverage_end": str(context["bundle_coverage_end"]),
                    },
                )
            )
        if (
            context["reserve_coverage_start"] > requested_start
            or context["reserve_coverage_end"] < requested_end
        ):
            global_problems.append(
                EligibilityProblem(
                    "error",
                    "reserve_coverage_gap",
                    "Reserve return dataset does not cover the requested range",
                    {},
                )
            )
        results: list[AssetEligibility] = []
        for member in context["members"]:
            dates = context["dates_by_asset"].get(member["asset_id"], [])
            issues = list(global_problems)
            data_ready = (
                dates[warmup_observations - 1] if len(dates) >= warmup_observations else None
            )
            if data_ready is None or data_ready > requested_start:
                issues.append(
                    EligibilityProblem(
                        "error",
                        "insufficient_warmup",
                        "Asset lacks the required observations by requested start",
                        {
                            "required": warmup_observations,
                            "available_by_start": sum(item <= requested_start for item in dates),
                        },
                    )
                )
            missing = sorted(requested_sessions.difference(dates))
            if missing:
                issues.append(
                    EligibilityProblem(
                        "error",
                        "missing_requested_sessions",
                        "Asset is missing one or more requested XNYS sessions",
                        {"count": len(missing), "first": str(missing[0])},
                    )
                )
            results.append(
                AssetEligibility(
                    member["asset_id"],
                    str(member["asset_key"]),
                    str(member["role"]),
                    not any(item.severity == "error" for item in issues),
                    dates[0] if dates else None,
                    dates[-1] if dates else None,
                    data_ready,
                    len(dates),
                    tuple(issues),
                )
            )
        return tuple(results)


def _published_row(
    connection: Connection,
    table: str,
    id_column: str,
    artifact_id: uuid.UUID,
) -> RowMapping:
    row = (
        connection.execute(
            text(
                f"SELECT business.* FROM {table} business JOIN lineage.artifact artifact "
                f"ON artifact.artifact_id = business.artifact_id WHERE business.artifact_id = :id "
                "AND artifact.status = 'published'"
            ),
            {"id": artifact_id},
        )
        .mappings()
        .one_or_none()
    )
    if row is None or row[id_column] is None:
        raise ValueError(f"Published dependency not found: {table}")
    return row


def _write_eligibility(
    connection: Connection,
    artifact_id: uuid.UUID,
    snapshot_key: str,
    context: dict[str, Any],
    requested_start: date,
    requested_end: date,
    warmup_observations: int,
    items: tuple[AssetEligibility, ...],
) -> None:
    snapshot_id = uuid.uuid4()
    connection.execute(
        text(
            "INSERT INTO catalog.eligibility_snapshot (eligibility_snapshot_id, artifact_id, "
            "universe_version_id, data_requirement_version_id, data_bundle_version_id, "
            "snapshot_key, requested_start, requested_end, warmup_observations, member_count, "
            "eligible_count) VALUES (:id, :artifact_id, :universe_id, :requirement_id, "
            ":bundle_id, :key, :start, :end, :warmup, :count, :eligible)"
        ),
        {
            "id": snapshot_id,
            "artifact_id": artifact_id,
            "universe_id": context["universe_version_id"],
            "requirement_id": context["requirement_version_id"],
            "bundle_id": context["bundle_version_id"],
            "key": snapshot_key,
            "start": requested_start,
            "end": requested_end,
            "warmup": warmup_observations,
            "count": len(items),
            "eligible": sum(item.is_eligible for item in items),
        },
    )
    for item in items:
        item_id = uuid.uuid4()
        connection.execute(
            text(
                "INSERT INTO catalog.eligibility_item (eligibility_item_id, "
                "eligibility_snapshot_id, asset_id, role, is_eligible, available_start, "
                "available_end, data_ready_date, observation_count) VALUES (:id, :snapshot_id, "
                ":asset_id, :role, :eligible, :start, :end, :ready, :count)"
            ),
            {
                "id": item_id,
                "snapshot_id": snapshot_id,
                "asset_id": item.asset_id,
                "role": item.role,
                "eligible": item.is_eligible,
                "start": item.available_start,
                "end": item.available_end,
                "ready": item.data_ready_date,
                "count": item.observation_count,
            },
        )
        if item.issues:
            connection.execute(
                text(
                    "INSERT INTO catalog.eligibility_issue (eligibility_issue_id, "
                    "eligibility_item_id, severity, issue_code, message, details) VALUES "
                    "(:id, :item_id, :severity, :code, :message, CAST(:details AS jsonb))"
                ),
                [
                    {
                        "id": uuid.uuid4(),
                        "item_id": item_id,
                        "severity": issue.severity,
                        "code": issue.issue_code,
                        "message": issue.message,
                        "details": json.dumps(issue.details),
                    }
                    for issue in item.issues
                ],
            )
