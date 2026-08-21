# EndfieldComboHUD v1.01

《明日方舟：终末地》连携技状态的外部 HUD。纯屏幕识别，不读内存、不注入进程、不改游戏文件。

## 快速开始

### 方式一：下载 exe（推荐，无需安装 Python）

从 **GitHub Releases** 下载 `EndfieldComboHUD_v1.01.exe`，双击运行即可。

### 方式二：源码运行（需要 Python 3.10+，仅 Windows）

```bat
python src\EndfieldCDHUD.pyw
```

本项目**零第三方运行时依赖**（仅 Python 标准库 + tkinter），无需 pip install 任何东西。

## 使用说明

1. 启动后自动开始监测，确认游戏在前台即生效
2. 战斗时在游戏画面底部显示 4 个干员的连携技状态（实心圆或角色头像）
3. 交互：
   - **左键按住 HUD 拖动**：调整位置（自动保存）
   - **Ctrl + 滚轮**：调整 HUD 大小
   - **双击 HUD**：抓取游戏头像
4. 暂停/恢复/退出：系统托盘图标右键菜单

### 界面（三页）

| 页 | 内容 |
|---|---|
| **干员** | 分辨率、干员显示（1号位~4号位）、操作布局、显示（仅战斗显示 / 隐藏 HUD） |
| **标识** | 抓取头像、恢复默认样式（实心圆为默认固定格式，抓取头像后自动切换） |
| **外观** | 通用设置（标志大小/间距/不透明度、CD 秒数、不可用状态）、工具（调试/恢复默认/预览）、关于 |

### 头像抓取步骤

1. 游戏内进入**角色界面**
2. 点击界面**右下角切换视图**（切换到显示 4 个头像的视图）
3. 点击**抓取头像**（「标识」页），或直接**双击游戏画面上的 HUD 悬浮窗**
4. 弹出抓取结果预览；头像按固定坐标圆形裁剪，跨分辨率自动缩放

> 抓取成功后自动切换为角色头像样式（FF14 式半透明 CD 遮罩）；点击"恢复默认样式"回到实心圆。

### 其他设置（干员页）

- **仅在战斗中显示 HUD**：取消勾选后游戏前台常显
- **隐藏 HUD**：保留检测，仅隐藏悬浮窗
- **显示调试信息**（外观页）：实时查看各槽位识别数值，便于反馈问题

## 已适配 / 已知限制

- 分辨率：3840×2160（主测）、2560×1440（实测通过）、1920×1080（理论缩放）
- 仅支持 16:9；21:9 / 32:9 未适配
- 手柄布局：Beta，仅 4K 实测

## 从源码构建 exe

```bat
python -m PyInstaller --noconfirm --clean EndfieldComboHUD_v1.01.spec
```

产物在 `dist\EndfieldComboHUD_v1.01.exe`。

## 项目结构

```text
EndfieldComboHUD/
├─ src/            # 当前源码（EndfieldCDHUD.pyw 为主程序）
├─ baseline/v2.0.1 # 稳定回滚基线（勿改）
├─ docs/           # 检测架构、测试清单、历史
├─ tools/          # 构建脚本、回归验证脚本（tools/verify/）
├─ samples/        # 参考样例（local/ 本地录像不入库）
├─ AGENTS.md       # 开发约束（改动前必读）
└─ EndfieldComboHUD_v1.01.spec  # PyInstaller 打包配置
```

## 给开发者

- 改动前先读 `AGENTS.md`、`CURRENT_STATE.md`、`docs/DETECTION_ARCHITECTURE.md`
- 回归验证：`python tools\verify\verify_avatar.py`（等）
- 语法检查：`tools\CHECK_SYNTAX.bat`
