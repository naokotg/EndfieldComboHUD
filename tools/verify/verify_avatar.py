# -*- coding: utf-8 -*-
"""验证头像抓取核心：PNG 圆形裁剪编码 + Overlay 头像显示。"""
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
results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond)))
    print(("PASS " if cond else "FAIL ") + name + (f"  [{detail}]" if detail else ""))


# ---- 1) PNG 编码与圆形裁剪 ----
w = h = 40
raw = bytearray()
for _y in range(h):
    for _x in range(w):
        raw += bytes((0, 0, 255, 255))  # BGRA: 纯红
png = m.bgra_to_circle_png(bytes(raw), w, h)
check("png has valid signature", png is not None and png[:8] == b"\x89PNG\r\n\x1a\n")

photo = tk.PhotoImage(data=png)
check("photo size 40x40", photo.width() == 40 and photo.height() == 40,
      f"{photo.width()}x{photo.height()}")

# Tk 将 PNG alpha 存为透明度：corner 透明、center 不透明
corner_trans = photo.transparency_get(0, 0)
center_trans = photo.transparency_get(20, 20)
check("corner transparent (circle crop)", corner_trans,
      f"transparency_get(0,0)={corner_trans}")
check("center opaque red", not center_trans,
      f"transparency_get(20,20)={center_trans}")

# 边缘抗锯齿：距中心约 radius=19 处 alpha 应介于 0~255
edge_trans = photo.transparency_get(20, 0)  # 距中心 20 > 19 略外
check("just-outside edge transparent", edge_trans,
      f"transparency_get(20,0)={edge_trans}")

# 无效输入
check("short raw -> None", m.bgra_to_circle_png(b"", 10, 10) is None)

# ---- 2) Overlay 头像显示（RGBA 数据） ----
raw40 = bytearray()
for _y in range(40):
    for _x in range(40):
        raw40 += bytes((0, 0, 255, 255))  # BGRA 纯红
rgba40 = m.bgra_to_circle_rgba(bytes(raw40), 40, 40)
check("circle rgba len", rgba40 is not None and len(rgba40) == 40 * 40 * 4)

ov = m.Overlay(root)
ov.set_avatars([(rgba40, 40, 40), None, None, (rgba40, 40, 40)])
check("has_avatar 0/3 true, 1 false",
      ov.has_avatar(0) and ov.has_avatar(3) and not ov.has_avatar(1))

disp = ov._avatar_display_photo(0, 32)
check("scaled display exactly 32px", disp is not None and disp.width() == 32,
      f"display={disp.width()}x{disp.height()}")

# 缓存命中
disp2 = ov._avatar_display_photo(0, 32)
check("scale cache reused", disp2 is disp)

# redraw 各状态不崩溃
mon = {"left": 0, "top": 0, "width": 1920, "height": 1080}
ov.place(mon)
ov.set_ready(0, True)
ov.set_progress(3, 0.5, remaining=6.0, learned_cd=12.0, confidence=1.0, active=True)
ov.redraw()
check("redraw with avatars ok", True)

# 清除
ov.clear_avatars()
check("clear avatars", not ov.has_avatar(0) and not ov.has_avatar(3))

# ---- 3) 手柄 ROI 平移可用于头像 ----
base = [m.scaled_roi(r, mon) for r in m.CONTROLLER_AVATAR_ROIS]
shifted = m.controller_shifted_rois(base, mon, 1.0)
check("controller avatar rois shiftable",
      len(shifted) == 4 and shifted[0]["left"] != base[0]["left"],
      f"in={base[0]['left']} out={shifted[0]['left']}")

# ---- 4) 键鼠头像 ROI 中心与死亡图标中心一致 ----
for i in range(4):
    ar = m.AVATAR_REF_ROIS[i]
    dr = m.SLOT_DEATH_ICON_ROIS[i]
    acx = (ar[0] + ar[2]) / 2
    dcx = (dr[0] + dr[2]) / 2
    assert abs(acx - dcx) < 0.01, f"slot{i} x center mismatch"
check("avatar ROI center == death-icon center (all 4)", True)

# ---- 5) 双击抓取回调 ----
double_called = []
ov2 = m.Overlay(root, on_double_click=lambda: double_called.append(1))
class E2:
    pass
ov2._double_click(E2())
check("double-click triggers callback", len(double_called) == 1)
ov2.on_double_click = None
ov2._double_click(E2())
check("double-click without callback safe", len(double_called) == 1)

