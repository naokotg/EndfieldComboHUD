# -*- coding: utf-8 -*-
"""
终末地连携 CD HUD v2.0.1

Windows / 纯外部屏幕识别 / 无第三方依赖
- 不读游戏内存
- 不注入游戏进程
- Win32 GDI 截取左下 HUD 小区域
- 1~4 槽独立检测
- READY 瞬间：数字圆圈闪烁 + 提示音
- READY 持续：小型状态 HUD 常亮
- v1.1：换人 CD 暗化不再重新触发 READY 提示
"""

import sys
import os
import json
import time
import queue
import threading
import traceback
import ctypes
from ctypes import wintypes
import tkinter as tk
from tkinter import ttk, messagebox

if sys.platform != "win32":
    raise RuntimeError("此程序只支持 Windows。")

# ---------- DPI ----------
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32
kernel32 = ctypes.windll.kernel32

# ---------- Single instance ----------
# 无 CMD + 托盘模式下旧实例很容易被忘在后台。
# 使用 Windows named mutex，确保同一用户会话只运行一个插件实例。
ERROR_ALREADY_EXISTS = 183
SINGLE_INSTANCE_MUTEX_NAME = r"Local\EndfieldCDHUD_SingleInstance_v1"

kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
kernel32.CreateMutexW.restype = wintypes.HANDLE
kernel32.GetLastError.argtypes = []
kernel32.GetLastError.restype = wintypes.DWORD

_single_instance_mutex = None


def acquire_single_instance():
    global _single_instance_mutex
    handle = kernel32.CreateMutexW(None, False, SINGLE_INSTANCE_MUTEX_NAME)
    if not handle:
        return True

    already_exists = kernel32.GetLastError() == ERROR_ALREADY_EXISTS
    if already_exists:
        try:
            kernel32.CloseHandle(handle)
        except Exception:
            pass
        return False

    _single_instance_mutex = handle
    return True


# ---------- Foreground game detection ----------
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

# Strict foreground identity.
# Do NOT use substring matching on arbitrary window titles:
# a browser tab containing "终末地" / "Endfield" must never count as the game.
GAME_EXE_NAME = "endfield.exe"
GAME_EXACT_TITLES = {
    "endfield",
    "明日方舟：终末地",
    "明日方舟:终末地",
}

user32.GetForegroundWindow.argtypes = []
user32.GetForegroundWindow.restype = wintypes.HWND
user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
user32.GetWindowTextLengthW.restype = ctypes.c_int
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowTextW.restype = ctypes.c_int
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowThreadProcessId.restype = wintypes.DWORD

kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.QueryFullProcessImageNameW.argtypes = [
    wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)
]
kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
kernel32.CloseHandle.restype = wintypes.BOOL


def foreground_window_identity():
    """Return (hwnd, title, exe_path) of the current foreground window."""
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return 0, "", ""

    title = ""
    try:
        n = user32.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(max(1, n + 1))
        user32.GetWindowTextW(hwnd, buf, len(buf))
        title = buf.value
    except Exception:
        pass

    exe_path = ""
    hproc = None
    try:
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value:
            hproc = kernel32.OpenProcess(
                PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value
            )
            if hproc:
                size = wintypes.DWORD(1024)
                buf = ctypes.create_unicode_buffer(size.value)
                if kernel32.QueryFullProcessImageNameW(
                    hproc, 0, buf, ctypes.byref(size)
                ):
                    exe_path = buf.value
    except Exception:
        pass
    finally:
        if hproc:
            try:
                kernel32.CloseHandle(hproc)
            except Exception:
                pass

    return hwnd, title, exe_path


def is_endfield_foreground():
    _, title, exe_path = foreground_window_identity()

    exe_name = os.path.basename(exe_path).casefold().strip()
    if exe_name == GAME_EXE_NAME:
        return True

    # Fallback only when process-path lookup failed.
    # Exact-title matching avoids false positives from Edge/Bilibili tabs such as
    # "终末地 连携技 - 哔哩哔哩".
    if not exe_name:
        title_name = title.casefold().strip()
        return title_name in GAME_EXACT_TITLES

    return False

# ---------- Calibration ----------
REF_W = 2048
REF_H = 1152

# 2048x1152 参考图中四个角色“连携能量条内部核心区域”
# 实际运行时会按显示器分辨率同比缩放。
SLOT_ENERGY_ROIS = [
    (45, 1053, 134, 1057),
    (169, 1053, 259, 1057),
    (294, 1053, 383, 1057),
    (419, 1053, 508, 1057),
]

# 血量条内部区域。用于识别“角色已经死亡”。
# 活着时这里至少有一小段明显的青色/红色彩色填充；
# 死亡后整条变成灰色。
SLOT_HP_ROIS = [
    (45, 1057, 134, 1064),
    (169, 1057, 259, 1064),
    (294, 1057, 383, 1064),
    (419, 1057, 508, 1064),
]

CAPTURE_HZ = 10.0
CONFIRM_FRAMES = 3
CONSUME_CONFIRM_FRAMES = 3

# 死亡判定：血条区域连续若干帧几乎完全没有彩色像素。
DEAD_CONFIRM_FRAMES = 5
ALIVE_COLOR_CHROMA_MIN = 45
ALIVE_COLOR_VALUE_MIN = 105
ALIVE_COLOR_RATIO_MIN = 0.008

# READY 判据
WHITE_MIN = 200
WHITE_CHROMA_MAX = 35
BRIGHT_MIN = 170
BRIGHT_CHROMA_MAX = 50
READY_WHITE_RATIO = 0.72
READY_BRIGHT_RATIO = 0.88
READY_MEAN_MIN = 190.0

# 低血量等状态会给 HUD 染色，导致“中性白”比例下降。
# 只要能量条整体仍然足够亮，也应识别为 READY。
TINTED_READY_LUMA_MIN = 200
TINTED_READY_LUMA_RATIO = 0.90
TINTED_READY_MEAN_MIN = 210.0

# “真正消耗连携”的签名。
# 换人 CD 会把整个 HUD 暂时压暗，但满条仍保留大量亮像素；
# 真正使用连携后，能量条会清空，亮像素比例会骤降。
CONSUMED_WHITE_RATIO_MAX = 0.15
CONSUMED_BRIGHT_RATIO_MAX = 0.30

SRCCOPY = 0x00CC0020
CAPTUREBLT = 0x40000000
BI_RGB = 0
DIB_RGB_COLORS = 0


class RECT(ctypes.Structure):
    _fields_ = [
        ("left", wintypes.LONG),
        ("top", wintypes.LONG),
        ("right", wintypes.LONG),
        ("bottom", wintypes.LONG),
    ]


class MONITORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", RECT),
        ("rcWork", RECT),
        ("dwFlags", wintypes.DWORD),
    ]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", wintypes.LONG),
        ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [
        ("bmiHeader", BITMAPINFOHEADER),
        ("bmiColors", wintypes.DWORD * 3),
    ]


def enum_monitors():
    monitors = []
    MONITORENUMPROC = ctypes.WINFUNCTYPE(
        wintypes.BOOL,
        wintypes.HMONITOR,
        wintypes.HDC,
        ctypes.POINTER(RECT),
        wintypes.LPARAM,
    )

    def callback(hmon, hdc, lprect, lparam):
        mi = MONITORINFO()
        mi.cbSize = ctypes.sizeof(MONITORINFO)
        if user32.GetMonitorInfoW(hmon, ctypes.byref(mi)):
            r = mi.rcMonitor
            monitors.append({
                "left": int(r.left),
                "top": int(r.top),
                "width": int(r.right - r.left),
                "height": int(r.bottom - r.top),
                "primary": bool(mi.dwFlags & 1),
            })
        return True

    cb = MONITORENUMPROC(callback)
    if not user32.EnumDisplayMonitors(0, 0, cb, 0):
        raise ctypes.WinError()
    return monitors


def scaled_roi(ref_roi, monitor):
    x0, y0, x1, y1 = ref_roi
    sx = monitor["width"] / REF_W
    sy = monitor["height"] / REF_H
    return {
        "left": monitor["left"] + round(x0 * sx),
        "top": monitor["top"] + round(y0 * sy),
        "width": max(2, round((x1 - x0) * sx)),
        "height": max(2, round((y1 - y0) * sy)),
    }


class GDICapture:
    def __init__(self):
        self.screen_dc = user32.GetDC(0)
        if not self.screen_dc:
            raise ctypes.WinError()

    def close(self):
        if self.screen_dc:
            user32.ReleaseDC(0, self.screen_dc)
            self.screen_dc = None

    def grab_bgra(self, left, top, width, height):
        mem_dc = gdi32.CreateCompatibleDC(self.screen_dc)
        if not mem_dc:
            raise ctypes.WinError()

        bmp = gdi32.CreateCompatibleBitmap(self.screen_dc, width, height)
        if not bmp:
            gdi32.DeleteDC(mem_dc)
            raise ctypes.WinError()

        old = gdi32.SelectObject(mem_dc, bmp)
        try:
            ok = gdi32.BitBlt(
                mem_dc, 0, 0, width, height,
                self.screen_dc, left, top,
                SRCCOPY | CAPTUREBLT,
            )
            if not ok:
                raise ctypes.WinError()

            bmi = BITMAPINFO()
            bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
            bmi.bmiHeader.biWidth = width
            bmi.bmiHeader.biHeight = -height
            bmi.bmiHeader.biPlanes = 1
            bmi.bmiHeader.biBitCount = 32
            bmi.bmiHeader.biCompression = BI_RGB

            buf = (ctypes.c_ubyte * (width * height * 4))()
            lines = gdi32.GetDIBits(
                mem_dc, bmp, 0, height,
                ctypes.byref(buf), ctypes.byref(bmi), DIB_RGB_COLORS,
            )
            if lines == 0:
                raise ctypes.WinError()

            return bytes(buf)
        finally:
            gdi32.SelectObject(mem_dc, old)
            gdi32.DeleteObject(bmp)
            gdi32.DeleteDC(mem_dc)


