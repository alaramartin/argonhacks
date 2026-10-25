import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { CSS2DRenderer, CSS2DObject } from "three/addons/renderers/CSS2DRenderer.js";

// Coordinates follow contracts: y is up, walls are [x, z] on the floor, item.anchor is the box center.
const STATUS_COLOR = { present: 0x4f8cff, moved: 0x9aa3ad, stolen: 0xe5484d };
const WALL_T = 0.08;
const OPENING_Y = { door: [0, 2.1], window: [0.9, 2.1] };

const host = document.getElementById("scene");
const renderer = new THREE.WebGLRenderer({ antialias: true });
renderer.setPixelRatio(devicePixelRatio);
host.appendChild(renderer.domElement);

const labels = new CSS2DRenderer();
labels.domElement.className = "labels";
host.appendChild(labels.domElement);

const scene = new THREE.Scene();
scene.background = new THREE.Color(0x0f1115);
scene.add(new THREE.HemisphereLight(0xffffff, 0x334, 1.4));
const sun = new THREE.DirectionalLight(0xffffff, 1.2);
sun.position.set(4, 8, 6);
scene.add(sun);

const camera = new THREE.PerspectiveCamera(50, 1, 0.05, 100);
// The label overlay is pointer-events:none (only tags take clicks), so drags must land on the canvas.
const controls = new OrbitControls(camera, renderer.domElement);
controls.enableDamping = true;

let world = new THREE.Group();
scene.add(world);
const itemMeshes = new Map(); // id -> { mesh, tag }
let current = null;
let framed = false;

function resize() {
  const { clientWidth: w, clientHeight: h } = host;
  renderer.setSize(w, h);
  labels.setSize(w, h);
  camera.aspect = w / h;
  camera.updateProjectionMatrix();
}
new ResizeObserver(resize).observe(host);

// ---------- scene building ----------

function wallPiece(a, dir, len, from, to, y0, y1, mat) {
  const w = to - from, h = y1 - y0;
  if (w <= 0.001 || h <= 0.001) return null;
  const mesh = new THREE.Mesh(new THREE.BoxGeometry(w, h, WALL_T), mat);
  const mid = from + w / 2;
  mesh.position.set(a[0] + dir.x * mid, y0 + h / 2, a[1] + dir.y * mid);
  mesh.rotation.y = -Math.atan2(dir.y, dir.x);
  return mesh;
}

function buildRoom(room, group) {
  const mat = new THREE.MeshStandardMaterial({ color: 0xd9dde3, transparent: true, opacity: 0.35, side: THREE.DoubleSide, depthWrite: false });
  const edge = new THREE.LineBasicMaterial({ color: 0x8b93a1 });
  const xs = [], zs = [];

  room.walls.forEach((wall, i) => {
    const [a, b] = [wall.from, wall.to];
    xs.push(a[0], b[0]); zs.push(a[1], b[1]);
    const len = Math.hypot(b[0] - a[0], b[1] - a[1]);
    const dir = new THREE.Vector2((b[0] - a[0]) / len, (b[1] - a[1]) / len);
    const H = wall.height ?? 2.6;
    const ops = (room.openings || []).filter((o) => o.wall === i).sort((p, q) => p.offset - q.offset);

    // Split the wall around each opening: solid span, then below/above the opening.
    // Openings can overlap (a door inside a full-width window), so each one starts where the last ended.
    let cursor = 0;
    const pieces = [];
    for (const o of ops) {
      const [y0, y1] = OPENING_Y[o.type] || OPENING_Y.door;
      const start = Math.max(o.offset, cursor), end = Math.min(o.offset + o.width, len);
      if (end <= start) continue;
      pieces.push([cursor, start, 0, H]);
      pieces.push([start, end, 0, y0]);
      pieces.push([start, end, y1, H]);
      cursor = end;
    }
    pieces.push([cursor, len, 0, H]);
    for (const p of pieces) {
      const m = wallPiece(a, dir, len, ...p, mat);
      if (!m) continue;
      group.add(m);
      const lines = new THREE.LineSegments(new THREE.EdgesGeometry(m.geometry), edge);
      lines.position.copy(m.position); lines.rotation.copy(m.rotation);
      group.add(lines);
    }
  });

  // Floor from the wall bounding box.
  const [minX, maxX, minZ, maxZ] = [Math.min(...xs), Math.max(...xs), Math.min(...zs), Math.max(...zs)];
  const floor = new THREE.Mesh(new THREE.PlaneGeometry(maxX - minX, maxZ - minZ), new THREE.MeshStandardMaterial({ color: 0x232833 }));
  floor.rotation.x = -Math.PI / 2;
  floor.position.set((minX + maxX) / 2, 0, (minZ + maxZ) / 2);
  group.add(floor);
  const grid = new THREE.GridHelper(Math.max(maxX - minX, maxZ - minZ), Math.round(Math.max(maxX - minX, maxZ - minZ) * 2), 0x3a4150, 0x2b313c);
  grid.position.set(floor.position.x, 0.002, floor.position.z);
  group.add(grid);
}

