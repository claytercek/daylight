# IKEA TRÅDFRI simultaneous brightness/color-temperature transitions

## Question and scope

This note investigates persistent or skipped brightness changes when Daylight sends brightness and color temperature together to these live ZHA devices:

| Zigbee model reported by device | Firmware reported by device | Maintainer-library product mapping |
|---|---:|---|
| `TRADFRI bulb GU10 WS 380lm` | `0x03000021` | IKEA LED2005R5/LED2106R3 family [1] |
| `TRADFRI bulb E12 WS globe 450lm` | `0x01010020` | IKEA LED2101G4 [1] |

The live installation is Home Assistant 2026.9.4 with ZHA 2.2.2, and neither device has a ZHA quirk applied. The ZHA conclusions below are pinned to 2.2.2. The hardware conclusions are narrower because no issue found names both exact device strings and these firmware versions.

## Concise conclusion

Controlled tests on October 10, 2026 reproduced a brightness endpoint miss on **one specimen of each listed model/firmware pair**. After a combined command from level 204 (80%) and 285 mired (about 3500 K) to level 128 (50%) and 400 mired (2500 K), an uncached device read found the temperature at 400 but the level still at 204. This occurred with 120-second transitions on both specimens and with a 5-second transition on the GU10. Brightness-only over 120 seconds reached 128 on the GU10, while combined `transition: 0` reached 128/400 on both models.

The evidence establishes a combined-positive-transition brightness failure for these two specimens under Home Assistant 2026.9.4 and ZHA 2.2.2. It does **not** establish the precise firmware/RF cause, a physical fade trajectory, or that all IKEA bulbs are affected. ZHA sends level and color-temperature as separate commands whose physical durations can overlap; the upstream reports make an IKEA overlap limitation plausible, but the tests do not isolate that mechanism.

The smallest verified existing workaround is to set **Transition to `0`** for the affected Daylight target and leave **Separate Turn-On Commands disabled**. This preserves Daylight's native-group initial turn-on path and made both tested specimens reach the requested endpoint, but periodic updates step instead of fade. No workaround or code change was permanently deployed during testing.

## Live hardware validation (2026-10-10)

On Home Assistant 2026.9.4 with ZHA 2.2.2, each trial targeted one already-on leaf while adaptation was paused, primed it to 80%, sent the trial command to 50%/2500 K, waited beyond the transition, and read Level Control `current_level` and Color Control `color_temperature`. HA's handler forces `allow_cache=False, only_cache=False` [14]; the captured 2026.9.4 handler was byte-identical to the locally installed 2026.9.3 copy. Levels 204/128 correspond to 80%/50%; 285/400 mired correspond to about 3500 K/2500 K.

| Trial | Specimen | Command after 80% prime | Uncached level / mired | Outcome |
|---|---|---|---|---|
| A | GU10 `0x03000021` | 50%, CT omitted, 120 s; prime 2500 K | Not read | HA-only; endpoint-inconclusive |
| B | GU10 `0x03000021` | 50% + unchanged 2500 K, 120 s | 204/400 at +163 s | Brightness missed |
| A2 | GU10 `0x03000021` | 50%, CT omitted, 120 s; prime 2500 K | 128/400 at +135 s | Passed |
| C | GU10 `0x03000021` | 50% + 2500 K, 120 s; prime 3500 K | 204/400 at +135 s | CT reached; brightness missed |
| D | GU10 `0x03000021` | 2500 K/0 s, await + 0.5 s, then 50%/120 s | 128/400 at +135 s | Final endpoint passed once |
| E | GU10 `0x03000021` | 50% + 2500 K, 0 s; prime 3500 K | 128/400 at +3 s | Passed |
| F | GU10 `0x03000021` | 50% + 2500 K, 5 s; prime 3500 K | 204/400 at +10 s | CT reached; brightness missed |
| G | E12 `0x01010020` | 50% + 2500 K, 120 s; prime 3500 K | 204/400 at +135 s | CT reached; brightness missed |
| H | E12 `0x01010020` | 50% + 2500 K, 0 s; prime 3500 K | 128/400 at +3 s and about +15 s | Passed; no rollback |

D–H started from endpoint-confirmed 204/285; earlier primes were HA-confirmed. Trial D had no endpoint read between the color snap and brightness fade, so it does not prove fade shape or reliability; that sequence is not implemented, and a nominal CT-only HA call with `transition: 0` also emits ZHA's cached brightness [7].

HA optimistically showed 128 immediately in failed trials; the later explicit read returned 204 and induced the HA correction. It was not a natural late report or a false-manual-control reproduction. No natural fresh-context late control change appeared in any trial window. The independent B audit verified the leaf/endpoint mapping, no intervening captured controls, and the read before cleanup. These are endpoint results from one specimen per model/firmware, without packet capture or photometric fade measurement; they do not prove the precise firmware/RF cause or explain the historical 18:18 event.

Exact timing and commands are retained in the operator-protected `/tmp/daylight-fade-tests-20261010T105510-0400/diagnostic-summary.md`; underlying captures are in the four `/tmp/daylight-fade-tests-20261010T10*-0400` directories, with the independent B audit at `/tmp/daylight-exact-replay/B-read-audit.md`. Cleanup restored adaptation and sent six 80%/3600 K zero-transition calls; final uncached reads found all six room leaves at 204/277 mired, matching HA and the current target. Saved configuration was unchanged, and nothing was deployed.

## Findings

### 1. The affected products and firmware are real IKEA product families

The Zigbee2MQTT device-definition source maps the exact GU10 model string to `LED2005R5/LED2106R3` and the exact E12 model string to `LED2101G4`; both are declared white-spectrum, color-temperature lights. This is useful identification evidence, not proof of behavior under ZHA. [1]

