from __future__ import annotations

import os
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from style_rotation.persistence.database import create_postgres_engine, reset_database
from style_rotation.v022.operations_slo import (
    OperationsReadinessService,
    SLOMeasurementInput,
    SLOMeasurementService,
    SLOPolicyService,
    SLORule,
)
from style_rotation.v022.release_control import ReleaseControlService

pytestmark = pytest.mark.integration
DATABASE_URL = os.getenv("STYLE_ROTATION_TEST_DATABASE_URL")
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


def _measurements(
    service: SLOMeasurementService,
    *,
    window_start: datetime,
    window_end: datetime,
    omit_domain: str | None = None,
    breach_domain: str | None = None,
) -> tuple:
    publications = []
    for rule in _rules():
        if rule.domain_key == omit_domain:
            continue
        value = Decimal("0.99") if rule.comparator == "gte" else Decimal("30")
        if rule.domain_key == breach_domain:
            value = Decimal("0.50") if rule.comparator == "gte" else Decimal("120")
        publications.append(
            service.publish(
                SLOMeasurementInput(
                    rule.metric_key,
                    rule.domain_key,
                    value,
                    20,
                    window_start,
                    window_end,
                    window_end,
                    {
                        "probe_kind": f"{rule.domain_key}_controlled_probe_v1",
                        "source_identity": f"integration:{rule.metric_key}",
                    },
                )
            )
        )
    return tuple(publications)


@pytest.mark.skipif(not DATABASE_URL, reason="STYLE_ROTATION_TEST_DATABASE_URL is not set")
def test_operations_readiness_requires_six_domains_and_opens_exact_alerts() -> None:
    assert DATABASE_URL is not None
    reset_database(DATABASE_URL, "style_rotation_test", "test")
    engine = create_postgres_engine(DATABASE_URL)
    policy = SLOPolicyService(engine).publish(
        policy_key="v022_default_cutover_slo",
        version_number=1,
        rules=_rules(),
    )
    measurements = SLOMeasurementService(engine)
    readiness = OperationsReadinessService(engine)

    first_start = datetime(2026, 8, 1, tzinfo=UTC)
    first_end = datetime(2026, 8, 8, tzinfo=UTC)
    passing = _measurements(
        measurements, window_start=first_start, window_end=first_end
    )
    ready = readiness.publish(
        slo_policy_version_id=policy.slo_policy_version_id,
        window_start_at=first_start,
        window_end_at=first_end,
        measurement_ids=tuple(item.slo_measurement_id for item in passing),
        evaluated_at=first_end,
    )
    ready_replay = readiness.publish(
        slo_policy_version_id=policy.slo_policy_version_id,
        window_start_at=first_start,
        window_end_at=first_end,
        measurement_ids=tuple(item.slo_measurement_id for item in passing),
        evaluated_at=first_end,
    )

    second_start = datetime(2026, 8, 8, tzinfo=UTC)
    second_end = datetime(2026, 8, 15, tzinfo=UTC)
    failing = _measurements(
        measurements,
        window_start=second_start,
        window_end=second_end,
        omit_domain="product_freshness",
        breach_domain="queue",
    )
    blocked = readiness.publish(
        slo_policy_version_id=policy.slo_policy_version_id,
        window_start_at=second_start,
        window_end_at=second_end,
        measurement_ids=tuple(item.slo_measurement_id for item in failing),
        evaluated_at=second_end,
    )

    assert ready.ready_for_default is True
    assert ready.alert_count == 0
    assert ready_replay.reused is True
    assert blocked.ready_for_default is False
    assert blocked.alert_count == 2
    assert "slo_breach:queue_metric" in blocked.blocker_codes
    assert "missing_measurement:product_freshness_metric" in blocked.blocker_codes
    validated = ReleaseControlService(engine)._published_evidence(  # noqa: SLF001
        {"operations_readiness_artifact_id": ready.artifact_id}
    )
    assert validated["operations_readiness_artifact_id"]["artifact_id"] == ready.artifact_id
    with pytest.raises(ValueError, match="not ready for default"):
        ReleaseControlService(engine)._published_evidence(  # noqa: SLF001
            {"operations_readiness_artifact_id": blocked.artifact_id}
        )
    engine.dispose()
