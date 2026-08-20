# -*- coding: utf-8 -*-
"""
终末地连携 CD HUD v2.3

Windows / 纯外部屏幕识别 / 无第三方依赖
- 不读游戏内存
- 不注入游戏进程
- Win32 GDI 截取左下 HUD 小区域
- 1~4 槽独立检测
- READY 瞬间：数字圆圈闪烁 + 提示音
- READY 持续：小型状态 HUD 常亮
- v2.1-alpha5：实验性读取白条横向进度并映射到圆形 HUD
- v2.1-alpha5：首次完整 CD 学习后显示预计剩余秒数
- v2.1-alpha5：死亡需同时满足低/无 HP + 固定死亡头像图标
- v1.1：换人 CD 暗化不再重新触发 READY 提示
"""

import sys
import os
import json
import time
import queue
import threading
import traceback
import math
import struct
import base64
import zlib
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

WNDENUMPROC_T = ctypes.WINFUNCTYPE(
    wintypes.BOOL, wintypes.HWND, wintypes.LPARAM
)
user32.EnumWindows.argtypes = [WNDENUMPROC_T, wintypes.LPARAM]
user32.EnumWindows.restype = wintypes.BOOL
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.IsWindowVisible.restype = wintypes.BOOL
user32.IsIconic.argtypes = [wintypes.HWND]
user32.IsIconic.restype = wintypes.BOOL
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.SetForegroundWindow.restype = wintypes.BOOL


def window_identity(hwnd):
    """Return (title, exe_path) of an arbitrary window handle."""
    if not hwnd:
        return "", ""

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

    return title, exe_path


def foreground_window_identity():
    """Return (hwnd, title, exe_path) of the current foreground window."""
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return 0, "", ""
    title, exe_path = window_identity(hwnd)
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


