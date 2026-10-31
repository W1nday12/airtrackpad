"""
Mouse and keyboard input controller for Windows using low-level SendInput API.
Provides near-zero latency, sub-pixel mouse precision, scrolling, and keyboard actions.
Includes support for all Apple macOS gestures mapped to Windows.
"""

import ctypes
from ctypes import wintypes
import time

user32 = ctypes.windll.user32

# Mouse flags
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_MIDDLEDOWN = 0x0020
MOUSEEVENTF_MIDDLEUP = 0x0040
MOUSEEVENTF_WHEEL = 0x0800
MOUSEEVENTF_HWHEEL = 0x1000

# One wheel notch is 120 units for MOUSEEVENTF_WHEEL / MOUSEEVENTF_HWHEEL.
WHEEL_DELTA = 120

# Emit scroll in thirds of a notch so scrolling is continuous rather than
# arriving as full clicks. Windows (and Chromium apps) handle sub-notch data.
WHEEL_SUBSTEP = WHEEL_DELTA // 3

# ULONG_PTR is pointer-sized (8 bytes on 64-bit Windows).
ULONG_PTR = ctypes.c_size_t

# Keyboard flags
KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004

# Virtual-Key codes
VK_LWIN = 0x5B
VK_TAB = 0x09
VK_SHIFT = 0x10
VK_CONTROL = 0x11
VK_LMENU = 0x12     # Alt
VK_RETURN = 0x0D
VK_BACK = 0x08
VK_SPACE = 0x20
VK_ESCAPE = 0x1B
VK_LEFT = 0x25
VK_UP = 0x26
VK_RIGHT = 0x27
VK_DOWN = 0x28
VK_DELETE = 0x2E
VK_D = 0x44
VK_N = 0x4E
VK_R = 0x52
VK_S = 0x53
VK_0 = 0x30
VK_OEM_PLUS = 0xBB

# Media keys
VK_VOLUME_MUTE = 0xAD
VK_VOLUME_DOWN = 0xAE
VK_VOLUME_UP = 0xAF
VK_MEDIA_PLAY_PAUSE = 0xB3
VK_MEDIA_NEXT_TRACK = 0xB0
VK_MEDIA_PREV_TRACK = 0xB1


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ('dx', wintypes.LONG),
        ('dy', wintypes.LONG),
        ('mouseData', wintypes.DWORD),
        ('dwFlags', wintypes.DWORD),
        ('time', wintypes.DWORD),
        ('dwExtraInfo', ULONG_PTR),
    ]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ('wVk', wintypes.WORD),
        ('wScan', wintypes.WORD),
        ('dwFlags', wintypes.DWORD),
        ('time', wintypes.DWORD),
        ('dwExtraInfo', ULONG_PTR),
    ]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ('uMsg', wintypes.DWORD),
        ('wParamL', wintypes.WORD),
        ('wParamH', wintypes.WORD),
    ]


class INPUT(ctypes.Structure):
    class _INPUT(ctypes.Union):
        _fields_ = [
            ('mi', MOUSEINPUT),
            ('ki', KEYBDINPUT),
            ('hi', HARDWAREINPUT),
        ]
    _anonymous_ = ('_input',)
    _fields_ = [
        ('type', wintypes.DWORD),
        ('_input', _INPUT),
    ]


# Explicit prototypes prevent 64-bit handle/pointer truncation.
user32.SendInput.argtypes = (wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int)
user32.SendInput.restype = wintypes.UINT

user32.IsWindowVisible.argtypes = (wintypes.HWND,)
user32.GetWindowTextLengthW.argtypes = (wintypes.HWND,)
user32.GetWindowTextLengthW.restype = ctypes.c_int
user32.GetWindowTextW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
user32.SetForegroundWindow.argtypes = (wintypes.HWND,)
user32.SetForegroundWindow.restype = wintypes.BOOL
user32.ShowWindow.argtypes = (wintypes.HWND, ctypes.c_int)
user32.IsIconic.argtypes = (wintypes.HWND,)
user32.GetForegroundWindow.restype = wintypes.HWND
user32.GetWindowThreadProcessId.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
user32.GetWindowThreadProcessId.restype = wintypes.DWORD
user32.AttachThreadInput.argtypes = (wintypes.DWORD, wintypes.DWORD, wintypes.BOOL)
user32.SwitchToThisWindow.argtypes = (wintypes.HWND, wintypes.BOOL)
user32.IsWindow.argtypes = (wintypes.HWND,)
user32.IsWindow.restype = wintypes.BOOL
user32.BringWindowToTop.argtypes = (wintypes.HWND,)

