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
import shutil
import sys
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


def urls_in(obj):
    return re.findall(r"https?://[^\s\"'\\)>\]]+", str(obj))


class ManagedPricer:
    """One Managed Agents sandbox, reused across items so we only provision once."""

    def __init__(self, agent, model):
        from google import genai

        self.client = genai.Client()
        self.agent = agent
        self.model = model
        self.environment = "remote"

    def price(self, item):
        seen = set()
        stream = self.client.interactions.create(
            agent=self.agent,
            agent_config={"type": "antigravity", "model": self.model},
            input=PROMPT.format(label=item["label"]),
            environment=self.environment,
            stream=True,
        )
        chunks = []
        for event in stream:
            # Event shapes aren't documented in detail; surface any page the agent visits, keep everything for parsing.
            env_id = getattr(event, "environment_id", None)
            if env_id:
                self.environment = env_id
            chunks.append(str(event))
            for url in urls_in(event):
                host = urlparse(url).netloc.removeprefix("www.")
                if host and host not in seen and "googleapis" not in host and len(seen) < 6:
                    seen.add(host)
                    emit("pricing", f"Browsing {host} for \"{item['label']}\"", "info", {"item": item["id"], "url": url})
            text = getattr(event, "output_text", None)
            if text:
                chunks.append(text)
        return parse_result("\n".join(chunks))


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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scene", type=Path, default=DEFAULT_SCENE)
    ap.add_argument("--only", nargs="*", help="item ids to price")
    ap.add_argument("--refresh", action="store_true", help="ignore cache and existing prices")
    ap.add_argument("--cache-only", action="store_true", help="never call the live agent")
    ap.add_argument("--agent", default=AGENT)
    ap.add_argument("--model", default=MODEL)
    args = ap.parse_args()

    if not args.scene.exists():
        args.scene.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(FIXTURE_SCENE, args.scene)
        print(f"no scene yet; copied fixture to {args.scene}")

    items = load_json(args.scene, {}).get("items", [])
    todo = [i for i in items if (args.refresh or not i.get("price")) and (not args.only or i["id"] in args.only)]
    cache = load_json(CACHE_PATH, {})
    pricer = None

    emit("pricing", f"Pricing {len(todo)} items at like kind and quality", "info")
    for n, item in enumerate(todo, 1):
        label, key = item["label"], cache_key(item["label"])
        emit("pricing", f"Searching price {n}/{len(todo)}: \"{label}\"", "info", {"item": item["id"]})

        result = None if args.refresh else cache.get(key)
        cached = result is not None
        if result is None and not args.cache_only:
            try:
                pricer = pricer or ManagedPricer(args.agent, args.model)
                result = pricer.price(item)
                cache[key] = result
                write_json(CACHE_PATH, cache)
            except Exception as e:  # keep going; one blocked retailer shouldn't stop the inventory
                emit("pricing", f"Price search failed for \"{label}\": {e}", "error", {"item": item["id"]})
                continue
        if result is None:
            emit("pricing", f"No cached price for \"{label}\"", "error", {"item": item["id"]})
            continue

        price = to_contract(result)
        save_price(args.scene, item["id"], price)
        hosts = sorted({urlparse(u).netloc.removeprefix("www.") for u in price["sources"]})
        where = ", ".join(hosts) if hosts else f"{len(price['sources'])} sources"
        emit(
            "pricing",
            f"Searching price: \"{result.get('query', label)}\" → {fmt(price)} ({where}){' · cached' if cached else ''}",
            "ok",
            {"item": item["id"], "low": price["low"], "high": price["high"]},
        )

    emit("pricing", f"Pricing done: {len(todo)} items checked", "ok")


if __name__ == "__main__":
    main()
