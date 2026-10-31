#!/usr/bin/env python3
"""Duolingo streak guard.

Runs late in the evening (default 23:50), checks whether the streak for today
has already been extended, and if not, completes one short story lesson so the
streak is not lost. Writes a log and (optionally) shows a Windows notification.

Commands:
    python duo_guard.py --check     # read-only: show account state, do nothing
    python duo_guard.py --run       # check, and complete a story if needed
    python duo_guard.py --debug     # dump the raw streak payload

Config: config.json next to this file. Token is the `jwt_token` cookie taken
from a logged-in https://www.duolingo.com page (see README.md).
"""

from __future__ import annotations

import argparse
import base64
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent

# Console may be a cp1252 pipe or absent (pythonw): make output UTF-8 and safe.
for _stream in (sys.stdout, sys.stderr):
    try:
        if _stream is not None:
            _stream.reconfigure(encoding='utf-8', errors='replace')
    except (AttributeError, ValueError):
        pass
CONFIG_PATH = HERE / "config.json"
STATE_PATH = HERE / "state.json"
LOG_DIR = HERE / "logs"

BASE = "https://www.duolingo.com/2017-06-30"
STORIES = "https://stories.duolingo.com/api2/stories"
TIMEOUT = 30


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def today_str() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def log(message: str, quiet: bool = False) -> None:
    line = "[{0}] {1}".format(datetime.now().strftime("%Y-%m-%d %H:%M:%S"), message)
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        path = LOG_DIR / ("guard-{0}.log".format(today_str()))
        with path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass
    if not quiet and sys.stdout is not None:
        print(line, flush=True)


def load_json(path: Path, default):
    try:
        with path.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


def save_json(path: Path, data) -> None:
    with path.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)


def load_config() -> dict:
    cfg = load_json(CONFIG_PATH, None)
    if not cfg:
        log("Нет config.json — заполните токен (см. README.md).", quiet=True)
        sys.exit(2)
    token = (cfg.get("jwt_token") or "").strip()
    if not token or token.startswith("ВСТАВЬТЕ"):
        log("В config.json не указан jwt_token. Возьмите его из cookie на duolingo.com.", quiet=True)
        sys.exit(2)
    return cfg


def jwt_subject(token: str) -> str:
    payload = token.split(".")[1]
    payload += "=" * (-len(payload) % 4)
    data = json.loads(base64.urlsafe_b64decode(payload.encode("ascii")))
    return str(data.get("sub") or data.get("user_id") or "")


def headers(token: str) -> dict:
    return {
        "authorization": "Bearer " + token,
        "cookie": "jwt_token=" + token,
        "connection": "Keep-Alive",
        "content-type": "application/json",
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36",
        "device-platform": "web",
        "x-duolingo-device-platform": "web",
        "x-duolingo-app-version": "1.0.0",
        "x-duolingo-application": "chrome",
        "x-duolingo-client-version": "web",
        "accept": "application/json",
        "origin": "https://www.duolingo.com",
        "referer": "https://www.duolingo.com/",
    }


def http_json(method: str, url: str, token: str, body=None):
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method=method, headers=headers(token))
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            raw = resp.read().decode("utf-8", "replace")
            return resp.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        try:
            parsed = json.loads(raw)
        except ValueError:
            parsed = {"raw": raw[:400]}
        return exc.code, parsed
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return 0, {"error": str(exc)}


# --------------------------------------------------------------------------- #
# Duolingo calls
# --------------------------------------------------------------------------- #
def get_user(token: str, user_id: str) -> dict:
    url = "{0}/users/{1}".format(BASE, user_id)
    status, data = http_json("GET", url, token)
    if status != 200:
        log("Не удалось получить профиль: HTTP {0} {1}".format(status, data))
        return {}
    return data


def current_streak(user: dict) -> dict:
    return ((user.get("streakData") or {}).get("currentStreak") or {})


def extended_today(user: dict) -> bool:
    """Best-effort: has the streak already been extended today?"""
    streak = current_streak(user)
    today = today_str()
    for key in ("lastExtendedDate", "lastExtendedDateString", "endDate"):
        value = streak.get(key)
        if value and str(value)[:10] == today:
            return True
    return False


def list_stories(token: str, from_lang: str, learn_lang: str) -> list:
    """Return story ids available for the course (API shape may change).

    The stories service answers with {"sets": [[{story}, {story}, ...], ...]};
    each story has an "id" (used in the /complete URL), a title and xpToAward.
    """
    for url in (
        "{0}?fromLanguage={1}&learningLanguage={2}".format(STORIES, from_lang, learn_lang),
        "{0}?learningLanguage={1}&fromLanguage={2}".format(STORIES, learn_lang, from_lang),
    ):
        status, data = http_json("GET", url, token)
        if status != 200 or not isinstance(data, dict):
            continue

        items = []
        for group in data.get("sets") or []:
            if isinstance(group, list):
                items.extend(group)
            elif isinstance(group, dict):
                items.append(group)
        if not items:
            items = data.get("stories") or data.get("data") or []

        stories = []
        for item in items:
            if not isinstance(item, dict):
                continue
            if item.get("isVideo"):
                continue
            if (item.get("state") or "ACTIVE") != "ACTIVE":
                continue
            story_id = item.get("id") or item.get("slug")
            if story_id:
                stories.append({
                    "id": story_id,
                    "title": item.get("title") or "",
                    "xp": item.get("xpToAward") or 0,
                })
        if stories:
            # Cheapest stories first: enough for the streak, less suspicious.
            stories.sort(key=lambda s: (s["xp"] or 999, s["id"]))
            return stories
    return []


