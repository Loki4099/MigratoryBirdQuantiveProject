import pytest

from style_rotation.domain.enums import AlertStatus, ProductLifecycle, WorkItemStatus
from style_rotation.domain.lifecycle import (
    ensure_alert_transition,
    ensure_product_transition,
    ensure_work_item_transition,
)


def test_work_item_failure_can_retry_but_completed_is_terminal() -> None:
    ensure_work_item_transition(WorkItemStatus.FAILED, WorkItemStatus.QUEUED)
    with pytest.raises(ValueError, match="Invalid work item transition"):
        ensure_work_item_transition(WorkItemStatus.COMPLETED, WorkItemStatus.RUNNING)


def test_retired_and_invalidated_products_cannot_resume() -> None:
    ensure_product_transition(ProductLifecycle.ACTIVE, ProductLifecycle.SUSPENDED)
    ensure_product_transition(ProductLifecycle.SUSPENDED, ProductLifecycle.ACTIVE)
    for terminal in (ProductLifecycle.RETIRED, ProductLifecycle.INVALIDATED):
        with pytest.raises(ValueError, match="Invalid Product lifecycle transition"):
            ensure_product_transition(terminal, ProductLifecycle.ACTIVE)


def test_alert_history_is_append_only_and_terminal_after_resolution() -> None:
    ensure_alert_transition(AlertStatus.OPEN, AlertStatus.ACKNOWLEDGED)
    ensure_alert_transition(AlertStatus.ACKNOWLEDGED, AlertStatus.RESOLVED)
    with pytest.raises(ValueError, match="Invalid alert transition"):
        ensure_alert_transition(AlertStatus.RESOLVED, AlertStatus.OPEN)
