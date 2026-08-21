from __future__ import annotations

import json
import math
import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any, Literal, cast

from sqlalchemy import Engine, bindparam, text
from sqlalchemy.engine import Connection

from style_rotation.core.canonical import sha256_hexdigest
from style_rotation.lineage.service import ArtifactService, DependencyInput

LifecycleEventType = Literal[
    "trading_halt",
    "trading_resume",
    "delisting",
    "otc_transition",
    "cash_merger",
    "stock_merger",
    "share_class_conversion",
    "spinoff",
    "bankruptcy",
    "liquidation",
]
LifecycleEventStatus = Literal["confirmed", "estimated", "unresolved"]
ValuationState = Literal["live", "stale_confirmed", "terminal", "unavailable"]
EvidenceRole = Literal[
    "primary_notice",
    "market_status",
    "corporate_action_terms",
    "identity_resolution",
    "other_public_record",
]
SettlementLegKind = Literal[
    "cash", "successor_security", "distributed_security", "writeoff"
]
SettlementValuationPolicy = Literal[
    "fixed_cash",
    "successor_market_value",
    "distribution_market_value",
    "zero_recovery",
]

_CONTRACT = "v0.22.security_lifecycle_event.v1"
_EVENT_TYPES = {
    "trading_halt",
    "trading_resume",
    "delisting",
    "otc_transition",
    "cash_merger",
    "stock_merger",
    "share_class_conversion",
    "spinoff",
    "bankruptcy",
    "liquidation",
}
_TERMINAL_TYPES = {
    "delisting",
    "cash_merger",
    "stock_merger",
    "share_class_conversion",
    "bankruptcy",
    "liquidation",
}
_EVENT_STATUSES = {"confirmed", "estimated", "unresolved"}
_VALUATION_STATES = {"live", "stale_confirmed", "terminal", "unavailable"}
_EVIDENCE_ROLES = {
    "primary_notice",
    "market_status",
    "corporate_action_terms",
    "identity_resolution",
    "other_public_record",
}
_KEY_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_.:-]{0,199}$")
_CURRENCY_PATTERN = re.compile(r"^[A-Z]{3}$")
_WINDOWS_PATH = re.compile(r"^[A-Za-z]:[\\/]")


@dataclass(frozen=True, slots=True)
class LifecycleEvidenceRef:
    artifact_id: uuid.UUID
    role: EvidenceRole

    def __post_init__(self) -> None:
        if self.role not in _EVIDENCE_ROLES:
            raise ValueError("Unsupported Lifecycle Evidence role")


