from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from style_rotation.v022.catalog import load_catalog_release
from style_rotation.v022.processing_runtime import (
    AMIHUD_DAILY_PRIMITIVES_IMPLEMENTATION,
    AMIHUD_ILLIQUIDITY_IMPLEMENTATION,
    COMPAT_CONTINUOUS_IMPLEMENTATION,
    CONTINUOUS_CROSS_SECTIONAL_SIGNAL_IMPLEMENTATION,
    CROSSOVER_EVENT_IMPLEMENTATION,
    DOWNSIDE_DEVIATION_IMPLEMENTATION,
    LAGGED_RETURN_IMPLEMENTATION,
    MAXIMUM_DRAWDOWN_IMPLEMENTATION,
    MOVING_AVERAGE_RATIO_IMPLEMENTATION,
    PPO_HISTOGRAM_IMPLEMENTATION,
    PRICE_CROSS_ABOVE_MA_IMPLEMENTATION,
    REALIZED_VOLATILITY_IMPLEMENTATION,
    RELATIVE_DOLLAR_VOLUME_IMPLEMENTATION,
    RETURN_EXCESS_KURTOSIS_IMPLEMENTATION,
    RETURN_SKEWNESS_IMPLEMENTATION,
    RSI_WILDER_IMPLEMENTATION,
    THRESHOLD_STATE_IMPLEMENTATION,
    TOTAL_RETURN_IMPLEMENTATION,
    Panel,
    cross_sectional_centered_rank,
    execute_catalog_node,
    execute_representative_graph,
)

PROJECT_ROOT = Path(__file__).parents[2]


def test_centered_rank_preserves_ties_missing_values_and_direction() -> None:
    session = date(2026, 1, 2)
    values = {
        ("A", session): Decimal("1"),
        ("B", session): Decimal("2"),
        ("C", session): Decimal("2"),
        ("D", session): None,
    }
    positive = cross_sectional_centered_rank(values, direction=1)
    negative = cross_sectional_centered_rank(values, direction=-1)

    assert positive[("A", session)] == Decimal("-1.000000000000000000")
    assert positive[("B", session)] == Decimal("0.500000000000000000")
    assert positive[("C", session)] == Decimal("0.500000000000000000")
    assert positive[("D", session)] is None
    assert negative[("A", session)] == Decimal("1.000000000000000000")


def test_three_representative_chains_execute_to_one_aggregation() -> None:
    start = date(2025, 1, 1)
    adjusted: Panel = {}
    raw: Panel = {}
    volume: Panel = {}
    for offset in range(205):
        session = start + timedelta(days=offset)
        for asset, slope, traded_volume in (
            ("A", Decimal("1.0"), Decimal("1000")),
            ("B", Decimal("0.5"), Decimal("2000")),
            ("C", Decimal("0.1"), Decimal("4000")),
        ):
            value = Decimal("100") + slope * Decimal(offset)
            adjusted[(asset, session)] = value
            raw[(asset, session)] = value
            volume[(asset, session)] = traded_volume

    execution = execute_representative_graph(
        adjusted_close=adjusted,
        close_raw=raw,
        volume_raw=volume,
    )
    final_session = start + timedelta(days=204)

    assert len(execution.features) == 9
    assert execution.features["return_continuation__w120"][("A", final_session)] == (
        Decimal("1.000000000000000000")
    )
    assert execution.features["low_illiquidity_quality__w20"][("C", final_session)] == (
        Decimal("1.000000000000000000")
    )
    assert execution.features["price_cross_above_ma__s1_l200"][("A", final_session)] == 0
    assert execution.aggregated_signal[("A", final_session)] is not None
    assert execution.aggregated_signal[("C", final_session)] is not None


def test_catalog_return_and_moving_average_variants_use_typed_node_dispatch() -> None:
    catalog = load_catalog_release(
        PROJECT_ROOT / "v0.22/catalogs/releases/catalog_release.v0.22.6.json"
    ).bundle.processing
    supported = tuple(
        node
        for node in catalog.nodes
        if node.implementation_key
        in {TOTAL_RETURN_IMPLEMENTATION, MOVING_AVERAGE_RATIO_IMPLEMENTATION}
    )
    assert len(supported) == 10
    start = date(2024, 1, 1)
    close: Panel = {
        (asset, start + timedelta(days=offset)): Decimal(100 + offset + ordinal)
        for offset in range(300)
        for ordinal, asset in enumerate(("A", "B"))
    }

    for node in supported:
        execution = execute_catalog_node(
            node.implementation_key,
            parameters=node.parameters,
            input_ports={"close_adj": close},
        )
        expected_ports = {
            item.port_key for item in node.ports if item.direction == "output"
        }
        assert set(execution.output_ports) == expected_ports
        assert all(
            panel[("A", start + timedelta(days=299))] is not None
            for panel in execution.output_ports.values()
        )


