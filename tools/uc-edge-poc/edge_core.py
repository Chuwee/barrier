"""Pure geometry and topology model for Universal Control edge routing."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Iterable


EDGES = ("left", "right", "top", "bottom")
HOTKEY_MODIFIERS = ("control", "option", "shift", "command")


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


@dataclass(frozen=True)
class HotkeyDestination:
    host_id: str
    display_key: str
    x_percent: float = 50.0
    y_percent: float = 50.0

    def to_json(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_json(cls, value: dict[str, Any]) -> HotkeyDestination:
        destination = cls(
            host_id=str(value["host_id"]),
            display_key=str(value["display_key"]),
            x_percent=float(value.get("x_percent", 50.0)),
            y_percent=float(value.get("y_percent", 50.0)),
        )
        destination.validate("destination")
        return destination

    def validate(self, label: str) -> None:
        if not self.host_id or not self.display_key:
            raise ValueError(f"{label} must reference a Mac and display")
        if not 0 <= self.x_percent <= 100 or not 0 <= self.y_percent <= 100:
            raise ValueError(f"{label} position must stay between 0 and 100")


@dataclass(frozen=True)
class KeyboardSwitch:
    connection_id: str
    key_code: int
    key_label: str
    modifiers: tuple[str, ...]
    a_destination: HotkeyDestination
    b_destination: HotkeyDestination
    enabled: bool = True

    def to_json(self) -> dict[str, Any]:
        return {
            "connection_id": self.connection_id,
            "key_code": self.key_code,
            "key_label": self.key_label,
            "modifiers": list(self.modifiers),
            "a_destination": self.a_destination.to_json(),
            "b_destination": self.b_destination.to_json(),
            "enabled": self.enabled,
        }

    @classmethod
    def from_json(cls, value: dict[str, Any]) -> KeyboardSwitch:
        raw_modifiers = {str(item).lower() for item in value.get("modifiers", [])}
        if any(modifier not in HOTKEY_MODIFIERS for modifier in raw_modifiers):
            raise ValueError("shortcut contains an invalid modifier")
        modifiers = tuple(
            modifier
            for modifier in HOTKEY_MODIFIERS
            if modifier in raw_modifiers
        )
        shortcut = cls(
            connection_id=str(value["connection_id"]),
            key_code=int(value["key_code"]),
            key_label=str(value.get("key_label", f"Key {value['key_code']}")),
            modifiers=modifiers,
            a_destination=HotkeyDestination.from_json(value["a_destination"]),
            b_destination=HotkeyDestination.from_json(value["b_destination"]),
            enabled=bool(value.get("enabled", True)),
        )
        shortcut.validate()
        return shortcut

    def validate(self) -> None:
        if not self.connection_id:
            raise ValueError("shortcut transport route is required")
        if not 0 <= self.key_code <= 127:
            raise ValueError("shortcut key code is invalid")
        if not self.key_label or len(self.key_label) > 24:
            raise ValueError("shortcut key label is invalid")
        if not self.modifiers:
            raise ValueError("shortcut requires at least one modifier")
        if any(modifier not in HOTKEY_MODIFIERS for modifier in self.modifiers):
            raise ValueError("shortcut contains an invalid modifier")
        if self.a_destination.host_id == self.b_destination.host_id:
            raise ValueError("shortcut destinations must belong to different Macs")
        self.a_destination.validate("side A destination")
        self.b_destination.validate("side B destination")

    def destination_for(self, host_id: str) -> HotkeyDestination | None:
        if self.a_destination.host_id == host_id:
            return self.a_destination
        if self.b_destination.host_id == host_id:
            return self.b_destination
        return None


@dataclass(frozen=True)
class DirectionalBinding:
    id: str
    source_host_id: str
    source_display_key: str
    direction: str
    target: HotkeyDestination
    connection_id: str | None = None
    source_start: float = 0.0
    source_end: float = 100.0
    enabled: bool = True

    def to_json(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "source_host_id": self.source_host_id,
            "source_display_key": self.source_display_key,
            "direction": self.direction,
            "target": self.target.to_json(),
            "connection_id": self.connection_id,
            "source_start": self.source_start,
            "source_end": self.source_end,
            "enabled": self.enabled,
        }

    @classmethod
    def from_json(cls, value: dict[str, Any]) -> DirectionalBinding:
        connection_id = value.get("connection_id")
        binding = cls(
            id=str(value["id"]),
            source_host_id=str(value["source_host_id"]),
            source_display_key=str(value["source_display_key"]),
            direction=str(value["direction"]),
            target=HotkeyDestination.from_json(value["target"]),
            connection_id=(str(connection_id) if connection_id else None),
            source_start=float(value.get("source_start", 0.0)),
            source_end=float(value.get("source_end", 100.0)),
            enabled=bool(value.get("enabled", True)),
        )
        binding.validate("directional binding")
        return binding

    def validate(self, label: str) -> None:
        if not self.id:
            raise ValueError(f"{label} id is required")
        if not self.source_host_id or not self.source_display_key:
            raise ValueError(f"{label} must reference a source display")
        if self.direction not in EDGES:
            raise ValueError(f"{label} has an invalid direction")
        if not 0 <= self.source_start <= 100 or not 0 <= self.source_end <= 100:
            raise ValueError(f"{label} source range must stay between 0 and 100")
        if self.source_start == self.source_end:
            raise ValueError(f"{label} source range cannot have zero length")
        self.target.validate(f"{label} target")

    def contains(self, position: float) -> bool:
        return min(self.source_start, self.source_end) <= position <= max(
            self.source_start,
            self.source_end,
        )


@dataclass(frozen=True)
class DirectionalNavigation:
    modifiers: tuple[str, ...]
    bindings: tuple[DirectionalBinding, ...]
    enabled: bool = True

    def to_json(self) -> dict[str, Any]:
        return {
            "modifiers": list(self.modifiers),
            "bindings": [binding.to_json() for binding in self.bindings],
            "enabled": self.enabled,
        }

    @classmethod
    def from_json(cls, value: dict[str, Any]) -> DirectionalNavigation:
        raw_modifiers = {str(item).lower() for item in value.get("modifiers", [])}
        if any(modifier not in HOTKEY_MODIFIERS for modifier in raw_modifiers):
            raise ValueError("directional navigation contains an invalid modifier")
        navigation = cls(
            modifiers=tuple(
                modifier
                for modifier in HOTKEY_MODIFIERS
                if modifier in raw_modifiers
            ),
            bindings=tuple(
                DirectionalBinding.from_json(item)
                for item in value.get("bindings", [])
            ),
            enabled=bool(value.get("enabled", True)),
        )
        navigation.validate()
        return navigation

    def validate(self) -> None:
        if not self.modifiers:
            raise ValueError("directional navigation requires at least one modifier")
        if any(modifier not in HOTKEY_MODIFIERS for modifier in self.modifiers):
            raise ValueError("directional navigation contains an invalid modifier")
        for binding in self.bindings:
            binding.validate("directional binding")

    def binding_for(
        self,
        host_id: str,
        display_key: str,
        direction: str,
        position: float,
    ) -> DirectionalBinding | None:
        return next(
            (
                binding
                for binding in self.bindings
                if binding.enabled
                and binding.source_host_id == host_id
                and binding.source_display_key == display_key
                and binding.direction == direction
                and binding.contains(position)
            ),
            None,
        )


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


def boosted_transport_magnitude(
    magnitude: float,
    gain: float,
    minimum: float,
    maximum: float,
) -> float:
    """Amplify transport pressure without allowing an unbounded cursor delta."""
    return clamp(max(abs(magnitude) * gain, minimum), minimum, maximum)


def inward_delta(edge: str, magnitude: float) -> tuple[float, float]:
    dx, dy = target_delta(edge, magnitude)
    return -dx, -dy


def transform_handoff_delta(
    source: EdgeEndpoint,
    destination: EdgeEndpoint,
    dx: float,
    dy: float,
) -> tuple[float, float]:
    """Rotate source-edge motion into the destination edge's coordinate basis."""
    outward_normals = {
        "left": (-1.0, 0.0),
        "right": (1.0, 0.0),
        "top": (0.0, -1.0),
        "bottom": (0.0, 1.0),
    }
    inward_normals = {
        edge: (-x, -y) for edge, (x, y) in outward_normals.items()
    }
    tangents = {
        "left": (0.0, 1.0),
        "right": (0.0, 1.0),
        "top": (1.0, 0.0),
        "bottom": (1.0, 0.0),
    }
    source_normal = outward_normals[source.edge]
    source_tangent = tangents[source.edge]
    destination_normal = inward_normals[destination.edge]
    destination_tangent = tangents[destination.edge]
    normal_amount = dx * source_normal[0] + dy * source_normal[1]
    tangent_amount = dx * source_tangent[0] + dy * source_tangent[1]
    orientation = (
        1.0
        if (source.end - source.start) * (destination.end - destination.start) > 0
        else -1.0
    )
    return (
        normal_amount * destination_normal[0]
        + tangent_amount * orientation * destination_tangent[0],
        normal_amount * destination_normal[1]
        + tangent_amount * orientation * destination_tangent[1],
    )


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


