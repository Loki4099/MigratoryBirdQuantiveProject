from __future__ import annotations

import uuid
from decimal import Decimal

from style_rotation.backtest.calculator import (
    buy_and_hold_target,
    equal_weight_targets,
    run_backtest,
)
from style_rotation.backtest.contracts import PHASE5_CONTRACTS
from style_rotation.backtest.repository import BacktestRepository
from style_rotation.backtest.types import BacktestBatchOutcome, BacktestResult
from style_rotation.domain.fingerprints import RunFingerprintInput

CANDIDATE_SYMBOLS = ("IWF", "IWD", "IWO", "IWN")
TRANSACTION_COST_SCENARIOS = (Decimal(2), Decimal(5), Decimal(10))


class BacktestBatchService:
    def __init__(self, repository: BacktestRepository) -> None:
        self._repository = repository

    def run(
        self,
        *,
        data_version_id: uuid.UUID,
        cleaning_version_id: uuid.UUID,
        factor_version_id: uuid.UUID,
        strategy_version_id: uuid.UUID,
        engine_version_id: uuid.UUID,
        system_version: str,
        variant_keys: set[str] | None = None,
    ) -> BacktestBatchOutcome:
        self._repository.publish_contracts(PHASE5_CONTRACTS)
        prices, reserve_returns, specs, warmup_start, official_end = self._repository.load_inputs(
            data_version_id,
            cleaning_version_id,
            factor_version_id,
            strategy_version_id,
        )
        if variant_keys is not None:
            specs = tuple(spec for spec in specs if spec.factor_variant_key in variant_keys)
        if not specs:
            raise ValueError("No matching formal run specifications")
        experiment_name = (
            f"v0.1-single-factor-{data_version_id}-{factor_version_id}-{strategy_version_id}"
        )
        experiment_id = self._repository.ensure_experiment(experiment_name, system_version)
        completed = 0
        reused = 0
        benchmark_cache: dict[tuple[str, Decimal], tuple[BacktestResult, BacktestResult]] = {}
        for spec in specs:
            execution_pairs = tuple(
                (target.signal_date, target.execution_date) for target in spec.targets
            )
            for cost_bps in TRANSACTION_COST_SCENARIOS:
                fingerprint_input = RunFingerprintInput(
                    data_version=str(data_version_id),
                    cleaning_version=str(cleaning_version_id),
                    factor_version=str(factor_version_id),
                    strategy_version=str(strategy_version_id),
                    engine_version=str(engine_version_id),
                    factor_variant_key=spec.factor_variant_key,
                    official_signal_start_date=spec.targets[0].signal_date,
                    official_end_date=official_end,
                    rebalance_frequency=spec.frequency,
                    strategy_template=spec.strategy_template,
                    transaction_cost_bps=cost_bps,
                    parameters={"cost_model": "single_sided_turnover"},
                )
                if self._repository.completed_run_id(fingerprint_input.fingerprint) is not None:
                    reused += 1
                    continue
                result = run_backtest(
                    prices=prices,
                    reserve_returns=reserve_returns,
                    targets=spec.targets,
                    symbols=CANDIDATE_SYMBOLS,
                    transaction_cost_bps=cost_bps,
                )
                cache_key = (spec.frequency.value, cost_bps)
                benchmarks = benchmark_cache.get(cache_key)
                if benchmarks is None:
                    equal_weight = run_backtest(
                        prices=prices,
                        reserve_returns=reserve_returns,
                        targets=equal_weight_targets(execution_pairs, CANDIDATE_SYMBOLS),
                        symbols=CANDIDATE_SYMBOLS,
                        transaction_cost_bps=cost_bps,
                    )
                    spy = run_backtest(
                        prices=prices,
                        reserve_returns=reserve_returns,
                        targets=buy_and_hold_target(
                            spec.targets[0].signal_date,
                            spec.targets[0].execution_date,
                            "SPY",
                        ),
                        symbols=("SPY",),
                        transaction_cost_bps=cost_bps,
                    )
                    benchmarks = (equal_weight, spy)
                    benchmark_cache[cache_key] = benchmarks
                self._repository.publish_run(
                    run_fields={
                        "experiment_id": experiment_id,
                        "data_version_id": data_version_id,
                        "cleaning_version_id": cleaning_version_id,
                        "factor_version_id": factor_version_id,
                        "strategy_version_id": strategy_version_id,
                        "engine_version_id": engine_version_id,
                        "run_fingerprint": fingerprint_input.fingerprint,
                        "factor_variant_key": spec.factor_variant_key,
                        "warmup_start_date": warmup_start,
                        "official_signal_start_date": spec.targets[0].signal_date,
                        "first_execution_date": spec.targets[0].execution_date,
                        "official_end_date": official_end,
                        "rebalance_frequency": spec.frequency.value,
                        "strategy_template": spec.strategy_template.value,
                        "transaction_cost_bps": cost_bps,
                        "configuration": {
                            "cost_model": "single_sided_turnover",
                            "initial_build_charged": True,
                            "terminal_liquidation": False,
                            "reserve_accrual": "prior_known_rate_calendar_days",
                            "benchmarks": ["four_etf_equal_weight", "spy_buy_hold"],
                        },
                    },
                    result=result,
                    equal_weight_benchmark=benchmarks[0],
                    spy_benchmark=benchmarks[1],
                )
                completed += 1
        self._repository.complete_experiment(experiment_id)
        return BacktestBatchOutcome(str(experiment_id), completed, reused)
