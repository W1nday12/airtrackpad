"""Network helpers for AirTrackpad.

The phone must reach the PC over a real LAN.  A naive "gethostbyname" or
"default route" lookup often returns a VPN / Tailscale / virtual adapter
address that the phone cannot use, and Windows may block incoming connections
after a VPN flips the network profile to "Public".

This module enumerates adapters with the Win32 GetAdaptersAddresses API so we
can tell a physical Wi-Fi/Ethernet card from a tunnel, show the user every
candidate address and pick the right one for the QR code.
"""

from __future__ import annotations

import ctypes
import ipaddress
import json
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

# --- Win32 constants -------------------------------------------------------

AF_UNSPEC = 0
ERROR_BUFFER_OVERFLOW = 111
GAA_FLAG_SKIP_ANYCAST = 0x0002
GAA_FLAG_SKIP_MULTICAST = 0x0004
GAA_FLAG_SKIP_DNS_SERVER = 0x0008

IF_TYPE_ETHERNET = 6
IF_TYPE_PPP = 23
IF_TYPE_LOOPBACK = 24
IF_TYPE_IEEE80211 = 71
IF_TYPE_TUNNEL = 131

# Interface descriptions that almost always mean "traffic goes through a
# tunnel" even when Windows reports a regular Ethernet-like adapter.
VPN_KEYWORDS = (
    "vpn", "tap-", "tap adapter", "tun", "wireguard", "tailscale",
    "openvpn", "amnezia", "wintun", "zerotier", "proton", "nordvpn",
    "nordlynx", "expressvpn", "surfshark", "outline", "shadowsocks",
    "clash", "sing-box", "xray", "v2ray", "warp", "cloudflare",
    "radmin", "hamachi", "softether", "pptp", "l2tp", "ikev2",
    "forticlient", "globalprotect", "anyconnect", "checkpoint",
    "mullvad", "windscribe", "tunnelbear", "hide.me", "hotspot shield",
)

# Virtual switches (Hyper-V, WSL, Docker, VirtualBox) are not reachable from
# the phone either, but they are not VPNs and should not be called one.
VIRTUAL_KEYWORDS = (
    "vethernet", "hyper-v", "default switch", "virtualbox", "vmware",
    "docker", "wsl", "virtual adapter", "npcap loopback", "bluetooth",
)

LINK_LOCAL = ipaddress.ip_network("169.254.0.0/16")
CGNAT = ipaddress.ip_network("100.64.0.0/10")  # Tailscale lives here
PRIVATE_10 = ipaddress.ip_network("10.0.0.0/8")
PRIVATE_172 = ipaddress.ip_network("172.16.0.0/12")
PRIVATE_192 = ipaddress.ip_network("192.168.0.0/16")


class _SOCKET_ADDRESS(ctypes.Structure):
    _fields_ = [
        ("lpSockaddr", ctypes.c_void_p),
        ("iSockaddrLength", ctypes.c_int),
    ]


class _IP_ADAPTER_UNICAST_ADDRESS(ctypes.Structure):
    pass


_IP_ADAPTER_UNICAST_ADDRESS._fields_ = [
    ("Length", ctypes.c_ulong),
    ("Flags", ctypes.c_ulong),
    ("Next", ctypes.POINTER(_IP_ADAPTER_UNICAST_ADDRESS)),
    ("Address", _SOCKET_ADDRESS),
    ("PrefixOrigin", ctypes.c_int),
    ("SuffixOrigin", ctypes.c_int),
    ("DadState", ctypes.c_int),
    ("ValidLifetime", ctypes.c_ulong),
    ("PreferredLifetime", ctypes.c_ulong),
    ("LeaseLifetime", ctypes.c_ulong),
    ("OnLinkPrefixLength", ctypes.c_ubyte),
]


class _IP_ADAPTER_ADDRESSES(ctypes.Structure):
    pass


