# Zigbee reported state versus visible light

## Can reported brightness or temperature differ from what the light emits?

**Yes.** Home Assistant can display a requested brightness or color temperature before a bulb reaches it, and can retain that optimistic value afterward. A successful uncached Zigbee read is stronger evidence: it returns the device's logical attribute rather than HA's target or zigpy's old cache entry. It still is not photometry. Firmware-maintained attributes, emitted light, and human perception are different observations.

That distinction does not make arbitrary stale values acceptable under Zigbee. The Zigbee Cluster Library (ZCL) defines current color attributes and says they are updated “as fast as practical” during color changes. It also specifies continuous transitions. The cited clauses provide neither a fixed attribute-refresh cadence nor a guarantee of sensor-derived, instantaneously accurate optical telemetry. A discrepancy can reflect timing, representation, hardware limits, host bookkeeping, or a defect; it needs evidence before assigning a cause or declaring noncompliance. [1]

This note explains the October 10, 2026 IKEA experiments and the source-audited HA 2026.9.4 / ZHA 2.2.2 / zigpy 2.2.0 read path. No optical measurement was made in those experiments. The proposed optical comparison below has **not been executed or authorized**.

## Four meanings of “state”

| Observation | What it establishes | What it does not establish |
|---|---|---|
| HA light state | HA's projection of the ZHA entity, including command targets, assumed group state and color conversions | A fresh device value or measured emitted light |
| zigpy cached attribute | The last host-cached value and its host update timestamp | That the device or light still has that value |
| Successful uncached read | The logical attribute returned by the device for that transaction | An optical measurement, atomic multi-attribute snapshot, or proof that firmware maintains the register correctly |
| Optical measurement | Intensity and color at the sensor, within its calibration, geometry and timing limits | Universal perceived brightness or an unqualified whole-room/specimen measurement |

A newly received response can contain an old or inconsistent device-side register. “Uncached” rules out the host value cache as the answer source; it does not bypass bulb firmware. Conversely, disagreement with HA does not mean a direct read failed.

## What ZCL actually requires

References here use **ZCL Revision 8, document 07-5123-08**. Page references give printed chapter-page numbers followed by 1-based PDF pages. Quotes preserve keyword case, with PDF line wrapping normalized. In particular, “It is updated…” is not rewritten as an uppercase SHALL requirement. [1]

### CurrentLevel is not a calibrated brightness meter

Section 3.10.2.2.1, printed page 3-74 / PDF page 184:

> “The CurrentLevel attribute represents the current level of this device. The meaning of 'level' is device dependent.”

Section 3.19.2.2.1, printed page 3-199 / PDF page 309, defines lighting's minimum and maximum levels, then says:

> “All other values are application specific gradations from the minimum to the maximum level.”

Level 128 is therefore not a universal lumen value or a universal percentage of perceived brightness. The Off behavior in §3.10.2.1.1, printed page 3-73 / PDF page 183, can temporarily store CurrentLevel, reduce it during turn-off, then restore the stored value. A nonminimum register value can coexist with an off light by design. That off-state rule does not excuse ignoring an active on-state transition.

Move to Level, §3.10.2.3.1.2, printed page 3-78 / PDF page 188, states:

> “The movement SHALL be as continuous as technically practical, i.e., not a step function, and the time taken to move to the new level SHALL be equal to the value of the Transition time field, in tenths of a second, or as close to this as the device is able.”

The same clause permits disregarding the transition field **if the device cannot move at a variable rate**. That conditional exception is not unrestricted permission to ignore duration.

### Color attributes represent current control values, not just final targets

CurrentX and CurrentY, §§5.2.2.2.1.4–5, printed page 5-6 / PDF page 376, each include:

> “It is updated as fast as practical during commands that change the color.”

ColorTemperatureMireds, §5.2.2.2.1.8, printed page 5-7 / PDF page 377, says:

> “The ColorTemperatureMireds attribute contains a scaled inverse of the current value of the color temperature.”

It repeats the same “updated as fast as practical” sentence. CT uses integer mireds, with kelvins equal to 1,000,000 divided by mireds; ZCL's CurrentX/Y representation uses a divisor of 65536. Finite encoding is not a general allowance for arbitrary endpoint error.

Move to Color and Move to Color Temperature, §§5.2.2.3.11.3 and 5.2.2.3.14.3, printed pages 5-24 and 5-26 / PDF pages 394 and 396, both specify:

> “The movement SHALL be continuous, i.e., not a step function, and the time taken to move to the new color SHALL be equal to the Transition Time field, in 1/10ths of a second.”

