# Daylight

Daylight is a custom Home Assistant integration that adjusts your lights' brightness and color temperature through the day, using your location's sunrise and sunset.

A **hub** defines the shared daily curve. Each **target** selects one or more light entities and sets their brightness and temperature ranges. You can give a bedroom and a kitchen different limits while keeping them on the same schedule.

## Setup

1. Copy `custom_components/daylight` into your Home Assistant configuration's `custom_components` directory, then restart Home Assistant.
2. Go to **Settings → Devices & services → Add integration → Daylight** and configure the hub. Check Home Assistant's location and time zone first: Daylight uses both.
3. Add a target to the integration, select your lights, and set their brightness and color temperature limits.
4. Enable the target's adaptation switch. New switches start off.

Daylight adapts lights that are already on. Turning on an adaptation switch won't turn on individual lights that are off. A configured light group is treated as one light: commands go to the group, so its integration may turn on members that were off. Avoid configuring a group and its members as overlapping targets.

## Settings

Hub settings include sunrise/sunset offsets, fixed times or earliest/latest limits, and `default`, `linear`, or smooth (`tanh`) brightness curves. The update interval defaults to 90 seconds.

Each target has these additional controls:

| Setting | Behavior |
| --- | --- |
| Transition | Fade duration in seconds, subject to the light's support. |
| Adapt Only On State Change | Disables periodic adaptation. Lights still adapt when they enter the on state or when you resume adaptation. |
| Manual Control Reset Minutes | Resumes after this many minutes without another detected manual change. `0` disables timed reset. Reset takes effect at the next adaptation opportunity. |
| Separate Turn-On Commands | Sends brightness and color temperature in separate commands for bulbs that don't accept both together. |
| Send Split Delay | Seconds between those separate commands. |

Open `/daylight-preview` in Home Assistant to experiment with a day's brightness and temperature curves. The preview uses Home Assistant's location and time zone. It doesn't save settings; copy your chosen values into the setup forms.

## Manual control

Changing a light's brightness or color pauses adaptation for that entity. Cycle the light off and on to resume, or toggle the target's adaptation switch off and on to reset all its lights. Switch state and manual flags survive restarts.

Detection uses Home Assistant state changes. Daylight recognizes its own command contexts and ignores metadata and availability changes. Reports without a user context are also ignored during its transition plus a short reporting grace period, favoring fewer false overrides.

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

Licensed under Apache 2.0. The curve implementation derives from Adaptive Lighting; see [NOTICE](NOTICE) for attribution.
