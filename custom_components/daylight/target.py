"""Manual-control ("hand-dim") detection for a daylight target.

`Target` decides whether a light this integration adapts has been taken over by
a human -- a dimmer switch, a voice command, a tap on the wall panel -- and
should be left alone until it is cycled off and on again.

This module deliberately has **no Home Assistant dependency**: no `hass`, no
`datetime.now()`, no imports from `homeassistant`. Every timestamp, entity id,
context id and attribute dict is passed in by the caller. That keeps the state
machine a pure, deterministic object which can be tested without the HA test
harness at all. A thin adapter (out of scope here) wires real hass events into
it.

Out of scope for this module, handled by that adapter:

* Resolving `area_id`/`device_id`/`label_id`/`entity_id: "all"` service targets
  into concrete entity ids -- `Target` only ever consumes resolved entity ids.
* Listening to hass events, calling `light.turn_on`, and any `RestoreEntity` or
  switch-entity plumbing.

Timestamps are float seconds on an arbitrary epoch (the adapter passes
`event.time_fired.timestamp()`).
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Any

# Bound on the per-entity ring of context ids this target created. A target
# issues at most a couple of commands per adaptation interval, and only the
# most recent ones can still be echoed back by a light, so a short ring is
# plenty; the bound is what stops the buffer growing over a long session.
OWN_CONTEXT_MAXLEN = 16

# Seconds added on top of a command's `transition` before state reports stop
# counting as ours. HA only propagates our context onto state changes for
# `CONTEXT_RECENT_TIME_SECONDS` (5, see homeassistant/helpers/entity.py), so a
# post-transition report from a slow Zigbee light carries a *fresh* context and
# would otherwise look manual. Deliberate, adjustable constant -- there is no
# principled value, just enough slack for reporting lag.
SUPPRESSION_GRACE_SECONDS = 2.0


@dataclass(frozen=True)
class TargetConfig:
    """Tuning knobs for a target's manual-control detection."""

    manual_control_reset_minutes: int = 0


@dataclass
class _EntityState:
    """Per-entity bookkeeping."""

    last_commanded: dict[str, Any] = field(default_factory=dict)
    own_context_ids: deque[str] = field(
        default_factory=lambda: deque(maxlen=OWN_CONTEXT_MAXLEN)
    )
    suppress_until: float = 0.0
    manual: bool = False
    # Refreshed on every manual observation, so repeated hand-dimming keeps
    # pushing the auto-reset deadline out rather than expiring mid-fiddle.
    manual_since: float | None = None


class Target:
    """Tracks which member lights are under manual control."""

    def __init__(self, config: TargetConfig) -> None:
        self.config = config
        self._entities: dict[str, _EntityState] = {}

    def _state(self, entity_id: str) -> _EntityState:
        state = self._entities.get(entity_id)
        if state is None:
            state = self._entities[entity_id] = _EntityState()
        return state

    def record_command(
        self,
        entity_id: str,
        context_id: str,
        attrs: dict[str, Any],
        transition_seconds: float = 0.0,
        *,
        now: float,
    ) -> None:
        """Register a command *about to be* issued for `entity_id`.

        Callers must call this **before** the `light.turn_on` service call, so
        that a state report can never race the bookkeeping that recognises it.
        """
        state = self._state(entity_id)
        state.last_commanded = dict(attrs)
        state.own_context_ids.append(context_id)
        state.suppress_until = now + transition_seconds + SUPPRESSION_GRACE_SECONDS

    def observe_state_change(
        self, entity_id: str, context_id: str, *, timestamp: float
    ) -> bool:
        """Feed in a state change; return whether it was flagged as manual."""
        state = self._state(entity_id)
        if context_id in state.own_context_ids or timestamp <= state.suppress_until:
            return False
        state.manual = True
        state.manual_since = timestamp
        return True

    def is_manual(self, entity_id: str, *, now: float) -> bool:
        """Whether `entity_id` is currently considered manually controlled."""
        state = self._entities.get(entity_id)
        if state is None or not state.manual:
            return False
        reset_seconds = self.config.manual_control_reset_minutes * 60
        if (
            reset_seconds > 0
            and state.manual_since is not None
            and now - state.manual_since >= reset_seconds
        ):
            self.clear_manual_flag(entity_id)
            return False
        return True

    def clear_manual_flag(self, entity_id: str) -> None:
        """Hand `entity_id` back to adaptive control."""
        state = self._entities.get(entity_id)
        if state is None:
            return
        state.manual = False
        state.manual_since = None

    def clear_all_manual_flags(self) -> None:
        """Hand every member back to adaptive control.

        The switch entity's off->on transition handler calls this.
        """
        for entity_id in self._entities:
            self.clear_manual_flag(entity_id)
