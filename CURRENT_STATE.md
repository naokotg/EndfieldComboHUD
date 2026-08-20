# CURRENT_STATE.md

Updated: 2026-08-15

## Release status

- Stable public baseline: **v2.0.1**
- Local stable checkpoint: **v2.1.1**
- Current packaged release: **v2.3**
- Controller rollback checkpoint: **v2.3.1 Controller Beta**
- Controller mode in v2.3: **experimental Beta**
- v2.2 promotes the v2.1.2 Beta 3 code unchanged after offline regression
  coverage from `clip`; it has not received additional long-duration real-game
  testing.

`src/` is the current v2.3 release source. Keyboard/mouse is the supported
path; controller remains explicitly labeled Beta.
`baseline/v2.0.1/` is the protected rollback/reference source.

## v2.3.1 Controller Beta

- Adds an explicit `键盘鼠标（v2.2）` / `手柄（v2.3 Beta）` layout selector;
  keyboard/mouse remains the default and keeps the v2.2 ROI path unchanged.
- Controller slot order is 1 left, 2 top, 3 right, 4 bottom.
- The four controller slots move radially by about 16 pixels at 3840x2160 while
  LB is held. Position now comes from the controller cluster's central input
  glyph: full-size `LB` selects the inward endpoint and the large white D-pad
  selects the outward endpoint. Two consecutive matching frames are required.
- Intermediate LB-animation frames, effects, occlusion, and an absent glyph are
  intentionally ambiguous. The controller path preserves all four slot states
  for those frames instead of guessing an HP-bar position. HP and death-icon
  ROIs move with the confirmed endpoint but no longer choose the endpoint.
- The input glyph is coordinate evidence only. It cannot confirm combat or wake
  the overlay. The existing center skill-bar plus protected combo-bar evidence
  remains authoritative for fresh overlay visibility.
- Offline 4K `test.mp4` evidence: the tracker was invalid only during the initial
  out-of-combat hidden-cluster interval (0.0-5.8 s); long LB holds, rapid LB
  tapping, and sequential character switches caused no three-frame false
  CONSUMED event before random combat input began at 79 s.
- `CTRL LOWHP` supplied actual controller death evidence: three dead slots plus
  one survivor at 9/2333 HP. After calibrating the controller death-icon ROIs,
  all six extracted failure keyframes selected the correct LB endpoint and the
  survivor's real bar read as empty -> about 45% -> about 90% -> READY.
- `TEST2` exposed the five-position HP anchor as fundamentally ambiguous even
  with all four characters alive: overlapping HP candidates caused 260 offset
  switches and false slot-4 READY/CONSUMED cycles. Central-glyph replay removed
  the false slot-4 consumption at 18.9 s and its later repeated false cycles;
  no fractional offset is sampled during LB animation.
- 2560x1440 and 1920x1080 controller layouts are scaled theoretically but have
  not been tested on real displays.

## v2.3.2 Controller Beta

- Keeps v2.3.1 as the rollback executable and changes controller display only.
- The first clean controller cooldown still follows the real white bar and can
  teach one complete CONSUMED -> READY duration.
- After a duration is learned, later controller cooldown circles advance from
  elapsed time divided by that learned duration. Rapid LB transition frames
  freeze screen sampling but do not freeze this display clock.
- Prediction is capped at 98.5%. Only the protected real READY detector can
  finish the circle at 100%, set READY, or trigger an alert. If the learned time
  expires before real READY is seen, the uncertain number becomes `--`.
- Center-bar interruption still invalidates the current learning sample but does
  not reset the separate display clock. Keyboard/mouse keeps the v2.2 real-bar
  progress behavior unchanged.
- Accelerated-cycle learning revision: the first valid sample remains
  immediately usable and compatible samples use a rolling median of up to five
  cycles. A complete cycle at least 2x shorter than the normal baseline is
  recorded as temporary acceleration but can never lower that baseline, even
  when several fast cycles occur during the roughly 20-second post-ultimate
  charge-speed buff. One clearly longer valid cycle may raise/restore the normal
  baseline immediately. This changes learned display timing only and cannot
  set, clear, delay, or complete READY / CONSUMED.

