from __future__ import annotations

import json
from collections import defaultdict
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, text

from style_rotation.core.canonical import sha256_hexdigest
from style_rotation.v022.strategy_compat_runtime import (
    DefenseVariant,
    RankedAsset,
    build_cross_section_topk_decision,
    resolve_defense_budget,
)


class StrategyProductParityError(RuntimeError):
    """Raised when frozen Product or Strategy Oracle identity has drifted."""


def build_strategy_product_parity_evidence(
    engine: Engine,
    *,
    oracle_path: Path,
    strategy_registry_path: Path,
    model_parity_path: Path,
) -> dict[str, Any]:
    oracle = _read_object(oracle_path)
    registry = _read_object(strategy_registry_path)
    model_evidence = _read_object(model_parity_path)
    product = _one(registry["product_records"], "active Product")
    legacy = product["legacy_identity"]
    frozen_results = {
        row["artifact_id"]: row
        for row in oracle["research_and_product_evidence"]["frozen_cell_results"]
    }

    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            connection.exec_driver_sql(
                "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"
            )
            database_name = connection.execute(
                text("SELECT current_database()")
            ).scalar_one()
            if database_name != "style_rotation":
                raise StrategyProductParityError(
                    "Strategy/Product parity requires the frozen style_rotation Oracle"
                )
            portfolio = (
                connection.execute(
                    text(
                        "SELECT result.artifact_id::text,result.series,result.diagnostics,"
                        "artifact.content_hash,artifact.status AS artifact_status,"
                        "cell.window_key,cell.cost_bps_per_side "
                        "FROM experiment.cell_result result JOIN lineage.artifact artifact "
                        "ON artifact.artifact_id=result.artifact_id "
                        "JOIN experiment.portfolio_cell_specification cell "
                        "ON cell.artifact_id=result.cell_artifact_id "
                        "WHERE cell.compiled_strategy_version_id=:strategy "
                        "AND cell.window_key='full_common_history' "
                        "AND cell.cost_bps_per_side=5"
                    ),
                    {"strategy": legacy["compiled_strategy_version_id"]},
                )
                .mappings()
                .one()
            )
            predictive_id = str(portfolio["diagnostics"]["predictive_result_artifact_id"])
            predictive = (
                connection.execute(
                    text(
                        "SELECT result.artifact_id::text,result.series,artifact.content_hash,"
                        "artifact.status AS artifact_status "
                        "FROM experiment.cell_result result JOIN lineage.artifact artifact "
                        "ON artifact.artifact_id=result.artifact_id "
                        "WHERE result.artifact_id=:artifact"
                    ),
                    {"artifact": predictive_id},
                )
                .mappings()
                .one()
            )
        finally:
            transaction.rollback()

    _assert_frozen(portfolio, frozen_results)
    _assert_frozen(predictive, frozen_results)
    score_rows = predictive["series"]["model_scores"]
    audit_rows = predictive["series"]["model_input_audit"]
    aggregation_mismatches = sum(
        sum((Decimal(item["contribution"]) for item in row["inputs"]), Decimal(0))
        != Decimal(row["model_score"])
        for row in audit_rows
    )
    score_lookup = {
        (row["observation_date"], row["asset_key"]): Decimal(row["score"])
        for row in score_rows
    }
    audit_lookup = {
        (row["observation_date"], row["asset_key"]): Decimal(row["model_score"])
        for row in audit_rows
    }
    predictive_score_mismatches = sum(
        score_lookup.get(identity) != score for identity, score in audit_lookup.items()
    ) + len(set(score_lookup) ^ set(audit_lookup))

    by_date: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in score_rows:
        by_date[str(row["observation_date"])].append(row)
    expected_decisions = portfolio["series"]["decisions"]
    previous: set[str] = set()
    actual_decisions: list[dict[str, Any]] = []
    mismatch_count = 0
    for expected in expected_decisions:
        day = str(expected["decision_date"])
        decision = build_cross_section_topk_decision(
            tuple(
                RankedAsset(
                    str(row["asset_key"]),
                    Decimal(row["score"]),
                    previously_held=str(row["asset_key"]) in previous,
                )
                for row in by_date[day]
            ),
            variant_key="cross_section_rank_top_k_large_cap_parity",
            target_k=10,
            research_mode="exploratory",
            selection_buffer="half_k",
            sector_cap="none",
            defense_budget=resolve_defense_budget("none"),
        )
        actual = _decision_payload(day, decision)
        expected_normalized = _oracle_decision_payload(expected)
        mismatch_count += actual != expected_normalized
        actual_decisions.append(actual)
        previous = {position.asset_key for position in decision.positions}

    strategy_cases = _strategy_fixed_cases()
    defense_cases = _defense_fixed_cases()
    passed = not any(
        (
            aggregation_mismatches,
            predictive_score_mismatches,
            mismatch_count,
            any(not case["passed"] for case in strategy_cases),
            any(not case["passed"] for case in defense_cases),
        )
    )
    evidence: dict[str, Any] = {
        "evidence_type": "v022_strategy_defense_product_continuity",
        "evidence_version": "0.22.0",
        "oracle_baseline_id": registry["oracle_baseline_id"],
        "oracle_manifest_fingerprint": registry["oracle_manifest_fingerprint"],
        "strategy_product_registry_fingerprint": registry["registry_fingerprint"],
        "model_parity_evidence_fingerprint": model_evidence["evidence_fingerprint"],
        "comparison_policy": {
            "oracle": "M0-frozen published Cell Results in repeatable-read/read-only transaction",
            "aggregation": "exact Decimal sum of three frozen input contributions",
            "strategy": "exact asset, competition rank, Decimal target weight and reserve",
            "defense": "exact Decimal budget including MA200 +/-2 percent boundaries",
            "tolerance": "none",
        },
        "active_product": {
            "product_version_id": legacy["product_version_id"],
            "compiled_model_instance_id": legacy["compiled_model_instance_id"],
            "compiled_strategy_version_id": legacy["compiled_strategy_version_id"],
            "portfolio_result_artifact_id": portfolio["artifact_id"],
            "portfolio_result_content_hash": portfolio["content_hash"],
            "predictive_result_artifact_id": predictive["artifact_id"],
            "predictive_result_content_hash": predictive["content_hash"],
            "input_signal_variant_keys": product["aggregation_mapping"][
                "input_signal_variant_keys"
            ],
            "aggregation_family_key": product["aggregation_mapping"]["family_key"],
            "aggregation_parameter_preset_key": product["aggregation_mapping"][
                "parameter_preset_key"
            ],
            "strategy_variant_key": product["strategy_mapping"]["variant_key"],
            "defense_variant_key": product["defense_mapping_key"],
            "score_point_count": len(score_rows),
            "aggregation_input_audit_count": len(audit_rows),
            "aggregation_mismatch_count": aggregation_mismatches,
            "predictive_score_mismatch_count": predictive_score_mismatches,
            "decision_count": len(expected_decisions),
            "decision_mismatch_count": mismatch_count,
            "expected_decisions_fingerprint": sha256_hexdigest(
                [_oracle_decision_payload(row) for row in expected_decisions]
            ),
            "actual_decisions_fingerprint": sha256_hexdigest(actual_decisions),
            "passed": aggregation_mismatches == predictive_score_mismatches == mismatch_count == 0,
        },
        "strategy_fixed_regression_cases": strategy_cases,
        "defense_fixed_regression_cases": defense_cases,
        "summary": {
            "strategy_variant_count": 2,
            "strategy_fixed_case_count": len(strategy_cases),
            "defense_variant_count": 3,
            "defense_fixed_case_count": len(defense_cases),
            "active_product_count": 1,
            "active_product_score_point_count": len(score_rows),
            "active_product_decision_count": len(expected_decisions),
            "mismatch_count": aggregation_mismatches
            + predictive_score_mismatches
            + mismatch_count,
            "passed": passed,
        },
    }
    evidence["evidence_fingerprint"] = sha256_hexdigest(evidence)
    return evidence