def analyze_bgra(raw):
    pixel_count = len(raw) // 4
    if pixel_count <= 0:
        return 0.0, 0.0, 0.0

    white = 0
    bright = 0
    total = 0

    mv = memoryview(raw)
    for i in range(0, len(raw), 4):
        b = mv[i]
        g = mv[i + 1]
        r = mv[i + 2]

        mn = min(r, g, b)
        mx = max(r, g, b)
        chroma = mx - mn
        total += r + g + b

        if mn >= WHITE_MIN and chroma <= WHITE_CHROMA_MAX:
            white += 1
        if mn >= BRIGHT_MIN and chroma <= BRIGHT_CHROMA_MAX:
            bright += 1

    mean_value = total / (pixel_count * 3.0)

    luma_high = 0
    mv = memoryview(raw)
    for i in range(0, len(raw), 4):
        b = mv[i]
        g = mv[i + 1]
        r = mv[i + 2]
        # 简单 RGB 平均足够用于这个小型 UI 白条。
        if (r + g + b) / 3.0 >= TINTED_READY_LUMA_MIN:
            luma_high += 1

    return (
        white / pixel_count,
        bright / pixel_count,
        mean_value,
        luma_high / pixel_count,
    )


def analyze_alive_bar(raw):
    """
    返回血量条区域中“明显彩色像素”的比例。
    - 正常血量：青蓝色
    - 低血量：红/粉色
    - 死亡：灰色，彩色比例接近 0
    """
    pixel_count = len(raw) // 4
    if pixel_count <= 0:
        return 0.0

    colored = 0
    mv = memoryview(raw)

    for i in range(0, len(raw), 4):
        b = mv[i]
        g = mv[i + 1]
        r = mv[i + 2]

        mx = max(r, g, b)
        mn = min(r, g, b)
        chroma = mx - mn

        if chroma >= ALIVE_COLOR_CHROMA_MIN and mx >= ALIVE_COLOR_VALUE_MIN:
            colored += 1

    return colored / pixel_count


class SlotState:
    def __init__(self):
        self.ready_streak = 0
        self.not_ready_streak = 0
        self.confirmed_ready = False
        self.seen_first_ready = False
        self.armed = False
        self.consumed_streak = 0
        self.consumed_seen = False
        self.white_ratio = 0.0
        self.bright_ratio = 0.0
        self.mean = 0.0
        self.luma_ratio = 0.0
        self.alive_color_ratio = 0.0
        self.dead_streak = 0
        self.is_dead = False



COLOR_PRESETS = {
    "蓝": "#4DA6FF",
    "青": "#67E8F9",
    "绿": "#5EE08A",
    "黄": "#FFD84D",
    "橙": "#FF9A4D",
    "红": "#FF5C5C",
    "粉": "#FF7EB6",
    "紫": "#C084FC",
    "白": "#FFFFFF",
}

DEFAULT_SLOT_COLOR_NAMES = ["蓝", "青", "紫", "橙"]


