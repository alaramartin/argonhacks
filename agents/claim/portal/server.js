// Mock Mutual: a stand-in insurer claims portal for the demo. Not a real insurer.
//
// Two audiences:
//  - The claim agent, which reaches us from a Managed Agents sandbox through a public tunnel. Every API
//    request must carry PORTAL_TOKEN as a bearer token. The agent never has it: the sandbox's egress proxy
//    injects it (Credentials API), so the model can't see, print or leak it.
//  - The policyholder, on this laptop at http://localhost:8090, who watches the draft fill in and is the
//    only one who can submit. Submitting is refused for any request that came through the tunnel.
//
// Every field the portal accepts is appended to events.jsonl as a `claim` event, so the Activity panel
// shows what the portal actually received, not what the agent says it did.
//
// Run from repo root: node agents/claim/portal/server.js
const http = require("http");
const fs = require("fs");
const path = require("path");

const ROOT = path.resolve(__dirname, "..", "..", "..");
const PORT = Number(process.env.PORTAL_PORT || 8090);
const EVENTS_PATH = process.env.POL_EVENTS || path.join(ROOT, "out", "events.jsonl");
const CLAIM_PATH = path.join(ROOT, "out", "claim", "claim.json");
const ATTACH_DIR = path.join(ROOT, "out", "claim", "attachments");

