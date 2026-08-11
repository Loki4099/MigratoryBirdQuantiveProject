from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal, cast

RankingDirection = Literal["higher_is_better", "lower_is_better"]


@dataclass(frozen=True, slots=True)
class RankableValue:
    key: str
    value: Decimal | None


def competition_ranks(
    values: tuple[RankableValue, ...], direction: RankingDirection
) -> dict[str, int | None]:
    """Assign deterministic competition ranks; undefined values do not rank."""
    defined = [item for item in values if item.value is not None]
    defined.sort(
        key=lambda item: (
            -cast(Decimal, item.value)
            if direction == "higher_is_better"
            else cast(Decimal, item.value),
            item.key,
        )
    )
    ranks: dict[str, int | None] = {item.key: None for item in values}
    previous: Decimal | None = None
    previous_rank = 0
    for position, item in enumerate(defined, start=1):
        if previous is None or item.value != previous:
            previous_rank = position
            previous = item.value
        ranks[item.key] = previous_rank
    return ranks
