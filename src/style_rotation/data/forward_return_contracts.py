from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ForwardReturnSeed(StrictModel):
    key: str = Field(min_length=1, max_length=140)
    version_number: int = Field(ge=1)
    frequency: Literal["weekly", "monthly"]
    decision_rule: Literal[
        "last_common_session_of_iso_week", "last_common_session_of_calendar_month"
    ]
    decision_time: Literal["session_close"]
    execution_policy: Literal["next_common_session_adjusted_open_v1"]
    start_price: Literal["open_adj"]
    end_price: Literal["next_schedule_execution_open_adj"]
    execution_lag_sessions: Literal[1]
    overlap_policy: Literal["non_overlapping_schedule_intervals"]
    calendar_key: Literal["xnys"]
    included_member_roles: list[Literal["candidate", "benchmark", "auxiliary_tradable"]]

    @model_validator(mode="after")
    def validate_schedule(self) -> ForwardReturnSeed:
        expected = {
            "weekly": "last_common_session_of_iso_week",
            "monthly": "last_common_session_of_calendar_month",
        }[self.frequency]
        if self.decision_rule != expected:
            raise ValueError("Forward-return frequency and decision rule do not match")
        _unique("included member role", self.included_member_roles)
        if "candidate" not in self.included_member_roles:
            raise ValueError("Forward-return targets must include candidate assets")
        return self


class ForwardReturnCatalog(StrictModel):
    catalog_type: Literal["forward_return"]
    catalog_version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    definitions: list[ForwardReturnSeed] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_catalog(self) -> ForwardReturnCatalog:
        _unique("forward-return key", [item.key for item in self.definitions])
        _unique("forward-return frequency", [item.frequency for item in self.definitions])
        return self


def _unique(label: str, values: Sequence[object]) -> None:
    if len(values) != len(set(values)):
        raise ValueError(f"Duplicate {label}")