## v2.3 packaged release

- Packages the final v2.3.2 accelerated-learning source as one public v2.3
  executable instead of publishing intermediate controller test builds.
- Keyboard/mouse remains the default and retains the v2.2 READY, CONSUMED,
  death, real-bar progress, and visibility paths.
- The shared display-only countdown learner now protects the normal baseline
  from temporary accelerated cycles and can restore it from one clearly longer
  complete cycle. Therefore keyboard/mouse detection is unchanged, but its
  advisory number learning is improved rather than byte-identical to v2.2.
- Controller stays marked Beta. Only 3840x2160 16:9 has real controller testing;
  complex effects, low-HP/healing overlays, and LB transitions can still cause
  temporary visual or learning errors.
- The DEBUG2 investigation found a controller false READY -> false CONSUMED ->
  real READY sequence during a strong effect around 36-38 seconds. No unverified
  structural-bar detector was added for release; the limitation is documented.

## v2.0.1 stable behavior

The stable baseline already handles:
- four slot READY/CD display;
- consecutive-frame confirmation;
- READY latch;
- true CONSUMED-based re-arm to avoid character-switch false alerts;
- low-HP/red-tinted READY detection;
- death handling;
- foreground auto-hide;
- exact process matching for `Endfield.exe`;
- single-instance behavior;
- configurable overlay appearance.

## v2.1 additions

### Real bar progress -> circle progress
The current progress module reads the horizontal fill position of the real
combo bar and maps it to a clockwise filled circle.

The progress reader is display-only and must never determine READY.

### Estimated remaining seconds
For each slot, complete in-combat CONSUMED -> READY cycles are timed. One clean
complete cycle is enough to learn a duration. Six consecutive center-bar-missing
frames (~0.6 s), foreground loss, or death permanently invalidate only the
current timing cycle. An invalid cycle cannot update the learned duration; the
real progress circle continues, and an already learned duration resumes driving
the number from real progress when the center bar returns.

v2.2 also requires a normal in-combat timing start to have three consecutive
frames of the center bar and that slot's unchanged READY brightness evidence,
kept fresh for 1.0 second. This prevents a bright ultimate frame while the HUD is
absent from authorizing a false residual-CD sample after the HUD returns. Fast
fresh-combat combo engagement remains a separate path: the same protected
READY -> CONSUMED event used to confirm fresh combat supplies the pending timing
start once the center bar appears. Nearby clean samples use a rolling median of
up to five cycles. A sample at least 2x shorter than the learned normal baseline
is retained only as a diagnostic fast-cycle observation and never replaces the
baseline; this covers the real 3-4x post-ultimate charge-speed buff as well as
isolated falsely short samples. A clearly longer valid sample raises the normal
baseline immediately, which repairs an initial sample learned while accelerated
without waiting for two more cycles. Estimated remaining seconds use the
learned normal full-CD time and current real bar percentage.

This value is advisory only.

### READY visual pulse
On a real READY alert, the whole circle becomes brighter and expands briefly.
This is intentionally different from the old extra-ring highlight.

### Death detection
A prior HP-only death detector could misclassify a living character at
extremely low HP (observed example: 55/1974).

Current beta uses a double confirmation:
- very low/absent colored HP evidence;
- high similarity to the fixed death portrait icon;
- consecutive-frame confirmation.

The death-icon template was derived from a real 2560x1440 death-state video and
then normalized in code.

### Center skill-bar visibility gate

The experimental lower-left combo-bar presence gate was removed after real
videos showed overlapping scores between bright out-of-combat scenes and dark
combat scenes. The v2.0.1 READY / CONSUMED mechanism now runs continuously again
while `Endfield.exe` is in the foreground.

Plugin HUD visibility is instead tied to the three-segment skill-energy bar
above the active character HP bar. This is display-only and never sets, clears,
freezes, or resets READY / CONSUMED / death state.

Current candidate parameters:
- each detected segment must contain a separated pair of upper/lower horizontal
  edges; the two edges are at least 3 normalized rows apart and both must score
  >= `0.50` in at least 2 of 3 segments;
