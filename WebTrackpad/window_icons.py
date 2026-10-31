"""List visible Windows applications together with their real taskbar icons.

The phone UI asks the server for the windows that are currently open. This
module enumerates them and converts each window icon into a PNG data URL so
the browser can draw exactly the same icon Windows shows in the taskbar.
"""

import ctypes
import hashlib
import io
import os
import threading
import time
from ctypes import wintypes
from pathlib import Path

try:
    from PIL import Image, ImageChops
except Exception:  # Pillow is optional: the UI falls back to letter badges.
    Image = None
    ImageChops = None

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
shell32 = ctypes.WinDLL("shell32", use_last_error=True)
ole32 = ctypes.WinDLL("ole32", use_last_error=True)

WM_GETICON = 0x007F
ICON_SMALL = 0
ICON_BIG = 1
ICON_SMALL2 = 2
GCLP_HICON = -14
GCLP_HICONSM = -34
DI_NORMAL = 0x0003
DIB_RGB_COLORS = 0
BI_RGB = 0
SMTO_ABORTIFHUNG = 0x0002
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
ICON_SIZE = 256

# Windows that are not real applications or that only mirror another window.
EXCLUDED_TITLES = {
    "Program Manager",
    "Windows Input Experience",
    "Task Switching",
    "Microsoft Text Input Application",
    "AirTrackpad Server",
}


class _BITMAPINFOHEADER(ctypes.Structure):
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


class _BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", _BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]


class _SHFILEINFOW(ctypes.Structure):
    _fields_ = [
        ("hIcon", wintypes.HICON),
        ("iIcon", ctypes.c_int),
        ("dwAttributes", wintypes.DWORD),
        ("szDisplayName", wintypes.WCHAR * 260),
        ("szTypeName", wintypes.WCHAR * 80),
    ]


user32.EnumWindows.argtypes = (ctypes.c_void_p, wintypes.LPARAM)
user32.IsWindowVisible.argtypes = (wintypes.HWND,)
user32.IsWindowVisible.restype = wintypes.BOOL
user32.GetWindowTextLengthW.argtypes = (wintypes.HWND,)
user32.GetWindowTextLengthW.restype = ctypes.c_int
user32.GetWindowTextW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
user32.GetWindowTextW.restype = ctypes.c_int
user32.SendMessageTimeoutW.argtypes = (
    wintypes.HWND,
    wintypes.UINT,
    wintypes.WPARAM,
    wintypes.LPARAM,
    wintypes.UINT,
    wintypes.UINT,
    ctypes.POINTER(ctypes.c_size_t),
)
user32.SendMessageTimeoutW.restype = ctypes.c_size_t
user32.GetClassLongPtrW.argtypes = (wintypes.HWND, ctypes.c_int)
user32.GetClassLongPtrW.restype = ctypes.c_void_p
user32.GetWindowThreadProcessId.argtypes = (
    wintypes.HWND,
    ctypes.POINTER(wintypes.DWORD),
)
user32.GetWindowThreadProcessId.restype = wintypes.DWORD
user32.PrivateExtractIconsW.argtypes = (
    wintypes.LPCWSTR,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.POINTER(wintypes.HICON),
    ctypes.POINTER(wintypes.UINT),
    wintypes.UINT,
    wintypes.UINT,
)
user32.PrivateExtractIconsW.restype = wintypes.UINT
user32.GetForegroundWindow.restype = wintypes.HWND
user32.DrawIconEx.argtypes = (
    wintypes.HDC,
    ctypes.c_int,
    ctypes.c_int,
    wintypes.HICON,
    ctypes.c_int,
    ctypes.c_int,
    wintypes.UINT,
    ctypes.c_void_p,
    wintypes.UINT,
)
user32.DrawIconEx.restype = wintypes.BOOL
user32.DestroyIcon.argtypes = (wintypes.HICON,)