function fmtPrice(p) {
  if (!p) return null;
  const f = (n) => "$" + Math.round(n).toLocaleString();
  return p.low === p.high ? f(p.low) : `${f(p.low)}–${f(p.high)}`;
}

function makeTag(item) {
  const el = document.createElement("button");
  el.className = `tag ${item.status || "present"}`;
  el.addEventListener("click", () => openEvidence(item.id));
  el.addEventListener("pointerenter", () => setHover(item.id, true));
  el.addEventListener("pointerleave", () => setHover(item.id, false));
  fillTag(el, item);
  return el;
}

function fillTag(el, item) {
  const price = fmtPrice(item.price);
  el.className = `tag ${item.status || "present"}${price ? "" : " unpriced"}`;
  el.innerHTML = "";
  const name = document.createElement("span");
  name.className = "name";
  name.textContent = item.label;
  const amt = document.createElement("span");
  amt.className = "price";
  amt.textContent = price || "pricing…";
  el.append(name, amt);
}

function buildItem(item, group) {
  const color = STATUS_COLOR[item.status] ?? STATUS_COLOR.present;
  const { w, h, d } = item.size;
  const geo = new THREE.BoxGeometry(Math.max(w, 0.02), Math.max(h, 0.02), Math.max(d, 0.02));
  const mesh = new THREE.Mesh(geo, new THREE.MeshStandardMaterial({ color, transparent: true, opacity: 0.55 }));
  mesh.position.set(item.anchor.x, item.anchor.y, item.anchor.z);
  mesh.userData.itemId = item.id;
  mesh.add(new THREE.LineSegments(new THREE.EdgesGeometry(geo), new THREE.LineBasicMaterial({ color })));
  const tagEl = makeTag(item);
  const tag = new CSS2DObject(tagEl);
  tag.position.set(0, h / 2 + 0.12, 0);
  mesh.add(tag);
  group.add(mesh);
  itemMeshes.set(item.id, { mesh, tagEl, tag, item, group: null });
}

// ---------- groups: items within GROUP_RADIUS of each other on the floor share one tag ----------
// The group tag lists its members (hover a row = highlight the box, click = evidence). Zooming in within
// SPLIT_DISTANCE of a group swaps it for the members' own tags; hovering a box always shows that box's tag.

const GROUP_RADIUS = 0.6;
const SPLIT_DISTANCE = 3.0;
let groups = [];
const expandedGroups = new Set();

function clusterItems(items) {
  const parent = items.map((_, i) => i);
  const find = (i) => (parent[i] === i ? i : (parent[i] = find(parent[i])));
  for (let i = 0; i < items.length; i++)
    for (let j = i + 1; j < items.length; j++) {
      const a = items[i].anchor, b = items[j].anchor;
      if (Math.hypot(a.x - b.x, a.z - b.z) <= GROUP_RADIUS) parent[find(i)] = find(j);
    }
  const byRoot = new Map();
  items.forEach((it, i) => byRoot.set(find(i), [...(byRoot.get(find(i)) || []), it]));
  return [...byRoot.values()].filter((m) => m.length > 1);
}