- the matching edges must not continue through either inter-segment gap
  (gap-edge coverage <= `0.30`), which rejects the continuous HP-bar border;
- fresh-combat condition: center bar plus at least 3 lower-left combo bars
  satisfying the unchanged v2.0.1 READY brightness rules for 3 frames (~0.3 s);
- alternate fresh-combat condition: a slot already latched READY by the stable
  detector changes to confirmed CONSUMED, and the center bar coexists within
  the following 1.0 s; this wakes on the next capture frame without waiting for
  three other bars to finish appearing;
- there is no center-bar-duration fallback;
- 6-frame (~0.6 s) visibility hide after the center bar disappears, rejecting
  the 0.1-0.2 s transient misses observed in `HUD MISS`;
- 80-frame (~8 s) invisible combat-session grace period;
- reappearance during that grace period shows the plugin HUD immediately.

Offline 4K evidence:
- out-of-combat skill use showed the bar for about 0.8-2.6 s per event;
- two combat recordings showed persistent bar geometry for about 29-30 s;
- the old bright out-of-combat false-wake recording produced no center-bar
  episode at the chosen threshold.
- the combined 3-slot condition produced zero frames in both the bright
  false-wake and out-of-combat skill recordings;
- in the two combat recordings, the condition remained continuous once three
  combo bars were actually READY, allowing confirmation about 0.3 s later.
- in `FALSE2`, the previous detector mislabeled the animated HP-bar border in
  259/338 sampled frames; the paired-edge + gap rule removed those episodes.
  Its only remaining detections were 7.3-9.7 s, where the real skill bar was
  visibly present after an out-of-combat skill use.

This visibility gate remains beta behavior and needs in-game regression testing.

## Compatibility evidence

- 3840x2160 16:9: primary development/test environment.
- 2560x1440 16:9: short test on a real 2K secondary monitor found no obvious issue.
- 1920x1080: theoretical scaling only, not tested.
- 21:9 / 32:9: currently unsupported.

## Release checkpoints

- `v2.1.1` preserves the paired-edge + inter-segment-gap center-bar fix.
- `v2.1.2 Beta` adds conservative CD-learning invalidation and `--` for an
  uncertain current timing cycle; it does not alter the real progress circle.
- `v2.1.2 Beta 2` adds the confirmed combo-consumption fresh-combat path for
  active-skill engagement followed immediately by combo use. It still requires
  the center bar and does not change the stable READY / CONSUMED state machine.
- `v2.1.2 Beta 3` adds display-only READY provenance to countdown learning and
  lets existing learned durations resume after an invalidated cycle. In `clip`,
  the 7.6-second ultimate flash still produced protected-core READY states, but
  its 8.4-second false CONSUMED transitions no longer started short samples.
- `v2.2` packages the v2.1.2 Beta 3 code unchanged as the prior keyboard and
  mouse layout release; promotion itself changed no detection behavior.
- `v2.3` is the current package: supported keyboard/mouse plus an explicitly
  experimental controller mode.
- Keep v2.0.1 downloadable as a rollback version until v2.1 has longer testing.
- Do not claim ultrawide compatibility.

## Recommended next validation

1. Long normal combat session on 4K.
2. ESC exit while one or more slots are mid-CD.
3. Normal final-enemy kill -> out of combat.
4. Move through strong light/shadow after leaving combat.
5. Very-low-HP but alive.
6. Actual teammate death and recovery/re-entry.
7. Character switch while READY.
8. Foreground/background switching.
9. Confirm that an out-of-combat skill press does not show the plugin HUD.
10. Confirm that a fresh combat shows the plugin HUD shortly after the center
    bar and at least three full combo bars appear together.
11. Enter combat with active skill 1 and immediately consume slot 1 or 2 combo
    before three lower-left bars are visible: HUD should wake within roughly one
    capture frame after confirmed consumption while the center bar is present.
12. During combat, use an ultimate that hides the center bar: the plugin HUD
    should hide after about 0.6 seconds and return immediately with the center bar.
13. Confirm that a true combat exit hides the plugin HUD after about 0.6
    seconds, then resets the combat session after about 8 seconds.
14. Repeat a short 2K test after any ROI/presence change.
