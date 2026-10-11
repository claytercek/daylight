"""Reversible adapters for HA's private, post-authorization light seams.

There is no public pre-turn-on hook. Keep the compatibility dependency here.
The resolver wrapper only changes invocation transport: a selected, proven ZHA
multicast group may replace selected copies of its own leaves. Periodic target
membership remains leaf based.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import sys
from collections import OrderedDict
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from functools import partial
from typing import Any, Protocol
from weakref import ref

from homeassistant.components.light import (
    DATA_COMPONENT,
    LightEntity,
    filter_turn_on_params,
    process_turn_on_params,
)
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import entity_registry, service
from homeassistant.helpers.group import IntegrationSpecificGroup, get_group_entities

_LOGGER = logging.getLogger(__name__)
_DATA_KEY = "daylight_turn_on_interceptor"
_STOCK_ENTITY_SERVICE_CALL = service.entity_service_call
_STOCK_RESOLVER = service._resolve_entity_service_call_entities
_STOCK_DISPATCH = service._handle_single_entity_call
_MAX_INVOCATION_PLANS = 128
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
    cancel: Callable[[], None] | None = None


@dataclass
class MulticastCommand:
    """Side-effect-free plan activated only when native group dispatch starts."""

    activate: Callable[[], TurnOnCommand | None]


class TurnOnOwner(Protocol):
    """The switch owns eligibility, schedule evaluation and report receipts."""

    def observe_light_call(self, entity_id: str, call: ServiceCall) -> None: ...

    def prepare_turn_on(
        self, entity_id: str, call: ServiceCall
    ) -> TurnOnCommand | None: ...

    def plan_multicast_turn_on(
        self, groups: Mapping[str, tuple[str, ...]], call: ServiceCall
    ) -> dict[str, MulticastCommand] | None: ...


@dataclass
class _InvocationPlan:
    """Commands and dispatches belonging to one actual ServiceCall object."""

    call: ServiceCall
    owner: TurnOnOwner
    groups: dict[str, tuple[str, ...]]
    commands: dict[str, MulticastCommand]
    pending: set[str]
    task: asyncio.Task[Any] | None = None
    done_callback: Callable[[asyncio.Task[Any]], None] | None = None


@dataclass(frozen=True)
class _TrustedGroup:
    """A selected ZHA group whose unique IDs all resolve to live light entities."""

    entity_id: str
    members: tuple[str, ...]


def _compatible_signature(function: Any, expected: tuple[str, ...]) -> bool:
    """Check a private coroutine seam without evaluating HA's type-only names."""
    try:
        options: dict[str, Any] = {}
        if sys.version_info >= (3, 14):
            from annotationlib import Format

            options["annotation_format"] = Format.STRING
        parameters = inspect.signature(function, **options).parameters
        return (
            tuple(parameters) == expected
            and all(
                parameter.kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
                for parameter in parameters.values()
            )
            and inspect.iscoroutinefunction(function)
        )
    except (TypeError, ValueError, NameError):
        return False


