from __future__ import annotations

import uuid
from contextlib import nullcontext
from dataclasses import replace
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import Any, cast

import pytest
from sqlalchemy import Engine

from style_rotation.experiment.contracts import (
    AccountingMarketBar,
    AccountingReserveInterval,
)
from style_rotation.v022.aggregation_work_runtime import SignalManifestPoint
from style_rotation.v022.defense_runtime import (
    DefenseAllocationMember,
    DefensePriceObservation,
)
from style_rotation.v022.runtime_contract import V022RuntimeDataError
from style_rotation.v022.runtime_output_payloads import PortfolioExecutionIdentity
from style_rotation.v022.suite_typed_work_runtime import (
    DefenseTimingWindow,
    PortfolioMarketInputRequest,
    PortfolioMarketInputs,
    RepresentativePortfolioMarketInputLoader,
    build_defense_payload,
    build_fixed20_defense_payload,
    build_merged_target_payload,
    build_portfolio_cell_payload,
    build_strategy_target_payload,
    decode_defense_decisions,
    decode_merged_targets,
    decode_runtime_payload_parquet,
    decode_strategy_targets,
    encode_runtime_payload_parquet,
    load_ma200_defense_timing_windows,
)

D = Decimal
ASSETS = tuple(uuid.UUID(f"00000000-0000-0000-0000-{ordinal:012d}") for ordinal in range(1, 5))
SPY = uuid.UUID("00000000-0000-0000-0000-000000000099")
BRANCH = uuid.UUID("00000000-0000-0000-0000-000000000101")
SNAPSHOT = uuid.UUID("00000000-0000-0000-0000-000000000102")
DEFENSE = uuid.UUID("00000000-0000-0000-0000-000000000103")
TIMING = uuid.UUID("00000000-0000-0000-0000-000000000104")
ALLOCATION = uuid.UUID("00000000-0000-0000-0000-000000000105")
CALENDAR = uuid.UUID("00000000-0000-0000-0000-000000000106")
SESSIONS = (
    date(2025, 1, 2),
    date(2025, 1, 3),
    date(2025, 1, 6),
    date(2025, 1, 7),
)


class _MemoryPortfolioLoader:
    def load(self, request: PortfolioMarketInputRequest) -> PortfolioMarketInputs:
        identities = tuple((asset, f"asset_{ordinal}") for ordinal, asset in enumerate(ASSETS))
        identities += ((SPY, "spy"),)
        prices = {
            SESSIONS[1]: (
                ("100", "105"),
                ("100", "100"),
                ("100", "99"),
                ("100", "98"),
                ("100", "101"),
            ),
            SESSIONS[2]: (
                ("105", "106"),
                ("100", "101"),
                ("99", "100"),
                ("98", "99"),
                ("101", "102"),
            ),
            SESSIONS[3]: (
                ("106", "107"),
                ("101", "102"),
                ("100", "101"),
                ("99", "100"),
                ("102", "103"),
            ),
        }
        bars = tuple(
            AccountingMarketBar(asset_id, asset_key, session, D(open_), D(close))
            for session, session_prices in prices.items()
            for (asset_id, asset_key), (open_, close) in zip(
                identities, session_prices, strict=True
            )
        )
        reserve = (
            AccountingReserveInterval(
                SESSIONS[1],
                SESSIONS[2],
                D("1.001"),
                SESSIONS[0],
                SESSIONS[0],
                "normal",
            ),
            AccountingReserveInterval(
                SESSIONS[2],
                SESSIONS[3],
                D("1.001"),
                SESSIONS[1],
                SESSIONS[1],
                "normal",
            ),
        )
        known = tuple(
            (session, datetime.combine(session, time(23), tzinfo=UTC)) for session in SESSIONS[1:]
        )
        return PortfolioMarketInputs(
            bars,
            reserve,
            SESSIONS,
            datetime(2025, 1, 8, tzinfo=UTC),
            known,
        )


class _Rows:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows

    def mappings(self) -> _Rows:
        return self

    def one_or_none(self) -> dict[str, Any] | None:
        return self.rows[0] if self.rows else None

    def all(self) -> list[dict[str, Any]]:
        return self.rows


