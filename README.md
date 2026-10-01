# Daylight

Daylight is a custom Home Assistant integration that adjusts your lights' brightness and color temperature through the day, using your location's sunrise and sunset.

A **hub** defines the shared daily curve. Each **target** selects areas, individual light entities, or both, and sets their brightness and temperature ranges. You can give a bedroom and a kitchen different limits while keeping them on the same schedule.

## Setup

1. Copy `custom_components/daylight` into your Home Assistant configuration's `custom_components` directory, then restart Home Assistant.
2. Check Home Assistant's location and time zone, then go to **Settings → Devices & services → Add integration → Daylight**.
3. Select areas or lights. Setup creates the shared schedule and your first target; all other fields have defaults. Add more targets later if different rooms need different levels.
4. Enable the target's adaptation switch. New switches start off.

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

Home Assistant has no public pre-turn-on hook. Daylight wraps a private entity-dispatch function after authorization and target resolution, preserving the original caller context and normal light handler. It doesn't replace service registrations or expand groups itself. If the hook's signature or data shape isn't supported, Daylight logs a compatibility warning and falls back to state-change adaptation. A light assigned to multiple enabled targets also skips interception and logs a warning; remove the overlap rather than relying on either target to win.

The update interval still defaults to 90 seconds. Mathematical curve shape, polling frequency and the fade requested from a bulb are separate: a smooth target curve does not guarantee continuous physical output.

Each target has these additional controls:

| Setting | Behavior |
| --- | --- |
| Transition | Fade duration in seconds for ongoing updates, subject to the light's support. A light entering the on state, joining an active target, or a target switch resuming adaptation snaps to the current values instead. |
| Adapt Only On State Change | Disables periodic adaptation. Lights still adapt when they enter the on state or when you resume adaptation. |
| Manual Control Reset Minutes | Resumes after this many minutes without another detected manual change. `0` disables timed reset. Reset takes effect at the next adaptation opportunity. |
| Separate Turn-On Commands | Sends brightness and color temperature in separate commands for bulbs that don't accept both together. |
| Send Split Delay | Seconds between those separate commands, including when adaptation resumes. |

With Separate Turn-On Commands enabled, the original turn-on carries scheduled brightness; Daylight waits at least Send Split Delay after the native turn-on completes, then sends temperature once the light reports on and is still under adaptive control. If no matching on-report arrives before the receipt expires, the temperature command is cancelled. This mode cannot guarantee the initial color: the bulb can show its previous color until the second command. Home Assistant's default light profiles can also supply a color in the first command. Disabling adaptation, removing the member, turning the light off, or taking manual control cancels the delayed command, never the caller's original turn-on.

## Preview a saved schedule

Open `/daylight-preview`, choose a target and date, and use **Refresh saved settings** after making changes. The preview is read-only: it never changes settings or lights. It shows the same curve and range mapping used by live adaptation, along with transition times and explanations for automatic fitting or polar fallback.

Use the time inspection slider or the expandable values table to inspect individual samples. Times use Home Assistant's timezone, not the browser's. Values represent the target's configured ranges; an individual bulb may clamp color temperature to its own limits.

## Manual control

Changing a light's brightness or color pauses adaptation for that entity. Cycle the light off and on to resume, or toggle the target's adaptation switch off and on to reset all its lights. Switch state and manual flags survive restarts.

Detection uses Home Assistant state changes. Daylight recognizes its own command contexts and ignores metadata and availability changes. Reports without a user context are also ignored during its transition plus a short reporting grace period, favoring fewer false overrides.

An intercepted turn-on has a separate, short-lived receipt for its caller context and child reports, including user-authored reports. It stays active while the native request executes, suppresses one duplicate off→on correction, and expires after the split delay (if any) plus two seconds from successful native completion. A failed or cancelled native request clears it immediately. The caller's context isn't saved as Daylight's own. A new external light service call clears the receipt and reporting grace, so an automation reusing the same context can still take manual control. An old manual flag clears only when the light actually reports off→on, not merely because a turn-on was requested.

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
