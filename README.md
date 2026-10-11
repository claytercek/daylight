# Daylight

Daylight is a custom Home Assistant integration that adjusts your lights' brightness and color temperature through the day, using your location's sunrise and sunset.

A **hub** defines the shared daily curve. Each **target** selects areas, individual light entities, or both, and sets their brightness and temperature ranges. You can give a bedroom and a kitchen different limits while keeping them on the same schedule.

## Installation and setup

### HACS (recommended)

Daylight is installed through HACS as a custom repository:

1. Open HACS, select the three-dot menu, and choose **Custom repositories**.
2. Add `https://github.com/claytercek/daylight` with the category **Integration**.
3. Open Daylight in HACS, select **Download**, and restart Home Assistant when the download finishes.

### Manual installation

As an alternative, download or clone this repository and copy `custom_components/daylight` into your Home Assistant configuration's `custom_components` directory. Restart Home Assistant afterward.

### Setup

1. Check Home Assistant's location and time zone, then go to **Settings → Devices & services → Add integration → Daylight**.
2. Select areas or lights. Setup creates the shared schedule and your first target; all other fields have defaults. Add more targets later if different rooms need different levels.
3. Enable the target's adaptation switch. New switches start off.

Defaults are **10% brightness / 2500 K at night** and **100% / 4000 K during the day**. Both tracks follow the sun automatically; no timing configuration is required.

Daylight adapts lights that are already on. Turning on an adaptation switch won't turn on individual lights that are off. An area resolves to its registered light entities (including those assigned through a device); each light receives its own command and has its own manual-control and failure handling. When an area includes both a light group and its registered member lights, Daylight omits the area-discovered group to avoid sending overlapping commands. Area membership changes take effect without reconfiguring the target. Lights explicitly selected as well as included in an area are adapted only once.

A light group selected directly (as an entity) is treated as one light: commands go to the group, so its integration may turn on members that were off. Explicitly selecting a group keeps it in the target even when its members are also included by area; avoid that overlap if the group integration may turn on members that were off. An area-only group remains included when none of its members are in the selected area.

## Change the daily schedule

All editing lives in **Settings → Devices & services → Daylight**. Use the hub entry's **Reconfigure** action for shared timing, or edit a target for its lights and night/day levels.

The schedule holds steady day and night levels, with separate morning and evening transitions for brightness and color. On a day with twelve hours of daylight:

| Track | Morning | Evening |
| --- | --- | --- |
| Brightness | 30 minutes before sunrise → 30 minutes after | 30 minutes before sunset → 30 minutes after |
| Color temperature | Sunrise → two hours afterward | Two hours before sunset → sunset |

Transitions stretch with longer days and shrink with shorter days. Brightness transitions shorten further if the night is too short. These are comfort-oriented defaults, not a medically validated sleep or circadian prescription.

Basic settings let you:

- Move morning or evening earlier/later relative to the sun, shifting both tracks together.
- **Start mornings at** a fixed time, or **finish evenings by** a fixed time. Select **Following day** explicitly for an evening ending after midnight.
- Make brightness or color transitions shorter/longer. Each control adjusts that track's morning and evening lengths together; the lengths continue to vary seasonally.

Basic schedules automatically shorten transitions when necessary to fit the available time. They never silently swap morning and evening.

Open the collapsed sections on the hub's Reconfigure form to edit timing, shapes, or update frequency. **Submit** saves all changes together; cancel discards them. Saving checks a full year of seasonal timing and reports an example date if rules conflict. Target level forms save independently when submitted.

### Advanced timing

Each track has four collapsed endpoint sections on the same form: morning start/finish and evening start/finish. There are no additional points or curve handles.

Open an endpoint section and select its timing rule:

- **Standard:** keep its automatically fitted timing, or remove an existing override.
- **Seasonal solar offset:** enter an offset in minutes for the current date. It stretches with daylight in other seasons.
- **Fixed-minute solar offset:** stay the same number of minutes before/after sunrise, sunset, solar noon or solar midnight.
- **Clock time:** use an exact local time, optionally on the following day.

Custom rules are exact: overlapping transitions or conflicting order are rejected, not moved to fit. Unchanged endpoints keep their standard rules. While any endpoint override exists, basic timing adjustments remain saved but do not affect the curve. **Restore standard timing** replaces overrides and basic timing adjustments without changing target ranges or transition shapes when you submit the form.

Each transition can be **Smooth** (smoothstep, gently starting and finishing) or **Linear**. Changing shape does not change timing. Shape-only edits leave basic timing controls available.

### Polar regions and clock changes

