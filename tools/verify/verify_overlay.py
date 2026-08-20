# -*- coding: utf-8 -*-
"""程序化验证 Overlay 几何与交互改动，无需人眼看屏。"""
import importlib.util
import tkinter as tk

spec = importlib.util.spec_from_file_location(
    "hudmod",
    r"D:\harness\连携cd-图像识别\EndfieldCDHUD_v2.3_Source\src\EndfieldCDHUD.pyw",
)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond), detail))
    print(("PASS " if cond else "FAIL ") + name + (f"  [{detail}]" if detail else ""))


root = tk.Tk()
root.withdraw()
ov = m.Overlay(root)

# 1) 间距：默认 scale=1.0 spacing=1.0 -> gap 应为 43（旧 54，空隙减半）
check("gap default == 43.0", abs(ov.gap - 43.0) < 0.001, f"gap={ov.gap}")
check("W compact (< old 238)", ov.W < 238, f"W={ov.W}, H={ov.H}")

# 2) spacing/scale 联动
ov.set_spacing(0.5)
check("gap @ spacing=0.5 == 21.5", abs(ov.gap - 21.5) < 0.001, f"gap={ov.gap}")
ov.set_spacing(1.0)

# 3) Ctrl+滚轮缩放
class Ev:
    def __init__(self, d):
        self.delta = d


changed = []
ov.on_scale_changed = lambda v: changed.append(v)
s0 = ov.scale
ov._ctrl_wheel(Ev(120))
check("wheel +1 -> scale+0.1", abs(ov.scale - (s0 + 0.1)) < 0.001,
      f"{s0} -> {ov.scale}")
check("wheel callback fired", len(changed) == 1 and abs(changed[0] - ov.scale) < 0.001,
      f"callback={changed}")
ov._ctrl_wheel(Ev(-240))
check("wheel -2 -> scale-0.2", abs(ov.scale - (s0 + 0.1 - 0.2)) < 0.001,
      f"scale={ov.scale}")
# 上限 clamp
ov.scale = 2.45
ov._ctrl_wheel(Ev(120))
check("wheel clamp at 2.5", abs(ov.scale - 2.5) < 0.001, f"scale={ov.scale}")
# 下限 clamp
ov.scale = 0.55
ov._ctrl_wheel(Ev(-120))
check("wheel clamp at 0.5", abs(ov.scale - 0.5) < 0.001, f"scale={ov.scale}")
ov.set_scale(1.0)

# 4) 拖拽：press -> move -> release 更新自定义位置
mon = {"left": 0, "top": 0, "width": 1920, "height": 1080}
ov.monitor = mon
ov.place(mon)
ov._apply_geometry(960, 400)

class E:
    def __init__(self, x_root=0, y_root=0):
        self.x_root = x_root
        self.y_root = y_root


ov._drag_press(E(960, 400))
ov._drag_move(E(1100, 520))
ov._drag_release(E(1100, 520))
check("drag updates custom_rel", ov.custom_rel is not None,
      f"custom_rel={ov.custom_rel}")
check("drag sets position 自定义", ov.position_name == "自定义",
      f"position={ov.position_name}")
rx, ry = ov.custom_rel
check("drag rel coords plausible", 0.0 <= rx <= 1.0 and 0.0 <= ry <= 1.0,
      f"rel=({rx:.4f},{ry:.4f})")

root.destroy()

fails = [n for n, ok, _ in results if not ok]
print("----")
print(f"{len(results) - len(fails)}/{len(results)} checks passed")
if fails:
    print("FAILED:", fails)
    raise SystemExit(1)