def inward_edge_depth(display: Display, edge: str, point: Point) -> float:
    if edge == "left":
        return point.x - display.x
    if edge == "right":
        return display.right - point.x
    if edge == "top":
        return point.y - display.y
    if edge == "bottom":
        return display.bottom - point.y
    raise ValueError(f"unknown edge: {edge}")


def near_edge_segment(
    display: Display,
    edge: str,
    start_percent: float,
    end_percent: float,
    point: Point,
    threshold: float,
) -> bool:
    """Return whether a point is close to a bounded section of a display edge."""
    if not inside_display(display, point, threshold):
        return False
    if not near_edge(display, edge, point, threshold):
        return False
    span = display.height if edge in ("left", "right") else display.width
    tolerance = 100.0 * threshold / max(span, 1.0)
    position = span_fraction(display, edge, point)
    low, high = sorted((start_percent, end_percent))
    return low - tolerance <= position <= high + tolerance


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


def should_prearm(
    display: Display,
    edge: str,
    start_percent: float,
    end_percent: float,
    distance: float,
    previous: Point | None,
    current: Point,
    dx: float,
    dy: float,
) -> bool:
    if outward_component(edge, dx, dy) <= 0:
        return False
    depth = inward_edge_depth(display, edge, current)
    if depth < 0 or depth > distance:
        return False
    if previous is not None and not inside_display(display, previous, distance):
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