function sumPrice(items) {
  const priced = items.filter((i) => i.price);
  if (!priced.length) return null;
  return { low: priced.reduce((t, i) => t + i.price.low, 0), high: priced.reduce((t, i) => t + i.price.high, 0) };
}

function buildGroup(members, world) {
  const vol = (i) => i.size.w * i.size.h * i.size.d;
  const biggest = [...members].sort((a, b) => vol(b) - vol(a))[0];
  const ordered = [biggest, ...members.filter((m) => m !== biggest).sort((a, b) => (b.price?.high || 0) - (a.price?.high || 0))];
  const cx = members.reduce((t, m) => t + m.anchor.x, 0) / members.length;
  const cz = members.reduce((t, m) => t + m.anchor.z, 0) / members.length;
  const top = Math.max(...members.map((m) => m.anchor.y + m.size.h / 2));

  const el = document.createElement("div");
  el.className = "tag group" + (members.some((m) => m.status === "stolen") ? " stolen" : "");
  const head = document.createElement("button");
  head.className = "ghead";
  const name = document.createElement("span");
  name.className = "name";
  name.textContent = `${biggest.label} · ${members.length} items`;
  const amt = document.createElement("span");
  amt.className = "price";
  amt.textContent = fmtPrice(sumPrice(members)) || "pricing…";
  head.append(name, amt);
  const list = document.createElement("ol");
  list.className = "gitems";
  for (const m of ordered) {
    const row = document.createElement("li");
    const b = document.createElement("button");
    b.className = m.status || "present";
    const l = document.createElement("span");
    l.textContent = m.label;
    const p = document.createElement("b");
    p.textContent = fmtPrice(m.price) || "…";
    b.append(l, p);
    b.addEventListener("click", (e) => { e.stopPropagation(); openEvidence(m.id); });
    b.addEventListener("pointerenter", () => setHover(m.id, true));
    b.addEventListener("pointerleave", () => setHover(m.id, false));
    row.append(b);
    list.append(row);
  }
  el.append(head, list);

  // The scene reloads on every agent update, so remember which groups are open by their member ids.
  const key = members.map((m) => m.id).sort().join(",");
  const g = { ids: members.map((m) => m.id), el, expanded: expandedGroups.has(key), center: new THREE.Vector3(cx, top, cz) };
  el.classList.toggle("expanded", g.expanded);
  head.addEventListener("click", () => {
    g.expanded = !g.expanded;
    g.expanded ? expandedGroups.add(key) : expandedGroups.delete(key);
    el.classList.toggle("expanded", g.expanded);
    el.classList.remove("dot");
    delete el.dataset.w; // size changed; re-measure for declutter
    lastDeclutter = 0;
  });
  const anchor = new THREE.Object3D();
  anchor.position.set(cx, top + 0.15, cz);
  g.tag = new CSS2DObject(el);
  anchor.add(g.tag);
  world.add(anchor);
  for (const id of g.ids) itemMeshes.get(id).group = g;
  return g;
}

// Elevated three-quarter view from beside the room's long side, so items spread across the screen
// instead of lining up behind each other down the room's length.
function roomBounds(s) {
  const pts = (s.rooms || []).flatMap((r) => r.walls.flatMap((w) => [w.from, w.to]));
  if (!pts.length) return null;
  const xs = pts.map((p) => p[0]), zs = pts.map((p) => p[1]);
  return { minX: Math.min(...xs), maxX: Math.max(...xs), minZ: Math.min(...zs), maxZ: Math.max(...zs) };
}

function frameCamera(s) {
  const b = roomBounds(s);
  const box = b ? null : new THREE.Box3().setFromObject(world);
  const [minX, maxX, minZ, maxZ] = b ? [b.minX, b.maxX, b.minZ, b.maxZ] : [box.min.x, box.max.x, box.min.z, box.max.z];
  const cx = (minX + maxX) / 2, cz = (minZ + maxZ) / 2, dx = maxX - minX, dz = maxZ - minZ;
  const L = Math.max(dx, dz);
  if (dz >= dx) camera.position.set(maxX + L * 0.55, L * 0.7, cz + L * 0.12);
  else camera.position.set(cx + L * 0.12, L * 0.7, maxZ + L * 0.55);
  controls.target.set(cx, 0.5, cz);
  controls.update();
}

