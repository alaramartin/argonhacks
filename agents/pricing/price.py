"""Pricing agent: for each item in scene.json, a Managed Agents sandbox browses retailers for a
like-kind-and-quality price range, then we write items[].price and emit visible `pricing` events.

Usage (from repo root):
    python agents/pricing/price.py                 # price every unpriced item, live
    python agents/pricing/price.py --cache-only    # demo-safe: replay cached prices only
    python agents/pricing/price.py --refresh --only item_003
"""
import argparse
import json
import os
import re
import queue
import shutil
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import env  # noqa: E402,F401  (loads .env so GEMINI_API_KEY reaches genai.Client)
from events import emit  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_SCENE = ROOT / "out" / "scene.json"
FIXTURE_SCENE = ROOT / "fixtures" / "scene.sample.json"
CACHE_PATH = Path(__file__).resolve().parent / "cache.json"
AGENT = os.environ.get("PRICING_AGENT", "antigravity-preview-09-2026")
# Browsing retailers is leaf work, so Flash is the default hands; set PRICING_MODEL=gemini-4-argon to run Argon in the sandbox.
MODEL = os.environ.get("PRICING_MODEL", "gemini-3.8-flash")

PROMPT = """You are pricing a household item for an insurance proof-of-loss claim.
Item, as seen on video: "{label}"

Insurers reimburse at "like kind and quality", so the exact model is unknown. Use the browser to search
2-3 major US retailers (e.g. Amazon, Target, Walmart, Best Buy, Wayfair, Home Depot, IKEA) for a NEW item
of the same kind and quality. Visit real product pages. Then reply with ONLY this JSON, no prose:
{{"low": <lowest price, number>, "high": <highest price, number>, "currency": "USD",
  "query": "<the search phrase you used>",
  "sources": [{{"retailer": "...", "title": "...", "price": <number>, "url": "https://..."}}]}}"""