These clauses do not specify a fixed telemetry refresh frequency. Smooth movement and finite-rate integer attribute updates are different requirements.

### Hardware and mode limits matter

Generic color usage, §5.2.2.3.1, printed page 5-15 / PDF page 385:

> “When asked to change color via one of these commands, the implementation SHALL select a color, within the limits of the hardware of the device, which is as close as possible to that requested. The determination as to the true representations of color is out of the scope of this specification.”

The continuation on printed page 5-16 / PDF page 386 allows an unachievable Move to Color Temperature target to clip at a physical limit, potentially before the requested duration. This is not an explanation of the observed Scene endpoint miss, nor permission to expose a false current attribute unrelated to the selected color.

ColorMode, §5.2.2.2.1.9, printed page 5-7 / PDF page 377:

> “The ColorMode attribute indicates which attributes are currently determining the color of the device.”

It is set at command reception, not upon optical completion. Read color values with their active mode. The experiments observed retained X/Y registers after returning to CT mode; this does not establish that active CT was stale, or that the standard permits every inactive attribute to remain stale indefinitely.

Level and Color RemainingTime are separate command metadata, both in tenths of a second (§3.10.2.2.2 and §5.2.2.2.1.3; printed pages 3-74 and 5-6 / PDF pages 184 and 376). A positive, zero or stalled timer is not an optical verdict, and the unit does not mandate 10 Hz updates.

### Sensor feedback is not guaranteed

DriftCompensation, §5.2.2.2.1.6 and Table 5.4, printed pages 5-6–5-7 / PDF pages 376–377, lists mechanisms including **None** as well as optical feedback. The reviewed clauses specify no quantitative optical-accuracy or sensor-latency bound for these attributes.

Silicon Labs' reference color server calculates intermediate attributes, writes them, then calls application PWM callbacks. That is an example of calculated control state followed by a separate actuation responsibility, not a required optical-sensor measurement. It is not evidence that IKEA uses that code or its timing. [2]

Scenes also have a distinct contract. Recall Scene, §3.7.2.4.7.2, printed page 3-52 / PDF page 162, requires setting stored cluster attributes and corresponding state, but says:

> “However, the exact transition is manufacturer dependent.”

The preceding text recommends a gradual transition where possible. Scene behavior cannot simply be substituted for the Move command requirements, and manufacturer-dependent shape does not erase the scene endpoint obligation.

## What the deployed uncached read does

For the recorded ordinary, unquirked input clusters, the audited path is:

1. HA's `zha/devices/clusters/attributes/value` handler resolves the cluster and calls `read_attributes([attribute], allow_cache=False, only_cache=False)`. It calls the cluster directly, not the light entity's guarded polling method. [3]
2. zigpy bypasses its cached-value and cached-unsupported shortcuts and constructs a global **Read Attributes** request for the attribute ID. Each valid invocation reaching this implementation initiates a fresh transaction; failures, cancellation and retries prevent any exactly-once radio guarantee. [4]
3. A successful response supplies the device value, updates zigpy's attribute cache and emits an `AttributeReadEvent`. An unsolicited report uses a different event path. The HA handler stringifies the successful value; an envelope containing `"None"` is not a valid numeric read. [3, 4]

ZCL §2.5.2.1.5, printed page 2-13 / PDF page 65, requires:

> “The attribute value field is variable in length and SHALL contain the current value of this attribute.”

That is a requirement on the attribute response, not a demand for fresh photometry. Reports have minimum/maximum intervals and change thresholds (§§2.5.7 and 2.5.11). Sparse reporting can explain stale host state; it does not explain static results from genuinely fresh reads by itself. [1]

### Host updates are uneven

The pinned ZHA light subscribes directly to read/report/update events for **OnOff and CurrentLevel**, not Color Control. A level read can immediately change the HA projection or replace the buffered level during a transition. A CT, X/Y, mode or color-timer read updates zigpy but has no equivalent direct color callback to update the light's fields. Entity polling separately reads and assigns color, subject to its own guards. Thus HA can remain optimistic about color after a successful direct CT read. [5]

Ordinary successful light commands assign requested targets optimistically. For a 30-second request, the usual ZHA software transition window is 32.5 seconds, started after the relevant command awaits. It is not the bulb's RemainingTime and does not extend its requested fade. Reads/reports do not restart that timer; a buffered intermediate level can later become the displayed level. Group assumed state is likewise bookkeeping, not per-member verification. zigpy multicast returns a synthetic success after sending rather than collecting application acknowledgments from every bulb. [6]

### Reads are active, but are not transition commands

