"""
AirTrackpad Server
Turns your smartphone into a smooth MacBook-like trackpad for Windows.
"""

import asyncio
import ctypes
import io
import json
import logging
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import qrcode
from websockets.asyncio.server import serve
from websockets.datastructures import Headers
from websockets.http11 import Response

import network_info
import media_control
from mouse_controller import WindowsInputController
from window_icons import icon_png, launch_pinned_app, list_windows_and_pinned

# Browsers, phones and network scanners often open a TCP connection and close
# it before sending an HTTP request. websockets logs a full traceback for that
# harmless case ("opening handshake failed"); drop only those records so real
# errors remain visible.
class _HandshakeNoiseFilter(logging.Filter):
    def filter(self, record):
        return record.getMessage() != "opening handshake failed"


logging.getLogger("websockets.server").addFilter(_HandshakeNoiseFilter())

# Configure console encoding safely for UTF-8
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    try:
        ctypes.windll.kernel32.SetConsoleOutputCP(65001)
        ctypes.windll.kernel32.SetConsoleCP(65001)
    except Exception:
        pass

BASE_DIR = Path(__file__).resolve().parent
HTML_PATH = BASE_DIR / "index.html"
QR_PATH = BASE_DIR / "qr.png"

controller = WindowsInputController()


def get_default_route_ip():
    """Return the IPv4 address of the interface used for the default route.

    Connecting a UDP socket sends no packets, but makes Windows pick the
    adapter it would use to reach the internet. With a VPN this is often the
    tunnel address, so it is only used as a fallback.
    """
    return network_info.get_default_route_ip()


def get_preferred_ips():
    """All usable IPv4 addresses, real Wi-Fi/Ethernet first."""
    ips = network_info.get_lan_ips()
    return ips if ips else ["127.0.0.1"]


FIREWALL_STATE = {"value": "unknown"}