gdi32.CreateCompatibleDC.argtypes = (wintypes.HDC,)
gdi32.CreateCompatibleDC.restype = wintypes.HDC
gdi32.CreateDIBSection.argtypes = (
    wintypes.HDC,
    ctypes.POINTER(_BITMAPINFO),
    wintypes.UINT,
    ctypes.POINTER(ctypes.c_void_p),
    wintypes.HANDLE,
    wintypes.DWORD,
)
gdi32.CreateDIBSection.restype = wintypes.HBITMAP
gdi32.SelectObject.argtypes = (wintypes.HDC, wintypes.HGDIOBJ)
gdi32.SelectObject.restype = wintypes.HGDIOBJ
gdi32.DeleteObject.argtypes = (wintypes.HGDIOBJ,)
gdi32.DeleteDC.argtypes = (wintypes.HDC,)

kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.QueryFullProcessImageNameW.argtypes = (
    wintypes.HANDLE,
    wintypes.DWORD,
    wintypes.LPWSTR,
    ctypes.POINTER(wintypes.DWORD),
)
kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)

shell32.ExtractIconExW.argtypes = (
    wintypes.LPCWSTR,
    ctypes.c_int,
    ctypes.POINTER(wintypes.HICON),
    ctypes.POINTER(wintypes.HICON),
    wintypes.UINT,
)
shell32.ExtractIconExW.restype = wintypes.UINT
shell32.SHGetFileInfoW.argtypes = (
    wintypes.LPCWSTR,
    wintypes.DWORD,
    ctypes.POINTER(_SHFILEINFOW),
    wintypes.UINT,
    wintypes.UINT,
)
shell32.SHGetFileInfoW.restype = ctypes.c_size_t

SHGFI_ICON = 0x000000100
SHGFI_LARGEICON = 0x000000000
SHGFI_SMALLICON = 0x000000001

# (executable, icon handle) -> content id. Icons never change while a process
# lives, so the phone only pays the conversion cost once per application.
_icon_ids: dict = {}
# content id -> PNG bytes, served over HTTP with a year-long cache.
_icon_pngs: dict = {}


def icon_png(icon_id: str) -> bytes:
    """Return the PNG for an icon id, or b"" when it is unknown."""
    return _icon_pngs.get(icon_id, b"")


def _process_path(hwnd) -> str:
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
    if not handle:
        return ""
    try:
        size = wintypes.DWORD(1024)
        buffer = ctypes.create_unicode_buffer(size.value)
        if kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            return buffer.value
        return ""
    finally:
        kernel32.CloseHandle(handle)


def _window_icon(hwnd):
    result = ctypes.c_size_t()
    for wparam in (ICON_BIG, ICON_SMALL2, ICON_SMALL):
        user32.SendMessageTimeoutW(
            hwnd, WM_GETICON, wparam, 0, SMTO_ABORTIFHUNG, 100, ctypes.byref(result)
        )
        if result.value:
            return result.value
    hicon = user32.GetClassLongPtrW(hwnd, GCLP_HICON)
    if hicon:
        return hicon
    return user32.GetClassLongPtrW(hwnd, GCLP_HICONSM)


def _exe_icon(path: str):
    if not path:
        return 0
    large = wintypes.HICON()
    small = wintypes.HICON()
    count = shell32.ExtractIconExW(
        path, 0, ctypes.byref(large), ctypes.byref(small), 1
    )
    if count and large.value:
        return large.value
    return 0


# exe path -> 256 px HICON. These handles stay alive for the server's lifetime
# (at most a few dozen applications), so they are never DestroyIcon'd.
_hi_icons: dict = {}


def _path_icon_hires(path: str, index: int = 0):
    """Return the biggest icon stored at `path`; `index` selects the group.

    WM_GETICON hands out a 32x32 icon, so drawing it into a 256x256 bitmap
    yields the blocky, pixelated tiles the phone showed. Executables normally
    embed a 256x256 icon, and PrivateExtractIcons returns it unscaled.
    """
    if not path:
        return 0
    key = (path.lower(), int(index))
    if key in _hi_icons:
        return _hi_icons[key]

    handle = 0
    large = wintypes.HICON()
    count = user32.PrivateExtractIconsW(
        path, int(index), ICON_SIZE, ICON_SIZE, ctypes.byref(large), None, 1, 0
    )
    if count and large.value:
        handle = large.value

    if len(_hi_icons) > 128:
        _hi_icons.clear()
    _hi_icons[key] = handle
    return handle


