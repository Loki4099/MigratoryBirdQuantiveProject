from __future__ import annotations

import argparse
import json
import math
import sys
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

ETF_SAMPLE_SYMBOLS = ("IWF", "IWD", "IWO", "IWN")
STOCK_CATEGORY = "stocks"


@dataclass(frozen=True)
class PipelineCase:
    key: str
    name: str
    universe: str
    factor_variant_keys: tuple[str, ...]
    signal_version_keys: tuple[str, ...]
    model_preset_keys: tuple[str, ...]
    model_target_keys: tuple[str, ...]
    strategy_preset_keys: tuple[str, ...]


CASES = (
    PipelineCase(
        key="a_etf_sample_momentum",
        name="4 ETF sample / momentum / K2",
        universe="etf_sample",
        factor_variant_keys=("total_return__w120",),
        signal_version_keys=("return_continuation__total_return__w120",),
        model_preset_keys=("single_signal__identity_v1",),
        model_target_keys=("cross_sectional_relative_return__h5",),
        strategy_preset_keys=("multi_etf_top_k__k2__none__none__none",),
    ),
    PipelineCase(
        key="b_stock_dual_momentum",
        name="112 stocks / dual momentum / K10",
        universe="stocks",
        factor_variant_keys=("total_return__w20", "total_return__w60"),
        signal_version_keys=(
            "return_continuation__total_return__w20",
            "return_continuation__total_return__w60",
        ),
        model_preset_keys=("linear_weighted__signal_equal_v1",),
        model_target_keys=("cross_sectional_relative_return__h5",),
        strategy_preset_keys=("us_large_cap_top_k__k10__none__half_k__none",),
    ),
    PipelineCase(
        key="c_stock_tail_dimensions",
        name="112 stocks / skew and kurtosis / K20 defensive",
        universe="stocks",
        factor_variant_keys=("return_skewness__w60", "return_excess_kurtosis__w120"),
        signal_version_keys=(
            "low_skew_premium__return_skewness__w60",
            "high_kurtosis_tail_regime__return_excess_kurtosis__w120",
        ),
        model_preset_keys=("linear_weighted__dimension_equal_v1",),
        model_target_keys=("cross_sectional_relative_return__h21",),
        strategy_preset_keys=("us_large_cap_top_k__k20__fixed_20__half_k__none",),
    ),
    PipelineCase(
        key="d_etf_low_volatility",
        name="4 ETF sample / low volatility / K1 defensive",
        universe="etf_sample",
        factor_variant_keys=("realized_volatility__w20",),
        signal_version_keys=("low_volatility__realized_volatility__w20",),
        model_preset_keys=("single_signal__identity_v1",),
        model_target_keys=("cross_sectional_relative_return__h5",),
        strategy_preset_keys=("multi_etf_top_k__k1__fixed_20__none__none",),
    ),
)


class PipelineValidationError(RuntimeError):
    pass


class Api:
    def __init__(self, base_url: str, *, request_timeout: float) -> None:
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            timeout=request_timeout,
            follow_redirects=True,
        )

    def close(self) -> None:
        self._client.close()

    def get(self, path: str, *, params: dict[str, Any] | None = None) -> dict[str, Any]:
        response = self._client.get(path, params=params)
        response.raise_for_status()
        return response.json()

    def put(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        response = self._client.put(path, json=payload)
        response.raise_for_status()
        return response.json()

    def post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        response = self._client.post(path, json=payload)
        response.raise_for_status()
        return response.json()


def _catalog(api: Api) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    first = api.get("/api/v2/catalog/assets", params={"limit": 200, "offset": 0})
    items = list(first["items"])
    total = int(first["total"])
    offset = len(items)
    while offset < total:
        page = api.get("/api/v2/catalog/assets", params={"limit": 200, "offset": offset})
        page_items = list(page["items"])
        if not page_items:
            raise PipelineValidationError("Asset catalog pagination stopped before total")
        items.extend(page_items)
        offset += len(page_items)
    return items, list(first["asset_sets"])


def _security_ids(case: PipelineCase, assets: list[dict[str, Any]]) -> list[str]:
    selectable = [item for item in assets if item["selectable"]]
    if case.universe == "etf_sample":
        by_symbol = {str(item["symbol"]): item for item in selectable}
        missing = sorted(set(ETF_SAMPLE_SYMBOLS).difference(by_symbol))
        if missing:
            raise PipelineValidationError(f"ETF sample is missing selectable assets: {missing}")
        return [str(by_symbol[symbol]["security_id"]) for symbol in ETF_SAMPLE_SYMBOLS]
    if case.universe == "stocks":
        stocks = [item for item in selectable if item["category_key"] == STOCK_CATEGORY]
        if len(stocks) < 50:
            raise PipelineValidationError(
                f"Exploratory stock strategy requires at least 50 assets; found {len(stocks)}"
            )
        return [str(item["security_id"]) for item in stocks]
    raise PipelineValidationError(f"Unsupported test universe: {case.universe}")


def _selection(case: PipelineCase, security_ids: list[str]) -> dict[str, Any]:
    return {
        "frequency": "weekly",
        "asset_security_ids": security_ids,
        "asset_data_inputs": {
            security_id: ["canonical_market_bars"] for security_id in security_ids
        },
        "factor_variant_keys": list(case.factor_variant_keys),
        "signal_version_keys": list(case.signal_version_keys),
        "model_preset_keys": list(case.model_preset_keys),
        "model_target_keys": list(case.model_target_keys),
        "strategy_preset_keys": list(case.strategy_preset_keys),
    }


def _save_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
    )
    temporary.replace(path)


