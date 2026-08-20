# EndfieldCDHUD v2.3 源码包

这是 v2.3 发布版对应的二次开发源码。程序仅通过 Windows 屏幕截图和画面识别工作，不读取游戏内存、不注入游戏进程、不修改游戏文件。

## 快速开始

1. 安装 Python 3，Windows 安装时勾选 `Add Python to PATH`。
2. 运行 `tools\START_DEBUG.bat` 进行调试。
3. 运行 `tools\CHECK_SYNTAX.bat` 检查语法。
4. 运行 `tools\BUILD_EXE.bat` 生成 `dist\EndfieldCDHUD_v2.3.exe`。首次构建会安装 PyInstaller。

运行时仅使用 Python 标准库；PyInstaller 只是构建依赖。

## 修改 UI

- 发布入口：`src\EndfieldCDHUD.pyw`
- 调试入口：`src\EndfieldCDHUD_debug.py`
- HUD 窗口、画布和圆圈绘制：`Overlay` 类
- 设置窗口、启停控制与配置：`App` 类
- 默认颜色、大小、位置和动画参数：搜索 `Overlay`、`READY_PULSE_`、`OVERLAY_`

两个入口当前内容一致。如果修改了其中一个，请同步另一个，或者只维护 `EndfieldCDHUD.pyw` 并相应调整调试脚本。

## 修改前请注意

如果只想改界面，请尽量不要改动 READY / CONSUMED / 低血量 / 死亡检测阈值和状态机。完整维护约束见：

- `AGENTS.md`
- `CURRENT_STATE.md`
- `docs\DETECTION_ARCHITECTURE.md`
- `docs\TEST_CHECKLIST.md`

`baseline\v2.0.1` 是受保护的稳定回退参考，不应直接覆盖。

## 已知范围

- Windows only
- 键盘鼠标：v2.3 支持路径
- 手柄：Beta，按 Xbox/XInput 画面标定
- 3840x2160 16:9：主要实机测试环境
- 2560x1440 16:9：进行过短时实机验证
- 1920x1080：仅理论缩放，未实测
- 21:9 / 32:9：未适配

## 授权说明

此压缩包没有附带开源许可证。这意味着源码可供查看和协作修改，但正式公开发布、分发改版或接受外部贡献前，项目所有者应明确选择并添加许可证，例如 MIT 或 GPL-3.0。

## 本包已排除

- 实机录像和 `samples\local`
- PyInstaller `build` / `dist` 产物
- 历史测试 exe 和发布 exe
- Python 缓存、本机设置和 Git 历史
