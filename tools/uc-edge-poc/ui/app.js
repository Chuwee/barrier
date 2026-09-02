const EDGES = ["left", "right", "top", "bottom"];
const DIRECTION_LABELS = {left: "Left", right: "Right", top: "Up", bottom: "Down"};
const DIRECTION_ARROWS = {left: "←", right: "→", top: "↑", bottom: "↓"};
const MODIFIER_GLYPHS = {control: "⌃", option: "⌥", shift: "⇧", command: "⌘"};
const HOTKEYS = [
  ["F1", 122], ["F2", 120], ["F3", 99], ["F4", 118], ["F5", 96], ["F6", 97],
  ["F7", 98], ["F8", 100], ["F9", 101], ["F10", 109], ["F11", 103], ["F12", 111],
  ["A", 0], ["B", 11], ["C", 8], ["D", 2], ["E", 14], ["F", 3], ["G", 5],
  ["H", 4], ["I", 34], ["J", 38], ["K", 40], ["L", 37], ["M", 46], ["N", 45],
  ["O", 31], ["P", 35], ["Q", 12], ["R", 15], ["S", 1], ["T", 17], ["U", 32],
  ["V", 9], ["W", 13], ["X", 7], ["Y", 16], ["Z", 6],
  ["0", 29], ["1", 18], ["2", 19], ["3", 20], ["4", 21], ["5", 23], ["6", 22],
  ["7", 26], ["8", 28], ["9", 25], ["Left Arrow", 123], ["Right Arrow", 124],
  ["Down Arrow", 125], ["Up Arrow", 126], ["Space", 49],
].map(([label, value]) => ({label, value}));
const $ = (id) => document.getElementById(id);
const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[char]));

let state = null;
let editId = null;
let formSignature = "";
let shortcutSignature = "";
let navigationSignature = "";
let navigationDraft = null;
let navigationDirty = false;
let topologyLayouts = new Map();
let dragState = null;

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
  if (!editId && state.connections.length) applyKnownTransportDefaults();
  $("a-host-label").textContent = state.local.name;
  $("a-transport-label").textContent = `${state.local.name} transport`;
  $("b-host-label").textContent = peerHost?.name || "Remote Mac";
  $("b-transport-label").textContent = `${peerHost?.name || "Remote Mac"} transport`;
  $("save-mapping").disabled = !peerHost;
}

function destinationForHost(shortcut, hostId) {
  if (shortcut?.a_destination?.host_id === hostId) return shortcut.a_destination;
  if (shortcut?.b_destination?.host_id === hostId) return shortcut.b_destination;
  return null;
}

function initializeShortcutOptions(force = false) {
  if (!state) return;
  const peerHost = state.peer ? {id: state.peer.id, name: state.peer.name, displays: state.peer.displays} : null;
  const signature = JSON.stringify({
    local: state.local.displays.map((display) => display.key),
    peer: peerHost?.displays.map((display) => display.key),
    connections: state.connections.map((connection) => [connection.id, connection.name, connection.enabled]),
    shortcut: state.keyboard_switch,
  });
  if (!force && signature === shortcutSignature) return;
  shortcutSignature = signature;
  const shortcut = state.keyboard_switch;
  const localDestination = destinationForHost(shortcut, state.local.id);
  const remoteDestination = destinationForHost(shortcut, peerHost?.id);
  setOptions($("shortcut-key"), HOTKEYS, "No keys", shortcut?.key_code ?? 100);
  setOptions(
    $("shortcut-route"),
    state.connections.map((connection) => ({
      value: connection.id,
      label: `${connection.name}${connection.enabled ? "" : " (edges off)"}`,
    })),
    "Create an edge mapping first",
    shortcut?.connection_id,
  );
  setOptions($("shortcut-a-display"), displayOptions(state.local), "No local displays", localDestination?.display_key);
  setOptions($("shortcut-b-display"), displayOptions(peerHost), "Pair a Mac first", remoteDestination?.display_key);
  $("shortcut-a-label").textContent = `${state.local.name} landing`;
  $("shortcut-b-label").textContent = `${peerHost?.name || "Remote Mac"} landing`;
  $("shortcut-enabled").checked = shortcut?.enabled ?? true;
  for (const modifier of ["control", "option", "shift", "command"]) {
    const defaults = modifier === "control" || modifier === "option";
    $(`shortcut-${modifier}`).checked = shortcut ? shortcut.modifiers.includes(modifier) : defaults;
  }
  field("shortcut-a-x", localDestination?.x_percent ?? 50);
  field("shortcut-a-y", localDestination?.y_percent ?? 50);
  field("shortcut-b-x", remoteDestination?.x_percent ?? 50);
  field("shortcut-b-y", remoteDestination?.y_percent ?? 50);
  $("save-shortcut").disabled = !peerHost || !state.connections.length;
  $("clear-shortcut").disabled = !shortcut;
}

