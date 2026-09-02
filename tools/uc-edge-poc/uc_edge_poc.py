#!/usr/bin/env python3
"""Run a paired Universal Control virtual edge router on macOS."""

from __future__ import annotations

import argparse
import hmac
import json
import secrets
import socket
import threading
import time
import uuid
import webbrowser
from collections import deque
from dataclasses import dataclass, replace
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

try:
    import AppKit
    import CoreFoundation
    import Quartz
except ImportError as exc:  # pragma: no cover - dependency installation failure
    raise SystemExit("PyObjC is required. Run this tool with: uv run uc_edge_poc.py") from exc

from edge_core import (
    Display,
    DirectionalBinding,
    DirectionalNavigation,
    EdgeConnection,
    EdgeEndpoint,
    HotkeyDestination,
    HostSnapshot,
    KeyboardSwitch,
    Point,
    TransportEdge,
    boosted_transport_magnitude,
    inside_display,
    inward_edge_depth,
    inward_delta,
    near_edge_segment,
    outward_component,
    point_inside_display,
    point_inside_edge,
    point_on_edge,
    propose_directional_navigation,
    should_trigger,
    span_fraction,
    target_delta,
    transform_handoff_delta,
    validate_connections,
    validate_directional_navigation,
    validate_keyboard_switch,
)
from peer_transport import OutboundWorker, PeerRecord, request_json


MOUSE_EVENT_TYPES = (
    Quartz.kCGEventMouseMoved,
    Quartz.kCGEventLeftMouseDragged,
    Quartz.kCGEventRightMouseDragged,
    Quartz.kCGEventOtherMouseDragged,
)
KEY_EVENT_TYPES = (Quartz.kCGEventKeyDown, Quartz.kCGEventKeyUp)
HOTKEY_FLAG_MASKS = {
    "control": Quartz.kCGEventFlagMaskControl,
    "option": Quartz.kCGEventFlagMaskAlternate,
    "shift": Quartz.kCGEventFlagMaskShift,
    "command": Quartz.kCGEventFlagMaskCommand,
}
HOTKEY_RELEVANT_FLAGS = sum(HOTKEY_FLAG_MASKS.values())
ARROW_KEY_DIRECTIONS = {
    123: "left",
    124: "right",
    125: "bottom",
    126: "top",
}
UI_DIR = Path(__file__).with_name("ui")
DEFAULT_STATE_PATH = Path.home() / ".uc-edge-lab" / "state.json"
AGENT_SCHEMA_VERSION = 11


@dataclass
class RedirectState:
    handoff_id: str
    connection_id: str
    source_edge: str
    transport: TransportEdge
    normalized: float
    expires_at: float
    restore_point: Point
    event_count: int = 0
    hotkey: bool = False


@dataclass
class PendingArrival:
    handoff_id: str
    connection_id: str
    normalized: float
    expires_at: float
    observed_events: int = 0
    destination: HotkeyDestination | None = None


@dataclass
class ArrivalPlacement:
    display_key: str
    destination_edge: str
    point: Point
    expires_at: float
    source_endpoint: EdgeEndpoint | None = None
    destination_endpoint: EdgeEndpoint | None = None
    transport: TransportEdge | None = None
    rewritten_events: int = 0
    forced_warps: int = 0


@dataclass(frozen=True)
class ArrivalLatch:
    display_key: str
    edge: str


def _cg_point(point: Point) -> Any:
    return Quartz.CGPointMake(point.x, point.y)


def _display_names() -> dict[int, str]:
    names: dict[int, str] = {}
    for screen in AppKit.NSScreen.screens():
        description = dict(screen.deviceDescription())
        display_id = description.get("NSScreenNumber")
        if display_id is not None:
            names[int(display_id)] = str(screen.localizedName())
    return names


def _display_key(display_id: int) -> str:
    return "-".join(
        (
            f"{int(Quartz.CGDisplayVendorNumber(display_id)):04x}",
            f"{int(Quartz.CGDisplayModelNumber(display_id)):04x}",
            f"{int(Quartz.CGDisplaySerialNumber(display_id)):08x}",
            f"{int(Quartz.CGDisplayUnitNumber(display_id)):02x}",
        )
    )


def list_displays() -> list[Display]:
    result = Quartz.CGGetActiveDisplayList(32, None, None)
    if not isinstance(result, tuple) or len(result) != 3:
        raise RuntimeError(f"unexpected CGGetActiveDisplayList result: {result!r}")
    error, display_ids, count = result
    if error != Quartz.kCGErrorSuccess:
        raise RuntimeError(f"CGGetActiveDisplayList failed with error {error}")

    main_id = int(Quartz.CGMainDisplayID())
    names = _display_names()
    displays: list[Display] = []
    for raw_display_id in list(display_ids)[: int(count)]:
        display_id = int(raw_display_id)
        bounds = Quartz.CGDisplayBounds(display_id)
        displays.append(
            Display(
                id=display_id,
                key=_display_key(display_id),
                index=0,
                name=names.get(display_id, "Display"),
                x=float(bounds.origin.x),
                y=float(bounds.origin.y),
                width=float(bounds.size.width),
                height=float(bounds.size.height),
                main=display_id == main_id,
            )
        )

    displays.sort(key=lambda item: (item.x, item.y, item.id))
    return [replace(display, index=index) for index, display in enumerate(displays)]


def local_addresses() -> list[str]:
    hostname = socket.gethostname()
    addresses = {hostname if hostname.endswith(".local") else f"{hostname}.local"}
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # No packet is sent; connect only asks the routing table for the LAN source IP.
        probe.connect(("192.0.2.1", 9))
        address = str(probe.getsockname()[0])
        if not address.startswith("127."):
            addresses.add(address)
    except OSError:
        pass
    finally:
        probe.close()
    return sorted(addresses)