def validate_strategy_product_parity_evidence(
    evidence: dict[str, Any],
    *,
    strategy_registry: dict[str, Any],
    model_parity_evidence: dict[str, Any],
) -> None:
    if evidence.get("evidence_type") != "v022_strategy_defense_product_continuity":
        raise ValueError("invalid Strategy/Product parity evidence type")
    if evidence.get("strategy_product_registry_fingerprint") != strategy_registry.get(
        "registry_fingerprint"
    ):
        raise ValueError("Strategy/Product parity Registry drift")
    if evidence.get("model_parity_evidence_fingerprint") != model_parity_evidence.get(
        "evidence_fingerprint"
    ):
        raise ValueError("Strategy/Product parity Model Evidence drift")
    product = evidence.get("active_product", {})
    if (
        not product.get("passed")
        or product.get("aggregation_mismatch_count") != 0
        or product.get("predictive_score_mismatch_count") != 0
        or product.get("decision_mismatch_count") != 0
        or product.get("expected_decisions_fingerprint")
        != product.get("actual_decisions_fingerprint")
    ):
        raise ValueError("Strategy/Product parity contains Product mismatches")
    strategy_cases = evidence.get("strategy_fixed_regression_cases")
    defense_cases = evidence.get("defense_fixed_regression_cases")
    if (
        not isinstance(strategy_cases, list)
        or {case.get("variant_key") for case in strategy_cases}
        != {"cross_section_rank_top_k_parity", "cross_section_rank_top_k_large_cap_parity"}
        or any(
            not case.get("passed")
            or case.get("expected_output_fingerprint")
            != case.get("actual_output_fingerprint")
            for case in strategy_cases
        )
    ):
        raise ValueError("Strategy/Product parity Strategy cases are incomplete")
    if (
        not isinstance(defense_cases, list)
        or {case.get("variant_key") for case in defense_cases}
        != {"none", "fixed20_defense", "ma200_tiered_defense"}
        or any(not case.get("passed") for case in defense_cases)
    ):
        raise ValueError("Strategy/Product parity Defense cases are incomplete")
    if evidence.get("summary") != {
        "strategy_variant_count": 2,
        "strategy_fixed_case_count": 2,
        "defense_variant_count": 3,
        "defense_fixed_case_count": 6,
        "active_product_count": 1,
        "active_product_score_point_count": 102916,
        "active_product_decision_count": 1042,
        "mismatch_count": 0,
        "passed": True,
    }:
        raise ValueError("Strategy/Product parity summary drift")
    payload = dict(evidence)
    fingerprint = payload.pop("evidence_fingerprint", None)
    if fingerprint != sha256_hexdigest(payload):
        raise ValueError("Strategy/Product parity evidence fingerprint drift")


