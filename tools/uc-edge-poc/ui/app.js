const EDGES = ["left", "right", "top", "bottom"];
const $ = (id) => document.getElementById(id);
const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[char]));

let state = null;
let editId = null;
let formSignature = "";

async function api(path, body) {
  const options = body === undefined ? {} : {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify(body),
  };
  const response = await fetch(path, options);
  const value = await response.json();
  if (!response.ok) throw new Error(value.error || response.statusText);
  return value;
}

function message(id, value) {
  $(id).textContent = value;
}

function setOptions(select, values, label, preferred) {
  const old = preferred ?? select.value;
  select.innerHTML = values.map((value) => `<option value="${esc(value.value)}">${esc(value.label)}</option>`).join("");
  if (values.some((value) => String(value.value) === String(old))) select.value = old;
  if (!values.length) select.innerHTML = `<option value="">${esc(label)}</option>`;
}

function displayOptions(host) {
  return (host?.displays || []).map((display) => ({value: display.key, label: display.label}));
}

function initializeFormOptions(force = false) {
  if (!state) return;
  const peerHost = state.peer ? {id: state.peer.id, name: state.peer.name, displays: state.peer.displays} : null;
  const signature = JSON.stringify([
    state.local.id,
    state.local.displays.map((display) => display.key),
    peerHost?.id,
    peerHost?.displays.map((display) => display.key),
  ]);
  if (!force && signature === formSignature) return;
  formSignature = signature;
  const localDisplays = displayOptions(state.local);
  const remoteDisplays = displayOptions(peerHost);
  setOptions($("a-display"), localDisplays, "No local displays");
  setOptions($("at-display"), localDisplays, "No local displays");
  setOptions($("b-display"), remoteDisplays, "Pair a Mac first");
  setOptions($("bt-display"), remoteDisplays, "Pair a Mac first");
  for (const id of ["a-edge", "b-edge", "at-edge", "bt-edge"]) {
    const fallback = id.startsWith("b") ? "left" : "right";
    setOptions($(id), EDGES.map((edge) => ({value: edge, label: edge})), "", fallback);
  }
  $("a-host-label").textContent = state.local.name;
  $("a-transport-label").textContent = `${state.local.name} transport`;
  $("b-host-label").textContent = peerHost?.name || "Remote Mac";
  $("b-transport-label").textContent = `${peerHost?.name || "Remote Mac"} transport`;
  $("save-mapping").disabled = !peerHost;
}

function renderStatus() {
  const peerOk = Boolean(state.peer?.connected);
  const tapOk = Boolean(state.tap.running);
  const peerTapOk = Boolean(state.peer?.tap_running);
  const permissionOk = state.permissions.listen !== false && state.permissions.post !== false;
  $("status").innerHTML = [
    `<span class="badge ${peerOk ? "ok" : "bad"}">${peerOk ? `paired: ${esc(state.peer.name)}` : "peer offline"}</span>`,
    `<span class="badge ${tapOk ? "ok" : "bad"}">local ${tapOk ? `tap: ${esc(state.tap.level)}` : "tap stopped"}</span>`,
    state.peer ? `<span class="badge ${peerTapOk ? "ok" : "bad"}">remote ${peerTapOk ? `tap: ${esc(state.peer.tap_level)}` : "tap stopped"}</span>` : "",
    `<span class="badge ${permissionOk ? "ok" : "bad"}">${permissionOk ? "input access" : "permissions needed"}</span>`,
    `<span class="badge ${state.armed ? "ok" : ""}">routing: ${state.armed ? "armed" : "safe"}</span>`,
  ].join("");
}