IKEA's official release notes list white-spectrum E14/GU10 firmware **3.0.21** for product IDs LED2106/LED2107 and white-spectrum firmware **1.1.20** for LED2101G4. Both entries say only “Update to support new product revisions”; they do not claim a transition fix or document simultaneous brightness/temperature behavior. The corresponding raw file-version integers in the IKEA OTA catalog are `50331681` (`0x03000021`) and `16842784` (`0x01010020`). [2, 3]

**Confidence: high** for product/firmware identification; **high** that IKEA's published notes contain no claimed transition fix. Absence from release notes does not prove the firmware lacks an unpublished fix.

### 2. An upstream issue confirms the limitation for the GU10 family

Zigbee2MQTT issue #9782 is specifically for **IKEA LED2005R5**, the family to which the exact GU10 string maps. The reporter found that `state: ON` plus a transition faded normally, but adding `color_temp` made the bulb jump immediately to its previous/full brightness. The Zigbee2MQTT maintainer answered that IKEA bulbs “can only transition on 1 property”; to transition both, commands must be serialized at the application level. [4]

A broader Zigbee2MQTT issue reproduces ignored transitions when brightness and color temperature are supplied together on “various Ikea lights.” The maintainer again attributed this to IKEA lights not supporting both transitions simultaneously. One user reported two separate commands worked “most of the time,” which is explicitly weaker than a reliable fix. [5]

A ZHA issue in Home Assistant reports that a combined 20-second brightness/temperature transition changed brightness but not temperature. A ZHA maintainer called this a problem with “some IKEA lights,” recommended `transition: 0` for affected lights, and suggested ZHA might eventually remove the transition from one of the two commands. The issue was closed as not planned, not fixed. [6]

**Confidence: high** that LED2005R5-family firmware has had this limitation. The live endpoint tests now confirm the same outcome pattern on the current GU10 and E12 specimens, but they do not prove that the upstream reports and the live failures share the same internal cause. No exact-model upstream report was found for LED2101G4.

### 3. ZHA 2.2.2 does not make brightness + temperature atomic

In pinned ZHA 2.2.2, a request containing brightness and color temperature ordinarily causes:

1. `move_to_level_with_on_off(level, int(10 * duration))`; then
2. `move_to_color_temp(color_temp_mireds, int(10 * duration))`.

The order reverses when the device advertises Color Control's `Execute_if_off` option. In either order, these are distinct Zigbee cluster commands. ZHA awaits the command result before sending the next command, but it does **not** wait for the requested physical transition duration. Thus two 20-second commands can overlap for almost all of the 20 seconds. [7]

This agrees with the ZHA maintainer's statement that Zigbee ZCL has no single brightness-plus-color-temperature command. [6]

**Confidence: high**, applicable specifically to ZHA 2.2.2 and the no-quirk live devices.

### 4. Daylight explicitly sends both targets every eligible adaptation

For a light advertising color-temperature support, Daylight's `compute_turn_on_kwargs()` unconditionally creates a `brightness_pct` target and a `color_temp_kelvin` target, then adds `transition`. It does not compare either target with the current state before including it. `_adapt_kwargs()` uses this same function for already-on periodic adaptation and intercepted turn-on adaptation. [8]

Consequently, even when only one target has materially changed—or rounding produces the same targets as last time—an eligible adaptation still explicitly asks HA/ZHA to set both brightness and temperature. This repeatedly exercises the combined-command condition that failed in the live tests. This is a source-code fact; it does not by itself prove the precise bulb or RF failure mechanism.

**Confidence: high**, applicable to the current checked-out Daylight source.

### 5. What Daylight splitting changes—and what it does not

With `separate_turn_on_commands` disabled, Daylight issues one HA service call containing both targets; ZHA still converts that call into two Zigbee commands as described above. With splitting enabled, Daylight issues a brightness-only HA call, sleeps for `send_split_delay`, then issues a temperature-only HA call. Each part retains the same transition duration. [9]

Therefore:

- a split delay shorter than the transition still creates overlapping physical transitions;
- a delay at least as long as the first transition is a closer implementation of the serialization recommended in #9782, but it increases update latency and has not been validated on these bulbs;
- splitting delivery does not itself retry or verify that physical brightness reached the target;
- for intercepted off→on adaptation, Daylight's first command uses `transition: 0`, waits for its configured delay and the on-state report, then sends temperature with `transition: 0`; this is different from periodic long fades, but is also unverified on these bulbs. [9]

Evidence about outcomes is mixed:

- An older HA/ZHA IKEA issue reproduced brightness flickering back to its old value when brightness and temperature were combined. A user reported that scripts with a two-second wait between brightness and color solved it; affected named models included an older GU10 WS 400 lm, not either bulb here. [10]
- A Hue-bridge issue with IKEA bulbs found that brightness physically stalled while the UI reported the target. Two commands worked, and explicit `transition: 0` also worked. Models were not identified, and the firmware was 2.3.095. [11]
- Adaptive Lighting issue #207 contains reports of recurring IKEA GU10/E12 pulsing. One user still saw pulsing with zero transition and split delays of 5–100 ms. Later, a user with IKEA color bulbs reported no improvement even with a split delay longer than the transition. Those comments involve other/unspecified IKEA models and Hue, deCONZ, Z2M, Matter, or ZHA, so they are counterexamples to a *universal* split fix, not direct tests of these white-spectrum bulbs. [12]

