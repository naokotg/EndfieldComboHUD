# Manual regression checklist

Use this after changes touching capture, ROI geometry, state logic, death,
bar-presence, progress, overlay state, or foreground handling.

## Core READY/CD
- Start monitoring while already in combat.
- READY appears correctly.
- Use combo: READY clears only after true consumption.
- Wait for refill: READY returns once and alert fires once.
- Character switch while READY: no false re-alert.
- Temporary HUD dimming: latched READY remains correct.

## Low HP / death
- Low HP red-tinted HUD still detects READY.
- Extremely low but non-zero HP must remain alive.
- Real death enters DEAD after confirmation.
- Death does not spontaneously clear because the portrait/HUD fades.
- Return/re-entry with colored HP clears stale DEAD state correctly.

## v2.1 progress/countdown
- After use, circle follows real white-bar fill.
- Real bar stall -> circle stalls.
- Real bar jump -> circle jumps.
- Progress cannot set READY by itself.
- First full CD cycle learns duration.
- Later estimated seconds are plausible and follow real bar percentage.
- A center-bar interruption shorter than 0.6 s does not discard a clean cycle.
- A center-bar interruption of at least 0.6 s prevents that cycle from learning
  and does not stop circle progress. If a duration was already learned, the
  number resumes from real progress when the center bar returns.
- Re-entering combat during the same invalid cycle must not restart timing from
  the middle or learn a short residual duration.
- An ultimate flash that produces READY only while the center bar is absent must
  not authorize a new learning sample when the normal HUD returns dark/empty.
- A normal consumed combo after three frames of center-bar plus per-slot READY
  evidence still starts a one-cycle timing sample.
- Fresh-combat confirmed consumption can start timing when the center bar appears
  inside its existing 1.0-second wake window, even if the READY bar was hidden.
- READY forces 100% and ends countdown.

## HUD presence / out of combat
- Begin while already out of combat: detector should not wake on scenery.
- Move through bright/dark terrain while out of combat.
- Repeatedly spend active-skill energy while out of combat: a sustained center
  bar without a combo consumption must not wake the plugin HUD.
- Enter combat with active skill 1, then immediately use slot 1 or 2 combo before
  three READY bars finish appearing: confirmed consumption plus the center bar
  should wake the plugin HUD promptly.
- Alt-tab immediately after consuming a combo during fresh engagement: the
  pending wake evidence must not survive the foreground loss.
- Let portraits/HP UI fade in and out.
- ESC exit while mid-CD.
- Kill final enemy while mid-CD.
- After confirmed bar disappearance, progress/readout must stop.
- Re-enter combat: bar presence is reacquired and state resynchronizes.
- Short in-combat HUD disappearance should freeze rather than consume/reset.

## Window behavior
- Alt-tab away: overlay hides.
- Return to exact `Endfield.exe`: overlay returns.
- Browser/tab title containing “终末地” must not wake overlay.
- Single-instance protection still works.

## Compatibility
- 4K 16:9 is the primary regression target.
- After ROI changes, repeat a short real 2560x1440 test.
- Do not mark 1080p/ultrawide as verified without real tests.

## v2.3 controller Beta
- Select `手柄（v2.3 Beta）`; verify the keyboard layout remains the default
  after restoring settings.
- Enter combat without holding LB: all four slots reach READY normally.
- Hold/release LB while standing still: no READY clear, alert, or circle jump.
- Rapidly tap LB for at least five seconds: no false CONSUMED/READY cycle.
- During LB expansion/contraction, partial-size input glyphs must freeze slot
  updates; after two stable endpoint frames, all four slots resume together.
- Switch through all four characters while every combo is READY: no false alert.
- Consume each of the four combo slots once with and without LB held; slot
  numbering and circle progress must remain 1-left, 2-top, 3-right, 4-bottom.
- Out of combat, hold LB so portraits/HP appear without skill/combo bars: plugin
  HUD must remain hidden.
- Test one critically-low-HP controller slot and one real character death.
- Test three dead controller slots with the sole survivor at critical HP: LB
  press/release must still track from the central glyph, and the survivor's
  circle must not jump.
- Recheck `TEST2`: slot 4 must not enter the false 1-second countdown around
  19 s, and rapid LB after that point must not make any READY slot's circle move.
- Controller 2560x1440 and 1920x1080 remain unverified until tested on real
  displays.

## v2.3.2 controller learned-circle Beta

- With no learned duration, consume a combo and verify the first circle still
  follows the real white bar; complete READY once to teach the duration.
- On the second consumption, verify the circle advances smoothly by the learned
  duration instead of jumping with small white-bar recognition errors.
- Rapidly press/release LB after learning: ambiguous transition frames may freeze
  detector sampling, but the predicted circle must keep moving smoothly.
- If the learned duration expires before real READY is confirmed, the circle
  must remain below 100%, the number must become `--`, and no alert may fire.
- Real READY, including an earlier-than-predicted READY, must immediately finish
  the circle at 100% and preserve the normal single alert.
- Hide the center skill bar long enough to invalidate training: the current
  display clock may continue, but that interrupted cycle must not update the
  learned duration.
- Switch back to keyboard/mouse and confirm its circle still follows real bar
  stalls, jumps, and backward corrections exactly as v2.2.
- First valid duration must still become usable immediately.
- Feed any number of durations at least 2x shorter than the learned normal
  value: every one must appear as a fast observation and none may lower the
  learned duration. Regression sequence `20.0s -> 5.0s -> 5.2s -> 4.8s` must
  retain `20.0s` throughout.
- Follow temporary fast observations with a compatible normal sample: the fast
  pool must clear and the normal sample must join the five-value rolling median.
- Start from a first sample captured while accelerated (`5.0s`), then feed one
  clearly longer valid normal cycle (`20.0s`): the learned baseline must rise
  to `20.0s` immediately rather than requiring two more cycles.
- Regression sequence `9.4s -> 2.0s` must retain `9.4s`; READY, CONSUMED,
  progress completion, pulse, and alerts must be byte-for-byte unaffected by
  the learning decision.