class _RepresentativeConnection:
    def __init__(self) -> None:
        self.execute_count = 0
        self.queries: list[str] = []
        self.legacy = tuple(
            uuid.UUID(f"00000000-0000-0000-0000-{ordinal:012d}") for ordinal in range(201, 205)
        )

    def execute(self, statement: object, _parameters: object = None) -> _Rows:
        self.execute_count += 1
        query = str(statement)
        self.queries.append(query)
        if "v022_compiled_defense_execution_data_input" in query:
            return _Rows(
                [{
                    "dataset_publication_id": uuid.UUID(int=501),
                    "calendar_version_id": CALENDAR,
                    "legacy_asset_id": SPY,
                }]
            )
        if "LEFT JOIN data.daily_bar" in query and "catalog.calendar_session" in query:
            start = date(2024, 1, 1)
            return _Rows(
                [
                    {
                        "session_date": start + timedelta(days=index),
                        "close_at_utc": datetime.combine(
                            start + timedelta(days=index), time(21), tzinfo=UTC
                        ),
                        "adj_close": D(100 + index) / D(100),
                    }
                    for index in range(200)
                ]
            )
        if "FROM data.v022_execution_context_payload_binding" in query:
            return _Rows(
                [
                    {
                        "snapshot_semantics": {
                            "semantic_mode": "back_adjusted_historical_research",
                            "price_basis": "back_adjusted",
                            "product_warning_required": True,
                        },
                        "materialization_state": "materialized",
                        "manifest_artifact_status": "published",
                        "dataset_artifact_status": "published",
                    }
                ]
            )
        if "FROM catalog.security" in query:
            return _Rows(
                [
                    {
                        "security_id": asset_id,
                        "legacy_asset_id": legacy_id,
                        "security_key": f"asset_{ordinal}",
                    }
                    for ordinal, (asset_id, legacy_id) in enumerate(
                        zip(ASSETS, self.legacy, strict=True)
                    )
                ]
            )
        if "asset_id IN" in query and "FROM data.daily_bar" in query:
            return _Rows(
                [
                    {
                        "asset_id": legacy_id,
                        "session_date": session,
                        "open_adj": D(100 + ordinal),
                        "adj_close": D(101 + ordinal),
                    }
                    for session in SESSIONS
                    for ordinal, legacy_id in enumerate(self.legacy)
                ]
            )
        if "asset_id=:asset" in query and "FROM data.daily_bar" in query:
            return _Rows(
                [
                    {
                        "asset_id": SPY,
                        "session_date": session,
                        "open_adj": D(100),
                        "adj_close": D(101),
                    }
                    for session in SESSIONS
                ]
            )
        if "FROM catalog.calendar_session" in query:
            return _Rows(
                [
                    {
                        "session_date": session,
                        "close_at_utc": datetime.combine(session, time(21), tzinfo=UTC),
                    }
                    for session in SESSIONS
                ]
            )
        if "SELECT snapshot.fetched_at" in query:
            return _Rows(
                [{"fetched_at": datetime(2025, 1, 8, hour, tzinfo=UTC)} for hour in (1, 2, 3)]
            )
        if "FROM data.reserve_return" in query:
            return _Rows(
                [
                    {
                        "interval_start": start,
                        "interval_end": end,
                        "accrual_factor": D("1.001"),
                        "source_observation_date": start,
                        "source_available_date": start,
                        "quality_status": "normal",
                    }
                    for start, end in zip(SESSIONS, SESSIONS[1:], strict=False)
                ]
            )
        raise AssertionError(query)


class _RepresentativeEngine:
    def __init__(self) -> None:
        self.connection = _RepresentativeConnection()

    def connect(self) -> nullcontext[_RepresentativeConnection]:
        return nullcontext(self.connection)