# ---- 6) 隐藏窗口时拖拽释放不保存位置 ----
mon2 = {"left": 0, "top": 0, "width": 1920, "height": 1080}
ov2.monitor = mon2
ov2.place(mon2)
ov2._apply_geometry(100, 200)
ov2.custom_rel = (0.1, 0.2)
ov2.hide()
class E3:
    x_root = 400
    y_root = 500
ov2._drag_press(E3())
ov2._drag_release(E3())
check("drag release while hidden skips save",
      ov2.custom_rel == (0.1, 0.2) and ov2._drag_start_pointer is None)

# ---- 7) AvatarAligner 对准器 ----
mon4k = {"left": 0, "top": 0, "width": 3840, "height": 2160}
grabbed = []
ref0 = m.AVATAR_ALIGN_DEFAULT[0]
aligner = m.AvatarAligner(
    root, mon4k,
    [list(p) for p in m.AVATAR_ALIGN_DEFAULT],
    on_grab=lambda refs: grabbed.append(refs),
)
aligner.show()
# 坐标换算往返：参考系 -> 屏幕 -> 画布
sx = mon4k["width"] / m.REF_W
cx, cy, r = aligner._slot_screen(0)
check("aligner slot0 screen pos scaled",
      abs(cx - ref0[0] * sx) < 1.0,
      f"cx={cx:.1f} expect={ref0[0] * sx:.1f}")
back = aligner._canvas_to_ref(cx - mon4k["left"], cy - mon4k["top"])
check("aligner ref round-trip",
      abs(back[0] - ref0[0]) < 0.01 and abs(back[1] - ref0[1]) < 0.01,
      f"back={back}")
# 拖动后圆心更新
aligner._press(type("E", (), {"x": int(cx), "y": int(cy)})(), 0)
aligner._move(type("E", (), {"x": int(cx) + 100, "y": int(cy)})(), 0)
check("aligner drag moves slot",
      abs(aligner.slots[0][0] - ref0[0] - 100 * m.REF_W / 3840) < 2.0,
      f"slot0 cx_ref={aligner.slots[0][0]:.1f}")
# 半径调整 clamp
aligner.slots = [list(p) for p in m.AVATAR_ALIGN_DEFAULT]
aligner.slots[0][2] = m.AvatarAligner.MIN_R_REF + 2
aligner._ctrl_wheel(type("E", (), {"delta": -120})())
check("aligner radius clamp min", aligner.slots[0][2] == m.AvatarAligner.MIN_R_REF,
      f"r={aligner.slots[0][2]}")
# 抓取回调
aligner._do_grab()
check("aligner grab callback fired", len(grabbed) == 1 and len(grabbed[0]) == 4)
# 恢复默认
aligner.slots[0][0] = 500
aligner._reset_defaults()
check("aligner reset defaults", abs(aligner.slots[0][0] - m.AVATAR_ALIGN_DEFAULT[0][0]) < 0.01)
aligner.destroy()

# ---- 8) 无保存坐标时使用内置默认并落盘 ----
app_mock = object.__new__(m.App)
app_mock.avatar_align = None
app_mock.root = root
saved = []
app_mock._save_settings = lambda: saved.append(1)
app_mock._ensure_game_foreground = lambda: False  # 阻止继续执行抓取
orig_mb = m.messagebox
m.messagebox = type(
    "MB", (), {"showinfo": staticmethod(lambda *a, **k: None)}
)()
app_mock._capture_avatars()
check("no align -> uses default and saves",
      app_mock.avatar_align == [tuple(p) for p in m.AVATAR_ALIGN_DEFAULT]
      and len(saved) == 1,
      f"align={app_mock.avatar_align}")
m.messagebox = orig_mb

# 双击入口不再打开对准器（方法已移除）
check("aligner entry removed",
      not hasattr(app_mock, "_open_avatar_aligner"))

# ---- 9) 前台拉回：游戏已前台 -> 直接 True；找不到窗口 -> False ----
orig_fg = m.is_endfield_foreground
orig_find = m.find_endfield_window
app_mock2 = object.__new__(m.App)
app_mock2.root = root

m.is_endfield_foreground = lambda: True
check("ensure fg when already foreground", app_mock2._ensure_game_foreground() is True)

m.is_endfield_foreground = lambda: False
m.find_endfield_window = lambda: 0
check("ensure fg fails when no game window",
      app_mock2._ensure_game_foreground() is False)

m.is_endfield_foreground = orig_fg
m.find_endfield_window = orig_find