def find_endfield_window():
    """返回当前可见且未最小化的 Endfield.exe 主窗口句柄（不要求前台）。

    用于“双击悬浮窗抓取头像”场景：点击悬浮窗会把它变成前台窗口，
    游戏虽在下面但已不是前台；此时仍能通过本函数找到游戏窗口，
    再由调用方 SetForegroundWindow 把游戏拉回前台再抓取。
    """
    found = []

    def cb(hwnd, lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        if user32.IsIconic(hwnd):
            return True
        title, exe_path = window_identity(hwnd)
        exe_name = os.path.basename(exe_path).casefold().strip()
        if exe_name == GAME_EXE_NAME:
            found.append(hwnd)
            return False
        if not exe_name:
            title_name = title.casefold().strip()
            if title_name in GAME_EXACT_TITLES:
                found.append(hwnd)
                return False
        return True

    user32.EnumWindows(WNDENUMPROC_T(cb), 0)
    return found[0] if found else 0

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

# v2.1-alpha5：死亡头像中心小区域。
# 只看角色死亡后固定出现的“斜杠圆圈”图标核心，不识别具体角色头像。
# 参考坐标仍以 2048x1152 为基准；运行时按当前屏幕同比缩放。
SLOT_DEATH_ICON_ROIS = [
    (77, 997, 103, 1023),
    (201, 997, 227, 1023),
    (326, 997, 352, 1023),
    (451, 997, 477, 1023),
]

# v2.3 Controller Beta：手柄战斗 HUD 将四个槽位排成十字。
# 参考顺序与键鼠保持一致：1 左、2 上、3 右、4 下。
# 以下是未按 LB（内收）时的 2048x1152 参考坐标；按住 LB 后四槽会沿
# 各自径向最多移动约 8.5 个参考像素（4K 实测约 16px）。中央的 LB /
# 白色方向键图标分别表示内收/外展端点；动画中间帧不采样四槽。
CONTROLLER_SLOT_ENERGY_ROIS = [
    (117, 1017, 205, 1019),
    (203, 923, 292, 925),
    (289, 1017, 378, 1019),
    (203, 1103, 292, 1105),
]
CONTROLLER_SLOT_HP_ROIS = [
    (117, 1022, 205, 1029),
    (203, 928, 292, 935),
    (289, 1022, 378, 1029),
    (203, 1108, 292, 1115),
]
CONTROLLER_SLOT_DEATH_ICON_ROIS = [
    (147, 961, 173, 987),
    (233, 867, 259, 893),
    (321, 961, 347, 987),
    (233, 1046, 259, 1072),
]
CONTROLLER_SLOT_DIRECTIONS = [
    (-1, 0),
    (0, -1),
    (1, 0),
    (0, 1),
]
CONTROLLER_EXPAND_REF = 8.5
CONTROLLER_INPUT_GLYPH_ROI = (211, 963, 286, 1027)
CONTROLLER_GLYPH_SAMPLE_W = 80
CONTROLLER_GLYPH_SAMPLE_H = 68
CONTROLLER_GLYPH_WHITE_MIN = 145
CONTROLLER_GLYPH_CHROMA_MAX = 70
CONTROLLER_GLYPH_SCORE_MIN = 0.58
CONTROLLER_GLYPH_MARGIN_MIN = 0.10
CONTROLLER_GLYPH_SETTLE_FRAMES = 2
CONTROLLER_LB_TEMPLATE_B64 = (
    "eNrt0UEKACAMxMD+/9MrgoJ3U1DInDwF2VZJkn6THM8J6yVE8OytKtoruEf+j96PCHbvdzvg+719BOoebE+SJElSrwHHSXeJ"
)
CONTROLLER_DPAD_TEMPLATE_B64 = (
    "eNrtlsEOwCAIQ/n/n+7iaaJsWCXZYe1NzN4UtGL2LABTxA7UvkanFqrmQTzxfswDPGHi+TGH84Todws7HIdusZzbxP70nLwEOGaoD71MM/W0njcmkOV5B53rwfOQVJ/m3YuyaI7nuXr8gFecv+r63gcwWOH2eQ6v2xqv8v5m/mJIG6aM11UhmF8wwHd/BnDk+Mfvh95z8cT7kBcZzj4PnN+x7eUpzopxNvdvkiRJktR0AUSXUec="
)
CONTROLLER_LB_TEMPLATE = zlib.decompress(
    base64.b64decode(CONTROLLER_LB_TEMPLATE_B64)
)
CONTROLLER_DPAD_TEMPLATE = zlib.decompress(
    base64.b64decode(CONTROLLER_DPAD_TEMPLATE_B64)
)

# v2.4：角色头像抓取。头像中心与死亡图标 ROI 中心一致（死亡图标叠加在
# 头像正中，经全屏样张彩色像素边界框验证偏移≈0）。抓取方形区域后按
# 圆形裁剪（圆外透明），用于替换纯色实心圆。参考坐标 2048x1152。
AVATAR_REF_ROIS = [
    (42, 962, 138, 1058),
    (166, 962, 262, 1058),
    (291, 962, 387, 1058),
    (416, 962, 512, 1058),
]

# 手柄十字布局头像基准（未按 LB 的内收端点，中心与手柄死亡图标中心一致）。
CONTROLLER_AVATAR_ROIS = [
    (112, 926, 208, 1022),
    (198, 832, 294, 928),
    (286, 926, 382, 1022),
    (198, 1011, 294, 1107),
]

# 圆形裁剪半径 = min(w,h)/2 * 该比例（留 5% 边距，避免切掉头像边缘）。
AVATAR_CROP_RATIO = 0.95

# 头像抓取默认位置（参考系 2048x1152）。头像在非战斗主界面/编队界面
# 显示，与连携 HUD 检测界面无关。默认值来自用户提供的 1920x1080 界面
# 截图（黑色实心圆标注）：4 个头像在界面左上角横排，y≈177，
# x≈117/258/400/541，半径 50（用户要求由 58 调小）。
AVATAR_ALIGN_DEFAULT = [
    (117, 177, 50),
    (258, 177, 50),
    (400, 177, 50),
    (541, 177, 50),
]

INPUT_LAYOUT_KEYBOARD = "键盘鼠标（v2.3）"
INPUT_LAYOUT_CONTROLLER = "手柄（v2.3 Beta）"
INPUT_LAYOUTS = (INPUT_LAYOUT_KEYBOARD, INPUT_LAYOUT_CONTROLLER)
LEGACY_KEYBOARD_LAYOUTS = (
    "键盘鼠标（v2.2）",
)
LEGACY_CONTROLLER_LAYOUTS = (
    "手柄（v2.3.1 Beta）",
    "手柄（v2.3.2 Beta）",
)

# v2.1：主控角色 HP 上方的三段技力条。
# 这里只寻找三个固定长矩形的水平边缘，用作插件 HUD 的显示门控。
# 它不参与四槽 READY / CONSUMED / death 判定。
CENTER_SKILL_BAR_ROIS = [
    (851, 1033, 961, 1055),
    (970, 1033, 1079, 1055),
    (1086, 1033, 1196, 1055),
]
CENTER_SKILL_BAR_GAP_ROIS = [
    (961, 1033, 970, 1055),
    (1079, 1033, 1086, 1055),
]

CAPTURE_HZ = 10.0
CONFIRM_FRAMES = 3
CONSUME_CONFIRM_FRAMES = 3

# v2.1-alpha5 死亡判定：
# 旧版仅看血条彩色像素，极低血量时会与真正死亡混淆。
# alpha5 保留血条作为辅助/恢复信号，同时新增固定“死亡头像图标”匹配。
DEAD_CONFIRM_FRAMES = 5
ALIVE_COLOR_CHROMA_MIN = 45
ALIVE_COLOR_VALUE_MIN = 105
ALIVE_COLOR_RATIO_MIN = 0.008

DEATH_ICON_TEMPLATE_SIZE = 32
DEATH_ICON_SCORE_MIN = 0.70
DEATH_ICON_TEMPLATE_B64 = (
    "eNoV0/tO21YAgPHn2KQBiRzHln1s69jHR7bPsXxVAkSUdRqsRaUU9RZxGSqDCtoixEC0UIVAtUJQkxIlOFGcm8JN06Q929gTfPr++CFBDTEDMoSXDU9Oma7G6KN6AgQOLxAKhzjNtRKsqUui6kJIc0jAtspjaosypohRQQ4KAKtAs1XAahTLZJSMKK6mcL7JizahHi+4jiBQV9PHXz6byW9vPnv66ldZQj4cthwjM8SFPgBOmJ3avTg9qcSN0+PL8tZifgL8ZHqYf2oG7lDSm1s9rjSb9fag2+31u62oWtx7HNoaaxaXMoCbeFP81un3OvFNt94d9Dtxb9BqlD++hKZ1UZr3pj5XLrtXrUa7G9frjajR7rTiXn8Q7+Qnyf7RwaeTWnR51SlXLncWlt+t5xe2v5yV261mtdfYoPZm9Hc9urutlYsfZrBhqwxv58anN4q1avRv72SdvI1uovgmOlzNZ0YEG0scyaiyMzm3fnpzN+hX5gq1KG63D+ezJDlMfAhUS095o1IqXCu3B3dXW98uol6vufXA4MSxUGBw5v7bQUDxck/22zeDUnPQrx9F8R+6iHWJURxNBJ6eEj3CaMufyp367T8X27/vlY5fB0Y6gQOQggSxkGoA+cF8IY6vu/uz2fHF8/PXFodCNCIhkxORKafNLG/vdOLuxftZwrr71cMnWY9PkUBheU/msIm4H/nnpbh1+EwXYPBo7bS0iEXdoQkri0Tg+EqSZki+Fu3mknzgis52/WgmF0LGIFZCDGlKMqmVeBFXlyzOcgTGeLj619e1gBUdJ8VaHp/AHhzxHt0WcnLo8CzJQHOjVZgmFhlCjp5I2I6cMIKx+LPtZ1jJD3VgTL0rfFwOOZ7oHKd5Ji+5FtcqTjoiZ4Rq0hxD4Xr56DfDplDApgahrSlqo5hReYpEiCgF2sPNwu7KlMphCtOyaQqItgoi6zsS503IP6jEGF2rnc/r2FAYySZp2ae9AyT5eloL/keCRP2X92eF1VAVYWDwkChMc2+M4Pu+Lsi6zgiO66/Uvs/qKDSA6ljAqJ7mqYB8zELfYO6BQG78z/P9VzkqQh0p6fEoXhMF32BllwBgm2kRO6MLpe9vVOBZaQbPXVfeZg1N1FwzDdysmkQZM+EdNL7M/UwVxfUeNztf50xG9CgHXFsR9EAfRsGDpbPGKpY8w1tpXMcfXIg1UXJ8kMaBxSDD5bzN1snz0Bamz/4DAB1Xqg=="
)
DEATH_ICON_TEMPLATE = zlib.decompress(
    base64.b64decode(DEATH_ICON_TEMPLATE_B64)
)

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

# ---------- v2.1-alpha5: progress reader (display-only) ----------
# 这组参数只用于把游戏白条横向填充位置转换成 0~100% 的显示进度。
# 它们不会参与 READY / CONSUMED / 死亡判定。
PROGRESS_EDGE_DROP_MIN = 16.0
PROGRESS_EDGE_WINDOW_RATIO = 0.030
PROGRESS_MAX_BEFORE_READY = 0.985
PROGRESS_HISTORY_SIZE = 3

# Countdown learning is display-only. Nearby complete cycles use a short
# rolling median to reduce timing jitter. A cycle at least 2x shorter than the
# normal baseline is treated as a temporary acceleration and can never lower
# that baseline; one clearly longer complete cycle may conservatively restore
# a baseline that was first learned while accelerated.
CD_LEARN_MIN_SECONDS = 0.5
CD_LEARN_MAX_SECONDS = 120.0
CD_LEARN_HISTORY_SIZE = 5
CD_RELEARN_RATIO = 2.0
CD_FAST_HISTORY_SIZE = 5
CD_OBSERVATION_HISTORY_SIZE = 20
CD_LEARN_CENTER_ABSENT_FRAMES = 6
CD_LEARN_READY_CONTEXT_FRAMES = CONFIRM_FRAMES
CD_LEARN_READY_CONTEXT_SECONDS = 1.0

# v2.1：中央三段技力条只控制插件 HUD 是否可见。
# 4K 录像离线结果：中央条单独持续存在不能证明处于战斗；
# 首次开战必须同时看到中央技力条和至少三个 READY 连携条。
# 不允许仅靠中央技力条持续时间兜底。战斗确认后，可见性快速跟随技力条；
# 内部战斗会话多保留数秒，让大招遮挡后的技力条可以立即唤回 HUD。
CENTER_BAR_EDGE_DIFF_MIN = 24.0
CENTER_BAR_SEGMENT_SCORE_MIN = 0.50
CENTER_BAR_MIN_SEGMENTS = 2
CENTER_BAR_GAP_EDGE_MAX = 0.30
CENTER_BAR_MIN_EDGE_SEPARATION_ROWS = 3
CENTER_BAR_REQUIRED_READY_SLOTS = 3
CENTER_BAR_COMBAT_CONFIRM_FRAMES = 3
CENTER_BAR_CONSUMPTION_WAKE_SECONDS = 1.0
CENTER_BAR_VISIBILITY_HIDE_FRAMES = 6
CENTER_BAR_SESSION_GRACE_FRAMES = 80
CENTER_BAR_SAMPLE_W = 96
CENTER_BAR_SAMPLE_H = 32

# Overlay 在两个 10Hz 采样点之间做很短的线性插值，仅改善视觉平滑度。
# 目标值仍然来自真实白条，不按时间自行向前预测。
PROGRESS_ANIM_SECONDS = 0.090
OVERLAY_ANIM_MS = 33
READY_PULSE_SECONDS = 0.62
READY_PULSE_EXPAND = 0.28
READY_PULSE_LIGHTEN = 0.96

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


def shifted_roi(roi, dx, dy):
    return {
        "left": roi["left"] + int(dx),
        "top": roi["top"] + int(dy),
        "width": roi["width"],
        "height": roi["height"],
    }


def controller_shift_pixels(monitor, slot, expand):
    vx, vy = CONTROLLER_SLOT_DIRECTIONS[slot]
    dx = round(vx * CONTROLLER_EXPAND_REF * expand * monitor["width"] / REF_W)
    dy = round(vy * CONTROLLER_EXPAND_REF * expand * monitor["height"] / REF_H)
    return dx, dy


def controller_shifted_rois(base_rois, monitor, expand):
    result = []
    for i, roi in enumerate(base_rois):
        dx, dy = controller_shift_pixels(monitor, i, expand)
        result.append(shifted_roi(roi, dx, dy))
    return result


def analyze_controller_input_glyph(raw, width, height):
    """识别手柄 HUD 中央的 LB（内收）/方向键（外展）端点。"""
    expected = width * height * 4
    if width <= 0 or height <= 0 or len(raw) < expected:
        return None, 0.0, 0.0

    mv = memoryview(raw)
    mask = bytearray(CONTROLLER_GLYPH_SAMPLE_W * CONTROLLER_GLYPH_SAMPLE_H)
    for ty in range(CONTROLLER_GLYPH_SAMPLE_H):
        sy = min(
            height - 1,
            int((ty + 0.5) * height / CONTROLLER_GLYPH_SAMPLE_H),
        )
        for tx in range(CONTROLLER_GLYPH_SAMPLE_W):
            sx = min(
                width - 1,
                int((tx + 0.5) * width / CONTROLLER_GLYPH_SAMPLE_W),
            )
            p = (sy * width + sx) * 4
            b, g, r = mv[p], mv[p + 1], mv[p + 2]
            mn = min(r, g, b)
            mx = max(r, g, b)
            if (
                mn >= CONTROLLER_GLYPH_WHITE_MIN
                and mx - mn <= CONTROLLER_GLYPH_CHROMA_MAX
            ):
                mask[ty * CONTROLLER_GLYPH_SAMPLE_W + tx] = 255

    observed_count = sum(1 for value in mask if value)
    if not observed_count:
        return None, 0.0, 0.0

    def template_score(template):
        template_count = sum(1 for value in template if value)
        intersection = sum(
            1 for observed, expected_value in zip(mask, template)
            if observed and expected_value
        )
        return intersection / math.sqrt(observed_count * template_count)

    lb_score = template_score(CONTROLLER_LB_TEMPLATE)
    dpad_score = template_score(CONTROLLER_DPAD_TEMPLATE)
    best_score = max(lb_score, dpad_score)
    if (
        best_score < CONTROLLER_GLYPH_SCORE_MIN
        or abs(lb_score - dpad_score) < CONTROLLER_GLYPH_MARGIN_MIN
    ):
        return None, lb_score, dpad_score
    return (0.0 if lb_score > dpad_score else 1.0), lb_score, dpad_score


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




def analyze_center_bar_edge_rows(raw, width, height):
    """
    返回归一化 ROI 中每条相邻行边界的横向覆盖率。

    原理是提取每段技力条上下边缘形成的长水平结构：
    1. 将不同分辨率下的小 ROI 归一采样到固定 96x32；
    2. 计算相邻行的亮度差；
    3. 对每条水平边界统计横向覆盖率；

    这里只提取行特征；真正的技力条判定还会同时检查三段矩形的
    上下边框和两个段间空隙，避免把下方连续生命条的边缘误认成三段条。
    """
    if width <= 4 or height <= 2:
        return []

    expected = width * height * 4
    if len(raw) < expected:
        return []

    mv = memoryview(raw)
    tw = CENTER_BAR_SAMPLE_W
    th = CENTER_BAR_SAMPLE_H

    # 归一化采样，避免 2K / 4K 下像素数不同直接改变几何阈值。
    gray = [[0.0] * tw for _ in range(th)]
    for ty in range(th):
        sy = min(height - 1, int((ty + 0.5) * height / th))
        row_base = sy * width
        grow = gray[ty]
        for tx in range(tw):
            sx = min(width - 1, int((tx + 0.5) * width / tw))
            off = (row_base + sx) * 4
            b = mv[off]
            g = mv[off + 1]
            r = mv[off + 2]
            grow[tx] = (29 * b + 150 * g + 77 * r) / 256.0

    coverages = []
    for y in range(th - 1):
        hits = 0
        a = gray[y]
        b = gray[y + 1]
        for x in range(tw):
            if abs(b[x] - a[x]) >= CENTER_BAR_EDGE_DIFF_MIN:
                hits += 1
        coverages.append(hits / float(tw))

    return coverages


def analyze_center_bar_structure(segment_rows, gap_rows):
    """
    返回三个中央技力条分段的结构得分。

    真技力条必须在同一对、彼此分离的水平位置上形成上下边框，且
    这些边框不能同时贯穿两个段间空隙。后一个条件专门排除 FALSE2
    中上下移动的连续生命条边框。至少两个分段同时通过才会由调用方
    判为技力条存在。
    """
    if len(segment_rows) != 3 or len(gap_rows) != 2:
        return [0.0, 0.0, 0.0]
    if any(not rows for rows in segment_rows + gap_rows):
        return [0.0, 0.0, 0.0]

    row_count = min(len(rows) for rows in segment_rows + gap_rows)
    best_group_score = 0.0
    best_segment_scores = [0.0, 0.0, 0.0]

    for upper in range(row_count):
        if max(gap_rows[0][upper], gap_rows[1][upper]) > CENTER_BAR_GAP_EDGE_MAX:
            continue
        for lower in range(
            upper + CENTER_BAR_MIN_EDGE_SEPARATION_ROWS,
            row_count,
        ):
            if max(
                gap_rows[0][lower], gap_rows[1][lower]
            ) > CENTER_BAR_GAP_EDGE_MAX:
                continue

            pair_scores = [
                min(rows[upper], rows[lower])
                for rows in segment_rows
            ]
            group_score = sorted(pair_scores, reverse=True)[1]
            if group_score > best_group_score:
                best_group_score = group_score
                best_segment_scores = pair_scores

    return best_segment_scores



def analyze_bar_progress(raw, width, height):
    """
    v2.1-alpha5 display-only progress estimate.

    目标：估计能量条“已填充区域”的最右边界。
    - 不用于 READY / CONSUMED 判定。
    - 不假设 CD 时长。
    - 允许进度前进、停住、跳跃或倒退。
    - 返回 (progress_or_None, confidence)。

    游戏的连携条在充能时通常表现为“左侧较亮、右侧较暗”，
    因此这里寻找横向亮度上最明显的下降边界。
    """
    if width <= 4 or height <= 0:
        return None, 0.0

    expected = width * height * 4
    if len(raw) < expected:
        return None, 0.0

    mv = memoryview(raw)
    cols = [0.0] * width

    # 每列取 ROI 全高度的平均 RGB 亮度。
    # 不要求中性白，因此低血染色时仍有机会找到填充前沿。
    for x in range(width):
        total = 0.0
        for y in range(height):
            off = (y * width + x) * 4
            b = mv[off]
            g = mv[off + 1]
            r = mv[off + 2]
            total += (r + g + b) / 3.0
        cols[x] = total / height

    # 轻微横向平滑，压掉 1px 级抗锯齿/噪声。
    radius = max(1, min(3, int(round(width * 0.012))))
    smooth = [0.0] * width
    for x in range(width):
        a = max(0, x - radius)
        b = min(width, x + radius + 1)
        smooth[x] = sum(cols[a:b]) / (b - a)

    # 在每个候选边界两侧取小窗口均值，寻找最明显的“亮 -> 暗”落差。
    win = max(2, min(8, int(round(width * PROGRESS_EDGE_WINDOW_RATIO))))
    first = max(2, win)
    last = min(width - 2, width - win)
    if last <= first:
        return None, 0.0

    best_x = None
    best_drop = -999.0
    for x in range(first, last + 1):
        left = sum(smooth[x - win:x]) / win
        right = sum(smooth[x:x + win]) / win
        drop = left - right
        if drop > best_drop:
            best_drop = drop
            best_x = x

    if best_x is None or best_drop < PROGRESS_EDGE_DROP_MIN:
        return None, max(0.0, min(1.0, best_drop / max(1.0, PROGRESS_EDGE_DROP_MIN)))

    progress = best_x / float(width)
    progress = max(0.0, min(1.0, progress))

    # 16 点落差是最低可信门槛；60 左右及以上视为高置信。
    confidence = (best_drop - PROGRESS_EDGE_DROP_MIN) / 44.0
    confidence = max(0.0, min(1.0, confidence))
    return progress, confidence



def update_progress_estimate(st, candidate, confidence):
    """
    Update SlotState.bar_progress from a display-only candidate.

    A 3-sample median removes one-frame edge jitter but still allows genuine
    forward jumps, stalls, and backward jumps after at most one capture frame.
    """
    if candidate is None:
        st.progress_confidence = 0.0
        return st.bar_progress

    p = max(0.0, min(1.0, float(candidate)))
    st.progress_samples.append(p)
    if len(st.progress_samples) > PROGRESS_HISTORY_SIZE:
        del st.progress_samples[:-PROGRESS_HISTORY_SIZE]

    ordered = sorted(st.progress_samples)
    if len(ordered) == 1:
        stable = ordered[0]
    elif len(ordered) == 2:
        stable = (ordered[0] + ordered[1]) / 2.0
    else:
        stable = ordered[len(ordered) // 2]

    st.bar_progress = max(0.0, min(1.0, stable))
    st.progress_confidence = max(0.0, min(1.0, float(confidence)))
    return st.bar_progress


def update_cd_learning(st, observed):
    """Update advisory CD time without mistaking a temporary speed buff.

    This function is display-only. It must never modify READY / CONSUMED state.
    The first valid sample remains immediately usable. A later sample at least
    2x shorter is recorded as a temporary fast cycle and never lowers the
    normal baseline. A clearly longer complete cycle may immediately raise the
    baseline, which also recovers quickly if the first sample was accelerated.
    """
    if not CD_LEARN_MIN_SECONDS <= observed <= CD_LEARN_MAX_SECONDS:
        return False

    def median(values):
        ordered = sorted(values)
        middle = len(ordered) // 2
        if len(ordered) % 2:
            return ordered[middle]
        return (ordered[middle - 1] + ordered[middle]) / 2.0

    def record(decision):
        st.cd_observations.append((observed, decision))
        if len(st.cd_observations) > CD_OBSERVATION_HISTORY_SIZE:
            del st.cd_observations[:-CD_OBSERVATION_HISTORY_SIZE]

    if st.learned_cd is None or not st.cd_samples:
        st.cd_samples[:] = [observed]
        st.cd_fast_samples.clear()
        st.learned_cd = observed
        record("initial")
        return True

    if st.learned_cd / observed >= CD_RELEARN_RATIO:
        # Repeated accelerated cycles (for example the post-ultimate charge
        # buff) stay diagnostic-only. They must not become the long-term model.
        st.cd_fast_samples.append(observed)
        if len(st.cd_fast_samples) > CD_FAST_HISTORY_SIZE:
            del st.cd_fast_samples[:-CD_FAST_HISTORY_SIZE]
        record("fast")
        return False

    if observed / st.learned_cd >= CD_RELEARN_RATIO:
        # A longer valid cycle is the safer display baseline: predicting too
        # slowly still snaps to the protected real READY, while predicting too
        # quickly can show a confidently wrong completion. It also repairs an
        # initial sample learned during a temporary acceleration in one cycle.
        st.cd_samples[:] = [observed]
        st.cd_fast_samples.clear()
        st.learned_cd = observed
        record("raised")
        return True

    st.cd_fast_samples.clear()
    st.cd_samples.append(observed)
    if len(st.cd_samples) > CD_LEARN_HISTORY_SIZE:
        del st.cd_samples[:-CD_LEARN_HISTORY_SIZE]
    st.learned_cd = median(st.cd_samples)
    record("accepted")
    return True


def controller_predicted_cd_display(st, now):
    """返回手柄模式的平滑预测进度；不参与 READY / CONSUMED。"""
    if (
        st.learned_cd is None
        or st.learned_cd <= 0.0
        or st.display_cd_start_time is None
        or not st.progress_tracking_active
        or st.confirmed_ready
        or st.is_dead
    ):
        return None

    elapsed = max(0.0, now - st.display_cd_start_time)
    predicted = min(
        PROGRESS_MAX_BEFORE_READY,
        elapsed / st.learned_cd,
    )
    remaining = (
        st.learned_cd - elapsed
        if elapsed < st.learned_cd
        else None
    )
    return predicted, remaining


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


def analyze_death_icon(raw, width, height):
    """
    返回 0~1 左右的死亡图标相似度（归一化相关系数）。

    设计目标：
    - 只匹配头像中心固定的“斜杠圆圈”死亡符号；
    - 不依赖角色是谁，也不依赖剩余血量；
    - 当前 ROI 会先按采样映射到固定 32x32，再与内置模板比较；
    - 归一化相关对整体光照明暗变化不敏感。

    此指标只参与 DEAD 的二次确认，不参与 READY / CONSUMED / CD 进度。
    """
    if width <= 0 or height <= 0:
        return 0.0
    if len(raw) < width * height * 4:
        return 0.0

    mv = memoryview(raw)
    tpl = DEATH_ICON_TEMPLATE
    target = DEATH_ICON_TEMPLATE_SIZE
    n = target * target

    sum_x = 0.0
    sum_t = 0.0
    sum_x2 = 0.0
    sum_t2 = 0.0
    sum_xt = 0.0

    k = 0
    for ty in range(target):
        sy = min(height - 1, int((ty + 0.5) * height / target))
        row = sy * width
        for tx in range(target):
            sx = min(width - 1, int((tx + 0.5) * width / target))
            p = (row + sx) * 4
            b = mv[p]
            g = mv[p + 1]
            r = mv[p + 2]

            # 与 OpenCV 常规灰度转换接近的整数亮度。
            x = (29 * b + 150 * g + 77 * r) / 256.0
            t = tpl[k]
            k += 1

            sum_x += x
            sum_t += t
            sum_x2 += x * x
            sum_t2 += t * t
            sum_xt += x * t

    num = n * sum_xt - sum_x * sum_t
    den_x = n * sum_x2 - sum_x * sum_x
    den_t = n * sum_t2 - sum_t * sum_t
    if den_x <= 1e-9 or den_t <= 1e-9:
        return 0.0

    score = num / math.sqrt(den_x * den_t)
    return max(-1.0, min(1.0, score))


def bgra_to_circle_rgba(raw, width, height, radius=None):
    """把 BGRA 截屏裁剪成圆形，返回 RGBA 像素字节（圆外 alpha=0）。

    圆心取 ROI 中心；半径默认 = min(w,h)/2 * AVATAR_CROP_RATIO，
    也可由调用方指定。边缘 1px 线性过渡抗锯齿。供显示时任意缩放。
    """
    if width <= 0 or height <= 0 or len(raw) < width * height * 4:
        return None
    cx = (width - 1) / 2.0
    cy = (height - 1) / 2.0
    if radius is None:
        radius = min(width, height) / 2.0 * AVATAR_CROP_RATIO
    inner = max(0.0, radius - 1.0)
    mv = memoryview(raw)
    out = bytearray(width * height * 4)
    for y in range(height):
        for x in range(width):
            p = (y * width + x) * 4
            b = mv[p]
            g = mv[p + 1]
            r = mv[p + 2]
            d = math.sqrt((x - cx) ** 2 + (y - cy) ** 2)
            if d <= inner:
                a = 255
            elif d >= radius:
                a = 0
            else:
                a = int(round(255 * (radius - d)))
            q = p
            out[q] = r
            out[q + 1] = g
            out[q + 2] = b
            out[q + 3] = a
    return bytes(out)


def rgba_to_scanlines(rgba, width, height):
    """把 RGBA 像素字节转成带 filter 字节的 PNG 扫描线列表。"""
    scanlines = []
    for y in range(height):
        row = bytearray(width * 4 + 1)
        row[0] = 0  # PNG filter: None
        row[1:] = rgba[y * width * 4:(y + 1) * width * 4]
        scanlines.append(bytes(row))
    return scanlines


def decode_png_to_rgba(png):
    """解码 RGBA PNG 为 (width, height, rgba_bytes)；支持全部 5 种 filter。"""
    if not png or png[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    pos = 8
    width = height = None
    bit_depth = color_type = None
    idat = b""
    while pos + 8 <= len(png):
        ln = struct.unpack(">I", png[pos:pos + 4])[0]
        tag = png[pos + 4:pos + 8]
        data = png[pos + 8:pos + 8 + ln]
        if tag == b"IHDR":
            width = struct.unpack(">I", data[0:4])[0]
            height = struct.unpack(">I", data[4:8])[0]
            bit_depth = data[8]
            color_type = data[9]
        elif tag == b"IDAT":
            idat += data
        pos += 12 + ln
    if not width or not height or bit_depth != 8 or color_type != 6:
        return None  # 仅支持 8bit RGBA（本程序生成的格式）
    raw = zlib.decompress(idat)
    stride = width * 4
    bpp = 4
    rows = []
    prev = bytearray(stride)
    for y in range(height):
        base = y * (stride + 1)
        f = raw[base]
        line = bytearray(raw[base + 1:base + 1 + stride])
        if f == 1:
            for x in range(bpp, stride):
                line[x] = (line[x] + line[x - bpp]) & 0xFF
        elif f == 2:
            for x in range(stride):
                line[x] = (line[x] + prev[x]) & 0xFF
        elif f == 3:
            for x in range(stride):
                a = line[x - bpp] if x >= bpp else 0
                line[x] = (line[x] + ((a + prev[x]) >> 1)) & 0xFF
        elif f == 4:
            for x in range(stride):
                a = line[x - bpp] if x >= bpp else 0
                b = prev[x]
                c = prev[x - bpp] if x >= bpp else 0
                p = a + b - c
                pa = abs(p - a)
                pb = abs(p - b)
                pc = abs(p - c)
                pr = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                line[x] = (line[x] + pr) & 0xFF
        rows.append(bytes(line))
        prev = line
    return width, height, b"".join(rows)


def resample_rgba(rgba, sw, sh, tw, th):
    """双线性重采样 RGBA 像素到目标尺寸（含 alpha，圆外保持透明）。"""
    if tw <= 0 or th <= 0 or sw <= 0 or sh <= 0:
        return b""
    out = bytearray(tw * th * 4)
    for ty in range(th):
        fy = (ty + 0.5) * sh / th - 0.5
        y0 = max(0, int(math.floor(fy)))
        y1 = min(sh - 1, y0 + 1)
        wy = fy - y0
        for tx in range(tw):
            fx = (tx + 0.5) * sw / tw - 0.5
            x0 = max(0, int(math.floor(fx)))
            x1 = min(sw - 1, x0 + 1)
            wx = fx - x0
            p00 = (y0 * sw + x0) * 4
            p01 = (y0 * sw + x1) * 4
            p10 = (y1 * sw + x0) * 4
            p11 = (y1 * sw + x1) * 4
            q = (ty * tw + tx) * 4
            w00 = (1.0 - wx) * (1.0 - wy)
            w01 = wx * (1.0 - wy)
            w10 = (1.0 - wx) * wy
            w11 = wx * wy
            for ch in range(4):
                v = (
                    rgba[p00 + ch] * w00
                    + rgba[p01 + ch] * w01
                    + rgba[p10 + ch] * w10
                    + rgba[p11 + ch] * w11
                )
                out[q + ch] = int(round(v))
    return bytes(out)


def _encode_rgba_png(width, height, scanlines):
    """把含每行 filter 字节的 RGBA 扫描线打包为 PNG（标准库 zlib/struct）。"""
    def chunk(tag, data):
        payload = tag + data
        return (
            struct.pack(">I", len(data))
            + payload
            + struct.pack(">I", zlib.crc32(payload) & 0xFFFFFFFF)
        )

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)  # 8bit RGBA
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(b"".join(scanlines), 9))
        + chunk(b"IEND", b"")
    )


