"""Tests for `compute_turn_on_kwargs`.

`compute_turn_on_kwargs` is pure: no `hass`, no HA imports. Expected values
below are hand-computed linear interpolations, never recomputed with the
module's own `lerp` -- that would let a broken `lerp` call pass its own test.
"""

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
