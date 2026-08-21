from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from style_rotation.v022.operations_slo import (
    SLOMeasurement,
    SLORule,
    evaluate_slo_rules,
)

WINDOW_START = datetime(2026, 8, 1, tzinfo=UTC)
WINDOW_END = datetime(2026, 8, 8, tzinfo=UTC)
DOMAINS = ("compile", "queue", "cache", "storage", "export", "product_freshness")


def _rules() -> tuple[SLORule, ...]:
    return tuple(
        SLORule(
            f"{domain}_metric",
            domain,  # type: ignore[arg-type]
            "gte" if domain in {"cache", "storage", "export"} else "lte",
            Decimal("0.95")
            if domain in {"cache", "storage", "export"}
            else Decimal("60"),
            10,
            "critical" if domain in {"storage", "product_freshness"} else "warning",
        )
        for domain in DOMAINS
    )


def _measurement(
    rule: SLORule, *, value: Decimal | None = None, samples: int = 20
) -> SLOMeasurement:
    observed = value
    if observed is None:
        observed = Decimal("0.99") if rule.comparator == "gte" else Decimal("30")
    return SLOMeasurement(
        uuid.uuid4(),
        uuid.uuid4(),
        rule.metric_key,
        rule.domain_key,
        observed,
        samples,
        WINDOW_START,
        WINDOW_END,
        "a" * 64,
    )


def test_all_six_operational_domains_are_required_and_can_pass() -> None:
    rules = _rules()
    results, blockers = evaluate_slo_rules(
        rules, tuple(_measurement(rule) for rule in rules)
    )

    assert blockers == ()
    assert len(results) == 6
    assert all(item.passed for item in results)


def test_breach_and_insufficient_samples_open_distinct_blockers() -> None:
    rules = _rules()
    measurements = tuple(
        _measurement(
            rule,
            value=Decimal("120") if rule.domain_key == "queue" else None,
            samples=2 if rule.domain_key == "cache" else 20,
        )
        for rule in rules
    )

    results, blockers = evaluate_slo_rules(rules, measurements)

    assert "slo_breach:queue_metric" in blockers
    assert "insufficient_samples:cache_metric" in blockers
    assert sum(not item.passed for item in results) == 2


def test_missing_measurement_fails_closed() -> None:
    rules = _rules()
    results, blockers = evaluate_slo_rules(
        rules, tuple(_measurement(rule) for rule in rules[:-1])
    )

    assert blockers == ("missing_measurement:product_freshness_metric",)
    assert results[-1].slo_measurement_id is None
    assert results[-1].actual_sample_count == 0


def test_policy_missing_required_domain_is_rejected() -> None:
    with pytest.raises(ValueError, match="missing required domains"):
        evaluate_slo_rules(_rules()[:-1], ())