def test_catalog_amihud_node_dispatches_three_atomic_outputs() -> None:
    session0 = date(2026, 1, 1)
    sessions = (session0, session0 + timedelta(days=1))
    adjusted = {("A", sessions[0]): Decimal("100"), ("A", sessions[1]): Decimal("102")}
    close = {("A", sessions[0]): Decimal("50"), ("A", sessions[1]): Decimal("51")}
    volume = {("A", sessions[0]): Decimal("1000"), ("A", sessions[1]): Decimal("2000")}

    result = execute_catalog_node(
        AMIHUD_DAILY_PRIMITIVES_IMPLEMENTATION,
        parameters={},
        input_ports={
            "close_adj": adjusted,
            "close_raw": close,
            "volume_raw": volume,
        },
    )

    assert set(result.output_ports) == {
        "simple_return",
        "dollar_volume",
        "daily_price_impact",
    }
    assert result.output_ports["simple_return"][("A", sessions[1])] == Decimal("0.02")
    assert result.output_ports["dollar_volume"][("A", sessions[1])] == Decimal("102000")


def test_catalog_stage2_nodes_dispatch_from_intermediate_panels() -> None:
    start = date(2026, 1, 1)
    ratio = {
        ("A", start): Decimal("-0.01"),
        ("A", start + timedelta(days=1)): Decimal("0.01"),
    }
    impact = {
        ("A", start + timedelta(days=offset)): Decimal(offset + 1)
        for offset in range(20)
    }

    cross = execute_catalog_node(
        PRICE_CROSS_ABOVE_MA_IMPLEMENTATION,
        parameters={"short_window": 1, "long_window": 200},
        input_ports={"ma_ratio": ratio},
    )
    rolling = execute_catalog_node(
        AMIHUD_ILLIQUIDITY_IMPLEMENTATION,
        parameters={"window": 20},
        input_ports={"daily_price_impact": impact},
    )

    assert cross.output_ports["event_score"][("A", start + timedelta(days=1))] == 1
    assert rolling.output_ports["rolling_mean_impact"][
        ("A", start + timedelta(days=19))
    ] == Decimal("10.5")


def test_catalog_stage3_dispatches_directional_cross_sectional_signal() -> None:
    session = date(2026, 1, 2)
    result = execute_catalog_node(
        CONTINUOUS_CROSS_SECTIONAL_SIGNAL_IMPLEMENTATION,
        parameters={"window": 120, "direction": 1},
        input_ports={
            "feature": {
                ("A", session): Decimal("3"),
                ("B", session): Decimal("2"),
                ("C", session): Decimal("1"),
            }
        },
    )

    assert result.output_ports["signal_score"][("A", session)] == Decimal(
        "1.000000000000000000"
    )
    assert result.output_ports["signal_score"][("C", session)] == Decimal(
        "-1.000000000000000000"
    )


def test_catalog_lagged_return_and_drawdown_variants_use_typed_dispatch() -> None:
    catalog = load_catalog_release(
        PROJECT_ROOT / "v0.22/catalogs/releases/catalog_release.v0.22.6.json"
    ).bundle.processing
    supported = tuple(
        node
        for node in catalog.nodes
        if node.implementation_key
        in {LAGGED_RETURN_IMPLEMENTATION, MAXIMUM_DRAWDOWN_IMPLEMENTATION}
    )
    assert len(supported) == 4
    start = date(2024, 1, 1)
    close: Panel = {
        ("A", start + timedelta(days=offset)): Decimal(100 + offset)
        for offset in range(300)
    }
    close[("A", start + timedelta(days=250))] = Decimal("75")

    for node in supported:
        execution = execute_catalog_node(
            node.implementation_key,
            parameters=node.parameters,
            input_ports={"close_adj": close},
        )
        assert set(execution.output_ports) == {"factor_value"}
        assert execution.output_ports["factor_value"][
            ("A", start + timedelta(days=299))
        ] is not None


