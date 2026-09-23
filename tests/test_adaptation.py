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