def _exe_icon_hires(path: str):
    return _path_icon_hires(path, 0)


def _hicon_to_png(hicon) -> bytes | None:
    if Image is None or not hicon:
        return None

    info = _BITMAPINFO()
    info.bmiHeader.biSize = ctypes.sizeof(_BITMAPINFOHEADER)
    info.bmiHeader.biWidth = ICON_SIZE
    info.bmiHeader.biHeight = -ICON_SIZE
    info.bmiHeader.biPlanes = 1
    info.bmiHeader.biBitCount = 32
    info.bmiHeader.biCompression = BI_RGB

    hdc = gdi32.CreateCompatibleDC(None)
    if not hdc:
        return None
    try:
        bits = ctypes.c_void_p()
        hbitmap = gdi32.CreateDIBSection(
            hdc, ctypes.byref(info), DIB_RGB_COLORS, ctypes.byref(bits), None, 0
        )
        if not hbitmap:
            return None
        old = gdi32.SelectObject(hdc, hbitmap)
        try:
            ctypes.memset(bits, 0, ICON_SIZE * ICON_SIZE * 4)
            if not user32.DrawIconEx(
                hdc, 0, 0, hicon, ICON_SIZE, ICON_SIZE, 0, None, DI_NORMAL
            ):
                return None
            raw = ctypes.string_at(bits, ICON_SIZE * ICON_SIZE * 4)
        finally:
            gdi32.SelectObject(hdc, old)
            gdi32.DeleteObject(hbitmap)
    finally:
        gdi32.DeleteDC(hdc)

    image = Image.frombuffer(
        "RGBA", (ICON_SIZE, ICON_SIZE), raw, "raw", "BGRA", 0, 1
    )

    # GDI draws icons premultiplied over black. Convert back to straight alpha
    # so the icon looks right on the panel, without halos around the edges.
    red, green, blue, alpha = image.split()
    has_partial_alpha = any(alpha.histogram()[1:255])

    if has_partial_alpha:
        # ImageChops.multiply(x, k) is x * k / 255, so k = 65025 / a turns the
        # premultiplied colour back into the original one.
        scale = alpha.point(
            [0] + [min(255, (65025 + value // 2) // value) for value in range(1, 255)] + [255]
        )
        image = Image.merge(
            "RGBA",
            (
                ImageChops.multiply(red, scale),
                ImageChops.multiply(green, scale),
                ImageChops.multiply(blue, scale),
                alpha,
            ),
        )
    else:
        # The icon has no alpha channel, so it was drawn on black: black is
        # the transparent part.
        mask = image.convert("RGB").convert("L").point(lambda value: 255 if value else 0)
        image.putalpha(mask)

    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    return buffer.getvalue()


def _register_icon(png: bytes) -> str:
    icon_id = hashlib.sha1(png).hexdigest()[:16]
    _icon_pngs[icon_id] = png
    # The phone caches these URLs for a year, so keep the ids around.
    if len(_icon_pngs) > 1024:
        _icon_pngs.clear()
        _icon_pngs[icon_id] = png
        # Cached ids would now point at PNGs that are gone; drop them so the
        # next request re-extracts and re-registers the same content ids.
        _icon_ids.clear()
        _pinned_icon_ids.clear()
    return icon_id


# Windows that host other apps' windows: their exe icon is a generic frame,
# while the window itself carries the real (smaller) application icon.
HOST_EXES = {"applicationframehost.exe"}


def _icon_for(exe: str, hicon) -> str:
    key = (exe.lower(), int(hicon or 0))
    cached = _icon_ids.get(key)
    if cached is not None:
        return cached

    exe_name = exe.rsplit("\\", 1)[-1].lower()
    png = None
    if exe_name not in HOST_EXES:
        # Preferred: the crisp 256x256 icon embedded in the executable.
        png = _hicon_to_png(_exe_icon_hires(exe))

    if not png:
        # Fall back to the window's own icon; it is at most 32x32, so it only
        # looks right because the phone draws it small.
        png = _hicon_to_png(hicon)

    if not png:
        fallback = _exe_icon(exe)
        if fallback and fallback != hicon:
            png = _hicon_to_png(fallback)
            user32.DestroyIcon(fallback)

    icon_id = _register_icon(png) if png else ""

    if len(_icon_ids) > 256:
        _icon_ids.clear()
    _icon_ids[key] = icon_id
    return icon_id


def list_open_windows(limit: int = 12) -> list:
    """Return visible top-level windows with titles and PNG icon data URLs."""
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def callback(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buffer, length + 1)
        title = buffer.value.strip()
        if title:
            found.append((hwnd, title))
        return True

    user32.EnumWindows(callback, 0)

    foreground = int(user32.GetForegroundWindow() or 0)

    windows = []
    seen_titles = set()
    for hwnd, title in found:
        if title in EXCLUDED_TITLES or title in seen_titles:
            continue
        seen_titles.add(title)
        windows.append((hwnd, title))
        if len(windows) >= limit:
            break

    result = []
    for hwnd, title in windows:
        exe = _process_path(hwnd)
        icon_id = _icon_for(exe, _window_icon(hwnd))
        result.append(
            {
                "hwnd": int(hwnd),
                "title": title,
                "exe": exe.rsplit("\\", 1)[-1] if exe else "",
                "icon_id": icon_id,
                "focused": int(hwnd) == foreground,
            }
        )
    return result


# ---------------------------------------------------------------------------
# Taskbar pinned applications
#
# The phone dock also shows what is pinned to the Windows taskbar, including
# apps that are not running. Pinned entries are .lnk files; their executable
# target is resolved through IShellLinkW (ctypes COM) because the 256px icon
# machinery only works on the .exe, not on the shortcut itself.
# ---------------------------------------------------------------------------

_APPDATA = os.environ.get("APPDATA") or os.path.expanduser(
    os.path.join("~", "AppData", "Roaming")
)
PINNED_DIR = (
    Path(_APPDATA)
    / "Microsoft"
    / "Internet Explorer"
    / "Quick Launch"
    / "User Pinned"
    / "TaskBar"
)
PINNED_TTL = 15.0  # seconds; the dock refreshes every 4s
PINNED_LIMIT = 20

# Shell-folder shortcuts (This PC / File Explorer / Проводник) have no .exe
# target; Windows itself displays %windir%\explorer.exe for them.
SHELL_FOLDER_EXES = {
    "file explorer": "%SystemRoot%\\explorer.exe",
    "проводник": "%SystemRoot%\\explorer.exe",
    "this pc": "%SystemRoot%\\explorer.exe",
    "этот компьютер": "%SystemRoot%\\explorer.exe",
}

# PNG content id cache, keyed by the icon source paths a shortcut resolves to.
# Icons are immutable per machine, so these are kept for the server's lifetime
# and registered in the shared _icon_pngs map.
_pinned_icon_ids: dict = {}

# Serialises pinned-list rebuilding: the dock list and launch requests may run
# on different threadpool threads at the same time.
_pinned_lock = threading.RLock()
_pinned_state = {
    "dir": "",
    "signature": None,
    "expires": 0.0,
    "apps": [],
    "by_id": {},  # p_<stable> -> (lnk path, display name)
}


class _GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", wintypes.DWORD),
        ("Data2", wintypes.WORD),
        ("Data3", wintypes.WORD),
        ("Data4", ctypes.c_ubyte * 8),
    ]


def _guid(text: str) -> _GUID:
    parts = text.strip("{}").split("-")
    value = _GUID()
    value.Data1 = int(parts[0], 16)
    value.Data2 = int(parts[1], 16)
    value.Data3 = int(parts[2], 16)
    for index, byte in enumerate(bytes.fromhex(parts[3] + parts[4])):
        value.Data4[index] = byte
    return value


_CLSID_SHELLLINK = _guid("00021401-0000-0000-C000-000000000046")
_IID_ISHELLLINKW = _guid("000214F9-0000-0000-C000-000000000046")
_IID_IPERSISTFILE = _guid("0000010B-0000-0000-C000-000000000046")

_COINIT_APARTMENTTHREADED = 0x2
_CLSCTX_INPROC_SERVER = 0x1
_STGM_READ = 0x0

# IShellLinkW vtable slots (after the three IUnknown entries).
_ISHELLLINK_QUERYINTERFACE = 0
_ISHELLLINK_RELEASE = 2
_ISHELLLINK_GETPATH = 3
_ISHELLLINK_GETDESCRIPTION = 6
_ISHELLLINK_GETICONLOCATION = 16
# IPersistFile vtable slot for Load().
_IPERSISTFILE_LOAD = 5

ole32.CoInitializeEx.argtypes = (ctypes.c_void_p, wintypes.DWORD)
ole32.CoInitializeEx.restype = ctypes.c_long
ole32.CoUninitialize.argtypes = ()
ole32.CoUninitialize.restype = None
ole32.CoCreateInstance.argtypes = (
    ctypes.POINTER(_GUID),
    ctypes.c_void_p,
    wintypes.DWORD,
    ctypes.POINTER(_GUID),
    ctypes.POINTER(ctypes.c_void_p),
)
ole32.CoCreateInstance.restype = ctypes.c_long


def _com_method(interface, slot: int, restype, *argtypes):
    """Return a callable for vtable `slot` of a raw COM interface pointer."""
    vtable = ctypes.cast(
        interface, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))
    )[0]
    address = vtable[slot]
    if not address:
        raise RuntimeError(f"null COM vtable slot {slot}")
    return ctypes.WINFUNCTYPE(restype, *argtypes)(address)


