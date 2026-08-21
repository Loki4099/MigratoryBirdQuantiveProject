from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, cast

from sqlalchemy import Engine, text
from sqlalchemy.engine import Connection, RowMapping

from style_rotation.core.canonical import sha256_hexdigest
from style_rotation.lineage.service import ArtifactService, DependencyInput

_CONTRACT = "migratory_bird_v022_green_baseline_quality_v1"
_UNAVAILABLE_RULE = "security_uniformly_excluded_provider_unavailable"


@dataclass(frozen=True, slots=True)
class GreenBaselineQualitySpec:
    dataset_publication_id: uuid.UUID
    calendar_version_id: uuid.UUID
    exclusion_policy_path: Path
    report_key: str
    version_number: int = 1
    created_by: str = "codex-green-baseline-quality"

    def __post_init__(self) -> None:
        if not self.report_key.strip() or self.version_number < 1:
            raise ValueError("green baseline quality identity is incomplete")


@dataclass(frozen=True, slots=True)
class GreenBaselineQualityPublication:
    quality_report_id: str
    artifact_id: str
    dataset_publication_id: str
    error_count: int
    warning_count: int
    uniformly_excluded_security_count: int
    zero_volume_security_count: int
    large_move_security_count: int
    report_fingerprint: str
    reused: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _policy(path: Path) -> tuple[str, dict[uuid.UUID, dict[str, Any]]]:
    raw = path.read_bytes()
    document = json.loads(raw)
    policy = document.get("exclusion_policy")
    if not isinstance(policy, dict) or not isinstance(policy.get("decisions"), list):
        raise ValueError("green baseline exclusion policy is malformed")
    decisions: dict[uuid.UUID, dict[str, Any]] = {}
    for item in policy["decisions"]:
        if not isinstance(item, dict):
            raise ValueError("green baseline exclusion decision is malformed")
        security_id = uuid.UUID(str(item["security_id"]))
        if security_id in decisions:
            raise ValueError("green baseline exclusion policy contains duplicate Securities")
        decisions[security_id] = item
    return hashlib.sha256(raw).hexdigest(), decisions


