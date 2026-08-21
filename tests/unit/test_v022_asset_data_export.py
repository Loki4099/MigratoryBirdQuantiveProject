from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from pathlib import Path

import pyarrow as pa
import pytest

from style_rotation.v022.asset_data_export import (
    DEFAULT_FIELDS,
    _content_path,
    _normalize_fields,
    _price_table,
)


def test_asset_export_price_table_preserves_decimal_and_key_types() -> None:
    fields = ("close_raw", "close_adj", "adjustment_factor", "volume_raw")
    table = _price_table(
        [
            (
                uuid.uuid4(),
                uuid.uuid4(),
                "aapl",
                "AAPL",
                date(2026, 1, 2),
                Decimal("100.1234567890"),
                Decimal("99.1234567890"),
                Decimal("0.99003449001234"),
                123456,
            )
        ],
        fields,
    )

    assert table.num_rows == 1
    assert table.schema.field("close_raw").type == pa.decimal128(24, 10)
    assert table.schema.field("adjustment_factor").type == pa.decimal128(24, 14)
    assert table.schema.field("volume_raw").type == pa.int64()
    assert table.column("close_raw").to_pylist() == [Decimal("100.1234567890")]


def test_asset_export_field_contract_rejects_unknowns_and_duplicates() -> None:
    assert _normalize_fields(DEFAULT_FIELDS) == DEFAULT_FIELDS
    with pytest.raises(ValueError, match="asset_export_field_selection_invalid"):
        _normalize_fields(())
    with pytest.raises(ValueError, match="asset_export_field_selection_invalid"):
        _normalize_fields(("close_adj", "close_adj"))
    with pytest.raises(ValueError, match="asset_export_field_selection_invalid"):
        _normalize_fields(("future_return",))


def test_asset_export_content_address_cannot_escape_root(tmp_path: Path) -> None:
    digest = "a" * 64
    assert _content_path(tmp_path.resolve(), digest).is_relative_to(tmp_path.resolve())
    with pytest.raises(ValueError, match="content hash is invalid"):
        _content_path(tmp_path.resolve(), "../escape")
