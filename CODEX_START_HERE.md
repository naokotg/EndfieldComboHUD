# CODEX_START_HERE.md

This folder is prepared as a Codex handoff repository.

## First use

Open the **repository folder itself** in Codex.

Recommended first prompt:

> 先不要改代码。请阅读 AGENTS.md、CURRENT_STATE.md 和
> docs/DETECTION_ARCHITECTURE.md，然后检查 src/ 与
> baseline/v2.0.1/ 的差异。给我总结：
> 1）当前稳定核心；
> 2）v2.1 新增模块；
> 3）目前最大回归风险；
> 4）你之后修改代码时会遵守哪些约束。

For a bug-fix task, give Codex:
- the observed behavior;
- exact reproduction steps;
- video timestamps or screenshots;
- whether the issue exists in v2.0.1 or only v2.1;
- which behavior must not regress.

## Future bug videos

Do not commit large videos by default.

Put local recordings under:

`samples/local/`

That directory is ignored by Git.

When possible, also provide a few key screenshots/timestamps. If Codex needs
frames from a video, it may use an available local tool such as ffmpeg rather
than guessing from the filename.

## Source locations

- Current development code: `src/`
- Stable rollback/reference: `baseline/v2.0.1/`
- Build scripts: `tools/`
- Architecture and test notes: `docs/`

## Release rule

A Codex-produced change is not automatically a new stable release.
Review the diff, run the syntax check, and perform the relevant manual
regression tests first.
