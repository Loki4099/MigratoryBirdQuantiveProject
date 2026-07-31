from __future__ import annotations

import uuid

from style_rotation.factors.calculator import calculate_factors
from style_rotation.factors.contracts import PHASE3_CONTRACTS
from style_rotation.factors.registry import DEFINITIONS, REGISTRY_HASH, VARIANTS
from style_rotation.factors.repository import FactorRepository
from style_rotation.factors.types import FactorPublicationOutcome


class FactorComputationService:
    def __init__(self, repository: FactorRepository) -> None:
        self._repository = repository

    def run(
        self,
        *,
        data_version_id: uuid.UUID,
        cleaning_version_id: uuid.UUID,
        factor_version_key: str,
        factor_code_hash: str,
    ) -> FactorPublicationOutcome:
        self._repository.publish_contracts(PHASE3_CONTRACTS)
        factor_version_id = self._repository.ensure_factor_version(
            version_key=factor_version_key,
            registry_hash=REGISTRY_HASH,
            code_hash=factor_code_hash,
            definitions=DEFINITIONS,
            variants=VARIANTS,
        )
        existing = self._repository.factor_dataset_exists(
            data_version_id, cleaning_version_id, factor_version_id
        )
        if existing is not None:
            return FactorPublicationOutcome(
                factor_version_id,
                True,
                existing.row_count,
                existing.common_valid_start,
            )
        prices = self._repository.load_clean_prices(data_version_id, cleaning_version_id)
        result = calculate_factors(prices)
        self._repository.publish_factor_result(
            data_version_id=data_version_id,
            cleaning_version_id=cleaning_version_id,
            factor_version_id=factor_version_id,
            result=result,
        )
        return FactorPublicationOutcome(
            factor_version_id,
            False,
            len(result.points),
            result.common_valid_start,
        )
