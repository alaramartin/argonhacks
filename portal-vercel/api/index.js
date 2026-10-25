// Mock Mutual, hosted: the same demo claims portal as agents/claim/portal/server.js, on Vercel so it stays up
// with the laptop closed and needs no tunnel. Not a real insurer.
//
// Two audiences, told apart by bearer token instead of "came through the tunnel":
//  - The claim agent in a Managed Agents sandbox. Its PORTAL_TOKEN is injected by the sandbox's egress proxy
//    (Credentials API), so the model never sees it. It can read the form and the draft, fill fields and add
//    lines. It can't submit, reset or attach photos.
//  - The policyholder's device. POLICYHOLDER_TOKEN lives only there (repo-root .env); B's local server.js uses
//    it to show the review page, upload evidence photos and submit.
//
// Every field the portal accepts is stored as a `claim` event; the local server copies them into events.jsonl
// so the Activity panel shows what the portal actually received.
const { Redis } = require("@upstash/redis");

const redis = new Redis({
  url: process.env.KV_REST_API_URL || process.env.UPSTASH_REDIS_REST_URL,
  token: process.env.KV_REST_API_TOKEN || process.env.UPSTASH_REDIS_REST_TOKEN,
});
const AGENT_TOKEN = process.env.PORTAL_TOKEN;
const HOLDER_TOKEN = process.env.POLICYHOLDER_TOKEN;

// Same form as the local portal.
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

const fresh = () => ({ fields: {}, items: [], status: "draft", updated: null });
async function load() { return (await redis.get("claim")) || fresh(); }
async function save(claim) { claim.updated = new Date().toISOString(); await redis.set("claim", claim); }

async function emit(action, status = "info", payload) {
  const ev = { ts: new Date().toLocaleTimeString("en-GB", { timeZone: "America/Los_Angeles", hour12: false }), agent: "claim", action, status };
  if (payload) ev.payload = payload;
  await redis.rpush("events", JSON.stringify(ev));
}

const money = (n) => "$" + Number(n).toLocaleString("en-US", { maximumFractionDigits: 2 });
const short = (v) => { const s = String(v); return s.length > 70 ? s.slice(0, 70) + "…" : s; };
const total = (c) => c.items.reduce((t, i) => t + Number(i.replacement_cost || 0) * Number(i.quantity || 1), 0);

function who(req) {
  const auth = req.headers.authorization || "";
  if (HOLDER_TOKEN && auth === `Bearer ${HOLDER_TOKEN}`) return "holder";
  if (AGENT_TOKEN && auth === `Bearer ${AGENT_TOKEN}`) return "agent";
  return null;
}

function send(res, code, body) {
  res.statusCode = code;
  res.setHeader("Content-Type", "application/json");
  res.setHeader("Cache-Control", "no-store");
  res.end(JSON.stringify(body));
}

function rawBody(req) {
  return new Promise((resolve, reject) => {
    const chunks = [];
    req.on("data", (c) => chunks.push(c));
    req.on("end", () => resolve(Buffer.concat(chunks)));
    req.on("error", reject);
  });
}
async function jsonBody(req) {
  const buf = await rawBody(req);
  try { return buf.length ? JSON.parse(buf.toString("utf8")) : {}; } catch { return null; }
}

const INFO_PAGE = `<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Mock Mutual</title>
<body style="font:16px/1.5 system-ui,sans-serif;max-width:560px;margin:15vh auto;padding:0 16px;color:#1d1d1b;background:#fff">
<h1 style="color:#1f5f4a">Mock Mutual claims API</h1>
<p>This address is for authorized claim assistants only. Every request needs credentials, which are added by
the assistant's network, never typed or seen by the assistant.</p>
<p>Policyholders review and submit their claim on their own device.</p>
<p style="color:#6b6a64;font-size:13px">Demo portal for a hackathon project. Not a real insurer.</p></body>`;

