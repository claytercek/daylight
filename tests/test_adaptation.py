"""Tests for `compute_turn_on_kwargs`.

`compute_turn_on_kwargs` is pure: no `hass`, no HA imports. Expected values
below are hand-computed linear interpolations, never recomputed with the
module's own `lerp` -- that would let a broken `lerp` call pass its own test.
"""

import pytest

from custom_components.daylight.adaptation import compute_turn_on_kwargs


def test_brightness_only_when_color_temp_not_supported() -> None:
    """Only `rgb`/`hs`/etc. supported -> brightness-only kwargs, no color key."""
    result = compute_turn_on_kwargs(
        supported_color_modes={"rgb"},
        brightness_factor=0.25,
        color_factor=0.5,
        min_brightness_pct=20,
        max_brightness_pct=80,
        min_color_temp_kelvin=2000,
        max_color_temp_kelvin=6500,
        transition=2.5,
    )

    assert result == {"brightness_pct": 35, "transition": 2.5}


def test_color_temp_kelvin_included_when_supported() -> None:
    """`color_temp` in supported modes -> kelvin key added alongside brightness."""
    result = compute_turn_on_kwargs(
        supported_color_modes={"color_temp"},
        brightness_factor=0.25,
        color_factor=0.5,
        min_brightness_pct=20,
        max_brightness_pct=80,
        min_color_temp_kelvin=2000,
        max_color_temp_kelvin=6500,
        transition=1.0,
    )

    assert result == {
        "brightness_pct": 35,
        "color_temp_kelvin": 4250,
        "transition": 1.0,
    }


def test_factor_endpoints_map_to_configured_min_and_max() -> None:
    """0.0 -> min, 1.0 -> max for both brightness and color temp."""
    at_min = compute_turn_on_kwargs(
        supported_color_modes={"color_temp"},
        brightness_factor=0.0,
        color_factor=0.0,
        min_brightness_pct=20,
        max_brightness_pct=80,
        min_color_temp_kelvin=2000,
        max_color_temp_kelvin=6500,
        transition=0.0,
    )
    at_max = compute_turn_on_kwargs(
        supported_color_modes={"color_temp"},
        brightness_factor=1.0,
        color_factor=1.0,
        min_brightness_pct=20,
        max_brightness_pct=80,
        min_color_temp_kelvin=2000,
        max_color_temp_kelvin=6500,
        transition=0.0,
    )

    assert at_min == {
        "brightness_pct": 20,
        "color_temp_kelvin": 2000,
        "transition": 0.0,
    }
    assert at_max == {
        "brightness_pct": 80,
        "color_temp_kelvin": 6500,
        "transition": 0.0,
    }


@pytest.mark.parametrize("modes", [{"onoff"}, {"unknown"}, set()])
def test_lights_without_brightness_have_no_adaptation(modes: set[str]) -> None:
    """A turn_on for an on/off light would only turn it on again."""
    assert compute_turn_on_kwargs(
        supported_color_modes=modes,
        brightness_factor=0.5,
        color_factor=0.5,
        min_brightness_pct=20,
        max_brightness_pct=80,
        min_color_temp_kelvin=2000,
        max_color_temp_kelvin=6500,
        transition=1.0,
    ) == {}


def test_color_temp_is_clamped_to_device_range() -> None:
    """A configured range can extend beyond a bulb's advertised limits."""
    params = {
        "supported_color_modes": {"color_temp"},
        "brightness_factor": 0.5,
        "min_brightness_pct": 20,
        "max_brightness_pct": 80,
        "min_color_temp_kelvin": 2000,
        "max_color_temp_kelvin": 6500,
        "transition": 1.0,
        "device_min_color_temp_kelvin": 2700,
        "device_max_color_temp_kelvin": 5000,
    }
    low = compute_turn_on_kwargs(color_factor=0.0, **params)
    high = compute_turn_on_kwargs(color_factor=1.0, **params)

    assert low == {"brightness_pct": 50, "color_temp_kelvin": 2700, "transition": 1.0}
    assert high == {"brightness_pct": 50, "color_temp_kelvin": 5000, "transition": 1.0}


def test_mixed_color_modes_keep_brightness_and_clamp_color_temp() -> None:
    """An on/off capability alongside CT does not make the light on/off-only."""
    result = compute_turn_on_kwargs(
        supported_color_modes={"onoff", "color_temp", "rgb"},
        brightness_factor=0.25,
        color_factor=1.0,
        min_brightness_pct=20,
        max_brightness_pct=80,
        min_color_temp_kelvin=2000,
        max_color_temp_kelvin=6500,
        transition=2.5,
        device_max_color_temp_kelvin=5000,
    )

    assert result == {
        "brightness_pct": 35,
        "color_temp_kelvin": 5000,
        "transition": 2.5,
    }


def test_missing_device_bounds_preserve_configured_color_temp() -> None:
    """Missing attributes must not imply a narrower device range."""
    result = compute_turn_on_kwargs(
        supported_color_modes={"color_temp"},
        brightness_factor=0.25,
        color_factor=1.0,
        min_brightness_pct=20,
        max_brightness_pct=80,
        min_color_temp_kelvin=2000,
        max_color_temp_kelvin=6500,
        transition=1.0,
    )

    assert result["color_temp_kelvin"] == 6500