def load_json(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default


def write_json(path, data):
    tmp = Path(str(path) + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def cache_key(label):
    return re.sub(r"\s+", " ", label.strip().lower())


def save_price(scene_path, item_id, price):
    # Re-read right before writing: A may have rewritten the scene while we were browsing.
    scene = load_json(scene_path, {})
    for item in scene.get("items", []):
        if item["id"] == item_id:
            item["price"] = price
    write_json(scene_path, scene)


def parse_result(text):
    """Pull the last JSON object containing a price out of the agent's final message."""
    for m in reversed(list(re.finditer(r"\{.*\}", text or "", re.S))):
        try:
            data = json.loads(m.group(0))
        except json.JSONDecodeError:
            continue
        if "low" in data and "high" in data:
            return data
    raise ValueError(f"no price JSON in agent reply: {text[:200] if text else text!r}")


# URLs that show up in agent traces but aren't retailers (search engines, XML namespaces, our own API).
NOISE_HOSTS = ("google.com", "googleapis.com", "gstatic.com", "w3.org", "schema.org", "bing.com", "duckduckgo.com")


def site_of(url):
    """Retailer site for a URL seen in the agent trace (redsky.target.com → target.com), or None for noise."""
    try:
        host = urlparse(url).netloc.lower().split(":")[0]
    except ValueError:  # malformed URLs in agent traces, e.g. "Invalid IPv6 URL"
        return None
    labels = host.split(".")
    if len(labels) < 2 or not labels[-1].isalpha():
        return None  # truncated URLs like "https://www"
    site = ".".join(labels[-2:])
    if site.endswith(NOISE_HOSTS) or any(t in site for t in ("img", "cdn", "static")):
        return None
    return site


def urls_in(obj):
    return re.findall(r"https?://[^\s\"'\\)>\]]+", str(obj))


class ManagedPricer:
    """One Managed Agents sandbox, reused across items so we only provision once."""

    def __init__(self, agent, model, number):
        from google import genai

        self.client = genai.Client()
        self.agent = agent
        self.model = model
        self.name = f"Sandbox {number}"
        self.environment = "remote"

    def _set_environment(self, env_id):
        if env_id and env_id != self.environment:
            if self.environment == "remote":
                emit("pricing", f"{self.name} provisioned (Managed Agents environment …{env_id[-6:]})", "ok",
                     {"environment": env_id})
            self.environment = env_id

    def price(self, item):
        seen = set()
        stream = self.client.interactions.create(
            agent=self.agent,
            agent_config={"type": "antigravity", "model": self.model},
            input=PROMPT.format(label=item["label"]),
            environment=self.environment,
            stream=True,
        )
        interaction_id = None
        for event in stream:
            # Streamed events carry the reply in fragments; we only use them to surface visited pages live
            # and to learn the interaction/environment ids. The final answer is fetched whole below.
            inter = getattr(event, "interaction", None)
            interaction_id = getattr(inter, "id", None) or interaction_id
            self._set_environment(getattr(inter, "environment_id", None) or getattr(event, "environment_id", None))
            for url in urls_in(event):
                host = site_of(url)
                if host and host not in seen and len(seen) < 6:
                    seen.add(host)
                    emit("pricing", f"{self.name} browsing {host} for \"{item['label']}\"", "info",
                         {"item": item["id"], "url": url})
        if not interaction_id:
            raise ValueError("stream ended without an interaction id")
        final = self.client.interactions.get(id=interaction_id)
        for _ in range(60):  # up to ~3 min for the agent to finish after the stream closes
            if getattr(final, "status", None) not in ("in_progress", "queued"):
                break
            time.sleep(3)
            final = self.client.interactions.get(id=interaction_id)
        self._set_environment(getattr(final, "environment_id", None))
        return parse_result(final.output_text)


def to_contract(result):
    sources = [s["url"] if isinstance(s, dict) else s for s in result.get("sources", [])]
    return {
        "low": float(result["low"]),
        "high": float(result["high"]),
        "currency": result.get("currency", "USD"),
        "sources": [s for s in sources if s],
        "basis": "like kind and quality",
    }


def fmt(p):
    return f"${p['low']:,.0f}" if p["low"] == p["high"] else f"${p['low']:,.0f}–${p['high']:,.0f}"


def report_sources(item, result):
    """One panel line per retailer listing, so judges can check each number behind a price range."""
    for s in result.get("sources", []):
        if not isinstance(s, dict) or s.get("price") is None:
            continue
        title = (s.get("title") or "").strip()
        title = title[:70] + "…" if len(title) > 70 else title
        emit("pricing", f"Found at {s.get('retailer', 'retailer')}: {title} — ${float(s['price']):,.2f}", "info",
             {"item": item["id"], "url": s.get("url")})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scene", type=Path, default=DEFAULT_SCENE)
    ap.add_argument("--only", nargs="*", help="item ids to price")
    ap.add_argument("--refresh", action="store_true", help="ignore cache and existing prices")
    ap.add_argument("--cache-only", action="store_true", help="never call the live agent")
    ap.add_argument("--agent", default=AGENT)
    ap.add_argument("--model", default=MODEL)
    ap.add_argument("--sandboxes", type=int, default=int(os.environ.get("PRICING_SANDBOXES", 6)),
                    help="parallel Managed Agents sandboxes")
    args = ap.parse_args()

    if not args.scene.exists():
        args.scene.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(FIXTURE_SCENE, args.scene)
        print(f"no scene yet; copied fixture to {args.scene}")

    items = load_json(args.scene, {}).get("items", [])
    todo = [i for i in items if (args.refresh or not i.get("price")) and (not args.only or i["id"] in args.only)]
    cache = load_json(CACHE_PATH, {})
    write_lock = threading.Lock()  # scene.json and cache.json are rewritten whole

    # A pool of sandboxes: each worker takes one, prices an item in it, and hands it back for reuse.
    live = [i for i in todo if args.refresh or cache_key(i["label"]) not in cache]
    n_boxes = 0 if args.cache_only else min(args.sandboxes, len(live))
    pool = queue.Queue()
    for k in range(1, n_boxes + 1):
        pool.put(ManagedPricer(args.agent, args.model, k))

    emit("pricing", f"Pricing {len(todo)} items at like kind and quality"
         + (f" · {n_boxes} Managed Agents sandboxes on {args.model}" if n_boxes else " · from cache"), "info")

    def price_item(n, item):
        label, key = item["label"], cache_key(item["label"])
        emit("pricing", f"Searching price {n}/{len(todo)}: \"{label}\"", "info", {"item": item["id"]})
        result = None if args.refresh else cache.get(key)
        cached = result is not None
        if result is None and not args.cache_only:
            pricer = pool.get()
            try:
                result = pricer.price(item)
            except Exception as e:  # keep going; one blocked retailer shouldn't stop the inventory
                emit("pricing", f"Price search failed for \"{label}\": {e}", "error", {"item": item["id"]})
                return False
            finally:
                pool.put(pricer)
            with write_lock:
                cache[key] = result
                write_json(CACHE_PATH, cache)
        if result is None:
            emit("pricing", f"No cached price for \"{label}\"", "error", {"item": item["id"]})
            return False

        price = to_contract(result)
        with write_lock:
            save_price(args.scene, item["id"], price)
        report_sources(item, result)
        hosts = sorted({urlparse(u).netloc.removeprefix("www.") for u in price["sources"]})
        where = ", ".join(hosts) if hosts else f"{len(price['sources'])} sources"
        emit(
            "pricing",
            f"Priced \"{label}\" → {fmt(price)} ({where}){' · cached' if cached else ''}",
            "ok",
            {"item": item["id"], "low": price["low"], "high": price["high"]},
        )
        return True

    with ThreadPoolExecutor(max_workers=max(n_boxes, 1)) as ex:
        done = sum(ex.map(lambda pair: price_item(*pair), enumerate(todo, 1)))

    emit("pricing", f"Pricing done: {done}/{len(todo)} items priced", "ok" if done == len(todo) else "warn")


if __name__ == "__main__":
    main()
