from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Health = Literal["observing", "healthy", "watch", "warning", "data_interrupted"]


@dataclass(frozen=True, slots=True)
class MonitoringEvidence:
    frequency: Literal["weekly", "monthly"]
    session_count: int
    decision_count: int
    data_contract_ok: bool
    capacity_ok: bool
    performance_watch: bool = False
    performance_warning: bool = False
    predictive_watch: bool = False
    predictive_warning: bool = False
    reference_sufficient: bool = True


@dataclass(frozen=True, slots=True)
class MonitoringHealth:
    overall: Health
    performance_ready: bool
    predictive_ready: bool
    reason_codes: tuple[str, ...]


def evaluate_monitoring_health(evidence: MonitoringEvidence) -> MonitoringHealth:
    if evidence.session_count < 0 or evidence.decision_count < 0:
        raise ValueError("Monitoring counts cannot be negative")
    performance_ready = evidence.session_count >= 126
    predictive_minimum = 26 if evidence.frequency == "weekly" else 12
    predictive_ready = evidence.decision_count >= predictive_minimum
    reasons: list[str] = []
    if not evidence.data_contract_ok:
        reasons.append("data_or_contract_interrupted")
        return MonitoringHealth(
            "data_interrupted", performance_ready, predictive_ready, tuple(reasons)
        )
    if not evidence.capacity_ok:
        reasons.append("capacity_review_required")
        return MonitoringHealth("warning", performance_ready, predictive_ready, tuple(reasons))
    if not evidence.reference_sufficient:
        reasons.append("reference_insufficient")
        return MonitoringHealth("observing", performance_ready, predictive_ready, tuple(reasons))
    if evidence.performance_warning and performance_ready:
        reasons.append("performance_warning")
    if evidence.predictive_warning and predictive_ready:
        reasons.append("predictive_warning")
    if reasons:
        return MonitoringHealth("warning", performance_ready, predictive_ready, tuple(reasons))
    if (evidence.performance_watch and performance_ready) or (
        evidence.predictive_watch and predictive_ready
    ):
        return MonitoringHealth("watch", performance_ready, predictive_ready, ("watch",))
    if not performance_ready or not predictive_ready:
        if not performance_ready:
            reasons.append("performance_sample_observing")
        if not predictive_ready:
            reasons.append("predictive_sample_observing")
        return MonitoringHealth("observing", performance_ready, predictive_ready, tuple(reasons))
    return MonitoringHealth("healthy", True, True, ())