Neither the specified Read Attributes operation nor the audited host path sends a Move, Stop, Recall, attribute write, transition restart or corrective actuator command. A global Read Attributes command ID of 0 is distinct from cluster-specific MoveToLevel ID 0. [1, 4]

Reads still add radio traffic, queueing and possible retries. They update observation state and may influence later software decisions. Unknown firmware side effects cannot be excluded merely by inspecting host code. The evidence establishes read-induced HA corrections, **not read-induced optical interruption**.

Measurements were sequential transactions. A table row beginning at +5 seconds is not one instantaneous level/color/timer snapshot; it can straddle a software frame. Response latency and host timestamps do not identify the exact optical sampling instant.

## Which experimental conclusions survive the optical gap?

The detailed trials are recorded in [IKEA light transitions](ikea_light_transitions.md). The following classifications concern the tested specimens and paths, not every IKEA bulb.

| Finding | Robust conclusion | Still optically unverified |
|---|---|---|
| Ordinary raw Level/CT pairs, including reversed order | Only the second-commanded attribute progressed at sampled times; independent-property controls progressed | Whether the first optical property never moved, stopped, or continued despite stale registers |
| GU10 raw XY pairs | Exact XY origin and mode were accepted despite a CT-only capability bitmap; both orders again advanced only the second attribute | Actual chromaticity, visible cancellation, or a shared internal transition engine; no E12/group XY replication ran |
| Scene Recall without override | Both endpoint attributes were present by the first +3-second read | An optical snap or the trajectory before that sample |
| Scene Recall with explicit override | Both specimens showed concurrent attribute progression; E12 later repeated level 128 / CT 361 and timers 0 / 67 through +45 seconds | Concurrent optical fading or a visible CT stall; GU10 observation ended at +23 seconds |
| Stored Level Options = 2 / Color Options = 1 | Both attributes changed, ending at 128 / 412 rather than requested 128 / 400; exact option restoration was verified | An optical overshoot or isolated contribution of either option; CT was exactly 400 at the middle sample |
| Software-stepped group run | All six progressed in both reported dimensions and reached 128 / 400 after 28 paired frames / 56 group calls | Smoothness, every-frame delivery, native positive-duration overlap, or production reliability |
| Cleanup | Fresh logical state, option bytes and empty scene tables were checked | Measured restoration of emitted light; original pre-scene CurrentScene metadata was not restored bit-for-bit |

These results cannot be dismissed as HA target values or old zigpy cache hits on the audited read path. They also cannot establish a universal physical inability to fade two properties. Scene and stored-option logical overlap already constrain that broad interpretation. Optical uncertainty is not evidence that the lights secretly performed correctly; it is a measurement gap.

### The no-intermediate-read Scene control

After one E12 Recall with override 200 deciseconds, there were **40.000891 seconds with no operator requests** following service completion. The subsequent sequential sample again returned level 128, CT 361 mired, and remaining times 0 / 67 deciseconds.

This reproduces the late logical endpoint miss without intermediate operator reads. Those reads are not needed to obtain that observed outcome. It does not prove a radio-silent interval, since background polling was not excluded; the terminal sample itself was still a read. One late sample establishes no trajectory or pre-read optical state, and does not exclude terminal-read side effects. It is a useful control for a narrow causal question, not a substitute for optics.

## Smallest useful optical comparison — design only

**This test has not been run and is not authorization for any light command, diagnostic read, adaptation change or scene allocation.** Passive video of an ordinary transition is a useful first screen. A controlled comparison needs separate approval.

Use one existing native group and one optically separable member; keep all control delivery group-addressed. Start with the already-tested ordinary combined 30-second path, without introducing Scenes, settings or XY variants.

- Fix a camera on a matte neutral surface predominantly lit by that member. Lock exposure, gain, aperture, white balance, focus and tone processing; avoid clipping, changing daylight, PWM bands and automatic HDR. Record continuously from at least five seconds before dispatch through +60 seconds. If members cannot be separated optically, call it aggregate group output.
- Prefer a calibrated tristimulus sensor logging intensity and chromaticity at about 10 Hz. A camera supports relative intensity/color comparison, not exact kelvins without calibration. A lux-only sensor cannot settle the color question.
- Record brightness-only and CT-only controls, then two matched combined trials: one without intermediate diagnostic reads, one with sparse uncached level/active-color reads near +5, +15 and +35 seconds. Keep endpoint reads after the +60-second observation window and continue recording through them.
- Obtain settled optical references for the four level/CT combinations: 204 / 285, 128 / 285, 204 / 400 and 128 / 400. CT alone can change camera intensity and luminous flux, so an intensity change is not automatically evidence of dimming.
- Synchronize optical time with dispatch, completion and individual read intervals. Check for discontinuities around reads. If the read/no-read trials differ, repeat in reversed order before assigning causation; delivery, ambient drift and order effects remain alternatives.