// CSS2DRenderer only removes a label's element when that label object itself gets a "removed" event.
// We remove the whole world group, so without this every rebuild left a frozen copy of every tag on screen.
function disposeWorld(group) {
  group.traverse((o) => {
    if (o.isCSS2DObject) o.element.remove();
    o.geometry?.dispose();
    o.material?.dispose();
  });
  scene.remove(group);
}

function render(s) {
  disposeWorld(world);
  world = new THREE.Group();
  itemMeshes.clear();
  for (const room of s.rooms || []) buildRoom(room, world);
  for (const item of s.items || []) buildItem(item, world);
  groups = clusterItems(s.items || []).map((m) => buildGroup(m, world));
  scene.add(world);
  current = s;
  if (!framed) { frameCamera(s); framed = true; }
  renderSummary(s);
}

document.getElementById("reset-view").addEventListener("click", () => current && frameCamera(current));

// ---------- camera pad: move across the floor, rotate, tilt, zoom (buttons repeat while held; keys too) ----------
// Moves are relative to where the camera looks, and slide the orbit target with the camera, so you can
// travel through the room instead of only circling its center.

const _off = new THREE.Vector3();
const _sph = new THREE.Spherical();
const _fwd = new THREE.Vector3();
const _right = new THREE.Vector3();
const UP = new THREE.Vector3(0, 1, 0);

const NAV = {
  fwd: () => move(0, 1), back: () => move(0, -1), left: () => move(-1, 0), right: () => move(1, 0),
  rotL: () => orbit(0.035, 0), rotR: () => orbit(-0.035, 0),
  tiltUp: () => orbit(0, -0.025), tiltDown: () => orbit(0, 0.025),
  in: () => zoom(0.96), out: () => zoom(1 / 0.96),
};

function move(dx, dz) {
  _fwd.subVectors(controls.target, camera.position).setY(0);
  if (_fwd.lengthSq() < 1e-6) _fwd.set(0, 0, -1);
  _fwd.normalize();
  _right.crossVectors(_fwd, UP).normalize();
  const step = Math.max(0.05, camera.position.distanceTo(controls.target) * 0.02);
  const d = _right.multiplyScalar(dx * step).add(_fwd.multiplyScalar(dz * step));
  camera.position.add(d);
  controls.target.add(d);
}

function orbit(dTheta, dPhi) {
  _off.subVectors(camera.position, controls.target);
  _sph.setFromVector3(_off);
  _sph.theta += dTheta;
  _sph.phi = Math.min(Math.PI / 2 - 0.02, Math.max(0.05, _sph.phi + dPhi)); // never under the floor
  _off.setFromSpherical(_sph);
  camera.position.copy(controls.target).add(_off);
}

function zoom(f) {
  _off.subVectors(camera.position, controls.target);
  const d = Math.min(80, Math.max(0.6, _off.length() * f));
  camera.position.copy(controls.target).add(_off.setLength(d));
}

// Held buttons and keys run their action every frame until released.
const held = new Set();
function stepNav() {
  for (const a of held) NAV[a]();
  if (held.size) lastDeclutter = 0;
}

for (const b of document.querySelectorAll("#nav [data-nav]")) {
  const a = b.dataset.nav;
  const stop = () => held.delete(a);
  b.addEventListener("pointerdown", (e) => { e.preventDefault(); b.setPointerCapture(e.pointerId); held.add(a); });
  b.addEventListener("pointerup", stop);
  b.addEventListener("pointercancel", stop);
  b.addEventListener("lostpointercapture", stop);
  b.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); NAV[a](); } });
}

