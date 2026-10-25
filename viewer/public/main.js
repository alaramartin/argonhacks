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
    let cursor = 0;
    const pieces = [];
    for (const o of ops) {
      const [y0, y1] = OPENING_Y[o.type] || OPENING_Y.door;
      pieces.push([cursor, o.offset, 0, H]);
      pieces.push([o.offset, o.offset + o.width, 0, y0]);
      pieces.push([o.offset, o.offset + o.width, y1, H]);
      cursor = o.offset + o.width;
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
  mesh.add(new THREE.LineSegments(new THREE.EdgesGeometry(geo), new THREE.LineBasicMaterial({ color })));
  const tagEl = makeTag(item);
  const tag = new CSS2DObject(tagEl);
  tag.position.set(0, h / 2 + 0.12, 0);
  mesh.add(tag);
  group.add(mesh);
  itemMeshes.set(item.id, { mesh, tagEl });
}

function frameCamera() {
  const box = new THREE.Box3().setFromObject(world);
  const c = box.getCenter(new THREE.Vector3());
  const size = box.getSize(new THREE.Vector3()).length();
  camera.position.set(c.x + size * 0.55, size * 0.75, c.z + size * 0.85);
  controls.target.set(c.x, 0.6, c.z);
  controls.update();
}

function render(s) {
  scene.remove(world);
  world = new THREE.Group();
  itemMeshes.clear();
  for (const room of s.rooms || []) buildRoom(room, world);
  for (const item of s.items || []) buildItem(item, world);
  scene.add(world);
  if (!framed) { frameCamera(); framed = true; }
  current = s;
  renderSummary(s);
}

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

function pulse(id) {
  const it = itemMeshes.get(id);
  if (!it) return;
  it.tagEl.classList.remove("pulse");
  void it.tagEl.offsetWidth;
  it.tagEl.classList.add("pulse");
}

// ---------- data ----------

let lastSceneText = "";
async function loadScene() {
  const res = await fetch("/scene.json", { cache: "no-store" });
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

// ---------- go ----------

function tick() {
  controls.update();
  renderer.render(scene, camera);
  labels.render(scene, camera);
  requestAnimationFrame(tick);
}

resize();
await loadScene();
connect();
setInterval(loadScene, 5000); // safety net if an agent writes without emitting an event
tick();