@dataclass(frozen=True, slots=True)
class SecuritySettlementLegSpec:
    leg_kind: SettlementLegKind
    target_security_id: uuid.UUID | None = None
    quantity_per_source_share: Decimal | None = None
    cash_amount_per_source_share: Decimal | None = None
    currency: str | None = None
    valuation_policy: SettlementValuationPolicy = "zero_recovery"

    def __post_init__(self) -> None:
        for label, value in (
            ("quantity_per_source_share", self.quantity_per_source_share),
            ("cash_amount_per_source_share", self.cash_amount_per_source_share),
        ):
            if value is not None and (not value.is_finite() or value < 0):
                raise ValueError(f"Settlement {label} must be finite and non-negative")
        if self.leg_kind == "cash":
            valid = (
                self.target_security_id is None
                and self.quantity_per_source_share is None
                and self.cash_amount_per_source_share is not None
                and self.currency is not None
                and _CURRENCY_PATTERN.fullmatch(self.currency) is not None
                and self.valuation_policy == "fixed_cash"
            )
        elif self.leg_kind in {"successor_security", "distributed_security"}:
            expected = (
                "successor_market_value"
                if self.leg_kind == "successor_security"
                else "distribution_market_value"
            )
            valid = (
                self.target_security_id is not None
                and self.quantity_per_source_share is not None
                and self.quantity_per_source_share > 0
                and self.cash_amount_per_source_share is None
                and self.currency is None
                and self.valuation_policy == expected
            )
        elif self.leg_kind == "writeoff":
            valid = (
                self.target_security_id is None
                and self.quantity_per_source_share is None
                and self.cash_amount_per_source_share is None
                and self.currency is None
                and self.valuation_policy == "zero_recovery"
            )
        else:
            valid = False
        if not valid:
            raise ValueError("Settlement Leg fields do not match its kind")

    def canonical_key(self) -> tuple[str, str, str, str, str, str]:
        return (
            self.leg_kind,
            str(self.target_security_id or ""),
            str(self.quantity_per_source_share or ""),
            str(self.cash_amount_per_source_share or ""),
            self.currency or "",
            self.valuation_policy,
        )

    def document(self, ordinal: int) -> dict[str, object]:
        return {
            "ordinal": ordinal,
            "leg_kind": self.leg_kind,
            "target_security_id": (
                str(self.target_security_id)
                if self.target_security_id is not None
                else None
            ),
            "quantity_per_source_share": (
                str(self.quantity_per_source_share)
                if self.quantity_per_source_share is not None
                else None
            ),
            "cash_amount_per_source_share": (
                str(self.cash_amount_per_source_share)
                if self.cash_amount_per_source_share is not None
                else None
            ),
            "currency": self.currency,
            "valuation_policy": self.valuation_policy,
        }


