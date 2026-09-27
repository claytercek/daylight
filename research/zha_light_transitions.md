# ZHA light transitions: physical stepping versus reported state

## Scope and version limits

Observed: with adaptation enabled, lights turned off/on **through Home Assistant** resume from old brightness/temperature; one flickers, another visibly steps (30 → 35 → 65 → 80) or stops partway. The steps occur **in the room and HA**, not just in the slider. This is not a physical-power startup/rejoin scenario, and report suppression alone cannot explain the physical effect.

Repository context only: Daylight's [manifest](../custom_components/daylight/manifest.json) is version 0.1.0; [test requirements](../requirements_test.txt) pin `pytest-homeassistant-custom-component==0.13.366`, not the user's running HA/ZHA version. The [README](../README.md#light-behavior) distinguishes the 90-second update interval, ongoing fade duration, snap-on adaptation, and optional split brightness/temperature commands. A separate internal trace supplied during this research confirms reactive off→on adaptation sends final brightness+temperature with `transition: 0.0` (or brightness then temperature when splitting), with no software intermediate ramp. It also identifies a possible late user-context report bypassing Daylight's 2-second suppression and falsely marking manual control; that mechanism is not runtime-confirmed. Internal tracing was not duplicated here.

Upstream source examined: ZHA **2.2.2** and HA core snapshot **55ced1def06f526fcdf01b071b8d0b206c10296e**, whose ZHA manifest requires 2.2.2. Installed versions, bulb models/firmware, quirks, and group use remain unknown; verify them before applying version-specific findings. [1–3]

## Where the fade happens

- **HA light service:** validates/transforms the request, applies configured light profiles, filters unsupported parameters, then calls the integration. It does not synthesize a stream of intermediate brightness values for ZHA. `transition` is seconds. A default `light_profiles.csv` transition can apply even when the light is already on; explicit action data overrides it. [1, 4]
- **HA's ZHA adapter:** converts Kelvin to mireds and forwards brightness, color and transition to the ZHA library. [2]
- **ZHA:** sends Zigbee Level Control `move_to_level_with_on_off` / `move_to_level` and Color Control `move_to_color_temp`, converting seconds with `int(10 * duration)`. It sequences commands and manages optimistic state/report handling; it does not render the physical fade. [3]
- **Bulb firmware:** executes the requested movement. The Zigbee Cluster Library's Level Control section specifies movement as continuous as technically practical, rather than a step function, subject to device capability. Transition fields use tenths of a second. This is a requested device behavior, not proof that a particular bulb implements concurrent brightness/color transitions correctly. [5]

## Defaults and sequencing that matter

ZHA's configured light transition defaults to **0 seconds**, enhanced/smooth power-on to **off**, and slider-jump prevention to **on**. Explicit `transition: 0` normally requests immediate movement, but ZHA has a device-specific minimum-transition implementation of 0.1 seconds. Do not infer that exception applies to these unknown bulbs. [3, 6]

A **bare turn-on** with neither brightness nor explicit transition can send an On command rather than a timed level command. The bulb's own On/Off/Level attributes then matter. ZCL defines `OnOffTransitionTime` and optional on/off-specific overrides. ZHA's internal `DEFAULT_ON_OFF_TRANSITION = 1` is an assumption for the report-suppression timer, **not a universal one-second fade injected into every light command**. [3, 5, 7]

A combined HA brightness+temperature request is **not atomic over Zigbee**. ZHA normally sends level/on first, then color; when the color cluster's `Execute_if_off` option is set, color goes first—even for an already-on light. This checks the cached Color Control options bit, not simply the advertised color capability. Commands are awaited for responses, not for completion of the physical fade, so transitions can overlap. [3]

For the confirmed Daylight `transition: 0.0` adaptation, ZHA normally sends **two zero-duration commands**, not intermediate levels or the configured default fade. A matching minimum-transition handler instead sends **0.1 seconds for each**. If the original HA turn-on started a fade from the old level, this later request changes brightness and temperature while that earlier action may still be in progress. That is a plausible interruption/flash mechanism, not proof of what this hardware does. `Execute_if_off` changes command order; it does not make them atomic or supply a gradual ramp. [3]

Enhanced power-on, when its conditions apply, instead sends low brightness (level 2), immediate color, then target brightness with the requested fade. This deliberately introduces extra commands to avoid showing the old color. It only helps a request ZHA sees while the light is off; adaptation arriving after an earlier HA On command may be too late for that path. ZHA disables this feature for group entities. Official docs caution that enabling it can temporarily congest the network when many lights start together. [3, 6]

## Why HA values are not a physical fade trace

On successful command responses, ZHA assigns target brightness/color optimistically. With slider-jump prevention enabled, it defers brightness/on-off report handling and skips polling while transitioning. In **2.2.2**, intermediate brightness reports are buffered and the last buffered value is applied when the timer completes; this can revise the optimistic target. Other releases may differ. [3]

The timer usually includes duration + 0.5 seconds, with a further 2 seconds for longer windows. These margins affect **state visibility**, not the commanded physical duration. They are separate from Daylight's documented manual-control grace window. Consequently a displayed target does not prove the bulb reached it, and a later displayed intermediate value does not by itself prove it stopped. Here, however, the user's physical observation rules out a reporting-only explanation. [3, 7]

## Targeted diagnostics

1. Record HA/ZHA versions, both bulbs' model/firmware, device signatures/quirks, coordinator firmware, ZHA options, profiles, and whether commands target a group or individual lights. Capture ZHA debug logs and device diagnostics. [6]
2. Disable adaptation and competing automations. Test one individual bulb: brightness-only fade while already on; temperature-only fade; then both together. Compare explicit `transition: 0` and a clearly observable duration such as 5 seconds. Repeat from HA-off, without cutting power.
3. Re-enable adaptation and repeat the same starting levels. Correlate physical timing/video with HA service calls, contexts, ZHA command logs, transition fields, responses/errors, and attribute reports. Identify whether 30/35/65/80 are **outgoing targets** or only reported levels.
4. If individual attributes fade smoothly but the combined request does not, test split commands with a sufficient delay to isolate overlapping fades. This distinguishes sequencing sensitivity from failure of basic dimming; it does not establish a universal workaround.
5. If the failure requires adaptation, investigate the original user turn-on followed by the confirmed zero-transition adaptation, duplicate/interleaved commands, and the separately identified possible false-manual classification. If an isolated single-attribute command fails too, investigate bulb/quirk/firmware behavior and delivery failures. These remain hypotheses until the command timeline is captured. Turning off slider prevention is only a reporting diagnostic, not a physical-flicker fix.

## Primary sources

1. [HA light service source, pinned snapshot](https://github.com/home-assistant/core/blob/55ced1def06f526fcdf01b071b8d0b206c10296e/homeassistant/components/light/__init__.py)
2. [HA ZHA light adapter](https://github.com/home-assistant/core/blob/55ced1def06f526fcdf01b071b8d0b206c10296e/homeassistant/components/zha/light.py) and [ZHA manifest](https://github.com/home-assistant/core/blob/55ced1def06f526fcdf01b071b8d0b206c10296e/homeassistant/components/zha/manifest.json)
3. [ZHA 2.2.2 light implementation](https://github.com/zigpy/zha/blob/2.2.2/zha/application/platforms/light/__init__.py): `async_turn_on`, `_async_turn_on_impl`, `async_handle_color_commands`, `handle_level_attribute_updated`, `async_transition_complete`, `LightGroup`.
4. [Official HA light documentation: default turn-on values](https://www.home-assistant.io/integrations/light/#default-turn-on-values)
5. [Zigbee Alliance ZCL revision 7, §3.10 Level Control and §5.2 Color Control](https://www.zigbeealliance.org/wp-content/uploads/2021/10/07-5123-07-ZigbeeClusterLibrary_Revision_7-1.pdf); [CSA-hosted ZCL revision 6](https://csa-iot.org/wp-content/uploads/2019/12/07-5123-06-zigbee-cluster-library-specification.pdf). Relevant passages were retrieved through indexed primary-source search; the PDF extraction tool returned only a partial document, so this is not an exhaustive conformance review.
6. [Official ZHA options and troubleshooting](https://www.home-assistant.io/integrations/zha/); [ZHA 2.2.2 LightOptions defaults](https://github.com/zigpy/zha/blob/2.2.2/zha/application/helpers.py)
7. [ZHA 2.2.2 light constants](https://github.com/zigpy/zha/blob/2.2.2/zha/application/platforms/light/const.py)