const KEYS = {
  ArrowUp: "fwd", w: "fwd", ArrowDown: "back", s: "back", ArrowLeft: "left", a: "left", ArrowRight: "right", d: "right",
  q: "rotL", e: "rotR", r: "tiltUp", f: "tiltDown", "+": "in", "=": "in", "-": "out", _: "out",
};
window.addEventListener("keydown", (e) => {
  if (e.target.closest?.("input, textarea, dialog[open]") || e.ctrlKey || e.metaKey || e.altKey) return;
  if (e.key === "0") { if (current) frameCamera(current); return; }
  const a = KEYS[e.key.length === 1 ? e.key.toLowerCase() : e.key];
  if (!a) return;
  e.preventDefault();
  held.add(a);
});
window.addEventListener("keyup", (e) => { const a = KEYS[e.key.length === 1 ? e.key.toLowerCase() : e.key]; if (a) held.delete(a); });
window.addEventListener("blur", () => held.clear());

function renderSummary(s) {
  const items = s.items || [];
  const priced = items.filter((i) => i.price);
  const stolen = items.filter((i) => i.status === "stolen");
  const sum = (list, k) => list.reduce((t, i) => t + (i.price?.[k] || 0), 0);
  const total = priced.length ? ` · total ${fmtPrice({ low: sum(priced, "low"), high: sum(priced, "high") })}` : "";
  let text = `${items.length} items · ${priced.length} priced${total}`;
  if (stolen.length) text += ` · ${stolen.length} stolen (${fmtPrice({ low: sum(stolen, "low"), high: sum(stolen, "high") }) || "unpriced"})`;
  document.getElementById("summary").textContent = text;

  const fid = s.fidelity, el = document.getElementById("fidelity");
  if (fid?.single_pass && fid?.after_loop) {
    const n = fid.ground_truth_count;
    const found = (r) => (n ? `${Math.round(r * n)} of ${n}` : `${Math.round(r * 100)}%`);
    el.innerHTML = "";
    const row = (k, r) => { const d = document.createElement("div"); d.innerHTML = `<span>${k}</span><b>${found(r.recall)}</b><small>precision ${Math.round(r.precision * 100)}%</small>`; return d; };
    el.append(row("single pass", fid.single_pass), row(`after loop · ${fid.iterations} iter`, fid.after_loop));
    el.hidden = false;
  } else el.hidden = true;
}

function setHover(id, on) {
  const it = itemMeshes.get(id);
  if (it) it.mesh.material.opacity = on ? 0.9 : 0.55;
}

// Pulse the tag that's actually on screen: the item's own, or its group's.
function pulse(id) {
  const it = itemMeshes.get(id);
  if (!it) return;
  const el = it.tag.visible || !it.group ? it.tagEl : it.group.el;
  el.classList.remove("pulse");
  void el.offsetWidth;
  el.classList.add("pulse");
}

// ---------- hovering a box shows its tag; clicking a box opens its evidence ----------

const raycaster = new THREE.Raycaster();
const pointer = new THREE.Vector2();
let hoveredId = null;
let downAt = null;

function itemAt(e) {
  const r = renderer.domElement.getBoundingClientRect();
  pointer.set(((e.clientX - r.left) / r.width) * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1);
  raycaster.setFromCamera(pointer, camera);
  const hit = raycaster.intersectObjects([...itemMeshes.values()].map((v) => v.mesh), false)[0];
  return hit?.object.userData.itemId ?? null;
}

renderer.domElement.addEventListener("pointermove", (e) => {
  if (e.buttons) return; // orbiting
  const id = itemAt(e);
  if (id === hoveredId) return;
  if (hoveredId) setHover(hoveredId, false);
  hoveredId = id;
  if (id) setHover(id, true);
  renderer.domElement.style.cursor = id ? "pointer" : "";
  lastDeclutter = 0; // show/hide tags now, not on the next throttle tick
});
renderer.domElement.addEventListener("pointerdown", (e) => { downAt = [e.clientX, e.clientY]; });
renderer.domElement.addEventListener("pointerup", (e) => {
  if (!downAt || Math.hypot(e.clientX - downAt[0], e.clientY - downAt[1]) > 4) return; // that was a drag
  const id = itemAt(e);
  if (id) openEvidence(id);
});

