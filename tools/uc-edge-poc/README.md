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
- Separate hidden Universal Control transport ranges for each direction.
- A unified web topology that visualizes logical and transport edges.
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
3. Create a mapping between a fractional edge on this Mac and one on the paired
   Mac. The mapping is automatically bidirectional.
4. Expand **Universal Control transport edges** and select the real edge that
   currently enters the other Mac in Apple's arrangement. Configure the reverse
   transport on the paired Mac the same way.
5. Save the mapping, then choose **Activate on both Macs**.
6. Push through either logical edge. The source agent redirects the physical
   event to the hidden UC edge, and the receiving agent places the cursor at the
   proportionally mapped destination point.

Ranges are directed. `0 -> 100%` preserves orientation while `100 -> 0%` flips
it. Mapping `25 -> 75%` to `0 -> 100%` expands the middle half of one edge over
the full destination edge.

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