@dataclass(frozen=True, slots=True)
class SecurityLifecycleEventSpec:
    security_id: uuid.UUID
    event_key: str
    version_number: int
    event_type: LifecycleEventType
    event_status: LifecycleEventStatus
    announced_at: datetime
    effective_session: date
    selectable_after: bool
    tradable_after: bool
    valuation_state_after: ValuationState
    evidence: tuple[LifecycleEvidenceRef, ...]
    created_by: str
    last_trading_session: date | None = None
    settlement_session: date | None = None
    settlement_legs: tuple[SecuritySettlementLegSpec, ...] = ()
    supersedes_lifecycle_event_id: uuid.UUID | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if _KEY_PATTERN.fullmatch(self.event_key) is None:
            raise ValueError("Lifecycle event_key must be a stable lowercase key")
        if self.version_number < 1 or not self.created_by.strip():
            raise ValueError("Lifecycle Event identity is incomplete")
        if self.event_type not in _EVENT_TYPES:
            raise ValueError("Unsupported Lifecycle Event type")
        if self.event_status not in _EVENT_STATUSES:
            raise ValueError("Unsupported Lifecycle Event status")
        if self.valuation_state_after not in _VALUATION_STATES:
            raise ValueError("Unsupported Lifecycle valuation state")
        if self.announced_at.tzinfo is None or self.announced_at.utcoffset() is None:
            raise ValueError("Lifecycle announced_at must be timezone-aware")
        if self.announced_at.date() > self.effective_session:
            raise ValueError("Lifecycle Event cannot be announced after it is effective")
        if (
            self.last_trading_session is not None
            and self.last_trading_session > self.effective_session
        ):
            raise ValueError("Lifecycle last trading session must not follow effective date")
        if self.settlement_legs != () and self.settlement_session is None:
            raise ValueError("Settlement legs require an exact settlement session")
        if self.settlement_session is not None and (
            not self.settlement_legs
            or self.settlement_session < self.effective_session
        ):
            raise ValueError("Settlement session does not match the settlement legs")
        if not self.evidence or len({item.artifact_id for item in self.evidence}) != len(
            self.evidence
        ):
            raise ValueError("Lifecycle Event requires unique source Evidence")
        if len({item.canonical_key() for item in self.settlement_legs}) != len(
            self.settlement_legs
        ):
            raise ValueError("Settlement legs must be unique")
        if any(
            leg.target_security_id == self.security_id
            for leg in self.settlement_legs
            if leg.target_security_id is not None
        ):
            raise ValueError("Settlement successor cannot be the source Security")
        self._validate_state_transition()
        if self.version_number == 1 and self.supersedes_lifecycle_event_id is not None:
            raise ValueError("First Lifecycle Event version cannot supersede another")
        if self.version_number > 1 and self.supersedes_lifecycle_event_id is None:
            raise ValueError("Later Lifecycle Event must supersede the previous version")
        object.__setattr__(self, "details", _json_object(self.details))

    def _validate_state_transition(self) -> None:
        if self.event_type == "trading_halt":
            expected = (False, False, "stale_confirmed")
        elif self.event_type == "trading_resume":
            expected = (True, True, "live")
        elif self.event_type in _TERMINAL_TYPES:
            expected = (False, False, "terminal")
        elif self.event_type == "spinoff":
            expected = (True, True, "live")
        else:
            expected = (self.selectable_after, self.tradable_after, self.valuation_state_after)
        if expected != (
            self.selectable_after,
            self.tradable_after,
            self.valuation_state_after,
        ):
            raise ValueError("Lifecycle state transition does not match its event type")
        if self.event_type in {"trading_halt", "trading_resume", "otc_transition"}:
            if self.settlement_legs:
                raise ValueError("Non-terminal Lifecycle Event cannot settle a position")
        elif (
            self.event_status == "confirmed"
            and self.event_type in _TERMINAL_TYPES | {"spinoff"}
            and not self.settlement_legs
        ):
            raise ValueError("Confirmed terminal Lifecycle Event requires settlement legs")
        if self.event_status == "confirmed":
            kinds = {item.leg_kind for item in self.settlement_legs}
            if self.event_type == "cash_merger" and "cash" not in kinds:
                raise ValueError("Cash merger requires a cash Settlement Leg")
            if (
                self.event_type in {"stock_merger", "share_class_conversion"}
                and "successor_security" not in kinds
            ):
                raise ValueError("Stock conversion requires a successor Settlement Leg")
            if self.event_type == "spinoff" and "distributed_security" not in kinds:
                raise ValueError("Spinoff requires a distributed Security Settlement Leg")

    def canonical_evidence(self) -> tuple[LifecycleEvidenceRef, ...]:
        return tuple(sorted(self.evidence, key=lambda item: (item.role, str(item.artifact_id))))

    def canonical_legs(self) -> tuple[SecuritySettlementLegSpec, ...]:
        return tuple(sorted(self.settlement_legs, key=lambda item: item.canonical_key()))

    def document(self) -> dict[str, Any]:
        evidence = self.canonical_evidence()
        legs = self.canonical_legs()
        return {
            "contract_version": _CONTRACT,
            "security_id": str(self.security_id),
            "event_key": self.event_key,
            "version_number": self.version_number,
            "event_type": self.event_type,
            "event_status": self.event_status,
            "announced_at": self.announced_at.astimezone(UTC).isoformat(),
            "effective_session": self.effective_session.isoformat(),
            "last_trading_session": (
                self.last_trading_session.isoformat()
                if self.last_trading_session is not None
                else None
            ),
            "settlement_session": (
                self.settlement_session.isoformat()
                if self.settlement_session is not None
                else None
            ),
            "selectable_after": self.selectable_after,
            "tradable_after": self.tradable_after,
            "valuation_state_after": self.valuation_state_after,
            "evidence_count": len(evidence),
            "settlement_leg_count": len(legs),
            "supersedes_lifecycle_event_id": (
                str(self.supersedes_lifecycle_event_id)
                if self.supersedes_lifecycle_event_id is not None
                else None
            ),
            "evidence_artifact_ids": [str(item.artifact_id) for item in evidence],
            "evidence": [
                {"artifact_id": str(item.artifact_id), "role": item.role}
                for item in evidence
            ],
            "settlement_legs": [item.document(index) for index, item in enumerate(legs)],
            "details": self.details,
        }