def complete_story(token: str, slug: str, from_lang: str, learn_lang: str) -> dict:
    now = int(time.time())
    payload = {
        "awardXp": True,
        "completedBonusChallenge": False,
        "fromLanguage": from_lang,
        "learningLanguage": learn_lang,
        "hasXpBoost": False,
        "illustrationFormat": "svg",
        "isFeaturedStoryInPracticeHub": False,
        "isLegendaryMode": False,
        "isV2Redo": False,
        "isV2Story": False,
        "masterVersion": True,
        "maxScore": 0,
        "score": 0,
        "startTime": now - 240,
        "endTime": now,
    }
    url = "{0}/{1}/complete".format(STORIES, slug)
    return http_json("POST", url, token, payload)


def notify(title: str, text: str) -> None:
    """Non-blocking Windows balloon tip."""
    script = (
        "Add-Type -AssemblyName System.Windows.Forms;"
        "Add-Type -AssemblyName System.Drawing;"
        "$n = New-Object System.Windows.Forms.NotifyIcon;"
        "$n.Icon = [System.Drawing.SystemIcons]::Information;"
        "$n.Visible = $true;"
        "$n.ShowBalloonTip(30000, '{0}', '{1}', 'Info');"
        "Start-Sleep -Seconds 12; $n.Dispose()"
    ).format(title.replace("'", ""), text.replace("'", ""))
    try:
        subprocess.Popen(
            ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except OSError:
        pass


# --------------------------------------------------------------------------- #
# main flows
# --------------------------------------------------------------------------- #
def describe(user: dict) -> str:
    streak = current_streak(user)
    return (
        "пользователь={0} стрик={1} (объект: {2}) язык={3}→{4}".format(
            user.get("username"),
            user.get("streak"),
            json.dumps(streak, ensure_ascii=False),
            user.get("fromLanguage"),
            user.get("learningLanguage"),
        )
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Duolingo streak guard")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--check", action="store_true", help="только проверить, ничего не делать")
    group.add_argument("--run", action="store_true", help="проверить и при необходимости завершить историю")
    group.add_argument("--debug", action="store_true", help="показать сырые данные о стрике")
    group.add_argument("--force-story", action="store_true", help="завершить историю даже если стрик уже продлён (проверка)")
    args = parser.parse_args()

    cfg = load_config()
    token = cfg["jwt_token"].strip()
    user_id = cfg.get("user_id") or jwt_subject(token)
    cfg["user_id"] = user_id

    user = get_user(token, user_id)
    if not user:
        notify("Duolingo", "Не удалось подключиться к Duolingo — проверьте токен.")
        return 3

    from_lang = cfg.get("from_language") or user.get("fromLanguage") or "ru"
    learn_lang = cfg.get("learning_language") or user.get("learningLanguage") or "en"

    if args.debug:
        log(describe(user))
        log("сырой объект: " + json.dumps(user.get("streakData"), ensure_ascii=False))
        return 0

    done = extended_today(user)
    if args.force_story:
        done = False
        log("Режим проверки: завершаю историю, даже если стрик уже продлён.")
    log(("Стрик уже продлён сегодня." if done else "Стрик сегодня ещё НЕ продлён.") + " " + describe(user))

    if args.check:
        return 0 if done else 1

    if done:
        save_json(STATE_PATH, {"date": today_str(), "streak": user.get("streak"), "action": "none"})
        return 0

    # Pick a story for the user's course.
    stories = list_stories(token, from_lang, learn_lang)
    forced = (cfg.get("story_slug") or "").strip()
    if forced:
        candidates = [forced] + [s["id"] for s in stories if s["id"] != forced]
    else:
        candidates = [s["id"] for s in stories]
    if not candidates:
        log("Не удалось получить список историй для курса {0}→{1}.".format(from_lang, learn_lang))
        notify("Duolingo: стрик под угрозой", "Бот не смог найти историю. Сделай урок вручную!")
        return 4
    log("Найдено историй: {0}, первая: «{1}» ({2} XP).".format(
        len(stories), stories[0]["title"], stories[0]["xp"]))

    for candidate in candidates[:5]:
        status, data = complete_story(token, candidate, from_lang, learn_lang)
        if status == 200:
            log("История «{0}» завершена, awardedXp={1}.".format(candidate, data.get("awardedXp", "?")))
            break
        log("История «{0}» не сработала: HTTP {1} {2}".format(candidate, status, data))
        if status in (401, 403):
            notify("Duolingo", "Токен истёк или не принят — стрик под угрозой, сделай урок вручную.")
            return 5
    else:
        notify("Duolingo: стрик под угрозой", "Автопрохождение не удалось. Сделай урок вручную!")
        return 6

    # Verify that the streak really moved.
    time.sleep(3)
    user_after = get_user(token, user_id)
    if extended_today(user_after):
        log("Проверка после: стрик продлён. ok.")
        save_json(STATE_PATH, {"date": today_str(), "streak": user_after.get("streak"), "action": "story"})
        return 0

    log("Проверка после: стрик всё ещё не продлён — нужен ручной урок.")
    notify("Duolingo: стрик под угрозой", "Бот выполнил историю, но стрик не засчитан. Сделай урок вручную!")
    save_json(STATE_PATH, {"date": today_str(), "streak": user_after.get("streak"), "action": "story-unconfirmed"})
    return 7


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