def bgra_to_circle_png(raw, width, height, radius=None):
    """把 BGRA 截屏裁剪成圆形并编码为 RGBA PNG（圆外透明）。

    圆心取 ROI 中心；半径默认 = min(w,h)/2 * AVATAR_CROP_RATIO，
    也可由调用方直接指定（对准器按用户拖出的圆框半径抓取）。
    边缘 1px 线性过渡实现抗锯齿。只使用标准库（zlib/struct），
    供 tk.PhotoImage(data=...) 直接加载。
    """
    if width <= 0 or height <= 0 or len(raw) < width * height * 4:
        return None

    cx = (width - 1) / 2.0
    cy = (height - 1) / 2.0
    if radius is None:
        radius = min(width, height) / 2.0 * AVATAR_CROP_RATIO
    inner = max(0.0, radius - 1.0)
    mv = memoryview(raw)

    scanlines = []
    for y in range(height):
        row = bytearray()
        row.append(0)  # PNG filter: None
        for x in range(width):
            p = (y * width + x) * 4
            b = mv[p]
            g = mv[p + 1]
            r = mv[p + 2]
            d = math.sqrt((x - cx) ** 2 + (y - cy) ** 2)
            if d <= inner:
                a = 255
            elif d >= radius:
                a = 0
            else:
                a = int(round(255 * (radius - d)))
            row += bytes((r, g, b, a))
        scanlines.append(bytes(row))
    return _encode_rgba_png(width, height, scanlines)


# v2.4 头像模式的 CD 半透明遮罩参数。
CD_MASK_ALPHA = 170        # 遮罩不透明度（0-255）
CD_MASK_COLOR = (10, 10, 12)

# HUD 标识样式：circle = 实心圆（原动画）；avatar = 头像 + FF14 式遮罩。
HUD_STYLE_CIRCLE = "circle"
HUD_STYLE_AVATAR = "avatar"
HUD_STYLE_NAMES = {
    "实心圆形": HUD_STYLE_CIRCLE,
    "角色头像": HUD_STYLE_AVATAR,
}
HUD_STYLE_LABELS = {v: k for k, v in HUD_STYLE_NAMES.items()}