module.exports = async (req, res) => {
  const url = new URL(req.url, "http://x");
  const p = url.pathname;

  if (p === "/" && req.method === "GET") {
    res.statusCode = 200;
    res.setHeader("Content-Type", "text/html; charset=utf-8");
    return res.end(INFO_PAGE);
  }

  const role = who(req);
  if (!role) {
    if (p.startsWith("/api/") || p.startsWith("/attachments/")) await emit("Portal rejected an unauthenticated request", "warn", { path: p });
    return send(res, 401, { error: "missing or invalid credentials" });
  }

  if (p.startsWith("/attachments/") && req.method === "GET") {
    const id = p.slice("/attachments/".length).replace(/\.jpg$/, "").replace(/[^\w\-]/g, "");
    const b64 = await redis.get(`attachment:${id}`);
    if (!b64) return send(res, 404, { error: "not found" });
    res.statusCode = 200;
    res.setHeader("Content-Type", "image/jpeg");
    return res.end(Buffer.from(b64, "base64"));
  }

  if (p === "/api/form" && req.method === "GET") return send(res, 200, FORM);
  if (p === "/api/claim" && req.method === "GET") { const c = await load(); return send(res, 200, { ...c, total: total(c) }); }

  if (p === "/api/events" && req.method === "GET") {
    if (role !== "holder") return send(res, 403, { error: "forbidden" });
    const since = Math.max(0, Number(url.searchParams.get("since") || 0));
    const list = await redis.lrange("events", since, -1);
    return send(res, 200, { since, events: list.map((e) => (typeof e === "string" ? JSON.parse(e) : e)) });
  }

  if (p.startsWith("/api/claim/fields/") && req.method === "PUT") {
    const name = decodeURIComponent(p.slice("/api/claim/fields/".length));
    const field = FORM.claim_fields.find((f) => f.name === name);
    const body = await jsonBody(req);
    if (!field) return send(res, 400, { error: `unknown field ${name}`, fields: FORM.claim_fields.map((f) => f.name) });
    if (!body || body.value === undefined || body.value === "") return send(res, 400, { error: 'send {"value": ...}' });
    if (field.options && !field.options.includes(body.value)) return send(res, 400, { error: `value must be one of ${field.options}` });
    const claim = await load();
    if (claim.status !== "draft") return send(res, 409, { error: "claim already submitted" });
    claim.fields[name] = String(body.value);
    await save(claim);
    await emit(`Filling claim field: ${name} = "${short(body.value)}"`, "ok", { field: name });
    return send(res, 200, { ok: true, field: name });
  }

  if (p === "/api/claim/items" && req.method === "POST") {
    const body = await jsonBody(req);
    if (!body) return send(res, 400, { error: "invalid JSON" });
    const missing = FORM.item_fields.filter((f) => f.required && (body[f.name] === undefined || body[f.name] === "")).map((f) => f.name);
    if (missing.length) return send(res, 400, { error: `missing ${missing.join(", ")}` });
    if (!(Number(body.replacement_cost) > 0)) return send(res, 400, { error: "replacement_cost must be a positive number" });
    const claim = await load();
    if (claim.status !== "draft") return send(res, 409, { error: "claim already submitted" });
    const row = Object.fromEntries(FORM.item_fields.map((f) => [f.name, body[f.name] ?? ""]));
    if (body.item_id) row.item_id = String(body.item_id).replace(/[^\w\-]/g, "");
    if (row.item_id && (await redis.exists(`attachment:${row.item_id}`))) row.attachment = `/attachments/${row.item_id}.jpg`;
    // Re-sending the same item replaces its line instead of claiming it twice.
    const dup = row.item_id ? claim.items.findIndex((i) => i.item_id === row.item_id) : -1;
    if (dup >= 0) claim.items[dup] = row; else claim.items.push(row);
    await save(claim);
    await emit(`Adding claim line ${claim.items.length}: ${short(row.description)} × ${row.quantity} @ ${money(row.replacement_cost)}`, "ok",
      row.item_id ? { item: row.item_id, value: Number(row.replacement_cost) } : { value: Number(row.replacement_cost) });
    return send(res, 200, { ok: true, line: claim.items.length, total: total(claim) });
  }

  // Submitting is the policyholder's decision; the agent's token can't do it.
  if (p === "/api/claim/submit" && req.method === "POST") {
    if (role !== "holder") {
      await emit("Portal refused submit from the agent: only the policyholder can submit", "warn");
      return send(res, 403, { error: "only the policyholder can submit this claim; it is ready for their review" });
    }
    const claim = await load();
    const missing = FORM.claim_fields.filter((f) => f.required && !claim.fields[f.name]).map((f) => f.name);
    if (missing.length || !claim.items.length) return send(res, 400, { error: `incomplete: ${[...missing, ...(claim.items.length ? [] : ["items"])].join(", ")}` });
    claim.status = "submitted";
    await save(claim);
    await emit(`Claim submitted by the policyholder: ${claim.items.length} items, ${money(total(claim))}`, "ok");
    return send(res, 200, { ok: true });
  }

  if (p.startsWith("/api/claim/attachments/") && req.method === "POST") {
    if (role !== "holder") return send(res, 403, { error: "attachments are uploaded by the policyholder's device" });
    const id = p.slice("/api/claim/attachments/".length).replace(/[^\w\-]/g, "");
    if (!id) return send(res, 400, { error: "item id required" });
    const buf = await rawBody(req);
    if (!buf.length || buf.length > 700_000) return send(res, 400, { error: "send a JPEG under 700 KB" });
    await redis.set(`attachment:${id}`, buf.toString("base64"));
    await emit(`Evidence photo attached for ${id}`, "ok", { item: id });
    return send(res, 200, { ok: true, url: `/attachments/${id}.jpg` });
  }

  if (p === "/api/claim/reset" && req.method === "POST") {
    if (role !== "holder") return send(res, 403, { error: "forbidden" });
    await save(fresh());
    await emit("Claim portal reset to an empty draft", "info");
    return send(res, 200, { ok: true });
  }

  send(res, 404, { error: "not found" });
};
