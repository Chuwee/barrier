"""Pure geometry and topology model for Universal Control edge routing."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Iterable


EDGES = ("left", "right", "top", "bottom")


@dataclass(frozen=True)
class Point:
    x: float
    y: float


@dataclass(frozen=True)
class Display:
    id: int
    key: str
    index: int
    name: str
    x: float
    y: float
    width: float
    height: float
    main: bool

    @property
    def right(self) -> float:
        return self.x + self.width

    @property
    def bottom(self) -> float:
        return self.y + self.height

    @property
    def label(self) -> str:
        marker = " (main)" if self.main else ""
        return (
            f"{self.name}{marker} | {self.width:.0f}x{self.height:.0f} "
            f"at {self.x:.0f},{self.y:.0f}"
        )

    def to_json(self) -> dict[str, Any]:
        return {**asdict(self), "label": self.label}

    @classmethod
    def from_json(cls, value: dict[str, Any]) -> Display:
        return cls(
            id=int(value["id"]),
            key=str(value["key"]),
            index=int(value.get("index", 0)),
            name=str(value.get("name", f"Display {value.get('index', 0)}")),
            x=float(value["x"]),
            y=float(value["y"]),
            width=float(value["width"]),
            height=float(value["height"]),
            main=bool(value.get("main", False)),
        )


@dataclass(frozen=True)
class HostSnapshot:
    id: str
    name: str
    displays: tuple[Display, ...]

    def to_json(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "displays": [display.to_json() for display in self.displays],
        }

    @classmethod
    def from_json(cls, value: dict[str, Any]) -> HostSnapshot:
        return cls(
            id=str(value["id"]),
            name=str(value["name"]),
            displays=tuple(
                Display.from_json(display) for display in value.get("displays", [])
            ),
        )


@dataclass(frozen=True)
class EdgeEndpoint:
    host_id: str
    display_key: str
    edge: str
    start: float = 0.0
    end: float = 100.0

    def to_json(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_json(cls, value: dict[str, Any]) -> EdgeEndpoint:
        endpoint = cls(
            host_id=str(value["host_id"]),
            display_key=str(value["display_key"]),
            edge=str(value["edge"]),
            start=float(value.get("start", 0.0)),
            end=float(value.get("end", 100.0)),
        )
        endpoint.validate("endpoint")
        return endpoint

    def validate(self, label: str) -> None:
        if self.edge not in EDGES:
            raise ValueError(f"{label} has an invalid edge")
        if not 0 <= self.start <= 100 or not 0 <= self.end <= 100:
            raise ValueError(f"{label} range must stay between 0 and 100")
        if self.start == self.end:
            raise ValueError(f"{label} range cannot have zero length")

    def contains(self, position: float) -> bool:
        return min(self.start, self.end) <= position <= max(self.start, self.end)

    def normalize(self, position: float) -> float:
        return clamp((position - self.start) / (self.end - self.start), 0.0, 1.0)

    def position(self, normalized: float) -> float:
        return self.start + clamp(normalized, 0.0, 1.0) * (self.end - self.start)


@dataclass(frozen=True)
class TransportEdge:
    display_key: str
    edge: str
    start: float = 25.0
    end: float = 75.0

    def to_json(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_json(cls, value: dict[str, Any]) -> TransportEdge:
        transport = cls(
            display_key=str(value["display_key"]),
            edge=str(value["edge"]),
            start=float(value.get("start", 25.0)),
            end=float(value.get("end", 75.0)),
        )
        transport.validate("transport")
        return transport

    def validate(self, label: str) -> None:
        if self.edge not in EDGES:
            raise ValueError(f"{label} has an invalid edge")
        if not 0 <= self.start <= 100 or not 0 <= self.end <= 100:
            raise ValueError(f"{label} range must stay between 0 and 100")
        if self.start == self.end:
            raise ValueError(f"{label} range cannot have zero length")

    def position(self, normalized: float) -> float:
        return self.start + clamp(normalized, 0.0, 1.0) * (self.end - self.start)


@dataclass(frozen=True)
class EdgeConnection:
    id: str
    name: str
    a: EdgeEndpoint
    b: EdgeEndpoint
    a_transport: TransportEdge
    b_transport: TransportEdge
    enabled: bool = True

    def to_json(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "a": self.a.to_json(),
            "b": self.b.to_json(),
            "a_transport": self.a_transport.to_json(),
            "b_transport": self.b_transport.to_json(),
            "enabled": self.enabled,
        }

    @classmethod
    def from_json(cls, value: dict[str, Any]) -> EdgeConnection:
        connection = cls(
            id=str(value["id"]),
            name=str(value.get("name", "Edge mapping")),
            a=EdgeEndpoint.from_json(value["a"]),
            b=EdgeEndpoint.from_json(value["b"]),
            a_transport=TransportEdge.from_json(value["a_transport"]),
            b_transport=TransportEdge.from_json(value["b_transport"]),
            enabled=bool(value.get("enabled", True)),
        )
        connection.validate()
        return connection

    def validate(self) -> None:
        if not self.id:
            raise ValueError("connection id is required")
        if self.a.host_id == self.b.host_id:
            raise ValueError("a connection must join two different Macs")
        self.a.validate("side A")
        self.b.validate("side B")
        self.a_transport.validate("side A transport")
        self.b_transport.validate("side B transport")

    def route_from(
        self, host_id: str
    ) -> tuple[EdgeEndpoint, EdgeEndpoint, TransportEdge] | None:
        if host_id == self.a.host_id:
            return self.a, self.b, self.a_transport
        if host_id == self.b.host_id:
            return self.b, self.a, self.b_transport
        return None


def clamp(value: float, low: float, high: float) -> float:
    return min(high, max(low, value))


def outward_component(edge: str, dx: float, dy: float) -> float:
    if edge == "left":
        return -dx
    if edge == "right":
        return dx
    if edge == "top":
        return -dy
    if edge == "bottom":
        return dy
    raise ValueError(f"unknown edge: {edge}")


def target_delta(edge: str, magnitude: float) -> tuple[float, float]:
    magnitude = max(1.0, abs(magnitude))
    if edge == "left":
        return -magnitude, 0.0
    if edge == "right":
        return magnitude, 0.0
    if edge == "top":
        return 0.0, -magnitude
    if edge == "bottom":
        return 0.0, magnitude
    raise ValueError(f"unknown edge: {edge}")


def inward_delta(edge: str, magnitude: float) -> tuple[float, float]:
    dx, dy = target_delta(edge, magnitude)
    return -dx, -dy


def span_fraction(display: Display, edge: str, point: Point) -> float:
    if edge in ("left", "right"):
        return 100.0 * (point.y - display.y) / max(display.height, 1.0)
    return 100.0 * (point.x - display.x) / max(display.width, 1.0)


def inside_display(display: Display, point: Point, margin: float = 0.0) -> bool:
    return (
        display.x - margin <= point.x <= display.right + margin
        and display.y - margin <= point.y <= display.bottom + margin
    )


def near_edge(display: Display, edge: str, point: Point, threshold: float) -> bool:
    if edge == "left":
        return abs(point.x - display.x) <= threshold
    if edge == "right":
        return abs(point.x - display.right) <= threshold
    if edge == "top":
        return abs(point.y - display.y) <= threshold
    if edge == "bottom":
        return abs(point.y - display.bottom) <= threshold
    raise ValueError(f"unknown edge: {edge}")


def should_trigger(
    display: Display,
    edge: str,
    start_percent: float,
    end_percent: float,
    threshold: float,
    previous: Point | None,
    current: Point,
    dx: float,
    dy: float,
) -> bool:
    if outward_component(edge, dx, dy) <= 0:
        return False
    if not near_edge(display, edge, current, threshold):
        return False
    if previous is not None and not inside_display(display, previous, threshold * 2):
        return False
    fraction = span_fraction(display, edge, current)
    return min(start_percent, end_percent) <= fraction <= max(start_percent, end_percent)


def point_on_edge(display: Display, edge: str, position_percent: float) -> Point:
    fraction = clamp(position_percent, 0.0, 100.0) / 100.0
    if edge == "left":
        return Point(display.x, display.y + display.height * fraction)
    if edge == "right":
        return Point(display.right - 1.0, display.y + display.height * fraction)
    if edge == "top":
        return Point(display.x + display.width * fraction, display.y)
    if edge == "bottom":
        return Point(display.x + display.width * fraction, display.bottom - 1.0)
    raise ValueError(f"unknown edge: {edge}")


def point_inside_edge(
    display: Display,
    edge: str,
    position_percent: float,
    inset: float = 8.0,
) -> Point:
    point = point_on_edge(display, edge, position_percent)
    inset = max(1.0, inset)
    if edge == "left":
        return Point(display.x + inset, point.y)
    if edge == "right":
        return Point(display.right - inset, point.y)
    if edge == "top":
        return Point(point.x, display.y + inset)
    if edge == "bottom":
        return Point(point.x, display.bottom - inset)
    raise ValueError(f"unknown edge: {edge}")


def validate_connections(
    connections: Iterable[EdgeConnection],
    hosts: Iterable[HostSnapshot],
) -> tuple[EdgeConnection, ...]:
    result = tuple(connections)
    host_displays = {
        host.id: {display.key for display in host.displays}
        for host in hosts
    }
    seen_ids: set[str] = set()
    occupied: dict[tuple[str, str, str], list[tuple[float, float, str]]] = {}

    for connection in result:
        connection.validate()
        if connection.id in seen_ids:
            raise ValueError(f"duplicate connection id: {connection.id}")
        seen_ids.add(connection.id)

        for label, endpoint, transport in (
            ("side A", connection.a, connection.a_transport),
            ("side B", connection.b, connection.b_transport),
        ):
            displays = host_displays.get(endpoint.host_id)
            if displays is None:
                raise ValueError(f"{label} references an unknown Mac")
            if endpoint.display_key not in displays:
                raise ValueError(f"{label} references a disconnected display")
            if transport.display_key not in displays:
                raise ValueError(f"{label} transport references a disconnected display")

            if connection.enabled:
                low, high = sorted((endpoint.start, endpoint.end))
                key = (endpoint.host_id, endpoint.display_key, endpoint.edge)
                for other_low, other_high, other_name in occupied.setdefault(key, []):
                    if max(low, other_low) < min(high, other_high):
                        raise ValueError(
                            f"{connection.name} overlaps {other_name} on the same source edge"
                        )
                occupied[key].append((low, high, connection.name))

    return result