class EdgeRouter:
    def __init__(self, state_path: Path, peer_port: int) -> None:
        self._lock = threading.RLock()
        self._state_path = state_path
        self._peer_port = peer_port
        persisted = self._load_state()
        self._node_id = str(persisted.get("node_id") or uuid.uuid4())
        self._node_name = str(persisted.get("node_name") or socket.gethostname())
        self._node_token = str(persisted.get("node_token") or secrets.token_urlsafe(32))
        self._pair_code = str(persisted.get("pair_code") or f"{secrets.randbelow(1_000_000):06d}")
        self._armed = bool(persisted.get("armed", False))
        self._threshold = float(persisted.get("threshold", 4.0))
        state_version = int(persisted.get("version", 0))
        self._redirect_ms = int(persisted.get("redirect_ms", 1800))
        self._cooldown_ms = int(persisted.get("cooldown_ms", 900))
        self._arrival_timeout_ms = int(persisted.get("arrival_timeout_ms", 3200))
        self._arrival_guard_ms = int(persisted.get("arrival_guard_ms", 650))
        self._transport_gain = float(persisted.get("transport_gain", 3.0))
        self._transport_min_delta = float(persisted.get("transport_min_delta", 8.0))
        self._transport_max_delta = float(persisted.get("transport_max_delta", 24.0))
        if state_version < 3:
            if self._redirect_ms == 700:
                self._redirect_ms = 1800
            if self._arrival_timeout_ms == 2200:
                self._arrival_timeout_ms = 3200
        if state_version < 4 and self._arrival_guard_ms == 650:
            self._arrival_guard_ms = 1200
        if state_version < 6 and self._arrival_guard_ms == 1200:
            self._arrival_guard_ms = 220
        if self._arrival_guard_ms == 80:
            self._arrival_guard_ms = 220

        self._display_cache = list_displays()
        peer_value = persisted.get("peer")
        self._peer = PeerRecord.from_json(peer_value) if isinstance(peer_value, dict) else None
        self._connections: tuple[EdgeConnection, ...] = ()
        self._load_connections(persisted.get("connections", []))
        self._keyboard_switch: KeyboardSwitch | None = None
        self._load_keyboard_switch(persisted.get("keyboard_switch"))
        self._directional_navigation: DirectionalNavigation | None = None
        self._load_directional_navigation(persisted.get("directional_navigation"))

        self._thread: threading.Thread | None = None
        self._tap: Any = None
        self._run_loop: Any = None
        self._run_loop_source: Any = None
        self._tap_level = "stopped"
        self._error: str | None = None
        self._last_point: Point | None = None
        self._last_restore_point: Point | None = None
        self._redirect: RedirectState | None = None
        self._pending_arrival: PendingArrival | None = None
        self._arrival_placement: ArrivalPlacement | None = None
        self._arrival_latch: ArrivalLatch | None = None
        self._hotkey_pressed: int | None = None
        self._cooldown_until = 0.0
        self._events: deque[dict[str, Any]] = deque(maxlen=120)
        self._callback_ref = self._handle_event
        self._closing = threading.Event()
        self._outbound = OutboundWorker(self.peer, self._network_error)
        self._heartbeat_thread = threading.Thread(
            target=self._heartbeat_loop,
            name="uc-edge-peer-heartbeat",
            daemon=True,
        )
        self._heartbeat_thread.start()
        with self._lock:
            self._save_locked()

    def _load_state(self) -> dict[str, Any]:
        try:
            return json.loads(self._state_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"could not load {self._state_path}: {exc}") from exc

    def _load_connections(self, values: Any) -> None:
        if not isinstance(values, list):
            return
        parsed: list[EdgeConnection] = []
        for value in values:
            try:
                parsed.append(EdgeConnection.from_json(value))
            except (KeyError, TypeError, ValueError):
                continue
        self._connections = tuple(parsed)

    def _load_keyboard_switch(self, value: Any) -> None:
        if not isinstance(value, dict):
            return
        try:
            self._keyboard_switch = KeyboardSwitch.from_json(value)
        except (KeyError, TypeError, ValueError):
            self._keyboard_switch = None

    def _load_directional_navigation(self, value: Any) -> None:
        if not isinstance(value, dict):
            return
        try:
            self._directional_navigation = DirectionalNavigation.from_json(value)
        except (KeyError, TypeError, ValueError):
            self._directional_navigation = None

    def _save_locked(self) -> None:
        value = {
            "version": 8,
            "node_id": self._node_id,
            "node_name": self._node_name,
            "node_token": self._node_token,
            "pair_code": self._pair_code,
            "peer": self._peer.to_json(include_secret=True) if self._peer else None,
            "connections": [connection.to_json() for connection in self._connections],
            "keyboard_switch": (
                self._keyboard_switch.to_json() if self._keyboard_switch else None
            ),
            "directional_navigation": (
                self._directional_navigation.to_json()
                if self._directional_navigation
                else None
            ),
            "armed": self._armed,
            "threshold": self._threshold,
            "redirect_ms": self._redirect_ms,
            "cooldown_ms": self._cooldown_ms,
            "arrival_timeout_ms": self._arrival_timeout_ms,
            "arrival_guard_ms": self._arrival_guard_ms,
            "transport_gain": self._transport_gain,
            "transport_min_delta": self._transport_min_delta,
            "transport_max_delta": self._transport_max_delta,
        }
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._state_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
        temporary.replace(self._state_path)

    def log(self, kind: str, message: str) -> None:
        with self._lock:
            self._events.appendleft(
                {"time": time.strftime("%H:%M:%S"), "kind": kind, "message": message}
            )

    def _network_error(self, message: str) -> None:
        self.log("network", message)

    def displays(self, refresh: bool = True) -> list[Display]:
        if refresh:
            displays = list_displays()
            with self._lock:
                self._display_cache = displays
            return displays
        with self._lock:
            return list(self._display_cache)

    def local_host(self, refresh: bool = True) -> HostSnapshot:
        return HostSnapshot(self._node_id, self._node_name, tuple(self.displays(refresh)))

    def peer(self) -> PeerRecord | None:
        with self._lock:
            return self._peer

    def _hosts_for_validation(self) -> tuple[HostSnapshot, ...]:
        local = self.local_host(refresh=True)
        peer = self.peer()
        return (local, peer.host) if peer else (local,)

    def pair(self, address: str, port: int, pair_code: str) -> None:
        local = self.local_host(refresh=True)
        payload = {
            "host": local.to_json(),
            "token": self._node_token,
            "port": self._peer_port,
            "connections": [connection.to_json() for connection in self._connections],
            "keyboard_switch": (
                self._keyboard_switch.to_json() if self._keyboard_switch else None
            ),
            "directional_navigation": (
                self._directional_navigation.to_json()
                if self._directional_navigation
                else None
            ),
        }
        response = request_json(
            "POST",
            address,
            port,
            "/peer/pair",
            payload=payload,
            pair_code=pair_code.strip(),
            timeout=4.0,
        )
        remote = HostSnapshot.from_json(response["host"])
        if remote.id == self._node_id:
            raise ValueError("cannot pair this Mac with itself")
        peer = PeerRecord(
            id=remote.id,
            name=remote.name,
            address=address.strip(),
            port=int(response.get("port", port)),
            token=str(response["token"]),
            displays=remote.displays,
            connected=True,
            last_seen=time.time(),
        )
        remote_connections = tuple(
            EdgeConnection.from_json(value) for value in response.get("connections", [])
        )
        remote_keyboard_value = response.get("keyboard_switch")
        remote_keyboard = (
            KeyboardSwitch.from_json(remote_keyboard_value)
            if isinstance(remote_keyboard_value, dict)
            else None
        )
        remote_navigation_value = response.get("directional_navigation")
        remote_navigation = (
            DirectionalNavigation.from_json(remote_navigation_value)
            if isinstance(remote_navigation_value, dict)
            else None
        )
        with self._lock:
            if self._peer is not None and self._peer.id != peer.id:
                self._connections = ()
                self._keyboard_switch = None
                self._directional_navigation = None
            self._peer = peer
            if not self._connections and remote_connections:
                self._connections = validate_connections(
                    remote_connections,
                    (local, remote),
                )
            if self._keyboard_switch is None and remote_keyboard is not None:
                self._keyboard_switch = validate_keyboard_switch(
                    remote_keyboard,
                    self._connections,
                    (local, remote),
                )
            if self._directional_navigation is None and remote_navigation is not None:
                self._directional_navigation = validate_directional_navigation(
                    remote_navigation,
                    self._connections,
                    (local, remote),
                )
            self._save_locked()
        self.log("peer", f"paired with {remote.name} at {address}:{peer.port}")
        if self._connections:
            self._sync_connections()
        if self._keyboard_switch:
            self._sync_keyboard_switch()
        if self._directional_navigation:
            self._sync_directional_navigation()

    def accept_pair(
        self,
        payload: dict[str, Any],
        pair_code: str,
        remote_address: str,
    ) -> dict[str, Any]:
        if not hmac.compare_digest(pair_code, self._pair_code):
            raise PermissionError("incorrect pairing code")
        remote = HostSnapshot.from_json(payload["host"])
        if remote.id == self._node_id:
            raise ValueError("cannot pair this Mac with itself")
        peer = PeerRecord(
            id=remote.id,
            name=remote.name,
            address=remote_address,
            port=int(payload.get("port", self._peer_port)),
            token=str(payload["token"]),
            displays=remote.displays,
            connected=True,
            last_seen=time.time(),
        )
        incoming = tuple(
            EdgeConnection.from_json(value) for value in payload.get("connections", [])
        )
        incoming_keyboard_value = payload.get("keyboard_switch")
        incoming_keyboard = (
            KeyboardSwitch.from_json(incoming_keyboard_value)
            if isinstance(incoming_keyboard_value, dict)
            else None
        )
        incoming_navigation_value = payload.get("directional_navigation")
        incoming_navigation = (
            DirectionalNavigation.from_json(incoming_navigation_value)
            if isinstance(incoming_navigation_value, dict)
            else None
        )
        local = self.local_host(refresh=True)
        with self._lock:
            if self._peer is not None and self._peer.id != peer.id:
                self._connections = ()
                self._keyboard_switch = None
                self._directional_navigation = None
            self._peer = peer
            if not self._connections and incoming:
                self._connections = validate_connections(incoming, (local, remote))
            if self._keyboard_switch is None and incoming_keyboard is not None:
                self._keyboard_switch = validate_keyboard_switch(
                    incoming_keyboard,
                    self._connections,
                    (local, remote),
                )
            if self._directional_navigation is None and incoming_navigation is not None:
                self._directional_navigation = validate_directional_navigation(
                    incoming_navigation,
                    self._connections,
                    (local, remote),
                )
            self._save_locked()
            connections = [connection.to_json() for connection in self._connections]
            keyboard_switch = (
                self._keyboard_switch.to_json() if self._keyboard_switch else None
            )
            directional_navigation = (
                self._directional_navigation.to_json()
                if self._directional_navigation
                else None
            )
        self.log("peer", f"accepted pairing from {remote.name}")
        return {
            "host": local.to_json(),
            "token": self._node_token,
            "port": self._peer_port,
            "connections": connections,
            "keyboard_switch": keyboard_switch,
            "directional_navigation": directional_navigation,
        }

    def disconnect_peer(self) -> None:
        with self._lock:
            self._peer = None
            self._connections = ()
            self._keyboard_switch = None
            self._directional_navigation = None
            self._armed = False
            self._pending_arrival = None
            self._redirect = None
            self._arrival_placement = None
            self._arrival_latch = None
            self._hotkey_pressed = None
            self._save_locked()
        self.stop_tap()
        self.log("peer", "peer disconnected; routing disarmed")

    def _heartbeat_loop(self) -> None:
        last_connected: bool | None = None
        while not self._closing.wait(1.5):
            peer = self.peer()
            if peer is None:
                last_connected = None
                continue
            try:
                response = request_json(
                    "GET",
                    peer.address,
                    peer.port,
                    "/peer/state",
                    token=peer.token,
                    timeout=1.0,
                )
                snapshot = HostSnapshot.from_json(response["host"])
                with self._lock:
                    if self._peer and self._peer.id == peer.id:
                        self._peer = self._peer.with_snapshot(
                            snapshot,
                            time.time(),
                            response,
                        )
                if last_connected is False:
                    self.log("peer", f"{snapshot.name} is reachable again")
                last_connected = True
            except (KeyError, TypeError, ValueError, RuntimeError) as exc:
                with self._lock:
                    if self._peer and self._peer.id == peer.id:
                        self._peer = replace(
                            self._peer,
                            connected=False,
                            error=str(exc),
                        )
                if last_connected is not False:
                    self.log("peer", str(exc))
                last_connected = False

    def peer_state(self) -> dict[str, Any]:
        cursor = Quartz.CGEventGetLocation(Quartz.CGEventCreate(None))
        with self._lock:
            thread_alive = bool(self._thread and self._thread.is_alive())
            return {
                "agent_schema": AGENT_SCHEMA_VERSION,
                "host": self.local_host(refresh=True).to_json(),
                "armed": self._armed,
                "tap": {
                    "running": thread_alive and self._tap is not None,
                    "level": self._tap_level,
                    "error": self._error,
                },
                "permissions": self.permissions(),
                "settings": {
                    "threshold": self._threshold,
                    "redirect_ms": self._redirect_ms,
                    "cooldown_ms": self._cooldown_ms,
                    "arrival_timeout_ms": self._arrival_timeout_ms,
                    "arrival_guard_ms": self._arrival_guard_ms,
                    "transport_gain": self._transport_gain,
                    "transport_min_delta": self._transport_min_delta,
                    "transport_max_delta": self._transport_max_delta,
                },
                "cursor": {"x": float(cursor.x), "y": float(cursor.y)},
                "redirecting": self._redirect is not None,
                "pending_arrival": self._pending_arrival is not None,
                "placing_arrival": self._arrival_placement is not None,
                "arrival_latched": self._arrival_latch is not None,
                "keyboard_switch": (
                    self._keyboard_switch.to_json() if self._keyboard_switch else None
                ),
                "directional_navigation": (
                    self._directional_navigation.to_json()
                    if self._directional_navigation
                    else None
                ),
                "events": list(self._events)[:40],
            }

    def authorized(self, authorization: str) -> bool:
        return hmac.compare_digest(authorization, f"Bearer {self._node_token}")

    def replace_connections(self, values: list[dict[str, Any]], sync: bool = True) -> None:
        connections = tuple(EdgeConnection.from_json(value) for value in values)
        connections = validate_connections(connections, self._hosts_for_validation())
        keyboard_changed = False
        navigation_changed = False
        with self._lock:
            self._connections = connections
            if self._keyboard_switch is not None:
                route = next(
                    (
                        item
                        for item in connections
                        if item.id == self._keyboard_switch.connection_id
                    ),
                    None,
                )
                if route is None:
                    self._keyboard_switch = None
                    keyboard_changed = True
            if self._directional_navigation is not None:
                connection_ids = {item.id for item in connections}
                bindings = tuple(
                    binding
                    for binding in self._directional_navigation.bindings
                    if binding.connection_id is None
                    or binding.connection_id in connection_ids
                )
                if bindings != self._directional_navigation.bindings:
                    self._directional_navigation = replace(
                        self._directional_navigation,
                        bindings=bindings,
                    )
                    navigation_changed = True
            self._redirect = None
            self._arrival_placement = None
            self._arrival_latch = None
            self._save_locked()
        self.log("config", f"saved {len(connections)} bidirectional edge mapping(s)")
        if sync:
            self._sync_connections()
            if keyboard_changed:
                self._sync_keyboard_switch()
            if navigation_changed:
                self._sync_directional_navigation()

    def _sync_connections(self) -> None:
        self._outbound.send(
            "/peer/connections",
            {"connections": [connection.to_json() for connection in self._connections]},
        )

    def replace_keyboard_switch(self, value: Any, sync: bool = True) -> None:
        shortcut = KeyboardSwitch.from_json(value) if isinstance(value, dict) else None
        if shortcut is not None:
            shortcut = validate_keyboard_switch(
                shortcut,
                self._connections,
                self._hosts_for_validation(),
            )
        with self._lock:
            self._keyboard_switch = shortcut
            self._hotkey_pressed = None
            self._save_locked()
        self.log(
            "config",
            "keyboard switch saved" if shortcut else "keyboard switch cleared",
        )
        if sync:
            self._sync_keyboard_switch()

    def replace_directional_navigation(self, value: Any, sync: bool = True) -> None:
        navigation = (
            DirectionalNavigation.from_json(value) if isinstance(value, dict) else None
        )
        if navigation is not None:
            navigation = validate_directional_navigation(
                navigation,
                self._connections,
                self._hosts_for_validation(),
            )
        with self._lock:
            self._directional_navigation = navigation
            self._hotkey_pressed = None
            self._save_locked()
        self.log(
            "config",
            (
                f"saved {len(navigation.bindings)} contextual arrow rule(s)"
                if navigation
                else "contextual arrow navigation cleared"
            ),
        )
        if sync:
            self._sync_directional_navigation()

    def propose_directional_navigation(
        self,
        modifiers: tuple[str, ...] = ("command",),
    ) -> DirectionalNavigation:
        navigation = propose_directional_navigation(
            self._hosts_for_validation(),
            self._connections,
            modifiers,
        )
        self.replace_directional_navigation(navigation.to_json())
        return navigation

    def _sync_directional_navigation(self) -> None:
        self._outbound.send(
            "/peer/directional-navigation",
            {
                "directional_navigation": (
                    self._directional_navigation.to_json()
                    if self._directional_navigation
                    else None
                )
            },
        )

    def _sync_keyboard_switch(self) -> None:
        self._outbound.send(
            "/peer/keyboard-switch",
            {
                "keyboard_switch": (
                    self._keyboard_switch.to_json() if self._keyboard_switch else None
                )
            },
        )

    def set_armed(self, armed: bool, sync: bool = True) -> None:
        with self._lock:
            self._armed = armed
            if not armed:
                self._redirect = None
                self._pending_arrival = None
                self._arrival_placement = None
                self._arrival_latch = None
                self._hotkey_pressed = None
            self._save_locked()
        if armed:
            self.start_tap()
        else:
            self.stop_tap()
        self.log("routing", "armed" if armed else "stopped")
        if sync and self.peer() is not None:
            self._outbound.send("/peer/routing", {"armed": armed})

    def start_tap(self) -> None:
        try:
            self.displays(refresh=True)
        except Exception as exc:
            with self._lock:
                self._error = f"could not enumerate displays: {exc}"
            self.log("error", self._error)
            return
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._error = None
            self._thread = threading.Thread(
                target=self._tap_thread,
                name="uc-edge-event-tap",
                daemon=True,
            )
            self._thread.start()

    def stop_tap(self) -> None:
        with self._lock:
            tap = self._tap
            run_loop = self._run_loop
        if tap is not None:
            Quartz.CGEventTapEnable(tap, False)
        if run_loop is not None:
            CoreFoundation.CFRunLoopStop(run_loop)

    def restore(self) -> None:
        with self._lock:
            point = self._last_restore_point
        self.set_armed(False)
        if point is not None:
            Quartz.CGWarpMouseCursorPosition(_cg_point(point))
            self.log("safety", f"restored cursor to {point.x:.0f},{point.y:.0f}")

    def request_access(self) -> dict[str, bool | None]:
        listen = (
            bool(Quartz.CGRequestListenEventAccess())
            if hasattr(Quartz, "CGRequestListenEventAccess")
            else None
        )
        post = (
            bool(Quartz.CGRequestPostEventAccess())
            if hasattr(Quartz, "CGRequestPostEventAccess")
            else None
        )
        self.log("access", "macOS input permission request sent")
        return {"listen": listen, "post": post}

    def permissions(self) -> dict[str, bool | None]:
        listen = (
            bool(Quartz.CGPreflightListenEventAccess())
            if hasattr(Quartz, "CGPreflightListenEventAccess")
            else None
        )
        post = (
            bool(Quartz.CGPreflightPostEventAccess())
            if hasattr(Quartz, "CGPreflightPostEventAccess")
            else None
        )
        return {"listen": listen, "post": post}

    def state(self) -> dict[str, Any]:
        local = self.local_host(refresh=True)
        with self._lock:
            peer = self._peer
            thread_alive = bool(self._thread and self._thread.is_alive())
            return {
                "agent_schema": AGENT_SCHEMA_VERSION,
                "local": local.to_json(),
                "peer": peer.to_json() if peer else None,
                "connections": [connection.to_json() for connection in self._connections],
                "keyboard_switch": (
                    self._keyboard_switch.to_json() if self._keyboard_switch else None
                ),
                "directional_navigation": (
                    self._directional_navigation.to_json()
                    if self._directional_navigation
                    else None
                ),
                "armed": self._armed,
                "tap": {
                    "running": thread_alive and self._tap is not None,
                    "level": self._tap_level,
                    "error": self._error,
                },
                "permissions": self.permissions(),
                "pairing": {
                    "code": self._pair_code,
                    "port": self._peer_port,
                    "addresses": local_addresses(),
                },
                "settings": {
                    "threshold": self._threshold,
                    "redirect_ms": self._redirect_ms,
                    "cooldown_ms": self._cooldown_ms,
                    "arrival_timeout_ms": self._arrival_timeout_ms,
                    "arrival_guard_ms": self._arrival_guard_ms,
                    "transport_gain": self._transport_gain,
                    "transport_min_delta": self._transport_min_delta,
                    "transport_max_delta": self._transport_max_delta,
                },
                "redirecting": self._redirect is not None,
                "pending_arrival": self._pending_arrival is not None,
                "placing_arrival": self._arrival_placement is not None,
                "arrival_latched": self._arrival_latch is not None,
                "events": list(self._events),
            }

    def receive_handoff(self, payload: dict[str, Any]) -> None:
        handoff_id = str(payload.get("handoff_id") or payload["connection_id"])
        connection_id = str(payload["connection_id"])
        source_host = str(payload["source_host"])
        target_host = str(payload["target_host"])
        normalized = float(payload["normalized"])
        destination_value = payload.get("destination")
        destination = (
            HotkeyDestination.from_json(destination_value)
            if isinstance(destination_value, dict)
            else None
        )
        peer = self.peer()
        if target_host != self._node_id or peer is None or source_host != peer.id:
            raise ValueError("handoff host does not match the active pair")
        if not 0 <= normalized <= 1:
            raise ValueError("handoff position must be normalized")
        if destination is not None:
            displays = {display.key for display in self.displays(refresh=False)}
            if destination.host_id != self._node_id:
                raise ValueError("handoff destination belongs to another Mac")
            if destination.display_key not in displays:
                raise ValueError("handoff destination display is unavailable")
        with self._lock:
            connection = next(
                (
                    item
                    for item in self._connections
                    if item.id == connection_id
                    and (destination is not None or item.enabled)
                ),
                None,
            )
            if connection is None or connection.route_from(self._node_id) is None:
                raise ValueError("handoff references an unavailable connection")
            self._pending_arrival = PendingArrival(
                handoff_id=handoff_id,
                connection_id=connection_id,
                normalized=normalized,
                expires_at=time.monotonic() + self._arrival_timeout_ms / 1000.0,
                destination=destination,
            )
        self.log(
            "handoff",
            (
                "keyboard handoff intent received; waiting for UC input"
                if destination is not None
                else f"handoff intent received at {normalized * 100:.1f}%; waiting for UC input"
            ),
        )

    def complete_handoff(self, payload: dict[str, Any]) -> None:
        handoff_id = str(payload["handoff_id"])
        source_host = str(payload["source_host"])
        target_host = str(payload["target_host"])
        peer = self.peer()
        if source_host != self._node_id or peer is None or target_host != peer.id:
            raise ValueError("handoff completion does not match the active pair")
        with self._lock:
            redirect = self._redirect
            if redirect is None or redirect.handoff_id != handoff_id:
                return
            self._redirect = None
            self._hotkey_pressed = None
            self._cooldown_until = time.monotonic() + self._cooldown_ms / 1000.0
        self.log("handoff", "Universal Control transfer confirmed by peer")

    def cancel_handoff(self, payload: dict[str, Any]) -> None:
        handoff_id = str(payload["handoff_id"])
        with self._lock:
            pending = self._pending_arrival
            if pending is not None and pending.handoff_id == handoff_id:
                self._pending_arrival = None

    def _tap_thread(self) -> None:
        mask = 0
        for event_type in MOUSE_EVENT_TYPES + KEY_EVENT_TYPES:
            mask |= 1 << int(event_type)

        tap = None
        tap_level = "unavailable"
        for location, label in (
            (Quartz.kCGHIDEventTap, "HID"),
            (Quartz.kCGSessionEventTap, "session"),
        ):
            tap = Quartz.CGEventTapCreate(
                location,
                Quartz.kCGHeadInsertEventTap,
                Quartz.kCGEventTapOptionDefault,
                mask,
                self._callback_ref,
                None,
            )
            if tap is not None:
                tap_level = label
                break

        if tap is None:
            with self._lock:
                self._error = (
                    "Could not create an active event tap. Grant Input Monitoring and "
                    "Accessibility access to Terminal/Python, then restart the agent."
                )
                self._tap_level = "failed"
                self._armed = False
                self._save_locked()
            self.log("error", self._error)
            return

        run_loop = CoreFoundation.CFRunLoopGetCurrent()
        source = CoreFoundation.CFMachPortCreateRunLoopSource(None, tap, 0)
        with self._lock:
            self._tap = tap
            self._run_loop = run_loop
            self._run_loop_source = source
            self._tap_level = tap_level
        CoreFoundation.CFRunLoopAddSource(
            run_loop,
            source,
            CoreFoundation.kCFRunLoopCommonModes,
        )
        Quartz.CGEventTapEnable(tap, True)
        self.log("tap", f"active {tap_level} event tap started")
        try:
            CoreFoundation.CFRunLoopRun()
        finally:
            Quartz.CGEventTapEnable(tap, False)
            CoreFoundation.CFRunLoopRemoveSource(
                run_loop,
                source,
                CoreFoundation.kCFRunLoopCommonModes,
            )
            with self._lock:
                self._tap = None
                self._run_loop = None
                self._run_loop_source = None
                self._tap_level = "stopped"
            self.log("tap", "event tap stopped")

    def _handle_event(self, proxy: Any, event_type: int, event: Any, refcon: Any) -> Any:
        del proxy, refcon
        if event_type in (
            Quartz.kCGEventTapDisabledByTimeout,
            Quartz.kCGEventTapDisabledByUserInput,
        ):
            with self._lock:
                tap = self._tap
            if tap is not None:
                Quartz.CGEventTapEnable(tap, True)
            self.log("tap", "event tap was disabled and has been re-enabled")
            return event
        if event_type in KEY_EVENT_TYPES:
            try:
                return self._route_key_event(event_type, event)
            except Exception as exc:  # Never break the system input stream.
                with self._lock:
                    self._error = f"keyboard callback error: {exc}"
                    self._hotkey_pressed = None
                self.log("error", self._error)
                return event
        if event_type not in MOUSE_EVENT_TYPES:
            return event
        try:
            return self._route_mouse_event(event)
        except Exception as exc:  # Never break the system input stream.
            with self._lock:
                self._error = f"event callback error: {exc}"
                self._redirect = None
                self._arrival_placement = None
                self._arrival_latch = None
            self.log("error", self._error)
            return event

    def _route_key_event(self, event_type: int, event: Any) -> Any:
        key_code = int(
            Quartz.CGEventGetIntegerValueField(event, Quartz.kCGKeyboardEventKeycode)
        )
        with self._lock:
            shortcut = self._keyboard_switch
            navigation = self._directional_navigation
            armed = self._armed
            pressed = self._hotkey_pressed

        if event_type == Quartz.kCGEventKeyUp:
            if pressed == key_code:
                with self._lock:
                    self._hotkey_pressed = None
                return None
            return event
        if not armed:
            return event
        actual_flags = int(Quartz.CGEventGetFlags(event)) & HOTKEY_RELEVANT_FLAGS
        is_repeat = bool(
            Quartz.CGEventGetIntegerValueField(event, Quartz.kCGKeyboardEventAutorepeat)
        )
        direction = ARROW_KEY_DIRECTIONS.get(key_code)
        if navigation is not None and navigation.enabled and direction is not None:
            expected_flags = sum(
                HOTKEY_FLAG_MASKS[item] for item in navigation.modifiers
            )
            if actual_flags == expected_flags:
                binding = self._current_directional_binding(navigation, direction)
                if pressed is None and not is_repeat:
                    with self._lock:
                        self._hotkey_pressed = key_code
                    if binding is not None:
                        arrow = {
                            "left": "Left Arrow",
                            "right": "Right Arrow",
                            "top": "Up Arrow",
                            "bottom": "Down Arrow",
                        }[direction]
                        label = "+".join(
                            [*(item.title() for item in navigation.modifiers), arrow]
                        )
                        self._begin_directional_switch(binding, label)
                return None

        if shortcut is None or not shortcut.enabled:
            return event
        expected_flags = sum(HOTKEY_FLAG_MASKS[item] for item in shortcut.modifiers)
        if key_code != shortcut.key_code or actual_flags != expected_flags:
            return event
        if pressed is None and not is_repeat:
            with self._lock:
                self._hotkey_pressed = key_code
            self._begin_keyboard_handoff(shortcut)
        return None

    def _current_directional_binding(
        self,
        navigation: DirectionalNavigation,
        direction: str,
    ) -> DirectionalBinding | None:
        location = Quartz.CGEventGetLocation(Quartz.CGEventCreate(None))
        point = Point(float(location.x), float(location.y))
        display = next(
            (
                item
                for item in self.displays(refresh=False)
                if inside_display(item, point, 1.0)
            ),
            None,
        )
        if display is None:
            return None
        position = span_fraction(display, direction, point)
        return navigation.binding_for(
            self._node_id,
            display.key,
            direction,
            position,
        )

    def _begin_directional_switch(
        self,
        binding: DirectionalBinding,
        label: str,
    ) -> None:
        if binding.target.host_id == self._node_id:
            display = next(
                (
                    item
                    for item in self.displays(refresh=False)
                    if item.key == binding.target.display_key
                ),
                None,
            )
            if display is None:
                self.log("navigation", "local target display is unavailable")
                self._release_keyboard_latch()
                return
            target = point_inside_display(
                display,
                binding.target.x_percent,
                binding.target.y_percent,
            )
            with self._lock:
                self._last_point = target
                self._cooldown_until = (
                    time.monotonic() + self._arrival_guard_ms / 1000.0
                )
            Quartz.CGWarpMouseCursorPosition(_cg_point(target))
            self.log(
                "navigation",
                f"{label}: moved to {display.name} "
                f"at {binding.target.x_percent:.1f}%,{binding.target.y_percent:.1f}%",
            )
            release_timer = threading.Timer(0.4, self._release_keyboard_latch)
            release_timer.daemon = True
            release_timer.start()
            return
        self._begin_remote_keyboard_handoff(
            binding.connection_id or "",
            binding.target,
            label,
        )

    def _begin_keyboard_handoff(self, shortcut: KeyboardSwitch) -> None:
        peer = self.peer()
        destination = shortcut.destination_for(peer.id) if peer else None
        if destination is None:
            self.log("keyboard", "switch destination is unavailable")
            return
        self._begin_remote_keyboard_handoff(
            shortcut.connection_id,
            destination,
            shortcut.key_label,
        )

    def _begin_remote_keyboard_handoff(
        self,
        connection_id: str,
        destination: HotkeyDestination,
        label: str,
    ) -> None:
        now = time.monotonic()
        with self._lock:
            peer = self._peer
            connection = next(
                (
                    item
                    for item in self._connections
                    if item.id == connection_id
                ),
                None,
            )
            if self._redirect is not None or now < self._cooldown_until:
                self.log("keyboard", "switch ignored while another handoff is settling")
                return
        if peer is None or not peer.connected:
            self.log("keyboard", "switch ignored because the paired Mac is offline")
            return
        route = connection.route_from(self._node_id) if connection else None
        if route is None or destination is None:
            self.log("keyboard", "switch references an unavailable route")
            return
        _, route_destination, transport = route
        if route_destination.host_id != peer.id:
            self.log("keyboard", "switch route does not lead to the paired Mac")
            return
        displays = {display.key: display for display in self.displays(refresh=False)}
        if transport.display_key not in displays:
            self.log("keyboard", "switch transport display is unavailable")
            return

        location = Quartz.CGEventGetLocation(Quartz.CGEventCreate(None))
        restore_point = Point(float(location.x), float(location.y))
        redirect = RedirectState(
            handoff_id=uuid.uuid4().hex,
            connection_id=connection.id,
            source_edge=transport.edge,
            transport=transport,
            normalized=0.5,
            expires_at=now + self._redirect_ms / 1000.0,
            restore_point=restore_point,
            hotkey=True,
        )
        with self._lock:
            self._redirect = redirect
            self._pending_arrival = None
            self._last_restore_point = restore_point
        self._outbound.send(
            "/peer/handoff",
            {
                "handoff_id": redirect.handoff_id,
                "connection_id": connection.id,
                "source_host": self._node_id,
                "target_host": peer.id,
                "normalized": redirect.normalized,
                "destination": destination.to_json(),
            },
        )
        self.log(
            "keyboard",
            f"{label}: switching to {peer.name} via {connection.name}",
        )
        release_timer = threading.Timer(0.75, self._release_keyboard_latch)
        release_timer.daemon = True
        release_timer.start()
        timeout_timer = threading.Timer(
            self._redirect_ms / 1000.0,
            self._expire_keyboard_redirect,
            (redirect,),
        )
        timeout_timer.daemon = True
        timeout_timer.start()
        for delay in (0.0, 0.012, 0.024, 0.038, 0.054, 0.072, 0.094, 0.12, 0.15):
            timer = threading.Timer(delay, self._post_keyboard_transport_event, (redirect,))
            timer.daemon = True
            timer.start()

    def _release_keyboard_latch(self) -> None:
        with self._lock:
            self._hotkey_pressed = None

    def _expire_keyboard_redirect(self, redirect: RedirectState) -> None:
        with self._lock:
            if self._redirect is not redirect:
                return
            self._redirect = None
            self._last_point = redirect.restore_point
            self._cooldown_until = time.monotonic() + self._cooldown_ms / 1000.0
        Quartz.CGWarpMouseCursorPosition(_cg_point(redirect.restore_point))
        self._outbound.send(
            "/peer/handoff/cancel",
            {"handoff_id": redirect.handoff_id},
        )
        self.log(
            "keyboard",
            "UC switch was not confirmed; restored the original cursor position",
        )

    def _post_keyboard_transport_event(self, redirect: RedirectState) -> None:
        with self._lock:
            if self._redirect is not redirect or not self._armed:
                return
            display = next(
                (
                    item
                    for item in self._display_cache
                    if item.key == redirect.transport.display_key
                ),
                None,
            )
        if display is None:
            return
        target = point_on_edge(
            display,
            redirect.transport.edge,
            redirect.transport.position(redirect.normalized),
        )
        magnitude = boosted_transport_magnitude(
            self._transport_min_delta,
            self._transport_gain,
            self._transport_min_delta,
            self._transport_max_delta,
        )
        dx, dy = target_delta(redirect.transport.edge, magnitude)
        event = Quartz.CGEventCreateMouseEvent(
            None,
            Quartz.kCGEventMouseMoved,
            _cg_point(target),
            Quartz.kCGMouseButtonLeft,
        )
        Quartz.CGEventSetIntegerValueField(event, Quartz.kCGMouseEventDeltaX, int(dx))
        Quartz.CGEventSetIntegerValueField(event, Quartz.kCGMouseEventDeltaY, int(dy))
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)

    def _route_mouse_event(self, event: Any) -> Any:
        location = Quartz.CGEventGetLocation(event)
        current = Point(float(location.x), float(location.y))
        dx = float(Quartz.CGEventGetIntegerValueField(event, Quartz.kCGMouseEventDeltaX))
        dy = float(Quartz.CGEventGetIntegerValueField(event, Quartz.kCGMouseEventDeltaY))
        now = time.monotonic()

        with self._lock:
            previous = self._last_point
            self._last_point = current
            armed = self._armed
            redirect = self._redirect
            pending = self._pending_arrival
            placement = self._arrival_placement
            arrival_latch = self._arrival_latch
            connections = self._connections
            peer = self._peer

        if not armed:
            return event

        displays = {display.key: display for display in self.displays(refresh=False)}
        if placement is not None:
            display = displays.get(placement.display_key)
            if display is None:
                with self._lock:
                    if self._arrival_placement is placement:
                        self._arrival_placement = None
            else:
                return self._apply_placement_event(
                    event,
                    placement,
                    display,
                    (
                        displays.get(placement.transport.display_key)
                        if placement.transport is not None
                        else None
                    ),
                    current,
                    dx,
                    dy,
                    now,
                )

        if arrival_latch is not None:
            latched_display = displays.get(arrival_latch.display_key)
            if latched_display is None:
                with self._lock:
                    if self._arrival_latch is arrival_latch:
                        self._arrival_latch = None
                arrival_latch = None
            elif (
                inside_display(latched_display, current)
                and inward_edge_depth(latched_display, arrival_latch.edge, current)
                >= max(32.0, self._threshold * 8.0)
            ):
                with self._lock:
                    if self._arrival_latch is arrival_latch:
                        self._arrival_latch = None
                self.log("arrival", "destination edge released after inward movement")
                arrival_latch = None

        if pending is not None:
            if now > pending.expires_at:
                with self._lock:
                    if self._pending_arrival is pending:
                        self._pending_arrival = None
                self.log("handoff", "handoff intent expired without UC input")
            else:
                connection = next(
                    (item for item in connections if item.id == pending.connection_id),
                    None,
                )
                route = connection.route_from(self._node_id) if connection else None
                if route is None:
                    with self._lock:
                        if self._pending_arrival is pending:
                            self._pending_arrival = None
                    self.log("handoff", "discarded intent for an unavailable mapping")
                else:
                    local_endpoint, remote_endpoint, transport = route
                    display_key = (
                        pending.destination.display_key
                        if pending.destination is not None
                        else local_endpoint.display_key
                    )
                    display = displays.get(display_key)
                    transport_display = displays.get(transport.display_key)
                    if (
                        display is not None
                        and transport_display is not None
                        and near_edge_segment(
                            transport_display,
                            transport.edge,
                            transport.start,
                            transport.end,
                            current,
                            max(24.0, self._threshold * 6.0),
                        )
                    ):
                        return self._apply_arrival(
                            event,
                            local_endpoint,
                            remote_endpoint,
                            transport,
                            display,
                            pending,
                            dx,
                            dy,
                            now,
                        )
                    with self._lock:
                        if self._pending_arrival is pending:
                            pending.observed_events += 1
                            first_observation = pending.observed_events == 1
                        else:
                            first_observation = False
                    if first_observation:
                        source_pid = int(
                            Quartz.CGEventGetIntegerValueField(
                                event,
                                Quartz.kCGEventSourceUnixProcessID,
                            )
                        )
                        self.log(
                            "handoff",
                            "ignored input away from the UC transport edge "
                            f"at {current.x:.0f},{current.y:.0f} (source pid {source_pid})",
                        )

        if redirect is not None:
            if now > redirect.expires_at:
                Quartz.CGEventSetLocation(event, _cg_point(redirect.restore_point))
                Quartz.CGEventSetIntegerValueField(event, Quartz.kCGMouseEventDeltaX, 0)
                Quartz.CGEventSetIntegerValueField(event, Quartz.kCGMouseEventDeltaY, 0)
                with self._lock:
                    self._redirect = None
                    self._last_point = redirect.restore_point
                    self._cooldown_until = now + self._cooldown_ms / 1000.0
                self._outbound.send(
                    "/peer/handoff/cancel",
                    {"handoff_id": redirect.handoff_id},
                )
                self.log(
                    "redirect",
                    "UC transfer was not confirmed; restored the original cursor position",
                )
                return event
            outward = outward_component(redirect.source_edge, dx, dy)
            if outward < -0.5 and not redirect.hotkey:
                Quartz.CGEventSetLocation(event, _cg_point(redirect.restore_point))
                Quartz.CGEventSetIntegerValueField(event, Quartz.kCGMouseEventDeltaX, 0)
                Quartz.CGEventSetIntegerValueField(event, Quartz.kCGMouseEventDeltaY, 0)
                with self._lock:
                    self._redirect = None
                    self._last_point = redirect.restore_point
                    self._cooldown_until = now + self._cooldown_ms / 1000.0
                self._outbound.send(
                    "/peer/handoff/cancel",
                    {"handoff_id": redirect.handoff_id},
                )
                self.log("redirect", "cancelled and restored after inward movement")
                return event
            transport_display = displays.get(redirect.transport.display_key)
            if transport_display is None:
                return event
            redirect.event_count += 1
            return self._apply_transport_event(
                event,
                redirect.transport,
                transport_display,
                redirect.normalized,
                max(outward, 1.0),
            )

        with self._lock:
            if now < self._cooldown_until:
                return event
        if peer is None or not peer.connected:
            return event

        for connection in connections:
            if not connection.enabled:
                continue
            route = connection.route_from(self._node_id)
            if route is None:
                continue
            source, destination, transport = route
            if (
                arrival_latch is not None
                and source.display_key == arrival_latch.display_key
                and source.edge == arrival_latch.edge
            ):
                continue
            if destination.host_id != peer.id:
                continue
            source_display = displays.get(source.display_key)
            transport_display = displays.get(transport.display_key)
            if source_display is None or transport_display is None:
                continue
            if not should_trigger(
                source_display,
                source.edge,
                source.start,
                source.end,
                self._threshold,
                previous,
                current,
                dx,
                dy,
            ):
                continue

            edge_position = span_fraction(source_display, source.edge, current)
            normalized = source.normalize(edge_position)
            restore_point = (
                previous
                if previous and inside_display(source_display, previous, self._threshold * 2)
                else current
            )
            redirect = RedirectState(
                handoff_id=uuid.uuid4().hex,
                connection_id=connection.id,
                source_edge=source.edge,
                transport=transport,
                normalized=normalized,
                expires_at=now + self._redirect_ms / 1000.0,
                restore_point=restore_point,
                event_count=1,
            )
            with self._lock:
                self._redirect = redirect
                self._pending_arrival = None
                self._last_restore_point = restore_point
            self._outbound.send(
                "/peer/handoff",
                {
                    "handoff_id": redirect.handoff_id,
                    "connection_id": connection.id,
                    "source_host": self._node_id,
                    "target_host": destination.host_id,
                    "normalized": normalized,
                },
            )
            self.log(
                "handoff",
                f"{connection.name}: sending {normalized * 100:.1f}% to {peer.name}",
            )
            outward = outward_component(source.edge, dx, dy)
            return self._apply_transport_event(
                event,
                transport,
                transport_display,
                normalized,
                outward,
            )
        return event

    def _apply_transport_event(
        self,
        event: Any,
        transport: TransportEdge,
        display: Display,
        normalized: float,
        magnitude: float,
    ) -> Any:
        target = point_on_edge(display, transport.edge, transport.position(normalized))
        magnitude = boosted_transport_magnitude(
            magnitude,
            self._transport_gain,
            self._transport_min_delta,
            self._transport_max_delta,
        )
        target_dx, target_dy = target_delta(transport.edge, magnitude)
        Quartz.CGEventSetLocation(event, _cg_point(target))
        Quartz.CGEventSetIntegerValueField(event, Quartz.kCGMouseEventDeltaX, int(target_dx))
        Quartz.CGEventSetIntegerValueField(event, Quartz.kCGMouseEventDeltaY, int(target_dy))
        with self._lock:
            self._last_point = target
        return event

    def _apply_arrival(
        self,
        event: Any,
        destination: EdgeEndpoint,
        source_endpoint: EdgeEndpoint,
        transport: TransportEdge,
        display: Display,
        pending: PendingArrival,
        dx: float,
        dy: float,
        now: float,
    ) -> Any:
        source = Quartz.CGEventGetLocation(event)
        source_pid = int(
            Quartz.CGEventGetIntegerValueField(
                event,
                Quartz.kCGEventSourceUnixProcessID,
            )
        )
        if pending.destination is not None:
            target = point_inside_display(
                display,
                pending.destination.x_percent,
                pending.destination.y_percent,
            )
            target_dx, target_dy = 0.0, 0.0
        else:
            target = point_inside_edge(
                display,
                destination.edge,
                destination.position(pending.normalized),
            )
            magnitude = max(abs(dx), abs(dy), 1.0)
            target_dx, target_dy = inward_delta(destination.edge, magnitude)
        Quartz.CGEventSetLocation(event, _cg_point(target))
        Quartz.CGEventSetIntegerValueField(event, Quartz.kCGMouseEventDeltaX, int(target_dx))
        Quartz.CGEventSetIntegerValueField(event, Quartz.kCGMouseEventDeltaY, int(target_dy))
        with self._lock:
            self._pending_arrival = None
            self._redirect = None
            self._last_point = target
            self._cooldown_until = now + self._arrival_guard_ms / 1000.0
            self._arrival_placement = ArrivalPlacement(
                display_key=display.key,
                destination_edge=destination.edge,
                point=target,
                expires_at=now + self._arrival_guard_ms / 1000.0,
                source_endpoint=(
                    None if pending.destination is not None else source_endpoint
                ),
                destination_endpoint=(
                    None if pending.destination is not None else destination
                ),
                transport=(None if pending.destination is not None else transport),
            )
            self._arrival_latch = (
                None
                if pending.destination is not None
                else ArrivalLatch(
                    display_key=display.key,
                    edge=destination.edge,
                )
            )
            placement = self._arrival_placement
            peer = self._peer
        Quartz.CGWarpMouseCursorPosition(_cg_point(target))
        if pending.destination is not None:
            self._schedule_placement_warps(placement)
        self._outbound.send(
            "/peer/handoff/complete",
            {
                "handoff_id": pending.handoff_id,
                "connection_id": pending.connection_id,
                "source_host": peer.id if peer else "",
                "target_host": self._node_id,
            },
        )
        arrival_description = (
            f"placed keyboard switch on {display.name} at "
            f"{pending.destination.x_percent:.1f}%,{pending.destination.y_percent:.1f}% "
            if pending.destination is not None
            else f"placed cursor on {display.name} {destination.edge} at "
            f"{destination.position(pending.normalized):.1f}% "
        )
        self.log(
            "arrival",
            f"{arrival_description}({target.x:.0f},{target.y:.0f}) from "
            f"({float(source.x):.0f},{float(source.y):.0f}), pid {source_pid}",
        )
        return event

    def _schedule_placement_warps(self, placement: ArrivalPlacement) -> None:
        # Universal Control can overwrite the event-tap location after returning
        # from the callback, so reinforce the logical destination while it settles.
        for delay in (0.03, 0.08, 0.16):
            timer = threading.Timer(delay, self._warp_active_placement, (placement,))
            timer.daemon = True
            timer.start()

    def _warp_active_placement(self, placement: ArrivalPlacement) -> None:
        with self._lock:
            if self._arrival_placement is not placement or not self._armed:
                return
            point = placement.point
            placement.forced_warps += 1
            self._last_point = point
        Quartz.CGWarpMouseCursorPosition(_cg_point(point))

    def _apply_placement_event(
        self,
        event: Any,
        placement: ArrivalPlacement,
        display: Display,
        transport_display: Display | None,
        current: Point,
        dx: float,
        dy: float,
        now: float,
    ) -> Any:
        if (
            placement.source_endpoint is not None
            and placement.destination_endpoint is not None
        ):
            # Packets already rewritten toward the hidden UC edge can arrive after
            # acknowledgement. Pin those; integrate only real post-transfer motion.
            at_transport = (
                placement.transport is not None
                and transport_display is not None
                and near_edge_segment(
                    transport_display,
                    placement.transport.edge,
                    placement.transport.start,
                    placement.transport.end,
                    current,
                    max(24.0, self._threshold * 6.0),
                )
                and (
                    abs(current.x - placement.point.x) > 96.0
                    or abs(current.y - placement.point.y) > 96.0
                )
            )
            if at_transport:
                step_x, step_y = 0.0, 0.0
                target = placement.point
            else:
                step_x, step_y = transform_handoff_delta(
                    placement.source_endpoint,
                    placement.destination_endpoint,
                    dx,
                    dy,
                )
                inset = 2.0
                target = Point(
                    min(
                        display.right - inset,
                        max(display.x + inset, placement.point.x + step_x),
                    ),
                    min(
                        display.bottom - inset,
                        max(display.y + inset, placement.point.y + step_y),
                    ),
                )
            Quartz.CGEventSetLocation(event, _cg_point(target))
            Quartz.CGEventSetIntegerValueField(
                event, Quartz.kCGMouseEventDeltaX, int(step_x)
            )
            Quartz.CGEventSetIntegerValueField(
                event, Quartz.kCGMouseEventDeltaY, int(step_y)
            )
            settled = False
            with self._lock:
                placement.point = target
                placement.rewritten_events += 1
                self._last_point = target
                if now >= placement.expires_at and self._arrival_placement is placement:
                    self._arrival_placement = None
                    settled = True
            if settled:
                count = placement.rewritten_events
                self.log(
                    "arrival",
                    f"edge placement settled after {count} integrated event(s)",
                )
            return event

        near_virtual_cursor = (
            inside_display(display, current)
            and abs(current.x - placement.point.x) <= 96.0
            and abs(current.y - placement.point.y) <= 96.0
        )
        if near_virtual_cursor:
            placement.point = current
            if now >= placement.expires_at:
                with self._lock:
                    if self._arrival_placement is placement:
                        self._arrival_placement = None
            return event

        magnitude = max(abs(dx), abs(dy), 1.0)
        step_x, step_y = inward_delta(placement.destination_edge, magnitude)
        inset = 2.0
        target = Point(
            min(display.right - inset, max(display.x + inset, placement.point.x + step_x)),
            min(display.bottom - inset, max(display.y + inset, placement.point.y + step_y)),
        )
        Quartz.CGEventSetLocation(event, _cg_point(target))
        Quartz.CGEventSetIntegerValueField(event, Quartz.kCGMouseEventDeltaX, int(step_x))
        Quartz.CGEventSetIntegerValueField(event, Quartz.kCGMouseEventDeltaY, int(step_y))
        Quartz.CGWarpMouseCursorPosition(_cg_point(target))
        with self._lock:
            placement.point = target
            placement.rewritten_events += 1
            self._last_point = target
            if now >= placement.expires_at and self._arrival_placement is placement:
                self._arrival_placement = None
        return event

    def close(self) -> None:
        self._closing.set()
        self.stop_tap()
        self._outbound.close()
        self._heartbeat_thread.join(timeout=1.0)


class ControllerHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], handler: type[BaseHTTPRequestHandler], router: EdgeRouter):
        self.router = router
        super().__init__(address, handler)


class JsonHandler(BaseHTTPRequestHandler):
    server: ControllerHTTPServer

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _json(self, payload: dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if length > 256 * 1024:
            raise ValueError("request body too large")
        value = json.loads(self.rfile.read(length) or b"{}")
        if not isinstance(value, dict):
            raise ValueError("JSON body must be an object")
        return value


class UiHandler(JsonHandler):
    def do_GET(self) -> None:
        if self.path == "/api/state":
            self._json(self.server.router.state())
            return
        assets = {
            "/": ("index.html", "text/html; charset=utf-8"),
            "/app.js": ("app.js", "text/javascript; charset=utf-8"),
            "/style.css": ("style.css", "text/css; charset=utf-8"),
        }
        asset = assets.get(self.path)
        if asset is None:
            self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)
            return
        path, content_type = asset
        body = (UI_DIR / path).read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        try:
            payload = self._body()
            if self.path == "/api/peer/connect":
                self.server.router.pair(
                    str(payload["address"]),
                    int(payload.get("port", 8766)),
                    str(payload["code"]),
                )
            elif self.path == "/api/peer/disconnect":
                self.server.router.disconnect_peer()
            elif self.path == "/api/connections":
                values = payload.get("connections")
                if not isinstance(values, list):
                    raise ValueError("connections must be a list")
                self.server.router.replace_connections(values)
            elif self.path == "/api/keyboard-switch":
                self.server.router.replace_keyboard_switch(
                    payload.get("keyboard_switch")
                )
            elif self.path == "/api/directional-navigation":
                self.server.router.replace_directional_navigation(
                    payload.get("directional_navigation")
                )
            elif self.path == "/api/directional-navigation/propose":
                raw_modifiers = payload.get("modifiers", ["command"])
                if not isinstance(raw_modifiers, list):
                    raise ValueError("modifiers must be a list")
                navigation = self.server.router.propose_directional_navigation(
                    tuple(str(item).lower() for item in raw_modifiers)
                )
                self._json(
                    {
                        "ok": True,
                        "directional_navigation": navigation.to_json(),
                    }
                )
                return
            elif self.path == "/api/routing/activate":
                self.server.router.set_armed(True)
            elif self.path == "/api/routing/stop":
                self.server.router.set_armed(False)
            elif self.path == "/api/restore":
                self.server.router.restore()
            elif self.path == "/api/access/request":
                self._json(self.server.router.request_access())
                return
            else:
                self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)
                return
            self._json({"ok": True})
        except (KeyError, TypeError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
            self._json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
        except Exception as exc:  # pragma: no cover - platform failure
            self.server.router.log("error", str(exc))
            self._json({"error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)


class PeerHandler(JsonHandler):
    def _authorized(self) -> bool:
        return self.server.router.authorized(self.headers.get("Authorization", ""))

    def _require_authorized(self) -> bool:
        if self._authorized():
            return True
        self._json({"error": "unauthorized"}, HTTPStatus.UNAUTHORIZED)
        return False

    def do_GET(self) -> None:
        if self.path != "/peer/state":
            self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)
            return
        if not self._require_authorized():
            return
        self._json(self.server.router.peer_state())

    def do_POST(self) -> None:
        try:
            payload = self._body()
            if self.path == "/peer/pair":
                result = self.server.router.accept_pair(
                    payload,
                    self.headers.get("X-UC-Edge-Pair-Code", ""),
                    self.client_address[0],
                )
                self._json(result)
                return
            if not self._require_authorized():
                return
            if self.path == "/peer/connections":
                values = payload.get("connections")
                if not isinstance(values, list):
                    raise ValueError("connections must be a list")
                self.server.router.replace_connections(values, sync=False)
            elif self.path == "/peer/keyboard-switch":
                self.server.router.replace_keyboard_switch(
                    payload.get("keyboard_switch"),
                    sync=False,
                )
            elif self.path == "/peer/directional-navigation":
                self.server.router.replace_directional_navigation(
                    payload.get("directional_navigation"),
                    sync=False,
                )
            elif self.path == "/peer/routing":
                self.server.router.set_armed(bool(payload.get("armed")), sync=False)
            elif self.path == "/peer/handoff":
                self.server.router.receive_handoff(payload)
            elif self.path == "/peer/handoff/complete":
                self.server.router.complete_handoff(payload)
            elif self.path == "/peer/handoff/cancel":
                self.server.router.cancel_handoff(payload)
            else:
                self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)
                return
            self._json({"ok": True})
        except PermissionError as exc:
            self._json({"error": str(exc)}, HTTPStatus.FORBIDDEN)
        except (KeyError, TypeError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
            self._json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
        except Exception as exc:  # pragma: no cover - platform failure
            self.server.router.log("error", str(exc))
            self._json({"error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765, help="local web UI port")
    parser.add_argument("--peer-port", type=int, default=8766, help="LAN agent port")
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE_PATH)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--safe", action="store_true", help="do not restore armed state")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    router = EdgeRouter(args.state, args.peer_port)
    ui_server = ControllerHTTPServer(("127.0.0.1", args.port), UiHandler, router)
    peer_server = ControllerHTTPServer(("0.0.0.0", args.peer_port), PeerHandler, router)
    peer_thread = threading.Thread(
        target=peer_server.serve_forever,
        kwargs={"poll_interval": 0.2},
        name="uc-edge-peer-server",
        daemon=True,
    )
    peer_thread.start()
    url = f"http://127.0.0.1:{args.port}"
    print(f"UC Edge Lab UI: {url}")
    print(f"UC Edge Lab peer port: {args.peer_port}/tcp")
    print("Keep this terminal available. Press Ctrl-C for an immediate stop.")
    if router._armed and not args.safe:
        router.start_tap()
    elif args.safe and router._armed:
        router.set_armed(False, sync=False)
    if not args.no_browser:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        ui_server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        pass
    finally:
        peer_server.shutdown()
        ui_server.server_close()
        peer_server.server_close()
        router.close()
        peer_thread.join(timeout=1.0)


if __name__ == "__main__":
    main()