function displayReferenceOptions() {
  const hosts = [state.local];
  if (state.peer) hosts.push({id: state.peer.id, name: state.peer.name, displays: state.peer.displays});
  return hosts.flatMap((host) => (host.displays || []).map((display) => ({
    value: JSON.stringify([host.id, display.key]),
    label: `${host.name} · ${display.name}`,
  })));
}

function displayReference(hostId, displayKey) {
  return JSON.stringify([hostId, displayKey]);
}

function parseDisplayReference(value) {
  const parsed = JSON.parse(value);
  if (!Array.isArray(parsed) || parsed.length !== 2) throw new Error("Choose a valid display");
  return {host_id: String(parsed[0]), display_key: String(parsed[1])};
}

function navigationRouteOptions(preferred) {
  const options = [{value: "", label: "Local display warp"}, ...state.connections.map((connection) => ({
    value: connection.id,
    label: `${connection.name}${connection.enabled ? "" : " (edges off)"}`,
  }))];
  return options.map((option) => `<option value="${esc(option.value)}" ${String(option.value) === String(preferred || "") ? "selected" : ""}>${esc(option.label)}</option>`).join("");
}

function navigationDisplayOptions(preferred) {
  return displayReferenceOptions().map((option) => `<option value="${esc(option.value)}" ${option.value === preferred ? "selected" : ""}>${esc(option.label)}</option>`).join("");
}

function renderNavigationRules() {
  const box = $("navigation-rules");
  const rules = navigationDraft?.bindings || [];
  if (!rules.length) {
    box.innerHTML = `<div class="navigation-empty">No arrow routes yet. Generate them from the topology or add one manually.</div>`;
    return;
  }
  box.innerHTML = rules.map((rule, index) => {
    const source = displayReference(rule.source_host_id, rule.source_display_key);
    const target = displayReference(rule.target.host_id, rule.target.display_key);
    const directions = EDGES.map((direction) => `<option value="${direction}" ${direction === rule.direction ? "selected" : ""}>${DIRECTION_LABELS[direction]} arrow</option>`).join("");
    return `<article class="navigation-rule${rule.enabled ? "" : " is-disabled"}" data-index="${index}" data-id="${esc(rule.id)}">
      <label><span class="mobile-label">When here</span><select data-field="source">${navigationDisplayOptions(source)}</select></label>
      <div class="navigation-direction"><span class="navigation-arrow">${DIRECTION_ARROWS[rule.direction]}</span><select data-field="direction">${directions}</select></div>
      <label><span class="mobile-label">Source band</span><span class="navigation-range"><input data-field="source_start" type="number" min="0" max="100" step="0.1" value="${esc(rule.source_start)}" aria-label="Source band start"><input data-field="source_end" type="number" min="0" max="100" step="0.1" value="${esc(rule.source_end)}" aria-label="Source band end"></span></label>
      <label><span class="mobile-label">Go here</span><select data-field="target">${navigationDisplayOptions(target)}</select></label>
      <label><span class="mobile-label">Landing X/Y</span><span class="navigation-landing"><input data-field="x_percent" type="number" min="0" max="100" step="0.1" value="${esc(rule.target.x_percent)}" aria-label="Landing horizontal percent"><input data-field="y_percent" type="number" min="0" max="100" step="0.1" value="${esc(rule.target.y_percent)}" aria-label="Landing vertical percent"></span></label>
      <label><span class="mobile-label">Transport</span><select data-field="connection_id">${navigationRouteOptions(rule.connection_id)}</select></label>
      <div class="navigation-rule-actions"><label class="toggle-label" title="Enable rule"><input data-field="enabled" type="checkbox" ${rule.enabled ? "checked" : ""}> On</label><button class="text-button remove-navigation-rule" type="button">Delete</button></div>
    </article>`;
  }).join("");
  box.querySelectorAll(".navigation-rule").forEach((row) => {
    const changed = (event) => {
      navigationDirty = true;
      const direction = row.querySelector('[data-field="direction"]').value;
      row.querySelector(".navigation-arrow").textContent = DIRECTION_ARROWS[direction];
      row.classList.toggle("is-disabled", !row.querySelector('[data-field="enabled"]').checked);
      if (event.target.dataset.field === "source" || event.target.dataset.field === "target") {
        const source = parseDisplayReference(row.querySelector('[data-field="source"]').value);
        const target = parseDisplayReference(row.querySelector('[data-field="target"]').value);
        const route = row.querySelector('[data-field="connection_id"]');
        if (source.host_id === target.host_id) route.value = "";
        else if (!route.value && state.connections.length) route.value = state.connections[0].id;
      }
      try { navigationDraft = formDirectionalNavigation(); } catch (_) { /* Save reports incomplete fields. */ }
      renderTopology();
    };
    row.querySelectorAll("input, select").forEach((input) => input.addEventListener("input", changed));
    row.querySelector(".remove-navigation-rule").onclick = () => {
      const index = Number(row.dataset.index);
      navigationDraft = formDirectionalNavigation();
      navigationDraft.bindings.splice(index, 1);
      navigationDirty = true;
      renderNavigationRules();
      renderTopology();
    };
  });
}