function loadEnv() {
  const p = path.join(ROOT, ".env");
  if (!fs.existsSync(p)) return;
  for (const line of fs.readFileSync(p, "utf8").split(/\r?\n/)) {
    const m = /^\s*([A-Z0-9_]+)\s*=\s*(.*)\s*$/.exec(line);
    if (m && !(m[1] in process.env)) process.env[m[1]] = m[2].replace(/^["']|["']$/g, "");
  }
}
loadEnv();
const TOKEN = process.env.PORTAL_TOKEN;
if (!TOKEN) { console.error("PORTAL_TOKEN missing from .env (agents/claim/claim.py creates it)"); process.exit(1); }

// What an insurer's contents claim asks for. The agent reads this from GET /api/form.
const FORM = {
  insurer: "Mock Mutual (demo portal, not a real insurer)",
  claim_fields: [
    { name: "policy_number", label: "Policy number", required: true },
    { name: "claimant_name", label: "Policyholder name", required: true },
    { name: "date_of_loss", label: "Date of loss (YYYY-MM-DD)", required: true },
    { name: "loss_type", label: "Type of loss", required: true, options: ["theft", "fire", "water", "wind", "other"] },
    { name: "incident_description", label: "What happened", required: true },
    { name: "police_report_number", label: "Police report number (theft)", required: false },
  ],
  item_fields: [
    { name: "description", label: "Item description", required: true },
    { name: "quantity", label: "Quantity", required: true },
    { name: "replacement_cost", label: "Replacement cost, USD (new item of like kind and quality)", required: true },
    { name: "valuation_basis", label: "How the value was determined", required: true },
    { name: "evidence", label: "Proof of ownership / evidence", required: true },
    { name: "sources", label: "Comparable listings (URLs)", required: false },
    { name: "notes", label: "Notes for the adjuster", required: false },
  ],
};

let claim = fresh();
function fresh() { return { fields: {}, items: [], status: "draft", updated: null }; }
function save() {
  claim.updated = new Date().toISOString();
  fs.mkdirSync(path.dirname(CLAIM_PATH), { recursive: true });
  fs.writeFileSync(CLAIM_PATH, JSON.stringify(claim, null, 2));
}
if (fs.existsSync(CLAIM_PATH)) try { claim = JSON.parse(fs.readFileSync(CLAIM_PATH, "utf8")); } catch {}

function emit(action, status = "info", payload) {
  const ev = { ts: new Date().toTimeString().slice(0, 8), agent: "claim", action, status };
  if (payload) ev.payload = payload;
  fs.mkdirSync(path.dirname(EVENTS_PATH), { recursive: true });
  fs.appendFileSync(EVENTS_PATH, JSON.stringify(ev) + "\n");
  console.log(`[${ev.ts}] claim  ${action}`);
}

const money = (n) => "$" + Number(n).toLocaleString("en-US", { maximumFractionDigits: 2 });
const short = (v) => { const s = String(v); return s.length > 70 ? s.slice(0, 70) + "…" : s; };
const total = () => claim.items.reduce((t, i) => t + Number(i.replacement_cost || 0) * Number(i.quantity || 1), 0);

// cloudflared connects from 127.0.0.1 too, so "came through the tunnel" is detected by its headers.
const viaTunnel = (req) => Boolean(req.headers["cf-connecting-ip"] || req.headers["x-forwarded-for"]);
const authorized = (req) => req.headers.authorization === `Bearer ${TOKEN}`;

function send(res, code, body) {
  res.writeHead(code, { "Content-Type": "application/json", "Cache-Control": "no-store" });
  res.end(JSON.stringify(body));
}
function readBody(req) {
  return new Promise((resolve) => {
    let data = "";
    req.on("data", (c) => { data += c; if (data.length > 1e6) req.destroy(); });
    req.on("end", () => { try { resolve(data ? JSON.parse(data) : {}); } catch { resolve(null); } });
  });
}

http.createServer(async (req, res) => {
  const url = new URL(req.url, "http://x");
  const p = url.pathname;

  // Policyholder page: this laptop only.
  if (p === "/" && req.method === "GET") {
    if (viaTunnel(req)) return send(res, 403, { error: "the policyholder page is only available on the policyholder's device" });
    res.writeHead(200, { "Content-Type": "text/html" });
    return fs.createReadStream(path.join(__dirname, "index.html")).pipe(res);
  }

  if (p.startsWith("/attachments/") && req.method === "GET") {
    if (viaTunnel(req) && !authorized(req)) return send(res, 401, { error: "missing or invalid credentials" });
    const file = path.join(ATTACH_DIR, path.basename(p));
    if (!fs.existsSync(file)) return send(res, 404, { error: "not found" });
    res.writeHead(200, { "Content-Type": "image/jpeg" });
    return fs.createReadStream(file).pipe(res);
  }

  if (!p.startsWith("/api/")) return send(res, 404, { error: "not found" });

  // The local page reads the draft and submits without a token; anything through the tunnel needs one.
  if (viaTunnel(req) && !authorized(req)) {
    emit("Portal rejected an unauthenticated request", "warn", { path: p });
    return send(res, 401, { error: "missing or invalid credentials" });
  }

  if (p === "/api/form" && req.method === "GET") return send(res, 200, FORM);
  if (p === "/api/claim" && req.method === "GET") return send(res, 200, { ...claim, total: total() });

  if (p.startsWith("/api/claim/fields/") && req.method === "PUT") {
    const name = decodeURIComponent(p.slice("/api/claim/fields/".length));
    const field = FORM.claim_fields.find((f) => f.name === name);
    const body = await readBody(req);
    if (!field) return send(res, 400, { error: `unknown field ${name}`, fields: FORM.claim_fields.map((f) => f.name) });
    if (!body || body.value === undefined || body.value === "") return send(res, 400, { error: 'send {"value": ...}' });
    if (field.options && !field.options.includes(body.value)) return send(res, 400, { error: `value must be one of ${field.options}` });
    if (claim.status !== "draft") return send(res, 409, { error: "claim already submitted" });
    claim.fields[name] = String(body.value);
    save();
    emit(`Filling claim field: ${name} = "${short(body.value)}"`, "ok", { field: name });
    return send(res, 200, { ok: true, field: name });
  }

  if (p === "/api/claim/items" && req.method === "POST") {
    const body = await readBody(req);
    if (!body) return send(res, 400, { error: "invalid JSON" });
    const missing = FORM.item_fields.filter((f) => f.required && (body[f.name] === undefined || body[f.name] === "")).map((f) => f.name);
    if (missing.length) return send(res, 400, { error: `missing ${missing.join(", ")}` });
    if (!(Number(body.replacement_cost) > 0)) return send(res, 400, { error: "replacement_cost must be a positive number" });
    if (claim.status !== "draft") return send(res, 409, { error: "claim already submitted" });
    const row = Object.fromEntries(FORM.item_fields.map((f) => [f.name, body[f.name] ?? ""]));
    if (body.item_id) row.item_id = String(body.item_id).replace(/[^\w\-]/g, "");
    if (row.item_id && fs.existsSync(path.join(ATTACH_DIR, `${row.item_id}.jpg`))) row.attachment = `/attachments/${row.item_id}.jpg`;
    // Re-sending the same item replaces its line instead of claiming it twice.
    const dup = row.item_id ? claim.items.findIndex((i) => i.item_id === row.item_id) : -1;
    if (dup >= 0) claim.items[dup] = row; else claim.items.push(row);
    save();
    emit(`Adding claim line ${claim.items.length}: ${short(row.description)} × ${row.quantity} @ ${money(row.replacement_cost)}`, "ok",
      row.item_id ? { item: row.item_id, value: Number(row.replacement_cost) } : { value: Number(row.replacement_cost) });
    return send(res, 200, { ok: true, line: claim.items.length, total: total() });
  }

  // Submitting is the policyholder's decision. Nothing that arrives through the tunnel can do it.
  if (p === "/api/claim/submit" && req.method === "POST") {
    if (viaTunnel(req)) {
      emit("Portal refused submit from the agent: only the policyholder can submit", "warn");
      return send(res, 403, { error: "only the policyholder can submit this claim; it is ready for their review" });
    }
    const missing = FORM.claim_fields.filter((f) => f.required && !claim.fields[f.name]).map((f) => f.name);
    if (missing.length || !claim.items.length) return send(res, 400, { error: `incomplete: ${[...missing, ...(claim.items.length ? [] : ["items"])].join(", ")}` });
    claim.status = "submitted";
    save();
    emit(`Claim submitted by the policyholder: ${claim.items.length} items, ${money(total())}`, "ok");
    return send(res, 200, { ok: true });
  }

  // Evidence photos: uploaded from this device by claim.py, attached to the claim line with the same item_id.
  if (p.startsWith("/api/claim/attachments/") && req.method === "POST") {
    if (viaTunnel(req)) return send(res, 403, { error: "attachments are uploaded by the policyholder's device" });
    const id = p.slice("/api/claim/attachments/".length).replace(/[^\w\-]/g, "");
    if (!id) return send(res, 400, { error: "item id required" });
    fs.mkdirSync(ATTACH_DIR, { recursive: true });
    const out = fs.createWriteStream(path.join(ATTACH_DIR, `${id}.jpg`));
    req.pipe(out);
    out.on("finish", () => {
      emit(`Evidence photo attached for ${id}`, "ok", { item: id });
      send(res, 200, { ok: true, url: `/attachments/${id}.jpg` });
    });
    return;
  }

  if (p === "/api/claim/reset" && req.method === "POST") {
    if (viaTunnel(req)) return send(res, 403, { error: "forbidden" });
    claim = fresh();
    save();
    emit("Claim portal reset to an empty draft", "info");
    return send(res, 200, { ok: true });
  }

  send(res, 404, { error: "not found" });
  // Bound to this device only: anything not via the tunnel is treated as the policyholder, so the LAN must not reach it.
}).listen(PORT, "127.0.0.1", () => console.log(`Mock Mutual portal: http://127.0.0.1:${PORT}  (events → ${path.relative(ROOT, EVENTS_PATH)})`));