_IP_ADAPTER_ADDRESSES._fields_ = [
    ("Length", ctypes.c_ulong),
    ("IfIndex", ctypes.c_ulong),
    ("Next", ctypes.POINTER(_IP_ADAPTER_ADDRESSES)),
    ("AdapterName", ctypes.c_char_p),
    ("FirstUnicastAddress", ctypes.POINTER(_IP_ADAPTER_UNICAST_ADDRESS)),
    ("FirstAnycastAddress", ctypes.c_void_p),
    ("FirstMulticastAddress", ctypes.c_void_p),
    ("FirstDnsServerAddress", ctypes.c_void_p),
    ("DnsSuffix", ctypes.c_wchar_p),
    ("Description", ctypes.c_wchar_p),
    ("FriendlyName", ctypes.c_wchar_p),
    ("PhysicalAddress", ctypes.c_ubyte * 8),
    ("PhysicalAddressLength", ctypes.c_ulong),
    ("Flags", ctypes.c_ulong),
    ("Mtu", ctypes.c_ulong),
    ("IfType", ctypes.c_ulong),
    ("OperStatus", ctypes.c_int),
    ("Ipv6IfIndex", ctypes.c_ulong),
    ("ZoneIndices", ctypes.c_ulong * 16),
    ("FirstPrefix", ctypes.c_void_p),
]

_IF_OPER_UP = 1


def _sockaddr_to_ip(pointer: int, length: int):
    """Convert a Win32 SOCKET_ADDRESS to a printable IP string."""
    if not pointer or length < 8:
        return None
    family = ctypes.cast(pointer, ctypes.POINTER(ctypes.c_ushort))[0]
    try:
        if family == socket.AF_INET:
            raw = ctypes.string_at(pointer + 4, 4)
            return socket.inet_ntop(socket.AF_INET, raw)
        if family == socket.AF_INET6:
            raw = ctypes.string_at(pointer + 8, 16)
            return socket.inet_ntop(socket.AF_INET6, raw)
    except (OSError, ValueError):
        return None
    return None


def _windows_adapters():
    """Return raw adapter records from GetAdaptersAddresses (or [])."""
    try:
        iphlpapi = ctypes.WinDLL("iphlpapi")
    except (AttributeError, OSError):
        return []

    fn = iphlpapi.GetAdaptersAddresses
    fn.argtypes = (
        ctypes.c_ulong,
        ctypes.c_ulong,
        ctypes.c_void_p,
        ctypes.POINTER(_IP_ADAPTER_ADDRESSES),
        ctypes.POINTER(ctypes.c_ulong),
    )
    fn.restype = ctypes.c_ulong

    size = ctypes.c_ulong(16 * 1024)
    flags = GAA_FLAG_SKIP_ANYCAST | GAA_FLAG_SKIP_MULTICAST | GAA_FLAG_SKIP_DNS_SERVER

    for _ in range(3):
        buffer = ctypes.create_string_buffer(size.value)
        result = fn(
            AF_UNSPEC,
            flags,
            None,
            ctypes.cast(buffer, ctypes.POINTER(_IP_ADAPTER_ADDRESSES)),
            ctypes.byref(size),
        )
        if result == ERROR_BUFFER_OVERFLOW:
            continue
        if result != 0:
            return []

        adapters = []
        current = ctypes.cast(buffer, ctypes.POINTER(_IP_ADAPTER_ADDRESSES))
        while current:
            adapter = current.contents
            addresses = []
            unicast = adapter.FirstUnicastAddress
            while unicast:
                entry = unicast.contents
                ip = _sockaddr_to_ip(entry.Address.lpSockaddr, entry.Address.iSockaddrLength)
                if ip:
                    addresses.append(ip)
                unicast = entry.Next
            adapters.append({
                "name": adapter.FriendlyName or adapter.Description or "Adapter",
                "description": adapter.Description or "",
                "if_type": adapter.IfType,
                "up": adapter.OperStatus == _IF_OPER_UP,
                "addresses": addresses,
            })
            current = adapter.Next
        return adapters

    return []


