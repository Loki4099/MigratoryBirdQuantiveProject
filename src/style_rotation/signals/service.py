from __future__ import annotations

import uuid
from typing import Any

from style_rotation.core.canonical import sha256_hexdigest
from style_rotation.signals.calculator import calculate_target_positions
from style_rotation.signals.contracts import PHASE4_CONTRACTS
from style_rotation.signals.repository import SignalRepository
from style_rotation.signals.types import SignalPublicationOutcome

STRATEGY_CONFIGURATION: dict[str, Any] = {
    "frequencies": ["weekly", "monthly"],
    "templates": ["cross_sectional", "trend_filtered"],
    "ticker_tie_order": ["IWF", "IWD", "IWO", "IWN"],
    "top_n": 2,
    "target_weight_per_selected_asset": "0.5",
    "trend_filter": {
        "field": "close_adj",
        "operator": ">",
        "moving_average": "SMA",
        "window": 200,
        "includes_signal_date": True,
    },
    "signal_timing": "last_actual_session_close",
    "execution_timing": "next_actual_session_open",
    "reserve_allocation": "unused_risk_budget",
}
STRATEGY_CONFIGURATION_HASH = sha256_hexdigest(STRATEGY_CONFIGURATION)


class SignalComputationService:
    def __init__(self, repository: SignalRepository) -> None:
        self._repository = repository

    def run(
        self,
        *,
        data_version_id: uuid.UUID,
        cleaning_version_id: uuid.UUID,
        factor_version_id: uuid.UUID,
        strategy_version_key: str,
    ) -> SignalPublicationOutcome:
        self._repository.publish_contracts(PHASE4_CONTRACTS)
        strategy_version_id = self._repository.ensure_strategy_version(
            version_key=strategy_version_key,
            configuration_hash=STRATEGY_CONFIGURATION_HASH,
            configuration=STRATEGY_CONFIGURATION,
        )
        existing = self._repository.signal_dataset_summary(
            data_version_id,
            cleaning_version_id,
            factor_version_id,
            strategy_version_id,
        )
        if existing is not None:
            event_count, position_count, first_signal_date = existing
            return SignalPublicationOutcome(
                strategy_version_id,
                True,
                event_count,
                position_count,
                first_signal_date,
            )
        prices, factor_points, factor_common_start = self._repository.load_inputs(
            data_version_id, cleaning_version_id, factor_version_id
        )
        result = calculate_target_positions(prices, factor_points, factor_common_start)
        self._repository.publish_signal_result(
            data_version_id=data_version_id,
            cleaning_version_id=cleaning_version_id,
            factor_version_id=factor_version_id,
            strategy_version_id=strategy_version_id,
            result=result,
        )
        return SignalPublicationOutcome(
            strategy_version_id,
            False,
            len(result.events),
            result.position_count,
            result.first_signal_date,
        )
