import uuid

from style_rotation.v022.shadow_coverage import (
    ShadowCoverageInput,
    ShadowObservation,
    evaluate_shadow_coverage,
)


def _sessions(count: int) -> tuple[uuid.UUID, ...]:
    return tuple(uuid.uuid4() for _ in range(count))


def _matched(session_ids: tuple[uuid.UUID, ...]) -> tuple[ShadowObservation, ...]:
    return tuple(ShadowObservation(item, "completed", "matched", False) for item in session_ids)


def test_coverage_never_merges_sessions_across_representatives() -> None:
    first_sessions = _sessions(6)
    second_sessions = _sessions(6)
    stats, blockers = evaluate_shadow_coverage(
        (
            ShadowCoverageInput(
                uuid.uuid4(), 1, "etf", "etf", "weekly", 12,
                first_sessions, _matched(first_sessions),
            ),
            ShadowCoverageInput(
                uuid.uuid4(), 2, "large_cap", "large_cap", "weekly", 12,
                second_sessions, _matched(second_sessions),
            ),
        )
    )
    assert all("insufficient_prospective_sessions" in item.blocker_codes for item in stats)
    assert "shadow_representative_not_ready" in blockers


def test_complete_context_frequency_matrix_can_be_ready() -> None:
    inputs = []
    for ordinal, (key, context_class, frequency, minimum) in enumerate(
        (
            ("etf", "etf", "weekly", 12),
            ("etf", "etf", "monthly", 3),
            ("large_cap", "large_cap", "weekly", 12),
            ("large_cap", "large_cap", "monthly", 3),
        ),
        start=1,
    ):
        sessions = _sessions(minimum)
        inputs.append(
            ShadowCoverageInput(
                uuid.uuid4(), ordinal, key, context_class, frequency, minimum,
                sessions, _matched(sessions),
            )
        )
    stats, blockers = evaluate_shadow_coverage(tuple(inputs))
    assert blockers == ()
    assert all(item.ready for item in stats)


def test_missing_and_unexplained_sessions_fail_closed() -> None:
    sessions = _sessions(12)
    observations = list(_matched(sessions[:-1]))
    observations[-1] = ShadowObservation(sessions[-2], "missing", "different", False)
    stats, _ = evaluate_shadow_coverage(
        (
            ShadowCoverageInput(
                uuid.uuid4(), 1, "etf", "etf", "weekly", 12,
                sessions, tuple(observations),
            ),
        )
    )
    assert stats[0].blocker_codes == (
        "missing_shadow_comparisons",
        "missing_v022_decision",
        "unexplained_shadow_difference",
    )