def _wait_for_suite(
    api: Api,
    suite_id: str,
    *,
    timeout_seconds: float,
    poll_seconds: float,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    deadline = time.monotonic() + timeout_seconds
    history: list[dict[str, Any]] = []
    previous: tuple[int, tuple[tuple[str, int], ...]] | None = None
    while True:
        status = api.get(f"/api/v2/workspace/suites/{suite_id}")
        marker = (
            int(status["terminal"]),
            tuple(sorted((str(key), int(value)) for key, value in status["status_counts"].items())),
        )
        if marker != previous:
            history.append(
                {
                    "observed_at": datetime.now(UTC).isoformat(),
                    "terminal": status["terminal"],
                    "total": status["total"],
                    "complete": status["complete"],
                    "status_counts": status["status_counts"],
                }
            )
            previous = marker
            print(
                f"    suite progress {status['terminal']}/{status['total']} "
                f"{status['status_counts']}",
                flush=True,
            )
        if status["complete"]:
            return status, history
        if time.monotonic() >= deadline:
            raise PipelineValidationError(
                f"Suite {suite_id} did not finish within {timeout_seconds:.0f}s"
            )
        time.sleep(poll_seconds)


def _suite_specifications(api: Api, suite_artifact_id: str) -> list[dict[str, Any]]:
    offset = 0
    matches: list[dict[str, Any]] = []
    while True:
        response = api.get(
            "/api/v2/experiments/overview",
            params={"limit": 200, "offset": offset},
        )
        rows = list(response["specifications"])
        matches.extend(row for row in rows if row["suite_artifact_id"] == suite_artifact_id)
        offset += len(rows)
        if not rows or offset >= int(response["filtered_specification_count"]):
            return matches


def _defined_metric(detail: dict[str, Any], role: str, key: str) -> float | None:
    for metric in detail.get("metrics", []):
        if metric["series_role"] == role and metric["metric_key"] == key:
            if metric["value_status"] != "defined" or metric["value"] is None:
                return None
            value = float(metric["value"])
            return value if math.isfinite(value) else None
    return None


def _summarize_result(detail: dict[str, Any]) -> dict[str, Any]:
    specification = detail["specification"]
    is_predictive = specification["template_key"] == "predictive_diagnostic"
    result = {
        "result_artifact_id": detail["result_artifact_id"],
        "cell_key": specification["cell_key"],
        "template_key": specification["template_key"],
        "variant_key": specification["variant_key"],
        "cost_bps_per_side": specification["cost_bps_per_side"],
        "status": specification["status"],
        "availability_status": specification["availability_status"],
        "quality_status": specification["quality_status"],
        "attempt_number": specification["attempt_number"],
        "error_summary": specification["error_summary"],
        "run_status": detail["run_status"],
        "resolved_start": detail["resolved_start"],
        "resolved_end": detail["resolved_end"],
        "observation_count": detail["observation_count"],
        "quality_check_count": len(detail.get("quality_checks", [])),
        "artifact_count": len(detail.get("artifacts", [])),
        "nav_observation_count": len(detail.get("nav_series", [])),
        "promotion_eligible": detail["promotion_eligible"],
        "promotion_reason_codes": detail["promotion_reason_codes"],
    }
    if not is_predictive:
        result["core_performance"] = {
            "strategy_cagr": _defined_metric(detail, "strategy", "cagr"),
            "strategy_sharpe": _defined_metric(detail, "strategy", "sharpe_ratio"),
            "maximum_drawdown": _defined_metric(detail, "strategy", "maximum_drawdown"),
            "benchmark_cagr": _defined_metric(detail, "benchmark", "cagr"),
        }
    return result


def _assert_results(
    submission: dict[str, Any],
    suite_status: dict[str, Any],
    specifications: list[dict[str, Any]],
    details: list[dict[str, Any]],
) -> None:
    expected = int(submission["predictive_cell_count"]) + int(submission["portfolio_cell_count"])
    if int(suite_status["total"]) != expected:
        raise PipelineValidationError(
            f"Suite reported {suite_status['total']} cells but submission declared {expected}"
        )
    if len(specifications) != expected or len(details) != expected:
        raise PipelineValidationError(
            f"Expected {expected} published results; overview={len(specifications)}, "
            f"details={len(details)}"
        )
    failures = [
        row
        for row in specifications
        if row["status"] != "accepted" or row["availability_status"] != "accepted"
    ]
    if failures:
        messages = [f"{row['cell_key']}: {row['error_summary']}" for row in failures]
        raise PipelineValidationError("Suite contains failed cells: " + " | ".join(messages))
    for detail in details:
        specification = detail["specification"]
        if detail["run_status"] != "completed":
            raise PipelineValidationError(
                f"Result {detail['result_artifact_id']} did not complete"
            )
        if specification["template_key"] == "predictive_diagnostic":
            target_periods = specification.get("core_metrics", {}).get(
                "predictive.target_period_count"
            )
            if target_periods is None or float(target_periods) < 1:
                raise PipelineValidationError(
                    f"Predictive result {detail['result_artifact_id']} has no target periods"
                )
            if not detail.get("metrics") or not detail.get("quality_checks"):
                raise PipelineValidationError(
                    f"Predictive result {detail['result_artifact_id']} has no diagnostics"
                )
        else:
            if int(detail["observation_count"]) < 1:
                raise PipelineValidationError(
                    f"Portfolio result {detail['result_artifact_id']} has no observations"
                )
            if len(detail.get("nav_series", [])) < 2:
                raise PipelineValidationError(
                    f"Portfolio result {detail['result_artifact_id']} has no usable NAV series"
                )
            if _defined_metric(detail, "strategy", "cagr") is None:
                raise PipelineValidationError(
                    f"Portfolio result {detail['result_artifact_id']} has no defined CAGR"
                )


def _finish_submitted_audit(
    api: Api,
    audit: dict[str, Any],
    output_path: Path,
    *,
    timeout_seconds: float,
    poll_seconds: float,
) -> dict[str, Any]:
    submission = audit["submission"]
    suite_status, history = _wait_for_suite(
        api,
        submission["research_suite_id"],
        timeout_seconds=timeout_seconds,
        poll_seconds=poll_seconds,
    )
    audit["suite_status_history"] = history
    audit["suite_status"] = suite_status
    specifications = _suite_specifications(api, submission["suite_artifact_id"])
    details = [
        api.get(f"/api/v2/experiments/results/{row['result_artifact_id']}")
        for row in specifications
    ]
    audit["results"] = [_summarize_result(detail) for detail in details]
    portfolio_details = [
        detail
        for detail in details
        if detail["specification"]["template_key"] != "predictive_diagnostic"
    ]
    qualifications = [
        api.get(f"/api/v2/experiments/results/{detail['result_artifact_id']}/qualification")
        for detail in portfolio_details
    ]
    audit["qualifications"] = qualifications
    _save_json(output_path, audit)
    try:
        _assert_results(submission, suite_status, specifications, details)
        ineligible = [item for item in qualifications if not item["eligible"]]
        if ineligible:
            raise PipelineValidationError(
                "Accepted portfolio result is not promotable: "
                f"{[item['reason_codes'] for item in ineligible]}"
            )
    except Exception as error:
        audit["status"] = "failed"
        audit["error"] = str(error)
        audit["completed_at"] = datetime.now(UTC).isoformat()
        _save_json(output_path, audit)
        raise
    audit["status"] = "passed"
    audit["completed_at"] = datetime.now(UTC).isoformat()
    _save_json(output_path, audit)
    print(
        f"    PASS: {len(details)} accepted cells; "
        f"{len(qualifications)} portfolio cells eligible for manual promotion",
        flush=True,
    )
    return audit


def _validate_case(
    api: Api,
    case: PipelineCase,
    assets: list[dict[str, Any]],
    *,
    run_id: str,
    output_path: Path,
    compile_only: bool,
    timeout_seconds: float,
    poll_seconds: float,
) -> dict[str, Any]:
    print(f"[{case.key}] {case.name}", flush=True)
    security_ids = _security_ids(case, assets)
    selection = _selection(case, security_ids)
    preview = api.post("/api/v2/workspace/compile-preview", selection)
    audit: dict[str, Any] = {
        "case": {**case.__dict__, "asset_count": len(security_ids)},
        "selection": selection,
        "compile_preview": preview,
        "started_at": datetime.now(UTC).isoformat(),
    }
    _save_json(output_path, audit)
    if preview["blockers"]:
        raise PipelineValidationError(f"{case.key} compile blockers: {preview['blockers']}")
    if int(preview["usable_asset_count"]) != len(security_ids):
        raise PipelineValidationError(
            f"{case.key} selected {len(security_ids)} assets but only "
            f"{preview['usable_asset_count']} are usable"
        )
    print(
        f"    compile accepted: {len(security_ids)} assets, "
        f"{len(preview['compiled']['model_instances'])} model instance(s), "
        f"{len(preview['compiled']['strategy_branches'])} strategy branch(es)",
        flush=True,
    )
    if compile_only:
        audit["status"] = "compile_only_passed"
        audit["completed_at"] = datetime.now(UTC).isoformat()
        _save_json(output_path, audit)
        return audit

    draft_key = f"pipeline-validation-{run_id}-{case.key}"
    draft = api.put(
        f"/api/v2/workspace/drafts/pipeline-validation/{draft_key}",
        {
            "idempotency_key": str(uuid.uuid4()),
            "researcher_id": "pipeline-validation",
            "draft_key": draft_key,
            "name": f"Pipeline validation {run_id}: {case.name}",
            "expected_revision": None,
            "selection": selection,
        },
    )
    audit["draft"] = draft
    submission = api.post(
        "/api/v2/workspace/suites",
        {
            "idempotency_key": str(uuid.uuid4()),
            "researcher_id": "pipeline-validation",
            "draft_key": draft_key,
            "expected_revision": draft["revision"],
            "suite_mode": "exploratory",
        },
    )
    audit["submission"] = submission
    _save_json(output_path, audit)
    return _finish_submitted_audit(
        api,
        audit,
        output_path,
        timeout_seconds=timeout_seconds,
        poll_seconds=poll_seconds,
    )


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run repeatable v0.21 normal-user Pipeline acceptance checks."
    )
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument(
        "--cases",
        default=",".join(case.key for case in CASES),
        help="Comma-separated case keys",
    )
    parser.add_argument("--compile-only", action="store_true")
    parser.add_argument(
        "--resume-audit",
        type=Path,
        help="Resume polling and validation for an already submitted case audit JSON",
    )
    parser.add_argument("--timeout-seconds", type=float, default=1800.0)
    parser.add_argument("--poll-seconds", type=float, default=2.0)
    parser.add_argument("--request-timeout", type=float, default=60.0)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(".codex_work") / "pipeline-validation",
    )
    return parser.parse_args()


