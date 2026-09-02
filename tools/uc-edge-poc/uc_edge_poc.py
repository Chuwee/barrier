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
    EdgeConnection,
    EdgeEndpoint,
    HostSnapshot,
    Point,
    TransportEdge,
    inside_display,
    inward_delta,
    near_edge_segment,
    outward_component,
    point_inside_edge,
    point_on_edge,
    should_trigger,
    span_fraction,
    target_delta,
    validate_connections,
)
from peer_transport import OutboundWorker, PeerRecord, request_json


MOUSE_EVENT_TYPES = (
    Quartz.kCGEventMouseMoved,
    Quartz.kCGEventLeftMouseDragged,
    Quartz.kCGEventRightMouseDragged,
    Quartz.kCGEventOtherMouseDragged,
)
UI_DIR = Path(__file__).with_name("ui")
DEFAULT_STATE_PATH = Path.home() / ".uc-edge-lab" / "state.json"


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


@dataclass
class PendingArrival:
    handoff_id: str
    connection_id: str
    normalized: float
    expires_at: float
    observed_events: int = 0


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
        if state_version < 3:
            if self._redirect_ms == 700:
                self._redirect_ms = 1800
            if self._arrival_timeout_ms == 2200:
                self._arrival_timeout_ms = 3200

        self._display_cache = list_displays()
        peer_value = persisted.get("peer")
        self._peer = PeerRecord.from_json(peer_value) if isinstance(peer_value, dict) else None
        self._connections: tuple[EdgeConnection, ...] = ()
        self._load_connections(persisted.get("connections", []))

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

    def _save_locked(self) -> None:
        value = {
            "version": 3,
            "node_id": self._node_id,
            "node_name": self._node_name,
            "node_token": self._node_token,
            "pair_code": self._pair_code,
            "peer": self._peer.to_json(include_secret=True) if self._peer else None,
            "connections": [connection.to_json() for connection in self._connections],
            "armed": self._armed,
            "threshold": self._threshold,
            "redirect_ms": self._redirect_ms,
            "cooldown_ms": self._cooldown_ms,
            "arrival_timeout_ms": self._arrival_timeout_ms,
            "arrival_guard_ms": self._arrival_guard_ms,
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
        with self._lock:
            if self._peer is not None and self._peer.id != peer.id:
                self._connections = ()
            self._peer = peer
            if not self._connections and remote_connections:
                self._connections = validate_connections(
                    remote_connections,
                    (local, remote),
                )
            self._save_locked()
        self.log("peer", f"paired with {remote.name} at {address}:{peer.port}")
        if self._connections:
            self._sync_connections()

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
        local = self.local_host(refresh=True)
        with self._lock:
            if self._peer is not None and self._peer.id != peer.id:
                self._connections = ()
            self._peer = peer
            if not self._connections and incoming:
                self._connections = validate_connections(incoming, (local, remote))
            self._save_locked()
            connections = [connection.to_json() for connection in self._connections]
        self.log("peer", f"accepted pairing from {remote.name}")
        return {
            "host": local.to_json(),
            "token": self._node_token,
            "port": self._peer_port,
            "connections": connections,
        }

    def disconnect_peer(self) -> None:
        with self._lock:
            self._peer = None
            self._connections = ()
            self._armed = False
            self._pending_arrival = None
            self._redirect = None
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
        with self._lock:
            thread_alive = bool(self._thread and self._thread.is_alive())
            return {
                "host": self.local_host(refresh=True).to_json(),
                "armed": self._armed,
                "tap": {
                    "running": thread_alive and self._tap is not None,
                    "level": self._tap_level,
                    "error": self._error,
                },
                "permissions": self.permissions(),
            }

    def authorized(self, authorization: str) -> bool:
        return hmac.compare_digest(authorization, f"Bearer {self._node_token}")

    def replace_connections(self, values: list[dict[str, Any]], sync: bool = True) -> None:
        connections = tuple(EdgeConnection.from_json(value) for value in values)
        connections = validate_connections(connections, self._hosts_for_validation())
        with self._lock:
            self._connections = connections
            self._redirect = None
            self._save_locked()
        self.log("config", f"saved {len(connections)} bidirectional edge mapping(s)")
        if sync:
            self._sync_connections()

    def _sync_connections(self) -> None:
        self._outbound.send(
            "/peer/connections",
            {"connections": [connection.to_json() for connection in self._connections]},
        )

    def set_armed(self, armed: bool, sync: bool = True) -> None:
        with self._lock:
            self._armed = armed
            if not armed:
                self._redirect = None
                self._pending_arrival = None
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
                "local": local.to_json(),
                "peer": peer.to_json() if peer else None,
                "connections": [connection.to_json() for connection in self._connections],
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
                },
                "redirecting": self._redirect is not None,
                "pending_arrival": self._pending_arrival is not None,
                "events": list(self._events),
            }

    def receive_handoff(self, payload: dict[str, Any]) -> None:
        handoff_id = str(payload.get("handoff_id") or payload["connection_id"])
        connection_id = str(payload["connection_id"])
        source_host = str(payload["source_host"])
        target_host = str(payload["target_host"])
        normalized = float(payload["normalized"])
        peer = self.peer()
        if target_host != self._node_id or peer is None or source_host != peer.id:
            raise ValueError("handoff host does not match the active pair")
        if not 0 <= normalized <= 1:
            raise ValueError("handoff position must be normalized")
        with self._lock:
            connection = next(
                (
                    item
                    for item in self._connections
                    if item.id == connection_id and item.enabled
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
            )
        self.log(
            "handoff",
            f"handoff intent received at {normalized * 100:.1f}%; waiting for UC input",
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
        for event_type in MOUSE_EVENT_TYPES:
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
        if event_type not in MOUSE_EVENT_TYPES:
            return event
        try:
            return self._route_mouse_event(event)
        except Exception as exc:  # Never break the system input stream.
            with self._lock:
                self._error = f"event callback error: {exc}"
                self._redirect = None
            self.log("error", self._error)
            return event

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
            connections = self._connections
            peer = self._peer

        if not armed:
            return event

        displays = {display.key: display for display in self.displays(refresh=False)}
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
                    destination, _, transport = route
                    display = displays.get(destination.display_key)
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
                            destination,
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
                    return event

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
            if outward < -0.5:
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
        display: Display,
        pending: PendingArrival,
        dx: float,
        dy: float,
        now: float,
    ) -> Any:
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
            peer = self._peer
        self._outbound.send(
            "/peer/handoff/complete",
            {
                "handoff_id": pending.handoff_id,
                "connection_id": pending.connection_id,
                "source_host": peer.id if peer else "",
                "target_host": self._node_id,
            },
        )
        self.log(
            "arrival",
            f"placed cursor on {display.name} {destination.edge} at "
            f"{destination.position(pending.normalized):.1f}%",
        )
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