def _com_release(interface) -> None:
    if not interface:
        return
    try:
        _com_method(interface, _ISHELLLINK_RELEASE, ctypes.c_ulong, ctypes.c_void_p)(
            interface
        )
    except Exception:
        pass


def _read_shortcut(lnk_path) -> tuple:
    """Read a .lnk through IShellLinkW.

    Returns (target, icon_location, icon_index, description). Every field is
    empty when the shortcut cannot be read or has no executable target (for
    example the File Explorer shell-folder link, which returns S_FALSE).
    """
    empty = ("", "", 0, "")
    if not lnk_path:
        return empty

    try:
        init_hr = ole32.CoInitializeEx(None, _COINIT_APARTMENTTHREADED)
        initialized = init_hr >= 0
    except Exception:
        return empty

    try:
        shell = ctypes.c_void_p()
        hr = ole32.CoCreateInstance(
            ctypes.byref(_CLSID_SHELLLINK),
            None,
            _CLSCTX_INPROC_SERVER,
            ctypes.byref(_IID_ISHELLLINKW),
            ctypes.byref(shell),
        )
        if hr < 0 or not shell.value:
            return empty
        try:
            persist = ctypes.c_void_p()
            query = _com_method(
                shell,
                _ISHELLLINK_QUERYINTERFACE,
                ctypes.c_long,
                ctypes.c_void_p,
                ctypes.POINTER(_GUID),
                ctypes.POINTER(ctypes.c_void_p),
            )
            hr = query(shell, ctypes.byref(_IID_IPERSISTFILE), ctypes.byref(persist))
            if hr < 0 or not persist.value:
                return empty
            try:
                load = _com_method(
                    persist,
                    _IPERSISTFILE_LOAD,
                    ctypes.c_long,
                    ctypes.c_void_p,
                    wintypes.LPCWSTR,
                    wintypes.DWORD,
                )
                if load(persist, str(lnk_path), _STGM_READ) < 0:
                    return empty

                target = ctypes.create_unicode_buffer(2048)
                get_path = _com_method(
                    shell,
                    _ISHELLLINK_GETPATH,
                    ctypes.c_long,
                    ctypes.c_void_p,
                    wintypes.LPWSTR,
                    ctypes.c_int,
                    ctypes.c_void_p,
                    wintypes.DWORD,
                )
                get_path(shell, target, 2048, None, 0)

                icon_location = ctypes.create_unicode_buffer(2048)
                icon_index = ctypes.c_int(0)
                get_icon = _com_method(
                    shell,
                    _ISHELLLINK_GETICONLOCATION,
                    ctypes.c_long,
                    ctypes.c_void_p,
                    wintypes.LPWSTR,
                    ctypes.c_int,
                    ctypes.POINTER(ctypes.c_int),
                )
                get_icon(shell, icon_location, 2048, ctypes.byref(icon_index))

                description = ctypes.create_unicode_buffer(1024)
                get_description = _com_method(
                    shell,
                    _ISHELLLINK_GETDESCRIPTION,
                    ctypes.c_long,
                    ctypes.c_void_p,
                    wintypes.LPWSTR,
                    ctypes.c_int,
                )
                get_description(shell, description, 1024)

                return (
                    target.value,
                    icon_location.value,
                    icon_index.value,
                    description.value,
                )
            finally:
                _com_release(persist)
        finally:
            _com_release(shell)
    except Exception:
        return empty
    finally:
        if initialized:
            try:
                ole32.CoUninitialize()
            except Exception:
                pass