// ---------- data ----------

let lastSceneText = "";
let view = "before"; // "after" = A's diff scene, with items[].status set
async function loadScene() {
  const res = await fetch(`/scene.json?view=${view}`, { cache: "no-store" });
  const text = await res.text();
  if (text === lastSceneText) return;
  lastSceneText = text;
  render(JSON.parse(text));
}

let reloadTimer = null;
function scheduleReload() {
  clearTimeout(reloadTimer);
  reloadTimer = setTimeout(loadScene, 300);
}

// ---------- activity panel ----------

const feed = document.getElementById("feed");
const live = document.getElementById("live");

function addEvent(ev) {
  const li = document.createElement("li");
  li.className = `ev ${ev.agent} ${ev.status || ""}`;
  const meta = document.createElement("div");
  meta.className = "meta";
  meta.innerHTML = `<span class="agent"></span><time></time>`;
  meta.querySelector(".agent").textContent = ev.agent;
  meta.querySelector("time").textContent = ev.ts || "";
  const action = document.createElement("div");
  action.className = "action";
  action.textContent = ev.action;
  li.append(meta, action);
  // Loop critiques carry payload.pair: a repo-relative REAL | RENDER composite (backslashes if written on Windows).
  const pair = ev.payload?.pair && "/" + ev.payload.pair.replace(/\\/g, "/");
  if (pair) {
    const img = document.createElement("img");
    img.className = "pair";
    img.src = pair;
    img.alt = "Real frame vs rendered scene";
    img.addEventListener("click", (e) => { e.stopPropagation(); showCatch(ev, pair); });
    img.addEventListener("error", () => img.remove());
    li.append(img);
    if (ev.agent === "loop" && ev.status === "warn") showCatch(ev, pair);
  }
  // Pricing names the item as payload.item; loop critiques/fixes name it as payload.target (may be a wall).
  const itemId = ev.payload?.item || ev.payload?.target;
  if (itemId) {
    li.dataset.item = itemId;
    li.addEventListener("click", () => openEvidence(itemId));
  }
  const atBottom = feed.scrollHeight - feed.scrollTop - feed.clientHeight < 40;
  feed.append(li);
  if (atBottom) feed.scrollTop = feed.scrollHeight;
  if (itemId) pulse(itemId);
}

function connect() {
  const es = new EventSource("/events");
  es.addEventListener("open", () => live.classList.add("on"));
  es.addEventListener("error", () => live.classList.remove("on"));
  es.addEventListener("reset", () => { feed.innerHTML = ""; });
  es.addEventListener("event", (m) => {
    let ev;
    try { ev = JSON.parse(m.data); } catch { return; }
    addEvent(ev);
    if (ev.status !== "info") scheduleReload(); // agents write scene.json, then report ok/warn
  });
}

// ---------- loop catch card: the real frame next to our render, over the 3D scene ----------

const catchCard = document.getElementById("catch");
catchCard.querySelector("button").addEventListener("click", () => { catchCard.hidden = true; });

function showCatch(ev, src, caption = "Loop caught a reconstruction error") {
  catchCard.querySelector("img").src = src;
  catchCard.querySelector("figcaption").textContent = caption;
  catchCard.querySelector("p").textContent = ev.action;
  catchCard.hidden = false;
}

// ---------- after the loss: upload, before/after, stolen list, file claim ----------

const afterEl = document.getElementById("after");
const afterStatus = document.getElementById("after-status");
const viewButtons = afterEl.querySelectorAll(".views button");
let afterState = null;

function setView(v) {
  if (v === view) return;
  view = v;
  for (const b of viewButtons) b.classList.toggle("on", b.dataset.view === v);
  lastSceneText = ""; // force a rebuild even if the file didn't change
  loadScene();
}
for (const b of viewButtons) b.addEventListener("click", () => setView(b.dataset.view));

