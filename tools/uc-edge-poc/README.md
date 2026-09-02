# Universal Control Edge Lab

Edge Lab is a macOS proof of concept that adds a user-defined display topology
on top of Apple Universal Control. Universal Control still transports mouse,
keyboard, and clipboard input. Edge Lab turns its rigid configured edges into
hidden transport triggers and places the cursor on the edge the user actually
selected.

The paired version supports:

- One agent on each Mac with authenticated LAN communication.
- Bidirectional logical edge mappings.
- Directed fractional ranges such as `20 -> 80%` mapped to `100 -> 0%`.
- Proportional cursor placement across displays with different sizes.
- Direct manipulation of display edges with draggable range handles and live
  overlap conflict feedback.
- Separate hidden Universal Control transport ranges for each direction.
- A unified web topology that visualizes logical and transport edges.
- Context-aware modifier + arrow navigation compiled from the virtual and local
  display layouts, with editable source bands and landing positions.
- A synchronized global keyboard switch with per-Mac display and landing point.
- Persistent pairing, mappings, and armed state.

## Requirements

- macOS on both computers.
- Universal Control already paired and working through Apple's configured edge.
- [uv](https://docs.astral.sh/uv/) installed on both computers.
- Accessibility and Input Monitoring permission for the terminal/Python process.
- TCP port `8766` reachable between the Macs. The web UI remains local-only on
  `127.0.0.1:8765`.

## Run on both Macs

From this repository on each Mac:

```sh
cd tools/uc-edge-poc
uv sync
uv run uc_edge_poc.py
```

Open `http://127.0.0.1:8765` if it does not open automatically. macOS may require
restarting the process after granting permissions.

## Pair and map

1. Keep Edge Lab running on both Macs.
2. On one Mac, enter the other Mac's `.local` address, peer port, and six-digit
   code shown in its UI.
3. Click an edge on each Mac in the topology. Drag either endpoint handle to
   resize its fractional range, or drag the highlighted segment to move it. The
   mapping is automatically bidirectional.
4. Expand **Universal Control transport edges** and select the real edge that
   currently enters the other Mac in Apple's arrangement. Configure the reverse
   transport on the paired Mac the same way.
5. Save the mapping, then choose **Activate on both Macs**.
6. Push through either logical edge. The source agent redirects the physical
   event to the hidden UC edge, and the receiving agent places the cursor at the
   proportionally mapped destination point.

## Keyboard switch

The **Contextual navigation** card can generate a complete `Command + Arrow`
graph from the current layout. Explicit cross-Mac edge mappings take priority;
directions not claimed by those mappings follow the physical arrangement of
displays attached to each Mac. This makes the same chord contextual: from a
left display, `Command + Right` goes to the center display, and from the center
display it goes to the display on the right.

Every generated rule remains editable. Its source display and fractional band
select where the rule applies, while its destination display and X/Y percentages
select the landing point. Same-Mac rules warp directly and do not need a
transport route. Cross-Mac rules use one saved Universal Control mapping as the
hidden transport. The contextual arrows and landing points are rendered over
the topology. The modifier chord is shared by the four arrow keys and can be
changed before generating or saving. While contextual navigation is enabled,
it owns all four arrows for that chord; an unmapped direction does nothing and
does not fall through to the older global keyboard switch.

After creating at least one edge mapping, use **Keyboard switch** to choose a
global modifier/key combination and the mapping whose real Universal Control
edges should carry the switch. Choose a destination display plus horizontal and
vertical landing percentages for each Mac, then save and activate routing.

The shortcut works in both directions and remains independent of the logical
edge ranges. Its crosshairs in the topology show the two saved landing points.
At least one modifier is required, and the shortcut is consumed by Edge Lab
instead of being delivered to the foreground application. Deleting its selected
mapping removes the shortcut. Disabling the mapping's logical edges does not
disable the keyboard switch because its UC transport geometry remains usable.

The receiving agent confirms that Universal Control delivered input at its real
transport edge before moving the pointer to the logical destination. It then
acknowledges the transfer to the source agent. If Apple does not complete the
transfer, Edge Lab restores the pre-handoff cursor position instead of leaving
the pointer exposed on the hidden transport edge.

After a confirmed transfer, a 220 ms settling guard keeps any in-flight UC
transport events on the logical destination. This prevents Apple's physical
edge from immediately pulling the cursor back to the hidden transport edge.
Edge arrivals integrate both mouse axes through a source-to-destination basis
transform during that guard instead of repeatedly warping or collapsing motion
onto one axis. Directed ranges also determine whether tangential motion is
preserved or flipped.
The hidden edge also receives a bounded movement boost so Universal Control's
push-through threshold is reached without requiring a long physical push.
An arrived edge remains latched until the cursor moves inward, preventing the
same physical events from immediately triggering the bidirectional mapping in
reverse. Unrelated outgoing mappings remain active while an incoming intent is
waiting for Universal Control.

Ranges are directed. `0 -> 100%` preserves orientation while `100 -> 0%` flips
it. Mapping `25 -> 75%` to `0 -> 100%` expands the middle half of one edge over
the full destination edge. Existing occupied ranges remain visible; an invalid
overlap turns the draft red and cannot be saved. Disabled mappings do not
reserve their edge ranges.

## Start at login

After completing the live test, stop the manually launched process and install
the per-user LaunchAgent on each Mac:

```sh
uv run install_launch_agent.py install
```

Routing resumes automatically only if it was armed when the previous process
stopped. Management remains available at `http://127.0.0.1:8765`.

To inspect or remove the agent:

```sh
uv run install_launch_agent.py status
uv run install_launch_agent.py uninstall
```

## Safety and security

- **Emergency stop + restore** disarms both sides and restores the last local
  pre-handoff cursor position.
- The web UI binds only to loopback. The peer API binds to the LAN and requires
  a random per-node bearer token established with the displayed pair code.
- Peer traffic is authenticated but not encrypted. Use only on a trusted LAN.
- Authenticated peer status includes recent handoff diagnostics and cursor
  coordinates so asymmetric two-Mac failures can be inspected remotely.
- The tool does not change Universal Control preferences or private Apple layout
  data.

## Current PoC limits

- One paired Mac is supported.
- Apple does not expose Universal Control peers as `CGDisplay` objects, so peer
  identity and displays come from the second Edge Lab agent.
- The source-side HID transformation is live-tested. Receiving-side placement
  and true two-Mac bidirectional behavior still require validation on two
  physical Macs and may vary by macOS release.
- Monitor identity uses vendor, model, serial, and connection unit. Moving an
  identical monitor to another port may require selecting it again.

## Tests

```sh
uv run python -m unittest -v
```