Real solar events are preserved whenever available. If a crossing is missing, Daylight uses the existing one-hour lighting-day fallback around solar noon in polar winter, or a one-hour lighting night around solar midnight in polar summer. This requires no extra controls. Preview labels the fallback rather than presenting synthetic anchors as real sunrise/sunset. The switch into fallback timing is not guaranteed to be seamless.

Schedules continue across midnight. Clock rules use Home Assistant's timezone; ambiguous DST times use the first occurrence, and nonexistent times move forward across the gap. Preview includes the full local calendar day, including 23- and 25-hour days.

After changing Home Assistant's location or timezone, reload Daylight and review custom timing. A newly invalid schedule fails updates rather than sending guessed lighting values.

## Light behavior

With adaptation enabled, an ordinary Home Assistant `light.turn_on` (or a `light.toggle` turning an off light on) receives freshly calculated brightness and color temperature in its first device command, with transition `0`. This applies only to known-off members with adaptable capabilities. Scheduled values take precedence over explicit brightness, profiles, and scene colors on that off→on request. RGB-only lights keep the caller's color and receive scheduled brightness. Changes made while a light is already on still take manual control.

Requests for zero brightness, relative brightness steps, flashes, or effects pass through unchanged. So do unmanaged lights and lights whose state is unknown or unavailable. Physical turn-ons and reconnects still use state-change correction, which can briefly show the previous values. Turning off the target's adaptation switch disables interception as well as ongoing adaptation.

Home Assistant has no public pre-turn-on hook. Daylight wraps private entity-resolution and entity-dispatch functions after Home Assistant has authorized and resolved the original target. It preserves the caller context and normal light handler and never adds an entity the caller did not select. If either hook's signature or data shape isn't supported, Daylight logs a compatibility warning and falls back to state-change adaptation. A light assigned to multiple enabled targets also skips interception and logs a warning; remove the overlap rather than relying on either target to win.

For a supported ZHA group turn-on, Daylight keeps the originally selected group as the native transport instead of replacing multicast with per-light calls. This applies only when Home Assistant exposes an `IntegrationSpecificGroup`, every physical ZHA endpoint has a matching light entity and complete registry mapping, every member is a known-off leaf owned by the same enabled target, and the group and members resolve to identical final light arguments (including default profiles). Selected member copies covered by that group are suppressed for that invocation; a group-only request remains group-only. Daylight rechecks membership, ownership and state and arms the leaf report receipts only when native group dispatch begins. Overlapping or unverified groups, partial or conflicting ownership, already-on members (manual or otherwise), unavailable members, incompatible capabilities or temperature limits, special requests, response-bearing calls, and Separate Turn-On Commands all use the existing unoptimized behavior. A historical manual flag does not block a leaf whose state is definitively off; the flag remains until that leaf actually reports off→on, then clears through the normal state-change path. Explicitly configured group targets retain their existing semantics.

The optimization guarantees one Home Assistant call to each retained ZHA group entity, not one Zigbee frame. ZHA may encode brightness, color temperature, and other arguments as several multicast radio commands, and bulb firmware can expose an intermediate value while applying them. Separate Turn-On Commands cannot guarantee the first color, so Daylight does not silently override that setting to use this path.

The update interval still defaults to 90 seconds. Mathematical curve shape, polling frequency and the fade requested from a bulb are separate: a smooth target curve does not guarantee continuous physical output. For periodic adaptation, Daylight caps each requested fade at the hub update interval, preventing a configured fade from exceeding the time between updates.