class Overlay:
    """
    v2.0 HUD renderer.

    Detection is deliberately outside this class. Overlay only consumes the
    final ready=True/False state.

    v2.0 renderer changes:
    - marker opacity and black background opacity are independent
    - black background can be fully hidden
    - marker size and spacing are independent
    - slot numbers can be hidden
    - unavailable slots can be dimmed or hidden

    Implementation note:
    Two small top-level windows are used on Windows:
    1) a black background window with its own alpha
    2) a color-key-transparent marker window with its own alpha
    No game process hooks or detection changes are involved.
    """

    BASE_W = 260
    BASE_H = 72
    TRANSPARENT_KEY = "#010203"

    POSITION_NAMES = {
        "中央偏下": "center_bottom",
        "中央偏上": "center_top",
        "左下": "left_bottom",
        "右下": "right_bottom",
        "自定义": "custom",
    }

    UNAVAILABLE_MODES = ("暗色显示", "完全隐藏")

    def __init__(self, root, on_drag_end=None):
        self.bg = "#0D0D0D"

        # Background layer: a plain black top-level with independent alpha.
        self.bg_win = tk.Toplevel(root)
        self.bg_win.withdraw()
        self.bg_win.overrideredirect(True)
        self.bg_win.attributes("-topmost", True)
        self.bg_win.configure(bg=self.bg)

        # Marker layer: transparent canvas carrying only circles/text.
        self.win = tk.Toplevel(root)
        self.win.withdraw()
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)

        self.transparency_supported = True
        try:
            self.win.configure(bg=self.TRANSPARENT_KEY)
            self.win.attributes("-transparentcolor", self.TRANSPARENT_KEY)
        except Exception:
            # Windows 10/11 Tk normally supports transparentcolor.  If a very
            # unusual Tk build does not, fall back to the old solid background
            # rather than making the HUD disappear.
            self.transparency_supported = False
            self.win.configure(bg=self.bg)

        canvas_bg = self.TRANSPARENT_KEY if self.transparency_supported else self.bg
        self.canvas = tk.Canvas(
            self.win,
            bg=canvas_bg,
            highlightthickness=0,
            bd=0,
        )
        self.canvas.pack(fill="both", expand=True)

        self.enabled = [True, True, True, True]
        self.ready = [False, False, False, False]
        self.slot_colors = [
            "#4DA6FF",
            "#67E8F9",
            "#C084FC",
            "#FF9A4D",
        ]

        self.flash_slot = None
        self.flash_enabled = False

        self.scale = 1.0
        self.spacing = 1.0
        self.marker_opacity = 1.0
        self.background_opacity = 0.62
        self.show_numbers = True
        self.unavailable_mode = "暗色显示"

        self.position_name = "中央偏下"
        self.custom_rel = None
        self.monitor = None

        self.adjust_mode = False
        self._visible = False
        self._drag_start_pointer = None
        self._drag_start_window = None
        self.on_drag_end = on_drag_end

        self.canvas.bind("<ButtonPress-1>", self._drag_press)
        self.canvas.bind("<B1-Motion>", self._drag_move)
        self.canvas.bind("<ButtonRelease-1>", self._drag_release)

        self.set_marker_opacity(self.marker_opacity)
        self.set_background_opacity(self.background_opacity)
        self._resize_canvas()

    @property
    def gap(self):
        return 54.0 * self.scale * self.spacing

    @property
    def W(self):
        outer = 24.0 * self.scale
        padding = 14.0 * self.scale
        width = 3.0 * self.gap + 2.0 * outer + 2.0 * padding
        return max(84, int(round(width)))

    @property
    def H(self):
        height = 72.0 * self.scale
        return max(38, int(round(height)))

    def _resize_canvas(self):
        self.canvas.configure(width=self.W, height=self.H)

    def _apply_geometry(self, x, y):
        geometry = f"{self.W}x{self.H}+{x}+{y}"
        self.bg_win.geometry(geometry)
        self.win.geometry(geometry)

    def set_marker_opacity(self, value):
        self.marker_opacity = max(0.20, min(1.0, float(value)))
        try:
            self.win.attributes("-alpha", self.marker_opacity)
        except Exception:
            pass

    def set_background_opacity(self, value):
        self.background_opacity = max(0.0, min(1.0, float(value)))
        try:
            self.bg_win.attributes("-alpha", max(0.01, self.background_opacity))
        except Exception:
            pass
        self._sync_background_layer()

    def _sync_background_layer(self):
        if not self.transparency_supported:
            self.bg_win.withdraw()
            return
        if self._visible and self.background_opacity > 0.001:
            self.bg_win.deiconify()
            self.bg_win.attributes("-topmost", True)
            self.bg_win.lift()
        else:
            self.bg_win.withdraw()

    def set_scale(self, value):
        self.scale = max(0.50, min(2.50, float(value)))
        self._resize_canvas()
        if self.monitor is not None:
            self.place(self.monitor, preserve_custom=True)
        else:
            self.redraw()

    def set_spacing(self, value):
        self.spacing = max(0.50, min(2.00, float(value)))
        self._resize_canvas()
        if self.monitor is not None:
            self.place(self.monitor, preserve_custom=True)
        else:
            self.redraw()

    def set_show_numbers(self, enabled):
        self.show_numbers = bool(enabled)
        self.redraw()

    def set_unavailable_mode(self, mode):
        if mode not in self.UNAVAILABLE_MODES:
            mode = "暗色显示"
        self.unavailable_mode = mode
        self.redraw()

    def set_flash_enabled(self, enabled):
        self.flash_enabled = bool(enabled)
        if not self.flash_enabled:
            self.flash_slot = None
            self.redraw()

    def set_adjust_mode(self, enabled):
        self.adjust_mode = bool(enabled)
        self.redraw()

    def set_position(self, name, custom_rel=None):
        if name in self.POSITION_NAMES:
            self.position_name = name
        if custom_rel is not None:
            self.custom_rel = tuple(custom_rel)
        if self.monitor is not None:
            self.place(self.monitor, preserve_custom=True)

    def _preset_xy(self, monitor):
        w, h = self.W, self.H
        margin_x = max(24, int(monitor["width"] * 0.02))
        margin_y = max(24, int(monitor["height"] * 0.05))
        mode = self.POSITION_NAMES.get(self.position_name, "center_bottom")

        if mode == "center_top":
            x = monitor["left"] + monitor["width"] // 2 - w // 2
            y = monitor["top"] + int(monitor["height"] * 0.18) - h // 2
        elif mode == "left_bottom":
            x = monitor["left"] + margin_x
            y = monitor["top"] + monitor["height"] - h - margin_y
        elif mode == "right_bottom":
            x = monitor["left"] + monitor["width"] - w - margin_x
            y = monitor["top"] + monitor["height"] - h - margin_y
        elif mode == "custom" and self.custom_rel is not None:
            rx, ry = self.custom_rel
            x = monitor["left"] + int(rx * monitor["width"])
            y = monitor["top"] + int(ry * monitor["height"])
        else:
            x = monitor["left"] + monitor["width"] // 2 - w // 2
            y = monitor["top"] + int(monitor["height"] * 0.70) - h // 2

        return x, y

    def place(self, monitor, preserve_custom=True):
        self.monitor = monitor
        x, y = self._preset_xy(monitor)
        self._apply_geometry(x, y)
        self.redraw()
        self.show()

    def show(self):
        self._visible = True
        self._sync_background_layer()
        if not self.win.winfo_viewable():
            self.win.deiconify()
        self.win.attributes("-topmost", True)
        self.win.lift()
        self.win.update_idletasks()

    def hide(self):
        if self._visible:
            self._visible = False
            self.win.withdraw()
        self.bg_win.withdraw()

    def destroy(self):
        try:
            self.win.destroy()
        except Exception:
            pass
        try:
            self.bg_win.destroy()
        except Exception:
            pass

    def set_slot_colors(self, colors):
        if isinstance(colors, (list, tuple)) and len(colors) == 4:
            cleaned = []
            for c in colors:
                if isinstance(c, str) and len(c) == 7 and c.startswith("#"):
                    cleaned.append(c)
                else:
                    cleaned.append("#FFFFFF")
            self.slot_colors = cleaned
            self.redraw()

    @staticmethod
    def _darken_hex(hex_color, factor=0.38):
        try:
            h = hex_color.lstrip("#")
            r = int(h[0:2], 16)
            g = int(h[2:4], 16)
            b = int(h[4:6], 16)
            r = int(r * factor)
            g = int(g * factor)
            b = int(b * factor)
            return f"#{r:02X}{g:02X}{b:02X}"
        except Exception:
            return "#666666"

    @staticmethod
    def _text_color_for_fill(hex_color):
        try:
            h = hex_color.lstrip("#")
            r = int(h[0:2], 16)
            g = int(h[2:4], 16)
            b = int(h[4:6], 16)
            luminance = 0.2126 * r + 0.7152 * g + 0.0722 * b
            return "#111111" if luminance >= 150 else "#FFFFFF"
        except Exception:
            return "#111111"

    def redraw(self):
        c = self.canvas
        c.delete("all")

        s = self.scale
        gap = self.gap
        start_x = self.W / 2 - 1.5 * gap
        y = self.H / 2

        if self.adjust_mode:
            c.create_rectangle(
                1, 1, self.W - 2, self.H - 2,
                outline="#8A8A8A",
                width=max(1, int(round(1.5 * s))),
            )

        for i in range(4):
            x = start_x + i * gap
            slot_color = self.slot_colors[i]
            dim_color = self._darken_hex(slot_color, 0.38)

            if not self.enabled[i]:
                outline = "#3F3F3F"
                fill = self.TRANSPARENT_KEY if self.transparency_supported else self.bg
                textc = "#5E5E5E"
                width = max(1, int(round(2 * s)))
            elif self.ready[i]:
                outline = slot_color
                fill = slot_color
                textc = self._text_color_for_fill(slot_color)
                width = max(2, int(round(3 * s)))
            else:
                if self.unavailable_mode == "完全隐藏":
                    continue
                outline = dim_color
                fill = self.TRANSPARENT_KEY if self.transparency_supported else self.bg
                textc = dim_color
                width = max(1, int(round(2 * s)))

            if self.flash_enabled and self.flash_slot == i:
                rr = 23 * s
                c.create_oval(
                    x - rr, y - rr, x + rr, y + rr,
                    outline=slot_color,
                    width=max(2, int(round(4 * s))),
                )

            r = 16 * s
            c.create_oval(
                x - r, y - r, x + r, y + r,
                outline=outline,
                width=width,
                fill=fill,
            )

            if self.show_numbers:
                c.create_text(
                    x, y,
                    text=str(i + 1),
                    fill=textc,
                    font=("Segoe UI", max(7, int(round(12 * s))), "bold"),
                )

    def set_enabled(self, vals):
        self.enabled = list(vals)
        self.redraw()

    def set_ready(self, idx, val):
        self.ready[idx] = bool(val)
        self.redraw()

    def flash(self, idx):
        if not self.flash_enabled:
            return
        self.flash_slot = idx
        self.redraw()
        if self._visible:
            self._sync_background_layer()
            self.win.attributes("-topmost", True)
            self.win.lift()
        self.win.after(500, self._clear_flash)

    def _clear_flash(self):
        self.flash_slot = None
        self.redraw()

    def _drag_press(self, event):
        if not self.adjust_mode:
            return
        self._drag_start_pointer = (event.x_root, event.y_root)
        self._drag_start_window = (self.win.winfo_x(), self.win.winfo_y())

    def _drag_move(self, event):
        if not self.adjust_mode or self._drag_start_pointer is None:
            return
        dx = event.x_root - self._drag_start_pointer[0]
        dy = event.y_root - self._drag_start_pointer[1]
        x = self._drag_start_window[0] + dx
        y = self._drag_start_window[1] + dy
        self._apply_geometry(x, y)

    def _drag_release(self, event):
        if not self.adjust_mode or self.monitor is None:
            return
        self._drag_start_pointer = None
        self._drag_start_window = None

        x = self.win.winfo_x()
        y = self.win.winfo_y()
        rx = (x - self.monitor["left"]) / max(1, self.monitor["width"])
        ry = (y - self.monitor["top"]) / max(1, self.monitor["height"])
        self.position_name = "自定义"
        self.custom_rel = (rx, ry)

        if self.on_drag_end is not None:
            self.on_drag_end(self.custom_rel)


# -------------------- System Tray (Win32, no third-party deps) --------------------

WM_APP = 0x8000
WM_TRAYICON = WM_APP + 77
WM_LBUTTONDBLCLK = 0x0203
WM_RBUTTONUP = 0x0205
WM_CLOSE = 0x0010
WM_DESTROY = 0x0002
WM_NULL = 0x0000

NIM_ADD = 0x00000000
NIM_DELETE = 0x00000002
NIF_MESSAGE = 0x00000001
NIF_ICON = 0x00000002
NIF_TIP = 0x00000004

MF_STRING = 0x00000000
MF_SEPARATOR = 0x00000800
TPM_RIGHTBUTTON = 0x0002
TPM_RETURNCMD = 0x0100

IDI_APPLICATION = 32512

TRAY_SHOW_HIDE = 1001
TRAY_PAUSE_RESUME = 1002
TRAY_EXIT = 1003


class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", wintypes.DWORD),
        ("Data2", wintypes.WORD),
        ("Data3", wintypes.WORD),
        ("Data4", ctypes.c_ubyte * 8),
    ]


class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("hWnd", wintypes.HWND),
        ("uID", wintypes.UINT),
        ("uFlags", wintypes.UINT),
        ("uCallbackMessage", wintypes.UINT),
        ("hIcon", wintypes.HICON),
        ("szTip", wintypes.WCHAR * 128),
        ("dwState", wintypes.DWORD),
        ("dwStateMask", wintypes.DWORD),
        ("szInfo", wintypes.WCHAR * 256),
        ("uTimeoutOrVersion", wintypes.UINT),
        ("szInfoTitle", wintypes.WCHAR * 64),
        ("dwInfoFlags", wintypes.DWORD),
        ("guidItem", GUID),
        ("hBalloonIcon", wintypes.HICON),
    ]


LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(
    LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
)


class WNDCLASSW(ctypes.Structure):
    _fields_ = [
        ("style", wintypes.UINT),
        ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE),
        ("hIcon", wintypes.HICON),
        ("hCursor", wintypes.HANDLE),
        ("hbrBackground", wintypes.HBRUSH),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
    ]


