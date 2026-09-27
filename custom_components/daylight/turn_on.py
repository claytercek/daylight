"""Reversible adapter for HA's private, post-authorization entity dispatch seam.

There is no public pre-turn-on hook. Keep the compatibility dependency here;
never replace registered services or resolve/split the caller's targets.
"""

from __future__ import annotations

import inspect
import logging
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from homeassistant.components.light import LightEntity
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import service

_LOGGER = logging.getLogger(__name__)
_DATA_KEY = "daylight_turn_on_interceptor"
COLOR_KEYS = frozenset(
    {
        "color_temp_kelvin",
        "color_temp",
        "color_name",
        "hs_color",
        "rgb_color",
        "rgbw_color",
        "rgbww_color",
        "xy_color",
        "white",
    }
)
# HA defers step normalization until it knows the entity's current brightness.
# Do not turn effective-off or one-shot requests into scheduled turn-ons.
SPECIAL_KEYS = frozenset({"brightness_step", "brightness_step_pct", "flash", "effect"})


@dataclass
class TurnOnCommand:
    """Prepared entity-local data and dispatch bookkeeping (not caller tasks)."""

    params: dict[str, Any]
    finish: Callable[[bool], None]


class TurnOnOwner(Protocol):
    """The switch owns eligibility, schedule evaluation and report receipts."""

    def observe_light_call(self, entity_id: str, call: ServiceCall) -> None: ...

    def prepare_turn_on(
        self, entity_id: str, call: ServiceCall
    ) -> TurnOnCommand | None: ...


class TurnOnInterceptor:
    """One registration table per HA runtime, shared by enabled targets."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self.owners: dict[TurnOnOwner, tuple[str, ...]] = {}
        self.active = True
        self._warned: set[str] = set()
        self._predecessor: Any = getattr(service, "_handle_single_entity_call", None)

        async def dispatch(hass: Any, entity: Any, func: Any, data: Any) -> Any:
            if (
                not self.active
                or hass is not self.hass
                or not isinstance(entity, LightEntity)
                or not isinstance(data, ServiceCall)
                or data.domain != "light"
                or data.service not in ("turn_on", "toggle", "turn_off")
            ):
                return await self._predecessor(hass, entity, func, data)
            owners = [
                owner
                for owner, members in self.owners.items()
                if entity.entity_id in members
            ]
            command = None
            if owners:
                if not isinstance(data.data.get("params"), Mapping):
                    self._warn("shape", "HA light service data shape has changed")
                else:
                    try:
                        for owner in owners:
                            owner.observe_light_call(entity.entity_id, data)
                        if len(owners) > 1:
                            self._warn(
                                entity.entity_id,
                                "Multiple enabled Daylight targets own "
                                f"{entity.entity_id}; "
                                "remove the overlap to enable turn-on interception",
                            )
                        elif data.service != "turn_off":
                            command = owners[0].prepare_turn_on(entity.entity_id, data)
                        if command is not None:
                            data = ServiceCall(
                                hass,
                                data.domain,
                                data.service,
                                {**data.data, "params": dict(command.params)},
                                context=data.context,
                                return_response=data.return_response,
                            )
                    except Exception:
                        if command is not None:
                            command.finish(False)
                            command = None
                        _LOGGER.warning(
                            "Cannot prepare Daylight turn-on for %s; "
                            "forwarding unchanged",
                            entity.entity_id,
                            exc_info=True,
                        )
            # Do not catch and retry a possibly applied command. Preserve errors,
            # cancellation and response values from exactly one predecessor call.
            success = False
            try:
                result = await self._predecessor(hass, entity, func, data)
                success = True
                return result
            finally:
                if command is not None:
                    command.finish(success)

        # Dynamic private seam: the runtime signature check is the contract.
        self.wrapper: Any = dispatch
        try:
            # Python 3.14 lazily evaluates annotations; HA has type-only imports.
            options: dict[str, Any] = {}
            if sys.version_info >= (3, 14):
                from annotationlib import Format

                options["annotation_format"] = Format.STRING
            parameters = inspect.signature(self._predecessor, **options).parameters
            compatible = (
                tuple(parameters) == ("hass", "entity", "func", "data")
                and all(
                    parameter.kind
                    in (
                        inspect.Parameter.POSITIONAL_ONLY,
                        inspect.Parameter.POSITIONAL_OR_KEYWORD,
                    )
                    for parameter in parameters.values()
                )
                and inspect.iscoroutinefunction(self._predecessor)
            )
        except (TypeError, ValueError, NameError):
            compatible = False
        if compatible:
            service._handle_single_entity_call = self.wrapper
        else:
            self._warn("signature", "HA entity dispatch signature is unsupported")

    def _warn(self, key: str, reason: str) -> None:
        if key not in self._warned:
            self._warned.add(key)
            _LOGGER.warning(
                "%s. Daylight is leaving the original request unchanged; "
                "state-change adaptation remains available. "
                "Check Daylight/HA compatibility.",
                reason,
            )

    def remove(self, owner: TurnOnOwner) -> None:
        """Deactivate before restoring; never overwrite a newer wrapper."""
        self.owners.pop(owner, None)
        if self.owners:
            return
        self.active = False
        if getattr(service, "_handle_single_entity_call", None) is self.wrapper:
            service._handle_single_entity_call = self._predecessor
        if self.hass.data.get(_DATA_KEY) is self:
            self.hass.data.pop(_DATA_KEY)


def register(
    hass: HomeAssistant, owner: TurnOnOwner, members: tuple[str, ...]
) -> TurnOnInterceptor:
    """Register/update an enabled owner's resolved members (groups stay intact)."""
    interceptor = hass.data.get(_DATA_KEY)
    if interceptor is None:
        interceptor = hass.data[_DATA_KEY] = TurnOnInterceptor(hass)
    interceptor.owners[owner] = members
    return interceptor
