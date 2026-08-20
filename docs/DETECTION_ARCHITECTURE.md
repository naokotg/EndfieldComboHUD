# Detection architecture

## High-level pipeline

```text
Windows screen
    |
    +-- foreground process check (`Endfield.exe`)
    |
    +-- tiny fixed HUD ROIs captured with Win32 GDI
            |
            +-- combo energy ROI -> READY / CONSUMED metrics
            +-- HP ROI -> alive-color evidence
            +-- death portrait ROI -> death-icon similarity
            +-- center three-segment skill bar -> overlay visibility only
            |
            +-- stable state machine -> authoritative READY/CD/death state
            |
            +-- v2.1 display-only modules
                    +-- real bar fill -> circle progress
                    +-- robust in-combat CD learning + real progress -> estimated seconds
                    +-- READY event -> visual pulse
```

## Reference coordinate system

Reference HUD coordinates are based on **2048x1152** and are scaled to the
current display.

Current energy ROIs:

```python
SLOT_ENERGY_ROIS = [
    (45, 1053, 134, 1057),
    (169, 1053, 259, 1057),
    (294, 1053, 383, 1057),
    (419, 1053, 508, 1057),
]
```

Current HP ROIs:

```python
SLOT_HP_ROIS = [
    (45, 1057, 134, 1064),
    (169, 1057, 259, 1064),
    (294, 1057, 383, 1064),
    (419, 1057, 508, 1064),
]
```

Current death-icon ROIs:

```python
SLOT_DEATH_ICON_ROIS = [
    (77, 997, 103, 1023),
    (201, 997, 227, 1023),
    (326, 997, 352, 1023),
    (451, 997, 477, 1023),
]
```

Current center skill-bar ROIs:

```python
CENTER_SKILL_BAR_ROIS = [
    (851, 1033, 961, 1055),
    (970, 1033, 1079, 1055),
    (1086, 1033, 1196, 1055),
]

CENTER_SKILL_BAR_GAP_ROIS = [
    (961, 1033, 970, 1055),
    (1079, 1033, 1086, 1055),
]
```

## Stable READY logic

Capture rate: 10 Hz.

READY has two routes:

1. Neutral bright-white route.
2. High-luminance tinted route for low-HP/red-tinted HUD states.

A READY state is confirmed only after consecutive frames.

Once READY is confirmed it is latched. Temporary HUD dimming does not clear it.

## Stable CONSUMED / re-arm logic

A real combo use clears most bright/white pixels in the energy bar.

Only a confirmed consumed/empty signature clears a latched READY and arms the
slot for a future alert.

This is specifically designed so character-switch HUD dimming does not produce
a false READY alert when brightness returns.

## Death logic

The original stable branch used colored HP evidence.

The v2.1 beta adds a second requirement for entering DEAD:
- HP evidence says almost no colored HP remains;
- the portrait center resembles the fixed death icon.

This change was introduced because extremely low but non-zero HP can make the
old HP-only metric resemble death.

## v2.1 progress rule

Progress is not authoritative.

The real horizontal fill position of the game bar is converted to 0..1.
Before READY confirmation, the visual progress is capped below 100%.
Only the old READY state machine can produce the final READY state.

Do not change this separation.

Countdown learning is also display-only. During an established combat session,
a normal timing sample starts only when a confirmed CONSUMED transition follows
three consecutive frames where the raw center bar and that slot's unchanged
READY brightness evidence coexist. This context remains eligible for 1.0 second.
It records the provenance of existing signals but never sets or clears the
protected READY / CONSUMED state.

Fast fresh-combat engagement is the exception because the lower-left HUD may
first appear after the combo is already empty. Its protected READY -> CONSUMED
event stores a pending timestamp; when the center bar confirms fresh combat
within the existing 1.0-second wake window, that timestamp becomes the timing
start. This exception does not apply during an already retained combat session.

Six consecutive raw-center-bar misses (~0.6 s), foreground loss, or death
permanently invalidate the current learning cycle. An invalid cycle never
restarts from the middle and never updates the learned duration. The real
progress circle continues normally; if a duration was already learned, its
estimated number resumes from real progress when the center bar returns. One
clean complete cycle is enough to provide a provisional duration. Compatible
later samples use a rolling median of up to five cycles. A complete sample at
least 2x shorter than the normal baseline is classified as a temporary fast
cycle and never lowers that baseline, regardless of how many consecutive fast
cycles occur. This prevents the real post-ultimate 3-4x charge-speed buff from
becoming the permanent display model. A clearly longer valid sample may raise
the baseline immediately; this is intentionally conservative and repairs a
first sample learned while accelerated. None of this may affect READY / CONSUMED
or authoritative state transitions.

## v2.1 center skill-bar visibility gate