def point_inside_display(
    display: Display,
    x_percent: float,
    y_percent: float,
    inset: float = 8.0,
) -> Point:
    inset = max(1.0, min(inset, display.width / 2.0, display.height / 2.0))
    usable_width = max(0.0, display.width - inset * 2.0)
    usable_height = max(0.0, display.height - inset * 2.0)
    return Point(
        display.x + inset + usable_width * clamp(x_percent, 0.0, 100.0) / 100.0,
        display.y + inset + usable_height * clamp(y_percent, 0.0, 100.0) / 100.0,
    )


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


def validate_keyboard_switch(
    shortcut: KeyboardSwitch,
    connections: Iterable[EdgeConnection],
    hosts: Iterable[HostSnapshot],
) -> KeyboardSwitch:
    shortcut.validate()
    connection = next(
        (item for item in connections if item.id == shortcut.connection_id),
        None,
    )
    if connection is None:
        raise ValueError("shortcut references a missing transport route")
    host_displays = {
        host.id: {display.key for display in host.displays}
        for host in hosts
    }
    expected_hosts = {connection.a.host_id, connection.b.host_id}
    destination_hosts = {
        shortcut.a_destination.host_id,
        shortcut.b_destination.host_id,
    }
    if destination_hosts != expected_hosts:
        raise ValueError("shortcut destinations do not match its transport route")
    for destination in (shortcut.a_destination, shortcut.b_destination):
        displays = host_displays.get(destination.host_id)
        if displays is None:
            raise ValueError("shortcut destination references an unknown Mac")
        if destination.display_key not in displays:
            raise ValueError("shortcut destination references a disconnected display")
    return shortcut


def _landing_for_endpoint(endpoint: EdgeEndpoint) -> tuple[float, float]:
    position = endpoint.position(0.5)
    if endpoint.edge == "left":
        return 0.0, position
    if endpoint.edge == "right":
        return 100.0, position
    if endpoint.edge == "top":
        return position, 0.0
    return position, 100.0


def _landing_for_direction(direction: str) -> tuple[float, float]:
    if direction == "left":
        return 100.0, 50.0
    if direction == "right":
        return 0.0, 50.0
    if direction == "top":
        return 50.0, 100.0
    return 50.0, 0.0


def _geometric_neighbor(
    source: Display,
    direction: str,
    displays: Iterable[Display],
) -> Display | None:
    candidates: list[tuple[float, float, Display]] = []
    source_center_x = source.x + source.width / 2.0
    source_center_y = source.y + source.height / 2.0
    for target in displays:
        if target.key == source.key:
            continue
        target_center_x = target.x + target.width / 2.0
        target_center_y = target.y + target.height / 2.0
        overlap_x = min(source.right, target.right) - max(source.x, target.x)
        overlap_y = min(source.bottom, target.bottom) - max(source.y, target.y)
        if direction == "left" and target.right <= source.x + 1.0 and overlap_y > 0:
            candidates.append(
                (source.x - target.right, abs(target_center_y - source_center_y), target)
            )
        elif direction == "right" and target.x >= source.right - 1.0 and overlap_y > 0:
            candidates.append(
                (target.x - source.right, abs(target_center_y - source_center_y), target)
            )
        elif direction == "top" and target.bottom <= source.y + 1.0 and overlap_x > 0:
            candidates.append(
                (source.y - target.bottom, abs(target_center_x - source_center_x), target)
            )
        elif direction == "bottom" and target.y >= source.bottom - 1.0 and overlap_x > 0:
            candidates.append(
                (target.y - source.bottom, abs(target_center_x - source_center_x), target)
            )
    return min(candidates, key=lambda item: (item[0], item[1]))[2] if candidates else None