def make_cd_mask_png(size, fraction, alpha=CD_MASK_ALPHA):
    """生成 FF14 风格的 CD 半透明遮罩 PNG（RGBA，圆外透明）。

    fraction = 遮罩覆盖比例：1.0 为满圆遮罩（冷却刚开始），0.0 为无遮罩
    （冷却完成）。遮罩从 12 点开始按逆时针方向覆盖，随冷却减少从
    12 点逆时针消退。圆边缘 1px 抗锯齿。
    """
    if size <= 0:
        return None
    cx = (size - 1) / 2.0
    cy = (size - 1) / 2.0
    radius = size / 2.0 - 1.0
    fraction = max(0.0, min(1.0, float(fraction)))
    cr, cg, cb = CD_MASK_COLOR

    scanlines = []
    for y in range(size):
        row = bytearray()
        row.append(0)  # PNG filter: None
        for x in range(size):
            dx = x - cx
            dy = y - cy
            d = math.hypot(dx, dy)
            if d >= radius:
                row += bytes((0, 0, 0, 0))
                continue
            # 从 12 点开始逆时针的角度（0..2pi）
            t = math.atan2(dx, -dy) % (2 * math.pi)
            t_ccw = (2 * math.pi - t) % (2 * math.pi)
            if t_ccw <= 2 * math.pi * fraction:
                a = alpha
                if d >= radius - 1.0:
                    a = int(a * (radius - d))
                row += bytes((cr, cg, cb, a))
            else:
                row += bytes((0, 0, 0, 0))
        scanlines.append(bytes(row))
    return _encode_rgba_png(size, size, scanlines)


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
        self.death_icon_score = 0.0
        self.center_bar_score = 0.0
        self.dead_streak = 0
        self.is_dead = False

        # v2.1-alpha5: display-only progress / countdown state.
        # These fields are intentionally separate from READY / CONSUMED logic.
        self.bar_progress = 0.0
        self.progress_confidence = 0.0
        self.progress_samples = []
        self.cd_start_time = None
        self.cd_cycle_valid = False
        self.cd_ready_context_streak = 0
        self.cd_ready_trusted_until = 0.0
        self.cd_pending_fresh_start = None
        # v2.3.2 Beta: controller display timer. Unlike cd_start_time, this
        # survives a center-bar interruption and is never used for learning or
        # authoritative state changes.
        self.display_cd_start_time = None
        self.learned_cd = None
        self.cd_samples = []
        self.cd_fast_samples = []
        self.cd_observations = []
        self.remaining_seconds = None
        # 这里只表示显示层当前正在展示“未 READY”的进度。
        # 它不参与 READY / CONSUMED 判定。
        self.progress_tracking_active = False



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
    v2.1-alpha5 HUD renderer.

    Detection is deliberately outside this class. Overlay only consumes the
    final ready=True/False state.

    v2.0 renderer changes:
    - marker opacity and black background opacity are independent
    - black background can be fully hidden
    - marker size and spacing are independent
    - slot numbers can be hidden
    - unavailable slots can be dimmed or hidden
    - CD progress can fill the circle clockwise from 12 o’clock
    - READY alert brightens the whole circle instead of drawing an outer ring

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

    def __init__(self, root, on_drag_end=None, on_scale_changed=None,
                 on_double_click=None):
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

        # v2.4: 槽位头像（圆形裁剪 RGBA PNG 的 PhotoImage），None 表示未抓取。
        # _avatar_display 是缩放到当前显示尺寸后的缓存，避免每帧重新缩放。
        self.avatars = [None, None, None, None]
        self._avatar_display = [None, None, None, None]
        self._avatar_display_target = [0.0, 0.0, 0.0, 0.0]
        # v2.4: 标识样式。circle = 实心圆（原动画不变）；
        # avatar = 角色头像 + FF14 式半透明 CD 遮罩（顺时针消退）。
        self.style = "circle"
        self._mask_cache = {}

        self.flash_slot = None
        self.flash_enabled = True
        self.flash_started = 0.0

        # v2.1-alpha5: target values come from the capture thread.
        # display_progress only interpolates between samples for smoother motion;
        # it never predicts progress on its own.
        self.target_progress = [0.0, 0.0, 0.0, 0.0]
        self.display_progress = [0.0, 0.0, 0.0, 0.0]
        self.progress_from = [0.0, 0.0, 0.0, 0.0]
        self.progress_anim_started = [0.0, 0.0, 0.0, 0.0]
        self.remaining_seconds = [None, None, None, None]
        self.learned_cd = [None, None, None, None]
        self.progress_confidence = [0.0, 0.0, 0.0, 0.0]
        self.progress_active = [False, False, False, False]
        self.show_countdown = True

        self.scale = 1.0
        self.spacing = 1.0
        self.marker_opacity = 1.0
        self.background_opacity = 0.62
        self.show_numbers = False
        self.unavailable_mode = "暗色显示"

        self.position_name = "中央偏下"
        self.custom_rel = None
        self.monitor = None

        self.adjust_mode = False
        self._visible = False
        self._drag_start_pointer = None
        self._drag_start_window = None
        self.on_drag_end = on_drag_end
        self.on_scale_changed = on_scale_changed
        self.on_double_click = on_double_click

        self.canvas.bind("<ButtonPress-1>", self._drag_press)
        self.canvas.bind("<B1-Motion>", self._drag_move)
        self.canvas.bind("<ButtonRelease-1>", self._drag_release)
        self.canvas.bind("<Control-MouseWheel>", self._ctrl_wheel)
        self.canvas.bind("<Double-Button-1>", self._double_click)

        self.set_marker_opacity(self.marker_opacity)
        self.set_background_opacity(self.background_opacity)
        self._resize_canvas()

        # 30Hz-ish UI animation loop. Detection remains 10Hz and untouched.
        self.win.after(OVERLAY_ANIM_MS, self._animation_tick)

    @property
    def gap(self):
        # 圆心距 54 -> 43（圆直径 32 不变，圆间空隙 22 -> 11，缩小一倍且不重叠）。
        return 43.0 * self.scale * self.spacing

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

    def _ctrl_wheel(self, event):
        """Ctrl + 滚轮调整 HUD 大小（0.5x ~ 2.5x，0.1 步进）。"""
        try:
            steps = int(event.delta) / 120.0
        except Exception:
            steps = 0.0
        if steps == 0.0:
            return
        new_scale = round(
            max(0.50, min(2.50, self.scale + 0.10 * steps)), 2
        )
        if abs(new_scale - self.scale) < 0.001:
            return
        self.set_scale(new_scale)
        if self.on_scale_changed is not None:
            try:
                self.on_scale_changed(new_scale)
            except Exception:
                pass

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

    # ---------------- v2.4 avatar support ----------------

    def set_avatars(self, avatars):
        """设置 4 个槽位的头像；元素为 (rgba_bytes, w, h) 或 None。"""
        if not isinstance(avatars, (list, tuple)) or len(avatars) != 4:
            return
        self.avatars = list(avatars)
        self._avatar_display = [None, None, None, None]
        self._avatar_display_target = [0.0, 0.0, 0.0, 0.0]
        self.redraw()

    def clear_avatars(self):
        self.set_avatars([None, None, None, None])

    def set_style(self, style):
        """设置标识样式：circle（实心圆原动画）/ avatar（头像+遮罩）。"""
        if style not in ("circle", "avatar"):
            style = "circle"
        if style != self.style:
            self.style = style
            self.redraw()

    def has_avatar(self, idx):
        return 0 <= idx < 4 and self.avatars[idx] is not None

    def _avatar_display_photo(self, idx, target_d):
        """把抓取的头像精确缩放到目标显示直径（双线性重采样）。

        头像以 RGBA 原始像素保存（avatars[idx] = (rgba, w, h)），
        任意缩放级别都平滑、精确填满圆，圆外保持透明；不再依赖
        PhotoImage 的整数倍缩放（那会导致 200% 附近尺寸突变）。
        结果按目标直径缓存，尺寸变化时才重建。
        """
        src = self.avatars[idx]
        if src is None or target_d <= 0:
            return None
        if (
            self._avatar_display[idx] is not None
            and abs(self._avatar_display_target[idx] - target_d) < 0.5
        ):
            return self._avatar_display[idx]

        rgba, sw, sh = src
        size = max(8, int(round(target_d)))
        try:
            scaled = resample_rgba(rgba, sw, sh, size, size)
            png = _encode_rgba_png(
                size, size, rgba_to_scanlines(scaled, size, size)
            )
            photo = tk.PhotoImage(data=png)
        except Exception:
            return None

        self._avatar_display[idx] = photo
        self._avatar_display_target[idx] = target_d
        return photo

    def _cd_mask_photo(self, target_d, fraction):
        """返回 FF14 式半透明 CD 遮罩 PhotoImage（按尺寸+档位缓存）。"""
        if target_d <= 0:
            return None
        size = max(8, int(round(target_d)))
        frac_key = round(max(0.0, min(1.0, float(fraction))), 2)
        key = (size, frac_key)
        photo = self._mask_cache.get(key)
        if photo is not None:
            return photo
        png = make_cd_mask_png(size, frac_key)
        if png is None:
            return None
        try:
            photo = tk.PhotoImage(data=png)
        except Exception:
            return None
        # 限制缓存条目，避免长会话累积过多档位。
        if len(self._mask_cache) > 96:
            self._mask_cache.clear()
        self._mask_cache[key] = photo
        return photo

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
    def _lighten_hex(hex_color, amount=0.65):
        """Blend a color toward white by amount 0..1."""
        try:
            amount = max(0.0, min(1.0, float(amount)))
            h = hex_color.lstrip("#")
            r = int(h[0:2], 16)
            g = int(h[2:4], 16)
            b = int(h[4:6], 16)
            r = round(r + (255 - r) * amount)
            g = round(g + (255 - g) * amount)
            b = round(b + (255 - b) * amount)
            return f"#{r:02X}{g:02X}{b:02X}"
        except Exception:
            return "#FFFFFF"

    def set_show_countdown(self, enabled):
        self.show_countdown = bool(enabled)
        self.redraw()

    def reset_runtime_progress(self):
        now = time.monotonic()
        for i in range(4):
            self.target_progress[i] = 0.0
            self.display_progress[i] = 0.0
            self.progress_from[i] = 0.0
            self.progress_anim_started[i] = now
            self.remaining_seconds[i] = None
            self.learned_cd[i] = None
            self.progress_confidence[i] = 0.0
            self.progress_active[i] = False
        self.flash_slot = None
        self.flash_started = 0.0
        self.redraw()

    def set_progress(
        self, idx, progress, remaining=None, learned_cd=None,
        confidence=0.0, active=False
    ):
        if not 0 <= idx < 4:
            return

        try:
            p = max(0.0, min(1.0, float(progress)))
        except Exception:
            return

        now = time.monotonic()

        # READY always owns the final 100% state.
        if self.ready[idx]:
            p = 1.0
            self.display_progress[idx] = 1.0
            self.progress_from[idx] = 1.0
            self.target_progress[idx] = 1.0
            self.progress_anim_started[idx] = now
        else:
            self.progress_from[idx] = self.display_progress[idx]
            self.target_progress[idx] = p
            self.progress_anim_started[idx] = now

        self.remaining_seconds[idx] = remaining
        self.learned_cd[idx] = learned_cd
        self.progress_active[idx] = bool(active)
        try:
            self.progress_confidence[idx] = float(confidence)
        except Exception:
            self.progress_confidence[idx] = 0.0

    def _pulse_strength(self, idx, now=None):
        if not self.flash_enabled or self.flash_slot != idx:
            return 0.0
        if now is None:
            now = time.monotonic()
        t = now - self.flash_started
        if t < 0.0 or t >= READY_PULSE_SECONDS:
            return 0.0
        # Smooth single pulse: normal -> bright -> normal.
        return math.sin(math.pi * (t / READY_PULSE_SECONDS))

    def _animation_tick(self):
        try:
            now = time.monotonic()
            changed = False

            for i in range(4):
                if self.ready[i]:
                    if self.display_progress[i] != 1.0:
                        self.display_progress[i] = 1.0
                        changed = True
                    continue

                start = self.progress_anim_started[i]
                if start <= 0:
                    continue

                u = (now - start) / PROGRESS_ANIM_SECONDS
                if u >= 1.0:
                    new_value = self.target_progress[i]
                elif u <= 0.0:
                    new_value = self.progress_from[i]
                else:
                    # Linear interpolation is intentionally used here:
                    # it looks like a smooth mechanical sweep and does not
                    # extrapolate beyond the latest captured white-bar value.
                    new_value = (
                        self.progress_from[i]
                        + (self.target_progress[i] - self.progress_from[i]) * u
                    )

                if abs(new_value - self.display_progress[i]) > 0.001:
                    self.display_progress[i] = new_value
                    changed = True

            if self.flash_slot is not None:
                if now - self.flash_started >= READY_PULSE_SECONDS:
                    self.flash_slot = None
                    changed = True
                else:
                    changed = True

            if changed:
                self.redraw()
        except Exception:
            # Overlay animation must never crash detection.
            pass
        finally:
            try:
                self.win.after(OVERLAY_ANIM_MS, self._animation_tick)
            except Exception:
                pass

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
        now = time.monotonic()

        if self.adjust_mode:
            c.create_rectangle(
                1, 1, self.W - 2, self.H - 2,
                outline="#8A8A8A",
                width=max(1, int(round(1.5 * s))),
            )

        for i in range(4):
            x = start_x + i * gap
            slot_color = self.slot_colors[i]
            dim_color = self._darken_hex(slot_color, 0.30)
            r = 16 * s
            textc = "#FFFFFF"

            if (
                self.style == "avatar"
                and self.avatars[i] is not None
                and self.enabled[i]
            ):
                # -------- avatar style: 仅头像 + FF14 式遮罩（无边框） --------
                if self.ready[i]:
                    photo = self._avatar_display_photo(i, 2 * r)
                    if photo is not None:
                        c.create_image(x, y, image=photo)
                elif self.unavailable_mode == "完全隐藏":
                    continue
                else:
                    # CD/待机：头像 + 半透明扇形遮罩（从满圆逆时针消退）。
                    photo = self._avatar_display_photo(i, 2 * r)
                    if photo is not None:
                        c.create_image(x, y, image=photo)
                    p = max(
                        0.0,
                        min(PROGRESS_MAX_BEFORE_READY, self.display_progress[i]),
                    )
                    if p < 1.0:
                        mask = self._cd_mask_photo(2 * r, 1.0 - p)
                        if mask is not None:
                            c.create_image(x, y, image=mask)

            elif not self.enabled[i]:
                outline = "#3F3F3F"
                fill = self.TRANSPARENT_KEY if self.transparency_supported else self.bg
                textc = "#5E5E5E"
                width = max(1, int(round(2 * s)))

                c.create_oval(
                    x - r, y - r, x + r, y + r,
                    outline=outline,
                    width=width,
                    fill=fill,
                )

            elif self.ready[i]:
                pulse = self._pulse_strength(i, now)
                draw_color = self._lighten_hex(
                    slot_color, READY_PULSE_LIGHTEN * pulse
                )
                outline = draw_color
                fill = draw_color
                textc = self._text_color_for_fill(draw_color)
                width = max(2, int(round(3 * s)))

                # alpha5 READY alert: the whole marker brightens AND expands.
                # This is still the same filled circle, not an added outer ring.
                pulse_r = r * (1.0 + READY_PULSE_EXPAND * pulse)
                c.create_oval(
                    x - pulse_r, y - pulse_r, x + pulse_r, y + pulse_r,
                    outline=outline,
                    width=width,
                    fill=fill,
                )

            else:
                if self.unavailable_mode == "完全隐藏":
                    continue

                # Dark base circle = not-yet-charged portion.
                c.create_oval(
                    x - r, y - r, x + r, y + r,
                    outline=dim_color,
                    width=max(1, int(round(2 * s))),
                    fill=dim_color,
                )

                p = max(0.0, min(PROGRESS_MAX_BEFORE_READY, self.display_progress[i]))
                if p > 0.001:
                    # Tk arc: 0° is 3 o'clock, positive is counter-clockwise.
                    # Start at 12 o'clock and use negative extent for clockwise fill.
                    c.create_arc(
                        x - r, y - r, x + r, y + r,
                        start=90,
                        extent=-360.0 * p,
                        style=tk.PIESLICE,
                        outline="",
                        fill=slot_color,
                    )
                    # Re-draw the dim outline after the pieslice so the shape stays clean.
                    c.create_oval(
                        x - r, y - r, x + r, y + r,
                        outline=dim_color,
                        width=max(1, int(round(2 * s))),
                        fill="",
                    )

                # White is easier to read across a mixed bright/dark circle.
                textc = "#FFFFFF"

            # alpha5: countdown and slot numbers are independent.
            # With countdown enabled, a CD circle shows seconds even when
            # slot numbering is disabled. READY circles stay clean unless the
            # user explicitly enables slot numbers.
            label = None
            if (
                self.enabled[i]
                and not self.ready[i]
                and self.show_countdown
                and self.progress_active[i]
            ):
                rem = self.remaining_seconds[i]
                if rem is None:
                    label = "--"
                else:
                    try:
                        label = str(max(0, int(math.ceil(float(rem)))))
                    except Exception:
                        label = "--"
            elif self.show_numbers:
                label = str(i + 1)

            if label is not None:
                if (
                    self.style == "avatar"
                    and self.avatars[i] is not None
                    and self.enabled[i]
                ):
                    # 头像上文字加简单阴影提升可读性。
                    c.create_text(
                        x + 1, y + 1,
                        text=label,
                        fill="#111111",
                        font=("Segoe UI", max(7, int(round(11 * s))), "bold"),
                    )
                c.create_text(
                    x, y,
                    text=label,
                    fill=textc,
                    font=("Segoe UI", max(7, int(round(11 * s))), "bold"),
                )

    def set_enabled(self, vals):
        self.enabled = list(vals)
        self.redraw()

    def set_ready(self, idx, val):
        self.ready[idx] = bool(val)
        if self.ready[idx]:
            self.target_progress[idx] = 1.0
            self.display_progress[idx] = 1.0
            self.progress_from[idx] = 1.0
            self.progress_anim_started[idx] = time.monotonic()
            self.remaining_seconds[idx] = 0.0
        self.redraw()

    def flash(self, idx):
        """READY visual alert: brighten the whole filled circle briefly.

        仅实心圆样式使用高亮动画；头像样式不做高亮（保持白环静态）。
        """
        if not self.flash_enabled or self.style != "circle":
            return
        self.flash_slot = idx
        self.flash_started = time.monotonic()
        self.redraw()
        if self._visible:
            self._sync_background_layer()
            self.win.attributes("-topmost", True)
            self.win.lift()

    def _clear_flash(self):
        # Kept for compatibility with older call sites; animation_tick clears it.
        self.flash_slot = None
        self.flash_started = 0.0
        self.redraw()

    def _double_click(self, event):
        """双击悬浮窗 -> 抓取游戏头像（由 App 提供回调）。"""
        if self.on_double_click is not None:
            try:
                self.on_double_click()
            except Exception:
                pass

    def _drag_press(self, event):
        # 鼠标左键按住 HUD 即可拖动位置，不再要求进入调整模式。
        self._drag_start_pointer = (event.x_root, event.y_root)
        self._drag_start_window = (self.win.winfo_x(), self.win.winfo_y())

    def _drag_move(self, event):
        if self._drag_start_pointer is None:
            return
        dx = event.x_root - self._drag_start_pointer[0]
        dy = event.y_root - self._drag_start_pointer[1]
        x = self._drag_start_window[0] + dx
        y = self._drag_start_window[1] + dy
        self._apply_geometry(x, y)

    def _drag_release(self, event):
        if self.monitor is None:
            return
        # 双击触发抓取时悬浮窗会先被隐藏；隐藏窗口的坐标无意义，跳过保存。
        if not self.win.winfo_viewable():
            self._drag_start_pointer = None
            self._drag_start_window = None
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