def _classify(name: str, description: str, if_type: int, ip: str) -> str:
    """Return one of: loopback, wifi, ethernet, vpn, tailscale, other."""
    text = f"{name} {description}".lower()

    try:
        address = ipaddress.ip_address(ip)
    except ValueError:
        return "other"

    if address.is_loopback:
        return "loopback"
    if address.is_link_local:
        # 169.254.x.x and fe80::/10 are auto-assigned and never useful here.
        return "other"
    if if_type == IF_TYPE_LOOPBACK:
        return "other"

    if address.version == 4 and address in CGNAT:
        return "tailscale"

    looks_virtual = any(keyword in text for keyword in VPN_KEYWORDS)
    if looks_virtual or if_type in (IF_TYPE_TUNNEL, IF_TYPE_PPP):
        return "vpn"
    if any(keyword in text for keyword in VIRTUAL_KEYWORDS):
        return "virtual"
    if if_type == IF_TYPE_IEEE80211:
        return "wifi"
    if if_type == IF_TYPE_ETHERNET:
        return "ethernet"
    return "other"


def _rank(entry: dict) -> int:
    """Lower is better when picking the address for the phone."""
    ip = entry["ip"]
    kind = entry["kind"]
    try:
        address = ipaddress.ip_address(ip)
    except ValueError:
        return 99

    if kind == "other" or address.is_link_local:
        return 90
    if kind == "loopback":
        return 95
    if kind in ("wifi", "ethernet"):
        if address.version == 4:
            if address in PRIVATE_192:
                return 0
            if address in PRIVATE_10:
                return 1
            if address in PRIVATE_172:
                return 2
            return 3
        return 4  # global IPv6 on a real adapter
    if kind == "tailscale":
        return 40
    if kind == "virtual":
        return 30
    if kind == "vpn":
        return 60
    return 50


def get_interfaces():
    """Return every usable IPv4/IPv6 address with adapter metadata.

    Each entry is a dict with keys: name, description, ip, kind, up, rank.
    Physical Wi-Fi/Ethernet adapters come first and are marked recommended.
    """
    entries = []
    seen = set()

    for adapter in _windows_adapters():
        for ip in adapter["addresses"]:
            key = ip.split("%", 1)[0]
            if key in seen:
                continue
            seen.add(key)
            kind = _classify(adapter["name"], adapter["description"], adapter["if_type"], key)
            if kind == "other" and key.startswith("169.254."):
                continue
            entries.append({
                "name": adapter["name"],
                "description": adapter["description"],
                "ip": key,
                "kind": kind,
                "up": adapter["up"],
                "rank": _rank({"ip": key, "kind": kind}),
            })

    if not entries:
        entries = _fallback_interfaces()

    # A down adapter should never be recommended over an active one.
    entries.sort(key=lambda item: (0 if item["up"] else 1, item["rank"], item["ip"]))

    recommended = False
    for entry in entries:
        entry["recommended"] = False
    for entry in entries:
        if entry["up"] and entry["kind"] in ("wifi", "ethernet"):
            entry["recommended"] = True
            recommended = True
            break
    if not recommended:
        for entry in entries:
            if entry["up"] and entry["kind"] in ("tailscale", "vpn", "other"):
                entry["recommended"] = True
                break

    return entries

def _fallback_interfaces():
    """Best-effort detection when GetAdaptersAddresses is unavailable."""
    ips = set()
    try:
        default_ip = get_default_route_ip()
        if default_ip:
            ips.add(default_ip)
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if not ip.startswith("127."):
                ips.add(ip)
    except OSError:
        pass

    entries = []
    for ip in sorted(ips):
        if ip.startswith("169.254."):
            continue
        kind = "wifi" if ip.startswith("192.168.") else "other"
        entries.append({
            "name": "Сеть",
            "description": "",
            "ip": ip,
            "kind": kind,
            "up": True,
            "rank": _rank({"ip": ip, "kind": kind}),
        })
    return entries