**Confidence: high** that a short split does not guarantee serialization. Combined `transition: 0` is endpoint-verified on the tested GU10 and E12 specimens. Fully serialized positive-duration commands and Daylight's current split mode remain unverified on these specimens and are not the recommended operational workaround.

### 6. Reporting can expose an intermediate level after the physical command problem

ZHA 2.2.2 optimistically stores the requested level, buffers brightness reports while its transition flag is active, and applies the last buffered report when its timer expires. For a normal brightness/color transition the timer starts at requested duration plus 0.5 seconds. When that resulting window is at least 10 seconds, ZHA adds another 2 seconds: for ordinary long transitions the report-processing window is therefore **requested duration + 2.5 seconds**. This changes when HA accepts/re-emits a bulb's reported brightness; it does not extend or repair the physical transition. [7]

Daylight separately suppresses ambiguous context-free reports for command transition duration plus **2.0 seconds** (plus split delay for a split command). That grace is independent of ZHA's +2.5-second long-transition reporting window and is being investigated separately. [9, 13]

Because ZHA applies its last buffered level at transition completion, a bulb that physically stops at an intermediate brightness can first appear optimistic and later “fall back” in HA. That mechanism can explain delayed state correction, but it cannot by itself explain a visibly stalled bulb.

**Confidence: high**, applicable to ZHA 2.2.2 and the current Daylight source. Note that ZHA's extra 2 seconds applies to long windows; shorter transition windows get only the initial +0.5 seconds.

## Facts versus hypotheses

### Established by source, issues, and live endpoint tests

- The GU10 exact device string belongs to LED2005R5/LED2106R3; the E12 exact string belongs to LED2101G4. [1]
- Their reported raw firmware values correspond to IKEA-published 3.0.21 and 1.1.20 releases. IKEA documents no transition-related change for those releases. [2, 3]
- LED2005R5 has a maintainer-confirmed upstream report that adding color temperature prevents the desired brightness/on transition. [4]
- ZHA 2.2.2 sends level and temperature as separate commands whose physical durations may overlap. [7]
- Daylight explicitly includes both values for every eligible CT adaptation. [8]
- On the tested GU10, brightness-only 120 seconds reached the endpoint, while combined 120-second and 5-second commands left brightness at the prime level and reached the requested temperature.
- On the tested E12, a combined 120-second command left brightness at the prime level and reached the requested temperature.
- Combined `transition: 0` reached both requested endpoints on both tested specimens; the E12 remained there on a second read about 15 seconds later.
- Daylight splitting inserts a delay between HA calls but does not guarantee non-overlap unless the delay covers the first transition. [9]
- ZHA's long-transition reporting timer is duration + 2.5 seconds; Daylight's separate grace is duration + 2 seconds. [7, 13]

### Plausible or unproved

- The live failures are caused by the exact firmware limitation discussed in the upstream issues. The command/result pattern and GU10 family match support this interpretation, but there is no RF trace or firmware-level diagnosis.
- Fully serializing brightness and temperature with positive transitions will be reliable. Trial D passed one final endpoint check, but did not capture its intermediate endpoint or establish fade shape or repeatability.
- Daylight's existing Separate Turn-On Commands mode fixes the failure. Its short delay can leave transitions overlapping and has not been tested as a fix on these specimens.
- A natural late report causes Daylight to classify the bulb as manually controlled or makes a transient failure persistent. No such natural late control-attribute change was observed in A/A2/B/C; the recorded HA corrections were diagnostic-read-induced.
- The historical 18:18 event was caused by a missing dispatch or by this endpoint failure. These tests do not reconstruct that event.

## Applicability, workaround, and remaining evidence gaps

The live results apply to **one GU10 on firmware `0x03000021` and one E12 on firmware `0x01010020`, using Home Assistant 2026.9.4 and ZHA 2.2.2**. They do not generalize to every bulb of either model, all IKEA products, other firmware, or other Zigbee stacks. The absence of quirks means no model-specific ZHA workaround altered these specimens' generic ZHA 2.2.2 path.

For these affected targets, use Transition `0` and keep Separate Turn-On Commands disabled. This is the only existing Daylight configuration tested successfully on both specimens. It removes periodic fades; it does not claim to repair the bulb behavior. Enabling Separate Turn-On Commands is not a confirmed substitute: the current sequence sends brightness first and temperature later with the same fade, so a short split delay may still overlap, and it also disables the pending native-multicast optimization.

The October 9 baseline—four complete six-leaf batches, 240 seconds apart, at a static 50%/2500 K target—showed no changes. That supports capture continuity only; it does not explain the historical event or prove reliability. Stronger causal evidence would require repeated D-style trials plus radio capture and physical-light measurement. Those are separate from the endpoint workaround documented here.

## Primary and maintainer sources