function initializeNavigationEditor(force = false) {
  if (!state) return;
  const signature = JSON.stringify({
    local: state.local.displays.map((display) => display.key),
    peer: state.peer?.displays.map((display) => display.key),
    connections: state.connections.map((connection) => [connection.id, connection.name, connection.enabled]),
    navigation: state.directional_navigation,
  });
  if (!force && (navigationDirty || signature === navigationSignature)) return;
  navigationSignature = signature;
  navigationDraft = state.directional_navigation
    ? JSON.parse(JSON.stringify(state.directional_navigation))
    : {modifiers: ["command"], bindings: [], enabled: true};
  $("navigation-enabled").checked = navigationDraft.enabled;
  for (const modifier of ["control", "option", "shift", "command"]) {
    $(`navigation-${modifier}`).checked = navigationDraft.modifiers.includes(modifier);
  }
  $("save-navigation").disabled = !displayReferenceOptions().length;
  $("clear-navigation").disabled = !state.directional_navigation;
  renderNavigationRules();
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

function landingPoint(layouts, destination) {
  const rect = layouts.get(destination?.host_id)?.rects.get(destination?.display_key);
  if (!rect) return null;
  return {
    x: rect.x + rect.width * Math.max(0, Math.min(100, destination.x_percent)) / 100,
    y: rect.y + rect.height * Math.max(0, Math.min(100, destination.y_percent)) / 100,
  };
}

function drawKeyboardLandings(layouts) {
  let shortcut = state.keyboard_switch;
  try { shortcut = formKeyboardSwitch(); }
  catch (_) { /* Keep the saved visualization while the form is incomplete. */ }
  if (!shortcut) return "";
  const destinations = [shortcut.a_destination, shortcut.b_destination];
  const points = destinations.map((destination) => landingPoint(layouts, destination));
  let html = "";
  if (points[0] && points[1]) {
    html += `<path class="landing-link" d="M ${points[0].x} ${points[0].y} C 570 ${points[0].y}, 630 ${points[1].y}, ${points[1].x} ${points[1].y}"/>`;
  }
  for (const point of points.filter(Boolean)) {
    html += `<circle class="landing-ring" cx="${point.x}" cy="${point.y}" r="9"/>`;
    html += `<path class="landing-cross" d="M ${point.x - 13} ${point.y} H ${point.x + 13} M ${point.x} ${point.y - 13} V ${point.y + 13}"/>`;
    html += `<text class="landing-label" x="${point.x + 14}" y="${point.y - 12}">${esc(shortcut.key_label)}</text>`;
  }
  return html;
}

function drawDirectionalNavigation(layouts) {
  let navigation = state.directional_navigation;
  try { navigation = formDirectionalNavigation(); }
  catch (_) { /* Keep the last complete draft visualization. */ }
  if (!navigation?.enabled) return "";
  const chord = navigation.modifiers.map((modifier) => MODIFIER_GLYPHS[modifier]).join("");
  let html = "";
  for (const rule of navigation.bindings.filter((binding) => binding.enabled)) {
    const sourceRect = layouts.get(rule.source_host_id)?.rects.get(rule.source_display_key);
    const target = landingPoint(layouts, rule.target);
    if (!sourceRect || !target) continue;
    const source = midpoint(segment(sourceRect, rule.direction, rule.source_start, rule.source_end));
    const bend = Math.max(30, Math.abs(target.x - source.x) * .28);
    html += `<path class="navigation-path" marker-end="url(#navigation-arrowhead)" d="M ${source.x} ${source.y} C ${source.x + bend} ${source.y}, ${target.x - bend} ${target.y}, ${target.x} ${target.y}"/>`;
    html += `<circle class="navigation-node" cx="${source.x}" cy="${source.y}" r="5"/>`;
    html += `<circle class="navigation-node" cx="${target.x}" cy="${target.y}" r="5"/>`;
    html += `<text class="navigation-label" x="${source.x + 8}" y="${source.y - 8}">${esc(chord + DIRECTION_ARROWS[rule.direction])}</text>`;
  }
  return html;
}

function rangesOverlap(first, second) {
  const firstLow = Math.min(first.start, first.end), firstHigh = Math.max(first.start, first.end);
  const secondLow = Math.min(second.start, second.end), secondHigh = Math.max(second.start, second.end);
  return Math.max(firstLow, secondLow) < Math.min(firstHigh, secondHigh);
}

function conflictsForDraft(draft) {
  const conflicts = new Set();
  for (const [prefix, endpoint] of [["a", draft.a], ["b", draft.b]]) {
    if (endpoint.start === endpoint.end) conflicts.add(prefix);
    if (!draft.enabled) continue;
    for (const connection of state.connections) {
      if (connection.id === editId || !connection.enabled) continue;
      for (const occupied of [connection.a, connection.b]) {
        if (
          endpoint.host_id === occupied.host_id &&
          endpoint.display_key === occupied.display_key &&
          endpoint.edge === occupied.edge &&
          rangesOverlap(endpoint, occupied)
        ) conflicts.add(prefix);
      }
    }
  }
  return conflicts;
}

function edgeHits(host, layout, prefix) {
  let html = "";
  for (const display of host.displays || []) {
    const rect = layout.rects.get(display.key);
    if (!rect) continue;
    for (const edge of EDGES) {
      const points = segment(rect, edge, 0, 100);
      html += `<line class="edge-hit" data-prefix="${prefix}" data-display="${esc(display.key)}" data-edge="${edge}" x1="${points[0].x}" y1="${points[0].y}" x2="${points[1].x}" y2="${points[1].y}"/>`;
    }
  }
  return html;
}

function draftEndpoint(rect, endpoint, prefix, conflict) {
  if (!rect) return "";
  const points = segment(rect, endpoint.edge, endpoint.start, endpoint.end);
  const vertical = endpoint.edge === "left" || endpoint.edge === "right";
  const labelOffset = endpoint.edge === "left" ? -13 : endpoint.edge === "right" ? 13 : 0;
  const labelY = endpoint.edge === "top" ? -13 : endpoint.edge === "bottom" ? 18 : 4;
  const conflictClass = conflict ? " conflict" : "";
  const orientationClass = vertical ? " vertical" : "";
  return [
    `<line id="draft-${prefix}-segment" class="draft-segment${conflictClass}" data-prefix="${prefix}" data-mode="range" x1="${points[0].x}" y1="${points[0].y}" x2="${points[1].x}" y2="${points[1].y}"/>`,
    `<circle class="draft-handle start${orientationClass}${conflictClass}" data-prefix="${prefix}" data-mode="start" cx="${points[0].x}" cy="${points[0].y}" r="8"/>`,
    `<circle class="draft-handle${orientationClass}${conflictClass}" data-prefix="${prefix}" data-mode="end" cx="${points[1].x}" cy="${points[1].y}" r="8"/>`,
    `<text class="handle-label${conflictClass}" text-anchor="middle" x="${points[0].x + labelOffset}" y="${points[0].y + labelY}">${Number(endpoint.start).toFixed(0)}%</text>`,
    `<text class="handle-label${conflictClass}" text-anchor="middle" x="${points[1].x + labelOffset}" y="${points[1].y + labelY}">${Number(endpoint.end).toFixed(0)}%</text>`,
  ].join("");
}

function draftValue() {
  if (!state.peer || !$('a-display').value || !$('b-display').value) return null;
  return formConnection(editId || "draft-preview");
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
  topologyLayouts = layouts;
  let routes = "";
  let transports = "";
  for (const connection of state.connections) {
    if (connection.id === editId) continue;
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
  const draft = draftValue();
  let draftHtml = "";
  if (draft) {
    const conflicts = conflictsForDraft(draft);
    const aRect = layouts.get(draft.a.host_id)?.rects.get(draft.a.display_key);
    const bRect = layouts.get(draft.b.host_id)?.rects.get(draft.b.display_key);
    const aPoints = aRect ? segment(aRect, draft.a.edge, draft.a.start, draft.a.end) : null;
    const bPoints = bRect ? segment(bRect, draft.b.edge, draft.b.start, draft.b.end) : null;
    draftHtml += drawSegment(aRect, {...draft.a, start: 0, end: 100}, "selected-edge");
    draftHtml += drawSegment(bRect, {...draft.b, start: 0, end: 100}, "selected-edge");
    draftHtml += drawSegment(layouts.get(draft.a.host_id)?.rects.get(draft.a_transport.display_key), draft.a_transport, "transport-segment draft-transport");
    draftHtml += drawSegment(layouts.get(draft.b.host_id)?.rects.get(draft.b_transport.display_key), draft.b_transport, "transport-segment draft-transport");
    if (aPoints && bPoints) {
      const a = midpoint(aPoints), b = midpoint(bPoints);
      const bend = Math.max(55, Math.abs(b.x - a.x) * .35);
      const conflictClass = conflicts.size ? " conflict" : "";
      draftHtml += `<path class="draft-curve${conflictClass}" d="M ${a.x} ${a.y} C ${a.x + bend} ${a.y}, ${b.x - bend} ${b.y}, ${b.x} ${b.y}"/>`;
    }
    draftHtml += draftEndpoint(aRect, draft.a, "a", conflicts.has("a"));
    draftHtml += draftEndpoint(bRect, draft.b, "b", conflicts.has("b"));
    const status = $("overlap-status");
    status.className = `overlap-status${conflicts.size ? " conflict" : ""}`;
    status.textContent = conflicts.size
      ? `Invalid or overlapping range on ${[...conflicts].map((side) => side === "a" ? state.local.name : state.peer.name).join(" and ")}. Move or resize the red range.`
      : `${draft.a.start}→${draft.a.end}% maps proportionally to ${draft.b.start}→${draft.b.end}%.`;
  }
  const hits = edgeHits(state.local, left, "a") + edgeHits(remote, right, "b");
  const navigation = drawDirectionalNavigation(layouts);
  const landings = drawKeyboardLandings(layouts);
  const definitions = `<defs><marker id="navigation-arrowhead" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="5" markerHeight="5" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" fill="#315a6b"/></marker></defs>`;
  svg.innerHTML = definitions + left.svg + right.svg + transports + routes + navigation + landings + hits + draftHtml;
  bindTopologyTargets();
}

function svgPoint(event) {
  const svg = $("topology");
  const point = svg.createSVGPoint();
  point.x = event.clientX;
  point.y = event.clientY;
  return point.matrixTransform(svg.getScreenCTM().inverse());
}

function percentageAt(rect, edge, point) {
  const value = edge === "left" || edge === "right"
    ? 100 * (point.y - rect.y) / rect.height
    : 100 * (point.x - rect.x) / rect.width;
  return Math.round(Math.max(0, Math.min(100, value)) * 10) / 10;
}

function rectForPrefix(prefix) {
  const hostId = prefix === "a" ? state.local.id : state.peer.id;
  return topologyLayouts.get(hostId)?.rects.get($(`${prefix}-display`).value);
}

function bindTopologyTargets() {
  const svg = $("topology");
  svg.querySelectorAll(".edge-hit").forEach((target) => {
    target.onpointerdown = (event) => {
      const prefix = target.dataset.prefix;
      field(`${prefix}-display`, target.dataset.display);
      field(`${prefix}-edge`, target.dataset.edge);
      const hostId = prefix === "a" ? state.local.id : state.peer.id;
      const rect = topologyLayouts.get(hostId)?.rects.get(target.dataset.display);
      if (rect) {
        const center = percentageAt(rect, target.dataset.edge, svgPoint(event));
        const oldStart = Number($(`${prefix}-start`).value);
        const oldEnd = Number($(`${prefix}-end`).value);
        const direction = oldEnd >= oldStart ? 1 : -1;
        const width = Math.min(100, Math.max(10, Math.abs(oldEnd - oldStart)));
        let low = Math.max(0, Math.min(100 - width, center - width / 2));
        let high = low + width;
        field(`${prefix}-start`, direction > 0 ? low : high);
        field(`${prefix}-end`, direction > 0 ? high : low);
      }
      message("editor-message", `${prefix === "a" ? state.local.name : state.peer.name} edge selected. Drag its handles to set overlap.`);
      renderTopology();
      event.preventDefault();
    };
  });
  svg.querySelectorAll(".draft-handle, .draft-segment").forEach((target) => {
    target.onpointerdown = (event) => {
      const prefix = target.dataset.prefix;
      const rect = rectForPrefix(prefix);
      if (!rect) return;
      dragState = {
        pointerId: event.pointerId,
        prefix,
        mode: target.dataset.mode,
        rect,
        edge: $(`${prefix}-edge`).value,
        pointerStart: percentageAt(rect, $(`${prefix}-edge`).value, svgPoint(event)),
        start: Number($(`${prefix}-start`).value),
        end: Number($(`${prefix}-end`).value),
      };
      svg.setPointerCapture(event.pointerId);
      event.preventDefault();
      event.stopPropagation();
    };
  });
}

function moveDraft(event) {
  if (!dragState || event.pointerId !== dragState.pointerId) return;
  const current = percentageAt(dragState.rect, dragState.edge, svgPoint(event));
  if (dragState.mode === "start" || dragState.mode === "end") {
    field(`${dragState.prefix}-${dragState.mode}`, current);
  } else {
    let start = dragState.start + current - dragState.pointerStart;
    let end = dragState.end + current - dragState.pointerStart;
    const low = Math.min(start, end), high = Math.max(start, end);
    if (low < 0) { start -= low; end -= low; }
    if (high > 100) { start -= high - 100; end -= high - 100; }
    field(`${dragState.prefix}-start`, Math.round(start * 10) / 10);
    field(`${dragState.prefix}-end`, Math.round(end * 10) / 10);
  }
  renderTopology();
}

function finishDraftMove(event) {
  if (!dragState || event.pointerId !== dragState.pointerId) return;
  const svg = $("topology");
  if (svg.hasPointerCapture(event.pointerId)) svg.releasePointerCapture(event.pointerId);
  dragState = null;
  renderTopology();
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

function applyKnownTransportDefaults() {
  const raw = state?.connections.find((connection) => connection.enabled) || state?.connections[0];
  if (!raw) {
    for (const prefix of ["at", "bt"]) { field(`${prefix}-start`, 25); field(`${prefix}-end`, 75); }
    field("at-edge", "right"); field("bt-edge", "left");
    return;
  }
  const connection = orientedConnection(raw);
  for (const [prefix, endpoint] of [["at", connection.a_transport], ["bt", connection.b_transport]]) {
    field(`${prefix}-display`, endpoint.display_key);
    field(`${prefix}-edge`, endpoint.edge);
    field(`${prefix}-start`, endpoint.start);
    field(`${prefix}-end`, endpoint.end);
  }
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
  initializeShortcutOptions();
  initializeNavigationEditor();
  if (!dragState) renderTopology();
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
  renderTopology();
  $("editor-title").scrollIntoView({behavior: "smooth", block: "center"});
}

function clearEditor() {
  editId = null;
  $("editor-title").textContent = "New bidirectional mapping";
  field("mapping-name", "Primary handoff");
  for (const prefix of ["a", "b"]) { field(`${prefix}-start`, 0); field(`${prefix}-end`, 100); }
  field("a-edge", "right"); field("b-edge", "left");
  applyKnownTransportDefaults();
  message("editor-message", "");
  renderTopology();
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

function formConnection(id = editId || "draft-preview") {
  if (!state.peer) throw new Error("Pair the other Mac first");
  const existing = editId ? state.connections.find((connection) => connection.id === editId) : null;
  return {
    id,
    name: $("mapping-name").value.trim() || "Edge mapping",
    a: endpoint("a", state.local.id),
    b: endpoint("b", state.peer.id),
    a_transport: transport("at"),
    b_transport: transport("bt"),
    enabled: existing?.enabled ?? true,
  };
}

function shortcutDestination(prefix, hostId) {
  const destination = {
    host_id: hostId,
    display_key: $(`shortcut-${prefix}-display`).value,
    x_percent: Number($(`shortcut-${prefix}-x`).value),
    y_percent: Number($(`shortcut-${prefix}-y`).value),
  };
  if (!destination.display_key) throw new Error("Choose a landing display on both Macs");
  if (![destination.x_percent, destination.y_percent].every((value) => Number.isFinite(value) && value >= 0 && value <= 100)) {
    throw new Error("Landing positions must stay between 0 and 100%");
  }
  return destination;
}

function formKeyboardSwitch() {
  if (!state.peer) throw new Error("Pair the other Mac first");
  const keyCode = Number($("shortcut-key").value);
  const key = HOTKEYS.find((item) => item.value === keyCode);
  if (!key) throw new Error("Choose a shortcut key");
  if (!$("shortcut-route").value) throw new Error("Choose a UC transport route");
  return {
    connection_id: $("shortcut-route").value,
    key_code: keyCode,
    key_label: key.label,
    modifiers: ["control", "option", "shift", "command"].filter((modifier) => $(`shortcut-${modifier}`).checked),
    a_destination: shortcutDestination("a", state.local.id),
    b_destination: shortcutDestination("b", state.peer.id),
    enabled: $("shortcut-enabled").checked,
  };
}

function formDirectionalNavigation() {
  const modifiers = ["control", "option", "shift", "command"].filter((modifier) => $(`navigation-${modifier}`).checked);
  const bindings = [...$("navigation-rules").querySelectorAll(".navigation-rule")].map((row) => {
    const source = parseDisplayReference(row.querySelector('[data-field="source"]').value);
    const target = parseDisplayReference(row.querySelector('[data-field="target"]').value);
    const values = {
      source_start: Number(row.querySelector('[data-field="source_start"]').value),
      source_end: Number(row.querySelector('[data-field="source_end"]').value),
      x_percent: Number(row.querySelector('[data-field="x_percent"]').value),
      y_percent: Number(row.querySelector('[data-field="y_percent"]').value),
    };
    if (!Object.values(values).every((value) => Number.isFinite(value) && value >= 0 && value <= 100)) {
      throw new Error("Rule ranges and landing positions must stay between 0 and 100%");
    }
    if (values.source_start === values.source_end) throw new Error("A source band cannot have zero length");
    const connectionId = row.querySelector('[data-field="connection_id"]').value || null;
    if (source.host_id !== target.host_id && !connectionId) throw new Error("Cross-Mac rules need a UC transport route");
    return {
      id: row.dataset.id,
      source_host_id: source.host_id,
      source_display_key: source.display_key,
      direction: row.querySelector('[data-field="direction"]').value,
      source_start: values.source_start,
      source_end: values.source_end,
      target: {
        host_id: target.host_id,
        display_key: target.display_key,
        x_percent: values.x_percent,
        y_percent: values.y_percent,
      },
      connection_id: connectionId,
      enabled: row.querySelector('[data-field="enabled"]').checked,
    };
  });
  return {modifiers, bindings, enabled: $("navigation-enabled").checked};
}

async function saveDirectionalNavigation() {
  try {
    const navigation = formDirectionalNavigation();
    if (!navigation.modifiers.length) throw new Error("Choose at least one modifier key");
    await api("/api/directional-navigation", {directional_navigation: navigation});
    navigationDirty = false;
    navigationSignature = "";
    message("navigation-message", `${navigation.bindings.length} contextual arrow rule(s) saved and synchronized.`);
    await refresh();
  } catch (error) { message("navigation-message", error.message); }
}

async function proposeDirectionalNavigation() {
  try {
    const modifiers = ["control", "option", "shift", "command"].filter((modifier) => $(`navigation-${modifier}`).checked);
    if (!modifiers.length) throw new Error("Choose at least one modifier key");
    const response = await api("/api/directional-navigation/propose", {modifiers});
    navigationDirty = false;
    navigationSignature = "";
    navigationDraft = response.directional_navigation;
    message("navigation-message", `Generated ${navigationDraft.bindings.length} contextual arrow rule(s) from the current layout.`);
    await refresh();
  } catch (error) { message("navigation-message", error.message); }
}

function addDirectionalRule() {
  try {
    navigationDraft = formDirectionalNavigation();
    const displays = displayReferenceOptions();
    if (!displays.length) throw new Error("No displays are available");
    const source = parseDisplayReference(displays[0].value);
    const preferredTarget = displays.find((item) => item.value !== displays[0].value) || displays[0];
    const target = parseDisplayReference(preferredTarget.value);
    navigationDraft.bindings.push({
      id: crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random()}`,
      source_host_id: source.host_id,
      source_display_key: source.display_key,
      direction: "right",
      source_start: 0,
      source_end: 100,
      target: {...target, x_percent: 0, y_percent: 50},
      connection_id: source.host_id === target.host_id ? null : (state.connections[0]?.id || null),
      enabled: true,
    });
    navigationDirty = true;
    renderNavigationRules();
    renderTopology();
  } catch (error) { message("navigation-message", error.message); }
}

async function clearDirectionalNavigation() {
  try {
    await api("/api/directional-navigation", {directional_navigation: null});
    navigationDirty = false;
    navigationSignature = "";
    message("navigation-message", "Contextual navigation removed.");
    await refresh();
  } catch (error) { message("navigation-message", error.message); }
}

async function saveKeyboardSwitch() {
  try {
    const keyboardSwitch = formKeyboardSwitch();
    if (!keyboardSwitch.modifiers.length) throw new Error("Choose at least one modifier key");
    await api("/api/keyboard-switch", {keyboard_switch: keyboardSwitch});
    message("shortcut-message", "Keyboard switch saved and synchronized.");
    shortcutSignature = "";
    await refresh();
  } catch (error) { message("shortcut-message", error.message); }
}

async function clearKeyboardSwitch() {
  try {
    await api("/api/keyboard-switch", {keyboard_switch: null});
    message("shortcut-message", "Keyboard switch removed.");
    shortcutSignature = "";
    await refresh();
  } catch (error) { message("shortcut-message", error.message); }
}

async function saveMapping() {
  try {
    const id = editId || (crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random()}`);
    const connection = formConnection(id);
    const conflicts = conflictsForDraft(connection);
    if (conflicts.size) throw new Error("A range is empty or overlaps an existing mapping. Adjust the red handles first.");
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
$("save-shortcut").onclick = saveKeyboardSwitch;
$("clear-shortcut").onclick = clearKeyboardSwitch;
$("save-navigation").onclick = saveDirectionalNavigation;
$("propose-navigation").onclick = proposeDirectionalNavigation;
$("add-navigation-rule").onclick = addDirectionalRule;
$("clear-navigation").onclick = clearDirectionalNavigation;
$("activate").onclick = () => routing("/api/routing/activate", "Routing activation sent to both Macs.");
$("stop").onclick = () => routing("/api/routing/stop", "Routing stopped on both Macs.");
$("restore").onclick = () => routing("/api/restore", "Emergency stop requested.");
$("permissions").onclick = () => routing("/api/access/request", "Permission request sent on this Mac.");
$("topology").addEventListener("pointermove", moveDraft);
$("topology").addEventListener("pointerup", finishDraftMove);
$("topology").addEventListener("pointercancel", finishDraftMove);
for (const id of ["a-display", "a-edge", "a-start", "a-end", "b-display", "b-edge", "b-start", "b-end", "at-display", "at-edge", "at-start", "at-end", "bt-display", "bt-edge", "bt-start", "bt-end"]) {
  $(id).addEventListener("input", () => state?.peer && renderTopology());
}
for (const id of ["shortcut-a-display", "shortcut-a-x", "shortcut-a-y", "shortcut-b-display", "shortcut-b-x", "shortcut-b-y", "shortcut-key"]) {
  $(id).addEventListener("input", () => state?.peer && renderTopology());
}
for (const modifier of ["control", "option", "shift", "command"]) {
  $(`navigation-${modifier}`).addEventListener("input", () => {
    navigationDirty = true;
    try { navigationDraft = formDirectionalNavigation(); } catch (_) { /* Save reports incomplete fields. */ }
    renderTopology();
  });
}
$("navigation-enabled").addEventListener("input", () => {
  navigationDirty = true;
  try { navigationDraft = formDirectionalNavigation(); } catch (_) { /* Save reports incomplete fields. */ }
  renderTopology();
});
window.addEventListener("resize", () => state && renderTopology());
setInterval(refresh, 1000);
refresh();