# 真实环境：无游戏时 find_endfield_window 不抛异常
try:
    h = m.find_endfield_window()
    check("find_endfield_window no crash", h == 0 or isinstance(h, int))
except Exception:
    check("find_endfield_window no crash", False, "raised")

# ---- 10) FF14 式 CD 遮罩 PNG ----
p_full = tk.PhotoImage(data=m.make_cd_mask_png(40, 1.0))
check("mask frac=1 center covered", not p_full.transparency_get(20, 20))
check("mask frac=1 top covered", not p_full.transparency_get(20, 1))

p_none = tk.PhotoImage(data=m.make_cd_mask_png(40, 0.0))
check("mask frac=0 all transparent", p_none.transparency_get(20, 20))

p_half = tk.PhotoImage(data=m.make_cd_mask_png(40, 0.5))
# 从 12 点逆时针覆盖半圆（t 从 2pi 递减）：上(逆时针侧)、左、下被遮；右不遮
check("mask frac=0.5 top covered", not p_half.transparency_get(19, 1))
check("mask frac=0.5 left covered", not p_half.transparency_get(1, 20))
check("mask frac=0.5 bottom covered", not p_half.transparency_get(19, 38))
check("mask frac=0.5 right uncovered", p_half.transparency_get(38, 20))

# 遮罩 alpha 字节：中心像素 alpha 应为 CD_MASK_ALPHA
import struct, zlib as _zlib
png = m.make_cd_mask_png(20, 1.0)
pos = 8
idat = b""
while pos < len(png):
    ln = struct.unpack(">I", png[pos:pos + 4])[0]
    tag = png[pos + 4:pos + 8]
    if tag == b"IDAT":
        idat += png[pos + 8:pos + 8 + ln]
    pos += 12 + ln
rows = _zlib.decompress(idat)
center_alpha = rows[(10 * (1 + 20 * 4)) + 1 + 10 * 4 + 3]
check("mask center alpha == CD_MASK_ALPHA",
      center_alpha == m.CD_MASK_ALPHA, f"alpha={center_alpha}")

# ---- 11) 样式切换与遮罩缓存 ----
ov3 = m.Overlay(root)
ov3.set_style("avatar")
check("style set avatar", ov3.style == "avatar")
ov3.set_style("circle")
check("style set circle", ov3.style == "circle")
ov3.set_style("bogus")
check("style invalid -> circle", ov3.style == "circle")
mask_a = ov3._cd_mask_photo(32, 0.5)
mask_b = ov3._cd_mask_photo(32, 0.5)
check("mask cache hit", mask_a is mask_b)

# ---- 12) 头像缩放无突变且精确（100px 源图，target 32..80 连续） ----
raw100 = bytearray()
for _y in range(100):
    for _x in range(100):
        raw100 += bytes((0, 0, 255, 255))
rgba100 = m.bgra_to_circle_rgba(bytes(raw100), 100, 100)
ov4 = m.Overlay(root)
ov4.set_avatars([(rgba100, 100, 100), None, None, None])
sizes = []
for t in range(32, 81, 2):
    p = ov4._avatar_display_photo(0, float(t))
    sizes.append((t, p.width()))
jumps = [
    (sizes[i - 1], sizes[i])
    for i in range(1, len(sizes))
    if abs(sizes[i][1] - sizes[i - 1][1]) > 2
]
check("avatar scale smooth (no jumps)", not jumps, f"jumps={jumps}")
check("avatar exactly matches target size",
      all(w == t for t, w in sizes),
      f"mismatch={[(t, w) for t, w in sizes if w != t][:5]}")

# ---- 13) PNG 编码/解码往返 + 重采样 ----
png40 = m._encode_rgba_png(40, 40, m.rgba_to_scanlines(rgba40, 40, 40))
dec = m.decode_png_to_rgba(png40)
check("png encode/decode round-trip",
      dec is not None and dec[0] == 40 and dec[1] == 40 and dec[2] == rgba40)

small = m.resample_rgba(rgba40, 40, 40, 20, 20)
check("resample size correct", len(small) == 20 * 20 * 4)
check("resample keeps center opaque", small[(10 * 20 + 10) * 4 + 3] == 255)
check("resample keeps corner transparent", small[0 + 3] == 0)

root.destroy()

fails = [n for n, ok in results if not ok]
print("----")
print(f"{len(results) - len(fails)}/{len(results)} checks passed")
if fails:
    print("FAILED:", fails)
    raise SystemExit(1)