class TrayIcon:
    def __init__(self, event_queue):
        self.event_queue = event_queue
        self.thread = None
        self.hwnd = None
        self._nid = None
        self._wndproc = None
        self._running_state = False
        self._window_visible = True
        self._state_lock = threading.Lock()
        self._ready = threading.Event()

    def start(self):
        if self.thread and self.thread.is_alive():
            return
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        self._ready.wait(2.0)

    def update_state(self, running=None, window_visible=None):
        with self._state_lock:
            if running is not None:
                self._running_state = bool(running)
            if window_visible is not None:
                self._window_visible = bool(window_visible)

    def stop(self):
        hwnd = self.hwnd
        if hwnd:
            try:
                user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
            except Exception:
                pass

    def _run(self):
        kernel32 = ctypes.windll.kernel32
        shell32 = ctypes.windll.shell32

        # Explicit prototypes: avoid 64-bit HWND/WPARAM/LPARAM truncation.
        # Without this, ctypes assumes C int for undeclared WinAPI parameters.
        user32.DefWindowProcW.argtypes = [
            wintypes.HWND,
            wintypes.UINT,
            wintypes.WPARAM,
            wintypes.LPARAM,
        ]
        user32.DefWindowProcW.restype = LRESULT

        user32.PostMessageW.argtypes = [
            wintypes.HWND,
            wintypes.UINT,
            wintypes.WPARAM,
            wintypes.LPARAM,
        ]
        user32.PostMessageW.restype = wintypes.BOOL

        user32.DestroyWindow.argtypes = [wintypes.HWND]
        user32.DestroyWindow.restype = wintypes.BOOL

        user32.SetForegroundWindow.argtypes = [wintypes.HWND]
        user32.SetForegroundWindow.restype = wintypes.BOOL

        user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
        user32.GetCursorPos.restype = wintypes.BOOL

        user32.TrackPopupMenu.argtypes = [
            wintypes.HMENU,
            wintypes.UINT,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            wintypes.HWND,
            ctypes.c_void_p,
        ]
        user32.TrackPopupMenu.restype = wintypes.UINT

        # Explicit prototypes: avoid 64-bit HWND/HICON/HMENU truncation.
        kernel32.GetModuleHandleW.restype = wintypes.HMODULE

        user32.LoadIconW.restype = wintypes.HICON
        user32.LoadIconW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR]

        user32.RegisterClassW.restype = wintypes.ATOM
        user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]

        user32.CreateWindowExW.restype = wintypes.HWND
        user32.CreateWindowExW.argtypes = [
            wintypes.DWORD,
            wintypes.LPCWSTR,
            wintypes.LPCWSTR,
            wintypes.DWORD,
            ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
            wintypes.HWND,
            wintypes.HMENU,
            wintypes.HINSTANCE,
            wintypes.LPVOID,
        ]

        user32.CreatePopupMenu.argtypes = []
        user32.CreatePopupMenu.restype = wintypes.HMENU

        user32.AppendMenuW.argtypes = [
            wintypes.HMENU,
            wintypes.UINT,
            ctypes.c_size_t,
            wintypes.LPCWSTR,
        ]
        user32.AppendMenuW.restype = wintypes.BOOL

        user32.DestroyMenu.argtypes = [wintypes.HMENU]
        user32.DestroyMenu.restype = wintypes.BOOL

        shell32.Shell_NotifyIconW.restype = wintypes.BOOL
        shell32.Shell_NotifyIconW.argtypes = [
            wintypes.DWORD,
            ctypes.POINTER(NOTIFYICONDATAW),
        ]

        hinst = kernel32.GetModuleHandleW(None)
        class_name = f"EndfieldCDAlertTray_{os.getpid()}"

        @WNDPROC
        def wndproc(hwnd, msg, wparam, lparam):
            if msg == WM_TRAYICON:
                event_code = int(lparam)
                if event_code == WM_LBUTTONDBLCLK:
                    self.event_queue.put(("tray", "show"))
                    return 0
                if event_code == WM_RBUTTONUP:
                    self._show_menu(hwnd)
                    return 0

            if msg == WM_CLOSE:
                try:
                    if self._nid is not None:
                        shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(self._nid))
                except Exception:
                    pass
                user32.DestroyWindow(hwnd)
                return 0

            if msg == WM_DESTROY:
                user32.PostQuitMessage(0)
                return 0

            return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

        self._wndproc = wndproc

        icon_res = ctypes.cast(ctypes.c_void_p(IDI_APPLICATION), wintypes.LPCWSTR)
        hicon = user32.LoadIconW(None, icon_res)

        wc = WNDCLASSW()
        wc.lpfnWndProc = wndproc
        wc.hInstance = hinst
        wc.hIcon = hicon
        wc.lpszClassName = class_name

        user32.RegisterClassW(ctypes.byref(wc))

        hwnd = user32.CreateWindowExW(
            0,
            class_name,
            class_name,
            0,
            0, 0, 0, 0,
            0, 0,
            hinst,
            None,
        )
        self.hwnd = hwnd

        nid = NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        nid.hWnd = hwnd
        nid.uID = 1
        nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        nid.uCallbackMessage = WM_TRAYICON
        nid.hIcon = hicon
        nid.szTip = "终末地连携 CD HUD v2.0.1"
        self._nid = nid

        shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid))
        self._ready.set()

        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), 0, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

        self.hwnd = None

    def _show_menu(self, hwnd):
        with self._state_lock:
            running = self._running_state
            visible = self._window_visible

        menu = user32.CreatePopupMenu()
        user32.AppendMenuW(
            menu, MF_STRING, TRAY_SHOW_HIDE,
            "隐藏设置窗口" if visible else "显示设置窗口"
        )
        user32.AppendMenuW(
            menu, MF_STRING, TRAY_PAUSE_RESUME,
            "暂停监测" if running else "开始监测"
        )
        user32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
        user32.AppendMenuW(menu, MF_STRING, TRAY_EXIT, "退出")

        pt = wintypes.POINT()
        user32.GetCursorPos(ctypes.byref(pt))
        user32.SetForegroundWindow(hwnd)

        cmd = user32.TrackPopupMenu(
            menu,
            TPM_RIGHTBUTTON | TPM_RETURNCMD,
            pt.x, pt.y,
            0,
            hwnd,
            None,
        )
        user32.DestroyMenu(menu)
        user32.PostMessageW(hwnd, WM_NULL, 0, 0)

        if cmd == TRAY_SHOW_HIDE:
            self.event_queue.put(("tray", "hide" if visible else "show"))
        elif cmd == TRAY_PAUSE_RESUME:
            self.event_queue.put(("tray", "stop" if running else "start"))
        elif cmd == TRAY_EXIT:
            self.event_queue.put(("tray", "exit"))