def _inputs(connection: Connection, spec: GreenBaselineQualitySpec) -> RowMapping:
    row = (
        connection.execute(
            text(
                """
                SELECT publication.dataset_publication_id,
                       publication.artifact_id AS dataset_artifact_id,
                       publication.dataset_key,publication.version_number,
                       publication.dataset_kind,publication.value_kind,
                       dataset_artifact.status AS dataset_status,
                       calendar.calendar_version_id,
                       calendar.artifact_id AS calendar_artifact_id,
                       calendar_artifact.status AS calendar_status,
                       manifest.external_import_manifest_id,
                       manifest.artifact_id AS external_import_manifest_artifact_id,
                       manifest_artifact.status AS manifest_status
                  FROM data.dataset_publication publication
                  JOIN lineage.artifact dataset_artifact
                    ON dataset_artifact.artifact_id=publication.artifact_id
                  JOIN lineage.artifact_dependency calendar_dependency
                    ON calendar_dependency.artifact_id=publication.artifact_id
                   AND calendar_dependency.role='calendar_version'
                  JOIN catalog.calendar_version calendar
                    ON calendar.artifact_id=calendar_dependency.depends_on_artifact_id
                  JOIN lineage.artifact calendar_artifact
                    ON calendar_artifact.artifact_id=calendar.artifact_id
                  JOIN lineage.artifact_dependency manifest_dependency
                    ON manifest_dependency.artifact_id=publication.artifact_id
                   AND manifest_dependency.role='external_import_manifest'
                  JOIN data.v022_external_import_manifest manifest
                    ON manifest.artifact_id=manifest_dependency.depends_on_artifact_id
                  JOIN lineage.artifact manifest_artifact
                    ON manifest_artifact.artifact_id=manifest.artifact_id
                 WHERE publication.dataset_publication_id=:dataset
                   AND calendar.calendar_version_id=:calendar
                """
            ),
            {"dataset": spec.dataset_publication_id, "calendar": spec.calendar_version_id},
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise ValueError("green baseline Dataset lacks exact import/calendar provenance")
    if (
        row["dataset_status"] != "published"
        or row["calendar_status"] != "published"
        or row["manifest_status"] != "published"
        or row["dataset_kind"] != "canonical"
        or row["value_kind"] != "daily_bar"
    ):
        raise ValueError("green baseline Dataset source identities are not published")
    return row


def _facts(connection: Connection, dataset_id: uuid.UUID) -> dict[str, Any]:
    scalar = connection.execute(
        text(
            """
            SELECT count(*) AS bar_count,count(DISTINCT bar.asset_id) AS asset_count,
                   min(bar.session_date) AS coverage_start,
                   max(bar.session_date) AS coverage_end,
                   count(*) FILTER (WHERE LEAST(
                     bar.open_raw,bar.high_raw,bar.low_raw,bar.close_raw,
                     bar.adj_close,bar.open_adj,bar.high_adj,bar.low_adj,
                     bar.close_adj,bar.adjustment_factor
                   )<=0) AS non_positive_count,
                   count(*) FILTER (WHERE
                     bar.low_raw>LEAST(bar.open_raw,bar.close_raw) OR
                     bar.high_raw<GREATEST(bar.open_raw,bar.close_raw) OR
                     bar.low_raw>bar.high_raw
                   ) AS raw_envelope_error_count,
                   count(*) FILTER (WHERE bar.volume_raw=0) AS zero_volume_count
              FROM data.daily_bar bar
             WHERE bar.dataset_publication_id=:dataset
            """
        ),
        {"dataset": dataset_id},
    ).mappings().one()
    zero_rows = connection.execute(
        text(
            """
            SELECT security.security_id,count(*) AS observation_count,
                   min(bar.session_date) AS first_session,
                   max(bar.session_date) AS last_session
              FROM data.daily_bar bar
              JOIN catalog.security security ON security.legacy_asset_id=bar.asset_id
             WHERE bar.dataset_publication_id=:dataset AND bar.volume_raw=0
             GROUP BY security.security_id ORDER BY security.security_id
            """
        ),
        {"dataset": dataset_id},
    ).mappings().all()
    move_rows = connection.execute(
        text(
            """
            WITH ordered AS (
              SELECT security.security_id,bar.session_date,bar.adj_close,
                     lag(bar.adj_close) OVER (
                       PARTITION BY security.security_id ORDER BY bar.session_date
                     ) AS prior_close
                FROM data.daily_bar bar
                JOIN catalog.security security ON security.legacy_asset_id=bar.asset_id
               WHERE bar.dataset_publication_id=:dataset
            )
            SELECT security_id,count(*) AS observation_count,
                   min(session_date) AS first_session,max(session_date) AS last_session
              FROM ordered
             WHERE prior_close>0 AND abs(adj_close/prior_close-1)>0.5
             GROUP BY security_id ORDER BY security_id
            """
        ),
        {"dataset": dataset_id},
    ).mappings().all()
    return {"scalar": scalar, "zero_rows": zero_rows, "move_rows": move_rows}


def _issue(
    severity: str,
    rule_code: str,
    message: str,
    subject_key: str,
    details: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "severity": severity,
        "rule_code": rule_code,
        "message": message,
        "subject_key": subject_key,
        "session_date": None,
        "details": dict(details or {}),
    }


def _report_document(
    *,
    dataset: RowMapping,
    policy_sha256: str,
    policy_decisions: Mapping[uuid.UUID, Mapping[str, Any]],
    facts: Mapping[str, Any],
) -> dict[str, Any]:
    scalar = cast(RowMapping, facts["scalar"])
    zero_rows = cast(Sequence[RowMapping], facts["zero_rows"])
    move_rows = cast(Sequence[RowMapping], facts["move_rows"])
    zero_by_security = {cast(uuid.UUID, row["security_id"]): row for row in zero_rows}
    exclusions = set(policy_decisions) | set(zero_by_security)
    issues: list[dict[str, Any]] = []
    if int(scalar["non_positive_count"]) > 0:
        issues.append(
            _issue(
                "error",
                "non_positive_market_value",
                "Imported baseline contains a non-positive market value",
                str(dataset["dataset_publication_id"]),
                {"observation_count": int(scalar["non_positive_count"])},
            )
        )
    if int(scalar["raw_envelope_error_count"]) > 0:
        issues.append(
            _issue(
                "error",
                "raw_ohlc_envelope_invalid",
                "Imported baseline contains an invalid raw OHLC envelope",
                str(dataset["dataset_publication_id"]),
                {"observation_count": int(scalar["raw_envelope_error_count"])},
            )
        )
    issues.append(
        _issue(
            "warning",
            "free_retrospective_market_data_limitations",
            "Free retrospective prices are not provider-native point-in-time data",
            str(dataset["dataset_publication_id"]),
        )
    )
    for security_id in sorted(exclusions, key=str):
        policy = policy_decisions.get(security_id, {})
        zero = zero_by_security.get(security_id)
        issues.append(
            _issue(
                "warning",
                _UNAVAILABLE_RULE,
                "Security is preserved in identity history but excluded from experiments",
                str(security_id),
                {
                    "policy_reason_code": policy.get(
                        "reason_code", "observed_zero_volume_market_path"
                    ),
                    "zero_volume_observation_count": (
                        0 if zero is None else int(zero["observation_count"])
                    ),
                    "reviewer_note": policy.get(
                        "reviewer_note",
                        "Zero-volume observations are not treated as executable market data.",
                    ),
                },
            )
        )
    for row in move_rows:
        issues.append(
            _issue(
                "warning",
                "adjusted_return_over_50_percent_reviewed_not_excluded",
                "Large adjusted return is retained as a review finding",
                str(row["security_id"]),
                {
                    "observation_count": int(row["observation_count"]),
                    "first_session": str(row["first_session"]),
                    "last_session": str(row["last_session"]),
                },
            )
        )
    return {
        "contract_version": _CONTRACT,
        "dataset_publication_id": str(dataset["dataset_publication_id"]),
        "dataset_artifact_id": str(dataset["dataset_artifact_id"]),
        "dataset_key": dataset["dataset_key"],
        "dataset_version": int(dataset["version_number"]),
        "external_import_manifest_id": str(dataset["external_import_manifest_id"]),
        "calendar_version_id": str(dataset["calendar_version_id"]),
        "research_tier": "rankable_research",
        "price_semantics": "split_normalized_ohlcv_dividends_backward_total_return_v2",
        "historical_pit_claimed": False,
        "exclusion_policy_sha256": policy_sha256,
        "bar_count": int(scalar["bar_count"]),
        "asset_count": int(scalar["asset_count"]),
        "coverage_start": str(scalar["coverage_start"]),
        "coverage_end": str(scalar["coverage_end"]),
        "zero_volume_observation_count": int(scalar["zero_volume_count"]),
        "zero_volume_security_count": len(zero_rows),
        "uniformly_excluded_security_count": len(exclusions),
        "large_move_security_count": len(move_rows),
        "large_move_policy": "retain_as_warning_without_automatic_exclusion",
        "issues": issues,
    }


def publish_green_baseline_quality(
    engine: Engine, spec: GreenBaselineQualitySpec
) -> GreenBaselineQualityPublication:
    policy_sha256, decisions = _policy(spec.exclusion_policy_path)
    with engine.connect() as connection:
        dataset = _inputs(connection, spec)
        facts = _facts(connection, spec.dataset_publication_id)
        known_ids = {
            row[0]
            for row in connection.execute(
                text("SELECT security_id FROM catalog.security")
            ).all()
        }
    unknown = set(decisions).difference(known_ids)
    if unknown:
        raise ValueError("green baseline exclusion policy references unknown Securities")
    document = _report_document(
        dataset=dataset,
        policy_sha256=policy_sha256,
        policy_decisions=decisions,
        facts=facts,
    )
    issues = cast(list[dict[str, Any]], document["issues"])
    errors = sum(item["severity"] == "error" for item in issues)
    warnings = sum(item["severity"] == "warning" for item in issues)
    fingerprint = sha256_hexdigest(document)
    report_id = uuid.uuid5(
        uuid.NAMESPACE_URL, f"bird:v0.22:green-baseline-quality:{fingerprint}"
    )

    def write(connection: Connection, artifact_id: uuid.UUID) -> None:
        connection.execute(
            text(
                """
                INSERT INTO data.v022_security_market_quality_report (
                  security_market_quality_report_id,artifact_id,
                  yahoo_ingestion_plan_id,yahoo_ingestion_plan_artifact_id,
                  source_dataset_publication_id,source_dataset_artifact_id,
                  external_import_manifest_id,external_import_manifest_artifact_id,
                  calendar_version_id,calendar_artifact_id,report_key,version_number,
                  research_tier,error_count,warning_count,unavailable_segment_count,
                  report_document,report_fingerprint,created_by
                ) VALUES (
                  :id,:artifact,NULL,NULL,:dataset,:dataset_artifact,
                  :manifest,:manifest_artifact,:calendar,:calendar_artifact,
                  :key,:version,'rankable_research',:errors,:warnings,:unavailable,
                  CAST(:document AS jsonb),:fingerprint,:created_by
                )
                """
            ),
            {
                "id": report_id,
                "artifact": artifact_id,
                "dataset": spec.dataset_publication_id,
                "dataset_artifact": dataset["dataset_artifact_id"],
                "manifest": dataset["external_import_manifest_id"],
                "manifest_artifact": dataset["external_import_manifest_artifact_id"],
                "calendar": spec.calendar_version_id,
                "calendar_artifact": dataset["calendar_artifact_id"],
                "key": spec.report_key,
                "version": spec.version_number,
                "errors": errors,
                "warnings": warnings,
                "unavailable": int(document["uniformly_excluded_security_count"]),
                "document": json.dumps(document, sort_keys=True),
                "fingerprint": fingerprint,
                "created_by": spec.created_by,
            },
        )

    publication = ArtifactService(engine).publish(
        artifact_type="v022_security_market_quality_report",
        artifact_key=f"v022_security_market_quality_report__{spec.report_key}",
        version_number=spec.version_number,
        semantic_payload=document,
        content_payload=document,
        dependencies=(
            DependencyInput(dataset["dataset_artifact_id"], "market_dataset", 0),
            DependencyInput(
                dataset["external_import_manifest_artifact_id"],
                "external_import_manifest",
                1,
            ),
            DependencyInput(dataset["calendar_artifact_id"], "calendar_version", 2),
        ),
        reason=f"publish clean-green quality report {spec.report_key}",
        draft_writer=write,
    )
    return GreenBaselineQualityPublication(
        quality_report_id=str(report_id),
        artifact_id=str(publication.artifact_id),
        dataset_publication_id=str(spec.dataset_publication_id),
        error_count=errors,
        warning_count=warnings,
        uniformly_excluded_security_count=int(
            document["uniformly_excluded_security_count"]
        ),
        zero_volume_security_count=int(document["zero_volume_security_count"]),
        large_move_security_count=int(document["large_move_security_count"]),
        report_fingerprint=fingerprint,
        reused=publication.reused,
    )
