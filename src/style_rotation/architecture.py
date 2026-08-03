from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class DomainBoundary:
    """A stable v0.2 business-module boundary, independent of database implementation."""

    key: str
    purpose: str
    upstream: tuple[str, ...]
    delivery_milestone: str


DOMAIN_BOUNDARIES = (
    DomainBoundary("catalog", "Assets, universes, calendars, and eligibility", (), "M2"),
    DomainBoundary("data", "Versioned source, validated, and derived datasets", ("catalog",), "M2"),
    DomainBoundary("factor", "Asset-agnostic market measurements", ("catalog", "data"), "M3"),
    DomainBoundary(
        "signal", "Economic interpretations of factor variants", ("data", "factor"), "M4"
    ),
    DomainBoundary(
        "model", "Combinations of signals into comparable outputs", ("data", "signal"), "M5"
    ),
    DomainBoundary(
        "strategy",
        "Model-to-portfolio target rules and product identity",
        ("catalog", "signal", "model"),
        "M6",
    ),
    DomainBoundary(
        "experiment",
        "Execution, accounting, benchmarks, intervals, and results",
        ("catalog", "data", "factor", "signal", "model", "strategy"),
        "M7",
    ),
    DomainBoundary("lineage", "Cross-domain artifact dependencies and manifests", (), "M1C"),
    DomainBoundary("ops", "Engines, run attempts, quality events, and errors", (), "M1B"),
)


def validate_domain_boundaries(
    boundaries: tuple[DomainBoundary, ...] = DOMAIN_BOUNDARIES,
) -> None:
    """Reject duplicate, missing, self-referential, or cyclic business dependencies."""

    by_key = {boundary.key: boundary for boundary in boundaries}
    if len(by_key) != len(boundaries):
        raise ValueError("Domain boundary keys must be unique")
    for boundary in boundaries:
        unknown = set(boundary.upstream).difference(by_key)
        if unknown:
            raise ValueError(f"Domain {boundary.key} has unknown upstream modules: {unknown}")
        if boundary.key in boundary.upstream:
            raise ValueError(f"Domain {boundary.key} cannot depend on itself")

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(key: str) -> None:
        if key in visiting:
            raise ValueError(f"Domain dependency cycle includes {key}")
        if key in visited:
            return
        visiting.add(key)
        for upstream in by_key[key].upstream:
            visit(upstream)
        visiting.remove(key)
        visited.add(key)

    for boundary_key in by_key:
        visit(boundary_key)


validate_domain_boundaries()
