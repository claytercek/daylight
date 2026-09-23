"""Adaptation on/off switch for daylight targets.

One `AdaptSwitch` per target subentry -- there are no brightness/color/sleep
sub-switches. This module is the adapter layer: it owns no adaptation maths
(`adaptation.py`) and no manual-control state machine (`target.py`), only the
wiring between them, the coordinator, and hass.
"""

from __future__ import annotations

import asyncio
import dataclasses
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry, ConfigSubentry
from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import Context, Event, HomeAssistant, callback
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

# The exact keys `adaptation.compute_turn_on_kwargs` can emit. Only needed to
# split one combined command into two when `separate_turn_on_commands` is set
# -- `adaptation.py` stays a single combined-kwargs function.
BRIGHTNESS_KWARG = "brightness_pct"
COLOR_TEMP_KWARG = "color_temp_kelvin"
TRANSITION_KWARG = "transition"


@dataclasses.dataclass(frozen=True)
class TargetSettings:
    """A target subentry's config, unpacked from its raw `data` dict."""

    entities: tuple[str, ...]
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
            entities=tuple(data["entities"]),
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

        self.async_on_remove(
            async_track_state_change_event(
                self.hass, self._settings.entities, self._async_member_state_changed
            )
        )

    @callback
    def _async_member_state_changed(
        self, event: Event[EventStateChangedData]
    ) -> None:
        """Feed every member state change into the manual-control tracker.

        Every change except an availability one -- a member hand-dimmed while
        this switch is off must still come back flagged manual so that turning
        the switch on is what resolves it, but a light dropping off the network
        and coming back is not a person touching it. That skip is load-bearing
        rather than tidiness: without it a bare reconnect flags manual on its
        `on->unavailable` leg, and the following `unavailable->on` then neither
        clears that flag (only off->on does) nor adapts (the clear-vs-adapt
        split below gates adapting on it) -- stuck manual for good.

        There is deliberately no service-call target resolution anywhere in
        this module: detection is purely `state_changed`-based (never
        `EVENT_CALL_SERVICE`), and a target's configured `entities` only ever
        holds literal entity ids -- no `area_id`/`device_id`/`label_id`
        targeting exists in this design, so there is nothing to resolve.
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
        if not availability_change:
            self._target.observe_state_change(
                entity_id, event.context.id, timestamp=event.time_fired.timestamp()
            )

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
        self._async_adapt(
            entity_id, self.coordinator.compute_day_state(now), now.timestamp()
        )

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
        if not self.is_on:
            self._target.clear_all_manual_flags()
            now = dt_util.utcnow()
            # Freshly computed, never `coordinator.data`: the last poll can be
            # most of an interval old by the time adaptation is resumed.
            day_state = self.coordinator.compute_day_state(now)
            for entity_id in self._settings.entities:
                self._async_adapt(entity_id, day_state, now.timestamp())
        self._attr_is_on = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs) -> None:
        """Pause adaptation for this target."""
        self._attr_is_on = False
        self.async_write_ha_state()

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
        for entity_id in self._settings.entities:
            if self._target.is_manual(entity_id, now=now):
                continue
            self._async_adapt(entity_id, day_state, now)

    @callback
    def _async_adapt(self, entity_id: str, day_state: DayState, now: float) -> None:
        """Record and dispatch one member's adaptation command.

        Adapting pushes values onto a light that is already on; it never
        turns one on. Guarding here rather than in each caller covers the
        periodic tick and the resume pass -- either of which would otherwise
        switch off members back on -- and is a harmless no-op for the
        not-on->on correction, which has already established the member is on.
        """
        state = self.hass.states.get(entity_id)
        if state is None or state.state != STATE_ON:
            return
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
            transition=self._settings.transition,
        )
        context = Context()
        # Strictly before the service call, and synchronously, so the
        # resulting state report can never race this bookkeeping.
        self._target.record_command(
            entity_id,
            context.id,
            kwargs,
            transition_seconds=self._settings.transition,
            now=now,
        )
        self.hass.async_create_task(self._async_send(entity_id, kwargs, context))

    async def _async_send(
        self, entity_id: str, kwargs: dict[str, Any], context: Context
    ) -> None:
        """Issue the `light.turn_on` call(s) for one member."""
        if not self._settings.separate_turn_on_commands:
            await self._async_turn_on(entity_id, kwargs, context)
            return

        # Some bulbs drop one attribute when both arrive together; splitting
        # is pure I/O ordering, so it lives here rather than in adaptation.py.
        shared = {
            key: value for key, value in kwargs.items() if key == TRANSITION_KWARG
        }
        parts = [
            {**shared, key: kwargs[key]}
            for key in (BRIGHTNESS_KWARG, COLOR_TEMP_KWARG)
            if key in kwargs
        ]
        for index, part in enumerate(parts):
            if index:
                await asyncio.sleep(self._settings.send_split_delay)
            await self._async_turn_on(entity_id, part, context)

    async def _async_turn_on(
        self, entity_id: str, data: dict[str, Any], context: Context
    ) -> None:
        await self.hass.services.async_call(
            "light", "turn_on", {"entity_id": entity_id, **data}, context=context
        )