def get_default_route_ip():
    """IP of the adapter used for internet traffic (may be a VPN tunnel)."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        ip = sock.getsockname()[0]
        return ip if ip and not ip.startswith("127.") else None
    except OSError:
        return None
    finally:
        sock.close()


def get_primary_ip():
    """The address the phone should use, or 127.0.0.1 as a last resort."""
    for entry in get_interfaces():
        if entry.get("recommended"):
            return entry["ip"]
    return "127.0.0.1"


def get_lan_ips():
    """All usable IPv4 addresses, best candidate first."""
    ips = []
    for entry in get_interfaces():
        if ":" in entry["ip"]:
            continue
        if entry["kind"] in ("loopback", "other", "virtual"):
            continue
        if entry["ip"] not in ips:
            ips.append(entry["ip"])
    return ips


def get_url_options(port: int):
    """Build the list of URLs shown to the user / returned by /api/network."""
    candidates = []
    for entry in get_interfaces():
        if entry["kind"] in ("loopback", "other", "virtual"):
            continue
        try:
            address = ipaddress.ip_address(entry["ip"])
        except ValueError:
            continue
        if address.is_link_local or address.is_loopback:
            continue
        if ":" in entry["ip"]:
            url = f"http://[{entry['ip']}]:{port}/"
        else:
            url = f"http://{entry['ip']}:{port}/"
        candidates.append({
            "url": url,
            "ip": entry["ip"],
            "label": entry["name"],
            "kind": entry["kind"],
            "recommended": bool(entry.get("recommended")),
            "up": entry["up"],
        })

    # IPv6 addresses make the list noisy and phones rarely need them; keep
    # them only when there is no IPv4 address at all.
    if any(":" not in item["ip"] for item in candidates):
        candidates = [item for item in candidates if ":" not in item["ip"]]

    # Tailscale may expose its address a moment before the adapter shows up
    # in GetAdaptersAddresses, so add it explicitly when it is running.
    ts = tailscale_state()
    if ts.get("running"):
        known = {item["ip"] for item in candidates}
        for ip in ts.get("ips", []):
            if ip in known:
                continue
            candidates.append({
                "url": f"http://{ip}:{port}/",
                "ip": ip,
                "label": "Tailscale",
                "kind": "tailscale",
                "recommended": False,
                "up": True,
            })

    candidates.sort(key=lambda item: (0 if item["recommended"] else 1, 0 if item["kind"] in ("wifi", "ethernet") else 1))
    return candidates


def has_vpn():
    """True when at least one VPN-like adapter is active."""
    return any(entry["kind"] in ("vpn", "tailscale") for entry in get_interfaces())


_tailscale_cache = {"at": 0.0, "value": None}


def tailscale_state():
    """Report whether Tailscale is installed and running.

    Tailscale is a useful fallback: if the phone and the PC are in the same
    tailnet, the trackpad works from any network and no port forwarding or
    LAN access is needed. On Android it replaces the active VPN slot.
    """
    now = time.time()
    cached = _tailscale_cache["value"]
    if cached is not None and now - _tailscale_cache["at"] < 10:
        return cached

    exe = shutil.which("tailscale")
    if not exe:
        for candidate in (
            Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Tailscale" / "tailscale.exe",
            Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Tailscale" / "tailscale.exe",
        ):
            if candidate.exists():
                exe = str(candidate)
                break

    if not exe:
        result = {"installed": False, "running": False, "ips": []}
        _tailscale_cache.update({"at": now, "value": result})
        return result

    ips = []
    running = False
    try:
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        completed = subprocess.run(
            [exe, "status", "--json"],
            capture_output=True,
            timeout=4,
            creationflags=flags,
        )
        if completed.returncode == 0 and completed.stdout:
            data = json.loads(completed.stdout.decode("utf-8", "replace") or "{}")
            running = data.get("BackendState") == "Running"
            ips = [ip for ip in (data.get("Self", {}).get("TailscaleIPs") or []) if ":" not in ip]
    except (OSError, subprocess.SubprocessError, ValueError):
        pass

    result = {"installed": True, "running": running, "ips": ips}
    _tailscale_cache.update({"at": now, "value": result})
    return result


def describe() -> str:
    """Human readable one-line summary for logs."""
    parts = []
    for entry in get_interfaces():
        if entry["kind"] == "loopback":
            continue
        parts.append(f"{entry['ip']} ({entry['name']}, {entry['kind']})")
    return ", ".join(parts) if parts else "no network"


if __name__ == "__main__":
    print(describe())
    for item in get_url_options(5050):
        print(("  * " if item["recommended"] else "    ") + item["url"] + "  [" + item["label"] + ", " + item["kind"] + "]")
