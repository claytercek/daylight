"""Adaptation on/off switch for daylight targets.

One `AdaptSwitch` per target subentry -- there are no brightness/color/sleep
sub-switches. This module is the adapter layer: it owns no adaptation maths
(`adaptation.py`) and no manual-control state machine (`target.py`), only the
wiring between them, the coordinator, and hass.
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry, ConfigSubentry
from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import Context, Event, HomeAssistant, State, callback
from homeassistant.helpers import area_registry, device_registry, entity_registry
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.event import (
    EventStateChangedData,
    async_track_state_change_event,
)
from homeassistant.helpers.restore_state import ExtraStoredData, RestoreEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .adaptation import compute_turn_on_kwargs
from .coordinator import DayCoordinator, DayState
from .target import Target, TargetConfig

TARGET_SUBENTRY_TYPE = "target"
_LOGGER = logging.getLogger(__name__)

# The exact keys `adaptation.compute_turn_on_kwargs` can emit. Only needed to
# split one combined command into two when `separate_turn_on_commands` is set
# -- `adaptation.py` stays a single combined-kwargs function.
BRIGHTNESS_KWARG = "brightness_pct"
COLOR_TEMP_KWARG = "color_temp_kelvin"
TRANSITION_KWARG = "transition"
CONTROL_ATTRIBUTES = frozenset(
    {
        "brightness",
        "color_mode",
        "color_temp",
        "color_temp_kelvin",
        "effect",
        "hs_color",
        "rgb_color",
        "rgbw_color",
        "rgbww_color",
        "white",
        "xy_color",
    }
)


def _has_control_change(old_state: State, new_state: State) -> bool:
    """Ignore metadata updates and attributes reported while the light is off."""
    if old_state.state != new_state.state:
        return True
    return new_state.state == STATE_ON and any(
        old_state.attributes.get(key) != new_state.attributes.get(key)
        for key in CONTROL_ATTRIBUTES
    )


@dataclasses.dataclass(frozen=True)
class TargetSettings:
    """A target subentry's config, unpacked from its raw `data` dict."""

    entities: tuple[str, ...]
    areas: tuple[str, ...]
    min_brightness_pct: int
    max_brightness_pct: int
    min_color_temp_kelvin: int
    max_color_temp_kelvin: int
    transition: float
    adapt_only_on_state_change: bool
    separate_turn_on_commands: bool
    send_split_delay: float

    @classmethod
    def from_subentry_data(cls, data) -> TargetSettings:
        """Build settings from a target subentry's `data` mapping."""
        return cls(
            entities=tuple(data.get("entities", ())),
            areas=tuple(data.get("areas", ())),
            min_brightness_pct=data["min_brightness_pct"],
            max_brightness_pct=data["max_brightness_pct"],
            min_color_temp_kelvin=data["min_color_temp_kelvin"],
            max_color_temp_kelvin=data["max_color_temp_kelvin"],
            transition=data["transition"],
            adapt_only_on_state_change=data["adapt_only_on_state_change"],
            separate_turn_on_commands=data["separate_turn_on_commands"],
            send_split_delay=data["send_split_delay"],
        )


