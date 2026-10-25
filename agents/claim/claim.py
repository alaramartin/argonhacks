"""Claim agent: turns the stolen list (out/diff/stolen.json) into a filled, unsubmitted contents claim on the
insurer's portal, with every field visible in the Activity panel.

How each item is valued (see RUNS.md / research notes):
  - Replacement cost, i.e. a new item of like kind and quality. The insurer applies its own depreciation.
  - The claimed number is the MEDIAN comparable listing the pricing agent found: a real product at a real
    price, with every listing attached as documentation.
  - Ranges wider than 2x and categories that standard policies cap (jewelry, watches, furs, firearms,
    silverware, collectibles, fine art, cash) get a note asking the policyholder to confirm.

How the agent reaches the portal:
  - A Managed Agents sandbox whose network allowlist contains ONLY the portal's host.
  - The portal's bearer token is a Credentials API credential bound to that host, so the egress proxy adds it
    to every request. The token never enters the sandbox or the model's context.
  - The portal refuses submit from anything but the policyholder's own device; the agent stops at review.

Usage (from repo root):
    python agents/claim/claim.py --local                                # fill the portal directly, no agent (test / demo fallback)
    python agents/claim/claim.py --portal https://<tunnel-host>         # live: claim agent in a sandbox
"""
import argparse
import json
import os
import re
import secrets
import statistics
import sys
import time
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import env  # noqa: E402,F401  (loads .env)
from events import emit  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent.parent
STOLEN_PATH = ROOT / "out" / "diff" / "stolen.json"
PROFILE_PATH = Path(__file__).resolve().parent / "profile.sample.json"
CACHE_PATH = ROOT / "agents" / "pricing" / "cache.json"
ENV_PATH = ROOT / ".env"
LOCAL_PORTAL = "http://localhost:8090"
CREDENTIAL_ID = "pol-mock-portal"
AGENT = os.environ.get("CLAIM_AGENT", "antigravity-preview-09-2026")
MODEL = os.environ.get("CLAIM_MODEL", "gemini-3.8-flash")

# Categories standard homeowners forms cap ("special limits of liability"), or that are usually scheduled.
SPECIAL_LIMIT = re.compile(
    r"\b(jewel\w*|ring|necklace|bracelet|earrings?|watch(es)?|fur|mink|gun|rifle|pistol|firearm|silverware|"
    r"sterling|gold coin|coins?|stamps?|collectible\w*|painting|fine art|cash|currency)\b", re.I)

PROMPT = """You are a claims assistant filling in a contents insurance claim on the policyholder's behalf.

The insurer's portal API is at {base}. Authentication is handled for you by the network: do NOT add any
Authorization header or look for credentials. Use curl (or Python) for HTTP.

Endpoints:
  GET  {base}/api/form                      the fields the insurer requires
  PUT  {base}/api/claim/fields/<name>       body {{"value": "..."}}       one claim field
  POST {base}/api/claim/items               body: one item line (JSON with the item fields, plus "item_id")
  GET  {base}/api/claim                     the current draft, with its total

The claim packet below was prepared from the policyholder's verified inventory. Your job:
1. GET the form. Fill every claim field from the packet, one request per field.
2. Add every item line from the packet, one request per item, including its item_id. Use the values exactly.
3. GET the draft and check it against the packet: every field present, every line present with the right
   value, and the total equal to {total:.2f}. Fix anything that differs.
4. Do NOT submit the claim. Submitting is the policyholder's decision; the portal will refuse it anyway.
5. Finish with a short report: what you filled, the total, and anything that needs the policyholder's attention.

CLAIM PACKET:
{packet}
"""


