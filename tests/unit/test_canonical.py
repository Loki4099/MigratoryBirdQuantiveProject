import math
import unittest
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

from style_rotation.core.canonical import canonical_json, sha256_hexdigest


class CanonicalSerializationTests(unittest.TestCase):
    def test_mapping_order_does_not_change_hash(self) -> None:
        left = {"b": 2, "a": {"y": 1, "x": Decimal("5.0000")}}
        right = {"a": {"x": Decimal("5.0000"), "y": 1}, "b": 2}
        self.assertEqual(sha256_hexdigest(left), sha256_hexdigest(right))

    def test_timezone_aware_datetime_is_stable(self) -> None:
        value = datetime(2026, 7, 31, 12, 0, tzinfo=UTC)
        equivalent = datetime(2026, 7, 31, 20, 0, tzinfo=timezone(timedelta(hours=8)))
        self.assertIn("2026-07-31T12:00:00.000000Z", canonical_json(value))
        self.assertEqual(sha256_hexdigest(value), sha256_hexdigest(equivalent))

    def test_type_boundaries_do_not_collide(self) -> None:
        values = [1, 1.0, Decimal("1.000"), "1"]
        self.assertEqual(len({sha256_hexdigest(value) for value in values}), len(values))

    def test_equivalent_decimals_and_unicode_have_the_same_hash(self) -> None:
        self.assertEqual(sha256_hexdigest(Decimal("1.000")), sha256_hexdigest(Decimal("1")))
        self.assertEqual(sha256_hexdigest("e\u0301"), sha256_hexdigest("é"))

    def test_non_string_mapping_key_is_rejected(self) -> None:
        with self.assertRaisesRegex(TypeError, "Mapping keys"):
            canonical_json({1: "value"})

    def test_naive_datetime_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "Naive datetimes"):
            canonical_json(datetime(2026, 7, 31, 12, 0))

    def test_non_finite_float_is_rejected(self) -> None:
        for value in (math.nan, math.inf, -math.inf):
            with self.assertRaisesRegex(ValueError, "NaN and infinity"):
                canonical_json(value)


if __name__ == "__main__":
    unittest.main()