def _netsh(args):
    """Run netsh without flashing a console window."""
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        return subprocess.run(
            ["netsh", *args],
            capture_output=True,
            creationflags=flags,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return None


def ensure_firewall_rule(port: int) -> str:
    """Make sure Windows Firewall allows inbound TCP on the server ports.

    A VPN often flips the Wi-Fi profile to "Public", where Windows silently
    blocks incoming connections. A rule for all profiles fixes that.
    Returns: "ok", "added", "no-admin" or "unknown".
    """
    rule_name = "AirTrackpad"

    shown = _netsh(["advfirewall", "firewall", "show", "rule", f"name={rule_name}"])
    if shown is not None and shown.returncode == 0:
        return "ok"

    added = _netsh([
        "advfirewall", "firewall", "add", "rule",
        f"name={rule_name}",
        "dir=in", "action=allow", "protocol=TCP",
        "localport=5050-5060", "profile=any",
    ])
    if added is not None and added.returncode == 0:
        return "added"
    return "no-admin"


def generate_qr_image(url: str):
    """Generate QR code image bytes and save to disk."""
    qr = qrcode.QRCode(
        version=1,
        box_size=10,
        border=2,
    )
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")

    try:
        img.save(str(QR_PATH))
    except Exception:
        pass

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def start_udp_broadcaster(url: str, ips: list, broadcast_port: int = 5051):
    """Background thread broadcasting server presence for auto-discovery by Android app."""
    targets = ["255.255.255.255"]
    for ip in ips:
        if ip == "127.0.0.1":
            continue
        parts = ip.split(".")
        if len(parts) == 4:
            targets.append(".".join(parts[:3] + ["255"]))
    targets = list(dict.fromkeys(targets))

    def broadcaster():
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        payload = json.dumps({"service": "AirTrackpad", "url": url}).encode("utf-8")

        while True:
            for target in targets:
                try:
                    sock.sendto(payload, (target, broadcast_port))
                except Exception:
                    pass
            time.sleep(2.0)

    t = threading.Thread(target=broadcaster, daemon=True)
    t.start()


def _kind_label(kind: str) -> str:
    return {
        "wifi": "Wi-Fi",
        "ethernet": "Ethernet",
        "vpn": "VPN",
        "tailscale": "Tailscale",
        "virtual": "виртуальный адаптер",
    }.get(kind, kind)


def print_banner(best_url: str, urls: list, port: int, firewall_state: str = "unknown"):
    """Print ASCII QR and friendly connection instructions in console."""
    print("=" * 60)
    print("      AirTrackpad - MacBook-style Trackpad for PC")
    print("=" * 60)
    print("\n[!] Отсканируйте этот QR-код камерой вашего смартфона:")
    print("-" * 60)

    try:
        qr = qrcode.QRCode(border=1)
        qr.add_data(best_url)
        qr.print_ascii(invert=True)
    except Exception:
        print("  (QR-код сохранен в файл qr.png)")

    print("-" * 60)
    primary_label = ""
    for item in urls:
        if item["url"] == best_url:
            primary_label = item["label"]
            break
    suffix = f"   ({primary_label})" if primary_label else ""
    print(f"Ссылка для смартфона:  {best_url}{suffix}")

    others = [item for item in urls if item["url"] != best_url]
    if others:
        print("   Другие адреса (если основной не открывается):")
        for item in others:
            print(f"    - {item['url']}  ({item['label']} · {_kind_label(item['kind'])})")

    print("\n[🤖 Auto-Discovery] UDP Broadcaster запущен на порту 5051.")

    if firewall_state == "no-admin":
        print("\n[!] Брандмауэр: не удалось добавить правило без прав администратора.")
        print("    Если телефон не подключается, запустите от имени администратора:")
        print("    «Разрешить доступ в брандмауэре.bat»")
    elif firewall_state == "added":
        print("\n[+] Брандмауэр: правило для порта создано (все профили сети).")
    elif firewall_state == "ok":
        print("\n[+] Брандмауэр: правило для порта уже разрешено.")

    if network_info.has_vpn():
        print("\n[!] Обнаружен VPN. Если телефон не открывает страницу:")
        print("    • на телефоне: отключите VPN или включите обход локальной сети")
        print("      (split tunneling / «Разрешить локальную сеть») для этого Wi-Fi;")
        print("    • на ПК: в VPN включите доступ к локальной сети (LAN) и отключите")
        print("      «блокировку при отключении» (kill switch);")
        print("    • либо подключите телефон к той же VPN и откройте адрес VPN-адаптера")
        print("      из списка выше.")
    else:
        print("\nПодсказка: телефон и ПК должны быть в одной сети Wi-Fi.")

    tailscale = network_info.tailscale_state()
    if tailscale.get("installed") and not tailscale.get("running"):
        print("\n[+] Tailscale установлен, но выключен. Если VPN мешает локальной")
        print("    сети, включите Tailscale на ПК и телефоне — трекпад будет работать")
        print("    из любой сети, без настроек роутера и брандмауэра.")

    print("=" * 60)
    print("Нажмите Ctrl+C в этом окне для остановки сервера.\n")


async def ws_handler(websocket):
    """Handle incoming WebSocket control events from the smartphone."""
    print("[+] Успешное WebSocket-подключение от телефона!")
    try:
        async for message in websocket:
            try:
                data = json.loads(message)
                msg_type = data.get("type")

                if msg_type == "move":
                    dx = float(data.get("dx", 0))
                    dy = float(data.get("dy", 0))
                    controller.move(dx, dy)

                elif msg_type == "click":
                    btn = data.get("btn", "left")
                    controller.click(btn)

                elif msg_type == "down":
                    btn = data.get("btn", "left")
                    controller.mouse_down(btn)

                elif msg_type == "up":
                    btn = data.get("btn", "left")
                    controller.mouse_up(btn)

                elif msg_type == "scroll":
                    dx = float(data.get("dx", 0))
                    dy = float(data.get("dy", 0))
                    controller.scroll(dx, dy)

                elif msg_type == "zoom":
                    delta = float(data.get("delta", 0))
                    controller.zoom(delta)

                elif msg_type == "action":
                    action = data.get("action", "")
                    controller.execute_action(action)

                elif msg_type == "type":
                    text = data.get("text", "")
                    controller.type_text(text)

                elif msg_type == "get_windows":
                    # Enumerating windows, resolving pinned shortcuts and
                    # converting icons touches Win32/COM APIs that may block
                    # briefly; keep the event loop free and do both lists in a
                    # single hop so the 4s dock refresh stays cheap.
                    windows, pinned = await asyncio.to_thread(list_windows_and_pinned)
                    await websocket.send(json.dumps({
                        "type": "windows_list",
                        "windows": windows,
                        "pinned": pinned,
                    }, ensure_ascii=False))

                elif msg_type == "launch_app":
                    # Launch a taskbar-pinned app by its stable id. The client
                    # never sends a path; only ids from the pinned cache work.
                    app_id = str(data.get("id", "") or "")
                    fallback_name = str(data.get("name", "") or "")
                    try:
                        ok, name, error = await asyncio.to_thread(
                            launch_pinned_app, app_id
                        )
                    except Exception as exc:  # reply even on unexpected errors
                        ok, name, error = False, "", str(exc)
                    await websocket.send(json.dumps({
                        "type": "launch_result",
                        "id": app_id,
                        "ok": ok,
                        "name": name or fallback_name,
                        "error": error,
                    }, ensure_ascii=False))

                elif msg_type == "ping":
                    # Round-trip probe used by the phone to show real latency.
                    await websocket.send(json.dumps({"type": "pong", "id": data.get("id", 0)}))

                elif msg_type == "media_state":
                    state = await media_control.get_state()
                    await websocket.send(json.dumps({
                        "type": "media_state",
                        "state": state,
                    }, ensure_ascii=False))

                elif msg_type == "media_control":
                    action = data.get("action", "")
                    ok = await media_control.control(action)
                    state = await media_control.get_state()
                    await websocket.send(json.dumps({
                        "type": "media_state",
                        "state": state,
                        "action": action,
                        "ok": ok,
                    }, ensure_ascii=False))

                elif msg_type == "focus_window":
                    hwnd = int(data.get("hwnd", 0))
                    action = await asyncio.to_thread(controller.focus_window, hwnd)
                    await websocket.send(json.dumps({
                        "type": "window_action",
                        "hwnd": hwnd,
                        "action": action,
                        "title": data.get("title", ""),
                    }))

            except Exception as e:
                print(f"[!] Ошибка парсинга сообщения: {e}")
    except Exception as e:
        print(f"[-] Соединение WebSocket закрыто: {e}")


def make_http_handler(qr_state: dict):
    def process_request(connection, request):
        if request.headers.get("Upgrade", "").lower() == "websocket":
            return None

        path = request.path.split("?")[0]
        client = connection.remote_address[0] if connection.remote_address else "?"

        if path in ("/", "/index.html"):
            print(f"[+] Страница открыта с {client}")
            try:
                with open(HTML_PATH, "rb") as f:
                    html_bytes = f.read()
            except Exception:
                html_bytes = b"404 Not Found"
            headers = Headers([
                ("Content-Type", "text/html; charset=utf-8"),
                ("Cache-Control", "no-cache, no-store, must-revalidate"),
            ])
            return Response(200, "OK", headers, html_bytes)

        if path == "/api/network":
            live_port = qr_state.get("port", 5050)
            live_urls = network_info.get_url_options(live_port) or qr_state.get("urls", [])
            payload = {
                "port": live_port,
                "urls": live_urls,
                "vpn": network_info.has_vpn(),
                "tailscale": network_info.tailscale_state(),
                "firewall": qr_state.get("firewall", "unknown"),
                "client": client,
            }
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers = Headers([
                ("Content-Type", "application/json; charset=utf-8"),
                ("Cache-Control", "no-cache, no-store, must-revalidate"),
                ("Access-Control-Allow-Origin", "*"),
            ])
            return Response(200, "OK", headers, body)

        # Application icons are immutable content, so the phone caches them for
        # a year and never re-downloads them.
        if path.startswith("/api/icon/"):
            body = icon_png(path[len("/api/icon/"):])
            if not body:
                return Response(404, "Not Found", Headers([("Content-Type", "text/plain")]), b"404 Not Found")
            headers = Headers([
                ("Content-Type", "image/png"),
                ("Cache-Control", "public, max-age=31536000, immutable"),
            ])
            return Response(200, "OK", headers, body)

        # Album artwork for the music island.
        if path.startswith("/api/art/"):
            body = media_control.art_png(path[len("/api/art/"):])
            if not body:
                return Response(404, "Not Found", Headers([("Content-Type", "text/plain")]), b"404 Not Found")
            headers = Headers([
                ("Content-Type", "image/png"),
                ("Cache-Control", "public, max-age=31536000, immutable"),
            ])
            return Response(200, "OK", headers, body)

        if path == "/qr.png":
            qr_bytes = qr_state.get("bytes", b"")
            if not qr_bytes:
                return Response(404, "Not Found", Headers([("Content-Type", "text/plain")]), b"404 Not Found")
            headers = Headers([
                ("Content-Type", "image/png"),
                ("Cache-Control", "no-cache, no-store, must-revalidate"),
            ])
            return Response(200, "OK", headers, qr_bytes)

        return Response(404, "Not Found", Headers([("Content-Type", "text/plain")]), b"404 Not Found")

    return process_request


async def main():
    port = 5050
    host = "0.0.0.0"

    ips = get_preferred_ips()
    primary_ip = network_info.get_primary_ip()

    # /qr.png is generated only after the real port is known. The state dict
    # is shared with the HTTP handler, which is created before binding.
    qr_state = {"bytes": b""}
    http_handler = make_http_handler(qr_state)

    server = None
    for test_port in (5050, 5052, 5053, 5054, 5055, 5056, 5057, 5058, 5059, 5060):
        try:
            server = await serve(
                ws_handler,
                host=host,
                port=test_port,
                process_request=http_handler,
                max_size=2 * 1024 * 1024,
            )
            port = test_port
            break
        except OSError:
            continue

    if not server:
        print("[X] Ошибка: Не удалось занять порт. Проверьте свободные порты.")
        return

    urls = network_info.get_url_options(port)
    best_url = urls[0]["url"] if urls else f"http://{primary_ip}:{port}/"
    if not best_url.startswith("http"):
        best_url = f"http://{primary_ip}:{port}/"

    firewall_state = ensure_firewall_rule(port)
    FIREWALL_STATE["value"] = firewall_state

    qr_state.update({"port": port, "urls": urls, "firewall": firewall_state})
    qr_state["bytes"] = generate_qr_image(best_url)

    # Start UDP broadcaster for Android Companion auto-discovery
    start_udp_broadcaster(best_url, ips)

    print_banner(best_url, urls, port, firewall_state)

    try:
        await asyncio.Future()  # run forever
    except (asyncio.CancelledError, KeyboardInterrupt):
        pass
    finally:
        server.close()
        await server.wait_closed()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nСервер остановлен.")
