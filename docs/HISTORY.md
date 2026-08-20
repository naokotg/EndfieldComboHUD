# Development history summary

## v2.0.1 stable
Foreground detection hotfix:
- exact `Endfield.exe` process match;
- exact-title fallback only when process-path lookup is unavailable;
- prevents browser tabs containing “终末地” from waking the HUD.

## v2.1 alpha1
- real combo-bar fill -> clockwise circle progress;
- learned first full CD -> estimated remaining seconds;
- READY whole-circle brighten animation.

## v2.1 alpha2
- stronger READY brighten + expansion;
- slot number and countdown display separated;
- attempted progress gating around real CD cycles.

## v2.1 alpha3
- fixed extremely-low-HP false death by adding death-portrait evidence.

## v2.1 alpha4
- added explicit combo-bar HUD presence detection;
- later found that the presence ROI overlapped the HP bar and could false-wake
  out of combat.

## v2.1 alpha5
- presence ROI changed to exclude the HP bar;
- threshold retuned from available battle/out-of-combat video samples;

## v2.1.1
- fresh combat requires center skill bar plus at least three READY combo bars;
- center-bar detection requires paired upper/lower edges in separate segments;
- inter-segment gaps reject the continuous active-character HP border seen in
  `FALSE2`;
- combat-session grace preserves fast HUD reappearance after an ultimate.

## v2.1.2 Beta
- one clean complete cycle remains sufficient for countdown learning;
- six consecutive center-bar-missing frames invalidate only the current timing
  sample;
- an invalid sample cannot poison the learned full-CD duration;
- uncertain countdown digits show `--` while real circle progress continues.

## v2.1.2 Beta 2
- keeps the three-READY-bars plus center-bar fresh-combat path unchanged;
- adds a second path when an already-latched READY combo becomes confirmed
  CONSUMED and the center bar coexists within 1.0 second;
- covers active-skill engagement followed by immediate combo use before three
  lower-left READY bars have finished appearing;
- the added evidence changes overlay visibility only and does not alter the
  stable READY / CONSUMED detector or countdown learning.

## v2.1.2 Beta 3
- normal in-combat countdown learning starts only from recent center-bar plus
  per-slot READY evidence;
- fast fresh-combat consumption keeps a separate pending timing-start path;
- an interrupted learning cycle cannot restart from a false residual-CD point;
- an already learned duration resumes its display estimate after the center bar
  returns, while the interrupted cycle remains ineligible for training;
- offline `clip` regression blocks the ultimate-flash false READY -> CONSUMED
  sequence without changing the protected state machine or real progress circle.

## v2.2
- promotes v2.1.2 Beta 3 unchanged as the current packaged release;
- changes no detection thresholds, ROI geometry, or state-machine behavior;
- validated scope remains 16:9 keyboard/mouse layout, with 3840x2160 as the
  primary development environment; controller and ultrawide remain unsupported.

## v2.3 Controller Beta
- adds an explicit controller layout while preserving v2.2 keyboard/mouse as
  the default unchanged path;
- maps slots to the controller cross as 1 left, 2 top, 3 right, 4 bottom;
- follows LB inward/outward animation through a single HP-bar-derived radial
  offset instead of reading local controller input or hard-switching two ROIs;
- treats portrait/HP visibility as coordinate evidence only, never as combat
  evidence;
- offline 4K `test.mp4` replay found no confirmed false CONSUMED transition
  during long holds, rapid LB tapping, or sequential character switching;
- remained Beta pending real gameplay, real controller death, and non-4K tests;
- `CTRL LOWHP` later exposed the original HP-only anchor limit: with three dead
  characters and the survivor at 9/2333 HP, most frames froze and occasional
  false offsets made the survivor's display-only progress jump;
- recalibrates the controller death-icon ROIs from that real recording and lets
  fixed death icons contribute coordinate evidence alongside living colored HP;
  the protected READY / CONSUMED thresholds and latch semantics remain unchanged.
- `TEST2` then showed that the five-position HP/death anchor remained ambiguous
  even with all characters alive: near-identical overlapping candidates switched
  260 times and produced false slot-4 cycles plus circle jumps during rapid LB;
- replaces position guessing with visual endpoint recognition of the central
  full-size `LB` and large white D-pad glyphs. Two equal endpoint frames are
  required; transition/occluded frames freeze all four slots, with no HP fallback;
- offline `TEST2` replay removes the false slot-4 CONSUMED at 18.9 s and later
  repeated slot-4 false cycles while preserving real transitions in `CTRL LOWHP`.

## v2.3.1 Controller Beta

- freezes the central-glyph endpoint solution as a rollback checkpoint;
- makes no additional detector, progress, or learning changes from the final
  v2.3 controller beta state.

## v2.3.2 Controller Beta

- keeps the first clean controller cycle on real white-bar progress;
- after learning a duration, drives later controller circles from a separate
  smooth elapsed-time display clock;
- lets that display clock continue while rapid LB transitions freeze ambiguous
  screen sampling;
- caps prediction below READY and shows `--` after the expected finish until the
  protected detector sees real READY;
- keeps learning invalidation separate and leaves keyboard/mouse v2.2 behavior
  unchanged.
- accelerated-cycle learning revision keeps the first valid sample immediately
  usable, expands the stable rolling median to five samples, and records a
  cycle at least 2x shorter as temporary acceleration without allowing it to
  lower the normal baseline, even after several consistent fast cycles;
- allows one clearly longer valid cycle to restore/raise the normal baseline,
  including when the first-ever sample was captured during the post-ultimate
  3-4x charge-speed buff, and exposes bounded normal/fast history in Debug;
- this revision changes learned display timing only and does not modify READY,
  CONSUMED, low-HP tint, death, or combat-visibility detection.

## v2.3 packaged release

- consolidates the final controller-beta development source into one public
  v2.3 package;
- keeps keyboard/mouse as the default supported path and retains its v2.2
  READY, CONSUMED, death, real-bar circle, and visibility behavior;
- promotes the shared accelerated-cycle-resistant countdown learner as a
  keyboard/mouse advisory-number improvement;
- keeps controller explicitly labeled Beta and documents known susceptibility
  to strong effects, low-HP/healing overlays, and LB transition ambiguity;
- does not include the unverified structural white-bar validation discussed
  after DEBUG2.
