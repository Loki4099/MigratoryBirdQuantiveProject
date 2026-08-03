from __future__ import annotations

import hashlib
import json
import math
import struct
import unicodedata
from collections.abc import Mapping, Sequence, Set
from dataclasses import asdict, is_dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any
from uuid import UUID

CANONICAL_SERIALIZATION_VERSION = "canonical-json-v2"


def _normalize_text(value: str) -> str:
    return unicodedata.normalize("NFC", value)


def _normalize_decimal(value: Decimal) -> str:
    if not value.is_finite():
        raise ValueError("NaN and infinity are not allowed in versioned payloads")
    if value.is_zero():
        return "0"
    normalized = format(value.normalize(), "f")
    return normalized.rstrip("0").rstrip(".") if "." in normalized else normalized


def canonicalize(value: Any) -> Any:
    """Convert supported values into the canonical-json-v2 representation."""

    if is_dataclass(value) and not isinstance(value, type):
        return canonicalize(asdict(value))
    if isinstance(value, Enum):
        return canonicalize(value.value)
    if isinstance(value, Mapping):
        normalized: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError("Mapping keys in versioned payloads must be strings")
            normalized_key = _normalize_text(key)
            if normalized_key in normalized:
                raise ValueError("Mapping contains duplicate keys after Unicode normalization")
            normalized[normalized_key] = canonicalize(item)
        return {key: normalized[key] for key in sorted(normalized)}
    if isinstance(value, Set) and not isinstance(value, (str, bytes, bytearray)):
        items = [canonicalize(item) for item in value]
        return sorted(items, key=_dump_normalized)
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [canonicalize(item) for item in value]
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Naive datetimes are not allowed in versioned payloads")
        utc_value = value.astimezone(UTC)
        return {"$datetime": utc_value.isoformat(timespec="microseconds").replace("+00:00", "Z")}
    if isinstance(value, date):
        return {"$date": value.isoformat()}
    if isinstance(value, Decimal):
        return {"$decimal": _normalize_decimal(value)}
    if isinstance(value, UUID):
        return {"$uuid": str(value).lower()}
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("NaN and infinity are not allowed in versioned payloads")
        return {"$float64": struct.pack(">d", value).hex()}
    if isinstance(value, (bytes, bytearray)):
        return {"$bytes": bytes(value).hex()}
    if isinstance(value, str):
        return _normalize_text(value)
    if value is None or isinstance(value, (int, bool)):
        return value
    raise TypeError(f"Unsupported value in versioned payload: {type(value).__name__}")


def _dump_normalized(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def canonical_json(value: Any) -> str:
    envelope = {
        "$canonical": CANONICAL_SERIALIZATION_VERSION,
        "$value": canonicalize(value),
    }
    return _dump_normalized(envelope)


def sha256_hexdigest(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()
