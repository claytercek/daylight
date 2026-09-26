"""Turn `DayState` factors into `light.turn_on` service-call kwargs.

This module deliberately has **no Home Assistant dependency**: no `hass`, no
imports from `homeassistant`. The caller spreads the returned dict directly
into a `light.turn_on` service call, so the key spellings here must match
what that service schema expects exactly -- there is no translation layer on
the other side.
"""

from __future__ import annotations

from typing import Any

from custom_components.daylight.color_and_brightness import lerp

# Matches homeassistant.components.light.ColorMode.COLOR_TEMP's value.
# Not imported to keep this module free of any HA dependency.
COLOR_MODE_COLOR_TEMP = "color_temp"
COLOR_MODES_WITHOUT_BRIGHTNESS = {"onoff", "unknown"}


def compute_turn_on_kwargs(
    *,
    supported_color_modes: set[str],
    brightness_factor: float,
    color_factor: float,
    min_brightness_pct: int,
    max_brightness_pct: int,
    min_color_temp_kelvin: int,
    max_color_temp_kelvin: int,
    transition: float,
    device_min_color_temp_kelvin: int | None = None,
    device_max_color_temp_kelvin: int | None = None,
) -> dict[str, Any]:
    """Return supported `light.turn_on` kwargs, or empty if nothing can adapt."""
    if not supported_color_modes - COLOR_MODES_WITHOUT_BRIGHTNESS:
        return {}

    kwargs: dict[str, Any] = {
        "brightness_pct": round(
            lerp(
                brightness_factor,
                x1=0.0,
                x2=1.0,
                y1=min_brightness_pct,
                y2=max_brightness_pct,
            )
        ),
    }
    # Lights without color_temp (rgb/hs/xy/onoff/brightness-only) get no
    # color adaptation at all -- RGB color conversion was dropped as a
    # feature, matching the prior decision to drop upstream's
    # prefer_rgb_color option.
    if COLOR_MODE_COLOR_TEMP in supported_color_modes:
        color_temp = round(
            lerp(
                color_factor,
                x1=0.0,
                x2=1.0,
                y1=min_color_temp_kelvin,
                y2=max_color_temp_kelvin,
            )
        )
        if device_min_color_temp_kelvin is not None:
            color_temp = max(color_temp, device_min_color_temp_kelvin)
        if device_max_color_temp_kelvin is not None:
            color_temp = min(color_temp, device_max_color_temp_kelvin)
        kwargs["color_temp_kelvin"] = color_temp
    kwargs["transition"] = transition
    return kwargs