afterEl.querySelector("input[type=file]").addEventListener("change", async (e) => {
  const file = e.target.files[0];
  e.target.value = "";
  if (!file) return;
  afterStatus.textContent = `Uploading ${file.name}…`;
  const r = await fetch("/api/after/upload", { method: "POST", headers: { "X-Filename": file.name }, body: file });
  afterStatus.textContent = r.ok ? "Comparing with the before scene. Watch the Activity panel." : (await r.json()).error;
  pollAfter();
});

document.getElementById("file-claim").addEventListener("click", async () => {
  const r = await fetch("/api/claim/start", { method: "POST" });
  if (!r.ok) afterStatus.textContent = (await r.json()).error;
  pollAfter();
});

async function pollAfter() {
  let s;
  try { s = await (await fetch("/api/after", { cache: "no-store" })).json(); } catch { return; }
  const was = afterState;
  afterState = s;
  viewButtons[1].disabled = !s.available;

  // A diff that just finished: show its result.
  if (was?.diff.state === "running" && s.diff.state === "done") setView("after");
  if (s.diff.state === "running") afterStatus.textContent = "Comparing with the before scene. Watch the Activity panel.";
  else if (s.diff.state === "error") afterStatus.textContent = `Diff failed: ${s.diff.error}`;
  else if (s.claim.state === "running") afterStatus.textContent = s.claim.live ? "Claim agent is filling the portal from a Managed Agents sandbox." : "Filling the claim portal.";
  else if (s.claim.state === "done") afterStatus.textContent = "Claim filled and waiting for your review. It has not been submitted.";
  else if (s.claim.state === "error") afterStatus.textContent = `Claim agent failed: ${s.claim.error}`;
  else if (was?.diff.state !== s.diff.state) afterStatus.textContent = "";

  renderStolen(s);
}

function renderStolen(s) {
  const box = document.getElementById("stolen");
  box.hidden = view !== "after" || !s.stolen.length;
  if (box.hidden) return;
  const list = document.getElementById("stolen-list");
  const key = JSON.stringify(s.stolen.map((i) => [i.id, i.price?.low, i.price?.high]));
  if (list.dataset.key !== key) {
    list.dataset.key = key;
    list.innerHTML = "";
    for (const it of s.stolen) {
      const li = document.createElement("li");
      const head = document.createElement("button");
      head.type = "button";
      const name = document.createElement("span");
      name.textContent = it.label;
      const price = document.createElement("b");
      price.textContent = fmtPrice(it.price) || "unpriced";
      head.append(name, price);
      head.addEventListener("click", () => openEvidence(it.id));
      head.addEventListener("pointerenter", () => setHover(it.id, true));
      head.addEventListener("pointerleave", () => setHover(it.id, false));
      li.append(head);
      if (it.evidence) {
        const src = "/" + it.evidence.replace(/\\/g, "/");
        const img = document.createElement("img");
        img.src = src;
        img.alt = `Before and after: ${it.label}`;
        img.title = it.reason || "";
        img.addEventListener("click", () => showCatch({ action: it.reason || it.label }, src, "Before | after: why this is a theft"));
        img.addEventListener("error", () => img.remove());
        li.append(img);
      }
      list.append(li);
    }
  }
  const sum = sumPrice(s.stolen);
  document.getElementById("stolen-total").textContent = `· ${s.stolen.length} item${s.stolen.length > 1 ? "s" : ""}${sum ? " · " + fmtPrice(sum) : ""}`;
  const fileBtn = document.getElementById("file-claim");
  fileBtn.disabled = s.claim.state === "running";
  fileBtn.textContent = s.claim.state === "running" ? "Filing…" : s.claim.state === "done" ? "File again" : "File claim";
  document.getElementById("portal-link").href = s.portal;
}

// ---------- evidence ----------

const dlg = document.getElementById("evidence");
const video = document.getElementById("ev-video");
video.addEventListener("error", () => { video.hidden = true; document.getElementById("ev-novideo").hidden = false; });

