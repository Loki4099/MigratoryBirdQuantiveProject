from decimal import Decimal

from style_rotation.experiment.ranking import RankableValue, competition_ranks


def test_competition_ranks_share_ties_and_exclude_undefined_values() -> None:
    values = (
        RankableValue("b", Decimal("0.8")),
        RankableValue("a", Decimal("1.0")),
        RankableValue("c", Decimal("0.8")),
        RankableValue("missing", None),
    )
    assert competition_ranks(values, "higher_is_better") == {
        "a": 1,
        "b": 2,
        "c": 2,
        "missing": None,
    }


def test_competition_ranks_support_lower_is_better() -> None:
    values = (RankableValue("a", Decimal("2")), RankableValue("b", Decimal("1")))
    assert competition_ranks(values, "lower_is_better") == {"a": 2, "b": 1}