def load_json(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default


def cache_key(label):
    return re.sub(r"\s+", " ", label.strip().lower())


def mmss(t):
    return f"{int(t) // 60}:{int(t) % 60:02d}"


def value_item(item, cache):
    """Median comparable listing, with the listings as the valuation basis and any flags as notes."""
    listings = [s for s in (cache.get(cache_key(item["label"])) or {}).get("sources", [])
                if isinstance(s, dict) and s.get("price")]
    price = item.get("price") or {}
    if listings:
        value = round(statistics.median(float(s["price"]) for s in listings), 2)
        basis = (f"Median of {len(listings)} comparable new listings (like kind and quality): "
                 + "; ".join(f"{s.get('retailer', 'retailer')} ${float(s['price']):,.2f}" for s in listings))
    elif price:
        value = round((price["low"] + price["high"]) / 2, 2)
        basis = f"Midpoint of comparable new listings ${price['low']:,.2f}–${price['high']:,.2f} (like kind and quality)"
    else:
        return None
    urls = [s["url"] for s in listings if s.get("url")] or price.get("sources", [])

    notes = []
    lo, hi = price.get("low"), price.get("high")
    if lo and hi and hi > 2 * lo:
        notes.append(f"Comparables range widely (${lo:,.0f}–${hi:,.0f}); policyholder to confirm model/tier.")
    if SPECIAL_LIMIT.search(item["label"]):
        notes.append("Category may be subject to a policy special limit; if scheduled, use the appraised amount.")

    evidence = f"Visible at {mmss(item['before_t'])} in the pre-loss walkthrough video"
    if item.get("after_t") is not None:
        evidence += f"; its spot is empty at {mmss(item['after_t'])} in the post-loss video"
    if item.get("evidence"):
        evidence += f" (BEFORE|AFTER image {item['evidence']})"
    return {
        "item_id": item["id"],
        "description": item["label"],
        "quantity": 1,
        "replacement_cost": value,
        "valuation_basis": basis,
        "evidence": evidence,
        "sources": " ".join(urls),
        "notes": " ".join(notes),
    }


def build_packet(stolen, profile, cache):
    lines = []
    for item in stolen:
        line = value_item(item, cache)
        if line is None:
            emit("claim", f"No price for \"{item['label']}\"; left off the claim for the policyholder to add", "warn",
                 {"item": item["id"]})
            continue
        lines.append(line)
        emit("claim", f"Claim value for \"{item['label']}\": ${line['replacement_cost']:,.2f} ({line['valuation_basis'].split(':')[0].lower()})",
             "warn" if line["notes"] else "info", {"item": item["id"], "value": line["replacement_cost"]})
        if line["notes"]:
            emit("claim", f"Needs review: {line['notes']}", "warn", {"item": item["id"]})
    fields = {k: v for k, v in profile.items() if v}
    return {"claim_fields": fields, "items": lines}, sum(l["replacement_cost"] * l["quantity"] for l in lines)


def portal_token():
    """The portal's bearer token lives in the gitignored .env; created on first use."""
    tok = os.environ.get("PORTAL_TOKEN")
    if not tok:
        tok = secrets.token_urlsafe(32)
        with open(ENV_PATH, "a", encoding="utf-8") as f:
            f.write(f"\nPORTAL_TOKEN={tok}\n")
        os.environ["PORTAL_TOKEN"] = tok
        print("created PORTAL_TOKEN in .env (restart the portal so it picks it up)")
    return tok


def http(method, url, body=None, token=None):
    req = urllib.request.Request(url, method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json"})
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read() or b"{}")


def fill_locally(packet, base):
    """No agent: the same requests the agent makes, from this machine. For testing and as a demo fallback."""
    for name, value in packet["claim_fields"].items():
        http("PUT", f"{base}/api/claim/fields/{name}", {"value": value})
        time.sleep(0.4)  # so the portal page and Activity panel fill visibly
    for line in packet["items"]:
        http("POST", f"{base}/api/claim/items", line)
        time.sleep(0.6)
    return http("GET", f"{base}/api/claim")


def register_credential(client, token):
    """Bearer-token credential the egress proxy injects for the portal host. Recreated so the token matches .env."""
    try:
        client.credentials.delete(id=CREDENTIAL_ID)
    except Exception:
        pass  # didn't exist yet
    client.credentials.create(id=CREDENTIAL_ID, type="bearer_token", token=token)


def fill_with_agent(packet, total, base, model):
    from google import genai

    client = genai.Client()
    host = urlparse(base).hostname
    register_credential(client, portal_token())
    emit("claim", f"Credentials API: portal token stored as \"{CREDENTIAL_ID}\", injected by the egress proxy for {host} only", "ok")
    emit("claim", f"Starting claim agent ({model}) in a Managed Agents sandbox; network allowlist: {host} only", "info")

    stream = client.interactions.create(
        agent=AGENT,
        agent_config={"type": "antigravity", "model": model},
        input=PROMPT.format(base=base, total=total, packet=json.dumps(packet, indent=2)),
        environment={"type": "remote", "network": {"allowlist": [{"domain": host, "credential": CREDENTIAL_ID}]}},
        stream=True,
    )
    interaction_id, announced = None, False
    for event in stream:
        inter = getattr(event, "interaction", None)
        interaction_id = getattr(inter, "id", None) or interaction_id
        env_id = getattr(inter, "environment_id", None) or getattr(event, "environment_id", None)
        if env_id and not announced:
            announced = True
            emit("claim", f"Sandbox provisioned (Managed Agents environment …{env_id[-6:]})", "ok", {"environment": env_id})
    final = client.interactions.get(id=interaction_id)
    for _ in range(100):
        if getattr(final, "status", None) not in ("in_progress", "queued"):
            break
        time.sleep(3)
        final = client.interactions.get(id=interaction_id)
    report = (final.output_text or "").strip()
    if report:
        emit("claim", f"Claim agent report: {report[:400]}", "info")
    return report


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stolen", type=Path, default=STOLEN_PATH)
    ap.add_argument("--profile", type=Path, default=PROFILE_PATH)
    ap.add_argument("--portal", help="public URL of the portal (tunnel) for the live agent")
    ap.add_argument("--local", action="store_true", help="fill the local portal directly, without the agent")
    ap.add_argument("--model", default=MODEL)
    args = ap.parse_args()
    if not args.local and not args.portal:
        ap.error("pass --portal https://<tunnel-host> for the live agent, or --local")

    stolen = load_json(args.stolen, [])
    profile = load_json(args.profile, {})
    cache = load_json(CACHE_PATH, {})
    emit("claim", f"Assembling contents claim: {len(stolen)} stolen item{'s' if len(stolen) != 1 else ''} from the after-video diff", "info")
    packet, total = build_packet(stolen, profile, cache)
    if not packet["items"]:
        emit("claim", "Nothing to claim", "warn")
        return
    emit("claim", f"Claim packet ready: {len(packet['items'])} lines, total ${total:,.2f} at replacement cost", "ok")

    if args.local:
        portal_token()
        draft = fill_locally(packet, LOCAL_PORTAL)
    else:
        fill_with_agent(packet, total, args.portal.rstrip("/"), args.model)
        draft = http("GET", f"{LOCAL_PORTAL}/api/claim")

    ok = abs(draft.get("total", 0) - total) < 0.01 and len(draft.get("items", [])) == len(packet["items"])
    emit("claim",
         f"Claim ready for the policyholder's review: {len(draft.get('items', []))} lines, ${draft.get('total', 0):,.2f}, NOT submitted"
         if ok else f"Portal draft doesn't match the packet (portal ${draft.get('total', 0):,.2f} vs ${total:,.2f}); check before submitting",
         "ok" if ok else "warn")


if __name__ == "__main__":
    main()