function renderPairPanel() {
  $("local-name").textContent = state.local.name;
  $("pair-code").textContent = state.pairing.code;
  $("listen-addresses").textContent = state.pairing.addresses.map((address) => `${address}:${state.pairing.port}`).join(" · ");
  const panel = $("peer-panel");
  const mode = state.peer ? "paired" : "pair";
  if (panel.dataset.mode === mode) {
    if (state.peer) {
      const connection = $("peer-connection-state");
      if (connection) connection.textContent = state.peer.connected ? "reachable" : (state.peer.error || "offline");
    }
    return;
  }
  panel.dataset.mode = mode;
  if (state.peer) {
    panel.innerHTML = `<span class="eyebrow">Paired Mac</span><h2>${esc(state.peer.name)}</h2><p class="muted"><strong id="peer-connection-state">${state.peer.connected ? "reachable" : "offline"}</strong> at ${esc(state.peer.address)}:${state.peer.port}</p><button id="disconnect-peer" class="text-button" type="button">Forget pairing</button>`;
    $("disconnect-peer").onclick = async () => {
      try { await api("/api/peer/disconnect", {}); await refresh(); }
      catch (error) { message("runtime-message", error.message); }
    };
  } else {
    panel.innerHTML = `<span class="eyebrow">Pair another Mac</span><div class="pair-form"><label>Address<input id="peer-address" placeholder="other-mac.local"></label><label>Port<input id="peer-port" type="number" value="8766"></label><label>Pair code<input id="peer-code" inputmode="numeric" maxlength="6" placeholder="000000"></label><button id="connect-peer" type="button">Pair</button></div><div id="pair-message" class="message"></div>`;
    $("connect-peer").onclick = async () => {
      try {
        message("pair-message", "Connecting...");
        await api("/api/peer/connect", {
          address: $("peer-address").value.trim(),
          port: Number($("peer-port").value),
          code: $("peer-code").value.trim(),
        });
        panel.dataset.mode = "";
        await refresh();
      } catch (error) { message("pair-message", error.message); }
    };
  }
}

function hostLayout(host, zoneX, zoneY, zoneW, zoneH) {
  const displays = host.displays || [];
  if (!displays.length) return {rects: new Map(), svg: ""};
  const minX = Math.min(...displays.map((display) => display.x));
  const minY = Math.min(...displays.map((display) => display.y));
  const maxX = Math.max(...displays.map((display) => display.x + display.width));
  const maxY = Math.max(...displays.map((display) => display.y + display.height));
  const scale = Math.min((zoneW - 60) / Math.max(1, maxX - minX), (zoneH - 85) / Math.max(1, maxY - minY));
  const usedW = (maxX - minX) * scale;
  const usedH = (maxY - minY) * scale;
  const offsetX = zoneX + (zoneW - usedW) / 2;
  const offsetY = zoneY + 54 + (zoneH - 70 - usedH) / 2;
  const rects = new Map();
  let svg = `<rect class="host-zone" x="${zoneX}" y="${zoneY}" width="${zoneW}" height="${zoneH}" rx="18"/><text class="host-title" x="${zoneX + 22}" y="${zoneY + 31}">${esc(host.name)}</text>`;
  for (const display of displays) {
    const rect = {
      x: offsetX + (display.x - minX) * scale,
      y: offsetY + (display.y - minY) * scale,
      width: Math.max(70, display.width * scale),
      height: Math.max(48, display.height * scale),
    };
    rects.set(display.key, rect);
    svg += `<rect class="display-rect ${display.main ? "display-main" : ""}" x="${rect.x}" y="${rect.y}" width="${rect.width}" height="${rect.height}" rx="5"/>`;
    svg += `<text class="display-label" text-anchor="middle" x="${rect.x + rect.width / 2}" y="${rect.y + rect.height / 2 - 3}">${esc(display.name)}</text>`;
    svg += `<text class="display-size" text-anchor="middle" x="${rect.x + rect.width / 2}" y="${rect.y + rect.height / 2 + 13}">${display.width}×${display.height}</text>`;
  }
  return {rects, svg};
}

function segment(rect, edge, start, end) {
  const a = Math.max(0, Math.min(100, Number(start))) / 100;
  const b = Math.max(0, Math.min(100, Number(end))) / 100;
  if (edge === "left") return [{x: rect.x, y: rect.y + rect.height * a}, {x: rect.x, y: rect.y + rect.height * b}];
  if (edge === "right") return [{x: rect.x + rect.width, y: rect.y + rect.height * a}, {x: rect.x + rect.width, y: rect.y + rect.height * b}];
  if (edge === "top") return [{x: rect.x + rect.width * a, y: rect.y}, {x: rect.x + rect.width * b, y: rect.y}];
  return [{x: rect.x + rect.width * a, y: rect.y + rect.height}, {x: rect.x + rect.width * b, y: rect.y + rect.height}];
}

function midpoint(points) {
  return {x: (points[0].x + points[1].x) / 2, y: (points[0].y + points[1].y) / 2};
}

function drawSegment(rect, endpoint, className) {
  if (!rect) return "";
  const points = segment(rect, endpoint.edge, endpoint.start, endpoint.end);
  return `<line class="${className}" x1="${points[0].x}" y1="${points[0].y}" x2="${points[1].x}" y2="${points[1].y}"/>`;
}

