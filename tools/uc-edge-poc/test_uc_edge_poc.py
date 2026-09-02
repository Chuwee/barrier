import time
import unittest
from dataclasses import replace

from edge_core import (
    Display,
    EdgeConnection,
    EdgeEndpoint,
    HostSnapshot,
    Point,
    TransportEdge,
    boosted_transport_magnitude,
    inward_delta,
    near_edge_segment,
    outward_component,
    point_inside_edge,
    point_on_edge,
    should_trigger,
    target_delta,
    validate_connections,
)
from peer_transport import PeerRecord


DISPLAY = Display(
    id=1,
    key="display-a",
    index=0,
    name="Studio Display",
    x=100,
    y=50,
    width=1200,
    height=800,
    main=True,
)


class GeometryTests(unittest.TestCase):
    def test_outward_component(self) -> None:
        self.assertEqual(outward_component("right", 4, 0), 4)
        self.assertEqual(outward_component("left", -4, 0), 4)
        self.assertEqual(outward_component("top", 0, -3), 3)
        self.assertEqual(outward_component("bottom", 0, 3), 3)

    def test_target_delta_rotates_motion(self) -> None:
        self.assertEqual(target_delta("left", 5), (-5, 0))
        self.assertEqual(target_delta("bottom", 5), (0, 5))
        self.assertEqual(inward_delta("left", 5), (5, -0.0))

    def test_transport_pressure_is_amplified_and_bounded(self) -> None:
        self.assertEqual(boosted_transport_magnitude(1, 3, 8, 24), 8)
        self.assertEqual(boosted_transport_magnitude(4, 3, 8, 24), 12)
        self.assertEqual(boosted_transport_magnitude(20, 3, 8, 24), 24)
        self.assertEqual(boosted_transport_magnitude(-4, 3, 8, 24), 12)

    def test_right_edge_crossing_in_interval_triggers(self) -> None:
        self.assertTrue(
            should_trigger(
                DISPLAY,
                "right",
                20,
                80,
                4,
                Point(1298, 450),
                Point(1301, 450),
                3,
                0,
            )
        )

    def test_wrong_direction_does_not_trigger(self) -> None:
        self.assertFalse(
            should_trigger(
                DISPLAY,
                "right",
                0,
                100,
                4,
                Point(1301, 450),
                Point(1299, 450),
                -2,
                0,
            )
        )

    def test_outside_interval_does_not_trigger(self) -> None:
        self.assertFalse(
            should_trigger(
                DISPLAY,
                "right",
                40,
                60,
                4,
                Point(1298, 80),
                Point(1300, 80),
                2,
                0,
            )
        )

    def test_transport_point_uses_last_pixel(self) -> None:
        self.assertEqual(point_on_edge(DISPLAY, "right", 50), Point(1299, 450))
        self.assertEqual(point_on_edge(DISPLAY, "top", 25), Point(400, 50))

    def test_destination_point_is_inside_display(self) -> None:
        self.assertEqual(point_inside_edge(DISPLAY, "left", 50, 8), Point(108, 450))
        self.assertEqual(point_inside_edge(DISPLAY, "bottom", 25, 8), Point(400, 842))

    def test_point_must_be_near_transport_edge_and_inside_its_range(self) -> None:
        self.assertTrue(near_edge_segment(DISPLAY, "top", 20, 80, Point(700, 54), 8))
        self.assertFalse(near_edge_segment(DISPLAY, "top", 20, 80, Point(700, 90), 8))
        self.assertFalse(near_edge_segment(DISPLAY, "top", 20, 80, Point(110, 54), 8))

    def test_transport_range_confirmation_supports_reversed_ranges(self) -> None:
        self.assertTrue(near_edge_segment(DISPLAY, "right", 80, 20, Point(1297, 450), 8))


class TopologyTests(unittest.TestCase):
    def setUp(self) -> None:
        remote_display = Display(
            id=2,
            key="display-b",
            index=0,
            name="iMac",
            x=0,
            y=0,
            width=1920,
            height=1080,
            main=True,
        )
        self.host_a = HostSnapshot("mac-a", "MacBook", (DISPLAY,))
        self.host_b = HostSnapshot("mac-b", "iMac", (remote_display,))

    def connection(
        self,
        connection_id: str = "one",
        a_start: float = 20,
        a_end: float = 80,
    ) -> EdgeConnection:
        return EdgeConnection(
            id=connection_id,
            name=connection_id,
            a=EdgeEndpoint("mac-a", "display-a", "right", a_start, a_end),
            b=EdgeEndpoint("mac-b", "display-b", "left", 100, 0),
            a_transport=TransportEdge("display-a", "right", 30, 70),
            b_transport=TransportEdge("display-b", "left", 25, 75),
        )

    def test_directed_fraction_scales_and_flips(self) -> None:
        connection = self.connection()
        self.assertTrue(connection.b.contains(75))
        self.assertEqual(connection.a.normalize(20), 0)
        self.assertEqual(connection.a.normalize(50), 0.5)
        self.assertEqual(connection.b.position(0.25), 75)
        self.assertEqual(connection.b.normalize(75), 0.25)

    def test_route_swaps_endpoint_and_transport(self) -> None:
        connection = self.connection()
        source, destination, transport = connection.route_from("mac-b") or (None, None, None)
        self.assertEqual(source, connection.b)
        self.assertEqual(destination, connection.a)
        self.assertEqual(transport, connection.b_transport)

    def test_validates_display_references(self) -> None:
        result = validate_connections(
            [self.connection()],
            [self.host_a, self.host_b],
        )
        self.assertEqual(len(result), 1)

    def test_connection_json_round_trip_preserves_direction(self) -> None:
        connection = self.connection()
        restored = EdgeConnection.from_json(connection.to_json())
        self.assertEqual(restored, connection)
        self.assertEqual(restored.b.start, 100)
        self.assertEqual(restored.b.end, 0)

    def test_rejects_overlapping_source_ranges(self) -> None:
        with self.assertRaisesRegex(ValueError, "overlaps"):
            validate_connections(
                [self.connection(), self.connection("two", 70, 90)],
                [self.host_a, self.host_b],
            )

    def test_disabled_mapping_does_not_reserve_edge_range(self) -> None:
        disabled = replace(self.connection(), enabled=False)
        overlapping = self.connection("two", 40, 60)
        result = validate_connections(
            [disabled, overlapping],
            [self.host_a, self.host_b],
        )
        self.assertEqual(len(result), 2)

    def test_rejects_unknown_display(self) -> None:
        connection = self.connection()
        broken = replace(
            connection,
            a=EdgeEndpoint("mac-a", "missing", "right"),
        )
        with self.assertRaisesRegex(ValueError, "disconnected display"):
            validate_connections([broken], [self.host_a, self.host_b])


class PeerRecordTests(unittest.TestCase):
    def test_public_state_hides_token_and_tracks_remote_runtime(self) -> None:
        peer = PeerRecord(
            id="mac-b",
            name="iMac",
            address="imac.local",
            port=8766,
            token="secret-token",
        )
        snapshot = HostSnapshot("mac-b", "iMac", (DISPLAY,))
        updated = peer.with_snapshot(
            snapshot,
            time.time(),
            {
                "armed": True,
                "tap": {"running": True, "level": "HID"},
                "permissions": {"listen": True},
            },
        )
        self.assertNotIn("token", updated.to_json())
        self.assertTrue(updated.tap_running)
        self.assertEqual(updated.tap_level, "HID")
        self.assertTrue(updated.listen_access)


if __name__ == "__main__":
    unittest.main()