One tested IKEA TRÅDFRI GU10 and one E12 white-spectrum bulb left brightness at its starting level when brightness and color temperature were sent together with a positive transition. For an affected target, set **Transition** to `0` and leave **Separate Turn-On Commands** disabled; this verified workaround preserves the native-group initial path, but periodic updates step instead of fade. The result is limited to the tested model/firmware specimens; see the [hardware evidence and test matrix](research/ikea_light_transitions.md#live-hardware-validation-2026-10-10).

Each target has these additional controls:

| Setting | Behavior |
| --- | --- |
| Transition | Maximum fade duration in seconds for ongoing updates, capped by the hub update interval and subject to the light's support. Increasing the update interval can restore a longer configured fade. A light entering the on state, joining an active target, or a target switch resuming adaptation snaps to the current values instead. |
| Adapt Only On State Change | Disables periodic adaptation. Lights still adapt when they enter the on state or when you resume adaptation. |
| Manual Control Reset Minutes | Resumes after this many minutes without another detected manual change. `0` disables timed reset. Reset takes effect at the next adaptation opportunity. |
| Separate Turn-On Commands | Sends brightness and color temperature in separate commands for bulbs that don't accept both together. |
| Send Split Delay | Seconds between those separate commands, including when adaptation resumes. |
| Fade temperature, then brightness | Off by default. For periodic updates with both properties and a positive fade, sends a native temperature fade first, then a native brightness fade. Each phase gets half the total capped fade duration. |

With Separate Turn-On Commands enabled, the original turn-on carries scheduled brightness; Daylight waits at least Send Split Delay after the native turn-on completes, then sends temperature once the light reports on and is still under adaptive control. If no matching on-report arrives before the receipt expires, the temperature command is cancelled. This mode cannot guarantee the initial color: the bulb can show its previous color until the second command. Home Assistant's default light profiles can also supply a color in the first command. Disabling adaptation, removing the member, turning the light off, or taking manual control cancels the delayed command, never the caller's original turn-on.

**Fade temperature, then brightness** (`serialized_native_fades`) applies only to periodic updates. Daylight waits for the first Home Assistant service dispatch to complete, then waits its half-fade duration before dispatching brightness; it does not wait through the second fade. The two requested native phases do not overlap. A configured maximum of 120 seconds with a 15-second update interval gives 7.5 seconds for temperature and 7.5 seconds for brightness. For qualifying updates this takes precedence over Separate Turn-On Commands, and Send Split Delay adds no time to the budget. Zero-duration and single-property updates, initial turn-ons, and resume/join/reconnect snaps keep their existing behavior.

Both phases use the same selected entity through Home Assistant's normal light service. An already-selected native group stays a group; this option does not discover groups or replace individual targets with one. A newer periodic update cancels an unfinished sequence before starting fresh, and manual control, off/unavailable state, or disabled adaptation cancels the pending brightness command. Cancellation cannot undo a fade already sent to the device. Actual firmware behavior remains device-specific: this option does not guarantee a Zigbee frame count or optically verified output.

## Preview a saved schedule

Open `/daylight-preview`, choose a target and date, and use **Refresh saved settings** after making changes. The preview is read-only: it never changes settings or lights. It shows the same curve and range mapping used by live adaptation, along with transition times and explanations for automatic fitting or polar fallback.

Use the time inspection slider or the expandable values table to inspect individual samples. Times use Home Assistant's timezone, not the browser's. Values represent the target's configured ranges; an individual bulb may clamp color temperature to its own limits.

## Manual control

Changing a light's brightness or color pauses adaptation for that entity. Cycle the light off and on to resume, or toggle the target's adaptation switch off and on to reset all its lights. Switch state and manual flags survive restarts.

Detection uses Home Assistant state changes. Daylight recognizes its own command contexts and ignores metadata and availability changes. Reports without a user context are also ignored during its transition plus a short reporting grace period, favoring fewer false overrides.

An intercepted turn-on has a separate, short-lived receipt for each affected leaf's caller context and child reports, including user-authored reports. Multicast group receipts are armed together immediately before the native group handler starts. A receipt stays active while the native request executes, suppresses one duplicate off→on correction, and expires after the split delay (if any) plus two seconds from native completion. A failed or cancelled multicast keeps that same bounded window once native dispatch has started because the radio command may already have applied; cancellation before dispatch arms no receipts. The caller's context isn't saved as Daylight's own. A new external light service call clears the relevant receipt and reporting grace, so an automation reusing the same context can still take manual control. An old manual flag clears only when the light actually reports off→on, not merely because a turn-on was requested.

That tradeoff matters: a physical dim during this window can go undetected. A delayed device report after the window can look like manual control. A reconnect alone won't clear an existing manual flag.

## Light compatibility

Daylight uses Home Assistant's light entities, including those provided by MQTT, Matter/Thread, and ZHA. Behavior depends on the capabilities and state reports each integration exposes. Color temperature is clamped to the light's reported limits. Lights that only support RGB receive brightness adjustments; on/off-only lights aren't adapted.

Automated tests simulate Home Assistant events and services. They don't establish compatibility with every physical bulb, bridge, or group implementation.

## Development

Use Python 3.13 or newer, `uv`, and Node.js for the panel tests:

```sh
uv sync
uv pip install --python .venv/bin/python -r requirements_test.txt
.venv/bin/pytest
.venv/bin/ruff check .
.venv/bin/ty check
node --test tests/panel.test.mjs
```

Timing is resolved in `solar.py` and `schedule.py`; interpolation is stateless. Native form/storage conversion lives in `schedule_config.py`, and both runtime and preview evaluate the same schedule.

Licensed under Apache 2.0. Solar diagnostic and polar-fallback code derives from Adaptive Lighting; see [NOTICE](NOTICE) for attribution.