def test_catalog_volatility_variants_use_typed_dispatch() -> None:
    catalog = load_catalog_release(
        PROJECT_ROOT / "v0.22/catalogs/releases/catalog_release.v0.22.6.json"
    ).bundle.processing
    supported = tuple(
        node
        for node in catalog.nodes
        if node.implementation_key
        in {REALIZED_VOLATILITY_IMPLEMENTATION, DOWNSIDE_DEVIATION_IMPLEMENTATION}
    )
    assert len(supported) == 4
    start = date(2024, 1, 1)
    close: Panel = {
        ("A", start + timedelta(days=offset)): Decimal(100 + offset + offset % 7)
        for offset in range(100)
    }

    for node in supported:
        execution = execute_catalog_node(
            node.implementation_key,
            parameters=node.parameters,
            input_ports={"close_adj": close},
        )
        final_value = execution.output_ports["factor_value"][
            ("A", start + timedelta(days=99))
        ]
        assert final_value is not None
        assert final_value >= 0


def test_catalog_relative_dollar_volume_variants_use_typed_dispatch() -> None:
    catalog = load_catalog_release(
        PROJECT_ROOT / "v0.22/catalogs/releases/catalog_release.v0.22.6.json"
    ).bundle.processing
    supported = tuple(
        node
        for node in catalog.nodes
        if node.implementation_key == RELATIVE_DOLLAR_VOLUME_IMPLEMENTATION
    )
    assert len(supported) == 2
    start = date(2024, 1, 1)
    close = {
        ("A", start + timedelta(days=offset)): Decimal(100 + offset)
        for offset in range(80)
    }
    volume = {
        ("A", start + timedelta(days=offset)): Decimal(1000 + offset * 10)
        for offset in range(80)
    }

    for node in supported:
        execution = execute_catalog_node(
            node.implementation_key,
            parameters=node.parameters,
            input_ports={"close_raw": close, "volume_raw": volume},
        )
        assert execution.output_ports["factor_value"][
            ("A", start + timedelta(days=79))
        ] is not None


def test_catalog_return_distribution_variants_use_typed_dispatch() -> None:
    catalog = load_catalog_release(
        PROJECT_ROOT / "v0.22/catalogs/releases/catalog_release.v0.22.6.json"
    ).bundle.processing
    supported = tuple(
        node
        for node in catalog.nodes
        if node.implementation_key
        in {RETURN_SKEWNESS_IMPLEMENTATION, RETURN_EXCESS_KURTOSIS_IMPLEMENTATION}
    )
    assert len(supported) == 4
    start = date(2024, 1, 1)
    close: Panel = {
        ("A", start + timedelta(days=offset)): Decimal(
            100 + offset + (offset % 11) ** 2 / 10
        )
        for offset in range(280)
    }

    for node in supported:
        execution = execute_catalog_node(
            node.implementation_key,
            parameters=node.parameters,
            input_ports={"close_adj": close},
        )
        assert execution.output_ports["factor_value"][
            ("A", start + timedelta(days=279))
        ] is not None


def test_catalog_rsi_and_ppo_variants_use_typed_dispatch() -> None:
    catalog = load_catalog_release(
        PROJECT_ROOT / "v0.22/catalogs/releases/catalog_release.v0.22.6.json"
    ).bundle.processing
    supported = tuple(
        node
        for node in catalog.nodes
        if node.implementation_key in {RSI_WILDER_IMPLEMENTATION, PPO_HISTOGRAM_IMPLEMENTATION}
    )
    assert len(supported) == 2
    start = date(2024, 1, 1)
    close: Panel = {
        ("A", start + timedelta(days=offset)): Decimal(
            100 + offset / 2 + (offset % 9) * 2
        )
        for offset in range(100)
    }

    for node in supported:
        execution = execute_catalog_node(
            node.implementation_key,
            parameters=node.parameters,
            input_ports={"close_adj": close},
        )
        assert execution.output_ports["factor_value"][
            ("A", start + timedelta(days=99))
        ] is not None


def test_all_compat_signal_adapters_dispatch_catalog_nodes() -> None:
    catalog = load_catalog_release(
        PROJECT_ROOT / "v0.22/catalogs/releases/catalog_release.v0.22.6.json"
    ).bundle.processing
    implementations = {
        COMPAT_CONTINUOUS_IMPLEMENTATION,
        THRESHOLD_STATE_IMPLEMENTATION,
        CROSSOVER_EVENT_IMPLEMENTATION,
    }
    supported = tuple(
        node for node in catalog.nodes if node.implementation_key in implementations
    )
    assert len(supported) == 48
    start = date(2025, 1, 1)
    feature: Panel = {
        (asset, start + timedelta(days=offset)): Decimal(offset + ordinal - 2)
        for offset in range(3)
        for ordinal, asset in enumerate(("A", "B", "C"))
    }

    for node in supported:
        execution = execute_catalog_node(
            node.implementation_key,
            parameters=node.parameters,
            input_ports={"feature": feature},
        )
        expected_ports = {
            port.port_key for port in node.ports if port.direction == "output"
        }
        assert set(execution.output_ports) == expected_ports