function openEvidence(id) {
  const item = current?.items?.find((i) => i.id === id);
  if (!item) return;
  document.getElementById("ev-title").textContent = item.label;
  const seen = (item.seen_at || []).map((t) => `${Math.floor(t / 60)}:${String(Math.floor(t % 60)).padStart(2, "0")}`).join(", ");
  const price = fmtPrice(item.price);
  document.getElementById("ev-meta").textContent =
    `${item.status || "present"} · seen at ${seen || "—"} · confidence ${Math.round((item.confidence ?? 0) * 100)}%` +
    (price ? ` · ${price} (${item.price.basis || "like kind and quality"})` : "");
  const src = document.getElementById("ev-sources");
  src.innerHTML = "";
  for (const s of item.price?.sources || []) {
    const li = document.createElement("li");
    const a = document.createElement("a");
    a.href = s; a.target = "_blank"; a.rel = "noopener"; a.textContent = s;
    li.append(a); src.append(li);
  }
  if (item.seen_at?.length && !video.hidden) {
    const seek = () => { video.currentTime = item.seen_at[0]; };
    video.readyState >= 1 ? seek() : video.addEventListener("loadedmetadata", seek, { once: true });
  }
  dlg.showModal();
}
dlg.addEventListener("close", () => video.pause());

// ---------- which tags show, and declutter ----------
// 1. A group shows one tag, unless it's expanded (its list) or the camera is close (members' own tags).
// 2. The hovered box always shows its own tag.
// 3. Of what's left, nearest tags stay full; any tag that would overlap one already placed becomes a dot
//    (hover it for the full tag). Overlap uses each tag's cached full size, so dots never flicker back.

const _v = new THREE.Vector3();
let lastDeclutter = 0;

function declutter(now) {
  if (now - lastDeclutter < 120) return;
  lastDeclutter = now;

  for (const g of groups) {
    const split = !g.expanded && camera.position.distanceTo(g.center) < SPLIT_DISTANCE;
    g.tag.visible = !split;
    for (const id of g.ids) itemMeshes.get(id).tag.visible = split || id === hoveredId;
  }
  for (const it of itemMeshes.values()) {
    if (!it.group) it.tag.visible = true;
    it.tagEl.classList.toggle("hot", it.item.id === hoveredId);
  }

  const tags = [];
  const consider = (el, worldPos, pinned) => {
    if (el.style.display === "none") return; // hidden or behind the camera
    if (!el.dataset.w && !el.classList.contains("dot")) {
      const r = el.getBoundingClientRect();
      if (r.width) { el.dataset.w = r.width; el.dataset.h = r.height; }
    }
    if (!el.dataset.w) return;
    const r = el.getBoundingClientRect();
    const w = +el.dataset.w, h = +el.dataset.h, cx = r.left + r.width / 2, cy = r.top + r.height / 2;
    tags.push({ el, pinned, box: { x: cx - w / 2, y: cy - h / 2, w, h }, dist: camera.position.distanceTo(worldPos) });
  };
  for (const it of itemMeshes.values()) if (it.tag.visible) consider(it.tagEl, it.mesh.getWorldPosition(_v), it.item.id === hoveredId);
  for (const g of groups) if (g.tag.visible) consider(g.el, g.center, g.expanded);

  // Pinned tags (hovered, expanded) are placed first so everything else yields to them.
  tags.sort((a, b) => (b.pinned - a.pinned) || (a.dist - b.dist));
  const kept = [];
  const hit = (a, b) => a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h;
  for (const t of tags) {
    const dot = !t.pinned && kept.some((k) => hit(t.box, k.box));
    t.el.classList.toggle("dot", dot);
    if (!dot) kept.push(t);
  }
}

// ---------- go ----------

function tick(now) {
  stepNav();
  controls.update();
  renderer.render(scene, camera);
  labels.render(scene, camera);
  declutter(now);
  requestAnimationFrame(tick);
}

resize();
await loadScene();
connect();
setInterval(loadScene, 5000); // safety net if an agent writes without emitting an event
pollAfter();
setInterval(pollAfter, 2000);
requestAnimationFrame(tick);
