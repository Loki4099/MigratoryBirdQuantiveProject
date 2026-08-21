from __future__ import annotations

from datetime import date, timedelta

import pytest

from style_rotation.core.canonical import sha256_hexdigest
from style_rotation.v022.incremental_runtime import (
    IncrementalExecutionContract,
    OutputPartition,
    PriorPartition,
    partition_sessions_by_calendar_year,
    plan_incremental_run,
)


def _sessions(count: int) -> tuple[date, ...]:
    start = date(2026, 1, 5)
    return tuple(start + timedelta(days=offset) for offset in range(count))


def _revisions(sessions: tuple[date, ...]) -> dict[date, str]:
    return {session: sha256_hexdigest(("raw", session)) for session in sessions}


def _partitions(sessions: tuple[date, ...]) -> tuple[OutputPartition, ...]:
    return tuple(
        OutputPartition({"asset_id": "A"}, sessions[start : start + 3])
        for start in range(0, len(sessions), 3)
    )


def _prior_from(plan: object) -> tuple[PriorPartition, ...]:
    assert hasattr(plan, "partitions")
    return tuple(
        PriorPartition(
            item.partition_key_hash,
            item.source_revision_fingerprint,
            f"payload-{index}",
        )
        for index, item in enumerate(plan.partitions)
    )


def _output_revisions(plan: object, node_key: str) -> dict[date, str]:
    assert hasattr(plan, "partitions")
    return {
        session: sha256_hexdigest(
            (node_key, item.partition_key_hash, item.source_revision_fingerprint)
        )
        for item in plan.partitions
        for session in item.output_sessions
    }


def test_append_reuses_unchanged_partitions_and_executes_only_new_tail() -> None:
    sessions = _sessions(9)
    contract = IncrementalExecutionContract("windowed", ("asset_id",), lookback=2)
    initial = plan_incremental_run(
        contract=contract,
        partitions=_partitions(sessions[:6]),
        source_revisions=_revisions(sessions[:6]),
    )
    appended = plan_incremental_run(
        contract=contract,
        partitions=_partitions(sessions),
        source_revisions=_revisions(sessions),
        prior_partitions=_prior_from(initial),
    )

    assert [item.disposition for item in appended.partitions] == [
        "reuse",
        "reuse",
        "execute",
    ]
    assert appended.partitions[-1].output_sessions == sessions[6:9]
    assert appended.partitions[-1].calculation_sessions == sessions[4:9]


def test_historical_revision_invalidates_only_intersecting_read_windows() -> None:
    sessions = _sessions(9)
    contract = IncrementalExecutionContract("windowed", ("asset_id",), lookback=2)
    original_revisions = _revisions(sessions)
    initial = plan_incremental_run(
        contract=contract,
        partitions=_partitions(sessions),
        source_revisions=original_revisions,
    )
    revised = dict(original_revisions)
    revised[sessions[4]] = sha256_hexdigest(("corrected", sessions[4]))
    replay = plan_incremental_run(
        contract=contract,
        partitions=_partitions(sessions),
        source_revisions=revised,
        prior_partitions=_prior_from(initial),
    )

    assert [item.disposition for item in replay.partitions] == [
        "reuse",
        "execute",
        "execute",
    ]
    assert sessions[4] not in replay.partitions[0].calculation_sessions
    assert sessions[4] in replay.partitions[1].calculation_sessions
    assert sessions[4] in replay.partitions[2].calculation_sessions


def test_historical_revision_propagates_only_changed_partitions_downstream() -> None:
    sessions = _sessions(9)
    factor_contract = IncrementalExecutionContract(
        "windowed", ("asset_id",), lookback=2
    )
    signal_contract = IncrementalExecutionContract(
        "windowed",
        ("asset_id",),
        lookback=0,
        revision_impact_policy="same_cross_section",
    )
    original = _revisions(sessions)
    factor_initial = plan_incremental_run(
        contract=factor_contract,
        partitions=_partitions(sessions),
        source_revisions=original,
    )
    signal_initial = plan_incremental_run(
        contract=signal_contract,
        partitions=_partitions(sessions),
        source_revisions=_output_revisions(factor_initial, "factor"),
    )
    revised = dict(original)
    revised[sessions[4]] = sha256_hexdigest(("corrected", sessions[4]))
    factor_replay = plan_incremental_run(
        contract=factor_contract,
        partitions=_partitions(sessions),
        source_revisions=revised,
        prior_partitions=_prior_from(factor_initial),
    )
    signal_replay = plan_incremental_run(
        contract=signal_contract,
        partitions=_partitions(sessions),
        source_revisions=_output_revisions(factor_replay, "factor"),
        prior_partitions=_prior_from(signal_initial),
    )

    assert [item.disposition for item in factor_replay.partitions] == [
        "reuse",
        "execute",
        "execute",
    ]
    assert [item.disposition for item in signal_replay.partitions] == [
        "reuse",
        "execute",
        "execute",
    ]