class App:
    SETTINGS_DIR = os.path.join(
        os.environ.get("APPDATA", os.path.expanduser("~")),
        "EndfieldCDAlert"
    )
    SETTINGS_PATH = os.path.join(SETTINGS_DIR, "settings.json")

    def __init__(self, root):
        self.root = root
        self.root.title("终末地连携 CD HUD v2.0.1")
        self.root.geometry("760x650")
        self.root.minsize(720, 600)

        self.settings = self._load_settings()

        self.monitors = enum_monitors()
        if not self.monitors:
            raise RuntimeError("没有检测到显示器。")

        self.events = queue.Queue()
        self.overlay = Overlay(root, on_drag_end=self._on_hud_drag_end)
        self.tray = TrayIcon(self.events)

        self.stop_event = threading.Event()
        self.worker = None
        self.running = False
        self.states = [SlotState() for _ in range(4)]
        self.window_visible = True
        self.preview_until = 0.0
        self.active_monitor = None

        self._ui()
        self._apply_saved_settings()
        self.tray.start()
        self.tray.update_state(running=False, window_visible=True)

        self.root.after(50, self._poll)

    # ---------------- Settings ----------------

    def _defaults(self):
        return {
            "monitor": None,
            "slots": [True, True, True, True],
            "hud_position": "中央偏下",
            "hud_custom_rel": None,
            "hud_scale": 1.0,
            "hud_spacing": 1.0,
            "marker_opacity": 1.0,
            "background_opacity": 0.62,
            # Keep this legacy key so rolling back to 1.x does not produce
            # a surprising fully opaque window.
            "hud_opacity": 0.62,
            "show_numbers": True,
            "unavailable_mode": "暗色显示",
            "slot_color_names": list(DEFAULT_SLOT_COLOR_NAMES),
            "slot_colors": [
                COLOR_PRESETS["蓝"],
                COLOR_PRESETS["青"],
                COLOR_PRESETS["紫"],
                COLOR_PRESETS["橙"],
            ],
            "sound": False,
            "flash": False,
            "pitch_by_slot": True,
            "silent_first": True,
            "show_debug": False,
        }

    def _load_settings(self):
        data = self._defaults()
        try:
            if os.path.exists(self.SETTINGS_PATH):
                with open(self.SETTINGS_PATH, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                if isinstance(loaded, dict):
                    data.update(loaded)
        except Exception:
            pass
        return data

    def _save_settings(self):
        try:
            os.makedirs(self.SETTINGS_DIR, exist_ok=True)
            data = {
                "monitor": self._monitor_signature(self.monitor()),
                "slots": [bool(v.get()) for v in self.slot_vars],
                "hud_position": self.position_var.get(),
                "hud_custom_rel": (
                    list(self.overlay.custom_rel)
                    if self.overlay.custom_rel is not None
                    else None
                ),
                "hud_scale": float(self.scale_var.get()),
                "hud_spacing": float(self.spacing_var.get()),
                "marker_opacity": float(self.marker_opacity_var.get()),
                "background_opacity": float(self.background_opacity_var.get()),
                "hud_opacity": float(self.background_opacity_var.get()),
                "show_numbers": bool(self.show_numbers_var.get()),
                "unavailable_mode": self.unavailable_mode_var.get(),
                "slot_color_names": list(self.slot_color_names),
                "slot_colors": list(self.slot_colors),
                "sound": bool(self.sound_var.get()),
                "flash": bool(self.flash_var.get()),
                "pitch_by_slot": bool(self.pitch_by_slot_var.get()),
                "silent_first": bool(self.silent_first_var.get()),
                "show_debug": bool(self.show_debug_var.get()),
            }
            with open(self.SETTINGS_PATH, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def _monitor_signature(self, m):
        if not m:
            return None
        return {
            "left": m["left"],
            "top": m["top"],
            "width": m["width"],
            "height": m["height"],
        }

    # ---------------- UI ----------------

    def _ui(self):
        style = ttk.Style()
        try:
            style.configure("Title.TLabel", font=("Microsoft YaHei UI", 16, "bold"))
            style.configure(
                "Section.TLabelframe.Label",
                font=("Microsoft YaHei UI", 10, "bold")
            )
        except Exception:
            pass

        ttk.Label(
            self.root,
            text="终末地连携 CD HUD v2.0.1",
            style="Title.TLabel",
        ).pack(anchor="w", padx=18, pady=(16, 2))

        ttk.Label(
            self.root,
            text="READY / CD 状态识别保持原逻辑；v2.0 主要整理 HUD 与设置界面。",
        ).pack(anchor="w", padx=18, pady=(0, 10))

        footer = ttk.Frame(self.root)
        footer.pack(side="bottom", fill="x", padx=18, pady=(8, 14))

        self.start_btn = ttk.Button(footer, text="开始监测", command=self.start)
        self.start_btn.pack(side="left")

        self.stop_btn = ttk.Button(
            footer, text="暂停", command=self.stop, state="disabled"
        )
        self.stop_btn.pack(side="left", padx=8)

        ttk.Button(
            footer, text="隐藏到托盘", command=self.hide_to_tray
        ).pack(side="left", padx=8)

        ttk.Button(footer, text="退出", command=self.exit_app).pack(side="right")

        status_frame = ttk.Frame(self.root)
        status_frame.pack(side="bottom", fill="x", padx=18, pady=(0, 2))

        self.status = tk.StringVar(value="未开始")
        ttk.Label(
            status_frame, textvariable=self.status, font=("Consolas", 10)
        ).pack(anchor="w")

        self.debug = tk.StringVar(value="")
        self.debug_label = ttk.Label(
            status_frame,
            textvariable=self.debug,
            font=("Consolas", 9),
            wraplength=700,
        )

        notebook = ttk.Notebook(self.root)
        notebook.pack(fill="both", expand=True, padx=18, pady=(0, 8))

        monitor_tab = ttk.Frame(notebook, padding=12)
        hud_tab = ttk.Frame(notebook, padding=12)
        notify_tab = ttk.Frame(notebook, padding=12)
        advanced_tab = ttk.Frame(notebook, padding=12)
        notebook.add(monitor_tab, text="监测")
        notebook.add(hud_tab, text="HUD")
        notebook.add(notify_tab, text="提示")
        notebook.add(advanced_tab, text="高级")

        # -------- Monitor tab --------
        ttk.Label(monitor_tab, text="游戏显示器").grid(
            row=0, column=0, padx=(0, 10), pady=10, sticky="w"
        )
        self.mon_var = tk.StringVar()
        self.mon_box = ttk.Combobox(
            monitor_tab, textvariable=self.mon_var, state="readonly", width=50
        )
        self.mon_box["values"] = [
            f"显示器 {i+1}: {m['width']}×{m['height']} @ ({m['left']},{m['top']})"
            for i, m in enumerate(self.monitors)
        ]
        self.mon_box.grid(row=0, column=1, columnspan=5, pady=10, sticky="ew")
        self.mon_box.bind("<<ComboboxSelected>>", lambda e: self._settings_changed())

        ttk.Label(monitor_tab, text="监测槽位").grid(
            row=1, column=0, padx=(0, 10), pady=10, sticky="w"
        )
        self.slot_vars = []
        self.slot_checks = []
        for i in range(4):
            v = tk.BooleanVar(value=True)
            self.slot_vars.append(v)
            cb = ttk.Checkbutton(
                monitor_tab,
                text=str(i + 1),
                variable=v,
                command=self._settings_changed,
            )
            self.slot_checks.append(cb)
            cb.grid(row=1, column=i + 1, padx=12, pady=10, sticky="w")
        monitor_tab.columnconfigure(1, weight=1)

        ttk.Label(
            monitor_tab,
            text="当前版本按 16:9 HUD 坐标同比缩放；不提供手动识别区域校准。",
        ).grid(row=2, column=0, columnspan=6, pady=(14, 0), sticky="w")

        # -------- HUD tab --------
        ttk.Label(hud_tab, text="位置").grid(row=0, column=0, pady=7, sticky="w")
        self.position_var = tk.StringVar(value="中央偏下")
        self.position_box = ttk.Combobox(
            hud_tab,
            textvariable=self.position_var,
            state="readonly",
            values=list(Overlay.POSITION_NAMES.keys()),
            width=12,
        )
        self.position_box.grid(row=0, column=1, padx=8, pady=7, sticky="w")
        self.position_box.bind("<<ComboboxSelected>>", lambda e: self._position_changed())

        self.adjust_btn = ttk.Button(
            hud_tab, text="调整位置", command=self._toggle_adjust_mode
        )
        self.adjust_btn.grid(row=0, column=2, padx=8, pady=7, sticky="w")
        ttk.Button(hud_tab, text="预览 HUD", command=self.preview_hud).grid(
            row=0, column=3, padx=8, pady=7, sticky="w"
        )

        ttk.Label(hud_tab, text="槽位颜色").grid(row=1, column=0, pady=7, sticky="w")
        self.slot_color_names = list(DEFAULT_SLOT_COLOR_NAMES)
        self.slot_colors = [COLOR_PRESETS[name] for name in self.slot_color_names]
        self.color_vars = []
        self.color_boxes = []
        color_frame = ttk.Frame(hud_tab)
        color_frame.grid(row=1, column=1, columnspan=4, padx=8, pady=7, sticky="w")
        for i in range(4):
            ttk.Label(color_frame, text=f"{i + 1}").pack(
                side="left", padx=(0 if i == 0 else 10, 3)
            )
            var = tk.StringVar(value=self.slot_color_names[i])
            self.color_vars.append(var)
            box = ttk.Combobox(
                color_frame,
                textvariable=var,
                state="readonly",
                values=list(COLOR_PRESETS.keys()),
                width=4,
            )
            box.pack(side="left")
            box.bind("<<ComboboxSelected>>", lambda e, idx=i: self._preset_color_changed(idx))
            self.color_boxes.append(box)
        ttk.Button(
            color_frame, text="恢复默认配色", command=self._reset_slot_colors
        ).pack(side="left", padx=(12, 0))

        ttk.Label(hud_tab, text="标志大小").grid(row=2, column=0, pady=7, sticky="w")
        self.scale_var = tk.DoubleVar(value=1.0)
        self.scale_slider = ttk.Scale(
            hud_tab, from_=0.50, to=2.50, variable=self.scale_var,
            orient="horizontal", length=320, command=self._scale_changed,
        )
        self.scale_slider.grid(row=2, column=1, columnspan=3, padx=8, pady=7, sticky="ew")
        self.scale_text = ttk.Label(hud_tab, text="100%")
        self.scale_text.grid(row=2, column=4, padx=8, pady=7, sticky="w")

        ttk.Label(hud_tab, text="标志间距").grid(row=3, column=0, pady=7, sticky="w")
        self.spacing_var = tk.DoubleVar(value=1.0)
        self.spacing_slider = ttk.Scale(
            hud_tab, from_=0.50, to=2.00, variable=self.spacing_var,
            orient="horizontal", length=320, command=self._spacing_changed,
        )
        self.spacing_slider.grid(row=3, column=1, columnspan=3, padx=8, pady=7, sticky="ew")
        self.spacing_text = ttk.Label(hud_tab, text="100%")
        self.spacing_text.grid(row=3, column=4, padx=8, pady=7, sticky="w")

        ttk.Label(hud_tab, text="标志不透明度").grid(row=4, column=0, pady=7, sticky="w")
        self.marker_opacity_var = tk.DoubleVar(value=1.0)
        self.marker_opacity_slider = ttk.Scale(
            hud_tab, from_=0.20, to=1.00, variable=self.marker_opacity_var,
            orient="horizontal", length=320, command=self._marker_opacity_changed,
        )
        self.marker_opacity_slider.grid(row=4, column=1, columnspan=3, padx=8, pady=7, sticky="ew")
        self.marker_opacity_text = ttk.Label(hud_tab, text="100%")
        self.marker_opacity_text.grid(row=4, column=4, padx=8, pady=7, sticky="w")

        ttk.Label(hud_tab, text="背景不透明度").grid(row=5, column=0, pady=7, sticky="w")
        self.background_opacity_var = tk.DoubleVar(value=0.62)
        self.background_opacity_slider = ttk.Scale(
            hud_tab, from_=0.0, to=1.00, variable=self.background_opacity_var,
            orient="horizontal", length=320, command=self._background_opacity_changed,
        )
        self.background_opacity_slider.grid(row=5, column=1, columnspan=3, padx=8, pady=7, sticky="ew")
        self.background_opacity_text = ttk.Label(hud_tab, text="62%")
        self.background_opacity_text.grid(row=5, column=4, padx=8, pady=7, sticky="w")

        self.show_numbers_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            hud_tab, text="显示 1 / 2 / 3 / 4", variable=self.show_numbers_var,
            command=self._show_numbers_changed,
        ).grid(row=6, column=0, columnspan=2, pady=10, sticky="w")

        ttk.Label(hud_tab, text="不可用状态").grid(row=6, column=2, padx=(14, 4), pady=10, sticky="e")
        self.unavailable_mode_var = tk.StringVar(value="暗色显示")
        self.unavailable_box = ttk.Combobox(
            hud_tab, textvariable=self.unavailable_mode_var, state="readonly",
            values=list(Overlay.UNAVAILABLE_MODES), width=10,
        )
        self.unavailable_box.grid(row=6, column=3, columnspan=2, pady=10, sticky="w")
        self.unavailable_box.bind(
            "<<ComboboxSelected>>", lambda e: self._unavailable_mode_changed()
        )

        hud_tab.columnconfigure(3, weight=1)

        # -------- Notify tab --------
        self.flash_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            notify_tab, text="CD 转好时外圈闪一下", variable=self.flash_var,
            command=self._notify_changed,
        ).grid(row=0, column=0, padx=4, pady=12, sticky="w")

        self.sound_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            notify_tab, text="提示音", variable=self.sound_var,
            command=self._notify_changed,
        ).grid(row=0, column=1, padx=24, pady=12, sticky="w")

        self.pitch_by_slot_var = tk.BooleanVar(value=True)
        self.pitch_check = ttk.Checkbutton(
            notify_tab, text="不同槽位不同音高", variable=self.pitch_by_slot_var,
            command=self._settings_changed,
        )
        self.pitch_check.grid(row=1, column=0, padx=4, pady=12, sticky="w")

        self.silent_first_var = tk.BooleanVar(value=True)
        self.silent_first_check = ttk.Checkbutton(
            notify_tab, text="首次 READY 静默", variable=self.silent_first_var,
            command=self._settings_changed,
        )
        self.silent_first_check.grid(row=1, column=1, padx=24, pady=12, sticky="w")

        ttk.Label(
            notify_tab,
            text="默认不播放提示音、不闪烁；HUD 本身只负责告诉你当前能不能用。",
        ).grid(row=2, column=0, columnspan=3, padx=4, pady=(20, 0), sticky="w")

        # -------- Advanced tab --------
        self.show_debug_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            advanced_tab, text="显示调试信息", variable=self.show_debug_var,
            command=self._toggle_debug,
        ).grid(row=0, column=0, pady=10, sticky="w")

        ttk.Button(
            advanced_tab, text="恢复全部默认设置", command=self._reset_all_settings
        ).grid(row=1, column=0, pady=10, sticky="w")

        support_text = (
            "背景独立透明：可用"
            if self.overlay.transparency_supported
            else "背景独立透明：当前 Tk 环境不支持，已回退到旧 HUD 绘制方式"
        )
        ttk.Label(advanced_tab, text=support_text).grid(
            row=2, column=0, pady=(18, 6), sticky="w"
        )
        ttk.Label(
            advanced_tab,
            text="检测区域、READY/CONSUMED、低血和死亡判定在 v2.0 不提供手动调整。",
        ).grid(row=3, column=0, pady=6, sticky="w")

    def _apply_saved_settings(self):
        s = self.settings

        sig = s.get("monitor")
        chosen = None
        if isinstance(sig, dict):
            for i, m in enumerate(self.monitors):
                if self._monitor_signature(m) == sig:
                    chosen = i
                    break
        if chosen is None:
            chosen = max(
                range(len(self.monitors)),
                key=lambda i: self.monitors[i]["width"] * self.monitors[i]["height"]
            )
        self.mon_box.current(chosen)

        slots = s.get("slots")
        if isinstance(slots, list) and len(slots) == 4:
            for i in range(4):
                self.slot_vars[i].set(bool(slots[i]))

        pos = s.get("hud_position", "中央偏下")
        if pos not in Overlay.POSITION_NAMES:
            pos = "中央偏下"
        self.position_var.set(pos)

        custom = s.get("hud_custom_rel")
        if isinstance(custom, list) and len(custom) == 2:
            self.overlay.custom_rel = (float(custom[0]), float(custom[1]))

        self.scale_var.set(max(0.50, min(2.50, float(s.get("hud_scale", 1.0)))))
        self.spacing_var.set(max(0.50, min(2.00, float(s.get("hud_spacing", 1.0)))))

        # v1.x had one alpha for the whole window.  On first v2 launch keep
        # that value for the black background, while markers default to 100%.
        old_alpha = float(s.get("hud_opacity", 0.62))
        marker_alpha = float(s.get("marker_opacity", 1.0))
        background_alpha = float(s.get("background_opacity", old_alpha))
        self.marker_opacity_var.set(max(0.20, min(1.0, marker_alpha)))
        self.background_opacity_var.set(max(0.0, min(1.0, background_alpha)))

        self.show_numbers_var.set(bool(s.get("show_numbers", True)))
        unavailable = s.get("unavailable_mode", "暗色显示")
        if unavailable not in Overlay.UNAVAILABLE_MODES:
            unavailable = "暗色显示"
        self.unavailable_mode_var.set(unavailable)

        names = s.get("slot_color_names")
        if isinstance(names, list) and len(names) == 4:
            cleaned = []
            for i, name in enumerate(names):
                cleaned.append(
                    name if name in COLOR_PRESETS else DEFAULT_SLOT_COLOR_NAMES[i]
                )
            self.slot_color_names = cleaned
        else:
            old_colors = s.get("slot_colors")
            matched = []
            reverse = {v.upper(): k for k, v in COLOR_PRESETS.items()}
            if isinstance(old_colors, list) and len(old_colors) == 4:
                for i, c in enumerate(old_colors):
                    matched.append(
                        reverse.get(str(c).upper(), DEFAULT_SLOT_COLOR_NAMES[i])
                    )
                self.slot_color_names = matched

        self.slot_colors = [COLOR_PRESETS[name] for name in self.slot_color_names]
        for i, var in enumerate(self.color_vars):
            var.set(self.slot_color_names[i])

        self.sound_var.set(bool(s.get("sound", False)))
        self.flash_var.set(bool(s.get("flash", False)))
        self.pitch_by_slot_var.set(bool(s.get("pitch_by_slot", True)))
        self.silent_first_var.set(bool(s.get("silent_first", True)))
        self.show_debug_var.set(bool(s.get("show_debug", False)))

        self.overlay.set_scale(self.scale_var.get())
        self.overlay.set_spacing(self.spacing_var.get())
        self.overlay.set_marker_opacity(self.marker_opacity_var.get())
        self.overlay.set_background_opacity(self.background_opacity_var.get())
        self.overlay.set_show_numbers(self.show_numbers_var.get())
        self.overlay.set_unavailable_mode(self.unavailable_mode_var.get())
        self.overlay.set_slot_colors(self.slot_colors)
        self.overlay.set_flash_enabled(self.flash_var.get())
        self.overlay.set_position(
            self.position_var.get(), custom_rel=self.overlay.custom_rel
        )

        self.scale_text.config(text=f"{int(self.scale_var.get() * 100)}%")
        self.spacing_text.config(text=f"{int(self.spacing_var.get() * 100)}%")
        self.marker_opacity_text.config(text=f"{int(self.marker_opacity_var.get() * 100)}%")
        self.background_opacity_text.config(text=f"{int(self.background_opacity_var.get() * 100)}%")
        self._toggle_debug()
        self._refresh_notify_controls()

    def _preset_color_changed(self, idx):
        name = self.color_vars[idx].get()
        if name not in COLOR_PRESETS:
            return
        self.slot_color_names[idx] = name
        self.slot_colors[idx] = COLOR_PRESETS[name]
        self.overlay.set_slot_colors(self.slot_colors)
        self._save_settings()

    def _reset_slot_colors(self):
        self.slot_color_names = list(DEFAULT_SLOT_COLOR_NAMES)
        self.slot_colors = [COLOR_PRESETS[name] for name in self.slot_color_names]
        for i, var in enumerate(self.color_vars):
            var.set(self.slot_color_names[i])
        self.overlay.set_slot_colors(self.slot_colors)
        self._save_settings()

    def _settings_changed(self):
        self._save_settings()

    def _position_changed(self):
        pos = self.position_var.get()
        self.overlay.set_position(pos)
        if self.overlay.monitor is not None:
            self.overlay.place(self.overlay.monitor)
        self._save_settings()

    def _scale_changed(self, value=None):
        v = float(self.scale_var.get())
        self.scale_text.config(text=f"{int(v * 100)}%")
        self.overlay.set_scale(v)
        self._save_settings()

    def _spacing_changed(self, value=None):
        v = float(self.spacing_var.get())
        self.spacing_text.config(text=f"{int(v * 100)}%")
        self.overlay.set_spacing(v)
        self._save_settings()

    def _marker_opacity_changed(self, value=None):
        v = float(self.marker_opacity_var.get())
        self.marker_opacity_text.config(text=f"{int(v * 100)}%")
        self.overlay.set_marker_opacity(v)
        self._save_settings()

    def _background_opacity_changed(self, value=None):
        v = float(self.background_opacity_var.get())
        self.background_opacity_text.config(text=f"{int(v * 100)}%")
        self.overlay.set_background_opacity(v)
        self._save_settings()

    def _show_numbers_changed(self):
        self.overlay.set_show_numbers(self.show_numbers_var.get())
        self._save_settings()

    def _unavailable_mode_changed(self):
        self.overlay.set_unavailable_mode(self.unavailable_mode_var.get())
        self._save_settings()

    def _notify_changed(self):
        self.overlay.set_flash_enabled(self.flash_var.get())
        self._refresh_notify_controls()
        self._save_settings()

    def _refresh_notify_controls(self):
        if self.sound_var.get():
            self.pitch_check.state(["!disabled"])
        else:
            self.pitch_check.state(["disabled"])

        if self.sound_var.get() or self.flash_var.get():
            self.silent_first_check.state(["!disabled"])
        else:
            self.silent_first_check.state(["disabled"])

    def _toggle_debug(self):
        if self.show_debug_var.get():
            self.debug_label.pack(anchor="w", pady=(2, 6))
        else:
            self.debug_label.pack_forget()
        self._save_settings()

    def _reset_all_settings(self):
        if self.running:
            messagebox.showinfo("提示", "请先暂停监测，再恢复默认设置。")
            return

        d = self._defaults()
        chosen = max(
            range(len(self.monitors)),
            key=lambda i: self.monitors[i]["width"] * self.monitors[i]["height"]
        )
        self.mon_box.current(chosen)
        for v in self.slot_vars:
            v.set(True)

        self.position_var.set(d["hud_position"])
        self.overlay.custom_rel = None
        self.scale_var.set(d["hud_scale"])
        self.spacing_var.set(d["hud_spacing"])
        self.marker_opacity_var.set(d["marker_opacity"])
        self.background_opacity_var.set(d["background_opacity"])
        self.show_numbers_var.set(d["show_numbers"])
        self.unavailable_mode_var.set(d["unavailable_mode"])

        self.slot_color_names = list(DEFAULT_SLOT_COLOR_NAMES)
        self.slot_colors = [COLOR_PRESETS[name] for name in self.slot_color_names]
        for i, var in enumerate(self.color_vars):
            var.set(self.slot_color_names[i])

        self.sound_var.set(False)
        self.flash_var.set(False)
        self.pitch_by_slot_var.set(True)
        self.silent_first_var.set(True)
        self.show_debug_var.set(False)

        self.overlay.set_position("中央偏下")
        self.overlay.set_scale(1.0)
        self.overlay.set_spacing(1.0)
        self.overlay.set_marker_opacity(1.0)
        self.overlay.set_background_opacity(0.62)
        self.overlay.set_show_numbers(True)
        self.overlay.set_unavailable_mode("暗色显示")
        self.overlay.set_slot_colors(self.slot_colors)
        self.overlay.set_flash_enabled(False)

        self.scale_text.config(text="100%")
        self.spacing_text.config(text="100%")
        self.marker_opacity_text.config(text="100%")
        self.background_opacity_text.config(text="62%")
        self._toggle_debug()
        self._refresh_notify_controls()
        self._save_settings()

    def _toggle_adjust_mode(self):
        new_state = not self.overlay.adjust_mode
        self.overlay.set_adjust_mode(new_state)

        if new_state:
            self.adjust_btn.config(text="锁定位置")
            self.overlay.set_enabled(self.enabled())
            self.overlay.place(self.monitor())
        else:
            self.adjust_btn.config(text="调整位置")
            self._save_settings()
            self._sync_overlay_visibility()

    def _on_hud_drag_end(self, custom_rel):
        self.position_var.set("自定义")
        self._save_settings()

    # ---------------- Tray / window visibility ----------------

    def hide_to_tray(self):
        self._save_settings()
        self.root.withdraw()
        self.window_visible = False
        self.tray.update_state(window_visible=False)

    def show_window(self):
        self.root.deiconify()
        self.root.lift()
        self.root.attributes("-topmost", True)
        self.root.after(100, lambda: self.root.attributes("-topmost", False))
        self.window_visible = True
        self.tray.update_state(window_visible=True)

    # ---------------- Helpers ----------------

    def monitor(self):
        idx = self.mon_box.current()
        return self.monitors[idx if idx >= 0 else 0]

    def enabled(self):
        return [bool(v.get()) for v in self.slot_vars]

    def _set_detection_controls_enabled(self, enabled):
        state = "normal" if enabled else "disabled"
        self.mon_box.config(state="readonly" if enabled else "disabled")
        for cb in self.slot_checks:
            cb.config(state=state)

    def _monitoring_status_text(self):
        mon = self.active_monitor or self.monitor()
        return f"监测中 | {mon['width']}×{mon['height']} | {CAPTURE_HZ:.0f} Hz"

    def _sync_overlay_visibility(self):
        # Manual adjustment / preview are the only cases where HUD is allowed
        # over the settings window or another application.
        if self.overlay.adjust_mode or time.monotonic() < self.preview_until:
            self.overlay.show()
            return

        if not self.running:
            return

        if is_endfield_foreground():
            self.overlay.show()
            self.status.set(self._monitoring_status_text())
        else:
            self.overlay.hide()
            self.status.set("等待终末地前台")

    # ---------------- Run control ----------------

    def start(self):
        if self.running:
            return

        mon = self.monitor()
        aspect = mon["width"] / max(1, mon["height"])
        if abs(aspect - 16 / 9) > 0.03:
            if not messagebox.askyesno(
                "比例提醒",
                f"当前显示器是 {mon['width']}×{mon['height']}，不是标准 16:9。\n"
                "HUD 坐标可能偏移，仍继续？",
            ):
                return

        enabled = self.enabled()
        if not any(enabled):
            messagebox.showinfo("提示", "至少勾一个槽位。")
            return

        self._save_settings()

        self.states = [SlotState() for _ in range(4)]

        # 每次重新开始监测都先清空上一次的 HUD 视觉状态。
        # 否则暂停/重新开始时，旧的 READY 白圆可能残留到新一轮监测。
        for i in range(4):
            self.overlay.set_ready(i, False)
        self.overlay.flash_slot = None

        self.stop_event.clear()
        self.running = True

        self.overlay.set_enabled(enabled)
        self.overlay.set_slot_colors(self.slot_colors)
        self.overlay.set_flash_enabled(self.flash_var.get())
        self.overlay.set_scale(self.scale_var.get())
        self.overlay.set_spacing(self.spacing_var.get())
        self.overlay.set_marker_opacity(self.marker_opacity_var.get())
        self.overlay.set_background_opacity(self.background_opacity_var.get())
        self.overlay.set_show_numbers(self.show_numbers_var.get())
        self.overlay.set_unavailable_mode(self.unavailable_mode_var.get())
        self.overlay.set_position(
            self.position_var.get(),
            custom_rel=self.overlay.custom_rel
        )
        self.overlay.place(mon)
        self.active_monitor = mon

        self.start_btn.config(state="disabled")
        self.stop_btn.config(state="normal")
        self._set_detection_controls_enabled(False)

        self.status.set(self._monitoring_status_text())
        self._sync_overlay_visibility()

        silent_first = bool(self.silent_first_var.get())

        self.worker = threading.Thread(
            target=self._capture_loop,
            args=(mon, enabled, silent_first),
            daemon=True,
        )
        self.worker.start()

        self.tray.update_state(running=True)

    def stop(self):
        if not self.running:
            return
        self.stop_event.set()
        self.running = False
        self.overlay.hide()

        self.start_btn.config(state="normal")
        self.stop_btn.config(state="disabled")
        self._set_detection_controls_enabled(True)
        self.status.set("已暂停")
        self.active_monitor = None

        self.tray.update_state(running=False)

    def preview_hud(self):
        enabled = self.enabled()

        self.overlay.set_enabled(enabled)
        self.overlay.set_slot_colors(self.slot_colors)
        self.overlay.set_flash_enabled(self.flash_var.get())
        self.overlay.set_scale(self.scale_var.get())
        self.overlay.set_spacing(self.spacing_var.get())
        self.overlay.set_marker_opacity(self.marker_opacity_var.get())
        self.overlay.set_background_opacity(self.background_opacity_var.get())
        self.overlay.set_show_numbers(self.show_numbers_var.get())
        self.overlay.set_unavailable_mode(self.unavailable_mode_var.get())
        self.overlay.set_position(
            self.position_var.get(),
            custom_rel=self.overlay.custom_rel
        )
        self.overlay.place(self.monitor())
        self.preview_until = time.monotonic() + 1.8

        if self.running:
            # Running preview only forces visibility briefly; it never changes
            # READY states.
            return

        # 未运行时用交替状态做纯 UI 预览。
        for i in range(4):
            self.overlay.set_ready(i, bool(enabled[i] and i % 2 == 1))

        if self.flash_var.get():
            slot = next((i for i, x in enumerate(enabled) if x), 0)
            self.overlay.flash(slot)

        if self.sound_var.get():
            slot = next((i for i, x in enumerate(enabled) if x), 0)
            self._play_sound(slot)

        def reset():
            if not self.running and not self.overlay.adjust_mode:
                for i in range(4):
                    self.overlay.set_ready(i, False)
                self.overlay.hide()
                self.preview_until = 0.0
        self.root.after(1800, reset)

    def _play_sound(self, slot):
        if not self.sound_var.get():
            return
        try:
            import winsound
            if self.pitch_by_slot_var.get():
                freqs = [660, 780, 900, 1020]
                freq = freqs[slot]
            else:
                freq = 880
            threading.Thread(
                target=lambda: winsound.Beep(freq, 85),
                daemon=True,
            ).start()
        except Exception:
            pass

    def _capture_loop(self, mon, enabled, silent_first):
        cap = None
        try:
            cap = GDICapture()
            rois = [scaled_roi(r, mon) for r in SLOT_ENERGY_ROIS]
            hp_rois = [scaled_roi(r, mon) for r in SLOT_HP_ROIS]
            period = 1.0 / CAPTURE_HZ

            while not self.stop_event.is_set():
                t0 = time.perf_counter()

                # GDI captures the visible desktop. When another app covers the
                # game, sampling those pixels would corrupt READY/death state.
                # Preserve the last known state and resume when Endfield returns.
                if not is_endfield_foreground():
                    self.stop_event.wait(period)
                    continue

                for i in range(4):
                    if not enabled[i]:
                        continue

                    rr = rois[i]
                    raw = cap.grab_bgra(
                        rr["left"], rr["top"], rr["width"], rr["height"]
                    )
                    white_ratio, bright_ratio, mean, luma_ratio = analyze_bgra(raw)

                    hp_rr = hp_rois[i]
                    hp_raw = cap.grab_bgra(
                        hp_rr["left"], hp_rr["top"],
                        hp_rr["width"], hp_rr["height"]
                    )
                    alive_color_ratio = analyze_alive_bar(hp_raw)

                    # 两条 READY 判定通道：
                    # A. 正常状态：整条接近中性亮白。
                    neutral_ready = (
                        white_ratio >= READY_WHITE_RATIO
                        and bright_ratio >= READY_BRIGHT_RATIO
                        and mean >= READY_MEAN_MIN
                    )

                    # B. 染色状态：例如低血量红色 HUD。
                    # 不要求 RGB 接近中性白，只要求几乎整条仍保持高亮。
                    tinted_ready = (
                        luma_ratio >= TINTED_READY_LUMA_RATIO
                        and mean >= TINTED_READY_MEAN_MIN
                    )

                    raw_ready = neutral_ready or tinted_ready

                    # 真正的“技能已被消耗/能量条已清空”状态。
                    # 关键区别：
                    # - 换人 CD：整个 HUD 变暗，但满白条仍保留较多亮像素。
                    # - 连携被使用：白条清空，white / bright 会跌到很低。
                    raw_consumed = (
                        white_ratio <= CONSUMED_WHITE_RATIO_MAX
                        and bright_ratio <= CONSUMED_BRIGHT_RATIO_MAX
                    )

                    st = self.states[i]
                    st.white_ratio = white_ratio
                    st.bright_ratio = bright_ratio
                    st.mean = mean
                    st.luma_ratio = luma_ratio
                    st.alive_color_ratio = alive_color_ratio

                    raw_dead = alive_color_ratio < ALIVE_COLOR_RATIO_MIN

                    if raw_dead:
                        st.dead_streak += 1
                    else:
                        st.dead_streak = 0

                    # 死亡优先级最高。
                    # 连续多帧血条完全失去彩色填充后，强制清掉 READY。
                    if st.dead_streak >= DEAD_CONFIRM_FRAMES:
                        if not st.is_dead:
                            st.is_dead = True
                            if st.confirmed_ready:
                                self.events.put(("state", i, False))

                        st.confirmed_ready = False
                        st.ready_streak = 0
                        st.not_ready_streak = 0
                        st.consumed_streak = 0
                        st.armed = False
                        st.consumed_seen = False
                        st.seen_first_ready = False
                        continue

                    # 从死亡状态恢复后，按当前画面重新同步，不继承旧状态。
                    if st.is_dead and not raw_dead:
                        st.is_dead = False
                        st.ready_streak = 0
                        st.not_ready_streak = 0
                        st.consumed_streak = 0
                        st.confirmed_ready = False
                        st.armed = False
                        st.consumed_seen = False
                        st.seen_first_ready = False

                    if raw_ready:
                        st.ready_streak += 1
                        st.not_ready_streak = 0
                        st.consumed_streak = 0
                    else:
                        st.not_ready_streak += 1
                        st.ready_streak = 0

                        if raw_consumed:
                            st.consumed_streak += 1
                        else:
                            st.consumed_streak = 0

                    # v1.3：READY 是“锁存状态”。
                    # 一旦确认 READY，普通的 UI 变灰/透明度变化/瞬时亮度下降
                    # 都不会把常驻圆圈熄灭。
                    #
                    # 只有真正检测到白条被清空（CONSUMED）连续若干帧，
                    # 才认为技能真的被使用，并把逻辑 READY 清掉。
                    if (
                        st.confirmed_ready
                        and st.consumed_streak >= CONSUME_CONFIRM_FRAMES
                    ):
                        st.confirmed_ready = False
                        if st.seen_first_ready:
                            st.armed = True
                        st.consumed_seen = True
                        self.events.put(("state", i, False))

                    # 从非 READY 回到 READY。
                    # 只有上一轮真的出现过 CONSUMED（armed=True）才播放提示。
                    if (
                        not st.confirmed_ready
                        and st.ready_streak >= CONFIRM_FRAMES
                    ):
                        st.confirmed_ready = True
                        should_alert = False

                        if not st.seen_first_ready:
                            st.seen_first_ready = True
                            if not silent_first:
                                should_alert = True
                        elif st.armed:
                            should_alert = True

                        st.armed = False
                        st.consumed_seen = False
                        self.events.put(("state", i, True))
                        if should_alert:
                            self.events.put(("alert", i))

                debug_snapshot = []
                for i in range(4):
                    if enabled[i]:
                        st = self.states[i]
                        debug_snapshot.append(
                            (
                                i,
                                st.white_ratio,
                                st.bright_ratio,
                                st.mean,
                                st.luma_ratio,
                                st.alive_color_ratio,
                                st.is_dead,
                                st.confirmed_ready,
                            )
                        )
                self.events.put(("debug", debug_snapshot))

                elapsed = time.perf_counter() - t0
                if elapsed < period:
                    self.stop_event.wait(period - elapsed)

        except Exception:
            self.events.put(("error", traceback.format_exc()))
        finally:
            if cap is not None:
                cap.close()


    def _poll(self):
        try:
            while True:
                ev = self.events.get_nowait()
                kind = ev[0]

                if kind == "state":
                    _, idx, ready = ev
                    self.overlay.set_ready(idx, ready)

                elif kind == "alert":
                    _, idx = ev
                    # UI-only choice: state has already changed to READY.
                    if self.flash_var.get():
                        self.overlay.flash(idx)
                    if self.sound_var.get():
                        self._play_sound(idx)

                elif kind == "debug":
                    _, rows = ev
                    parts = []
                    for idx, w, b, m, l, a, dead, ready in rows:
                        st = self.states[idx]
                        raw_now = (
                            (
                                w >= READY_WHITE_RATIO
                                and b >= READY_BRIGHT_RATIO
                                and m >= READY_MEAN_MIN
                            )
                            or
                            (
                                l >= TINTED_READY_LUMA_RATIO
                                and m >= TINTED_READY_MEAN_MIN
                            )
                        )
                        if dead:
                            phase = "DEAD"
                        elif ready and raw_now:
                            phase = "READY"
                        elif ready and not raw_now:
                            phase = "LATCH"
                        elif st.armed:
                            phase = "ARMED"
                        else:
                            phase = "----"
                        parts.append(
                            f"S{idx+1} {phase:5} "
                            f"W={w:4.0%} B={b:4.0%} L={l:4.0%} "
                            f"A={a:4.1%} M={m:5.0f}"
                        )
                    self.debug.set(" | ".join(parts) if parts else "无监测槽位")

                elif kind == "error":
                    _, detail = ev
                    self.stop()
                    self.show_window()
                    messagebox.showerror("监测失败", detail)

                elif kind == "tray":
                    _, action = ev
                    if action == "show":
                        self.show_window()
                    elif action == "hide":
                        self.hide_to_tray()
                    elif action == "start":
                        self.start()
                    elif action == "stop":
                        self.stop()
                    elif action == "exit":
                        self.exit_app()

        except queue.Empty:
            pass

        self._sync_overlay_visibility()
        self.root.after(50, self._poll)

    def exit_app(self):
        self._save_settings()
        self.stop_event.set()
        self.running = False

        try:
            self.tray.stop()
        except Exception:
            pass

        try:
            self.overlay.destroy()
        except Exception:
            pass

        self.root.destroy()


def main():
    if not acquire_single_instance():
        # 第二次启动只给一个明确提示，不创建 HUD / 托盘 / 检测线程。
        temp = tk.Tk()
        temp.withdraw()
        try:
            messagebox.showinfo(
                "终末地连携 CD HUD",
                "插件已经在运行。\n\n"
                "请查看系统托盘中的现有实例。"
            )
        finally:
            temp.destroy()
        return

    root = tk.Tk()
    app = App(root)
    root.protocol("WM_DELETE_WINDOW", app.exit_app)
    root.mainloop()


if __name__ == "__main__":
    main()