def _resolve_shortcut(lnk_path, display_name: str) -> tuple:
    """Return (target_exe, exe_basename_lower, icon_source, icon_index)."""
    target, icon_location, icon_index, _description = _read_shortcut(lnk_path)

    target = target.strip().strip('"')
    icon_location = icon_location.strip().strip('"')
    exe = ""

    if target and os.path.isfile(target):
        if target.lower().endswith(".exe"):
            exe = os.path.basename(target).lower()
    else:
        target = ""

    if icon_location:
        icon_location = os.path.expandvars(icon_location)
        if not os.path.isfile(icon_location):
            icon_location = ""

    # Shell-folder shortcuts (File Explorer) resolve to no target; use the
    # well-known executable Windows shows for them.
    if not target and not icon_location:
        mapped = SHELL_FOLDER_EXES.get(display_name.casefold())
        if mapped:
            mapped = os.path.expandvars(mapped)
            if os.path.isfile(mapped):
                target = mapped
                icon_location = mapped
                icon_index = 0

    if not exe and icon_location.lower().endswith(".exe"):
        exe = os.path.basename(icon_location).lower()

    return target, exe, icon_location, max(0, int(icon_index))


def _shell_file_icon_png(path) -> bytes | None:
    """Icon Windows associates with a file (last-resort fallback for .lnk)."""
    if not path:
        return None
    info = _SHFILEINFOW()
    flags = SHGFI_ICON | SHGFI_LARGEICON
    if not shell32.SHGetFileInfoW(
        str(path), 0, ctypes.byref(info), ctypes.sizeof(info), flags
    ):
        return None
    hicon = info.hIcon
    if not hicon:
        return None
    try:
        return _hicon_to_png(hicon)
    finally:
        try:
            user32.DestroyIcon(hicon)
        except Exception:
            pass