def main() -> int:
    arguments = _arguments()
    if arguments.timeout_seconds <= 0 or arguments.poll_seconds <= 0:
        raise SystemExit("timeout and poll seconds must be positive")
    indexed = {case.key: case for case in CASES}
    requested = [value.strip() for value in arguments.cases.split(",") if value.strip()]
    unknown = sorted(set(requested).difference(indexed))
    if unknown:
        raise SystemExit(f"Unknown case keys: {unknown}")
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    run_directory = arguments.output_dir / run_id
    api = Api(arguments.base_url, request_timeout=arguments.request_timeout)
    if arguments.resume_audit is not None:
        try:
            audit = json.loads(arguments.resume_audit.read_text(encoding="utf-8"))
            _finish_submitted_audit(
                api,
                audit,
                arguments.resume_audit,
                timeout_seconds=arguments.timeout_seconds,
                poll_seconds=arguments.poll_seconds,
            )
            return 0
        finally:
            api.close()
    summary: dict[str, Any] = {
        "run_id": run_id,
        "base_url": arguments.base_url,
        "compile_only": arguments.compile_only,
        "started_at": datetime.now(UTC).isoformat(),
        "cases": [],
    }
    exit_code = 0
    try:
        health = api.get("/api/v2/health")
        assets, asset_sets = _catalog(api)
        summary["health"] = health
        summary["asset_catalog"] = {
            "item_count": len(assets),
            "sets": asset_sets,
        }
        for key in requested:
            path = run_directory / f"{key}.json"
            try:
                audit = _validate_case(
                    api,
                    indexed[key],
                    assets,
                    run_id=run_id,
                    output_path=path,
                    compile_only=arguments.compile_only,
                    timeout_seconds=arguments.timeout_seconds,
                    poll_seconds=arguments.poll_seconds,
                )
                summary["cases"].append(
                    {"key": key, "status": audit["status"], "audit_file": str(path)}
                )
            except Exception as error:
                exit_code = 1
                summary["cases"].append(
                    {"key": key, "status": "failed", "error": str(error), "audit_file": str(path)}
                )
                print(f"    FAIL: {error}", file=sys.stderr, flush=True)
                break
    finally:
        api.close()
        summary["completed_at"] = datetime.now(UTC).isoformat()
        summary["status"] = "passed" if exit_code == 0 else "failed"
        _save_json(run_directory / "summary.json", summary)
        print(f"Audit: {run_directory / 'summary.json'}", flush=True)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
