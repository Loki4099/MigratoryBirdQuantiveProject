from __future__ import annotations

import statistics
import uuid
from collections import defaultdict
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import Engine, text

from style_rotation.domain.enums import WorkFailureClass
from style_rotation.experiment.contracts import AccountingReserveInterval
from style_rotation.experiment.v021_matrix import (
    ImpactPolicy,
    evaluate_capacity,
    square_root_impact_bps,
)
from style_rotation.ops.v021_execution import V021DatabaseExecutor, _defensive_allocations
from style_rotation.ops.worker import ClassifiedWorkFailure
from style_rotation.product.monitoring import MonitoringEvidence
from style_rotation.product.monitoring_service import (
    MonitoringOutput,
    MonitoringRequest,
    MonitoringWorker,
)
from style_rotation.strategy.v021_topk import (
    RankedAsset,
    build_topk_decision,
    internal_timing_defense_budget,
)

ZERO = Decimal(0)
ONE = Decimal(1)


def _reserve_factor(
    intervals: tuple[AccountingReserveInterval, ...],
    previous_date: date | None,
    current_date: date,
) -> Decimal:
    if previous_date is None:
        return ONE
    by_start: dict[date, AccountingReserveInterval] = {}
    for item in intervals:
        if item.interval_start in by_start:
            raise ClassifiedWorkFailure(
                WorkFailureClass.DATA_QUALITY,
                "Frozen reserve accrual contains duplicate interval starts",
            )
        by_start[item.interval_start] = item
    cursor = previous_date
    factor = ONE
    while cursor < current_date:
        interval = by_start.get(cursor)
        if (
            interval is None
            or interval.interval_end <= cursor
            or interval.interval_end > current_date
        ):
            break
        factor *= interval.accrual_factor
        cursor = interval.interval_end
    if cursor != current_date:
        raise ClassifiedWorkFailure(
            WorkFailureClass.DATA_QUALITY,
            "Frozen reserve accrual is missing for the OOS valuation interval",
            details={
                "interval_start": previous_date.isoformat(),
                "interval_end": current_date.isoformat(),
            },
        )
    return factor