def test_full_recompute_never_reuses_a_matching_partition() -> None:
    sessions = _sessions(6)
    windowed = IncrementalExecutionContract("windowed", ("asset_id",), lookback=2)
    initial = plan_incremental_run(
        contract=windowed,
        partitions=_partitions(sessions),
        source_revisions=_revisions(sessions),
    )
    conservative = plan_incremental_run(
        contract=IncrementalExecutionContract("full_recompute", ("asset_id",), lookback=2),
        partitions=_partitions(sessions),
        source_revisions=_revisions(sessions),
        prior_partitions=_prior_from(initial),
    )

    assert conservative.execute_count == 2
    assert conservative.reuse_count == 0


def test_forward_revision_policy_invalidates_every_later_partition() -> None:
    sessions = _sessions(9)
    contract = IncrementalExecutionContract(
        "windowed",
        ("asset_id",),
        lookback=0,
        revision_impact_policy="from_revised_session_forward",
    )
    original = _revisions(sessions)
    initial = plan_incremental_run(
        contract=contract,
        partitions=_partitions(sessions),
        source_revisions=original,
    )
    revised = dict(original)
    revised[sessions[4]] = sha256_hexdigest(("corrected", sessions[4]))
    replay = plan_incremental_run(
        contract=contract,
        partitions=_partitions(sessions),
        source_revisions=revised,
        prior_partitions=_prior_from(initial),
    )

    assert [item.disposition for item in replay.partitions] == [
        "reuse",
        "execute",
        "execute",
    ]


def test_partition_contract_rejects_overlaps_and_wrong_keys() -> None:
    sessions = _sessions(4)
    contract = IncrementalExecutionContract("windowed", ("asset_id",), lookback=0)
    revisions = _revisions(sessions)
    with pytest.raises(ValueError, match="must not overlap"):
        plan_incremental_run(
            contract=contract,
            partitions=(
                OutputPartition({"asset_id": "A"}, sessions[:3]),
                OutputPartition({"asset_id": "A"}, sessions[2:]),
            ),
            source_revisions=revisions,
        )
    with pytest.raises(ValueError, match="exactly match"):
        plan_incremental_run(
            contract=contract,
            partitions=(OutputPartition({"session_date": "2026-01"}, sessions),),
            source_revisions=revisions,
        )


def test_calendar_year_partitions_are_canonical_and_non_overlapping() -> None:
    sessions = (
        date(2025, 12, 30),
        date(2025, 12, 31),
        date(2026, 1, 2),
        date(2026, 1, 5),
        date(2027, 1, 4),
    )

    partitions = partition_sessions_by_calendar_year(
        partition_key=("asset_id",), sessions=sessions
    )

    assert tuple(item.partition_key for item in partitions) == (
        {"asset_id": "calendar_year:2025"},
        {"asset_id": "calendar_year:2026"},
        {"asset_id": "calendar_year:2027"},
    )
    assert tuple(item.sessions for item in partitions) == (
        sessions[:2],
        sessions[2:4],
        sessions[4:],
    )
    assert tuple(session for item in partitions for session in item.sessions) == sessions


def test_calendar_year_partition_preserves_window_halo_across_year_boundary() -> None:
    sessions = (
        date(2025, 12, 29),
        date(2025, 12, 30),
        date(2025, 12, 31),
        date(2026, 1, 2),
        date(2026, 1, 5),
        date(2026, 1, 6),
    )
    partitions = partition_sessions_by_calendar_year(
        partition_key=("asset_id",), sessions=sessions
    )

    plan = plan_incremental_run(
        contract=IncrementalExecutionContract(
            "windowed", ("asset_id",), lookback=2
        ),
        partitions=partitions,
        source_revisions=_revisions(sessions),
    )

    assert plan.partitions[0].output_sessions == sessions[:3]
    assert plan.partitions[0].calculation_sessions == sessions[:3]
    assert plan.partitions[1].output_sessions == sessions[3:]
    assert plan.partitions[1].calculation_sessions == sessions[1:]
    assert plan.partitions[1].calculation_sessions[:2] == sessions[1:3]
