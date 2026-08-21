from __future__ import annotations

import argparse
import json
import re
import sys
import uuid
from dataclasses import asdict
from datetime import date, datetime
from pathlib import Path
from typing import Any, cast

from style_rotation.config.settings import get_settings
from style_rotation.core.canonical import sha256_hexdigest
from style_rotation.persistence.session import create_postgres_engine
from style_rotation.v022.identity_review import (
    SecurityIdentityEvidenceSpec,
    SecurityIdentityResolutionSpec,
    SecurityIdentityReviewCaseSpec,
    SecurityIdentityReviewService,
)
from style_rotation.v022.sp500_data_audit import (
    Sp500CandidateDates,
    audit_unmapped_historical_identities,
)

_EXPORT_CONTRACT = "v0.22.security_identity_review_export.v1"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Export or publish append-only v0.22 Security identity review evidence"
    )
    commands = parser.add_subparsers(dest="command", required=True)

    export = commands.add_parser("export-unresolved")
    export.add_argument("runtime_root", type=Path)
    export.add_argument("external_import_manifest_id", type=uuid.UUID)
    export.add_argument("--provider-scope", default="fja05680_sp500")
    export.add_argument("--created-by", default="local")
    export.add_argument("--warmup-start", type=date.fromisoformat, default=date(2004, 12, 31))
    export.add_argument(
        "--evaluation-start", type=date.fromisoformat, default=date(2007, 1, 3)
    )
    export.add_argument(
        "--evaluation-end", type=date.fromisoformat, default=date(2026, 6, 30)
    )
    export.add_argument("--output", type=Path)

    for name in ("publish-case", "publish-evidence", "publish-resolution"):
        publish = commands.add_parser(name)
        publish.add_argument("spec", type=Path)
    return parser


def build_unresolved_export(
    *,
    runtime_root: Path,
    external_import_manifest_id: uuid.UUID,
    provider_scope: str,
    created_by: str,
    candidate_dates: Sp500CandidateDates,
) -> dict[str, Any]:
    cases: list[dict[str, Any]] = []
    for item in audit_unmapped_historical_identities(
        runtime_root=runtime_root,
        candidate_dates=candidate_dates,
    ):
        identity = {
            "provider_scope": provider_scope,
            "source_symbol": item.source_symbol,
            "first_observed_session": item.first_observed_session.isoformat(),
            "last_observed_session": item.last_observed_session.isoformat(),
        }
        digest = sha256_hexdigest(identity)[:12]
        slug = re.sub(r"[^a-z0-9]+", "_", item.source_symbol.casefold()).strip("_")
        spec = SecurityIdentityReviewCaseSpec(
            external_import_manifest_id=external_import_manifest_id,
            case_key=f"sp500_identity_{slug}_{digest}"[:200],
            version_number=1,
            provider_scope=provider_scope,
            source_symbol=item.source_symbol,
            first_observed_session=item.first_observed_session,
            last_observed_session=item.last_observed_session,
            observed_snapshot_count=item.observed_snapshot_count,
            membership_episode_count=item.membership_episode_count,
            reason_code=item.reason_code,
            created_by=created_by,
            context={"resolution_status": item.resolution_status},
        )
        cases.append({**spec.document(), "created_by": spec.created_by})
    return {
        "contract_version": _EXPORT_CONTRACT,
        "case_count": len(cases),
        "cases": cases,
    }


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "export-unresolved":
        payload = build_unresolved_export(
            runtime_root=args.runtime_root,
            external_import_manifest_id=args.external_import_manifest_id,
            provider_scope=args.provider_scope,
            created_by=args.created_by,
            candidate_dates=Sp500CandidateDates(
                args.warmup_start, args.evaluation_start, args.evaluation_end
            ),
        )
        encoded = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
        if args.output is None:
            print(encoded)
        else:
            if args.output.exists():
                raise FileExistsError(f"Identity Review export already exists: {args.output}")
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(encoded + "\n", encoding="utf-8")
        return 0

    document = _read_object(args.spec)
    engine = create_postgres_engine(get_settings().database_url)
    service = SecurityIdentityReviewService(engine)
    if args.command == "publish-case":
        publication = service.publish_case(_case_spec(document))
    elif args.command == "publish-evidence":
        publication = service.publish_evidence(_evidence_spec(document))
    else:
        publication = service.publish_resolution(_resolution_spec(document))
    print(json.dumps(asdict(publication), default=str, sort_keys=True))
    return 0


