// Viewer server: static UI, scene.json, before-video, and a live SSE tail of events.jsonl.
// Zero dependencies. Run from repo root: node viewer/server.js [--replay]
const http = require("http");
const fs = require("fs");
const path = require("path");

const ROOT = path.resolve(__dirname, "..");
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

function scenePath() {
  return fs.existsSync(SCENE_PATH) ? SCENE_PATH : SCENE_FALLBACK;
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
    res.writeHead(200, { "Content-Type": "application/json", "Cache-Control": "no-store", "X-Scene-Source": path.relative(ROOT, scenePath()) });
    return fs.createReadStream(scenePath()).pipe(res);
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
}).listen(PORT, () => {
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