function renderTopology() {
  const svg = $("topology");
  const empty = $("topology-empty");
  if (!state.peer) {
    svg.style.display = "none";
    empty.style.display = "grid";
    return;
  }
  svg.style.display = "block";
  empty.style.display = "none";
  const remote = {id: state.peer.id, name: state.peer.name, displays: state.peer.displays};
  const left = hostLayout(state.local, 20, 20, 550, 430);
  const right = hostLayout(remote, 630, 20, 550, 430);
  const layouts = new Map([[state.local.id, left], [remote.id, right]]);
  let routes = "";
  let transports = "";
  for (const connection of state.connections) {
    const aLayout = layouts.get(connection.a.host_id);
    const bLayout = layouts.get(connection.b.host_id);
    const aRect = aLayout?.rects.get(connection.a.display_key);
    const bRect = bLayout?.rects.get(connection.b.display_key);
    const aPoints = aRect ? segment(aRect, connection.a.edge, connection.a.start, connection.a.end) : null;
    const bPoints = bRect ? segment(bRect, connection.b.edge, connection.b.start, connection.b.end) : null;
    routes += drawSegment(aRect, connection.a, "map-segment");
    routes += drawSegment(bRect, connection.b, "map-segment");
    if (aPoints && bPoints) {
      const a = midpoint(aPoints), b = midpoint(bPoints);
      const bend = Math.max(55, Math.abs(b.x - a.x) * .35);
      routes += `<path class="map-curve" d="M ${a.x} ${a.y} C ${a.x + bend} ${a.y}, ${b.x - bend} ${b.y}, ${b.x} ${b.y}"/><circle class="map-dot" cx="${a.x}" cy="${a.y}" r="5"/><circle class="map-dot" cx="${b.x}" cy="${b.y}" r="5"/><text class="map-label" text-anchor="middle" x="${(a.x + b.x) / 2}" y="${(a.y + b.y) / 2 - 8}">${esc(connection.name)}</text>`;
    }
    const atLayout = layouts.get(connection.a.host_id);
    const btLayout = layouts.get(connection.b.host_id);
    transports += drawSegment(atLayout?.rects.get(connection.a_transport.display_key), connection.a_transport, "transport-segment");
    transports += drawSegment(btLayout?.rects.get(connection.b_transport.display_key), connection.b_transport, "transport-segment");
  }
  svg.innerHTML = left.svg + right.svg + transports + routes;
}

function orientedConnection(connection) {
  if (connection.a.host_id === state.local.id) return connection;
  return {
    ...connection,
    a: connection.b,
    b: connection.a,
    a_transport: connection.b_transport,
    b_transport: connection.a_transport,
  };
}

function describeEndpoint(endpoint) {
  const host = endpoint.host_id === state.local.id ? state.local : state.peer;
  const display = host?.displays.find((item) => item.key === endpoint.display_key);
  return `${host?.name || "Mac"} · ${display?.name || "display"} · ${endpoint.edge} ${endpoint.start}→${endpoint.end}%`;
}

function renderConnections() {
  $("connection-count").textContent = state.connections.length;
  const box = $("connections");
  if (!state.connections.length) {
    box.innerHTML = `<p class="muted">No mappings yet. Create one above; each saved mapping works in both directions.</p>`;
    return;
  }
  box.innerHTML = state.connections.map((connection) => `<div class="connection-row"><div><h3>${esc(connection.name)} ${connection.enabled ? "" : "(disabled)"}</h3><p>${esc(describeEndpoint(connection.a))}<br>${esc(describeEndpoint(connection.b))}</p></div><div class="row-actions"><button class="secondary edit" data-id="${esc(connection.id)}">Edit</button><button class="secondary toggle" data-id="${esc(connection.id)}">${connection.enabled ? "Disable" : "Enable"}</button><button class="danger delete" data-id="${esc(connection.id)}">Delete</button></div></div>`).join("");
  box.querySelectorAll(".edit").forEach((button) => button.onclick = () => editConnection(button.dataset.id));
  box.querySelectorAll(".toggle").forEach((button) => button.onclick = () => mutateConnection(button.dataset.id, (connection) => ({...connection, enabled: !connection.enabled})));
  box.querySelectorAll(".delete").forEach((button) => button.onclick = () => deleteConnection(button.dataset.id));
}

function renderEvents() {
  $("events").innerHTML = state.events.length ? state.events.map((event) => `<div class="event-row"><span>${esc(event.time)}</span><strong>${esc(event.kind)}</strong><span>${esc(event.message)}</span></div>`).join("") : `<p class="muted">No events yet.</p>`;
}