def _case_spec(document: dict[str, Any]) -> SecurityIdentityReviewCaseSpec:
    return SecurityIdentityReviewCaseSpec(
        external_import_manifest_id=uuid.UUID(
            _text(document, "external_import_manifest_id")
        ),
        case_key=_text(document, "case_key"),
        version_number=_integer(document, "version_number"),
        provider_scope=_text(document, "provider_scope"),
        source_symbol=_text(document, "source_symbol"),
        first_observed_session=date.fromisoformat(
            _text(document, "first_observed_session")
        ),
        last_observed_session=date.fromisoformat(
            _text(document, "last_observed_session")
        ),
        observed_snapshot_count=_integer(document, "observed_snapshot_count"),
        membership_episode_count=_integer(document, "membership_episode_count"),
        reason_code=_text(document, "reason_code"),
        created_by=_text(document, "created_by"),
        context=_object(document, "context"),
    )


def _evidence_spec(document: dict[str, Any]) -> SecurityIdentityEvidenceSpec:
    return SecurityIdentityEvidenceSpec(
        review_case_id=uuid.UUID(_text(document, "review_case_id")),
        evidence_key=_text(document, "evidence_key"),
        version_number=_integer(document, "version_number"),
        evidence_kind=cast(Any, _text(document, "evidence_kind")),
        source_uri=_text(document, "source_uri"),
        content_sha256=_text(document, "content_sha256"),
        known_at=datetime.fromisoformat(_text(document, "known_at")),
        effective_session=(
            date.fromisoformat(_text(document, "effective_session"))
            if document.get("effective_session") is not None
            else None
        ),
        recorded_by=_text(document, "recorded_by"),
        facts=_object(document, "facts"),
    )


def _resolution_spec(document: dict[str, Any]) -> SecurityIdentityResolutionSpec:
    evidence_ids = document.get("evidence_ids")
    if not isinstance(evidence_ids, list) or not all(
        isinstance(item, str) for item in evidence_ids
    ):
        raise ValueError("evidence_ids must be a list of UUID strings")
    return SecurityIdentityResolutionSpec(
        review_case_id=uuid.UUID(_text(document, "review_case_id")),
        version_number=_integer(document, "version_number"),
        resolution_status=cast(Any, _text(document, "resolution_status")),
        resolution_kind=cast(Any, _text(document, "resolution_kind")),
        evidence_ids=tuple(uuid.UUID(item) for item in evidence_ids),
        target_security_id=_optional_uuid(document.get("target_security_id")),
        target_security_identifier_id=_optional_uuid(
            document.get("target_security_identifier_id")
        ),
        supersedes_resolution_id=_optional_uuid(
            document.get("supersedes_resolution_id")
        ),
        resolved_by=_text(document, "resolved_by"),
        details=_object(document, "details"),
    )


def _read_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("Identity Review specification must be a JSON object")
    return cast(dict[str, Any], value)


def _text(document: dict[str, Any], key: str) -> str:
    value = document.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} must be non-empty text")
    return value


def _integer(document: dict[str, Any], key: str) -> int:
    value = document.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{key} must be an integer")
    return value


def _object(document: dict[str, Any], key: str) -> dict[str, Any]:
    value = document.get(key, {})
    if not isinstance(value, dict):
        raise ValueError(f"{key} must be an object")
    return cast(dict[str, Any], value)


def _optional_uuid(value: object) -> uuid.UUID | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("Optional identity values must be UUID strings")
    return uuid.UUID(value)


def run() -> None:
    try:
        raise SystemExit(main())
    except (FileNotFoundError, LookupError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(2) from error


if __name__ == "__main__":
    run()