def _pinned_icon_id(lnk_path, target: str, icon_location: str, icon_index: int) -> str:
    """PNG content id for a pinned app, or "" when no icon can be extracted."""
    sources = []
    if icon_location:
        sources.append((icon_location, icon_index))
    if target and (not icon_location or target.lower() != icon_location.lower()):
        sources.append((target, 0))

    key = (str(lnk_path).lower(), tuple((path.lower(), index) for path, index in sources))
    cached = _pinned_icon_ids.get(key)
    if cached is not None:
        return cached

    png = None
    for path, index in sources:
        try:
            png = _hicon_to_png(_path_icon_hires(path, index))
        except Exception:
            png = None
        if png:
            break

    if not png:
        try:
            png = _shell_file_icon_png(lnk_path)
        except Exception:
            png = None

    icon_id = _register_icon(png) if png else ""

    if len(_pinned_icon_ids) > 64:
        _pinned_icon_ids.clear()
    _pinned_icon_ids[key] = icon_id
    return icon_id


def _pinned_signature() -> tuple:
    """Cheap (name, mtime, size) fingerprint of the pinned folder + entries."""
    entries = []
    try:
        with os.scandir(PINNED_DIR) as iterator:
            for entry in iterator:
                if not entry.name.lower().endswith(".lnk"):
                    continue
                try:
                    stat = entry.stat()
                except OSError:
                    continue
                entries.append((entry.name, entry.path, stat.st_mtime_ns, stat.st_size))
    except OSError:
        return None, []

    entries.sort(key=lambda item: item[0].casefold())
    entries = entries[:PINNED_LIMIT]
    signature = tuple((name, mtime, size) for name, _path, mtime, size in entries)
    return signature, entries


