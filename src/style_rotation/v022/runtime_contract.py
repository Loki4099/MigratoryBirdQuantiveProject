from __future__ import annotations

from typing import Any


class V022RuntimeContractError(ValueError):
    """A deterministic runtime request violates a frozen component contract.

    Contract failures are permanent for the exact request and must not be retried by
    a Worker without changing the immutable input identity.
    """

    def __init__(
        self,
        reason_code: str,
        message: str,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        if not reason_code.strip():
            raise ValueError("Runtime failure reason_code must be nonblank")
        self.reason_code = reason_code
        self.details = details or {}
        super().__init__(f"{reason_code}: {message}")


class V022RuntimeDataError(RuntimeError):
    """Exact runtime data cannot support an honest deterministic output.

    Data failures never return a partial target or silently substitute another
    Strategy, Defense Package, prior decision, or reserve fallback.
    """

    def __init__(
        self,
        reason_code: str,
        message: str,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        if not reason_code.strip():
            raise ValueError("Runtime failure reason_code must be nonblank")
        self.reason_code = reason_code
        self.details = details or {}
        super().__init__(f"{reason_code}: {message}")