@dataclass(frozen=True, slots=True)
class SecurityLifecycleEventPublication:
    security_lifecycle_event_id: uuid.UUID
    artifact_id: uuid.UUID
    event_fingerprint: str
    reused: bool


class SecurityLifecycleEventService:
    """Publish source-backed lifecycle state and deterministic position settlement."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._artifacts = ArtifactService(engine)

    def publish(self, spec: SecurityLifecycleEventSpec) -> SecurityLifecycleEventPublication:
        evidence = spec.canonical_evidence()
        legs = spec.canonical_legs()
        with self._engine.connect() as connection:
            security_key = connection.execute(
                text("SELECT security_key FROM catalog.security WHERE security_id=:security"),
                {"security": spec.security_id},
            ).scalar_one_or_none()
            if security_key is None:
                raise LookupError("Lifecycle Security not found")
            _require_published_evidence(connection, evidence)
            prior_artifact_id = _prior_artifact(connection, spec)
        document = spec.document()
        fingerprint = sha256_hexdigest(document)
        event_id = uuid.uuid5(
            uuid.NAMESPACE_URL, f"bird:v0.22:security-lifecycle:{fingerprint}"
        )
        dependencies = tuple(
            DependencyInput(item.artifact_id, "source_evidence", ordinal)
            for ordinal, item in enumerate(evidence)
        )
        if prior_artifact_id is not None:
            dependencies += (
                DependencyInput(
                    prior_artifact_id, "superseded_lifecycle_event", len(evidence)
                ),
            )

        def writer(connection: Connection, artifact_id: uuid.UUID) -> None:
            connection.execute(
                text(
                    """
                    INSERT INTO catalog.v022_security_lifecycle_event (
                      security_lifecycle_event_id,artifact_id,security_id,event_key,
                      version_number,event_type,event_status,announced_at,
                      effective_session,last_trading_session,settlement_session,
                      selectable_after,tradable_after,valuation_state_after,
                      evidence_count,settlement_leg_count,
                      supersedes_lifecycle_event_id,event_document,event_fingerprint,
                      created_by
                    ) VALUES (
                      :id,:artifact,:security,:key,:version,:type,:status,:announced,
                      :effective,:last_trading,:settlement,:selectable,:tradable,
                      :valuation,:evidence_count,:leg_count,:supersedes,
                      CAST(:document AS jsonb),:fingerprint,:created_by
                    )
                    """
                ),
                {
                    "id": event_id,
                    "artifact": artifact_id,
                    "security": spec.security_id,
                    "key": spec.event_key,
                    "version": spec.version_number,
                    "type": spec.event_type,
                    "status": spec.event_status,
                    "announced": spec.announced_at,
                    "effective": spec.effective_session,
                    "last_trading": spec.last_trading_session,
                    "settlement": spec.settlement_session,
                    "selectable": spec.selectable_after,
                    "tradable": spec.tradable_after,
                    "valuation": spec.valuation_state_after,
                    "evidence_count": len(evidence),
                    "leg_count": len(legs),
                    "supersedes": spec.supersedes_lifecycle_event_id,
                    "document": json.dumps(document, sort_keys=True),
                    "fingerprint": fingerprint,
                    "created_by": spec.created_by,
                },
            )
            connection.execute(
                text(
                    """
                    INSERT INTO catalog.v022_security_lifecycle_event_evidence (
                      security_lifecycle_event_id,ordinal,evidence_artifact_id,
                      evidence_role
                    ) VALUES (:event,:ordinal,:artifact,:role)
                    """
                ),
                [
                    {
                        "event": event_id,
                        "ordinal": ordinal,
                        "artifact": item.artifact_id,
                        "role": item.role,
                    }
                    for ordinal, item in enumerate(evidence)
                ],
            )
            if legs:
                connection.execute(
                    text(
                        """
                        INSERT INTO catalog.v022_security_settlement_leg (
                          security_lifecycle_event_id,ordinal,leg_kind,
                          target_security_id,quantity_per_source_share,
                          cash_amount_per_source_share,currency,valuation_policy,
                          leg_document
                        ) VALUES (
                          :event,:ordinal,:kind,:target,:quantity,:cash,:currency,
                          :policy,CAST(:document AS jsonb)
                        )
                        """
                    ),
                    [
                        {
                            "event": event_id,
                            "ordinal": ordinal,
                            "kind": item.leg_kind,
                            "target": item.target_security_id,
                            "quantity": item.quantity_per_source_share,
                            "cash": item.cash_amount_per_source_share,
                            "currency": item.currency,
                            "policy": item.valuation_policy,
                            "document": json.dumps(item.document(ordinal), sort_keys=True),
                        }
                        for ordinal, item in enumerate(legs)
                    ],
                )

        result = self._artifacts.publish(
            artifact_type="v022_security_lifecycle_event",
            artifact_key=(
                f"v022_security_lifecycle__{security_key}__{spec.event_key}"
            ),
            version_number=spec.version_number,
            semantic_payload=document,
            content_payload=document,
            dependencies=dependencies,
            reason=f"publish Security lifecycle event {security_key}/{spec.event_key}",
            draft_writer=writer,
        )
        return SecurityLifecycleEventPublication(
            event_id, result.artifact_id, fingerprint, result.reused
        )


def _require_published_evidence(
    connection: Connection, evidence: tuple[LifecycleEvidenceRef, ...]
) -> None:
    artifact_ids = tuple(item.artifact_id for item in evidence)
    rows = connection.execute(
        text(
            "SELECT artifact_id,status FROM lineage.artifact WHERE artifact_id IN :ids"
        ).bindparams(bindparam("ids", expanding=True)),
        {"ids": artifact_ids},
    ).all()
    published = {cast(uuid.UUID, row[0]) for row in rows if row[1] == "published"}
    if published != set(artifact_ids):
        raise ValueError("Lifecycle Event requires exact published Evidence")


def _prior_artifact(
    connection: Connection, spec: SecurityLifecycleEventSpec
) -> uuid.UUID | None:
    if spec.supersedes_lifecycle_event_id is None:
        return None
    row = connection.execute(
        text(
            """
            SELECT event.artifact_id,event.security_id,event.event_key,
                   event.version_number,artifact.status
              FROM catalog.v022_security_lifecycle_event event
              JOIN lineage.artifact artifact ON artifact.artifact_id=event.artifact_id
             WHERE event.security_lifecycle_event_id=:event
            """
        ),
        {"event": spec.supersedes_lifecycle_event_id},
    ).mappings().one_or_none()
    if row is None or any(
        (
            row["status"] != "published",
            row["security_id"] != spec.security_id,
            row["event_key"] != spec.event_key,
            row["version_number"] != spec.version_number - 1,
        )
    ):
        raise ValueError("Lifecycle Event supersession is not exact")
    return cast(uuid.UUID, row["artifact_id"])


def _json_object(value: dict[str, Any]) -> dict[str, Any]:
    encoded = json.dumps(value, sort_keys=True, allow_nan=False, default=_json_default)
    document = json.loads(encoded)
    _reject_workstation_paths(document)
    return cast(dict[str, Any], document)


def _json_default(value: object) -> object:
    if isinstance(value, (date, datetime, uuid.UUID, Decimal)):
        return str(value)
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("Lifecycle details cannot contain non-finite values")
    raise TypeError(f"Unsupported Lifecycle detail value: {type(value).__name__}")


def _reject_workstation_paths(value: object) -> None:
    if isinstance(value, dict):
        for item in value.values():
            _reject_workstation_paths(item)
    elif isinstance(value, list):
        for item in value:
            _reject_workstation_paths(item)
    elif isinstance(value, str) and (
        _WINDOWS_PATH.match(value) is not None or value.startswith(("file:", "\\\\"))
    ):
        raise ValueError("Lifecycle details cannot contain a workstation path")
