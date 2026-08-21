from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from functools import partial
from typing import Any

from sqlalchemy import Engine, bindparam, text
from sqlalchemy.engine import Connection

from style_rotation.core.canonical import sha256_hexdigest
from style_rotation.lineage.service import ArtifactService, DependencyInput
from style_rotation.ops.v021_execution import V021DatabaseExecutor, _defensive_allocations
from style_rotation.product.v021_monitoring import V021MonitoringCalculator
from style_rotation.strategy.v021_topk import RankedAsset, build_topk_decision


@dataclass(frozen=True, slots=True)
class V021ShadowExecutionSpecPublication:
    artifact_id: uuid.UUID
    product_version_id: uuid.UUID
    spec_fingerprint: str
    reused: bool


@dataclass(frozen=True, slots=True)
class V021ShadowReplayDecision:
    decision_document: dict[str, object]
    source_artifact_id: uuid.UUID
    known_at: datetime


class V021ShadowExecutionSpecService:
    """Freeze a non-capital v0.21 Product Version for prospective Shadow replay."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._artifacts = ArtifactService(engine)

    def publish(
        self,
        *,
        spec_key: str,
        version_number: int,
        product_version_id: uuid.UUID,
        prior_holdings: tuple[str, ...] = (),
    ) -> V021ShadowExecutionSpecPublication:
        spec_key = spec_key.strip()
        holdings = tuple(sorted({item.strip() for item in prior_holdings if item.strip()}))
        if not spec_key or version_number < 1:
            raise ValueError("Shadow execution spec key and positive version are required")
        with self._engine.connect() as connection:
            product = connection.execute(
                text(
                    "SELECT version.artifact_id,artifact.status "
                    "FROM product.product_version version "
                    "JOIN lineage.artifact artifact ON artifact.artifact_id=version.artifact_id "
                    "WHERE version.product_version_id=:version"
                ),
                {"version": product_version_id},
            ).mappings().one_or_none()
        if product is None or product["status"] != "published":
            raise ValueError("Shadow execution spec requires a published v0.21 Product Version")
        semantic = {
            "runtime_contract": "v0.21",
            "mode": "shadow_only",
            "product_version_id": str(product_version_id),
            "data_bundle_selection_policy": "latest_published_at_decision_cutoff",
            "prior_holdings": list(holdings),
        }
        fingerprint = sha256_hexdigest(semantic)
        publication = self._artifacts.publish(
            artifact_type="v021_shadow_execution_spec",
            artifact_key=spec_key,
            version_number=version_number,
            semantic_payload=semantic,
            content_payload=semantic,
            dependencies=(DependencyInput(product["artifact_id"], "product_version", 0),),
            reason="publish exact non-capital v0.21 Shadow replay specification",
            draft_writer=partial(
                self._write,
                product_version_id=product_version_id,
                prior_holdings=holdings,
                fingerprint=fingerprint,
            ),
        )
        return V021ShadowExecutionSpecPublication(
            publication.artifact_id,
            product_version_id,
            fingerprint,
            publication.reused,
        )

    @staticmethod
    def _write(
        connection: Connection,
        artifact_id: uuid.UUID,
        *,
        product_version_id: uuid.UUID,
        prior_holdings: tuple[str, ...],
        fingerprint: str,
    ) -> None:
        connection.execute(
            text(
                "INSERT INTO compatibility.v022_shadow_v021_execution_spec "
                "(artifact_id,product_version_id,data_bundle_selection_policy,"
                "prior_holdings,spec_fingerprint) VALUES "
                "(:artifact,:product_version,'latest_published_at_decision_cutoff',"
                "CAST(:holdings AS jsonb),:fingerprint)"
            ),
            {
                "artifact": artifact_id,
                "product_version": product_version_id,
                "holdings": json.dumps(prior_holdings),
                "fingerprint": fingerprint,
            },
        )


class V021ShadowReplayService:
    """Execute one exact v0.21 Product Version at a frozen Shadow cutoff."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._executor = V021DatabaseExecutor(engine)

    def replay(
        self,
        *,
        execution_spec_artifact_id: uuid.UUID,
        decision_session: date,
        decision_cutoff_at: datetime,
    ) -> V021ShadowReplayDecision:
        if decision_cutoff_at.tzinfo is None:
            raise ValueError("v0.21 Shadow replay cutoff must be timezone-aware")
        spec = self._spec(execution_spec_artifact_id)
        bundle = self._bundle(
            spec["product_version_id"],
            decision_session=decision_session,
            decision_cutoff_at=decision_cutoff_at,
        )
        scores, model = self._executor.replay_product_version_scores(
            product_version_id=spec["product_version_id"],
            data_bundle_artifact_id=bundle["artifact_id"],
            as_of_session=decision_session,
        )
        if model["decision_date"] != decision_session:
            raise ValueError("v0.21 Shadow replay did not produce the frozen Decision session")
        selected = self._executor._selected_asset_records(model["normalized_selection"])
        score_lookup = {item["asset_key"]: Decimal(item["score"]) for item in scores}
        parameters = model["strategy_parameters"]
        prior_holdings = set(spec["prior_holdings"])
        formal_membership = bool(
            model["pit_gate_artifact_id"] and model["terminal_gate_artifact_id"]
        )
        eligibility = (
            self._executor._pit_eligibility(
                selected,
                decision_session,
                uuid.UUID(model["pit_gate_artifact_id"]),
                uuid.UUID(model["terminal_gate_artifact_id"]),
            )
            if formal_membership
            else {item["asset_id"]: item["asset_key"] in score_lookup for item in selected}
        )
        sectors = (
            self._executor._sector_keys(
                tuple(item["asset_id"] for item in selected),
                decision_session,
                uuid.UUID(model["pit_gate_artifact_id"]),
            )
            if parameters.get("sector_cap", "none") == "pit_30_percent" and formal_membership
            else {}
        )
        terminal_gate = (
            uuid.UUID(model["terminal_gate_artifact_id"])
            if model["terminal_gate_artifact_id"]
            else None
        )
        _, raw = self._executor.market_data_for_bundle(bundle["artifact_id"], terminal_gate)
        decision = build_topk_decision(
            tuple(
                RankedAsset(
                    asset_key=item["asset_key"],
                    model_score=score_lookup.get(item["asset_key"]),
                    eligible=eligibility.get(item["asset_id"], False),
                    sector_key=sectors.get(item["asset_id"]),
                    previously_held=item["asset_key"] in prior_holdings,
                )
                for item in selected
            ),
            family=model["strategy_family_key"],
            target_k=int(parameters["target_k"]),
            research_mode="formal" if formal_membership else "exploratory",
            selection_buffer=parameters.get("selection_buffer", "none"),
            sector_cap=parameters.get("sector_cap", "none"),
            defense_budget=V021MonitoringCalculator._defense_budget(
                str(parameters.get("defense", "none")), raw, decision_session
            ),
        )
        positions: list[dict[str, object]] = []
        if decision.status == "accepted":
            positions.extend(
                {
                    "asset_key": item.asset_key,
                    "allocation_role": "risk",
                    "rank": item.rank,
                    "target_weight": str(item.target_weight),
                }
                for item in sorted(decision.positions, key=lambda value: value.rank)
            )
            defense, reserve_weight = _defensive_allocations(
                decision.defense_budget,
                model["defensive_basket_version"],
                available_assets={item["asset_key"] for item in raw},
            )
            positions.extend(
                {
                    "asset_key": key,
                    "allocation_role": "defense",
                    "rank": None,
                    "target_weight": str(weight),
                }
                for key, weight in sorted(defense.items())
            )
            if reserve_weight:
                positions.append(
                    {
                        "asset_key": "__reserve__",
                        "allocation_role": "reserve",
                        "rank": None,
                        "target_weight": str(reserve_weight),
                    }
                )
        execution_date = self._next_session(bundle["calendar_version_id"], decision_session)
        return V021ShadowReplayDecision(
            {
                "decision_status": "completed" if decision.status == "accepted" else "missing",
                "decision_session": decision_session.isoformat(),
                "recommended_execution_date": (
                    execution_date.isoformat() if decision.status == "accepted" else None
                ),
                "positions": positions,
                "reason_codes": [decision.reason_code] if decision.reason_code else [],
                "source_execution_spec_artifact_id": str(execution_spec_artifact_id),
                "source_data_bundle_artifact_id": str(bundle["artifact_id"]),
                "source_known_at": bundle["published_at"].isoformat(),
            },
            bundle["artifact_id"],
            decision_cutoff_at,
        )

    def _spec(self, artifact_id: uuid.UUID) -> dict[str, Any]:
        with self._engine.connect() as connection:
            row = connection.execute(
                text(
                    "SELECT spec.*,artifact.status FROM "
                    "compatibility.v022_shadow_v021_execution_spec spec "
                    "JOIN lineage.artifact artifact ON artifact.artifact_id=spec.artifact_id "
                    "WHERE spec.artifact_id=:artifact"
                ),
                {"artifact": artifact_id},
            ).mappings().one_or_none()
        if row is None or row["status"] != "published":
            raise ValueError("v0.21 Shadow execution spec is unavailable")
        return dict(row)

    def _bundle(
        self,
        product_version_id: uuid.UUID,
        *,
        decision_session: date,
        decision_cutoff_at: datetime,
    ) -> dict[str, Any]:
        with self._engine.connect() as connection:
            security_ids = tuple(
                uuid.UUID(value)
                for value in connection.execute(
                    text(
                        "SELECT spec.normalized_selection->'asset_security_ids' "
                        "FROM product.product_version version "
                        "JOIN strategy.compiled_strategy_version strategy ON "
                        "strategy.compiled_strategy_version_id="
                        "version.compiled_strategy_version_id "
                        "JOIN workspace.compiled_research_spec spec ON "
                        "spec.compiled_research_spec_id=strategy.compiled_research_spec_id "
                        "WHERE version.product_version_id=:version"
                    ),
                    {"version": product_version_id},
                ).scalar_one()
            )
            asset_ids = tuple(
                connection.execute(
                    text(
                        "SELECT legacy_asset_id FROM catalog.security WHERE security_id IN :ids "
                        "AND legacy_asset_id IS NOT NULL"
                    ).bindparams(bindparam("ids", expanding=True)),
                    {"ids": security_ids},
                ).scalars()
            )
            rows = connection.execute(
                text(
                    "SELECT bundle.artifact_id,artifact.published_at,"
                    "calendar.calendar_version_id FROM data.data_bundle_version bundle "
                    "JOIN lineage.artifact artifact ON artifact.artifact_id=bundle.artifact_id "
                    "AND artifact.status='published' AND artifact.published_at<=:cutoff "
                    "JOIN data.data_bundle_member market ON market.data_bundle_version_id="
                    "bundle.data_bundle_version_id AND market.role='canonical_market' "
                    "JOIN data.data_bundle_member calendar ON calendar.data_bundle_version_id="
                    "bundle.data_bundle_version_id AND calendar.role='trading_calendar' "
                    "JOIN data.daily_bar bar ON bar.dataset_publication_id="
                    "market.dataset_publication_id "
                    "WHERE bundle.coverage_start<=:session AND bundle.coverage_end>=:session "
                    "AND bar.asset_id IN :asset_ids GROUP BY bundle.artifact_id,"
                    "artifact.published_at,"
                    "calendar.calendar_version_id HAVING count(DISTINCT bar.asset_id)=:asset_count "
                    "ORDER BY artifact.published_at DESC,bundle.artifact_id DESC LIMIT 2"
                ).bindparams(bindparam("asset_ids", expanding=True)),
                {
                    "cutoff": decision_cutoff_at,
                    "session": decision_session,
                    "asset_ids": asset_ids,
                    "asset_count": len(asset_ids),
                },
            ).mappings().all()
        if not rows:
            raise ValueError("No v0.21 Data Bundle was published by the Decision cutoff")
        return dict(rows[0])

    def _next_session(self, calendar_version_id: uuid.UUID, session: date) -> date:
        with self._engine.connect() as connection:
            next_session = connection.scalar(
                text(
                    "SELECT min(session_date) FROM catalog.calendar_session "
                    "WHERE calendar_version_id=:calendar AND session_date>:session"
                ),
                {"calendar": calendar_version_id, "session": session},
            )
        if not isinstance(next_session, date):
            raise ValueError("v0.21 Shadow replay has no following execution session")
        return next_session
