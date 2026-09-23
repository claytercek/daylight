"""Tests for the pure sun-timing / curve math in color_and_brightness.py.

No `hass` fixture, no `enable_custom_integrations` — this module has zero HA
runtime dependency, so these tests are plain Python against pure functions
and a frozen dataclass.
"""

import pytest

from custom_components.daylight.color_and_brightness import (
    clamp,
    lerp,
    scaled_tanh,
)


def test_lerp_midpoint() -> None:
    assert lerp(5, x1=0, x2=10, y1=0, y2=100) == 50


def test_clamp_above_range() -> None:
    assert clamp(150, 0, 100) == 100


def test_clamp_inverted_bounds() -> None:
    # upstream explicitly supports minimum > maximum (inverted timescale)
    assert clamp(5, 100, 0) == 5


def test_scaled_tanh_endpoints_approach_y_min_and_y_max() -> None:
    # at x1/x2 the tanh curve is defined to sit at y1/y2 fractions of [y_min, y_max]
    low = scaled_tanh(-10, x1=-10, x2=10, y1=0.05, y2=0.95, y_min=0.0, y_max=1.0)
    high = scaled_tanh(10, x1=-10, x2=10, y1=0.05, y2=0.95, y_min=0.0, y_max=1.0)
    mid = scaled_tanh(0, x1=-10, x2=10, y1=0.05, y2=0.95, y_min=0.0, y_max=1.0)
    assert low == pytest.approx(0.05)
    assert high == pytest.approx(0.95)
    assert mid == pytest.approx(0.5)