class V021MonitoringCalculator:
    """Continue one Product candidate from frozen definition using only newer bundles."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._executor = V021DatabaseExecutor(engine)

    def calculate(self, request: MonitoringRequest) -> MonitoringOutput:
        context = self._context(request)
        terminal_gate = (
            uuid.UUID(context["terminal_gate_artifact_id"])
            if context["terminal_gate_artifact_id"]
            else None
        )
        _, raw = self._executor.market_data_for_bundle(
            request.data_bundle_artifact_id, terminal_gate
        )
        reserve = self._executor._reserve_intervals(context["data_bundle_version_id"])
        previous = context["previous"]
        prior_holdings = previous.get("holdings", [])
        previous_date = context["previous_as_of"]
        pending = (
            [] if request.held_during_suspension else previous.get("pending_target_holdings", [])
        )
        pending_date = (
            None if request.held_during_suspension else previous.get("pending_decision_date")
        )
        attempted_target = bool(
            pending
            and not request.held_during_suspension
            and pending_date
            and date.fromisoformat(pending_date) < request.as_of_session
        )
        reserve_factor = _reserve_factor(reserve, previous_date, request.as_of_session)
        # First mark the existing holdings to the execution open/close.  A pending
        # target is applied only after the complete 100M capacity check succeeds.
        overnight, intraday, holdings, pretrade = self._advance_holdings(
            raw,
            prior_holdings,
            prior_holdings,
            previous_date,
            request.as_of_session,
            False,
            reserve_factor,
        )
        prior_primary = Decimal(str(context["prior_primary_nav"] or 1))
        prior_stress = Decimal(str(context["prior_stress_nav"] or 1))
        capacity_ok = True
        primary_cost = ZERO
        stress_cost = ZERO
        cost_audit: list[dict[str, Any]] = []
        executed_target = False
        if attempted_target:
            capacity_ok, primary_cost, stress_cost, cost_audit = self._costs(
                raw,
                pretrade,
                pending,
                request.as_of_session,
                context["impact_policy"],
                prior_primary * overnight * Decimal("100000000"),
                prior_stress * overnight * Decimal("100000000"),
            )
            if capacity_ok:
                overnight, intraday, holdings, pretrade = self._advance_holdings(
                    raw,
                    prior_holdings,
                    pending,
                    previous_date,
                    request.as_of_session,
                    True,
                    reserve_factor,
                )
                executed_target = True
        primary = prior_primary * overnight * (ONE - primary_cost) * intraday
        stress = prior_stress * overnight * (ONE - stress_cost) * intraday

        decision_count = int(previous.get("decision_count", 0))
        # An execution attempt is single-session.  Rejected capacity never creates
        # fictional holdings and the stale target is not silently retried later.
        pending_holdings: list[dict[str, Any]] = [] if attempted_target else list(pending)
        pending_decision_date: str | None = None if attempted_target else pending_date
        signal_artifacts: list[str] = list(
            context["previous_health"].get("signal_dataset_artifact_ids", [])
        )
        predictive_dispersion = previous.get("predictive_dispersion")
        model_decision_date = previous.get("model_decision_date")
        new_predictive_decision = False
        if request.rebalance_due and not request.held_during_suspension:
            scores, model = self._executor.latest_product_scores(
                enrollment_id=request.product_enrollment_id,
                data_bundle_artifact_id=request.data_bundle_artifact_id,
                as_of_session=request.as_of_session,
            )
            if model["decision_date"] != request.as_of_session:
                raise ClassifiedWorkFailure(
                    WorkFailureClass.DATA_QUALITY,
                    "OOS Model score date does not equal the legal Decision session",
                    details={
                        "model_decision_date": model["decision_date"].isoformat(),
                        "required_decision_date": request.as_of_session.isoformat(),
                    },
                )
            selected = self._executor._selected_asset_records(model["normalized_selection"])
            score_lookup = {item["asset_key"]: Decimal(item["score"]) for item in scores}
            parameters = model["strategy_parameters"]
            formal_membership = bool(
                model["pit_gate_artifact_id"] and model["terminal_gate_artifact_id"]
            )
            eligibility = (
                self._executor._pit_eligibility(
                    selected,
                    request.as_of_session,
                    uuid.UUID(model["pit_gate_artifact_id"]),
                    uuid.UUID(model["terminal_gate_artifact_id"]),
                )
                if formal_membership
                else {
                    item["asset_id"]: item["asset_key"] in score_lookup
                    for item in selected
                }
            )
            sectors = (
                self._executor._sector_keys(
                    tuple(item["asset_id"] for item in selected),
                    request.as_of_session,
                    uuid.UUID(model["pit_gate_artifact_id"]),
                )
                if parameters.get("sector_cap", "none") == "pit_30_percent"
                and formal_membership
                else {}
            )
            decision = build_topk_decision(
                tuple(
                    RankedAsset(
                        asset_key=item["asset_key"],
                        model_score=score_lookup.get(item["asset_key"]),
                        eligible=eligibility.get(item["asset_id"], False),
                        sector_key=sectors.get(item["asset_id"]),
                        previously_held=any(
                            held["asset_key"] == item["asset_key"] for held in prior_holdings
                        ),
                    )
                    for item in selected
                ),
                family=model["strategy_family_key"],
                target_k=int(parameters["target_k"]),
                research_mode="formal" if formal_membership else "exploratory",
                selection_buffer=parameters.get("selection_buffer", "none"),
                sector_cap=parameters.get("sector_cap", "none"),
                defense_budget=self._defense_budget(
                    str(parameters.get("defense", "none")), raw, request.as_of_session
                ),
            )
            if decision.status != "accepted":
                raise ClassifiedWorkFailure(
                    WorkFailureClass.DATA_QUALITY,
                    "OOS Top-K Decision failed",
                    details={"reason_code": decision.reason_code},
                )
            risk = {item.asset_key: item.target_weight for item in decision.positions}
            defense, reserve_weight = _defensive_allocations(
                decision.defense_budget,
                context["defensive_basket_version"],
                available_assets={item["asset_key"] for item in raw},
            )
            risk.update(defense)
            pending_holdings = [
                {"asset_key": item.asset_key, "target_weight": str(item.target_weight)}
                for item in decision.positions
            ]
            pending_holdings.extend(
                {"asset_key": key, "target_weight": str(weight)} for key, weight in defense.items()
            )
            if reserve_weight:
                pending_holdings.append(
                    {"asset_key": "__reserve__", "target_weight": str(reserve_weight)}
                )
            pending_decision_date = request.as_of_session.isoformat()
            decision_count += 1
            signal_artifacts = model["signal_dataset_artifact_ids"]
            model_decision_date = model["decision_date"].isoformat()
            predictive_dispersion = (
                float(statistics.pstdev(float(item["score"]) for item in scores))
                if len(scores) >= 2
                else 0.0
            )
            new_predictive_decision = True

        benchmark, benchmark_invested, benchmark_capacity_ok, benchmark_audit = self._benchmark_nav(
            raw=raw,
            previous_date=previous_date,
            current_date=request.as_of_session,
            prior_nav=Decimal(str(previous.get("benchmark_nav", 1))),
            invested=bool(previous.get("benchmark_invested", False)),
            execute=executed_target,
            policy_document=context["impact_policy"],
            reserve_factor=reserve_factor,
        )
        capacity_ok = capacity_ok and benchmark_capacity_ok
        session_count = self._session_count(
            request.data_bundle_artifact_id,
            context["monitoring_start_at"],
            request.as_of_session,
        )
        performance_measure = self._performance_measure(
            request.product_enrollment_id, primary, benchmark
        )
        policy = context["monitoring_policy"]
        performance_state = _reference_state(
            performance_measure,
            policy.get("performance_watch_threshold"),
            policy.get("performance_warning_threshold"),
        )
        predictive_state = _reference_state(
            predictive_dispersion,
            policy.get("predictive_watch_threshold"),
            policy.get("predictive_warning_threshold"),
        )
        prior_performance_streak = int(previous.get("performance_degradation_streak", 0))
        performance_review_due = self._is_monthly_review_session(
            request.data_bundle_artifact_id, request.as_of_session
        )
        performance_streak = (
            _next_streak(prior_performance_streak, performance_state)
            if performance_review_due
            else prior_performance_streak
        )
        prior_predictive_streak = int(previous.get("predictive_degradation_streak", 0))
        predictive_streak = (
            _next_streak(prior_predictive_streak, predictive_state)
            if new_predictive_decision
            else prior_predictive_streak
        )
        reference_sufficient = all(
            policy.get(key) is not None
            for key in (
                "performance_watch_threshold",
                "performance_warning_threshold",
                "predictive_watch_threshold",
                "predictive_warning_threshold",
            )
        )
        metrics = {
            "cumulative_return": float(primary - ONE),
            "stress_cumulative_return": float(stress - ONE),
            "benchmark_nav": float(benchmark),
            "excess_wealth_return": float(primary / benchmark - ONE),
            "holdings": holdings,
            "pending_target_holdings": pending_holdings,
            "pending_decision_date": pending_decision_date,
            "decision_count": decision_count,
            "model_decision_date": model_decision_date,
            "predictive_dispersion": predictive_dispersion,
            "performance_reference_measure": performance_measure,
            "performance_degradation_streak": performance_streak,
            "performance_review_due": performance_review_due,
            "predictive_degradation_streak": predictive_streak,
            "benchmark_invested": benchmark_invested,
            "last_valued_session": request.as_of_session.isoformat(),
        }
        evidence = MonitoringEvidence(
            frequency=context["frequency"],
            session_count=session_count,
            decision_count=decision_count,
            data_contract_ok=True,
            capacity_ok=capacity_ok,
            performance_watch=performance_state in {"watch", "warning"}
            and performance_streak >= int(policy["watch_consecutive_reviews"]),
            performance_warning=performance_state == "warning"
            and performance_streak >= int(policy["warning_consecutive_reviews"]),
            predictive_watch=predictive_state in {"watch", "warning"}
            and predictive_streak >= int(policy["watch_consecutive_reviews"]),
            predictive_warning=predictive_state == "warning"
            and predictive_streak >= int(policy["warning_consecutive_reviews"]),
            reference_sufficient=reference_sufficient,
        )
        return MonitoringOutput(
            evidence=evidence,
            primary_nav=primary,
            stress_nav=stress,
            metrics=metrics,
            health_components={
                "signal_dataset_artifact_ids": signal_artifacts,
                "held_during_suspension": request.held_during_suspension,
                "rebalance_due": request.rebalance_due,
                "executed_target": executed_target,
                "primary_cost_fraction": str(primary_cost),
                "stress_cost_fraction": str(stress_cost),
                "cost_capacity_audit": cost_audit,
                "benchmark_cost_capacity_audit": benchmark_audit,
                "reference_sufficient": reference_sufficient,
            },
        )

    @staticmethod
    def _defense_budget(mode: str, raw: list[dict[str, Any]], as_of: date) -> Decimal:
        if mode == "none":
            return ZERO
        if mode == "fixed_20":
            return Decimal("0.2")
        if mode != "internal_timing_v1":
            raise ClassifiedWorkFailure(WorkFailureClass.CONTRACT, f"Unknown defense mode: {mode}")
        closes = [
            row["close_adj"]
            for row in raw
            if row["asset_key"].casefold() == "spy" and row["session_date"] <= as_of
        ]
        if len(closes) < 200:
            raise ClassifiedWorkFailure(
                WorkFailureClass.DATA_QUALITY,
                "internal_timing_v1 requires 200 SPY sessions",
            )
        return internal_timing_defense_budget(
            spy_close=closes[-1],
            spy_sma200=sum(closes[-200:], ZERO) / Decimal(200),
        )

    def _context(self, request: MonitoringRequest) -> dict[str, Any]:
        with self._engine.connect() as connection:
            row = (
                connection.execute(
                    text("""
                SELECT enrollment.monitoring_start_at,
                       policy.document -> 'impact_policy' AS impact_policy,
                       policy.document ->> 'defensive_basket_version'
                           AS defensive_basket_version,
                       policy.document #>> '{release_gate_artifact_ids,terminal_event}'
                           AS terminal_gate_artifact_id,
                       bundle.data_bundle_version_id,
                       monitoring.parameters AS monitoring_policy,
                       spec.frequency,
                       COALESCE(
                           (snapshot.metrics ->> 'last_valued_session')::date,
                           snapshot.as_of_session
                       ) AS previous_as_of,
                       snapshot.primary_nav AS prior_primary_nav,
                       snapshot.stress_nav AS prior_stress_nav,
                       COALESCE(snapshot.metrics, '{}'::jsonb) AS previous,
                       COALESCE(snapshot.health_components, '{}'::jsonb) AS previous_health
                FROM product.product_enrollment enrollment
                JOIN product.product_version version
                  ON version.product_version_id = enrollment.product_version_id
                JOIN experiment.execution_policy_catalog policy
                  ON policy.artifact_id = version.capital_policy_artifact_id
                JOIN product.monitoring_policy monitoring
                  ON monitoring.monitoring_policy_id = version.monitoring_policy_id
                JOIN data.data_bundle_version bundle
                  ON bundle.artifact_id = :data_bundle_artifact_id
                JOIN strategy.compiled_strategy_version strategy
                  ON strategy.compiled_strategy_version_id = version.compiled_strategy_version_id
                JOIN workspace.compiled_research_spec spec
                  ON spec.compiled_research_spec_id = strategy.compiled_research_spec_id
                LEFT JOIN LATERAL (
                    SELECT * FROM product.monitoring_snapshot snapshot
                    WHERE snapshot.product_enrollment_id = enrollment.product_enrollment_id
                    ORDER BY snapshot.as_of_session DESC LIMIT 1
                ) snapshot ON true
                WHERE enrollment.product_enrollment_id = :enrollment_id
            """),
                    {
                        "enrollment_id": request.product_enrollment_id,
                        "data_bundle_artifact_id": request.data_bundle_artifact_id,
                    },
                )
                .mappings()
                .one_or_none()
            )
        if row is None:
            raise ClassifiedWorkFailure(
                WorkFailureClass.CONTRACT, "OOS monitoring Product context is unavailable"
            )
        context = dict(row)
        # The scheduler must not claim that monitoring started merely because
        # it enqueued work.  The first legal Decision is calculated against a
        # provisional start and MonitoringSnapshotMaterializer commits the
        # real cursor atomically with the first published Snapshot.
        context["monitoring_start_at"] = context["monitoring_start_at"] or request.as_of_session
        return context

    @staticmethod
    def _advance_holdings(
        raw: list[dict[str, Any]],
        previous: list[dict[str, Any]],
        target: list[dict[str, Any]],
        previous_date: date | None,
        current_date: date,
        execute: bool,
        reserve_factor: Decimal = ONE,
    ) -> tuple[Decimal, Decimal, list[dict[str, Any]], list[dict[str, Any]]]:
        prices: dict[str, dict[date, dict[str, Decimal]]] = defaultdict(dict)
        for row in raw:
            prices[row["asset_key"]][row["session_date"]] = {
                "open": row["open_adj"],
                "close": row["close_adj"],
            }
        old = {
            item["asset_key"]: Decimal(str(item.get("close_weight", item["target_weight"])))
            for item in previous
        }
        if not old:
            old = {"__reserve__": ONE}
        overnight_notionals: dict[str, Decimal] = {}
        for asset_key, weight in old.items():
            if asset_key == "__reserve__":
                overnight_notionals[asset_key] = weight * reserve_factor
                continue
            if previous_date is None:
                overnight_notionals[asset_key] = weight
                continue
            history = prices[asset_key]
            if previous_date not in history or current_date not in history:
                raise ClassifiedWorkFailure(
                    WorkFailureClass.DATA_QUALITY, "Held asset price history is interrupted"
                )
            overnight_notionals[asset_key] = (
                weight * history[current_date]["open"] / history[previous_date]["close"]
            )
        overnight = sum(overnight_notionals.values(), ZERO)
        pretrade = {key: value / overnight for key, value in overnight_notionals.items()}
        posttrade = (
            {item["asset_key"]: Decimal(str(item["target_weight"])) for item in target}
            if execute
            else pretrade
        )
        close_notionals: dict[str, Decimal] = {}
        for asset_key, weight in posttrade.items():
            if asset_key == "__reserve__":
                close_notionals[asset_key] = weight
            else:
                history = prices[asset_key]
                if current_date not in history:
                    raise ClassifiedWorkFailure(
                        WorkFailureClass.DATA_QUALITY, "Held asset close is interrupted"
                    )
                close_notionals[asset_key] = (
                    weight * history[current_date]["close"] / history[current_date]["open"]
                )
        intraday = sum(close_notionals.values(), ZERO)
        close = [
            {
                "asset_key": key,
                "target_weight": str(posttrade[key]),
                "close_weight": str(value / intraday),
            }
            for key, value in sorted(close_notionals.items())
        ]
        pretrade_records = [
            {"asset_key": key, "target_weight": str(value)}
            for key, value in sorted(pretrade.items())
        ]
        return overnight, intraday, close, pretrade_records

    @staticmethod
    def _costs(
        raw: list[dict[str, Any]],
        previous: list[dict[str, Any]],
        target: list[dict[str, Any]],
        as_of: date,
        policy_document: dict[str, Any],
        primary_pretrade_currency_nav: Decimal,
        stress_pretrade_currency_nav: Decimal,
    ) -> tuple[bool, Decimal, Decimal, list[dict[str, Any]]]:
        old = {item["asset_key"]: Decimal(str(item["target_weight"])) for item in previous}
        new = {item["asset_key"]: Decimal(str(item["target_weight"])) for item in target}
        history: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in raw:
            history[row["asset_key"]].append(row)
        impact_enabled = bool(policy_document.get("enabled", True))
        policy = ImpactPolicy.model_validate(
            {key: value for key, value in policy_document.items() if key != "enabled"}
        )
        base_turnover = ZERO
        primary_impact = ZERO
        stress_impact = ZERO
        audit: list[dict[str, Any]] = []
        for asset_key in set(old).union(new).difference({"__reserve__"}):
            change = abs(new.get(asset_key, ZERO) - old.get(asset_key, ZERO))
            if not change:
                continue
            base_turnover += change
            if not impact_enabled:
                audit.append(
                    {
                        "asset_key": asset_key,
                        "execution_date": as_of.isoformat(),
                        "absolute_weight_change": str(change),
                        "impact_status": "uncalibrated_linear_bps_only",
                    }
                )
                continue
            window = [row for row in history[asset_key] if row["session_date"] < as_of][-20:]
            if len(window) < 20:
                return False, ZERO, ZERO, audit
            dollar = [row["close_raw"] * Decimal(row["volume_raw"]) for row in window]
            adv20 = Decimal(statistics.median(dollar))
            primary_capacity = evaluate_capacity(
                order_notional=change * primary_pretrade_currency_nav,
                trailing_median_dollar_volume_20=adv20,
            )
            stress_capacity = evaluate_capacity(
                order_notional=change * stress_pretrade_currency_nav,
                trailing_median_dollar_volume_20=statistics.median(dollar),
            )
            if (
                primary_capacity.status != "accepted"
                or primary_capacity.participation_rate is None
                or stress_capacity.status != "accepted"
                or stress_capacity.participation_rate is None
            ):
                return False, ZERO, ZERO, audit
            returns = [
                window[index]["close_adj"] / window[index - 1]["close_adj"] - ONE
                for index in range(1, len(window))
            ]
            volatility = Decimal(str(statistics.stdev(float(value) for value in returns)))
            primary_bps = square_root_impact_bps(
                participation_rate=primary_capacity.participation_rate,
                daily_volatility=volatility,
                policy=policy,
            )
            stress_bps = min(
                square_root_impact_bps(
                    participation_rate=stress_capacity.participation_rate,
                    daily_volatility=volatility,
                    policy=policy,
                )
                * Decimal("1.5"),
                policy.maximum_bps,
            )
            primary_impact += change * primary_bps / Decimal(10000)
            stress_impact += change * stress_bps / Decimal(10000)
            execution_row = next(row for row in history[asset_key] if row["session_date"] == as_of)
            audit.append(
                {
                    "asset_key": asset_key,
                    "execution_date": as_of.isoformat(),
                    "raw_open": str(execution_row["open_raw"]),
                    "absolute_weight_change": str(change),
                    "primary_order_notional": str(change * primary_pretrade_currency_nav),
                    "stress_order_notional": str(change * stress_pretrade_currency_nav),
                    "adv20": str(adv20),
                    "primary_participation_rate": str(primary_capacity.participation_rate),
                    "stress_participation_rate": str(stress_capacity.participation_rate),
                    "primary_impact_bps": str(primary_bps),
                    "stress_impact_bps": str(stress_bps),
                }
            )
        primary = base_turnover * Decimal(5) / Decimal(10000) + primary_impact
        stress = base_turnover * Decimal(10) / Decimal(10000) + stress_impact
        return True, primary, stress, audit

    def _benchmark_nav(
        self,
        *,
        raw: list[dict[str, Any]],
        previous_date: date | None,
        current_date: date,
        prior_nav: Decimal,
        invested: bool,
        execute: bool,
        policy_document: dict[str, Any],
        reserve_factor: Decimal,
    ) -> tuple[Decimal, bool, bool, list[dict[str, Any]]]:
        spy = {row["session_date"]: row for row in raw if row["asset_key"].casefold() == "spy"}
        if current_date not in spy:
            raise ClassifiedWorkFailure(
                WorkFailureClass.DATA_QUALITY, "OOS SPY benchmark is interrupted"
            )
        reserve_nav = prior_nav * reserve_factor
        if not invested and not execute:
            return reserve_nav, False, True, []
        if not invested:
            spy_key = str(spy[current_date]["asset_key"])
            capacity, cost, _stress, audit = self._costs(
                raw,
                [{"asset_key": "__reserve__", "target_weight": "1"}],
                [{"asset_key": spy_key, "target_weight": "1"}],
                current_date,
                policy_document,
                reserve_nav * Decimal("100000000"),
                reserve_nav * Decimal("100000000"),
            )
            if not capacity:
                return reserve_nav, False, False, audit
            value = (
                reserve_nav
                * (ONE - cost)
                * spy[current_date]["close_adj"]
                / spy[current_date]["open_adj"]
            )
            return value, True, True, audit
        if previous_date not in spy:
            raise ClassifiedWorkFailure(
                WorkFailureClass.DATA_QUALITY, "OOS SPY benchmark is interrupted"
            )
        value = prior_nav * spy[current_date]["close_adj"] / spy[previous_date]["close_adj"]
        return value, True, True, []

    def _performance_measure(
        self, enrollment_id: uuid.UUID, primary: Decimal, benchmark: Decimal
    ) -> float | None:
        with self._engine.connect() as connection:
            anchor = (
                connection.execute(
                    text(
                        """
                    SELECT primary_nav, metrics ->> 'benchmark_nav' AS benchmark_nav
                    FROM product.monitoring_snapshot
                    WHERE product_enrollment_id = :enrollment_id
                    ORDER BY as_of_session DESC OFFSET 124 LIMIT 1
                    """
                    ),
                    {"enrollment_id": enrollment_id},
                )
                .mappings()
                .one_or_none()
            )
        if anchor is None or anchor["benchmark_nav"] is None:
            return None
        return float(
            (primary / Decimal(anchor["primary_nav"]))
            / (benchmark / Decimal(anchor["benchmark_nav"]))
            - ONE
        )

    def _session_count(self, bundle_id: uuid.UUID, start: Any, end: date) -> int:
        with self._engine.connect() as connection:
            return int(
                connection.execute(
                    text("""
                SELECT count(DISTINCT calendar.session_date) FROM catalog.calendar_session calendar
                JOIN data.data_bundle_member member
                  ON member.calendar_version_id = calendar.calendar_version_id
                JOIN data.data_bundle_version bundle
                  ON bundle.data_bundle_version_id = member.data_bundle_version_id
                WHERE bundle.artifact_id = :bundle_id
                  AND calendar.session_date BETWEEN CAST(:start AS date) AND :end
            """),
                    {"bundle_id": bundle_id, "start": start, "end": end},
                ).scalar_one()
            )

    def _is_monthly_review_session(self, bundle_id: uuid.UUID, current: date) -> bool:
        with self._engine.connect() as connection:
            following = connection.execute(
                text("""
                    SELECT min(calendar.session_date)
                    FROM catalog.calendar_session calendar
                    JOIN data.data_bundle_member member
                      ON member.calendar_version_id = calendar.calendar_version_id
                    JOIN data.data_bundle_version bundle
                      ON bundle.data_bundle_version_id = member.data_bundle_version_id
                    WHERE bundle.artifact_id = :bundle_id
                      AND calendar.session_date > :current
                """),
                {"bundle_id": bundle_id, "current": current},
            ).scalar_one_or_none()
        return following is not None and (following.year, following.month) != (
            current.year,
            current.month,
        )


def build_v021_monitoring_worker(engine: Engine, *, worker_id: str) -> MonitoringWorker:
    calculator = V021MonitoringCalculator(engine)
    return MonitoringWorker(engine, worker_id=worker_id, calculator=calculator.calculate)


def _reference_state(
    value: float | None, watch_threshold: float | None, warning_threshold: float | None
) -> str:
    if value is None or watch_threshold is None or warning_threshold is None:
        return "insufficient"
    if value <= warning_threshold:
        return "warning"
    if value <= watch_threshold:
        return "watch"
    return "normal"


def _next_streak(previous: int, state: str) -> int:
    return previous + 1 if state in {"watch", "warning"} else 0
