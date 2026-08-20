# -*- coding: utf-8 -*-
"""验证战斗显隐开关：勾选/取消 x 战斗状态 x 前台状态。"""
import importlib.util
import tkinter as tk

spec = importlib.util.spec_from_file_location(
    "hudmod",
    r"D:\harness\连携cd-图像识别\EndfieldCDHUD_v2.3_Source\src\EndfieldCDHUD.pyw",
)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

root = tk.Tk()
root.withdraw()


class OverlayMock:
    def __init__(self):
        self.adjust_mode = False
        self._visible = False

    def show(self):
        self._visible = True

    def hide(self):
        self._visible = False


def make_app(gate_value, combat_present, fg=True, running=True, hide_hud=False):
    app = object.__new__(m.App)
    app.overlay = OverlayMock()
    app.preview_until = 0.0
    app.running = running
    app.combat_hud_present = combat_present
    app.status = tk.StringVar()
    app.show_in_combat_only_var = tk.BooleanVar(value=gate_value)
    app.hide_hud_var = tk.BooleanVar(value=hide_hud)
    app._monitoring_status_text = lambda: "MON-TEXT"
    m.is_endfield_foreground = lambda: fg
    return app


results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond)))
    print(("PASS " if cond else "FAIL ") + name + (f"  [{detail}]" if detail else ""))


# 1) 默认勾选 + 未进战斗 + 前台 -> 隐藏（原行为不变）
a = make_app(True, False)
a._sync_overlay_visibility()
check("gate=on, no combat, fg -> hidden", not a.overlay._visible,
      f"visible={a.overlay._visible} status={a.status.get()}")

# 2) 默认勾选 + 已进战斗 + 前台 -> 显示
a = make_app(True, True)
a._sync_overlay_visibility()
check("gate=on, combat, fg -> shown", a.overlay._visible)

# 3) 取消勾选 + 未进战斗 + 前台 -> 显示（新功能核心）
a = make_app(False, False)
a._sync_overlay_visibility()
check("gate=off, no combat, fg -> shown", a.overlay._visible,
      f"visible={a.overlay._visible}")

# 4) 取消勾选 + 已进战斗 + 前台 -> 显示
a = make_app(False, True)
a._sync_overlay_visibility()
check("gate=off, combat, fg -> shown", a.overlay._visible)

# 5) 非前台 -> 隐藏（勾选与否都隐藏）
a = make_app(False, False, fg=False)
a._sync_overlay_visibility()
check("not foreground -> hidden", not a.overlay._visible)

# 6) 未运行 -> 保持原状态
a = make_app(True, False, running=False)
a.overlay._visible = True
a._sync_overlay_visibility()
check("not running -> untouched", a.overlay._visible)

# 7) 默认值
d = m.App._defaults(m.App)
check("default show_in_combat_only is True", d.get("show_in_combat_only") is True)
check("default hide_hud is False", d.get("hide_hud") is False)

# 8) 隐藏 HUD 总开关（最高优先级：战斗/前台也强制隐藏）
a = make_app(True, True, fg=True, hide_hud=True)
a.overlay._visible = True
a._sync_overlay_visibility()
check("hide_hud on, combat, fg -> hidden", not a.overlay._visible,
      f"visible={a.overlay._visible} status={a.status.get()}")

a = make_app(False, True, fg=True, hide_hud=True)
a.overlay._visible = True
a._sync_overlay_visibility()
check("hide_hud on, gate off, combat -> hidden", not a.overlay._visible)

a = make_app(True, False, fg=True, hide_hud=False)
a.overlay._visible = True
a._sync_overlay_visibility()
check("hide_hud off, no combat, gate on -> hidden", not a.overlay._visible)

root.destroy()

fails = [n for n, ok in results if not ok]
print("----")
print(f"{len(results) - len(fails)}/{len(results)} checks passed")
if fails:
    print("FAILED:", fails)
    raise SystemExit(1)