def _strategy_fixed_cases() -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    frozen_outputs = {
        "cross_section_rank_top_k_parity": (
            "578799b3b30e35cebcaf1c5ab295595b542b4dd2607fc635e5feb13abb8aa8a8"
        ),
        "cross_section_rank_top_k_large_cap_parity": (
            "8bbfade655b5e0d9cee3ee4dcbd61682e2a70289fe1d1d13199dfa31a7c57bc3"
        ),
    }
    definitions = (
        (
            "cross_section_rank_top_k_parity",
            tuple(RankedAsset(f"etf_{index}", Decimal(4 - index)) for index in range(4)),
            2,
            "none",
            Decimal("0.2"),
        ),
        (
            "cross_section_rank_top_k_large_cap_parity",
            tuple(
                RankedAsset(f"stock_{index:03d}", Decimal(100 - index))
                for index in range(100)
            ),
            10,
            "half_k",
            Decimal(0),
        ),
    )
    for variant, assets, target_k, buffer, defense in definitions:
        decision = build_cross_section_topk_decision(
            assets,
            variant_key=variant,  # type: ignore[arg-type]
            target_k=target_k,
            research_mode="formal",
            selection_buffer=buffer,  # type: ignore[arg-type]
            sector_cap="none",
            defense_budget=defense,
        )
        output_fingerprint = sha256_hexdigest(decision)
        cases.append(
            {
                "variant_key": variant,
                "input_fingerprint": sha256_hexdigest(assets),
                "expected_output_fingerprint": frozen_outputs[variant],
                "actual_output_fingerprint": output_fingerprint,
                "eligible_count": decision.eligible_count,
                "position_count": len(decision.positions),
                "risk_budget": str(decision.risk_budget),
                "defense_budget": str(decision.defense_budget),
                "passed": (
                    decision.status == "accepted"
                    and output_fingerprint == frozen_outputs[variant]
                ),
            }
        )
    return cases


