# Ported from basnijholt/adaptive-lighting (Apache-2.0). See NOTICE.
from __future__ import annotations

import math


def find_a_b(x1: float, x2: float, y1: float, y2: float) -> tuple[float, float]:
    """Compute the values of 'a' and 'b' for a scaled and shifted tanh function.

    Given two points (x1, y1) and (x2, y2), this function calculates the coefficients 'a' and 'b'
    for a tanh function of the form y = 0.5 * (tanh(a * (x - b)) + 1) that passes through these points.

    The derivation is as follows:

    1. Start with the equation of the tanh function:
       y = 0.5 * (tanh(a * (x - b)) + 1)

    2. Rearrange the equation to isolate tanh:
       tanh(a * (x - b)) = 2*y - 1

    3. Take the inverse tanh (or artanh) on both sides to solve for 'a' and 'b':
       a * (x - b) = artanh(2*y - 1)

    4. Plug in the points (x1, y1) and (x2, y2) to get two equations.
       Using these, we can solve for 'a' and 'b' as:
       a = (artanh(2*y2 - 1) - artanh(2*y1 - 1)) / (x2 - x1)
       b = x1 - (artanh(2*y1 - 1) / a)

    Parameters
    ----------
    x1
        x-coordinate of the first point.
    x2
        x-coordinate of the second point.
    y1
        y-coordinate of the first point (should be between 0 and 1).
    y2
        y-coordinate of the second point (should be between 0 and 1).

    Returns
    -------
    a
        Coefficient 'a' for the tanh function.
    b
        Coefficient 'b' for the tanh function.

    Notes
    -----
    The values of y1 and y2 should lie between 0 and 1, inclusive.

    """
    a = (math.atanh(2 * y2 - 1) - math.atanh(2 * y1 - 1)) / (x2 - x1)
    b = x1 - (math.atanh(2 * y1 - 1) / a)
    return a, b


def scaled_tanh(
    x: float,
    x1: float,
    x2: float,
    y1: float = 0.05,
    y2: float = 0.95,
    y_min: float = 0.0,
    y_max: float = 100.0,
) -> float:
    """Apply a scaled and shifted tanh function to a given input.

    This function represents a transformation of the tanh function that scales and shifts
    the output to lie between y_min and y_max. For values of 'x' close to 'x1' and 'x2'
    (used to calculate 'a' and 'b'), the output of this function will be close to 'y_min'
    and 'y_max', respectively.

    The equation of the function is as follows:
    y = y_min + (y_max - y_min) * 0.5 * (tanh(a * (x - b)) + 1)

    Parameters
    ----------
    x
        The input to the function.
    x1
        x-coordinate of the first point.
    x2
        x-coordinate of the second point.
    y1
        y-coordinate of the first point (should be between 0 and 1). Defaults to 0.05.
    y2
        y-coordinate of the second point (should be between 0 and 1). Defaults to 0.95.
    y_min
        The minimum value of the output range. Defaults to 0.
    y_max
        The maximum value of the output range. Defaults to 100.

    Returns
    -------
        float: The output of the function, which lies in the range [y_min, y_max].

    """
    a, b = find_a_b(x1, x2, y1, y2)
    return y_min + (y_max - y_min) * 0.5 * (math.tanh(a * (x - b)) + 1)


def lerp(x: float, x1: float, x2: float, y1: float, y2: float) -> float:
    """Linearly interpolate between two values."""
    return y1 + (x - x1) * (y2 - y1) / (x2 - x1)


def clamp(value: float, minimum: float, maximum: float) -> float:
    """Clamp value between minimum and maximum.

    `minimum` is not assumed to be <= `maximum`: a user may intentionally
    configure `min_brightness > max_brightness` (or the equivalent for color
    temperature) for an inverted timescale (#1421). Sort the bounds first so
    that case clamps against the real lower/upper bound instead of
    collapsing to `minimum` for every input.
    """
    low, high = (minimum, maximum) if minimum <= maximum else (maximum, minimum)
    return max(low, min(value, high))