@dataclasses.dataclass(frozen=True)
class TargetStoredData(ExtraStoredData):
    """Carries a target's manual-control state across a reload or restart."""

    target_data: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        """Return `Target.to_dict()` output, as HA will JSON-serialize it."""
        return self.target_data


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add one adaptation switch per target subentry of this hub entry."""
    coordinator = entry.runtime_data
    for subentry in entry.subentries.values():
        if subentry.subentry_type != TARGET_SUBENTRY_TYPE:
            continue
        async_add_entities(
            [AdaptSwitch(coordinator, subentry)],
            config_subentry_id=subentry.subentry_id,
        )


class AdaptSwitch(CoordinatorEntity[DayCoordinator], SwitchEntity, RestoreEntity):
    """Turns adaptation on/off for one target's member lights."""

    def __init__(self, coordinator: DayCoordinator, subentry: ConfigSubentry) -> None:
        super().__init__(coordinator)
        self._settings = TargetSettings.from_subentry_data(subentry.data)
        self._target = Target(
            TargetConfig(
                manual_control_reset_minutes=subentry.data[
                    "manual_control_reset_minutes"
                ]
            )
        )
        self._attr_unique_id = f"{subentry.subentry_id}_adapt"
        self._attr_name = f"{subentry.title} adapt"
        self._attr_is_on = False
        self._send_tasks: dict[str, asyncio.Task[None]] = {}
        self._members: tuple[str, ...] = ()
        self._unsubscribe_members = None

    @property
    def extra_restore_state_data(self) -> TargetStoredData:
        """Persist which members are hand-controlled, alongside on/off."""
        return TargetStoredData(self._target.to_dict())

    async def async_added_to_hass(self) -> None:
        """Restore on/off plus manual-control state, defaulting to off."""
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state is not None:
            self._attr_is_on = last_state.state == "on"

        # Only `.as_dict()` is used: on a same-process reload this is our own
        # `TargetStoredData`, but after a restart it is a `RestoredExtraData`
        # wrapper around the JSON that was written out.
        last_extra = await self.async_get_last_extra_data()
        if last_extra is not None:
            self._target = Target.from_dict(self._target.config, last_extra.as_dict())

        self._refresh_members(adapt_new=False)
        if self._settings.areas:
            for event_type in (
                entity_registry.EVENT_ENTITY_REGISTRY_UPDATED,
                device_registry.EVENT_DEVICE_REGISTRY_UPDATED,
                area_registry.EVENT_AREA_REGISTRY_UPDATED,
            ):
                self.async_on_remove(
                    self.hass.bus.async_listen(event_type, self._registry_changed)
                )
        self.async_on_remove(self._remove_member_listener)
        self.async_on_remove(self._cancel_pending_sends)

    @callback
    def _remove_member_listener(self) -> None:
        if self._unsubscribe_members is not None:
            self._unsubscribe_members()
            self._unsubscribe_members = None

    @callback
    def _registry_changed(self, event: Event) -> None:
        self._refresh_members()

    @callback
    def _refresh_members(self, *, adapt_new: bool = True) -> None:
        """Resolve areas to light entities and rebind state tracking on changes."""
        area_members = ()
        if self._settings.areas:
            registry = entity_registry.async_get(self.hass)
            areas = set(self._settings.areas)
            area_members = sorted(
                entry.entity_id
                for entry in registry.entities.values()
                if entry.domain == "light"
                and entry.disabled_by is None
                and entity_registry.async_get_effective_area_id(self.hass, entry)
                in areas
            )
        resolved = tuple(dict.fromkeys((*self._settings.entities, *area_members)))
        if resolved == self._members:
            return
        previous = set(self._members)
        members = set(resolved)
        self._remove_member_listener()
        for removed in previous - members:
            self._cancel_send(removed)
        self._members = resolved
        if resolved:
            self._unsubscribe_members = async_track_state_change_event(
                self.hass, resolved, self._async_member_state_changed
            )
        if adapt_new and self.is_on and members - previous:
            now = dt_util.utcnow()
            day_state = self.coordinator.compute_day_state(now)
            for entity_id in members - previous:
                if not self._target.is_manual(entity_id, now=now.timestamp()):
                    self._async_adapt(entity_id, day_state, snap=True)

    @callback
    def _async_member_state_changed(
        self, event: Event[EventStateChangedData]
    ) -> None:
        """Feed member control changes into the manual-control tracker.

        A member hand-dimmed while this switch is off stays flagged manual
        until adaptation resumes. Availability and metadata-only changes are
        skipped: neither is evidence of a person changing light controls.
        Without the availability skip, a bare reconnect flags manual on its
        `on->unavailable` leg, and `unavailable->on` cannot clear that flag.

        Detection is purely `state_changed`-based (never service-call-based).
        Area selections are resolved to member entity ids before subscribing.
        """
        entity_id = event.data["entity_id"]
        old_state = event.data["old_state"]
        new_state = event.data["new_state"]
        availability_change = (
            old_state is None
            or new_state is None
            or old_state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN)
            or new_state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN)
        )
        if not availability_change and _has_control_change(old_state, new_state):
            manual = self._target.observe_state_change(
                entity_id,
                event.context.id,
                timestamp=event.time_fired.timestamp(),
                parent_id=event.context.parent_id,
                user_id=event.context.user_id,
            )
            if manual:
                self._cancel_send(entity_id)
        if new_state is None or new_state.state != STATE_ON:
            self._cancel_send(entity_id)

        # Deliberately not an `else`: a light reconnecting straight back to
        # `on` still needs its immediate adaptation correction below. Every
        # edge into `on` reaches the block below -- off->on, unavailable->on
        # and unknown->on alike -- but only off->on clears a manual flag.
        if (
            old_state is None
            or new_state is None
            or new_state.state != STATE_ON
            or old_state.state == STATE_ON
        ):
            return
        if not self.is_on:
            # Whatever the observation above flagged stands: turning this
            # switch on later is what resolves it.
            return

        # Clearing and adapting are separate gestures with separate triggers.
        # Only an explicit off->on is a person saying "resume", so only that
        # forgives a hand-dim -- a reconnect must not launder the flag away.
        # Known limitation: `on (manual) -> off -> unavailable -> on` reaches
        # here with `old_state` of `unavailable`, so the user's genuine off is
        # not honoured and the member stays manual. Accepted rather than
        # tracked: it needs a blip landing in the window between the off and
        # the light's own on-report.
        if old_state.state == STATE_OFF:
            self._target.clear_manual_flag(entity_id)

        # Adapting, by contrast, is warranted by *any* entry into `on` -- the
        # member is showing whatever it was last left at, which is stale.
        # Gated only on the flag as it stands after the clear above.
        now = dt_util.utcnow()
        if self._target.is_manual(entity_id, now=now.timestamp()):
            return
        # Freshly computed, never `coordinator.data`: the last poll can be
        # most of an interval old by the time a light is switched on.
        self._async_adapt(entity_id, self.coordinator.compute_day_state(now), snap=True)

    async def async_turn_on(self, **kwargs) -> None:
        """Resume adaptation for this target.

        Resuming forgives every manual flag raised while adaptation was
        paused -- including the ones a scene or a hand-dim raised precisely
        *because* `_async_member_state_changed` keeps observing while this
        switch is off. A member that is still genuinely hand-controlled
        re-flags on its next foreign state change.

        Forgiving alone is not enough: the members are still sitting at
        whatever the scene left them at. The one-shot correction below runs
        regardless of `adapt_only_on_state_change`, which gates the periodic
        tick only -- in that mode it is the sole thing that can undo a scene.
        """
        was_off = not self.is_on
        self._attr_is_on = True
        if was_off:
            self._target.clear_all_manual_flags()
            now = dt_util.utcnow()
            # Freshly computed, never `coordinator.data`: the last poll can be
            # most of an interval old by the time adaptation is resumed.
            day_state = self.coordinator.compute_day_state(now)
            for entity_id in self._members:
                self._async_adapt(entity_id, day_state, snap=True)
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs) -> None:
        """Pause adaptation for this target."""
        self._attr_is_on = False
        self._cancel_pending_sends()
        self.async_write_ha_state()

    @callback
    def _cancel_send(self, entity_id: str) -> None:
        """Stop a member's pending command sequence."""
        task = self._send_tasks.pop(entity_id, None)
        if task is not None:
            task.cancel()

    @callback
    def _cancel_pending_sends(self) -> None:
        """Stop pending commands when adaptation pauses or the entity unloads."""
        for entity_id in tuple(self._send_tasks):
            self._cancel_send(entity_id)

    @callback
    def _handle_coordinator_update(self) -> None:
        """Push adapted values to every non-manual member on each poll tick."""
        super()._handle_coordinator_update()
        if not self.is_on or self._settings.adapt_only_on_state_change:
            return
        # `coordinator.data` is only safe to read here: this fires directly
        # off a fresh poll, so it cannot be stale.
        day_state = self.coordinator.data
        if day_state is None:
            return
        now = dt_util.utcnow().timestamp()
        for entity_id in self._members:
            if self._target.is_manual(entity_id, now=now):
                continue
            self._async_adapt(entity_id, day_state)

    @callback
    def _async_adapt(
        self, entity_id: str, day_state: DayState, *, snap: bool = False
    ) -> None:
        """Queue one member's adaptation command.

        Adapting pushes values onto a light that is already on; it never
        turns one on. Guarding here rather than in each caller covers the
        periodic tick and the resume pass -- either of which would otherwise
        switch off members back on -- and is a harmless no-op for the
        not-on->on correction, which has already established the member is on.
        `snap` bypasses the fade when a light comes on, joins a target, or
        adaptation resumes.
        """
        # Finish the current split before considering another poll. Replacing
        # it on every tick could postpone the color command indefinitely when
        # the poll interval is shorter than the configured split delay.
        if entity_id in self._send_tasks:
            return
        state = self.hass.states.get(entity_id)
        if state is None or state.state != STATE_ON:
            return
        min_color_temp = state.attributes.get("min_color_temp_kelvin")
        max_color_temp = state.attributes.get("max_color_temp_kelvin")
        kwargs = compute_turn_on_kwargs(
            supported_color_modes=set(
                state.attributes.get("supported_color_modes") or []
            ),
            brightness_factor=day_state.brightness_factor,
            color_factor=day_state.color_factor,
            min_brightness_pct=self._settings.min_brightness_pct,
            max_brightness_pct=self._settings.max_brightness_pct,
            min_color_temp_kelvin=self._settings.min_color_temp_kelvin,
            max_color_temp_kelvin=self._settings.max_color_temp_kelvin,
            transition=0.0 if snap else self._settings.transition,
            device_min_color_temp_kelvin=(
                min_color_temp if isinstance(min_color_temp, int) else None
            ),
            device_max_color_temp_kelvin=(
                max_color_temp if isinstance(max_color_temp, int) else None
            ),
        )
        if not kwargs:
            return
        context = Context()
        task = self.hass.async_create_task(self._async_send(entity_id, kwargs, context))
        self._send_tasks[entity_id] = task
        task.add_done_callback(lambda done: self._finish_send(entity_id, done))

    @callback
    def _finish_send(self, entity_id: str, task: asyncio.Task[None]) -> None:
        if self._send_tasks.get(entity_id) is task:
            del self._send_tasks[entity_id]

    async def _async_send(
        self, entity_id: str, kwargs: dict[str, Any], context: Context
    ) -> None:
        """Issue the `light.turn_on` call(s) for one member."""
        # Some bulbs drop one attribute when both arrive together; splitting
        # is pure I/O ordering, so it lives here rather than in adaptation.py.
        if self._settings.separate_turn_on_commands:
            shared = {
                key: value for key, value in kwargs.items() if key == TRANSITION_KWARG
            }
            parts = [
                {**shared, key: kwargs[key]}
                for key in (BRIGHTNESS_KWARG, COLOR_TEMP_KWARG)
                if key in kwargs
            ]
        else:
            parts = [kwargs]

        if not parts or not self._can_send(entity_id):
            return
        # Record immediately before dispatch, after task scheduling. A split
        # needs a window long enough for its delayed second report; a one-part
        # command needs only its transition and reporting grace.
        transition_seconds = kwargs[TRANSITION_KWARG]
        if len(parts) > 1:
            transition_seconds += self._settings.send_split_delay
        previous_suppress_until = self._target.record_command(
            entity_id,
            context.id,
            transition_seconds=transition_seconds,
            now=dt_util.utcnow().timestamp(),
        )
        for index, part in enumerate(parts):
            if index:
                await asyncio.sleep(self._settings.send_split_delay)
                if not self._can_send(entity_id):
                    return
            try:
                await self._async_turn_on(entity_id, part, context)
            except Exception:
                if index == 0:
                    self._target.discard_command(
                        entity_id, context.id, previous_suppress_until
                    )
                _LOGGER.exception("Unable to adapt %s", entity_id)
                return

    def _can_send(self, entity_id: str) -> bool:
        """Check the live state before either part of a queued command."""
        state = self.hass.states.get(entity_id)
        return (
            self.is_on is True
            and state is not None
            and state.state == STATE_ON
            and entity_id in self._members
            and not self._target.is_manual(
                entity_id, now=dt_util.utcnow().timestamp()
            )
        )

    async def _async_turn_on(
        self, entity_id: str, data: dict[str, Any], context: Context
    ) -> None:
        await self.hass.services.async_call(
            "light", "turn_on", {"entity_id": entity_id, **data}, context=context
        )