def test_representative_four_etf_typed_chain_reaches_portfolio_result() -> None:
    points = tuple(
        SignalManifestPoint(
            asset_id,
            f"asset_{ordinal}",
            decision,
            D(10 - ordinal),
            datetime.combine(decision, time(21), tzinfo=UTC),
            f"revision-{decision}-{ordinal}",
            None,
        )
        for decision in SESSIONS[:2]
        for ordinal, asset_id in enumerate(ASSETS)
    )
    strategy_payload = build_strategy_target_payload(
        points,
        variant_key="cross_section_rank_top_k_parity",
        resolved_parameters={
            "target_k": 2,
            "selection_buffer": "none",
            "sector_cap": "none",
        },
        research_mode="exploratory",
        work_execution_fingerprint="a" * 64,
    )
    strategy_payload = decode_runtime_payload_parquet(
        encode_runtime_payload_parquet(strategy_payload)
    )
    strategy_targets = decode_strategy_targets(strategy_payload)
    assert len(strategy_targets) == 2
    assert all(len(item.positions) == 2 for item in strategy_targets)

    defense_payload = build_fixed20_defense_payload(
        strategy_targets,
        defense_version_id=DEFENSE,
        timing_policy_version_id=TIMING,
        allocation_policy_version_id=ALLOCATION,
        timing_variant_key="fixed20_budget",
        work_execution_fingerprint="b" * 64,
    )
    merged_payload = build_merged_target_payload(
        strategy_targets,
        defense_decisions=decode_defense_decisions(defense_payload),
        allocation_members=(
            DefenseAllocationMember(None, "synthetic_reserve", "reserve", D(1), 0),
        ),
        compiled_strategy_branch_id=BRANCH,
        work_execution_fingerprint="c" * 64,
    )
    merged_targets = decode_merged_targets(merged_payload)
    request = PortfolioMarketInputRequest(
        uuid.uuid4(),
        uuid.uuid4(),
        uuid.uuid4(),
        uuid.uuid4(),
        CALENDAR,
        uuid.uuid4(),
        SESSIONS[1],
        SESSIONS[-1],
        SPY,
        "spy",
        ASSETS,
        SESSIONS[:2],
    )
    result = build_portfolio_cell_payload(
        merged_targets,
        inputs=_MemoryPortfolioLoader().load(request),
        identity=PortfolioExecutionIdentity(
            BRANCH,
            SNAPSHOT,
            "d" * 64,
            SESSIONS[1],
            SESSIONS[-1],
            SPY,
            "spy",
            "linear_10bps_per_side_v1",
            D(10),
            1,
            D("100000000"),
        ),
        work_execution_fingerprint="e" * 64,
    )

    assert result.contract_key == "portfolio_cell_result"
    assert result.document["quality"]["outcome"] == "accepted"
    assert result.document["net_path"][-1]["session_date"] == "2025-01-07"


def test_ma200_defense_starts_at_first_complete_timing_window() -> None:
    sessions = tuple(date(2024, 1, 1) + timedelta(days=index) for index in range(200))
    decision_dates = (sessions[-2], sessions[-1])
    points = tuple(
        SignalManifestPoint(
            asset_id,
            f"asset_{ordinal}",
            decision,
            D(10 - ordinal),
            datetime.combine(decision, time(21), tzinfo=UTC),
            f"revision-{decision}-{ordinal}",
            None,
        )
        for decision in decision_dates
        for ordinal, asset_id in enumerate(ASSETS)
    )
    targets = decode_strategy_targets(
        build_strategy_target_payload(
            points,
            variant_key="cross_section_rank_top_k_parity",
            resolved_parameters={
                "target_k": 2,
                "selection_buffer": "none",
                "sector_cap": "none",
            },
            research_mode="exploratory",
            work_execution_fingerprint="1" * 64,
        )
    )
    observations = tuple(
        DefensePriceObservation(
            session,
            datetime.combine(session, time(21), tzinfo=UTC),
            D(100 + index) / D(100),
        )
        for index, session in enumerate(sessions)
    )
    defense = build_defense_payload(
        targets,
        defense_version_id=DEFENSE,
        timing_policy_version_id=TIMING,
        allocation_policy_version_id=ALLOCATION,
        timing_variant_key="spy_ma200_tiered_budget",
        timing_windows={sessions[-1]: DefenseTimingWindow(observations, sessions)},
        work_execution_fingerprint="2" * 64,
    )
    decisions = decode_defense_decisions(defense)
    merged = decode_merged_targets(
        build_merged_target_payload(
            targets,
            defense_decisions=decisions,
            allocation_members=(
                DefenseAllocationMember(None, "synthetic_reserve", "reserve", D(1), 0),
            ),
            compiled_strategy_branch_id=BRANCH,
            work_execution_fingerprint="3" * 64,
        )
    )

    assert [item.decision_date for item in decisions] == [sessions[-1]]
    assert decisions[0].timing_variant_key == "spy_ma200_tiered_budget"
    assert [item.decision_date for item in merged] == [sessions[-1]]


