// Viewer server: static UI, scene.json, before-video, and a live SSE tail of events.jsonl.
// Part 2: upload an after-video (runs A's diff), view the after scene, and start the claim agent.
// Zero dependencies. Listens on 127.0.0.1 only, since it can start processes.
// Run from repo root: node viewer/server.js [--replay]
const http = require("http");
const fs = require("fs");
const path = require("path");
const { spawn } = require("child_process");

const ROOT = path.resolve(__dirname, "..");

// Repo-root .env (gitignored): PORTAL_PUBLIC_URL makes "File claim" run the live agent through the tunnel.
const envFile = path.join(ROOT, ".env");
if (fs.existsSync(envFile)) for (const line of fs.readFileSync(envFile, "utf8").split(/\r?\n/)) {
  const m = /^\s*([A-Z0-9_]+)\s*=\s*(.*?)\s*$/.exec(line);
  if (m && !(m[1] in process.env)) process.env[m[1]] = m[2].replace(/^["']|["']$/g, "");
}
const PUBLIC = path.join(__dirname, "public");
const PORT = Number(process.env.PORT || 8080);
const OUT = path.join(ROOT, "out"); // A's pipeline writes here (pipeline/orchestrator.py --out)

// Real scene from A once it exists, otherwise the fixture.
const SCENE_PATH = process.env.SCENE_PATH || path.join(OUT, "scene.json");
const SCENE_FALLBACK = path.join(ROOT, "fixtures", "scene.sample.json");
const EVENTS_PATH = process.env.POL_EVENTS || path.join(OUT, "events.jsonl");
const VIDEO_PATH = process.env.VIDEO_PATH || path.join(ROOT, "fixtures", "before.mp4");

fs.mkdirSync(path.dirname(EVENTS_PATH), { recursive: true });
if (!fs.existsSync(EVENTS_PATH)) fs.writeFileSync(EVENTS_PATH, "");

const MIME = { ".html": "text/html", ".js": "text/javascript", ".css": "text/css", ".json": "application/json", ".mp4": "video/mp4", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png" };

function scenePath(view) {
  if (view === "after" && afterIsFresh()) return AFTER_SCENE;
  return fs.existsSync(SCENE_PATH) ? SCENE_PATH : SCENE_FALLBACK;
}

// --- Part 2 jobs: A's diff on an uploaded after-video, then B's claim agent. One of each at a time. ---
const DIFF_OUT = path.join(OUT, "diff");
const AFTER_SCENE = path.join(DIFF_OUT, "scene.json");
const STOLEN = path.join(DIFF_OUT, "stolen.json");
const jobs = { diff: { state: "idle" }, claim: { state: "idle" } };

// Only show after-video results this viewer produced (an upload since it started), so a demo never opens with a
// stolen list left over from an earlier run. KEEP_AFTER=1 shows earlier results, e.g. after a restart mid-demo.
const STARTED = Date.now();
function afterIsFresh() {
  if (!fs.existsSync(AFTER_SCENE)) return false;
  return Boolean(process.env.KEEP_AFTER) || fs.statSync(AFTER_SCENE).mtimeMs > STARTED;
}

function runJob(name, cmd, args, opts) {
  const job = (jobs[name] = { state: "running", started: Date.now(), log: "" });
  const child = spawn(cmd, args, { ...opts, env: { ...process.env, POL_EVENTS: EVENTS_PATH, ...(opts.env || {}) } });
  const keep = (d) => { job.log = (job.log + d.toString()).slice(-20000); };
  child.stdout.on("data", keep);
  child.stderr.on("data", keep);
  child.on("error", (e) => { job.state = "error"; job.error = e.message; });
  child.on("close", (code) => {
    if (job.state === "error") return;
    job.state = code === 0 ? "done" : "error";
    if (code !== 0) job.error = `exited with ${code}: ${job.log.trim().split("\n").slice(-3).join(" | ")}`;
    const last = job.log.trim().split("\n").pop(); // diff.py ends with a one-line JSON summary
    try { job.summary = JSON.parse(last); } catch {}
  });
}

function startDiff(file) {
  // diff.py converts phone .MOV to 720p itself and streams `diff` events into the panel's file.
  runJob("diff", "uv", ["run", "python", "../diff/diff.py", "--fast", "--after", file, "--events", EVENTS_PATH], { cwd: path.join(ROOT, "pipeline") });
}

function startClaim() {
  // Live agent through the tunnel when PORTAL_PUBLIC_URL is set, otherwise fill the local portal directly.
  const live = process.env.PORTAL_PUBLIC_URL;
  runJob("claim", process.env.PYTHON || "python", ["agents/claim/claim.py", ...(live ? ["--portal", live] : ["--local"])], { cwd: ROOT });
}

function receiveUpload(req, res) {
  const name = path.basename(String(req.headers["x-filename"] || "after.mp4")).replace(/[^\w.\-]/g, "_");
  if (!/\.(mp4|mov|m4v)$/i.test(name)) return sendJson(res, 400, { error: "upload an .mp4 or .mov video" });
  if (jobs.diff.state === "running") return sendJson(res, 409, { error: "a diff is already running" });
  const dir = path.join(DIFF_OUT, "uploads");
  fs.mkdirSync(dir, { recursive: true });
  const dest = path.join(dir, `${Date.now()}_${name}`);
  const out = fs.createWriteStream(dest);
  req.pipe(out);
  out.on("finish", () => { startDiff(dest); sendJson(res, 202, { ok: true, file: path.relative(ROOT, dest) }); });
  out.on("error", (e) => sendJson(res, 500, { error: e.message }));
}

function sendJson(res, code, body) {
  res.writeHead(code, { "Content-Type": "application/json", "Cache-Control": "no-store" });
  res.end(JSON.stringify(body));
}

function readJson(p, fallback) {
  try { return JSON.parse(fs.readFileSync(p, "utf8")); } catch { return fallback; }
}

// --- SSE: every client gets the full history, then new lines as they are appended ---
const clients = new Set();
let offset = 0;
let partial = "";

function readNewLines() {
  const size = fs.statSync(EVENTS_PATH).size;
  if (size < offset) { offset = 0; partial = ""; broadcast("reset", "{}"); } // truncated
  if (size === offset) return;
  const fd = fs.openSync(EVENTS_PATH, "r");
  const buf = Buffer.alloc(size - offset);
  fs.readSync(fd, buf, 0, buf.length, offset);
  fs.closeSync(fd);
  offset = size;
  const lines = (partial + buf.toString("utf8")).split("\n");
  partial = lines.pop();
  for (const line of lines) if (line.trim()) broadcast("event", line.trim());
}

function broadcast(type, data) {
  for (const res of clients) res.write(`event: ${type}\ndata: ${data}\n\n`);
}

function serveEvents(req, res) {
  res.writeHead(200, { "Content-Type": "text/event-stream", "Cache-Control": "no-cache", Connection: "keep-alive" });
  const history = fs.readFileSync(EVENTS_PATH).subarray(0, offset).toString("utf8").split("\n").filter((l) => l.trim());
  for (const line of history) res.write(`event: event\ndata: ${line.trim()}\n\n`);
  clients.add(res);
  req.on("close", () => clients.delete(res));
}

setInterval(readNewLines, 250);
setInterval(() => broadcast("ping", "{}"), 15000);
offset = fs.statSync(EVENTS_PATH).size; // history is sent per-client on connect

// --- video with Range support so the evidence player can seek ---
function serveVideo(req, res) {
  if (!fs.existsSync(VIDEO_PATH)) { res.writeHead(404); return res.end("no video"); }
  const size = fs.statSync(VIDEO_PATH).size;
  const m = /bytes=(\d*)-(\d*)/.exec(req.headers.range || "");
  if (!m) {
    res.writeHead(200, { "Content-Type": "video/mp4", "Content-Length": size, "Accept-Ranges": "bytes" });
    return fs.createReadStream(VIDEO_PATH).pipe(res);
  }
  const start = m[1] ? Number(m[1]) : 0;
  const end = m[2] ? Number(m[2]) : size - 1;
  res.writeHead(206, { "Content-Type": "video/mp4", "Content-Range": `bytes ${start}-${end}/${size}`, "Content-Length": end - start + 1, "Accept-Ranges": "bytes" });
  fs.createReadStream(VIDEO_PATH, { start, end }).pipe(res);
}

http.createServer((req, res) => {
  const url = new URL(req.url, "http://x");
  if (url.pathname === "/events") return serveEvents(req, res);
  if (url.pathname === "/scene.json") {
    const p = scenePath(url.searchParams.get("view"));
    res.writeHead(200, { "Content-Type": "application/json", "Cache-Control": "no-store", "X-Scene-Source": path.relative(ROOT, p) });
    return fs.createReadStream(p).pipe(res);
  }
  if (url.pathname === "/api/after" && req.method === "GET") {
    return sendJson(res, 200, {
      available: afterIsFresh(),
      stolen: afterIsFresh() ? readJson(STOLEN, []) : [],
      diff: { state: jobs.diff.state, error: jobs.diff.error, summary: jobs.diff.summary },
      claim: { state: jobs.claim.state, error: jobs.claim.error, live: Boolean(process.env.PORTAL_PUBLIC_URL) },
      portal: "http://localhost:" + (process.env.PORTAL_PORT || 8090),
    });
  }
  if (url.pathname === "/api/after/upload" && req.method === "POST") return receiveUpload(req, res);
  if (url.pathname === "/api/claim/start" && req.method === "POST") {
    if (jobs.claim.state === "running") return sendJson(res, 409, { error: "the claim agent is already running" });
    if (!readJson(STOLEN, []).length) return sendJson(res, 400, { error: "no missing items to claim" });
    startClaim();
    return sendJson(res, 202, { ok: true });
  }
  if (url.pathname === "/video") return serveVideo(req, res);
  // Loop evidence images (REAL | RENDER composites) the pipeline writes under out/, e.g. /out/loop/iter1_kf3.jpg
  if (url.pathname.startsWith("/out/")) {
    const file = path.join(OUT, path.normalize(decodeURIComponent(url.pathname.slice(5))));
    if (!file.startsWith(OUT + path.sep) || !fs.existsSync(file)) { res.writeHead(404); return res.end("not found"); }
    res.writeHead(200, { "Content-Type": MIME[path.extname(file).toLowerCase()] || "application/octet-stream" });
    return fs.createReadStream(file).pipe(res);
  }

  const file = path.join(PUBLIC, url.pathname === "/" ? "index.html" : path.normalize(url.pathname));
  if (!file.startsWith(PUBLIC) || !fs.existsSync(file) || fs.statSync(file).isDirectory()) { res.writeHead(404); return res.end("not found"); }
  res.writeHead(200, { "Content-Type": MIME[path.extname(file)] || "application/octet-stream" });
  fs.createReadStream(file).pipe(res);
}).listen(PORT, "127.0.0.1", () => {
  console.log(`viewer: http://localhost:${PORT}`);
  console.log(`  scene:  ${path.relative(ROOT, scenePath())}`);
  console.log(`  events: ${path.relative(ROOT, EVENTS_PATH)}`);
});

// --replay: append the sample events one per second so the Activity panel can be exercised without agents.
if (process.argv.includes("--replay")) {
  const sample = fs.readFileSync(path.join(ROOT, "fixtures", "events.sample.jsonl"), "utf8").split("\n").filter((l) => l.trim());
  sample.forEach((line, i) => setTimeout(() => {
    const ev = JSON.parse(line);
    ev.ts = new Date().toTimeString().slice(0, 8);
    fs.appendFileSync(EVENTS_PATH, JSON.stringify(ev) + "\n");
  }, 1000 * (i + 1)));
}
