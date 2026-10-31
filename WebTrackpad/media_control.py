"""Windows media session bridge for the phone's music island.

Windows exposes every player (Spotify, Yandex Music, browsers, VLC, …) through
the System Media Transport Controls (SMTC). This module reads the current
track and artwork and forwards play/pause/next/previous actions, so the phone
controls whatever is playing on the PC without extra software in the player.

The WinRT bindings are only imported when first used: a machine without them
still gets a working trackpad, just without the island.
"""

import hashlib

# app user model id -> human name shown in the island
PLAYER_NAMES = {
    "ru.yandex.desktop.music": "Яндекс Музыка",
    "yandexmusic": "Яндекс Музыка",
    "spotify": "Spotify",
    "spotify.exe": "Spotify",
    "chrome": "Chrome",
    "google chrome": "Chrome",
    "msedge": "Edge",
    "microsoft edge": "Edge",
    "firefox": "Firefox",
    "opera": "Opera",
    "vivaldi": "Vivaldi",
    "vlc": "VLC",
    "vlc.exe": "VLC",
    "wmplayer.exe": "Windows Media Player",
    "music.ui.exe": "Media Player",
    "foobar2000.exe": "foobar2000",
    "aimp.exe": "AIMP",
    "telegram.exe": "Telegram",
    "discord.exe": "Discord",
}

_manager = None
_art_ids: dict = {}   # track key -> art id
_art_pngs: dict = {}  # art id -> PNG bytes
_art_order: list = []


def art_png(art_id: str) -> bytes:
    """Return the cached cover image, or b"" when unknown."""
    return _art_pngs.get(art_id, b"")


def _friendly_app(app_id: str) -> str:
    if not app_id:
        return ""
    key = app_id.lower()
    name = PLAYER_NAMES.get(key)
    if name:
        return name
    # "C:\...\Spotify.exe" or "com.spotify.client" -> last meaningful part.
    part = key.replace("\\", "/").split("/")[-1]
    if part.endswith(".exe"):
        part = part[:-4]
    for chunk in reversed(part.split(".")):
        if chunk and chunk not in ("desktop", "client", "app", "music"):
            return chunk[:1].upper() + chunk[1:]
    return part[:1].upper() + part[1:] if part else ""


def _remember_art(key: str, png: bytes) -> str:
    cached = _art_ids.get(key)
    if cached and cached in _art_pngs:
        return cached
    art_id = hashlib.sha1(png).hexdigest()[:16]
    _art_pngs[art_id] = png
    _art_ids[key] = art_id
    _art_order.append(art_id)
    while len(_art_order) > 32:
        old = _art_order.pop(0)
        if old != art_id:
            _art_pngs.pop(old, None)
    return art_id


async def _manager_instance():
    global _manager
    if _manager is None:
        from winrt.windows.media.control import (
            GlobalSystemMediaTransportControlsSessionManager as Manager,
        )

        _manager = await Manager.request_async()
    return _manager


async def _current_session():
    manager = await _manager_instance()
    session = manager.get_current_session()
    if session is None:
        # Sessions come and go; a fresh manager finds a newly started player.
        global _manager
        _manager = None
        manager = await _manager_instance()
        session = manager.get_current_session()
    return session


async def _read_art(props, key: str) -> str:
    if not key:
        return ""
    cached = _art_ids.get(key)
    if cached:
        return cached

    from winrt.windows.storage.streams import DataReader

    thumb = props.thumbnail
    if thumb is None:
        return ""
    stream = await thumb.open_read_async()
    size = int(stream.size)
    if size <= 0 or size > 4 * 1024 * 1024:
        return ""
    reader = DataReader(stream)
    await reader.load_async(size)
    buffer = bytearray(size)
    reader.read_bytes(buffer)
    return _remember_art(key, bytes(buffer))


async def get_state() -> dict:
    """Return the current track, or {"has_session": False} when nothing plays."""
    try:
        from winrt.windows.media.control import (
            GlobalSystemMediaTransportControlsSessionPlaybackStatus as PlaybackStatus,
        )

        session = await _current_session()
        if session is None:
            return {"has_session": False}

        props = await session.try_get_media_properties_async()
        info = session.get_playback_info()
        timeline = session.get_timeline_properties()

        playing = info.playback_status == PlaybackStatus.PLAYING
        title = (props.title or "").strip()
        artist = (props.artist or "").strip()
        album = (props.album_title or "").strip()
        app_id = session.source_app_user_model_id or ""

        controls = info.controls
        can_next = bool(controls and controls.is_next_enabled)
        can_previous = bool(controls and controls.is_previous_enabled)

        key = "|".join((app_id, title, artist, album))
        art_id = ""
        try:
            art_id = await _read_art(props, key)
        except Exception:
            art_id = ""

        return {
            "has_session": True,
            "title": title or "Без названия",
            "artist": artist,
            "album": album,
            "app": _friendly_app(app_id),
            "playing": playing,
            "position": max(0.0, timeline.position.total_seconds()),
            "duration": max(0.0, timeline.end_time.total_seconds()),
            "art": art_id,
            "can_next": can_next,
            "can_previous": can_previous,
        }
    except Exception:
        global _manager
        _manager = None
        return {"has_session": False}


async def control(action: str) -> bool:
    """Send a transport command to the active player."""
    try:
        session = await _current_session()
        if session is None:
            return False
        if action == "play_pause":
            return bool(await session.try_toggle_play_pause_async())
        if action == "play":
            return bool(await session.try_play_async())
        if action == "pause":
            return bool(await session.try_pause_async())
        if action == "next":
            return bool(await session.try_skip_next_async())
        if action in ("previous", "prev"):
            return bool(await session.try_skip_previous_async())
        return False
    except Exception:
        return False
