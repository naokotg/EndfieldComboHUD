# AGENTS.md

## Project purpose

This repository maintains a Windows-only external HUD helper for
《明日方舟：终末地》连携技状态。

The tool works by screen capture and image analysis only:
- do not read game memory;
- do not inject into the game process;
- do not modify game files;
- do not automate player input.

## Priority

Development priority is:

1. Self-use stability
2. Avoid regression
3. Practical usefulness
4. Wider compatibility
5. Code elegance / refactoring

Do not refactor stable detection code merely to make the architecture cleaner.

## Mandatory workflow before editing

Before making changes:

1. Read `CURRENT_STATE.md`.
2. Read `docs/DETECTION_ARCHITECTURE.md`.
3. Inspect the relevant code in `src/`.
4. If the task can touch READY / CONSUMED / low-HP / death detection,
   compare with `baseline/v2.0.1/` first.
5. State the regression risk and prefer the smallest patch.
6. After editing, inspect the diff and run the syntax check.

## Stable-core rules

The v2.0.1 READY / CONSUMED behavior is the protected baseline.

Unless there is explicit reproducible evidence of a bug, do not change:
- neutral READY thresholds;
- tinted/low-HP READY thresholds;
- CONSUMED thresholds;
- READY latch semantics;
- `armed` / true-consumption re-arm behavior;
- foreground process detection using exact `Endfield.exe`.

A new visual feature should preferably be display-only and consume existing
state instead of changing state detection.

## v2.1 rules

The progress circle and estimated countdown are advisory display features.

They MUST NOT:
- directly set READY;
- directly clear READY;
- replace the existing READY/CONSUMED state machine;
- force progress to be monotonic if the real game bar moves backwards.

The real game white-bar progress is the preferred source for the progress
circle. READY remains authoritative for the final 100% state.

The current v2.1 branch also contains:
- display-only robust in-combat CD-duration learning for estimated seconds;
- READY whole-circle brighten/expand animation;
- death detection using HP evidence plus fixed death-icon evidence;
- a display-only center skill-bar gate for normal overlay visibility.

The center skill-bar gate must not set, clear, freeze, or reset READY / CONSUMED
/ death state. Treat its ROI and timing as experimental until they have longer
real-game validation. Do not restore the retired lower-left combo-bar presence
gate without new reproducible evidence.

Fresh-combat overlay visibility requires the center skill bar plus at least
three lower-left combo bars satisfying the protected READY brightness rules.
Do not add a center-bar-duration-only fallback: out-of-combat movement can keep
the center bar visible by repeatedly spending rapidly recovering skill energy.

## Compatibility

Known status:
- 3840x2160 16:9: primary real-world test environment.
- 2560x1440 16:9: short real-monitor test passed.
- 1920x1080 16:9: not yet verified.
- 21:9 / 32:9: not adapted and must not be claimed as supported.

Do not add general ROI auto-calibration unless explicitly requested.
Compatibility work should not put the known-good 4K path at risk.

## Dependencies

Prefer Python standard library + ctypes + tkinter, as the current project does.
Do not add runtime dependencies without explicit approval.

PyInstaller is a build-time dependency only.

## Testing

For ordinary edits, at minimum run:

`python -m py_compile src/EndfieldCDHUD.pyw src/EndfieldCDHUD_debug.py`

For detection changes, also give a concrete manual regression checklist
covering the affected states.

Do not claim a resolution or aspect ratio is verified unless it was actually
tested.

## Bug videos and images

When a bug report includes a video:
- prefer observable evidence over assumptions;
- use timestamps/keyframes to identify state transitions;
- if direct video inspection is unavailable, extract representative frames
  with an available local tool such as ffmpeg;
- do not tune thresholds from a single frame when a time-series sample is
  available.

Future local recordings can be placed in `samples/local/`; that directory is
ignored by Git.

## Release discipline

Keep `baseline/v2.0.1/` untouched as the stable rollback reference.

Before calling a build “stable”, separate:
- tested facts;
- theoretical compatibility;
- experimental behavior.

Do not overwrite a stable release merely because a beta feature works in one
short test.