1. [zigbee-herdsman-converters IKEA definitions: LED2101G4 and LED2005R5/LED2106R3](https://github.com/Koenkk/zigbee-herdsman-converters/blob/d436575ffdcf9e7d35a9bfd0a6d45b47fc0e6458/src/devices/ikea.ts#L283-L289) (GU10 definition is later in the same file at lines 404–417).
2. [IKEA Home smart release notes](https://ww8.ikea.com/ikeahomesmart/releasenotes/releasenotes.html) — release 1.27.1 entries for LED2106/LED2107 3.0.21 and LED2101G4 1.1.20.
3. [IKEA-hosted OTA for raw version 50331681 / `0x03000021`](https://fw.ota.homesmart.ikea.com/files/zingo-ws_release_prod_v50331681_969bee21-eca6-4f10-a4c0-9685c0cd5d52.ota) and [raw version 16842784 / `0x01010020`](https://fw.ota.homesmart.ikea.com/files/zingo-kt-bulb-hwpwmcs-ws_release_prod_v16842784_32dc5ff1-4fa4-4960-a847-5e548a81047c.ota); discoverable in the [OTA catalog](https://github.com/Koenkk/zigbee-OTA/blob/master/index.json).
4. [zigbee2mqtt #9782: IKEA LED2005R5 does not transition when power and temperature are provided](https://github.com/Koenkk/zigbee2mqtt/issues/9782), including [maintainer limitation statement](https://github.com/Koenkk/zigbee2mqtt/issues/9782#issuecomment-974900836) and [serialization recommendation](https://github.com/Koenkk/zigbee2mqtt/issues/9782#issuecomment-975212260).
5. [zigbee2mqtt #19186: transition ignored with brightness and color temperature](https://github.com/Koenkk/zigbee2mqtt/issues/19186), including [maintainer explanation](https://github.com/Koenkk/zigbee2mqtt/issues/19186#issuecomment-1750807616) and [“most of the time” split result](https://github.com/Koenkk/zigbee2mqtt/issues/19186#issuecomment-1883832488).
6. [Home Assistant core #99850: ZHA combined transition fails one attribute](https://github.com/home-assistant/core/issues/99850), especially the [ZHA maintainer response](https://github.com/home-assistant/core/issues/99850#issuecomment-1710839445).
7. [ZHA 2.2.2 light implementation](https://github.com/zigpy/zha/blob/2.2.2/zha/application/platforms/light/__init__.py): `async_turn_on`, `_async_turn_on_impl`, `async_handle_color_commands`, `async_transition_start_timer`, and `async_transition_complete`; [2.2.2 constants](https://github.com/zigpy/zha/blob/2.2.2/zha/application/platforms/light/const.py).
8. Daylight source: [`adaptation.py`](../custom_components/daylight/adaptation.py) and [`switch.py` `_adapt_kwargs`](../custom_components/daylight/switch.py).
9. Daylight source: [`switch.py` `_async_send`, `_prepare_turn_on_values`, and `_async_finish_turn_on`](../custom_components/daylight/switch.py).
10. [Home Assistant core #21888: IKEA brightness flicker/stall with combined temperature](https://github.com/home-assistant/core/issues/21888), including [named older models](https://github.com/home-assistant/core/issues/21888#issuecomment-473173678) and [two-second serialized workaround](https://github.com/home-assistant/core/issues/21888#issuecomment-513563806).
11. [Home Assistant core #81866: physical IKEA brightness stall through Hue](https://github.com/home-assistant/core/issues/81866), including [separate-command result](https://github.com/home-assistant/core/issues/81866#issuecomment-1351906879) and [`transition: 0` result](https://github.com/home-assistant/core/issues/81866#issuecomment-1351978111).
12. [Adaptive Lighting #207: recurring TRÅDFRI GU10 brightness cycling](https://github.com/basnijholt/adaptive-lighting/issues/207), including [failure with 5–100 ms split delays](https://github.com/basnijholt/adaptive-lighting/issues/207#issuecomment-1663333107), [proposal to delay longer than the transition](https://github.com/basnijholt/adaptive-lighting/issues/207#issuecomment-2510283101), and [reported no improvement](https://github.com/basnijholt/adaptive-lighting/issues/207#issuecomment-2527832981).
13. Daylight source: [`target.py` `SUPPRESSION_GRACE_SECONDS` and `record_command`](../custom_components/daylight/target.py).
14. [Home Assistant 2026.9.4 ZHA websocket attribute-read handler](https://github.com/home-assistant/core/blob/2026.9.4/homeassistant/components/zha/websocket_api.py#L837-L890), especially the forced `allow_cache=False, only_cache=False` call.

## October 10 follow-up: measured overlap tests

This addendum preserves the earlier endpoint findings and extends them with midpoint measurements. The earlier zero-transition workaround remains an endpoint workaround, not a solution to the requirement for overlapping positive-duration fades. The raw Level/CT paths below did not demonstrate overlap. Subsequent native-Scene Recall with an explicit duration override demonstrated simultaneous device-attribute progression on both specimens, but did not verify both requested endpoints: E12 CT stalled, and GU10 observation ended before CT 400 was verified. No group Scene trial ran. The Scene path is not a reliable endpoint solution. A separate software-stepped, 30-second native-group trial progressed both attributes and reached both endpoints on all six lights. Neither experiment establishes optical smoothness or production reliability.

### Independent fades work; overlapping commands progress only the second property

The same GU10 `0x03000021` and E12 `0x01010020` specimens were tested already on, with adaptation paused, on HA 2026.9.4 / ZHA 2.2.2 without quirks. Each trial uncached-confirmed a prime of level 204 / 285 mired, then requested level 128 and/or CT 400 over 30 seconds. The following are representative uncached **level / CT-mired** measurements near +5, +15 and +35 seconds:

| Path | Specimens | Measured trajectory |
|---|---|---|
| Brightness alone | GU10 and E12, independently | 192 / 285 → 167 / 285 → 128 / 285 |
| Raw CT alone | GU10 and E12, independently | 204 / 304 → 204 / 342 → 204 / 400 |
| Raw CT, then Level with coupling disabled | GU10 and E12 | 192 / 285 → 167 / 285 → 128 / 285 |
| Raw Level with coupling disabled, then CT | GU10 | 204 / 305 → 204 / 343 → 204 / 400 |
| Same Level-first path | E12 | 204 / 304 → 204 / 342 → 204 / 400 |

Both properties independently fade through measured midpoints. When commands overlap, only the second-commanded property progresses at the sampled times; the first remains at its prime. Raw calls bypass ZHA's light adapter, including its cached-level injection on explicit-transition CT-only HA calls. GU10 comparisons included both orders of `MoveToLevelWithOnOff` (command 4), plain `MoveToLevel` (command 0), and command 0 with `options_mask: 2, options_override: 0` (transient coupling disable). E12 covered both orders with coupling disabled, plus CT-first/command 4 and command 0-first/CT. None demonstrated overlap. These are per-command overrides, not persistent attribute writes. [15, 16]

On GU10 only, bounded continuous `MoveColorTemperature` (command 75, rate 10 mired/s, bounds 285–400 mired) also failed to overlap with an 11.5-second Level fade in either order. At +3/+7/+15 seconds, CT-first left CT 285 while level progressed 186 → 160 → 128; Level-first left level 204 while CT progressed 316 → 357 → 400. CT alone passed the same midpoint control; an explicit bounded stop completed cleanup. [15, 16]

Native multicast reproduced the failure, rather than solving it: raw commands in both orders on the three-member floor group again progressed only the second property. Ordinary combined HA calls to the existing native floor and paper ZHA groups left **all six members** at level 204 while CT progressed through 305–306 and 343–344 to 400. Only diagnostic reads used individual members; command delivery was native group multicast, not unicast fanout. One failed prerequisite prime was excluded before a bounded retry. [15]

### Configuration, interpretation, and restoration

Actual configuration was `enhanced_light_transition = true`, `default_light_transition = 1.0`, `light_transitioning_flag = true`, and `group_members_assume_state = true`. Enhanced transitions select an off→on branch, not a fix for these already-on trials; explicit durations override the default. Reporting flags and assumed group state affect bookkeeping. Both specimens reported Color Options 0 and Level Options 0, and disabling coupling per command did not repair overlap. No already-on configuration fix was proved, and no configuration flips or deployment occurred. [7, 15]

The measurements are intrusive, successive uncached attribute reads, not atomic samples or natural manual-feedback events. They can update HA state; they do not reproduce spontaneous corrections or false manual takeover. There was no RF capture or photometric measurement. The directional test was 80% / 3500 K → 50% / 2500 K, not a reliability study of every direction, duration, firmware, or entry point. ZHA's raw service discards reply payloads **but checks a returned named status and raises on non-success**; successful service completion alone does not prove physical interpolation or delivery to every group member. [15, 17]

The overlap batches restored adaptation ON and uncached-verified all six leaves at 204 / 277 mired, matching the current 80% / 3600 K target. No production changes, persistent Zigbee attribute writes, firmware changes, or scene writes were part of those batches. Zero-duration calls only primed and restored hardware; serialization and alternating microfades were not validated as overlap solutions. [15]

### Native Scenes: simultaneous attribute progression, incomplete endpoints

An [upstream first-person report](https://github.com/Koenkk/zigbee-herdsman-converters/pull/8637#issuecomment-2850752127) describes simultaneous brightness and color changes after native scene Add/Recall on unspecified IKEA hardware. It does not identify either exact model/firmware here. The converter's `scene_add` encodes requested CT as **XY** Color Control extension fields, so a Store-based test is not an exact reproduction of that recipe. A failed Store-based trial would not disprove the upstream XY-encoded path. [18]

The supported diagnostic route is **empty Add(duration) → Store into that existing entry → Recall**. ZCL Revision 8 §3.7.2.4.6.2 preserves an existing entry's transition time and name during Store; creating an entry with Store instead initializes them to zero/empty. NXP documentation and Silicon Labs' reference implementation independently support this distinction. Pinned zigpy 2.2.0 supports empty Add but lacks arbitrary Add extension-field schemas. This establishes a command recipe, not IKEA overlap behavior. [19]

The initial two-specimen trial used empty Add requesting 20 seconds, Store into that entry, and Recall **without an override**. Both GU10 and E12 read 128 / 400 by the first +3-second sample and remained there at +8, +13 and +23 seconds, in color-temperature mode. This confirms both endpoints but provides **no measured 20-second progression or proven overlapping fade**. Without visual/photometric measurement, it does not establish an optical snap. The requested stored duration was not observable in the View reply; preservation is source-supported, not verified by readback. [17, 19, 20]

Both trials passed uncached **total SceneCount = 0** plus explicit View of scene 254 **NOT_FOUND (139)** before allocation. Cleanup removed **only the newly owned scene 254 / group 0** and verified count = 0 plus View of scene 254 NOT_FOUND (139). After light restoration, CurrentScene still read 254 and SceneValid was false: the empty scene table was restored, but scene metadata was not restored bit-for-bit. All six leaves were uncached-verified at 204 / 277 mired with adaptation ON. [20]

The completed follow-up used one explicit Recall `transition_time: 200` per specimen (tenths of a second, requesting 20 seconds). Both GU10 and E12 progressed from 204 / 285 through **203 / 287 near +0.5 seconds, 193 / 296 at +3 seconds, 175 / 314 at +8 seconds, and 156 / 333 at +13 seconds**. Both LevelRemainingTime and ColorRemainingTime were positive and decreasing. This demonstrates simultaneous **device-attribute progression**, unlike the raw two-command paths, but not measured optical interpolation. [21]

At +23 seconds, both specimens reported **128 / 361**, with LevelRemainingTime = 0 and ColorRemainingTime = 67; the CT target was 400. GU10 observation ended there, so CT 400 was not verified and no late-stall conclusion follows for GU10 alone. E12 remained at 128 / 361 with timers 0 / 67 at +35 and +45 seconds without another Recall: CT and its reported timer stalled through that bounded window. Both timer definitions use deciseconds; the slower and then unchanged Color countdown is not evidence of a different unit or guaranteed later completion. Requested 20 seconds is not a measured successful completion time. **This is not a reliable two-endpoint solution.** [21]

No native-group Scene trial ran after the E12 endpoint failure. All four Scene trials (two without override, two with override) have verified owned-ID removal, count = 0 and explicit View of scene 254 NOT_FOUND (139). Final restoration uncached-verified all six leaves at 204 / 277 mired with adaptation ON; CurrentScene = 254 persisted and SceneValid was false. Empty tables were restored, not pre-session metadata bit-for-bit. [21]

A subsequent E12 control made exactly one Recall with the same explicit 200-decisecond override, then issued no diagnostic requests or commands for 40.0009 seconds after service completion. Its single sequential endpoint sample again returned level 128 / CT 361 mired and remaining times 0 / 67 deciseconds, rather than the requested 128 / 400. This reproduces the late endpoint miss without intermediate diagnostic reads; those reads are therefore not supported as a necessary cause. A single late sample does not establish a stall trajectory in this control or isolate firmware/radio causation, and passive capture cannot rule out ZHA background polling at radio level. [22]

The control removed only its owned scene 254 / group 0 and verified SceneCount = 0 plus View NOT_FOUND (139). Final CurrentScene = 254, CurrentGroup = 0 and SceneValid = false matched this trial's baseline, but not the original pre-scene CurrentScene = 0. All six lights were restored on at level 204 / 277 mired with adaptation ON; ZHA/Daylight configuration timestamps were unchanged. [22]

Add → Store requires physically priming the desired endpoint before capture, so it is **not a production adaptation algorithm**, even if Recall succeeds. Arbitrary-extension schema support, scene ownership/capacity, persistence and possible write wear remain separate design questions. No implementation or deployment is recommended from this hypothesis.

### Software-stepped concurrent fade: all six endpoints reached in one run

A separate software-driven trial used the two existing native ZHA groups (IDs 2 and 3), sending intermediate brightness and Kelvin targets together with `transition: 0`. Targets were interpolated from level 204 / 3500 K to level 128 / 2500 K using elapsed time over a requested 30-second trajectory. This preserves an overall fade duration through software stepping; it is **not native positive-duration overlap**, nor an alternating single-property microfade. It uses no Scenes or scene-table writes. [23]

The first attempt stopped at its prerequisite prime: despite successful group service completion, the first GU10 read level 204 / 277 mired instead of 204 / 285. No software frames were sent, and no cause was established. One retry allowed five seconds of settling and verified all six primes at 204 / 285 before starting. The successful trajectory began at 15:42:31.346644 EDT. [23]

- **28 paired frames / 56 native-group `light.turn_on` calls** executed. Each pair addressed both groups; each call contained brightness, Kelvin and zero transition. No experimental leaf calls or corrective commands were used.
- The first pair began at +1.002 seconds; the final pair began at +30.001 seconds and completed at +30.472 seconds. Frame starts were approximately one second apart or longer (minimum 0.998 seconds from timer jitter). Nominal ticks 22 and 24 were skipped under backpressure, without overlapping control calls or a catch-up burst.
- Pair durations were 0.149–1.547 seconds; individual awaited service completions were 0.048–1.199 seconds. The 56 calls nominally imply 112 Level/Color cluster commands from the source path, **not a measured radio-transmission count**; retries and actual transmissions were not captured. Including priming and adaptation switches, the retry made 60 owned service calls. [23]

All six devices progressed in both reported dimensions and reached level 128 / 400 mired. Representative sequential measurements are below; the source retains all six trajectories and exact offsets. [23]

| Specimen | First intermediate sample | Middle sample | Late sample | Endpoint sample |
|---|---|---|---|---|
| GU10 `floor_lamp_01` | 194 / 300 at +5.001 s | 171 / 322 at +15.002 s | 141 / 374 at +25.004 s | 128 / 400 at +32.003 s |
| E12 `paper_lamp_01` | 191 / 300 at +6.063 s | 166 / 333 at +15.870 s | 138 / 379 at +26.860 s | 128 / 400 at +32.618 s |

All-six endpoint sample starts spanned +32.003 to +33.020 seconds, with the last read completing at +33.194 seconds. Reads were intrusive and sequential: dimensions could straddle a frame, some bulbs lagged recently sent frames, and diagnostic traffic could contend for the shared Zigbee transport. This demonstrates sampled progression and endpoint success in one run, not delivery of every frame, natural manual feedback, optical smoothness, or long-term reliability. The method increases command traffic and is not production-validated. [23]

Cleanup restored adaptation ON and uncached-verified all six lights on at level 204 / 277 mired, matching the current 80% / 3600 K target. ZHA/Daylight configuration timestamps were unchanged. No implementation, deployment, configuration toggle, firmware update or membership change was made. [23]

### Follow-up evidence

15. Operator artifacts: `/tmp/daylight-smooth-20261010/final-report.md` and `trial-details.md` (exact trajectories, timestamps and payloads); adjacent `audit.json`, `config-findings.md`, and `verified-membership.json`. These are local experimental evidence, not published upstream replication.
16. Pinned [zigpy 2.2.0 Level/Scenes schemas](https://github.com/zigpy/zigpy/blob/2.2.0/zigpy/zcl/clusters/general.py) and [Color schemas](https://github.com/zigpy/zigpy/blob/2.2.0/zigpy/zcl/clusters/lighting.py); [NXP bounded CT semantics](https://mcuxpresso.nxp.com/mcuxsdk/25.09.00/html/middleware/wireless/zigbee/Docs/JNUG3132/Colour_Control_cluster/topics/controlling_colour_temperature.html). Source investigation: `/tmp/daylight-smooth-design.md`.
17. [ZHA 2.2.2 `issue_cluster_command`](https://github.com/zigpy/zha/blob/2.2.2/zha/zigbee/device.py) and [HA 2026.9.4 websocket service exception handling](https://github.com/home-assistant/core/blob/2026.9.4/homeassistant/components/websocket_api/commands.py). The status-check clarification supersedes the operator artifacts' shorthand that raw status is not exposed.
18. [Native-Scene overlap report, PR 8637](https://github.com/Koenkk/zigbee-herdsman-converters/pull/8637#issuecomment-2850752127); [pinned converter `scene_add`](https://github.com/Koenkk/zigbee-herdsman-converters/blob/d436575ffdcf9e7d35a9bfd0a6d45b47fc0e6458/src/converters/toZigbee.ts). Research summary: `/tmp/daylight-overlap-research.md`.
19. [Zigbee Cluster Library Revision 8, §3.7.2.4.6.2](https://csa-iot.org/wp-content/uploads/2022/01/07-5123-08-Zigbee-Cluster-Library-1.pdf) (printed page 3-51 / PDF page 161: Store preserves an existing scene's transition time and name); [NXP Scenes documentation](https://mcuxpresso.nxp.com/mcuxsdk/25.09.00/html/middleware/wireless/zigbee/Docs/JNUG3132/Scenes_cluster/topics/scenes_cluster.html); [Silicon Labs Scenes reference implementation](https://github.com/SiliconLabs/gecko_sdk/blob/gsdk_4.4/protocol/zigbee/app/framework/plugin/scenes/scenes.c). Updated safety gate and limits: `/tmp/daylight-scene-test-recipe.md`; its count = 0 + explicit NOT_FOUND gate supersedes earlier membership/logging prerequisites.
20. Preliminary operator record: `/tmp/daylight-scene-live-progress.md`; capture: `/tmp/daylight-scene-20261010/scene-trial-capture.jsonl`. No-override trials completed and restoration verified; the completed override report below supersedes preliminary progress interpretations.
21. Final override evidence: `/tmp/daylight-scene-override-report.md`; exact commands: `/tmp/daylight-scene-20261010/override-commands-and-status.json`; shared capture as above. [Silicon Labs Scenes callback documentation](https://docs.silabs.com/zigbee/7.5.1/zigbee-af-api/scenes-server) identifies the Recall override as `transitionTimeDs`; pinned zigpy Level/Color definitions [16] define both RemainingTime attributes in deciseconds.
22. Completed no-intermediate-read control: `/tmp/daylight-scene-no-read-control.md`; exact requests, replies and passive events: `/tmp/daylight-retry-20261010/scene-no-read-capture.jsonl`; ownership/cleanup record: `/tmp/daylight-retry-20261010/scene-ownership.json`.
23. Completed software trial: `/tmp/daylight-software-fade-report.md`; `/tmp/daylight-software-fade-20261010/` contains `capture.jsonl`, `frames.json`, `samples.json` and `owned-services.json`. Its `first-prime-blocker/` directory preserves the failed prerequisite attempt separately from the successful trajectory.

## October 10 follow-up: device settings and raw XY

### Stop and coupling tests did not reach the requested paired endpoint

Three additional 30-second trials on the same GU10 specimen and firmware (`0x03000021`) began from uncached-verified level 204 / CT 285 mired and requested level 128, with CT 400 mired where applicable. These were native commands, not software stepping. [24]

| Path | Near +5 seconds | Near +15 seconds | Near +35 seconds | Result |
|---|---|---|---|---|
| Level Stop and Color Stop, then ordinary combined call | 204 / 304 | 204 / 342 | 204 / 400 | CT progressed; brightness did not |
| One Level command with transient coupling enabled | 192 / 285 | 167 / 285 | 128 / 285 | Brightness progressed; no observed CT change |
| Stored Level Options = 2 and Color Options = 1, then ordinary combined call | 191 / 391 | 166 / 400 | 128 / 412 | Concurrent changes, but wrong CT endpoint |

Stop-clear used Level command 3 and Color command 71, both before priming and immediately before the combined trial. It did not resolve the paired-transition failure. ColorRemainingTime did not immediately reset to zero after Stop; that diagnostic value does not establish continued optical motion. The transient coupling probe used one plain `MoveToLevel` command with `options_mask: 2, options_override: 2`. Its lack of CT movement does not imply that stored-option coupling is absent. [24]

The third branch temporarily wrote the device's persistent option attributes: Level Options bit 1 enables coupling, while Color Options bit 0 enables Execute_if_off. Pinned ZHA source predicts color-before-level for the latter setting; actual radio order was not captured. Both attributes changed, but CT was already 391 mired near +5 seconds, passed 400, and ended at **412 rather than the requested 400 mired**. The measured coupling minimum was 370 mired and physical limits were 250–454 mired. This is consistent with a constrained, level-dependent coupling response, not verified independent interpolation to arbitrary targets. Sparse samples do not establish an exact coupling curve. [7, 24]

No E12 transition or group trial followed these failed success gates. Cleanup restored the GU10's **exact original whole option bytes**, Level Options = 0 followed by Color Options = 0, with uncached readback. E12 options remained 0 / 0 and were never written. All six lights were restored on at level 204 / 277 mired, matching the then-current 80% / 3600 K target, with adaptation ON and unchanged ZHA/Daylight configuration timestamps. Temporary device-attribute writes are distinct from global configuration changes; no global setting was changed or reloaded. [24]

### Global settings do not provide an evidenced already-on fix

The pinned ZHA 2.2.2 source distinguishes command selection from reporting and assumed-state bookkeeping. The effective settings captured earlier remain the relevant baseline; the following source findings provide no reason to mutate global configuration or reload ZHA for this reproduction. They are not results of live global-toggle experiments. [7]

| Setting | Source behavior | Relevance to these trials |
|---|---|---|
| Enhanced light transition | Special sequencing requires an off→on transition and other eligibility checks; `LightGroup.recompute_capabilities()` explicitly disables it for groups | Does not select a different fade path for the already-on specimens |
| Default light transition | Used when no explicit transition is supplied | Explicit 30-second requests bypass the configured default |
| Light transitioning flag | Controls transition/report handling and state timing | Does not change the requested Level/Color command family or fade duration |
| Group members assume state | Updates assumed member state and refresh/debounce behavior | Does not replace or change the outgoing native Level/Color fade commands |

Reporting and refresh behavior can still affect subsequent state observations and diagnostic traffic; “no fade-command change” is not a claim that all background traffic is identical. No successful ZHA settings fix or deployment follows from this matrix.

### Raw XY acceptance did not permit overlapping brightness and XY

Fresh GU10 reads returned **ColorCapabilities = 16 (`0x0010`, color temperature only)**, without the XY capability bit `0x0008`. Despite that advertised limitation, raw Color command 7 (`MoveToColor`) accepted an origin of **X = 26561, Y = 25606** exactly and changed ColorMode to `X_and_Y`. Each trial separately verified that origin with level 204. Readable or stale X/Y registers in CT mode were not treated as origin proof. This establishes raw command acceptance on this specimen, not normal advertised XY support. [25, 26]

Two trials paired `MoveToLevelWithOnOff` toward level 128 with raw `MoveToColor` toward X = 31260, Y = 27110; both requested 300 deciseconds (30 seconds). The second call followed completion of the first, with dispatch offsets of about 0.083 seconds for Level→XY and 0.042 seconds for XY→Level. These are service-dispatch timings, not radio measurements. [26]

| Order | Near +5 seconds: level / X / Y | Near +15 seconds | Near +35 seconds |
|---|---|---|---|
| Level → XY | 204 / 27297 / 25846 | 204 / 28753 / 26312 | 204 / 31260 / 27110 |
| XY → Level | 192 / 26561 / 25606 | 167 / 26561 / 25606 | 128 / 26561 / 25606 |

Only the second-commanded property progressed. Neither order reached the paired endpoint, so **no E12 or native-group XY trial ran**. Together with the earlier raw CT results, this is evidence consistent with mutually exclusive transitions in these tested command paths on this GU10 specimen/firmware. It does not establish a universal hardware engine, firmware implementation, or optical behavior; Scene and coupling observations above also limit any broader inference. CT 400 was not an acceptance criterion in XY mode, and lookup-derived XY coordinates are not calibrated optical Kelvin measurements. [26]

Ordinary HA calls are not an equivalent XY diagnostic here. Both tested leaf entities and their native group entities advertise only `color_temp`; HA 2026.9.4 converts an incoming `xy_color` request back to `color_temp_kelvin` when XY is unsupported but CT is supported. A raw accepted command therefore does not make ordinary HA delivery XY-capable. The unsuccessful raw path is not a production solution and supplies no basis for changing advertised capabilities or deploying a transport workaround. [25, 27]

### Fresh restoration after XY trials

The current Daylight target had moved off the earlier 3600 K plateau. After the first XY trial, all six lights were verified on in CT mode at level 204 / 278 mired, matching the freshly sampled 80% / 3587 K target. After the second, all six were verified on in CT mode at **level 204 / 279 mired**, matching 80% / 3583 K. Adaptation was restored ON; all six Level/Color option bytes remained exactly 0 / 0, and ZHA/Daylight configuration timestamps were unchanged. No settings writes, Scenes, group commands or membership changes occurred in the XY trials. Restoration concerned current brightness/CT and CT mode, not stale X/Y register contents. [26]

All reported trajectories use intrusive, sequential uncached reads rather than natural feedback or optical measurements. No RF capture, implementation change, deployment, or universal reliability claim accompanies these findings.

### Settings and XY evidence

24. Completed settings matrix: `/tmp/daylight-zha-settings-live-report.md`; exact requests, samples, original-byte ownership/restoration and audits: `/tmp/daylight-zha-settings-live-20261010/`. Primary command/option definitions are in the pinned zigpy sources [16]; ZHA ordering is in [7].
25. Source-only XY preflight: `/tmp/daylight-xy-live-preflight.md`, with pinned HA and converter files under `/tmp/daylight-xy-preflight-20261010/`. Its lack of a measured capability bitmap was subsequently resolved by the live reads in [26], not inferred from entity modes.
26. Completed raw XY trials: `/tmp/daylight-xy-native-live-report.md`; exact captures, samples, original options and cleanup records: `/tmp/daylight-xy-native-live-20261010/`.
27. [HA 2026.9.4 light color-mode conversion](https://github.com/home-assistant/core/blob/2026.9.4/homeassistant/components/light/__init__.py) and [color utilities](https://github.com/home-assistant/core/blob/2026.9.4/homeassistant/util/color.py); [pinned converter color conversion](https://github.com/Koenkk/zigbee-herdsman-converters/blob/f1064588a9c7329a3b599114c256ba918f42d40d/src/lib/color.ts). ZHA capability handling, group settings and XY command serialization are in [7]; raw MoveToColor and ColorCapabilities definitions are in [16].