class AvatarAligner:
    """已弃用：手动拖动对准的头像抓取对准器（v2.4 早期方案）。

    当前版本改为固定坐标抓取（AVATAR_ALIGN_DEFAULT / avatar_align 设置），
    不再从 UI 调用本类；保留定义仅为避免大段删除引入风险。
    """

    MIN_R_REF = 18.0
    MAX_R_REF = 70.0

    def __init__(self, root, monitor, ref_positions, on_grab, on_close=None):
        self.root = root
        self.monitor = monitor
        self.on_grab = on_grab
        self.on_close = on_close
        self.slots = [list(p) for p in ref_positions]  # [cx_ref, cy_ref, r_ref] x4
        self._drag = None  # (offset_x, offset_y, slot_idx)

        self.win = tk.Toplevel(root)
        self.win.withdraw()
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        self.transparent = True
        try:
            self.win.configure(bg=Overlay.TRANSPARENT_KEY)
            self.win.attributes("-transparentcolor", Overlay.TRANSPARENT_KEY)
        except Exception:
            self.transparent = False
            self.win.configure(bg="#101010")

        ctrl_bg = "#202020"
        self.ctrl = tk.Frame(self.win, bg=ctrl_bg)
        self.ctrl.pack(side="top", fill="x")
        tk.Label(
            self.ctrl,
            text="拖动 4 个圆框对准角色头像；Ctrl+滚轮调圆大小；Esc 关闭",
            bg=ctrl_bg, fg="#DDDDDD",
        ).pack(side="left", padx=8, pady=6)
        tk.Button(
            self.ctrl, text="抓取头像", command=self._do_grab,
            bg="#3E6B4F", fg="white", activebackground="#4C855F",
        ).pack(side="left", padx=4, pady=6)
        tk.Button(
            self.ctrl, text="恢复默认", command=self._reset_defaults
        ).pack(side="left", padx=4, pady=6)
        tk.Button(
            self.ctrl, text="关闭", command=self._do_close
        ).pack(side="left", padx=4, pady=6)

        canvas_bg = Overlay.TRANSPARENT_KEY if self.transparent else "#101010"
        self.canvas = tk.Canvas(
            self.win, bg=canvas_bg, highlightthickness=0, bd=0,
        )
        self.canvas.pack(fill="both", expand=True)

        self._items = []
        for i in range(4):
            colors = ["#4DA6FF", "#67E8F9", "#C084FC", "#FF9A4D"]
            tag = f"align_slot_{i}"
            oval = self.canvas.create_oval(
                0, 0, 10, 10,
                outline=colors[i], width=2, tags=(tag,),
            )
            ch = self.canvas.create_line(0, 0, 0, 0, fill=colors[i], tags=(tag,))
            cv = self.canvas.create_line(0, 0, 0, 0, fill=colors[i], tags=(tag,))
            txt = self.canvas.create_text(
                0, 0, text=str(i + 1), fill="white", font=("Segoe UI", 12, "bold"),
                tags=(tag,),
            )
            self._items.append((oval, ch, cv, txt))
            self.canvas.tag_bind(
                tag, "<ButtonPress-1>",
                lambda e, idx=i: self._press(e, idx),
            )
            self.canvas.tag_bind(
                tag, "<B1-Motion>",
                lambda e, idx=i: self._move(e, idx),
            )

        self.canvas.bind("<Control-MouseWheel>", self._ctrl_wheel)
        self.win.bind("<Escape>", lambda e: self._do_close())

    # ---------------- geometry helpers ----------------

    def _slot_screen(self, i):
        """返回第 i 槽圆框的屏幕物理坐标 (cx, cy, r)。"""
        cx_ref, cy_ref, r_ref = self.slots[i]
        sx = self.monitor["width"] / REF_W
        sy = self.monitor["height"] / REF_H
        return (
            self.monitor["left"] + cx_ref * sx,
            self.monitor["top"] + cy_ref * sy,
            r_ref * sx,
        )

    def _canvas_to_ref(self, x, y):
        sx = REF_W / self.monitor["width"]
        sy = REF_H / self.monitor["height"]
        return (x * sx, y * sy)

    def _redraw_slot(self, i):
        cx, cy, r = self._slot_screen(i)
        x = cx - self.monitor["left"]
        y = cy - self.monitor["top"]
        oval, ch, cv, txt = self._items[i]
        self.canvas.coords(oval, x - r, y - r, x + r, y + r)
        self.canvas.coords(ch, x - 9, y, x + 9, y)
        self.canvas.coords(cv, x, y - 9, x, y + 9)
        self.canvas.coords(txt, x, y - r - 12)
        self.canvas.tag_raise(oval)

    def redraw(self):
        for i in range(4):
            self._redraw_slot(i)

    # ---------------- interactions ----------------

    def _press(self, event, idx):
        cx, cy, _ = self._slot_screen(idx)
        x = cx - self.monitor["left"]
        y = cy - self.monitor["top"]
        self._drag = (event.x - x, event.y - y, idx)

    def _move(self, event, idx):
        if not self._drag or self._drag[2] != idx:
            return
        off_x, off_y, _ = self._drag
        cx_ref, cy_ref = self._canvas_to_ref(event.x - off_x, event.y - off_y)
        self.slots[idx][0] = cx_ref
        self.slots[idx][1] = cy_ref
        self._redraw_slot(idx)

    def _ctrl_wheel(self, event):
        try:
            steps = int(event.delta) / 120.0
        except Exception:
            return
        for i in range(4):
            self.slots[i][2] = max(
                self.MIN_R_REF,
                min(self.MAX_R_REF, self.slots[i][2] + 4.0 * steps),
            )
        self.redraw()

    def _reset_defaults(self):
        self.slots = [list(p) for p in AVATAR_ALIGN_DEFAULT]
        self.redraw()

    def _do_grab(self):
        if self.on_grab is None:
            return
        self.win.withdraw()
        try:
            self.root.update()
        except Exception:
            pass
        refs = [tuple(s) for s in self.slots]
        try:
            self.on_grab(refs)
        except Exception:
            pass

    def _do_close(self):
        if self.on_close is not None:
            self.on_close()

    def show(self):
        w = self.monitor["width"]
        h = self.monitor["height"]
        self.win.geometry(
            f"{w}x{h}+{self.monitor['left']}+{self.monitor['top']}"
        )
        self.redraw()
        self.win.deiconify()
        self.win.attributes("-topmost", True)
        self.win.lift()
        self.win.focus_force()

    def destroy(self):
        try:
            self.win.destroy()
        except Exception:
            pass


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
        nid.szTip = "终末地连携 CD HUD v2.3"
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
        "EndfieldCDAlert_v21_alpha5"
    )
    SETTINGS_PATH = os.path.join(SETTINGS_DIR, "settings.json")

    def __init__(self, root):
        self.root = root
        self.root.title("终末地连携 CD HUD v2.3")
        self.root.geometry("820x780")
        self.root.minsize(780, 780)

        self.settings = self._load_settings()

        self.monitors = enum_monitors()
        if not self.monitors:
            raise RuntimeError("没有检测到显示器。")

        self.events = queue.Queue()
        self.overlay = Overlay(
            root,
            on_drag_end=self._on_hud_drag_end,
            on_scale_changed=self._on_hud_scale_changed,
            on_double_click=self._capture_avatars,
        )
        self.tray = TrayIcon(self.events)

        self.stop_event = threading.Event()
        self.worker = None
        self.running = False
        self.states = [SlotState() for _ in range(4)]
        self.combat_session_active = False
        self.combat_hud_present = False
        self.raw_center_bar_present = False
        self.center_bar_scores = [0.0, 0.0, 0.0]
        self.controller_expand = 0.0
        self.controller_anchor_score = 0.0
        self.window_visible = True
        self.preview_until = 0.0
        self.active_monitor = None
        # v2.4: 头像抓取坐标（参考系 [cx, cy, r] x4，None = 用内置默认值）
        self.avatar_align = None

        self._ui()
        self._apply_saved_settings()
        self._load_avatars()
        self.tray.start()
        self.tray.update_state(running=False, window_visible=True)

        self.root.after(50, self._poll)
        # 打开程序即自动开始检测（跳过非 16:9 确认框）。
        self.root.after(150, lambda: self.start(confirm_aspect=False))

    # ---------------- Settings ----------------

    def _defaults(self):
        return {
            "monitor": None,
            "input_layout": INPUT_LAYOUT_KEYBOARD,
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
            "show_numbers": False,
            "unavailable_mode": "暗色显示",
            "slot_color_names": list(DEFAULT_SLOT_COLOR_NAMES),
            "slot_colors": [
                COLOR_PRESETS["蓝"],
                COLOR_PRESETS["青"],
                COLOR_PRESETS["紫"],
                COLOR_PRESETS["橙"],
            ],
            "sound": False,
            "flash": True,
            "show_countdown": True,
            "pitch_by_slot": True,
            "silent_first": True,
            "show_in_combat_only": True,
            "hide_hud": False,
            "hud_style": HUD_STYLE_CIRCLE,
            "avatar_align": None,
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
                "input_layout": self.input_layout_var.get(),
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
                "show_countdown": bool(self.show_countdown_var.get()),
                "pitch_by_slot": bool(self.pitch_by_slot_var.get()),
                "silent_first": bool(self.silent_first_var.get()),
                "show_in_combat_only": bool(self.show_in_combat_only_var.get()),
                "hide_hud": bool(self.hide_hud_var.get()),
                "hud_style": self._hud_style_value(),
                "avatar_align": (
                    [list(p) for p in self.avatar_align]
                    if self.avatar_align is not None
                    else None
                ),
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
            text="终末地连携 CD HUD v2.3",
            style="Title.TLabel",
        ).pack(anchor="w", padx=18, pady=(16, 2))

        ttk.Label(
            self.root,
            text="键鼠正式支持；手柄 Beta 为实验性画面识别。",
        ).pack(anchor="w", padx=18, pady=(0, 10))

        footer = ttk.Frame(self.root)
        footer.pack(side="bottom", fill="x", padx=18, pady=(8, 14))

        # 打开程序即自动开始检测，不再提供开始/暂停按钮；
        # 暂停/恢复通过托盘菜单使用。
        ttk.Button(
            footer, text="隐藏到托盘", command=self.hide_to_tray
        ).pack(side="left")
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
            # 固定小间隔紧凑排列
            cb.grid(row=1, column=i + 1, padx=4, pady=10, sticky="w")
        monitor_tab.columnconfigure(1, weight=1)

        ttk.Label(monitor_tab, text="游戏操作布局").grid(
            row=2, column=0, padx=(0, 10), pady=10, sticky="w"
        )
        self.input_layout_var = tk.StringVar(value=INPUT_LAYOUT_KEYBOARD)
        self.input_layout_box = ttk.Combobox(
            monitor_tab,
            textvariable=self.input_layout_var,
            state="readonly",
            values=INPUT_LAYOUTS,
            width=24,
        )
        self.input_layout_box.grid(
            row=2, column=1, columnspan=3, pady=10, sticky="w"
        )
        self.input_layout_box.bind(
            "<<ComboboxSelected>>", lambda e: self._settings_changed()
        )

        ttk.Label(
            monitor_tab,
            text="手柄 Beta 目前仅以 3840×2160 实测；复杂特效下可能误判。",
        ).grid(row=3, column=0, columnspan=6, pady=(14, 0), sticky="w")

        # -------- HUD tab --------
        # 位置不再提供下拉/调整按钮：直接用鼠标拖拽 HUD 定位（自定义）。
        self.position_var = tk.StringVar(value="中央偏下")

        # 顶部：预览 + 样式选择
        top = ttk.Frame(hud_tab)
        top.grid(row=0, column=0, columnspan=4, sticky="ew", padx=8, pady=(4, 6))
        ttk.Button(top, text="预览 HUD", command=self.preview_hud).pack(
            side="left", padx=(0, 16)
        )
        ttk.Label(top, text="HUD 标识样式：").pack(side="left")
        self.hud_style_var = tk.StringVar(value="实心圆形")
        self.hud_style_box = ttk.Combobox(
            top, textvariable=self.hud_style_var, state="readonly",
            values=list(HUD_STYLE_NAMES.keys()), width=12,
        )
        self.hud_style_box.pack(side="left", padx=(6, 0))
        self.hud_style_box.bind(
            "<<ComboboxSelected>>", lambda e: self._hud_style_changed()
        )

        self.show_countdown_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            top, text="CD 显示预计秒数", variable=self.show_countdown_var,
            command=self._show_countdown_changed,
        ).pack(side="left", padx=(18, 0))

        # 两个板块左右排布
        boards = ttk.Frame(hud_tab)
        boards.grid(row=1, column=0, columnspan=4, sticky="ew",
                    padx=8, pady=(0, 8))
        boards.columnconfigure(0, weight=1)
        boards.columnconfigure(1, weight=1)

        # -------- 板块一：实心圆形样式 --------
        circle_frame = ttk.LabelFrame(
            boards, text="实心圆形样式（原动画）", padding=8,
        )
        circle_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 4))
        circle_frame.columnconfigure(1, weight=1)

        ttk.Label(circle_frame, text="槽位颜色").grid(
            row=0, column=0, padx=(0, 6), pady=3, sticky="w"
        )
        self.slot_color_names = list(DEFAULT_SLOT_COLOR_NAMES)
        self.slot_colors = [COLOR_PRESETS[name] for name in self.slot_color_names]
        self.color_vars = []
        self.color_boxes = []
        color_frame = ttk.Frame(circle_frame)
        color_frame.grid(row=0, column=1, columnspan=3, pady=3, sticky="w")
        for i in range(4):
            ttk.Label(color_frame, text=f"{i + 1}").pack(
                side="left", padx=(0 if i == 0 else 8, 2)
            )
            var = tk.StringVar(value=self.slot_color_names[i])
            self.color_vars.append(var)
            box = ttk.Combobox(
                color_frame, textvariable=var, state="readonly",
                values=list(COLOR_PRESETS.keys()), width=3,
            )
            box.pack(side="left")
            box.bind("<<ComboboxSelected>>", lambda e, idx=i: self._preset_color_changed(idx))
            self.color_boxes.append(box)
        ttk.Button(
            color_frame, text="恢复默认配色", command=self._reset_slot_colors
        ).pack(side="left", padx=(10, 0))

        ttk.Label(circle_frame, text="标志大小").grid(
            row=1, column=0, padx=(0, 6), pady=3, sticky="w"
        )
        self.scale_var = tk.DoubleVar(value=1.0)
        self.scale_slider = ttk.Scale(
            circle_frame, from_=0.50, to=2.50, variable=self.scale_var,
            orient="horizontal", command=self._scale_changed,
        )
        self.scale_slider.grid(row=1, column=1, pady=3, sticky="ew")
        self.scale_text = ttk.Label(circle_frame, text="100%")
        self.scale_text.grid(row=1, column=2, padx=(6, 0), pady=3, sticky="w")

        ttk.Label(circle_frame, text="标志间距").grid(
            row=2, column=0, padx=(0, 6), pady=3, sticky="w"
        )
        self.spacing_var = tk.DoubleVar(value=1.0)
        self.spacing_slider = ttk.Scale(
            circle_frame, from_=0.50, to=2.00, variable=self.spacing_var,
            orient="horizontal", command=self._spacing_changed,
        )
        self.spacing_slider.grid(row=2, column=1, pady=3, sticky="ew")
        self.spacing_text = ttk.Label(circle_frame, text="100%")
        self.spacing_text.grid(row=2, column=2, padx=(6, 0), pady=3, sticky="w")

        ttk.Label(circle_frame, text="标志不透明度").grid(
            row=3, column=0, padx=(0, 6), pady=3, sticky="w"
        )
        self.marker_opacity_var = tk.DoubleVar(value=1.0)
        self.marker_opacity_slider = ttk.Scale(
            circle_frame, from_=0.20, to=1.00, variable=self.marker_opacity_var,
            orient="horizontal", command=self._marker_opacity_changed,
        )
        self.marker_opacity_slider.grid(row=3, column=1, pady=3, sticky="ew")
        self.marker_opacity_text = ttk.Label(circle_frame, text="100%")
        self.marker_opacity_text.grid(row=3, column=2, padx=(6, 0), pady=3, sticky="w")

        ttk.Label(circle_frame, text="背景不透明度").grid(
            row=4, column=0, padx=(0, 6), pady=3, sticky="w"
        )
        self.background_opacity_var = tk.DoubleVar(value=0.62)
        self.background_opacity_slider = ttk.Scale(
            circle_frame, from_=0.0, to=1.00, variable=self.background_opacity_var,
            orient="horizontal", command=self._background_opacity_changed,
        )
        self.background_opacity_slider.grid(row=4, column=1, pady=3, sticky="ew")
        self.background_opacity_text = ttk.Label(circle_frame, text="62%")
        self.background_opacity_text.grid(row=4, column=2, padx=(6, 0), pady=3, sticky="w")

        self.show_numbers_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            circle_frame, text="显示槽位编号", variable=self.show_numbers_var,
            command=self._show_numbers_changed,
        ).grid(row=5, column=0, pady=4, sticky="w")

        self.flash_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            circle_frame, text="CD 转好时整圆高亮", variable=self.flash_var,
            command=self._notify_changed,
        ).grid(row=6, column=0, pady=4, sticky="w")

        ttk.Label(circle_frame, text="不可用状态").grid(
            row=6, column=1, padx=(4, 4), pady=4, sticky="e"
        )
        self.unavailable_mode_var = tk.StringVar(value="暗色显示")
        self.unavailable_box = ttk.Combobox(
            circle_frame, textvariable=self.unavailable_mode_var, state="readonly",
            values=list(Overlay.UNAVAILABLE_MODES), width=9,
        )
        self.unavailable_box.grid(row=6, column=2, columnspan=2, pady=4, sticky="w")
        self.unavailable_box.bind(
            "<<ComboboxSelected>>", lambda e: self._unavailable_mode_changed()
        )

        # -------- 板块二：角色头像样式 --------
        avatar_frame = ttk.LabelFrame(
            boards, text="角色头像样式（FF14 式 CD 遮罩）", padding=8,
        )
        avatar_frame.grid(row=0, column=1, sticky="nsew", padx=(4, 0))
        avatar_frame.columnconfigure(1, weight=1)

        ttk.Button(
            avatar_frame, text="抓取头像", command=self._capture_avatars
        ).grid(row=0, column=0, pady=3, sticky="w")
        ttk.Button(
            avatar_frame, text="清除头像", command=self._clear_avatars
        ).grid(row=0, column=1, padx=8, pady=3, sticky="w")
        ttk.Label(
            avatar_frame,
            text="进入角色界面后点击右下角切换视图，之后再点击抓取",
            foreground="#666666",
            wraplength=190,
        ).grid(row=1, column=0, columnspan=3, pady=(6, 0), sticky="w")

        ttk.Label(
            hud_tab,
            text="提示：左键拖拽移动 HUD；Ctrl+滚轮调整大小；双击抓取游戏头像。",
            foreground="#666666",
        ).grid(row=2, column=0, columnspan=4, pady=(4, 0), sticky="w")

        hud_tab.columnconfigure(0, weight=1)

        # -------- Notify tab --------
        self.sound_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            notify_tab, text="提示音", variable=self.sound_var,
            command=self._notify_changed,
        ).grid(row=0, column=0, padx=4, pady=12, sticky="w")

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
            text="整圆高亮仅是 READY 后的视觉提示；不会参与 READY 判定。",
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
            text="alpha5：修正连携条存在性 ROI，明确排除脱战仍存在的 HP 条。",
        ).grid(row=3, column=0, pady=6, sticky="w")

        # -------- HUD visibility options --------
        self.hide_hud_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            advanced_tab,
            text="隐藏 HUD（保留检测与提示音，仅隐藏悬浮窗）",
            variable=self.hide_hud_var,
            command=self._settings_changed,
        ).grid(row=4, column=0, columnspan=3, pady=(14, 2), sticky="w")

        self.show_in_combat_only_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            advanced_tab,
            text="仅在战斗中显示 HUD（战斗检测控制显隐）",
            variable=self.show_in_combat_only_var,
            command=self._settings_changed,
        ).grid(row=5, column=0, columnspan=3, pady=(10, 2), sticky="w")
        ttk.Label(
            advanced_tab,
            text="取消勾选后，只要游戏在前台就始终显示 HUD。",
            foreground="#666666",
        ).grid(row=6, column=0, columnspan=4, pady=(2, 0), sticky="w")

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

        input_layout = s.get("input_layout", INPUT_LAYOUT_KEYBOARD)
        if input_layout in LEGACY_KEYBOARD_LAYOUTS:
            input_layout = INPUT_LAYOUT_KEYBOARD
        if input_layout in LEGACY_CONTROLLER_LAYOUTS:
            input_layout = INPUT_LAYOUT_CONTROLLER
        if input_layout not in INPUT_LAYOUTS:
            input_layout = INPUT_LAYOUT_KEYBOARD
        self.input_layout_var.set(input_layout)

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

        self.show_numbers_var.set(bool(s.get("show_numbers", False)))
        self.show_countdown_var.set(bool(s.get("show_countdown", True)))
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
        self.show_in_combat_only_var.set(
            bool(s.get("show_in_combat_only", True))
        )
        self.hide_hud_var.set(bool(s.get("hide_hud", False)))
        hud_style = s.get("hud_style", HUD_STYLE_CIRCLE)
        if hud_style not in HUD_STYLE_LABELS:
            hud_style = HUD_STYLE_CIRCLE
        self.hud_style_var.set(HUD_STYLE_LABELS[hud_style])
        self.overlay.set_style(hud_style)
        align = s.get("avatar_align")
        if (
            isinstance(align, (list, tuple))
            and len(align) == 4
            and all(
                isinstance(p, (list, tuple)) and len(p) == 3
                for p in align
            )
        ):
            self.avatar_align = [
                (float(p[0]), float(p[1]), float(p[2])) for p in align
            ]
        else:
            self.avatar_align = None
        self.show_debug_var.set(bool(s.get("show_debug", False)))

        self.overlay.set_scale(self.scale_var.get())
        self.overlay.set_spacing(self.spacing_var.get())
        self.overlay.set_marker_opacity(self.marker_opacity_var.get())
        self.overlay.set_background_opacity(self.background_opacity_var.get())
        self.overlay.set_show_numbers(self.show_numbers_var.get())
        self.overlay.set_show_countdown(self.show_countdown_var.get())
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

    def _show_countdown_changed(self):
        self.overlay.set_show_countdown(self.show_countdown_var.get())
        self._save_settings()

    def _unavailable_mode_changed(self):
        self.overlay.set_unavailable_mode(self.unavailable_mode_var.get())
        self._save_settings()

    def _hud_style_value(self):
        return HUD_STYLE_NAMES.get(self.hud_style_var.get(), HUD_STYLE_CIRCLE)

    def _hud_style_changed(self):
        self.overlay.set_style(self._hud_style_value())
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
        self.input_layout_var.set(INPUT_LAYOUT_KEYBOARD)

        self.position_var.set(d["hud_position"])
        self.overlay.custom_rel = None
        self.scale_var.set(d["hud_scale"])
        self.spacing_var.set(d["hud_spacing"])
        self.marker_opacity_var.set(d["marker_opacity"])
        self.background_opacity_var.set(d["background_opacity"])
        self.show_numbers_var.set(d["show_numbers"])
        self.show_countdown_var.set(d["show_countdown"])
        self.unavailable_mode_var.set(d["unavailable_mode"])

        self.slot_color_names = list(DEFAULT_SLOT_COLOR_NAMES)
        self.slot_colors = [COLOR_PRESETS[name] for name in self.slot_color_names]
        for i, var in enumerate(self.color_vars):
            var.set(self.slot_color_names[i])

        self.sound_var.set(False)
        self.flash_var.set(True)
        self.pitch_by_slot_var.set(True)
        self.silent_first_var.set(True)
        self.show_in_combat_only_var.set(True)
        self.hide_hud_var.set(False)
        self.hud_style_var.set(HUD_STYLE_LABELS[HUD_STYLE_CIRCLE])
        self.overlay.set_style(HUD_STYLE_CIRCLE)
        self.avatar_align = None
        self.show_debug_var.set(False)

        self.overlay.set_position("中央偏下")
        self.overlay.set_scale(1.0)
        self.overlay.set_spacing(1.0)
        self.overlay.set_marker_opacity(1.0)
        self.overlay.set_background_opacity(0.62)
        self.overlay.set_show_numbers(False)
        self.overlay.set_show_countdown(True)
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

    def _on_hud_drag_end(self, custom_rel):
        self.position_var.set("自定义")
        self._save_settings()

    def _on_hud_scale_changed(self, value):
        # Ctrl+滚轮缩放后同步滑块显示并保存，与滑块调节保持一致。
        self.scale_var.set(value)
        self.scale_text.config(text=f"{int(round(value * 100))}%")
        self._save_settings()

    # ---------------- v2.4 avatar capture ----------------

    def _avatar_dir(self):
        d = os.path.join(self.SETTINGS_DIR, "avatars")
        try:
            os.makedirs(d, exist_ok=True)
        except Exception:
            pass
        return d

    def _avatar_path(self, idx):
        return os.path.join(self._avatar_dir(), f"slot_{idx}.png")

    def _load_avatars(self):
        """启动时加载已保存的头像为 RGBA 数据（主线程调用）。"""
        photos = []
        for i in range(4):
            av = None
            try:
                path = self._avatar_path(i)
                if os.path.exists(path):
                    with open(path, "rb") as f:
                        png = f.read()
                    av = decode_png_to_rgba(png)
            except Exception:
                av = None
            photos.append(av)
        self.overlay.set_avatars(photos)

    def _ensure_game_foreground(self):
        """确保游戏窗口在前台；悬浮窗/对准器抢走前台时自动把游戏拉回。

        本进程在前台时调用 SetForegroundWindow 会被 Windows 允许，
        因此双击悬浮窗或点击对准器后能直接把游戏窗口拉回前台。
        """
        if is_endfield_foreground():
            return True
        hwnd = find_endfield_window()
        if not hwnd:
            return False
        try:
            user32.SetForegroundWindow(hwnd)
        except Exception:
            pass
        # 前台切换是异步的；这里只轮询，不调用 root.update()，
        # 避免中途触发 _poll 改变悬浮窗可见状态。
        deadline = time.monotonic() + 0.8
        while time.monotonic() < deadline:
            if is_endfield_foreground():
                return True
            time.sleep(0.03)
        return is_endfield_foreground()

    def _capture_avatars(self, positions=None):
        """双击悬浮窗/抓取按钮触发：按固定坐标抓取四个头像。

        位置来自已保存的对准坐标（参考系 [cx, cy, r]），未保存时使用
        内置默认值；与连携 HUD 检测界面完全独立。不再支持手动对准，
        抓取中心即圆框/坐标中心。
        """
        if positions is None:
            positions = self.avatar_align
        if not positions:
            # 固定坐标：无自定义值时使用内置默认并落盘保存
            positions = [tuple(p) for p in AVATAR_ALIGN_DEFAULT]
            self.avatar_align = positions
            self._save_settings()

        if not self._ensure_game_foreground():
            messagebox.showinfo(
                "提示",
                "未找到可见的《终末地》游戏窗口。\n"
                "请先打开游戏并显示头像界面（主界面/编队界面），"
                "再抓取头像。",
            )
            return

        # 抓取前临时隐藏悬浮窗，避免其盖住头像区域；
        # 抓取后按原可见状态恢复，_poll 随后会按战斗状态再次同步。
        was_visible = self.overlay._visible
        if was_visible:
            self.overlay.hide()
        try:
            self.root.update()
        except Exception:
            pass

        mon = self.monitor()
        sx = mon["width"] / REF_W
        sy = mon["height"] / REF_H
        rects = []
        for (cx_ref, cy_ref, r_ref) in positions:
            cx = mon["left"] + cx_ref * sx
            cy = mon["top"] + cy_ref * sy
            r = r_ref * sx
            d = int(2 * r + 6)
            rects.append({
                "left": int(cx - d / 2),
                "top": int(cy - d / 2),
                "width": d,
                "height": d,
                "radius": r,
            })

        cap = None
        try:
            cap = GDICapture()
            photos = []
            for i, rr in enumerate(rects):
                raw = cap.grab_bgra(
                    rr["left"], rr["top"], rr["width"], rr["height"]
                )
                rgba = bgra_to_circle_rgba(
                    raw, rr["width"], rr["height"], radius=rr["radius"]
                )
                if rgba is None:
                    photos.append(None)
                    continue
                w, h = rr["width"], rr["height"]
                try:
                    png = _encode_rgba_png(
                        w, h, rgba_to_scanlines(rgba, w, h)
                    )
                    with open(self._avatar_path(i), "wb") as f:
                        f.write(png)
                except Exception:
                    pass
                photos.append((rgba, w, h))
            self.overlay.set_avatars(photos)
            count = sum(1 for p in photos if p is not None)
            self.status.set(f"头像抓取完成：{count}/4 个槽位")
            if count:
                self._show_avatar_preview(photos)
        except Exception:
            self.status.set("头像抓取失败，请重试")
        finally:
            if cap is not None:
                cap.close()
            if was_visible:
                try:
                    self.overlay.show()
                except Exception:
                    pass

    def _clear_avatars(self):
        for i in range(4):
            try:
                path = self._avatar_path(i)
                if os.path.exists(path):
                    os.remove(path)
            except Exception:
                pass
        self.overlay.clear_avatars()
        self.status.set("已清除头像")

    def _show_avatar_preview(self, photos):
        """抓取后放大预览 4 个头像，方便判断是否对准（无需切回游戏）。"""
        try:
            win = tk.Toplevel(self.root)
            win.title("头像抓取结果")
            win.attributes("-topmost", True)
            ttk.Label(
                win,
                text="抓取结果（不满意的槽位可重新对准再抓一次）：",
            ).pack(padx=12, pady=(10, 4))

            frame = ttk.Frame(win)
            frame.pack(padx=12, pady=4)
            for i, av in enumerate(photos):
                if av is None:
                    continue
                rgba, w, h = av
                try:
                    png = _encode_rgba_png(
                        w, h, rgba_to_scanlines(rgba, w, h)
                    )
                    disp = tk.PhotoImage(data=png)
                except Exception:
                    continue
                if disp.width() < 96:
                    z = max(1, int(round(96.0 / max(1, disp.width()))))
                    disp = disp.zoom(z)
                cell = ttk.Frame(frame)
                cell.pack(side="left", padx=8)
                lbl = ttk.Label(cell, image=disp)
                lbl.image = disp  # 保持引用防止被回收
                lbl.pack()
                ttk.Label(cell, text=f"槽位 {i + 1}").pack()

            ttk.Button(win, text="关闭", command=win.destroy).pack(pady=(6, 10))
            win.update_idletasks()
            x = (self.root.winfo_screenwidth() - win.winfo_width()) // 2
            y = (self.root.winfo_screenheight() - win.winfo_height()) // 2
            win.geometry(f"+{max(0, x)}+{max(0, y)}")
        except Exception:
            pass

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
        self.input_layout_box.config(state="readonly" if enabled else "disabled")
        for cb in self.slot_checks:
            cb.config(state=state)

    def _monitoring_status_text(self):
        mon = self.active_monitor or self.monitor()
        layout = "手柄 Beta" if self.input_layout_var.get() == INPUT_LAYOUT_CONTROLLER else "键鼠"
        return (
            f"监测中 | {mon['width']}×{mon['height']} | "
            f"{layout} | {CAPTURE_HZ:.0f} Hz"
        )

    def _sync_overlay_visibility(self):
        # Manual adjustment / preview are the only cases where HUD is allowed
        # over the settings window or another application.
        if self.overlay.adjust_mode or time.monotonic() < self.preview_until:
            self.overlay.show()
            return

        if not self.running:
            return

        # “隐藏 HUD”总开关：强制不显示悬浮窗，但检测与提示音照常。
        if self.hide_hud_var.get():
            self.overlay.hide()
            self.status.set("监测中 | HUD 已隐藏")
            return

        if is_endfield_foreground():
            # 战斗显隐开关：勾选（默认）时仅战斗期间显示 HUD；
            # 取消勾选后，游戏前台即始终显示，战斗判定不再影响显隐。
            if (
                self.show_in_combat_only_var.get()
                and not self.combat_hud_present
            ):
                self.overlay.hide()
                self.status.set("监测中 | 等待持续技力条")
            else:
                self.overlay.show()
                self.status.set(self._monitoring_status_text())
        else:
            self.overlay.hide()
            self.status.set("等待终末地前台")

    # ---------------- Run control ----------------

    def start(self, confirm_aspect=True):
        if self.running:
            return

        mon = self.monitor()
        aspect = mon["width"] / max(1, mon["height"])
        if confirm_aspect and abs(aspect - 16 / 9) > 0.03:
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
        self.combat_session_active = False
        self.combat_hud_present = False
        self.raw_center_bar_present = False
        self.center_bar_scores = [0.0, 0.0, 0.0]
        self.controller_expand = 0.0
        self.controller_anchor_score = 0.0

        # 每次重新开始监测都先清空上一次的 HUD 视觉状态。
        # 否则暂停/重新开始时，旧的 READY 白圆可能残留到新一轮监测。
        for i in range(4):
            self.overlay.set_ready(i, False)
        self.overlay.reset_runtime_progress()

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
        self.overlay.set_show_countdown(self.show_countdown_var.get())
        self.overlay.set_unavailable_mode(self.unavailable_mode_var.get())
        self.overlay.set_style(self._hud_style_value())
        self.overlay.set_position(
            self.position_var.get(),
            custom_rel=self.overlay.custom_rel
        )
        self.overlay.place(mon)
        self.active_monitor = mon

        self._set_detection_controls_enabled(False)

        self.status.set(self._monitoring_status_text())
        self._sync_overlay_visibility()

        silent_first = bool(self.silent_first_var.get())
        controller_mode = (
            self.input_layout_var.get() == INPUT_LAYOUT_CONTROLLER
        )

        self.worker = threading.Thread(
            target=self._capture_loop,
            args=(mon, enabled, silent_first, controller_mode),
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
        self.overlay.set_show_countdown(self.show_countdown_var.get())
        self.overlay.set_unavailable_mode(self.unavailable_mode_var.get())
        self.overlay.set_style(self._hud_style_value())
        self.overlay.set_position(
            self.position_var.get(),
            custom_rel=self.overlay.custom_rel
        )
        self.overlay.place(self.monitor())
        self.preview_until = time.monotonic() + 2.4

        if self.running:
            # Running preview only forces visibility briefly; it never changes
            # READY/progress states.
            return

        # alpha5 pure UI preview:
        # 1: 15%, unknown seconds
        # 2: 42%, learned 12s -> ~7s remaining
        # 3: 76%, learned 12s -> ~3s remaining
        # 4: READY + whole-circle pulse
        self.overlay.reset_runtime_progress()
        preview_progress = [0.15, 0.42, 0.76, 1.0]
        preview_remaining = [None, 7.0, 3.0, 0.0]

        for i in range(4):
            is_ready = bool(enabled[i] and i == 3)
            self.overlay.set_ready(i, is_ready)
            if enabled[i]:
                self.overlay.set_progress(
                    i,
                    preview_progress[i],
                    remaining=preview_remaining[i],
                    learned_cd=(12.0 if i in (1, 2) else None),
                    confidence=1.0,
                    active=(i != 3),
                )

        if self.flash_var.get() and enabled[3]:
            self.overlay.flash(3)

        if self.sound_var.get():
            slot = next((i for i, x in enumerate(enabled) if x), 0)
            self._play_sound(slot)

        def reset():
            if not self.running and not self.overlay.adjust_mode:
                for i in range(4):
                    self.overlay.set_ready(i, False)
                self.overlay.reset_runtime_progress()
                self.overlay.hide()
                self.preview_until = 0.0
        self.root.after(2400, reset)

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

    def _capture_loop(self, mon, enabled, silent_first, controller_mode=False):
        cap = None
        try:
            cap = GDICapture()
            if controller_mode:
                energy_base_rois = [
                    scaled_roi(r, mon) for r in CONTROLLER_SLOT_ENERGY_ROIS
                ]
                hp_base_rois = [
                    scaled_roi(r, mon) for r in CONTROLLER_SLOT_HP_ROIS
                ]
                death_base_rois = [
                    scaled_roi(r, mon)
                    for r in CONTROLLER_SLOT_DEATH_ICON_ROIS
                ]
                controller_input_roi = scaled_roi(
                    CONTROLLER_INPUT_GLYPH_ROI, mon
                )
                controller_expand = 0.0
                controller_glyph_candidate = None
                controller_glyph_streak = 0
                rois = list(energy_base_rois)
                hp_rois = list(hp_base_rois)
                death_icon_rois = list(death_base_rois)
            else:
                rois = [scaled_roi(r, mon) for r in SLOT_ENERGY_ROIS]
                hp_rois = [scaled_roi(r, mon) for r in SLOT_HP_ROIS]
                death_icon_rois = [
                    scaled_roi(r, mon) for r in SLOT_DEATH_ICON_ROIS
                ]
            center_bar_rois = [
                scaled_roi(r, mon) for r in CENTER_SKILL_BAR_ROIS
            ]
            center_bar_gap_rois = [
                scaled_roi(r, mon) for r in CENTER_SKILL_BAR_GAP_ROIS
            ]
            period = 1.0 / CAPTURE_HZ

            combat_session_active = False
            combat_hud_present = False
            combat_confirm_streak = 0
            combo_consumption_wake_until = 0.0
            absent_streak = 0

            while not self.stop_event.is_set():
                t0 = time.perf_counter()

                # GDI captures the visible desktop. When another app covers the
                # game, sampling those pixels would corrupt READY/death state.
                # Preserve the last known state and resume when Endfield returns.
                if not is_endfield_foreground():
                    # Do not learn a full-CD duration across an alt-tab/background
                    # gap, because we do not know whether the game kept advancing.
                    # A pending consumption wake is also transient screen evidence;
                    # never carry it across a foreground loss.
                    combo_consumption_wake_until = 0.0
                    if controller_mode:
                        controller_glyph_candidate = None
                        controller_glyph_streak = 0
                    for st in self.states:
                        st.cd_start_time = None
                        st.cd_cycle_valid = False
                        st.cd_ready_context_streak = 0
                        st.cd_ready_trusted_until = 0.0
                        st.cd_pending_fresh_start = None
                        st.remaining_seconds = None
                    self.stop_event.wait(period)
                    continue

                # Controller-only coordinate selection. The central input glyph
                # has two visually distinct endpoints: LB means fully inward;
                # the large white D-pad means fully outward. Intermediate/
                # obscured animation frames freeze all four slots. This avoids
                # feeding a guessed HP-bar offset into the protected detector.
                controller_frame_valid = True
                if controller_mode:
                    glyph_raw = cap.grab_bgra(
                        controller_input_roi["left"],
                        controller_input_roi["top"],
                        controller_input_roi["width"],
                        controller_input_roi["height"],
                    )
                    glyph_expand, lb_score, dpad_score = (
                        analyze_controller_input_glyph(
                            glyph_raw,
                            controller_input_roi["width"],
                            controller_input_roi["height"],
                        )
                    )
                    controller_frame_valid = False
                    if glyph_expand is None:
                        controller_glyph_candidate = None
                        controller_glyph_streak = 0
                    else:
                        if glyph_expand == controller_glyph_candidate:
                            controller_glyph_streak += 1
                        else:
                            controller_glyph_candidate = glyph_expand
                            controller_glyph_streak = 1
                        if (
                            controller_glyph_streak
                            >= CONTROLLER_GLYPH_SETTLE_FRAMES
                        ):
                            controller_expand = glyph_expand
                            controller_frame_valid = True

                    rois = controller_shifted_rois(
                        energy_base_rois, mon, controller_expand
                    )
                    hp_rois = controller_shifted_rois(
                        hp_base_rois, mon, controller_expand
                    )
                    death_icon_rois = controller_shifted_rois(
                        death_base_rois, mon, controller_expand
                    )
                    self.controller_expand = controller_expand
                    self.controller_anchor_score = max(lb_score, dpad_score)

                # Central skill-bar geometry controls visibility only. The stable
                # four-slot detector below always runs, exactly as before the
                # experimental lower-left presence gate was introduced.
                center_segment_rows = []
                for cr in center_bar_rois:
                    center_raw = cap.grab_bgra(
                        cr["left"], cr["top"], cr["width"], cr["height"]
                    )
                    center_segment_rows.append(
                        analyze_center_bar_edge_rows(
                            center_raw, cr["width"], cr["height"]
                        )
                    )
                center_gap_rows = []
                for gr in center_bar_gap_rois:
                    gap_raw = cap.grab_bgra(
                        gr["left"], gr["top"], gr["width"], gr["height"]
                    )
                    center_gap_rows.append(
                        analyze_center_bar_edge_rows(
                            gap_raw, gr["width"], gr["height"]
                        )
                    )
                center_scores = analyze_center_bar_structure(
                    center_segment_rows, center_gap_rows
                )

                present_segments = sum(
                    1
                    for score in center_scores
                    if score >= CENTER_BAR_SEGMENT_SCORE_MIN
                )
                raw_center_bar_present = (
                    present_segments >= CENTER_BAR_MIN_SEGMENTS
                )

                # Fresh-combat confirmation: require BOTH the center skill bar
                # and at least three lower-left combo bars that satisfy the
                # protected v2.0.1 READY brightness rules. There is deliberately
                # no center-bar-duration fallback: sustained out-of-combat skill
                # use must not wake the overlay. This evidence controls visibility
                # only and never changes any slot state.
                combat_ready_slots = 0
                if (
                    raw_center_bar_present
                    and not combat_session_active
                    and controller_frame_valid
                ):
                    for fast_rr in rois:
                        fast_raw = cap.grab_bgra(
                            fast_rr["left"], fast_rr["top"],
                            fast_rr["width"], fast_rr["height"]
                        )
                        fast_w, fast_b, fast_m, fast_l = analyze_bgra(fast_raw)
                        fast_neutral_ready = (
                            fast_w >= READY_WHITE_RATIO
                            and fast_b >= READY_BRIGHT_RATIO
                            and fast_m >= READY_MEAN_MIN
                        )
                        fast_tinted_ready = (
                            fast_l >= TINTED_READY_LUMA_RATIO
                            and fast_m >= TINTED_READY_MEAN_MIN
                        )
                        if fast_neutral_ready or fast_tinted_ready:
                            combat_ready_slots += 1
                combat_confirm_signal = (
                    raw_center_bar_present
                    and combat_ready_slots >= CENTER_BAR_REQUIRED_READY_SLOTS
                )
                combat_consumption_signal = (
                    raw_center_bar_present
                    and t0 <= combo_consumption_wake_until
                )
                fresh_combat_from_consumption = False

                if raw_center_bar_present:
                    absent_streak = 0
                    if combat_session_active:
                        # Reappearance during the same combat session is
                        # immediate; do not repeat fresh-combat confirmation.
                        combat_hud_present = True
                        combat_confirm_streak = 0
                    else:
                        if combat_consumption_signal:
                            # Active-skill engagement can expose the lower-left
                            # HUD only after one or more latched READY combo skills
                            # have already been consumed. A confirmed consumption
                            # is therefore a second, event-based fresh-combat path.
                            combat_session_active = True
                            combat_hud_present = True
                            combat_confirm_streak = 0
                            combo_consumption_wake_until = 0.0
                            fresh_combat_from_consumption = True
                        elif combat_confirm_signal:
                            combat_confirm_streak += 1
                        else:
                            combat_confirm_streak = 0
                        if (
                            combat_confirm_streak
                            >= CENTER_BAR_COMBAT_CONFIRM_FRAMES
                        ):
                            combat_session_active = True
                            combat_hud_present = True
                else:
                    absent_streak += 1
                    combat_confirm_streak = 0
                    if (
                        combat_hud_present
                        and absent_streak >= CENTER_BAR_VISIBILITY_HIDE_FRAMES
                    ):
                        combat_hud_present = False
                    if (
                        combat_session_active
                        and absent_streak >= CENTER_BAR_SESSION_GRACE_FRAMES
                    ):
                        combat_session_active = False

                self.combat_session_active = combat_session_active
                self.combat_hud_present = combat_hud_present
                self.raw_center_bar_present = raw_center_bar_present
                self.center_bar_scores = list(center_scores)
                center_score = sorted(center_scores, reverse=True)[1]
                for st in self.states:
                    st.center_bar_score = center_score
                    if (
                        st.cd_pending_fresh_start is not None
                        and t0 - st.cd_pending_fresh_start
                        > CENTER_BAR_CONSUMPTION_WAKE_SECONDS
                    ):
                        st.cd_pending_fresh_start = None
                    if fresh_combat_from_consumption:
                        pending_start = st.cd_pending_fresh_start
                        if (
                            pending_start is not None
                            and t0 - pending_start
                            <= CENTER_BAR_CONSUMPTION_WAKE_SECONDS
                        ):
                            # Fast fresh-combat entry may consume a combo before
                            # its lower-left READY bar becomes visible. The same
                            # protected READY -> CONSUMED event that woke combat
                            # supplies this display-only timing start.
                            st.cd_start_time = pending_start
                            st.cd_cycle_valid = True
                            st.remaining_seconds = st.learned_cd
                        st.cd_pending_fresh_start = None
                    if (
                        st.cd_cycle_valid
                        and absent_streak >= CD_LEARN_CENTER_ABSENT_FRAMES
                    ):
                        # Brief center-bar misses neither hide the overlay nor
                        # poison a 20-second learning sample. Six consecutive
                        # missing frames permanently invalidate only this cycle.
                        st.cd_cycle_valid = False
                        st.cd_start_time = None
                        st.remaining_seconds = None
                    if absent_streak >= CD_LEARN_CENTER_ABSENT_FRAMES:
                        # Do not let a READY-looking ultimate frame authorize a
                        # new countdown sample after the real HUD returns.
                        st.cd_ready_context_streak = 0
                        st.cd_ready_trusted_until = 0.0

                # If the controller portrait/HP cluster is genuinely absent,
                # sampling scenery at its expected position could look like a
                # false empty combo bar. Preserve all protected slot states for
                # this frame. Center-bar visibility/session bookkeeping above
                # remains independent and unchanged.
                if controller_mode and not controller_frame_valid:
                    # Ambiguous LB transition frames freeze screen sampling, but
                    # an already learned display clock must keep advancing.
                    for i in range(4):
                        if not enabled[i]:
                            continue
                        st = self.states[i]
                        predicted = controller_predicted_cd_display(st, t0)
                        if predicted is None:
                            continue
                        visual_progress, remaining = predicted
                        st.remaining_seconds = remaining
                        self.events.put(
                            (
                                "progress",
                                i,
                                visual_progress,
                                remaining,
                                st.learned_cd,
                                1.0,
                                True,
                            )
                        )
                    elapsed = time.perf_counter() - t0
                    if elapsed < period:
                        self.stop_event.wait(period - elapsed)
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

                    death_rr = death_icon_rois[i]
                    death_raw = cap.grab_bgra(
                        death_rr["left"], death_rr["top"],
                        death_rr["width"], death_rr["height"]
                    )
                    death_icon_score = analyze_death_icon(
                        death_raw, death_rr["width"], death_rr["height"]
                    )

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
                    st.death_icon_score = death_icon_score

                    # Display-only learning provenance. A normal in-combat
                    # timing sample may start only after this slot was visibly
                    # READY while the real center skill bar was also present.
                    # The protected READY / CONSUMED state machine below does
                    # not consume or depend on this evidence.
                    if raw_center_bar_present and raw_ready:
                        st.cd_ready_context_streak += 1
                        if (
                            st.cd_ready_context_streak
                            >= CD_LEARN_READY_CONTEXT_FRAMES
                        ):
                            st.cd_ready_trusted_until = (
                                t0 + CD_LEARN_READY_CONTEXT_SECONDS
                            )
                    else:
                        st.cd_ready_context_streak = 0

                    # ---------------- v2.1-alpha5 display-only progress ----------------
                    # This runs beside the old state machine. Its output NEVER
                    # participates in raw_ready / raw_consumed / death decisions.
                    edge_progress, edge_conf = analyze_bar_progress(
                        raw, rr["width"], rr["height"]
                    )

                    # 进度仍直接读取真实白条前沿；即使当前插件 HUD 因
                    # 中央技力条门控而隐藏，也不改变核心状态机。
                    if raw_ready:
                        progress_candidate = 1.0
                        progress_conf = 1.0
                    elif edge_progress is not None:
                        progress_candidate = edge_progress
                        progress_conf = edge_conf
                    elif raw_consumed:
                        progress_candidate = 0.0
                        progress_conf = 0.25
                    else:
                        progress_candidate = None
                        progress_conf = 0.0

                    if progress_candidate is not None:
                        update_progress_estimate(
                            st, progress_candidate, progress_conf
                        )
                    else:
                        st.progress_confidence = 0.0

                    # alpha5：进入死亡状态必须同时满足两件事：
                    # 1) 旧血条检测认为几乎没有有效彩色 HP；
                    # 2) 头像中心与固定死亡图标高度相似。
                    #
                    # 这样极低血量（血条几乎为 0）不会仅凭 HP 条被误判死亡。
                    hp_dead_candidate = (
                        alive_color_ratio < ALIVE_COLOR_RATIO_MIN
                    )
                    icon_dead_candidate = (
                        death_icon_score >= DEATH_ICON_SCORE_MIN
                    )
                    raw_dead = hp_dead_candidate and icon_dead_candidate

                    if not st.is_dead:
                        if raw_dead:
                            st.dead_streak += 1
                        else:
                            st.dead_streak = 0

                    # 死亡优先级最高。
                    # 连续多帧“低/无 HP + 明确死亡头像”后，强制清掉 READY。
                    if (
                        not st.is_dead
                        and st.dead_streak >= DEAD_CONFIRM_FRAMES
                    ):
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

                        # Display-only runtime state is reset, but learned_cd is
                        # intentionally kept until monitoring restarts.
                        st.bar_progress = 0.0
                        st.progress_samples.clear()
                        st.progress_confidence = 1.0
                        st.cd_start_time = None
                        st.cd_cycle_valid = False
                        st.cd_ready_context_streak = 0
                        st.cd_ready_trusted_until = 0.0
                        st.cd_pending_fresh_start = None
                        st.display_cd_start_time = None
                        st.remaining_seconds = None
                        st.progress_tracking_active = False
                        self.events.put(
                            ("progress", i, 0.0, None, st.learned_cd, 1.0, False)
                        )
                        continue

                    # 已确认死亡后保持锁存，直到旧 HP 检测重新看到明确彩色血条。
                    # 这样即使头像 HUD 临时淡出，也不会把 DEAD 自动清掉。
                    if (
                        st.is_dead
                        and alive_color_ratio < ALIVE_COLOR_RATIO_MIN
                    ):
                        st.bar_progress = 0.0
                        st.progress_samples.clear()
                        st.progress_confidence = 1.0
                        st.cd_start_time = None
                        st.cd_cycle_valid = False
                        st.cd_ready_context_streak = 0
                        st.cd_ready_trusted_until = 0.0
                        st.cd_pending_fresh_start = None
                        st.display_cd_start_time = None
                        st.remaining_seconds = None
                        st.progress_tracking_active = False
                        self.events.put(
                            ("progress", i, 0.0, None, st.learned_cd, 1.0, False)
                        )
                        continue

                    # 从死亡状态恢复：
                    # 继续沿用旧版“血条重新出现明确彩色填充”作为保守恢复信号。
                    # 不因为头像暂时消失/场景遮挡就自动解除 DEAD。
                    if (
                        st.is_dead
                        and alive_color_ratio >= ALIVE_COLOR_RATIO_MIN
                    ):
                        st.is_dead = False
                        st.ready_streak = 0
                        st.not_ready_streak = 0
                        st.consumed_streak = 0
                        st.confirmed_ready = False
                        st.armed = False
                        st.consumed_seen = False
                        st.seen_first_ready = False
                        st.cd_start_time = None
                        st.cd_cycle_valid = False
                        st.cd_ready_context_streak = 0
                        st.cd_ready_trusted_until = 0.0
                        st.cd_pending_fresh_start = None
                        st.display_cd_start_time = None
                        st.progress_tracking_active = False

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

                        trusted_ready_context = (
                            t0 <= st.cd_ready_trusted_until
                        )
                        if not combat_session_active:
                            # The stable READY latch plus three-frame CONSUMED
                            # confirmation is strong combat evidence. Give the
                            # center bar one second to coexist with this event;
                            # the next capture frame can then wake the overlay.
                            combo_consumption_wake_until = (
                                t0
                                + CENTER_BAR_CONSUMPTION_WAKE_SECONDS
                            )
                            st.cd_pending_fresh_start = t0
                        else:
                            st.cd_pending_fresh_start = None

                        # Countdown timing is display-only. Do not start a sample
                        # during the short center-bar flash after an out-of-combat
                        # skill press.
                        st.cd_cycle_valid = (
                            combat_session_active
                            and raw_center_bar_present
                            and trusted_ready_context
                        )
                        st.cd_start_time = (
                            t0
                            if st.cd_cycle_valid
                            else None
                        )
                        st.cd_ready_context_streak = 0
                        st.cd_ready_trusted_until = 0.0
                        st.progress_tracking_active = True
                        if controller_mode:
                            st.display_cd_start_time = t0
                        st.bar_progress = 0.0
                        st.progress_samples.clear()
                        st.progress_confidence = 1.0
                        st.remaining_seconds = st.learned_cd

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

                        # Learn from complete, confirmed in-combat cycles. Later
                        # cycles may correct an anomalous first sample.
                        if (
                            st.cd_cycle_valid
                            and st.cd_start_time is not None
                            and combat_session_active
                            and raw_center_bar_present
                        ):
                            observed = time.perf_counter() - st.cd_start_time
                            if update_cd_learning(st, observed):
                                self.events.put(
                                    ("cd_learned", i, st.learned_cd)
                                )
                        st.cd_start_time = None
                        st.cd_cycle_valid = False
                        st.cd_pending_fresh_start = None
                        st.display_cd_start_time = None

                        st.progress_tracking_active = False
                        st.bar_progress = 1.0
                        st.progress_samples[:] = [1.0]
                        st.progress_confidence = 1.0
                        st.remaining_seconds = 0.0

                        st.armed = False
                        st.consumed_seen = False
                        self.events.put(("state", i, True))
                        if should_alert:
                            self.events.put(("alert", i))

                    # alpha5：真实连携条存在且尚未确认 READY 时，
                    # 显示层允许展示部分进度；这个标记不参与核心判定。
                    st.progress_tracking_active = (
                        not st.confirmed_ready and not st.is_dead
                    )

                    # READY/DEAD remain authoritative for endpoint visuals.
                    if st.confirmed_ready:
                        visual_progress = 1.0
                        st.remaining_seconds = 0.0
                        display_confidence = 1.0
                    else:
                        predicted = (
                            controller_predicted_cd_display(st, t0)
                            if controller_mode
                            else None
                        )
                        if predicted is not None:
                            visual_progress, remaining = predicted
                            st.remaining_seconds = remaining
                            display_confidence = 1.0
                        else:
                            if st.progress_tracking_active:
                                visual_progress = min(
                                    PROGRESS_MAX_BEFORE_READY,
                                    max(0.0, st.bar_progress),
                                )
                            else:
                                visual_progress = 0.0

                            if (
                                st.learned_cd is not None
                                and st.progress_tracking_active
                            ):
                                # Keyboard/mouse and the first controller cycle
                                # remain corrected by the real on-screen bar.
                                st.remaining_seconds = max(
                                    0.0,
                                    st.learned_cd * (1.0 - visual_progress),
                                )
                            else:
                                st.remaining_seconds = None
                            display_confidence = st.progress_confidence

                    self.events.put(
                        (
                            "progress",
                            i,
                            visual_progress,
                            st.remaining_seconds,
                            st.learned_cd,
                            display_confidence,
                            st.progress_tracking_active,
                        )
                    )

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
                                st.death_icon_score,
                                st.center_bar_score,
                                st.is_dead,
                                st.confirmed_ready,
                                st.bar_progress,
                                st.progress_confidence,
                                st.learned_cd,
                                st.remaining_seconds,
                                st.progress_tracking_active,
                                combat_hud_present,
                                raw_center_bar_present,
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

                elif kind == "progress":
                    _, idx, p, remaining, learned, confidence, active = ev
                    self.overlay.set_progress(
                        idx,
                        p,
                        remaining=remaining,
                        learned_cd=learned,
                        confidence=confidence,
                        active=active,
                    )

                elif kind == "cd_learned":
                    _, idx, learned = ev
                    # Keep status terse; detailed values are visible in Debug.
                    self.status.set(
                        f"槽位 {idx + 1} 已学习完整 CD：{learned:.1f}s"
                    )

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
                    for (
                        idx, w, b, m, l, a, dscore, cscore, dead, ready,
                        p, pc, learned, remaining, tracking, combat, center_raw
                    ) in rows:
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

                        learned_text = "--" if learned is None else f"{learned:4.1f}"
                        remain_text = "--" if remaining is None else f"{remaining:4.1f}"
                        observation_text = ",".join(
                            f"{value:.1f}{decision[0].upper()}"
                            for value, decision in st.cd_observations[-5:]
                        ) or "--"
                        fast_text = ",".join(
                            f"{value:.1f}"
                            for value in st.cd_fast_samples
                        ) or "--"

                        parts.append(
                            f"S{idx+1} {phase:5} "
                            f"Combat={int(combat)}/{int(center_raw)} C={cscore:4.2f} "
                            f"P={p:4.0%} C={pc:3.0%} X={int(tracking)} "
                            f"T={learned_text}s R={remain_text}s "
                            f"Hist={observation_text} Fast={fast_text} "
                            f"W={w:4.0%} B={b:4.0%} L={l:4.0%} "
                            f"A={a:4.1%} D={dscore:4.2f} M={m:5.0f}"
                        )
                    layout_debug = ""
                    if self.input_layout_var.get() == INPUT_LAYOUT_CONTROLLER:
                        layout_debug = (
                            f"PAD Glyph={self.controller_anchor_score:.2f} "
                            f"Expand={self.controller_expand:.2f} | "
                        )
                    self.debug.set(
                        layout_debug + (" | ".join(parts) if parts else "无监测槽位")
                    )

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