def test_ma200_loader_reads_exact_defense_context_dataset_and_calendar() -> None:
    final_session = date(2024, 1, 1) + timedelta(days=199)
    windows = load_ma200_defense_timing_windows(
        cast(Engine, _RepresentativeEngine()),
        compiled_defense_execution_context_id=uuid.uuid4(),
        decision_dates=(final_session,),
    )

    assert tuple(windows) == (final_session,)
    assert len(windows[final_session].observations) == 200
    assert windows[final_session].expected_sessions[-1] == final_session


def test_representative_production_loader_materializes_frozen_market_inputs() -> None:
    engine = _RepresentativeEngine()
    request = PortfolioMarketInputRequest(
        uuid.uuid4(),
        uuid.uuid4(),
        uuid.uuid4(),
        uuid.uuid4(),
        CALENDAR,
        uuid.uuid4(),
        SESSIONS[0],
        SESSIONS[-1],
        SPY,
        "spy",
        ASSETS,
        SESSIONS[:2],
    )
    inputs = RepresentativePortfolioMarketInputLoader(cast(Engine, engine)).load(request)

    assert inputs.common_sessions == SESSIONS
    assert len(inputs.bars) == len(SESSIONS) * 5
    assert len(inputs.reserve_intervals) == 3
    assert tuple(item[0] for item in inputs.session_input_known_at) == SESSIONS
    assert all(
        known_at == datetime.combine(session, time(21), tzinfo=UTC)
        for session, known_at in inputs.session_input_known_at
    )
    proof_query = next(
        query
        for query in engine.connection.queries
        if "v022_execution_context_payload_binding" in query
    )
    assert "v022_calculation_context_payload_binding" in proof_query
    assert "v022_compiled_context_calculation_binding" in proof_query


def test_representative_loader_reuses_exact_frozen_market_panel() -> None:
    engine = _RepresentativeEngine()
    loader = RepresentativePortfolioMarketInputLoader(cast(Engine, engine))
    request = PortfolioMarketInputRequest(
        uuid.uuid4(),
        uuid.uuid4(),
        uuid.uuid4(),
        uuid.uuid4(),
        CALENDAR,
        uuid.uuid4(),
        SESSIONS[0],
        SESSIONS[-1],
        SPY,
        "spy",
        ASSETS,
        SESSIONS[:2],
    )

    first = loader.load(request)
    query_count = engine.connection.execute_count
    second = loader.load(
        replace(request, compiled_execution_data_context_id=uuid.uuid4())
    )

    assert second is first
    assert engine.connection.execute_count == query_count + 1


def test_strategy_warmup_rows_begin_at_first_rankable_decision() -> None:
    warmup = date(2024, 12, 31)
    points = tuple(
        SignalManifestPoint(
            asset_id,
            f"asset_{ordinal}",
            decision,
            None if decision == warmup else D(10 - ordinal),
            datetime.combine(decision, time(21), tzinfo=UTC),
            f"revision-{decision}-{ordinal}",
            "insufficient_history" if decision == warmup else None,
        )
        for decision in (warmup, SESSIONS[0])
        for ordinal, asset_id in enumerate(ASSETS)
    )

    payload = build_strategy_target_payload(
        points,
        variant_key="cross_section_rank_top_k_parity",
        resolved_parameters={
            "target_k": 2,
            "selection_buffer": "none",
            "sector_cap": "none",
        },
        research_mode="exploratory",
        work_execution_fingerprint="f" * 64,
    )

    assert {row["decision_date"] for row in payload.document["rows"]} == {"2025-01-02"}


def test_strategy_requires_every_frozen_selectable_asset_on_each_decision() -> None:
    decision = SESSIONS[0]
    points = tuple(
        SignalManifestPoint(
            asset_id,
            f"asset_{ordinal}",
            decision,
            D(10 - ordinal),
            datetime.combine(decision, time(21), tzinfo=UTC),
            f"revision-{decision}-{ordinal}",
            None,
        )
        for ordinal, asset_id in enumerate(ASSETS[:-1])
    )

    with pytest.raises(V022RuntimeDataError) as error:
        build_strategy_target_payload(
            points,
            variant_key="cross_section_rank_top_k_parity",
            resolved_parameters={
                "target_k": 2,
                "selection_buffer": "none",
                "sector_cap": "none",
            },
            research_mode="exploratory",
            work_execution_fingerprint="0" * 64,
            selectable_asset_ids_by_date={decision: frozenset(ASSETS)},
        )
    assert error.value.reason_code == "cohort_decision_signal_panel_mismatch"