function render(next) {
  state = next;
  renderStatus();
  renderPairPanel();
  initializeFormOptions();
  renderTopology();
  renderConnections();
  renderEvents();
}

function field(id, value) {
  $(id).value = String(value);
}

function editConnection(id) {
  const raw = state.connections.find((connection) => connection.id === id);
  if (!raw) return;
  const connection = orientedConnection(raw);
  editId = id;
  $("editor-title").textContent = "Edit bidirectional mapping";
  field("mapping-name", connection.name);
  for (const [prefix, endpoint] of [["a", connection.a], ["b", connection.b]]) {
    field(`${prefix}-display`, endpoint.display_key);
    field(`${prefix}-edge`, endpoint.edge);
    field(`${prefix}-start`, endpoint.start);
    field(`${prefix}-end`, endpoint.end);
  }
  for (const [prefix, endpoint] of [["at", connection.a_transport], ["bt", connection.b_transport]]) {
    field(`${prefix}-display`, endpoint.display_key);
    field(`${prefix}-edge`, endpoint.edge);
    field(`${prefix}-start`, endpoint.start);
    field(`${prefix}-end`, endpoint.end);
  }
  $("editor-title").scrollIntoView({behavior: "smooth", block: "center"});
}

function clearEditor() {
  editId = null;
  $("editor-title").textContent = "New bidirectional mapping";
  field("mapping-name", "Primary handoff");
  for (const prefix of ["a", "b"]) { field(`${prefix}-start`, 0); field(`${prefix}-end`, 100); }
  field("a-edge", "right"); field("b-edge", "left");
  for (const prefix of ["at", "bt"]) { field(`${prefix}-start`, 25); field(`${prefix}-end`, 75); }
  field("at-edge", "right"); field("bt-edge", "left");
  message("editor-message", "");
}

function endpoint(prefix, hostId) {
  return {
    host_id: hostId,
    display_key: $(`${prefix}-display`).value,
    edge: $(`${prefix}-edge`).value,
    start: Number($(`${prefix}-start`).value),
    end: Number($(`${prefix}-end`).value),
  };
}

function transport(prefix) {
  return {
    display_key: $(`${prefix}-display`).value,
    edge: $(`${prefix}-edge`).value,
    start: Number($(`${prefix}-start`).value),
    end: Number($(`${prefix}-end`).value),
  };
}

function formConnection() {
  if (!state.peer) throw new Error("Pair the other Mac first");
  return {
    id: editId || (crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random()}`),
    name: $("mapping-name").value.trim() || "Edge mapping",
    a: endpoint("a", state.local.id),
    b: endpoint("b", state.peer.id),
    a_transport: transport("at"),
    b_transport: transport("bt"),
    enabled: true,
  };
}

async function saveMapping() {
  try {
    const connection = formConnection();
    const connections = editId ? state.connections.map((item) => item.id === editId ? connection : item) : [...state.connections, connection];
    await api("/api/connections", {connections});
    message("editor-message", "Mapping saved and synchronized.");
    editId = null;
    await refresh();
  } catch (error) { message("editor-message", error.message); }
}

async function mutateConnection(id, mutate) {
  try {
    const connections = state.connections.map((connection) => connection.id === id ? mutate(connection) : connection);
    await api("/api/connections", {connections});
    await refresh();
  } catch (error) { message("editor-message", error.message); }
}

async function deleteConnection(id) {
  if (!confirm("Delete this bidirectional edge mapping?")) return;
  try {
    await api("/api/connections", {connections: state.connections.filter((connection) => connection.id !== id)});
    await refresh();
  } catch (error) { message("editor-message", error.message); }
}

async function routing(path, success) {
  try {
    message("runtime-message", "Working...");
    await api(path, {});
    message("runtime-message", success);
    setTimeout(refresh, 300);
  } catch (error) { message("runtime-message", error.message); }
}

async function refresh() {
  try { render(await api("/api/state")); }
  catch (error) { message("runtime-message", error.message); }
}

$("save-mapping").onclick = saveMapping;
$("clear-edit").onclick = clearEditor;
$("activate").onclick = () => routing("/api/routing/activate", "Routing activation sent to both Macs.");
$("stop").onclick = () => routing("/api/routing/stop", "Routing stopped on both Macs.");
$("restore").onclick = () => routing("/api/restore", "Emergency stop requested.");
$("permissions").onclick = () => routing("/api/access/request", "Permission request sent on this Mac.");
window.addEventListener("resize", () => state && renderTopology());
setInterval(refresh, 1000);
refresh();