def propose_directional_navigation(
    hosts: Iterable[HostSnapshot],
    connections: Iterable[EdgeConnection],
    modifiers: tuple[str, ...] = ("command",),
) -> DirectionalNavigation:
    host_values = tuple(hosts)
    connection_values = tuple(connections)
    bindings: list[DirectionalBinding] = []
    claimed: set[tuple[str, str, str]] = set()

    for connection in connection_values:
        for source, target in ((connection.a, connection.b), (connection.b, connection.a)):
            x_percent, y_percent = _landing_for_endpoint(target)
            bindings.append(
                DirectionalBinding(
                    id=f"edge:{connection.id}:{source.host_id}",
                    source_host_id=source.host_id,
                    source_display_key=source.display_key,
                    direction=source.edge,
                    source_start=source.start,
                    source_end=source.end,
                    target=HotkeyDestination(
                        host_id=target.host_id,
                        display_key=target.display_key,
                        x_percent=x_percent,
                        y_percent=y_percent,
                    ),
                    connection_id=connection.id,
                )
            )
            claimed.add((source.host_id, source.display_key, source.edge))

    for host in host_values:
        for source in host.displays:
            for direction in EDGES:
                key = (host.id, source.key, direction)
                if key in claimed:
                    continue
                target = _geometric_neighbor(source, direction, host.displays)
                if target is None:
                    continue
                x_percent, y_percent = _landing_for_direction(direction)
                bindings.append(
                    DirectionalBinding(
                        id=f"local:{host.id}:{source.key}:{direction}",
                        source_host_id=host.id,
                        source_display_key=source.key,
                        direction=direction,
                        target=HotkeyDestination(
                            host_id=host.id,
                            display_key=target.key,
                            x_percent=x_percent,
                            y_percent=y_percent,
                        ),
                    )
                )

    navigation = DirectionalNavigation(
        modifiers=modifiers,
        bindings=tuple(bindings),
    )
    return validate_directional_navigation(
        navigation,
        connection_values,
        host_values,
    )


def validate_directional_navigation(
    navigation: DirectionalNavigation,
    connections: Iterable[EdgeConnection],
    hosts: Iterable[HostSnapshot],
) -> DirectionalNavigation:
    navigation.validate()
    host_displays = {
        host.id: {display.key for display in host.displays}
        for host in hosts
    }
    connection_values = {connection.id: connection for connection in connections}
    seen_ids: set[str] = set()
    occupied: dict[tuple[str, str, str], list[tuple[float, float]]] = {}
    for binding in navigation.bindings:
        if binding.id in seen_ids:
            raise ValueError(f"duplicate directional binding id: {binding.id}")
        seen_ids.add(binding.id)
        source_displays = host_displays.get(binding.source_host_id)
        target_displays = host_displays.get(binding.target.host_id)
        if source_displays is None or binding.source_display_key not in source_displays:
            raise ValueError("directional binding references a disconnected source display")
        if target_displays is None or binding.target.display_key not in target_displays:
            raise ValueError("directional binding references a disconnected target display")
        if binding.source_host_id == binding.target.host_id:
            if binding.connection_id is not None:
                raise ValueError("same-Mac directional binding cannot use a UC route")
        else:
            connection = connection_values.get(binding.connection_id or "")
            route = connection.route_from(binding.source_host_id) if connection else None
            if route is None or route[1].host_id != binding.target.host_id:
                raise ValueError("cross-Mac directional binding needs a matching UC route")
        if binding.enabled:
            low, high = sorted((binding.source_start, binding.source_end))
            key = (
                binding.source_host_id,
                binding.source_display_key,
                binding.direction,
            )
            for other_low, other_high in occupied.setdefault(key, []):
                if max(low, other_low) < min(high, other_high):
                    raise ValueError("directional binding source ranges overlap")
            occupied[key].append((low, high))
    return navigation
