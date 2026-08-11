from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from threading import Lock
from typing import Any, cast

from sqlalchemy import Engine, bindparam, text

from style_rotation.ops.v021_execution import V021DatabaseExecutor, _defensive_allocations
from style_rotation.product.v021_monitoring import V021MonitoringCalculator
from style_rotation.strategy.v021_topk import RankedAsset, build_topk_decision


class ProductRecommendationService:
    """Calculate the latest research recommendation without extending OOS history."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._executor = V021DatabaseExecutor(engine)
        self._cache: dict[tuple[uuid.UUID, uuid.UUID, tuple[str, ...]], dict[str, Any]] = {}
        self._cache_lock = Lock()

    def latest(self, enrollment_id: uuid.UUID) -> dict[str, Any]:
        context = self._context(enrollment_id)
        bundle = self._latest_bundle(tuple(context["asset_ids"]))
        cache_key = (
            enrollment_id,
            bundle["artifact_id"],
            tuple(sorted(context["current_holdings"])),
        )
        with self._cache_lock:
            cached = self._cache.get(cache_key)
        if cached is not None:
            return cached
        decision_session = self._latest_signal_session(
            bundle["coverage_end"], context["frequency"], bundle["calendar_version_id"]
        )
        scores, model = self._executor.latest_product_scores(
            enrollment_id=enrollment_id,
            data_bundle_artifact_id=bundle["artifact_id"],
            as_of_session=decision_session,
            cached_signals_only=True,
        )
        selected = self._executor._selected_asset_records(model["normalized_selection"])
        prior_holdings = set(context["current_holdings"])
        score_lookup = {item["asset_key"]: Decimal(item["score"]) for item in scores}
        parameters = model["strategy_parameters"]
        formal_membership = bool(
            model["pit_gate_artifact_id"] and model["terminal_gate_artifact_id"]
        )
        eligibility = (
            self._executor._pit_eligibility(
                selected,
                model["decision_date"],
                uuid.UUID(model["pit_gate_artifact_id"]),
                uuid.UUID(model["terminal_gate_artifact_id"]),
            )
            if formal_membership
            else {item["asset_id"]: item["asset_key"] in score_lookup for item in selected}
        )
        sectors = (
            self._executor._sector_keys(
                tuple(item["asset_id"] for item in selected),
                model["decision_date"],
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
                str(parameters.get("defense", "none")), raw, model["decision_date"]
            ),
        )
        names = self._asset_names(tuple(item["asset_key"] for item in selected))
        positions: list[dict[str, Any]] = []
        if decision.status == "accepted":
            positions.extend(
                {
                    "asset_key": item.asset_key,
                    "symbol": names.get(item.asset_key, {}).get("symbol", item.asset_key.upper()),
                    "name": names.get(item.asset_key, {}).get("name", item.asset_key),
                    "allocation_role": "risk",
                    "model_score": float(item.model_score),
                    "rank": item.rank,
                    "target_weight": float(item.target_weight),
                    "retained_by_buffer": item.retained_by_buffer,
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
                    "symbol": names.get(key, {}).get("symbol", key.upper()),
                    "name": names.get(key, {}).get("name", key),
                    "allocation_role": "defense",
                    "model_score": None,
                    "rank": None,
                    "target_weight": float(weight),
                    "retained_by_buffer": False,
                }
                for key, weight in sorted(defense.items())
            )
            if reserve_weight:
                positions.append(
                    {
                        "asset_key": "__reserve__",
                        "symbol": "CASH",
                        "name": "Synthetic reserve",
                        "allocation_role": "reserve",
                        "model_score": None,
                        "rank": None,
                        "target_weight": float(reserve_weight),
                        "retained_by_buffer": False,
                    }
                )
        execution_session, next_signal_session = self._future_sessions(
            model["decision_date"], context["frequency"], bundle["calendar_version_id"]
        )
        payload = {
            "available": decision.status == "accepted",
            "status": decision.status,
            "reason_codes": [decision.reason_code] if decision.reason_code else [],
            "frequency": context["frequency"],
            "data_bundle_artifact_id": bundle["artifact_id"],
            "data_as_of_session": bundle["coverage_end"],
            "data_known_at": bundle["created_at"],
            "decision_session": model["decision_date"],
            "recommended_execution_session": execution_session,
            "next_expected_signal_session": next_signal_session,
            "eligible_count": decision.eligible_count,
            "rankable_count": decision.rankable_count,
            "coverage_ratio": float(decision.coverage_ratio),
            "positions": positions,
            "not_oos": True,
            "refresh_policy": "refresh_after_each_published_signal_decision",
        }
        with self._cache_lock:
            self._cache = {
                key: value for key, value in self._cache.items() if key[0] != enrollment_id
            }
            self._cache[cache_key] = payload
        return payload

    def _context(self, enrollment_id: uuid.UUID) -> dict[str, Any]:
        with self._engine.connect() as connection:
            row = (
                connection.execute(
                    text("""
                    SELECT spec.frequency, spec.normalized_selection,
                           COALESCE(snapshot.metrics -> 'holdings', '[]'::jsonb)
                             AS current_holdings
                    FROM product.product_enrollment enrollment
                    JOIN product.product_version version
                      ON version.product_version_id = enrollment.product_version_id
                    JOIN strategy.compiled_strategy_version strategy
                      ON strategy.compiled_strategy_version_id =
                         version.compiled_strategy_version_id
                    JOIN workspace.compiled_research_spec spec
                      ON spec.compiled_research_spec_id = strategy.compiled_research_spec_id
                    LEFT JOIN LATERAL (
                      SELECT metrics FROM product.monitoring_snapshot snapshot
                      WHERE snapshot.product_enrollment_id = enrollment.product_enrollment_id
                      ORDER BY snapshot.as_of_session DESC, snapshot.known_at DESC LIMIT 1
                    ) snapshot ON true
                    WHERE enrollment.product_enrollment_id = :enrollment_id
                      AND enrollment.lifecycle IN ('active','suspended')
                """),
                    {"enrollment_id": enrollment_id},
                )
                .mappings()
                .one_or_none()
            )
        if row is None:
            raise LookupError(f"Active Product Research Candidate not found: {enrollment_id}")
        selection = dict(row["normalized_selection"])
        security_ids = tuple(uuid.UUID(value) for value in selection["asset_security_ids"])
        with self._engine.connect() as connection:
            asset_ids = tuple(
                connection.execute(
                    text(
                        "SELECT legacy_asset_id FROM catalog.security "
                        "WHERE security_id IN :ids AND legacy_asset_id IS NOT NULL"
                    ).bindparams(bindparam("ids", expanding=True)),
                    {"ids": security_ids},
                ).scalars()
            )
        current_holdings = {
            str(item["asset_key"])
            for item in (row["current_holdings"] or [])
            if isinstance(item, dict) and item.get("asset_key")
        }
        return {
            "frequency": row["frequency"],
            "asset_ids": asset_ids,
            "current_holdings": current_holdings,
        }

    def _latest_bundle(self, asset_ids: tuple[uuid.UUID, ...]) -> dict[str, Any]:
        with self._engine.connect() as connection:
            row = (
                connection.execute(
                    text("""
                    SELECT bundle.data_bundle_version_id, bundle.artifact_id,
                           bundle.coverage_end, artifact.created_at,
                           calendar.calendar_version_id
                    FROM data.data_bundle_version bundle
                    JOIN lineage.artifact artifact ON artifact.artifact_id = bundle.artifact_id
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
                    ORDER BY bundle.coverage_end DESC, artifact.created_at DESC LIMIT 1
                """).bindparams(bindparam("asset_ids", expanding=True)),
                    {"asset_ids": asset_ids, "asset_count": len(asset_ids)},
                )
                .mappings()
                .one_or_none()
            )
        if row is None:
            raise LookupError("No published Data Bundle covers the Product asset selection")
        return dict(row)

    def _asset_names(self, asset_keys: tuple[str, ...]) -> dict[str, dict[str, str]]:
        if not asset_keys:
            return {}
        with self._engine.connect() as connection:
            rows = (
                connection.execute(
                    text("""
                SELECT DISTINCT ON (asset.asset_key) asset.asset_key, security.name,
                       COALESCE(profile.symbol, upper(asset.asset_key)) AS symbol
                FROM catalog.asset asset
                JOIN catalog.security security ON security.legacy_asset_id = asset.asset_id
                LEFT JOIN catalog.security_profile profile
                  ON profile.security_id = security.security_id
                LEFT JOIN catalog.asset_registry_release release
                  ON release.asset_registry_release_id = profile.asset_registry_release_id
                WHERE asset.asset_key IN :asset_keys
                ORDER BY asset.asset_key, release.version_number DESC NULLS LAST
            """).bindparams(bindparam("asset_keys", expanding=True)),
                    {"asset_keys": asset_keys},
                )
                .mappings()
                .all()
            )
        return {row["asset_key"]: {"name": row["name"], "symbol": row["symbol"]} for row in rows}

    def _future_sessions(
        self, decision: date, frequency: str, calendar_version_id: uuid.UUID
    ) -> tuple[date | None, date | None]:
        with self._engine.connect() as connection:
            sessions = cast(
                tuple[date, ...],
                tuple(
                    connection.execute(
                        text("""
                    SELECT calendar.session_date
                    FROM catalog.calendar_session calendar
                    WHERE calendar.calendar_version_id = :calendar_version_id
                      AND calendar.session_date > :decision
                    ORDER BY calendar.session_date LIMIT 100
                """),
                        {
                            "decision": decision,
                            "calendar_version_id": calendar_version_id,
                        },
                    ).scalars()
                ),
            )
        execution = sessions[0] if sessions else None
        if frequency == "daily":
            return execution, execution
        for index, session in enumerate(sessions[:-1]):
            following = sessions[index + 1]
            if frequency == "weekly" and session.isocalendar()[:2] != following.isocalendar()[:2]:
                return execution, session
            if frequency == "monthly" and (session.year, session.month) != (
                following.year,
                following.month,
            ):
                return execution, session
        return execution, None

    def _latest_signal_session(
        self, coverage_end: date, frequency: str, calendar_version_id: uuid.UUID
    ) -> date:
        with self._engine.connect() as connection:
            sessions = cast(
                tuple[date, ...],
                tuple(
                    connection.execute(
                        text("""
                    SELECT calendar.session_date
                    FROM catalog.calendar_session calendar
                    WHERE calendar.calendar_version_id = :calendar_version_id
                      AND (calendar.session_date <= :coverage_end
                       OR calendar.session_date = (
                         SELECT min(future.session_date)
                         FROM catalog.calendar_session future
                         WHERE future.calendar_version_id = :calendar_version_id
                           AND future.session_date > :coverage_end
                       ))
                    ORDER BY calendar.session_date
                """),
                        {
                            "coverage_end": coverage_end,
                            "calendar_version_id": calendar_version_id,
                        },
                    ).scalars()
                ),
            )
        if frequency == "daily":
            candidates = [session for session in sessions if session <= coverage_end]
            if candidates:
                return candidates[-1]
        for index in range(len(sessions) - 2, -1, -1):
            session = sessions[index]
            following = sessions[index + 1]
            if session > coverage_end:
                continue
            if frequency == "weekly" and session.isocalendar()[:2] != following.isocalendar()[:2]:
                return session
            if frequency == "monthly" and (session.year, session.month) != (
                following.year,
                following.month,
            ):
                return session
        raise LookupError(f"No completed {frequency} Signal Decision is available")