def _defense_fixed_cases() -> list[dict[str, Any]]:
    inputs: tuple[
        tuple[DefenseVariant, Decimal | None, Decimal | None, Decimal], ...
    ] = (
        ("none", None, None, Decimal(0)),
        ("fixed20_defense", None, None, Decimal("0.2")),
        ("ma200_tiered_defense", Decimal("103"), Decimal("100"), Decimal(0)),
        ("ma200_tiered_defense", Decimal("102"), Decimal("100"), Decimal("0.2")),
        ("ma200_tiered_defense", Decimal("98"), Decimal("100"), Decimal("0.2")),
        ("ma200_tiered_defense", Decimal("97"), Decimal("100"), Decimal("0.4")),
    )
    return [
        {
            "variant_key": variant,
            "spy_close": None if close is None else str(close),
            "spy_sma200": None if average is None else str(average),
            "expected_budget": str(expected),
            "actual_budget": str(
                resolve_defense_budget(variant, spy_close=close, spy_sma200=average)
            ),
            "passed": resolve_defense_budget(
                variant, spy_close=close, spy_sma200=average
            )
            == expected,
        }
        for variant, close, average, expected in inputs
    ]


def _decision_payload(day: str, decision: Any) -> dict[str, Any]:
    return {
        "decision_date": day,
        "eligible_count": decision.eligible_count,
        "rankable_count": decision.rankable_count,
        "coverage_ratio": Decimal(decision.coverage_ratio),
        "positions": [
            {
                "asset_key": position.asset_key,
                "rank": position.rank,
                "target_weight": Decimal(position.target_weight),
            }
            for position in decision.positions
        ],
        "defense_budget": Decimal(decision.defense_budget),
        "reserve_target_weight": Decimal(0),
    }


def _oracle_decision_payload(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "decision_date": str(row["decision_date"]),
        "eligible_count": int(row["eligible_count"]),
        "rankable_count": int(row["rankable_count"]),
        "coverage_ratio": Decimal(row["coverage_ratio"]),
        "positions": [
            {
                "asset_key": str(position["asset_key"]),
                "rank": int(position["rank"]),
                "target_weight": Decimal(position["target_weight"]),
            }
            for position in row["positions"]
        ],
        "defense_budget": Decimal(row["defense_budget"]),
        "reserve_target_weight": Decimal(row["reserve_target_weight"]),
    }


def _assert_frozen(row: Any, frozen: dict[str, dict[str, Any]]) -> None:
    identity = str(row["artifact_id"])
    oracle = frozen.get(identity)
    if (
        oracle is None
        or oracle["content_hash"] != row["content_hash"]
        or oracle["artifact_status"] != row["artifact_status"]
        or row["artifact_status"] != "published"
    ):
        raise StrategyProductParityError(f"M0-frozen Cell Result drift: {identity}")


def _read_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _one(values: list[Any], label: str) -> Any:
    if len(values) != 1:
        raise StrategyProductParityError(f"Expected one {label}, found {len(values)}")
    return values[0]