class TurnOnInterceptor:
    """One registration table per HA runtime, shared by enabled targets."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self.owners: dict[TurnOnOwner, tuple[str, ...]] = {}
        self.active = True
        self._warned: set[str] = set()
        self._plans: OrderedDict[int, _InvocationPlan] = OrderedDict()
        self._known_groups: dict[str, tuple[str, ...]] = {}
        self._dispatch_predecessor: Any = getattr(
            service, "_handle_single_entity_call", None
        )
        self._resolver_predecessor: Any = getattr(
            service, "_resolve_entity_service_call_entities", None
        )

        async def resolve(
            hass: HomeAssistant,
            registered_entities: Any,
            call: ServiceCall,
            required_features: Iterable[int] | None = None,
            entity_device_classes: Iterable[str | None] | None = None,
            admin_only: bool = False,
        ) -> list[Any] | None:
            # Only HA's stock entity_service_call + HassJob path reaches the
            # single-entity wrapper with this same ServiceCall. Batched and
            # string-function callers also use the resolver but cannot consume
            # a transport plan safely.
            caller_func = None
            caller = None
            try:
                caller = sys._getframe(1)
                caller_func = (
                    caller.f_locals.get("func")
                    if caller.f_code is _STOCK_ENTITY_SERVICE_CALL.__code__
                    and caller.f_locals.get("call") is call
                    and caller.f_locals.get("registered_entities")
                    is registered_entities
                    else None
                )
            except ValueError:
                caller_func = None
            finally:
                del caller

            # Authorization, target selection, availability and feature filtering
            # always happen in HA first. Daylight never expands that result.
            entities = await self._resolver_predecessor(
                hass,
                registered_entities,
                call,
                required_features,
                entity_device_classes,
                admin_only=admin_only,
            )
            if (
                not self.active
                or hass is not self.hass
                or entities is None
                or call.domain != "light"
                or call.return_response
            ):
                return entities
            try:
                component = hass.data.get(DATA_COMPONENT)
                stock_entities = getattr(component, "_entities", None)
                if (
                    service._resolve_entity_service_call_entities
                    is not self.resolver_wrapper
                    or service._handle_single_entity_call is not self.wrapper
                    or registered_entities is not stock_entities
                    or self._resolver_predecessor is not _STOCK_RESOLVER
                    or self._dispatch_predecessor is not _STOCK_DISPATCH
                    or not self._uses_stock_light_dispatch(
                        stock_entities, caller_func, call.service
                    )
                ):
                    self._warn(
                        "service-path",
                        "HA light service dispatch path is unsupported",
                    )
                    return entities
                registered = registered_entities
                if not isinstance(registered, Mapping):
                    self._warn("resolver-shape", "HA entity registry shape has changed")
                    return entities
                trusted, rejected = self._selected_trusted_groups(
                    entities, registered
                )
                self._observe_group_invocation(entities, trusted, call)
                if call.service != "turn_on" or rejected:
                    return entities
                return self._plan_multicast(call, entities, trusted)
            except Exception:
                self._warn(
                    "resolver-plan",
                    "Cannot prepare multicast Daylight turn-on",
                    exc_info=True,
                )
                return entities

        async def dispatch(hass: Any, entity: Any, func: Any, data: Any) -> Any:
            plan = self._get_plan(data)
            multicast = (
                plan.commands.get(entity.entity_id) if plan is not None else None
            )
            command: TurnOnCommand | None = None
            if (
                not self.active
                or hass is not self.hass
                or not isinstance(entity, LightEntity)
                or not isinstance(data, ServiceCall)
                or data.domain != "light"
                or data.service not in ("turn_on", "toggle", "turn_off")
            ):
                try:
                    return await self._dispatch_predecessor(hass, entity, func, data)
                finally:
                    self._finish_plan_entity(plan, entity)

            if multicast is not None:
                try:
                    command = self._activate_multicast(
                        plan, entity, multicast
                    )
                except Exception:
                    self._warn(
                        f"activate-{entity.entity_id}",
                        f"Cannot activate Daylight multicast for {entity.entity_id}",
                        exc_info=True,
                    )

            owners = [
                owner
                for owner, members in self.owners.items()
                if entity.entity_id in members
            ]
            if command is None and owners:
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
                                f"{entity.entity_id}; remove the overlap to enable "
                                "turn-on interception",
                            )
                        elif data.service != "turn_off":
                            command = owners[0].prepare_turn_on(entity.entity_id, data)
                    except Exception:
                        if command is not None:
                            command.finish(False)
                            command = None
                        self._warn(
                            f"prepare-{entity.entity_id}",
                            f"Cannot prepare Daylight turn-on for {entity.entity_id}",
                            exc_info=True,
                        )
            if command is not None:
                try:
                    data = ServiceCall(
                        hass,
                        data.domain,
                        data.service,
                        {**data.data, "params": dict(command.params)},
                        context=data.context,
                        return_response=data.return_response,
                    )
                except Exception:
                    command.finish(False)
                    if command.cancel is not None:
                        command.cancel()
                    command = None
                    self._warn(
                        "clone-shape",
                        "HA light ServiceCall shape has changed",
                        exc_info=True,
                    )

            # Do not catch and retry a possibly applied command. Preserve errors,
            # cancellation and response values from exactly one predecessor call.
            success = False
            try:
                result = await self._dispatch_predecessor(hass, entity, func, data)
                success = True
                return result
            finally:
                if command is not None:
                    # Once predecessor dispatch has started, cancellation is an
                    # uncertain outcome: a multicast frame may already be out.
                    # Keep the same bounded failure receipt in that case.
                    command.finish(success)
                self._finish_plan_entity(plan, entity)

        self.resolver_wrapper: Any = resolve
        self.wrapper: Any = dispatch
        resolver_compatible = _compatible_signature(
            self._resolver_predecessor,
            (
                "hass",
                "registered_entities",
                "call",
                "required_features",
                "entity_device_classes",
                "admin_only",
            ),
        )
        dispatch_compatible = _compatible_signature(
            self._dispatch_predecessor, ("hass", "entity", "func", "data")
        )
        if resolver_compatible and dispatch_compatible:
            service._resolve_entity_service_call_entities = self.resolver_wrapper
            service._handle_single_entity_call = self.wrapper
        else:
            self._warn("signature", "HA entity dispatch signature is unsupported")

    def _uses_stock_light_dispatch(
        self, entities: Any, caller_func: Any, service_name: str
    ) -> bool:
        """Exclude batched and string-func resolver paths before filtering."""
        handler_name = {
            "turn_on": "async_handle_light_on_service",
            "turn_off": "async_handle_light_off_service",
            "toggle": "async_handle_toggle_service",
        }.get(service_name)
        if handler_name is None:
            return False
        handler = self.hass.services.async_services_internal().get("light", {}).get(
            service_name
        )
        target = getattr(getattr(handler, "job", None), "target", None)
        if not (
            isinstance(target, partial)
            and target.func is _STOCK_ENTITY_SERVICE_CALL
            and len(target.args) >= 3
            and target.args[0] is self.hass
            and target.args[1] is entities
            and target.args[2] is caller_func
            and not isinstance(caller_func, str)
        ):
            return False
        light_handler = getattr(target.args[2], "target", None)
        return (
            getattr(light_handler, "__module__", None)
            == "homeassistant.components.light"
            and getattr(light_handler, "__qualname__", "").endswith(
                f"async_setup.<locals>.{handler_name}"
            )
        )

    def _warn(self, key: str, reason: str, *, exc_info: bool = False) -> None:
        if key not in self._warned:
            self._warned.add(key)
            _LOGGER.warning(
                "%s. Daylight is leaving the original request unchanged; "
                "state-change adaptation remains available. "
                "Check Daylight/HA compatibility.",
                reason,
                exc_info=exc_info,
            )

    def _selected_trusted_groups(
        self, entities: list[Any], registered: Mapping[str, Any]
    ) -> tuple[list[_TrustedGroup], bool]:
        """Prove registry and physical endpoint topology for selected ZHA groups."""
        group_entities = get_group_entities(self.hass)
        registry = entity_registry.async_get(self.hass)
        trusted: list[_TrustedGroup] = []
        rejected = False
        for entity in entities:
            entity_id = getattr(entity, "entity_id", None)
            if not isinstance(entity_id, str):
                continue
            if group_entities.get(entity_id) is not entity:
                state = self.hass.states.get(entity_id)
                if state is not None and any(
                    isinstance(state.attributes.get(key), (list, tuple, set))
                    for key in ("entity_id", "group_entities")
                ):
                    rejected = True
                continue
            group = getattr(entity, "group", None)
            entry = registry.async_get(entity_id)
            if entry is None or entry.platform != "zha":
                rejected = True
                continue
            if not isinstance(group, IntegrationSpecificGroup):
                rejected = True
                continue
            unique_ids = tuple(group.member_unique_ids)
            members = tuple(group.member_entity_ids)
            if (
                not members
                or len(members) != len(unique_ids)
                or len(set(members)) != len(members)
                or len(set(unique_ids)) != len(unique_ids)
            ):
                rejected = True
                continue
            complete = True
            for member_id, unique_id in zip(members, unique_ids, strict=True):
                member_entry = registry.async_get(member_id)
                member = registered.get(member_id)
                if (
                    member_entry is None
                    or member_entry.platform != "zha"
                    or member_entry.unique_id != unique_id
                    or not isinstance(member, LightEntity)
                ):
                    complete = False
                    break
            if complete:
                entity_data = getattr(entity, "entity_data", None)
                group_proxy = getattr(entity_data, "group_proxy", None)
                physical_group = getattr(group_proxy, "group", None)
                physical_members = getattr(physical_group, "members", None)
                zha_entity = getattr(entity_data, "entity", None)
                platform = getattr(zha_entity, "PLATFORM", None)
                if (
                    not isinstance(physical_members, (list, tuple, set))
                    or platform is None
                ):
                    complete = False
                else:
                    physical_ids: list[str] = []
                    for physical_member in physical_members:
                        associated = getattr(
                            physical_member, "associated_entities", None
                        )
                        if not isinstance(associated, (list, tuple, set)):
                            complete = False
                            break
                        matching = [
                            associated_entity
                            for associated_entity in associated
                            if getattr(associated_entity, "PLATFORM", None) == platform
                        ]
                        if len(matching) != 1 or not isinstance(
                            unique_id := getattr(
                                getattr(matching[0], "identifiers", None),
                                "unique_id",
                                None,
                            ),
                            str,
                        ):
                            complete = False
                            break
                        physical_ids.append(unique_id)
                    if (
                        complete
                        and len(physical_ids) == len(unique_ids)
                        and set(physical_ids) == set(unique_ids)
                    ):
                        trusted.append(_TrustedGroup(entity_id, members))
                    else:
                        complete = False
            if not complete:
                rejected = True
        return trusted, rejected

    def _observe_group_invocation(
        self,
        entities: list[Any],
        groups: list[_TrustedGroup],
        call: ServiceCall,
    ) -> None:
        """A later group request invalidates expectations for just its leaves."""
        trusted = {group.entity_id: group for group in groups}
        selected_ids = {
            entity.entity_id
            for entity in entities
            if isinstance(getattr(entity, "entity_id", None), str)
        }
        group_ids = selected_ids.intersection(self._known_groups) | set(trusted)
        for group_id in group_ids:
            previous = self._known_groups.pop(group_id, ())
            group = trusted.get(group_id)
            current = group.members if group is not None else ()
            members = tuple(dict.fromkeys((*previous, *current)))
            if group is not None:
                self._known_groups[group_id] = current
            for member_id in members:
                for owner, owned in self.owners.items():
                    if member_id in owned:
                        owner.observe_light_call(member_id, call)

    def _plan_multicast(
        self,
        call: ServiceCall,
        entities: list[Any],
        groups: list[_TrustedGroup],
    ) -> list[Any]:
        """Retain selected groups while removing only their selected leaves."""
        params = call.data.get("params")
        if not groups:
            return entities
        if not isinstance(params, Mapping):
            self._warn("multicast-shape", "HA light service data shape has changed")
            return entities
        if (
            SPECIAL_KEYS.intersection(params)
            or any(
                params.get(key) == 0
                for key in ("brightness", "brightness_pct", "white")
            )
        ):
            return entities

        group_ids = {group.entity_id for group in groups}
        if any(group_ids.intersection(group.members) for group in groups):
            return entities
        covered: set[str] = set()
        for group in groups:
            members = set(group.members)
            if covered.intersection(members):
                return entities
            covered.update(members)

        owner: TurnOnOwner | None = None
        group_members: dict[str, tuple[str, ...]] = {}
        for group in groups:
            # Preserve existing explicit-group behavior. This optimization is
            # solely a transport for leaf-owned targets discovered by HA.
            if any(group.entity_id in owned for owned in self.owners.values()):
                return entities
            for member_id in group.members:
                member_owners = [
                    candidate
                    for candidate, owned in self.owners.items()
                    if member_id in owned
                ]
                if len(member_owners) != 1:
                    return entities
                if owner is None:
                    owner = member_owners[0]
                elif owner is not member_owners[0]:
                    return entities
            group_members[group.entity_id] = group.members
        if owner is None:
            return entities

        commands = owner.plan_multicast_turn_on(group_members, call)
        if commands is None or set(commands) != set(group_members):
            return entities

        filtered = [
            entity
            for entity in entities
            if entity.entity_id in group_ids or entity.entity_id not in covered
        ]
        task = asyncio.current_task()
        plan = _InvocationPlan(
            call=call,
            owner=owner,
            groups=group_members,
            commands=commands,
            pending={entity.entity_id for entity in filtered},
        )
        key = id(call)
        old = self._plans.pop(key, None)
        if old is not None:
            self._finish_plan(old)
        self._plans[key] = plan
        if task is not None:
            plan_ref = ref(plan)

            def task_done(_done: asyncio.Task[Any]) -> None:
                current = plan_ref()
                if current is not None and self._plans.get(key) is current:
                    self._finish_plan(current, remove_done_callback=False)

            plan.task = task
            plan.done_callback = task_done
            task.add_done_callback(task_done)
        while len(self._plans) > _MAX_INVOCATION_PLANS:
            _, stale = self._plans.popitem(last=False)
            self._finish_plan(stale)
            self._warn(
                "plan-capacity",
                "Too many unfinished Daylight multicast invocations",
            )
        return filtered

    def _activate_multicast(
        self,
        plan: _InvocationPlan | None,
        entity: LightEntity,
        multicast: MulticastCommand,
    ) -> TurnOnCommand | None:
        """Revalidate topology, ownership and final kwargs before receipts."""
        if plan is None:
            return None
        component = self.hass.data.get(DATA_COMPONENT)
        registered = getattr(component, "_entities", None)
        if not isinstance(registered, Mapping):
            return None
        trusted, rejected = self._selected_trusted_groups([entity], registered)
        expected = plan.groups.get(entity.entity_id)
        if (
            rejected
            or expected is None
            or len(trusted) != 1
            or trusted[0].members != expected
            or any(
                entity.entity_id in members for members in self.owners.values()
            )
        ):
            return None
        for member_id in expected:
            member_owners = [
                owner
                for owner, members in self.owners.items()
                if member_id in members
            ]
            if member_owners != [plan.owner]:
                return None

        command = multicast.activate()
        if command is None:
            return None
        native_kwargs: list[dict[str, Any]] = []
        for entity_id in (entity.entity_id, *expected):
            light = registered.get(entity_id)
            if not isinstance(light, LightEntity):
                command.finish(False)
                if command.cancel is not None:
                    command.cancel()
                return None
            processed = process_turn_on_params(
                self.hass, light, dict(command.params)
            )
            native_kwargs.append(filter_turn_on_params(light, processed))
        if any(kwargs != native_kwargs[0] for kwargs in native_kwargs[1:]):
            command.finish(False)
            if command.cancel is not None:
                command.cancel()
            return None
        return command

    def _get_plan(self, data: Any) -> _InvocationPlan | None:
        if not isinstance(data, ServiceCall):
            return None
        plan = self._plans.get(id(data))
        return plan if plan is not None and plan.call is data else None

    def _finish_plan_entity(
        self, plan: _InvocationPlan | None, entity: Any
    ) -> None:
        if plan is None:
            return
        plan.pending.discard(entity.entity_id)
        if not plan.pending:
            self._finish_plan(plan)

    def _finish_plan(
        self, plan: _InvocationPlan, *, remove_done_callback: bool = True
    ) -> None:
        if self._plans.get(id(plan.call)) is plan:
            self._plans.pop(id(plan.call), None)
        if (
            remove_done_callback
            and plan.task is not None
            and plan.done_callback is not None
        ):
            plan.task.remove_done_callback(plan.done_callback)
        plan.task = None
        plan.done_callback = None
        plan.pending.clear()

    def remove(self, owner: TurnOnOwner) -> None:
        """Deactivate before restoring; never overwrite a newer wrapper."""
        self.owners.pop(owner, None)
        for key, plan in tuple(self._plans.items()):
            if plan.owner is owner:
                # Drop side-effect-free metadata rather than retain a stale
                # owner reference. Any already-running native task continues.
                self._plans.pop(key)
                self._finish_plan(plan)
        if self.owners:
            return
        self.active = False
        self._known_groups.clear()
        if getattr(service, "_handle_single_entity_call", None) is self.wrapper:
            service._handle_single_entity_call = self._dispatch_predecessor
        if (
            getattr(service, "_resolve_entity_service_call_entities", None)
            is self.resolver_wrapper
        ):
            service._resolve_entity_service_call_entities = self._resolver_predecessor
        if self.hass.data.get(_DATA_KEY) is self:
            self.hass.data.pop(_DATA_KEY)


def register(
    hass: HomeAssistant, owner: TurnOnOwner, members: tuple[str, ...]
) -> TurnOnInterceptor:
    """Register/update an enabled owner's periodic leaf membership."""
    interceptor = hass.data.get(_DATA_KEY)
    if interceptor is None:
        interceptor = hass.data[_DATA_KEY] = TurnOnInterceptor(hass)
    interceptor.owners[owner] = members
    return interceptor