This is a display-only signal. It searches for the repeated horizontal-edge
geometry of the three skill-energy segments above the active character HP bar.
Each accepted segment must contribute both an upper and a lower horizontal
edge at separated row positions. The same edge rows must remain weak in the two
gaps between segments. This rejects the active-character HP bar: its animated
border can enter the skill-bar ROI, but it remains one continuous line through
both gaps instead of three separate rectangles.

At least two center segments must pass the structural score. Fresh combat is
confirmed only when that center-bar signal and at least three lower-left combo
bars satisfying the unchanged v2.0.1 READY brightness rules coexist for three
consecutive frames. There is a second event-based path for active-skill
engagement followed by immediate combo use: when the stable state machine sees
an already-latched READY slot become confirmed CONSUMED, it opens a 1.0-second
window; coexistence with the center bar during that window confirms combat on
the next capture frame. Foreground loss clears the pending event. There is
intentionally no center-bar-duration fallback: out-of-combat movement may
sustain the center bar by repeatedly spending rapidly recovering skill energy.

Both fresh-combat paths control overlay visibility only. The event path observes
the existing protected READY -> CONSUMED transition but must not create, relax,
clear, or re-arm that transition. Neither path may gate the stable detector.

Once a combat session has been confirmed, six consecutive missing samples
(about 0.6 seconds) hide the plugin HUD, filtering the 0.1-0.2-second transient
misses observed in real gameplay; reappearance still shows it immediately.
The invisible combat session is retained for about 8 seconds so an ultimate
animation can hide the center bar for several seconds
without forcing fresh-combat confirmation again. A longer absence expires the
session, so the next appearance must satisfy the combined condition again.

The stable four-slot READY / CONSUMED detector continues to sample normally
regardless of this visibility state. The center-bar signal must never set,
clear, freeze, or reset READY / CONSUMED / death state. Manual preview and
adjustment mode remain visibility exceptions.

The previous lower-left combo-bar input-validity gate is intentionally retired:
real recordings showed that scenery/lighting and dark combat could not be
reliably separated by that ROI without regressing the stable detector.

## v2.3 controller layout (experimental)

Controller mode is an explicit user-selected layout; it does not auto-detect
the input device and does not read controller input. The original keyboard and
mouse ROI path is left unchanged.

The controller portrait cluster is a fixed cross: slot 1 left, slot 2 top,
slot 3 right, and slot 4 bottom. Holding LB moves all four slots synchronously
outward by about 16 pixels at 3840x2160. The detector reads the central input
glyph inside that cluster instead of deriving position from the thin HP bars:
a full-size `LB` glyph selects the fully inward endpoint; the large white D-pad
glyph selects the fully outward endpoint. A normalized neutral-white mask is
matched against recorded endpoint templates. The winning template must exceed
both an absolute score and a separation margin, then remain the same for two
consecutive capture frames.

Intermediate animation sizes, effects, occlusion, character-switch glyphs, and
an absent controller cluster do not select an offset. All four slot-state updates
are skipped for those frames. There is deliberately no HP/death fallback: the
five candidate HP positions overlap heavily and `TEST2` proved that their tiny
score differences can repeatedly move the ROI through the real fill edge,
creating false progress and READY/CONSUMED changes. The confirmed endpoint
translates energy, HP, and death-icon ROIs together; HP and death icons remain
inputs only to alive/death detection at that confirmed position.

The central glyph is geometry evidence only. It is not combat evidence, cannot
set or clear READY, and cannot wake the overlay. Center skill-bar detection and
combat-session timing remain at their existing screen coordinates. The original
keyboard/mouse path does not execute any of this controller logic.

## v2.3.2 controller learned display clock (experimental)

This controller-only branch separates the visual circle clock from the existing
learning clock. On the first clean cycle, or whenever no duration has been
learned, the circle still uses the detected real white-bar percentage. A clean
confirmed CONSUMED -> READY cycle teaches the duration through the existing
learning rules.

On later confirmed controller consumptions, a separate display timestamp starts.
The circle then advances smoothly from elapsed time / learned duration, including
through ambiguous LB transition frames where slot image sampling is intentionally
frozen. The predicted circle is capped at `PROGRESS_MAX_BEFORE_READY` (98.5%). If
elapsed time reaches the learned duration without a real READY confirmation, the
circle remains below 100% and the countdown becomes `--`.

The display timestamp never starts, clears, arms, or completes READY / CONSUMED;
it is not a learning sample and cannot trigger an alert. Actual READY remains the
only authority for 100% and notification. Center-bar loss may invalidate the
learning timestamp while the independent display timestamp continues. Death or
actual READY clears the display timestamp. Keyboard/mouse does not use this
prediction path and continues to follow the real white bar.

The learned-duration aggregator is shared display-only infrastructure. Every
valid complete observation is retained in a bounded runtime debug history with
an initial, accepted, fast, or raised decision. The stable sample pool and the
current diagnostic fast-cycle pool each contain at most five values; the full
diagnostic observation history contains at most twenty. These histories are
reset when monitoring restarts and are never persisted as character data.