Static first-property readback with both optical dimensions progressing would demonstrate register/output divergence. Progressing attributes with a static optical dimension would show the converse. Agreement between optics and the second-property-only readback would support an actual output failure on that path. These conclusions require changes above baseline noise and the single-property controls. An ordinary-group comparison would not validate Scene optics or software-step smoothness; those paths would require their own authorized observations.

## Sources and evidence

### Durable primary sources

1. [CSA Zigbee Cluster Library Revision 8, 07-5123-08](https://csa-iot.org/wp-content/uploads/2022/01/07-5123-08-Zigbee-Cluster-Library-1.pdf). Exact sections and printed/PDF pages are given above. The inspected source does not establish later-revision or IKEA-specific firmware behavior.
2. Silicon Labs reference [color server](https://github.com/SiliconLabs/gecko_sdk/blob/8a2efd2cb191cb250fd4c1e655b16b8a80be1997/protocol/zigbee/app/framework/plugin/color-control-server/color-control-server.c) and [PWM callbacks](https://github.com/SiliconLabs/gecko_sdk/blob/8a2efd2cb191cb250fd4c1e655b16b8a80be1997/protocol/zigbee/app/framework/plugin/color-control-server/color-control-server-cb.c). `tempTransitionEventHandler` and `xyTransitionEventHandler` write calculated attributes before invoking hardware callbacks.
3. Pinned HA [uncached websocket handler](https://github.com/home-assistant/core/blob/9212531f40a0b7b23229a90d688dd79d9dfccff4/homeassistant/components/zha/websocket_api.py#L837-L890), corresponding to 2026.9.4.
4. Pinned zigpy 2.2.0 [read/cache processing](https://github.com/zigpy/zigpy/blob/512f3cfdb6ee049481c792f326343c575b3181f6/zigpy/zcl/__init__.py#L1069-L1353), [global command definitions](https://github.com/zigpy/zigpy/blob/512f3cfdb6ee049481c792f326343c575b3181f6/zigpy/zcl/foundation.py#L1405-L1444), and [request transport](https://github.com/zigpy/zigpy/blob/512f3cfdb6ee049481c792f326343c575b3181f6/zigpy/device.py#L609-L727).
5. Pinned ZHA 2.2.2 [light event subscriptions](https://github.com/zigpy/zha/blob/28272e99e1d734b6b339aaec91540a02719f1fd6/zha/application/platforms/light/__init__.py#L1011-L1043), [level handler](https://github.com/zigpy/zha/blob/28272e99e1d734b6b339aaec91540a02719f1fd6/zha/application/platforms/light/__init__.py#L1101-L1126), and [explicit entity polling](https://github.com/zigpy/zha/blob/28272e99e1d734b6b339aaec91540a02719f1fd6/zha/application/platforms/light/__init__.py#L1194-L1293).
6. Pinned ZHA [command optimism](https://github.com/zigpy/zha/blob/28272e99e1d734b6b339aaec91540a02719f1fd6/zha/application/platforms/light/__init__.py#L390-L646), [transition timer/buffer handling](https://github.com/zigpy/zha/blob/28272e99e1d734b6b339aaec91540a02719f1fd6/zha/application/platforms/light/__init__.py#L786-L874), [group state handling](https://github.com/zigpy/zha/blob/28272e99e1d734b6b339aaec91540a02719f1fd6/zha/application/platforms/light/__init__.py#L1438-L1490), and zigpy [multicast synthetic success](https://github.com/zigpy/zigpy/blob/512f3cfdb6ee049481c792f326343c575b3181f6/zigpy/group.py#L61-L85).

### Local audit provenance

This note synthesizes the primary-source investigations `/tmp/daylight-zigbee-visible-semantics.md` and `/tmp/daylight-zha-read-semantics.md`, plus `/tmp/daylight-optical-inference-audit.md`. These are local working evidence, not durable external publications or independent optical replication.

The audit covers the ordinary trials in `/tmp/daylight-smooth-20261010/final-report.md`, the Scene reports `/tmp/daylight-scene-override-report.md` and `/tmp/daylight-scene-no-read-control.md`, the settings and XY reports `/tmp/daylight-zha-settings-live-report.md` and `/tmp/daylight-xy-native-live-report.md`, and `/tmp/daylight-software-fade-report.md`. Source and execution facts are accepted as documented by those audits; this synthesis made no HA/device calls and did not independently re-run the captures.