user32.GetCursorPos.argtypes = (ctypes.POINTER(wintypes.POINT),)
user32.GetCursorPos.restype = wintypes.BOOL
user32.SetCursorPos.argtypes = (ctypes.c_int, ctypes.c_int)
user32.SetCursorPos.restype = wintypes.BOOL
user32.BringWindowToTop.restype = wintypes.BOOL


class WindowsInputController:
    def __init__(self):
        # Accumulators for fractional movements
        self.acc_x = 0.0
        self.acc_y = 0.0
        self.scroll_acc_y = 0.0
        self.scroll_acc_x = 0.0
        self.zoom_acc = 0.0
        self.is_left_down = False
        self.is_right_down = False
        self.smart_zoom_state = False

    def move(self, dx: float, dy: float):
        """Move cursor by relative dx, dy.

        Instead of relative SendInput moves (which Windows passes through its
        "enhance pointer precision" curve — small, uneven deltas get amplified
        into visible jitter), position the cursor exactly. Fractional pixels
        are accumulated so slow movement is still smooth.
        """
        self.acc_x += dx
        self.acc_y += dy

        step_x = int(self.acc_x)
        step_y = int(self.acc_y)
        if step_x == 0 and step_y == 0:
            return

        self.acc_x -= step_x
        self.acc_y -= step_y

        point = wintypes.POINT()
        if not user32.GetCursorPos(ctypes.byref(point)):
            return
        user32.SetCursorPos(point.x + step_x, point.y + step_y)

    def mouse_down(self, button: str = "left"):
        """Press mouse button down."""
        flag = 0
        if button == "left":
            flag = MOUSEEVENTF_LEFTDOWN
            self.is_left_down = True
        elif button == "right":
            flag = MOUSEEVENTF_RIGHTDOWN
            self.is_right_down = True
        elif button == "middle":
            flag = MOUSEEVENTF_MIDDLEDOWN

        if flag:
            inp = INPUT()
            inp.type = 0
            inp.mi.dwFlags = flag
            user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))

    def mouse_up(self, button: str = "left"):
        """Release mouse button."""
        flag = 0
        if button == "left":
            flag = MOUSEEVENTF_LEFTUP
            self.is_left_down = False
        elif button == "right":
            flag = MOUSEEVENTF_RIGHTUP
            self.is_right_down = False
        elif button == "middle":
            flag = MOUSEEVENTF_MIDDLEUP

        if flag:
            inp = INPUT()
            inp.type = 0
            inp.mi.dwFlags = flag
            user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))

    def click(self, button: str = "left"):
        """Click mouse button (down + up)."""
        self.mouse_down(button)
        time.sleep(0.01)
        self.mouse_up(button)

    def double_click(self, button: str = "left"):
        """Double click mouse button."""
        self.click(button)
        time.sleep(0.05)
        self.click(button)

    def scroll(self, delta_x: float, delta_y: float):
        """Scroll wheel event.

        Deltas are measured in wheel notches: 1.0 == one 120-unit WHEEL_DELTA.
        Windows apps accept wheel data in sub-notch steps, so we emit a third
        of a notch (~40 units) instead of one full 120-unit click. That keeps
        scrolling continuous instead of arriving in visible jumps.
        """
        self.scroll_acc_y += delta_y * WHEEL_DELTA
        self.scroll_acc_x += delta_x * WHEEL_DELTA

        step_y = int(self.scroll_acc_y / WHEEL_SUBSTEP)
        step_x = int(self.scroll_acc_x / WHEEL_SUBSTEP)

        if step_y != 0:
            self.scroll_acc_y -= step_y * WHEEL_SUBSTEP
            inp = INPUT()
            inp.type = 0
            inp.mi.mouseData = ctypes.c_ulong(int(step_y * WHEEL_SUBSTEP)).value
            inp.mi.dwFlags = MOUSEEVENTF_WHEEL
            user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))

        if step_x != 0:
            self.scroll_acc_x -= step_x * WHEEL_SUBSTEP
            inp = INPUT()
            inp.type = 0
            inp.mi.mouseData = ctypes.c_ulong(int(step_x * WHEEL_SUBSTEP)).value
            inp.mi.dwFlags = MOUSEEVENTF_HWHEEL
            user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))

    def zoom(self, delta: float):
        """Pinch-to-zoom simulation using Ctrl + Wheel."""
        self.zoom_acc += delta
        # One zoom notch every ~13 px of pinch travel (delta is pre-scaled
        # by the browser side). Sending multiple notches at once keeps fast
        # pinches responsive.
        step = int(self.zoom_acc / 20)
        if step == 0:
            return

        self.zoom_acc -= step * 20

        # Press Ctrl
        inp_ctrl = INPUT()
        inp_ctrl.type = 1
        inp_ctrl.ki.wVk = VK_CONTROL
        user32.SendInput(1, ctypes.byref(inp_ctrl), ctypes.sizeof(INPUT))

        # Wheel
        wheel_val = step * WHEEL_DELTA
        inp_wheel = INPUT()
        inp_wheel.type = 0
        inp_wheel.mi.mouseData = ctypes.c_ulong(wheel_val).value
        inp_wheel.mi.dwFlags = MOUSEEVENTF_WHEEL
        user32.SendInput(1, ctypes.byref(inp_wheel), ctypes.sizeof(INPUT))

        # Release Ctrl
        inp_ctrl.ki.dwFlags = KEYEVENTF_KEYUP
        user32.SendInput(1, ctypes.byref(inp_ctrl), ctypes.sizeof(INPUT))

    def smart_zoom(self):
        """Smart zoom toggle (Ctrl + '+' or Ctrl + '0')."""
        if not self.smart_zoom_state:
            # Zoom in
            self.send_combo(VK_CONTROL, VK_OEM_PLUS)
            self.smart_zoom_state = True
        else:
            # Reset zoom to 100%
            self.send_combo(VK_CONTROL, VK_0)
            self.smart_zoom_state = False

    def send_key(self, vk: int, extended: bool = False):
        """Send a single virtual key press and release."""
        ext_flag = KEYEVENTF_EXTENDEDKEY if extended else 0

        inp = INPUT()
        inp.type = 1
        inp.ki.wVk = vk
        inp.ki.dwFlags = ext_flag
        user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))

        inp.ki.dwFlags = ext_flag | KEYEVENTF_KEYUP
        user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))

    def send_combo(self, *vks):
        """Send keyboard combination (e.g. Win + Tab)."""
        for vk in vks:
            inp = INPUT()
            inp.type = 1
            inp.ki.wVk = vk
            user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))

        time.sleep(0.02)

        for vk in reversed(vks):
            inp = INPUT()
            inp.type = 1
            inp.ki.wVk = vk
            inp.ki.dwFlags = KEYEVENTF_KEYUP
            user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))

    def type_text(self, text: str):
        """Type unicode string directly into active window."""
        # Encode as UTF-16 so non-BMP characters (emoji) are sent as
        # surrogate pairs instead of a single invalid code unit.
        data = text.encode("utf-16-le", errors="replace")
        for i in range(0, len(data), 2):
            code = data[i] | (data[i + 1] << 8)
            inp = INPUT()
            inp.type = 1
            inp.ki.wScan = code
            inp.ki.dwFlags = KEYEVENTF_UNICODE
            user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))

            inp.ki.dwFlags = KEYEVENTF_UNICODE | KEYEVENTF_KEYUP
            user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))

    def execute_action(self, action: str):
        """Execute all mapped Apple macOS gestures for Windows."""
        # 1. Mission Control (4 fingers swipe up)
        if action == "mission_control":
            self.send_combo(VK_LWIN, VK_TAB)

        # 2. Exposé (4 fingers swipe down)
        elif action == "expose":
            self.send_combo(VK_LMENU, VK_TAB)

        # 3. Show Desktop (Pinch spread thumb + 3 fingers / Win + D)
        elif action == "show_desktop":
            self.send_combo(VK_LWIN, VK_D)

        # 4. Applications / Launchpad (Pinch together thumb + 3 fingers / Win key)
        elif action == "launchpad":
            self.send_key(VK_LWIN)

        # 5. Fullscreen / Virtual Desktops switch (4 fingers swipe left / right)
        elif action == "desktop_right":
            self.send_combo(VK_CONTROL, VK_LWIN, VK_RIGHT)
        elif action == "desktop_left":
            self.send_combo(VK_CONTROL, VK_LWIN, VK_LEFT)

        # 6. Page navigation (2 fingers swipe left / right)
        elif action == "page_back":
            self.send_combo(VK_LMENU, VK_LEFT)
        elif action == "page_forward":
            self.send_combo(VK_LMENU, VK_RIGHT)

        # 7. Notification Center (2 fingers swipe left from right edge)
        elif action == "notification_center":
            self.send_combo(VK_LWIN, VK_N)

        # 8. Look up & Search (3 fingers tap)
        elif action == "search":
            self.send_combo(VK_LWIN, VK_S)

        # 9. Smart Zoom (2 fingers double tap)
        elif action == "smart_zoom":
            self.smart_zoom()

        # 10. Rotate (2 fingers rotate)
        elif action == "rotate_cw":
            self.send_combo(VK_CONTROL, VK_R)
        elif action == "rotate_ccw":
            self.send_combo(VK_CONTROL, VK_SHIFT, VK_R)

        # Middle click
        elif action == "middle_click":
            self.click("middle")

        # Media & common shortcuts
        elif action == "task_view":
            self.send_combo(VK_LWIN, VK_TAB)
        elif action == "alt_tab":
            self.send_combo(VK_LMENU, VK_TAB)
        elif action == "volume_up":
            self.send_key(VK_VOLUME_UP, extended=True)
        elif action == "volume_down":
            self.send_key(VK_VOLUME_DOWN, extended=True)
        elif action == "volume_mute":
            self.send_key(VK_VOLUME_MUTE, extended=True)
        elif action == "play_pause":
            self.send_key(VK_MEDIA_PLAY_PAUSE, extended=True)
        elif action == "enter":
            self.send_key(VK_RETURN)
        elif action == "backspace":
            self.send_key(VK_BACK)
        elif action == "delete":
            self.send_key(VK_DELETE, extended=True)
        elif action == "space":
            self.send_key(VK_SPACE)
        elif action == "escape":
            self.send_key(VK_ESCAPE)

    def get_open_windows(self):
        windows = []
        seen_titles = set()

        @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def enum_windows_callback(hwnd, lparam):
            if user32.IsWindowVisible(hwnd):
                length = user32.GetWindowTextLengthW(hwnd)
                if length > 0:
                    buff = ctypes.create_unicode_buffer(length + 1)
                    user32.GetWindowTextW(hwnd, buff, length + 1)
                    title = buff.value.strip()
                    if title and title not in ("Program Manager", "Settings", "Windows Input Experience", "Task Switching", "Microsoft Text Input Application", "AirTrackpad Server"):
                        if title not in seen_titles:
                            seen_titles.add(title)
                            windows.append({"hwnd": hwnd, "title": title})
            return True

        user32.EnumWindows(enum_windows_callback, 0)
        return windows[:10]

    def focus_window(self, hwnd: int) -> str:
        """Bring a window to the foreground, or minimize it if already there.

        Returns "focused", "minimized" or "none" so the phone can show the
        right state and HUD message.
        """
        try:
            hwnd = int(hwnd)
            if not hwnd or not user32.IsWindow(hwnd):
                return "none"

            foreground = user32.GetForegroundWindow()
            if foreground and int(foreground) == hwnd and not user32.IsIconic(hwnd):
                # Second tap on the active app: behave like its taskbar button.
                user32.ShowWindow(hwnd, 6)   # SW_MINIMIZE
                return "minimized"

            if user32.IsIconic(hwnd):
                user32.ShowWindow(hwnd, 9)   # SW_RESTORE
            else:
                user32.ShowWindow(hwnd, 5)   # SW_SHOW

            if user32.SetForegroundWindow(hwnd):
                return "focused"

            # Windows can refuse foreground changes from background processes.
            # Attaching to the foreground thread's input queue bypasses that.
            fg = user32.GetForegroundWindow()
            fg_tid = user32.GetWindowThreadProcessId(fg, None) if fg else 0
            cur_tid = ctypes.windll.kernel32.GetCurrentThreadId()
            if fg_tid and fg_tid != cur_tid:
                user32.AttachThreadInput(cur_tid, fg_tid, True)
                try:
                    user32.BringWindowToTop(hwnd)
                    user32.SetForegroundWindow(hwnd)
                finally:
                    user32.AttachThreadInput(cur_tid, fg_tid, False)
            else:
                user32.BringWindowToTop(hwnd)
                user32.SwitchToThisWindow(hwnd, True)
            return "focused"
        except Exception:
            return "none"
