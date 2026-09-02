"""Small authenticated JSON transport used between paired Edge Lab agents."""

from __future__ import annotations

import json
import queue
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass, replace
from typing import Any, Callable

from edge_core import Display, HostSnapshot


OutboundCompletion = Callable[[RuntimeError | None], None]


@dataclass(frozen=True)
class PeerRecord:
    id: str
    name: str
    address: str
    port: int
    token: str
    displays: tuple[Display, ...] = ()
    connected: bool = False
    last_seen: float | None = None
    error: str | None = None
    armed: bool = False
    tap_running: bool = False
    tap_level: str = "stopped"
    listen_access: bool | None = None

    @property
    def host(self) -> HostSnapshot:
        return HostSnapshot(self.id, self.name, tuple(self.displays))

    def to_json(self, include_secret: bool = False) -> dict[str, Any]:
        value: dict[str, Any] = {
            "id": self.id,
            "name": self.name,
            "address": self.address,
            "port": self.port,
            "connected": self.connected,
            "last_seen": self.last_seen,
            "error": self.error,
            "armed": self.armed,
            "tap_running": self.tap_running,
            "tap_level": self.tap_level,
            "listen_access": self.listen_access,
            "displays": [display.to_json() for display in self.displays],
        }
        if include_secret:
            value["token"] = self.token
        return value

    @classmethod
    def from_json(cls, value: dict[str, Any]) -> PeerRecord:
        host = HostSnapshot.from_json(
            {
                "id": value["id"],
                "name": value["name"],
                "displays": value.get("displays", []),
            }
        )
        return cls(
            id=host.id,
            name=host.name,
            address=str(value["address"]),
            port=int(value.get("port", 8766)),
            token=str(value["token"]),
            displays=host.displays,
        )

    def with_snapshot(
        self,
        snapshot: HostSnapshot,
        now: float,
        runtime: dict[str, Any] | None = None,
    ) -> PeerRecord:
        runtime = runtime or {}
        tap = runtime.get("tap") if isinstance(runtime.get("tap"), dict) else {}
        permissions = (
            runtime.get("permissions")
            if isinstance(runtime.get("permissions"), dict)
            else {}
        )
        return replace(
            self,
            id=snapshot.id,
            name=snapshot.name,
            displays=snapshot.displays,
            connected=True,
            last_seen=now,
            error=None,
            armed=bool(runtime.get("armed", False)),
            tap_running=bool(tap.get("running", False)),
            tap_level=str(tap.get("level", "stopped")),
            listen_access=permissions.get("listen"),
        )


def _base_url(address: str, port: int) -> str:
    address = address.strip()
    if not address:
        raise ValueError("peer address is required")
    if ":" in address and not address.startswith("["):
        address = f"[{address}]"
    return f"http://{address}:{port}"


def request_json(
    method: str,
    address: str,
    port: int,
    path: str,
    *,
    payload: dict[str, Any] | None = None,
    token: str | None = None,
    pair_code: str | None = None,
    timeout: float = 1.5,
) -> dict[str, Any]:
    body = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if pair_code:
        headers["X-UC-Edge-Pair-Code"] = pair_code

    request = urllib.request.Request(
        f"{_base_url(address, port)}{path}",
        data=body,
        headers=headers,
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as exc:
        try:
            detail = json.loads(exc.read() or b"{}").get("error")
        except (json.JSONDecodeError, AttributeError):
            detail = None
        raise RuntimeError(detail or f"peer returned HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"could not reach peer: {exc.reason}") from exc


class OutboundWorker:
    """Moves peer requests off the CoreGraphics event-tap thread."""

    def __init__(
        self,
        peer_provider: Callable[[], PeerRecord | None],
        error_handler: Callable[[str], None],
    ) -> None:
        self._peer_provider = peer_provider
        self._error_handler = error_handler
        self._queue: queue.Queue[
            tuple[str, dict[str, Any], OutboundCompletion | None] | None
        ] = queue.Queue()
        self._thread = threading.Thread(
            target=self._run,
            name="uc-edge-peer-outbound",
            daemon=True,
        )
        self._thread.start()

    def send(
        self,
        path: str,
        payload: dict[str, Any],
        on_complete: OutboundCompletion | None = None,
    ) -> None:
        self._queue.put_nowait((path, payload, on_complete))

    def close(self) -> None:
        self._queue.put_nowait(None)
        self._thread.join(timeout=1.0)

    def _complete(
        self,
        callback: OutboundCompletion | None,
        error: RuntimeError | None,
    ) -> None:
        if callback is None:
            return
        try:
            callback(error)
        except Exception as exc:  # Keep the outbound worker alive.
            self._error_handler(f"outbound completion failed: {exc}")

    def _run(self) -> None:
        while True:
            item = self._queue.get()
            if item is None:
                return
            path, payload, on_complete = item
            peer = self._peer_provider()
            if peer is None:
                error = RuntimeError("handoff dropped because no peer is paired")
                self._error_handler(str(error))
                self._complete(on_complete, error)
                continue
            error: RuntimeError | None = None
            try:
                request_json(
                    "POST",
                    peer.address,
                    peer.port,
                    path,
                    payload=payload,
                    token=peer.token,
                    timeout=0.6,
                )
            except RuntimeError as exc:
                error = exc
                self._error_handler(str(exc))
            self._complete(on_complete, error)
