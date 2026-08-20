终末地连携 CD HUD v2.0.1

Hotfix：修复切到浏览器中的“终末地”网页时 HUD 误弹出。

原因：
v2.0 的前台判断同时检查：
- 前台进程名
- 前台窗口标题是否“包含 Endfield / 终末地”

因此 Edge / B站标签页标题里出现“终末地”时，会被误判成游戏。

v2.0.1：
- 优先精确判断前台进程是否为 Endfield.exe
- 只有在无法取得进程路径时，才允许“窗口标题完全等于游戏名”的兜底
- 不再使用窗口标题包含关键字的模糊匹配

因此：
- Endfield.exe 前台 -> HUD 显示
- Edge / B站 / ChatGPT 中打开终末地相关页面 -> HUD 隐藏
- 切回游戏 -> HUD 自动恢复

其余保持 v2.0：
- READY / CD / 死亡检测算法不改
- 独立背景/标志透明度
- HUD 50%~250% 缩放
- 独立间距
- 槽位颜色
- 数字开关
- CD中暗色/隐藏
- 提示音和外圈高亮可选
- 单实例
- 设置保存
- 无 CMD 启动

正常启动：
START_SILENT.vbs

打包 EXE：
BUILD_EXE.bat
生成：
dist\EndfieldCDHUD.exe