def _build_pinned_apps(entries: list) -> tuple:
    apps = []
    by_id = {}
    for name, path, _mtime, _size in entries:
        display_name = name[:-4]  # strip ".lnk"
        app_id = "p_" + hashlib.sha1(
            display_name.casefold().encode("utf-8", "replace")
        ).hexdigest()[:12]
        try:
            target, exe, icon_location, icon_index = _resolve_shortcut(path, display_name)
            icon_id = _pinned_icon_id(path, target, icon_location, icon_index)
        except Exception:
            exe, icon_id = "", ""
        apps.append(
            {
                "id": app_id,
                "name": display_name,
                "exe": exe,
                "icon_id": icon_id,
            }
        )
        by_id[app_id] = (path, display_name)
    return apps, by_id


def list_pinned_apps(force: bool = False) -> list:
    """Taskbar-pinned applications, cached for PINNED_TTL seconds.

    A cheap folder fingerprint is checked after the TTL so a 4s dock refresh
    never pays for COM resolution or icon extraction again. Any failure is
    reported as an empty list, never an exception.
    """
    try:
        now = time.monotonic()
        dir_key = str(PINNED_DIR)
        with _pinned_lock:
            state = _pinned_state
            if (
                not force
                and state["dir"] == dir_key
                and state["expires"] > now
            ):
                return list(state["apps"])

            signature, entries = _pinned_signature()
            if (
                not force
                and state["dir"] == dir_key
                and signature == state["signature"]
            ):
                state["expires"] = now + PINNED_TTL
                return list(state["apps"])

            apps, by_id = _build_pinned_apps(entries)
            state.update(
                {
                    "dir": dir_key,
                    "signature": signature,
                    "expires": now + PINNED_TTL,
                    "apps": apps,
                    "by_id": by_id,
                }
            )
            return list(apps)
    except Exception:
        return []


def launch_pinned_app(app_id: str) -> tuple:
    """Launch a shortcut previously advertised by list_pinned_apps().

    Only ids from the cached pinned list are accepted — a raw path from the
    client is never executed. Returns (ok, name, error) and never raises.
    """
    app_id = str(app_id or "")
    name = ""
    lnk = None
    try:
        list_pinned_apps()
        with _pinned_lock:
            entry = _pinned_state["by_id"].get(app_id)
        if entry is None:
            # The folder may have changed since the last 4s refresh.
            list_pinned_apps(force=True)
            with _pinned_lock:
                entry = _pinned_state["by_id"].get(app_id)
        if entry is None:
            return False, "", "unknown app id"

        lnk, name = entry
        if not os.path.isfile(lnk):
            return False, name, "shortcut not found"
        try:
            expected_dir = os.path.abspath(str(PINNED_DIR)).casefold()
            actual_dir = os.path.dirname(os.path.abspath(lnk)).casefold()
        except Exception:
            return False, name, "invalid shortcut path"
        if actual_dir != expected_dir:
            return False, name, "shortcut outside pinned folder"

        os.startfile(lnk)
        return True, name, ""
    except Exception as exc:
        return False, name, str(exc) or exc.__class__.__name__


def list_windows_and_pinned(limit: int = 12) -> tuple:
    """Both dock lists in one call, so the server needs a single to_thread hop."""
    windows = list_open_windows(limit)
    try:
        pinned = list_pinned_apps()
    except Exception:
        pinned = []
    return windows, pinned
