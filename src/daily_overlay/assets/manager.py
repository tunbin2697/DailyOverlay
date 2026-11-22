from __future__ import annotations

import json
import logging
import random
import threading
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Callable

from pydantic import BaseModel, Field

from daily_overlay.config import ASSETS_DIR, ensure_data_dirs

logger = logging.getLogger(__name__)

CATALOG_PATH = ASSETS_DIR / "catalog.json"
NOTO_API_URL = "https://googlefonts.github.io/noto-emoji-animation/data/api.json"
SUPPORTED_EXTS = {".gif", ".webp", ".png"}

AESTHETIC_CATEGORIES = {
    "Animals and nature",
    "Food and drink",
    "Activities and events",
    "Travel and places",
    "Objects",
}

EXTRA_ALLOWED_SLUGS = {
    "ghost",
    "robot",
    "alien",
    "sparkles",
    "glowing-star",
    "fire",
    "dizzy",
    "zzz",
    "hot-springs",
}

# Filter out cluttered UI/warning/medical/office icons that don't fit calm desktop ambience
EXCLUDED_KEYWORDS = (
    "stop-sign",
    "construction",
    "police",
    "ambulance",
    "fire-engine",
    "warning",
    "no-",
    "prohibited",
    "toilet",
    "plunger",
    "syringe",
    "pill",
    "blood",
    "coffin",
    "urn",
    "headstone",
    "bomb",
    "knife",
    "dagger",
    "gun",
    "pistol",
    "cigarette",
    "skull",
    "poop",
    "pile-of-poo",
    "pager",
    "fax",
    "printer",
    "plug",
    "battery",
    "stethoscope",
    "thermometer",
    "adhesive",
    "crutch",
    "wheelchair",
    "shopping-cart",
    "money",
    "dollar",
    "credit-card",
    "receipt",
    "chart",
    "traffic-light",
    "fuel-pump",
    "delivery-truck",
    "tractor",
    "taxi",
    "bus",
    "automobile",
    "motorcycle",
    "racing-car",
    "minibus",
    "trolleybus",
    "sport-utility",
    "pickup-truck",
    "articulated-lorry",
    "onion",
    "garlic",
    "potato",
    "broccoli",
    "meat",
    "poultry",
    "bacon",
    "hamburger",
    "hot-dog",
    "burrito",
    "taco",
    "salt",
    "can",
    "tooth",
    "bone",
    "microbe",
    "mosquito",
    "cockroach",
    "fly",
    "worm",
    "rat",
    "mouse",
    "pig",
    "boar",
    "skunk",
    "bat",
    "spider",
    "scorpion",
    "socks",
    "briefs",
    "shorts",
    "bikini",
    "shoe",
    "boot",
    "sandal",
    "clutch",
    "handbag",
    "purse",
    "necktie",
    "jeans",
    "t-shirt",
    "dress",
    "kimono",
    "sari",
    "lab-coat",
    "safety-vest",
    "goggles",
    "glasses",
    "diaper",
    "lotion",
    "soap",
    "sponge",
    "razor",
    "toothbrush",
    "basket",
    "roll-of-paper",
    "bucket",
    "mouse-trap",
    "fire-extinguisher",
    "hammer",
    "axe",
    "pick",
    "wrench",
    "screwdriver",
    "nut-and-bolt",
    "gear",
    "clamp",
    "chains",
    "hook",
    "toolbox",
    "magnet",
    "ladder",
    "brick",
    "wood",
    "medal",
    "trophy",
    "soccer",
    "baseball",
    "softball",
    "basketball",
    "volleyball",
    "rugby",
    "football",
    "bowling",
    "cricket",
    "hockey",
    "lacrosse",
    "ping-pong",
    "badminton",
    "boxing",
    "martial-arts",
    "goal-net",
    "diving-mask",
    "sled",
    "curling",
    "pool",
    "slot-machine",
    "joystick",
    "die",
    "jigsaw",
    "chess",
    "mahjong",
    "spade",
    "club",
    "diamond",
)

# Built-in offline fallback entries if the first catalog sync happens offline
FALLBACK_CATALOG_ITEMS: list[tuple[str, str, str]] = [
    ("cherry-blossom", "1f338", "Animals and nature"),
    ("lotus", "1fab7", "Animals and nature"),
    ("maple-leaf", "1f341", "Animals and nature"),
    ("fallen-leaf", "1f342", "Animals and nature"),
    ("mushroom", "1f344", "Animals and nature"),
    ("butterfly", "1f98b", "Animals and nature"),
    ("whale", "1f433", "Animals and nature"),
    ("dolphin", "1f42c", "Animals and nature"),
    ("octopus", "1f419", "Animals and nature"),
    ("turtle", "1f422", "Animals and nature"),
    ("owl", "1f989", "Animals and nature"),
    ("fox", "1f98a", "Animals and nature"),
    ("panda", "1f43c", "Animals and nature"),
    ("penguin", "1f427", "Animals and nature"),
    ("frog", "1f438", "Animals and nature"),
    ("cat-face", "1f431", "Animals and nature"),
    ("fish", "1f41f", "Animals and nature"),
    ("bird", "1f426", "Animals and nature"),
    ("bee", "1f41d", "Animals and nature"),
    ("ladybug", "1f41e", "Animals and nature"),
    ("snail", "1f40c", "Animals and nature"),
    ("sparkles", "2728", "Smileys and emotions"),
    ("ghost", "1f47b", "Smileys and emotions"),
    ("fire", "1f525", "Smileys and emotions"),
    ("sailboat", "26f5", "Travel and places"),
    ("rocket", "1f680", "Travel and places"),
    ("flying-saucer", "1f6f8", "Travel and places"),
    ("diya-lamp", "1fa94", "Activities and events"),
    ("balloon", "1f388", "Activities and events"),
    ("fireworks", "1f386", "Activities and events"),
]

_in_flight: set[str] = set()
_in_flight_lock = threading.Lock()
_catalog_cache: dict[str, "AssetEntry"] | None = None


class AssetEntry(BaseModel):
    id: str
    codepoint: str = ""
    category: str = "Other"
    filename: str
    url: str = ""
    tags: list[str] = Field(default_factory=list)


class AssetCatalog(BaseModel):
    source: str = "Google Noto Animated Emoji (Apache 2.0)"
    assets: list[AssetEntry] = Field(default_factory=list)


def _is_aesthetic_slug(slug: str) -> bool:
    low = slug.lower()
    return not any(bad in low for bad in EXCLUDED_KEYWORDS)


def sync_public_catalog(force: bool = False) -> AssetCatalog:
    """Fetch and cache the curated ~180-220 public animated WebP catalog from Google Noto API."""
    global _catalog_cache
    ensure_data_dirs()
    if CATALOG_PATH.exists() and not force:
        try:
            cat = AssetCatalog.model_validate_json(CATALOG_PATH.read_text(encoding="utf-8"))
            if len(cat.assets) >= 30:
                _catalog_cache = {a.id: a for a in cat.assets}
                return cat
        except Exception:
            pass

    entries: list[AssetEntry] = []
    seen: set[str] = set()
    try:
        req = urllib.request.Request(NOTO_API_URL, headers={"User-Agent": "DailyOverlay/0.4"})
        with urllib.request.urlopen(req, timeout=12) as resp:
            raw = json.loads(resp.read().decode("utf-8"))

        for ic in raw.get("icons", []):
            cats = set(ic.get("categories", []))
            tags = [t.strip(":").lower().replace("_", "-") for t in ic.get("tags", []) if t]
            if not tags:
                continue
            slug = tags[0]
            if slug in seen:
                continue
            if not ((cats & AESTHETIC_CATEGORIES) or (set(tags) & EXTRA_ALLOWED_SLUGS)):
                continue
            if not _is_aesthetic_slug(slug):
                continue

            cp = str(ic.get("codepoint", "")).strip()
            if not cp:
                continue
            seen.add(slug)
            cat_name = next(iter(cats & AESTHETIC_CATEGORIES), sorted(cats)[0] if cats else "Other")
            entries.append(
                AssetEntry(
                    id=slug,
                    codepoint=cp,
                    category=cat_name,
                    filename=f"{slug}.webp",
                    url=f"https://fonts.gstatic.com/s/e/notoemoji/latest/{cp}/512.webp",
                    tags=tags[:4],
                )
            )
        logger.info("Synced public Noto Animated catalog: %d aesthetic assets", len(entries))
    except Exception as exc:
        logger.warning("Could not sync remote Noto catalog (%s); using built-in list.", exc)
        for slug, cp, cat_name in FALLBACK_CATALOG_ITEMS:
            entries.append(
                AssetEntry(
                    id=slug,
                    codepoint=cp,
                    category=cat_name,
                    filename=f"{slug}.webp",
                    url=f"https://fonts.gstatic.com/s/e/notoemoji/latest/{cp}/512.webp",
                    tags=[slug],
                )
            )

    catalog = AssetCatalog(assets=entries)
    CATALOG_PATH.write_text(catalog.model_dump_json(indent=2), encoding="utf-8")
    _catalog_cache = {a.id: a for a in catalog.assets}
    return catalog


def _get_catalog_map() -> dict[str, AssetEntry]:
    global _catalog_cache
    if _catalog_cache is None:
        sync_public_catalog(force=False)
    return _catalog_cache or {}


def ensure_manifest() -> AssetCatalog:
    return sync_public_catalog(force=False)


def list_local_assets() -> list[str]:
    ensure_data_dirs()
    return sorted(
        p.stem
        for p in ASSETS_DIR.iterdir()
        if p.is_file() and p.suffix.lower() in SUPPORTED_EXTS
    )


def sample_catalog_for_prompt(max_items: int = 90) -> dict[str, list[str]]:
    """Return a diverse category-grouped sample of available public + local assets for Gemini."""
    cmap = _get_catalog_map()
    by_cat: dict[str, list[str]] = {}
    for entry in cmap.values():
        by_cat.setdefault(entry.category, []).append(entry.id)

    # Also include any custom user-dropped assets in data/assets/
    local_custom = [stem for stem in list_local_assets() if stem not in cmap]
    if local_custom:
        by_cat["Custom Local Assets"] = local_custom

    per_cat = max(12, max_items // max(1, len(by_cat)))
    sampled: dict[str, list[str]] = {}
    for cat, ids in sorted(by_cat.items()):
        # Prioritize locally downloaded assets + random exploration of the full catalog
        local_set = set(list_local_assets())
        local_first = [i for i in ids if i in local_set]
        remote_rest = [i for i in ids if i not in local_set]
        random.shuffle(local_first)
        random.shuffle(remote_rest)
        pick = (local_first[: per_cat // 2] + remote_rest)[:per_cat]
        if len(pick) < per_cat:
            extras = [i for i in local_first if i not in pick]
            pick.extend(extras[: per_cat - len(pick)])
        sampled[cat] = sorted(pick)
    return sampled


def _normalize_id(name: str) -> str:
    return name.strip().lower().replace("_", "-").replace(" ", "-")


def resolve_asset_path(name_or_style: str) -> Path | None:
    """Resolve a style string to an already-cached local asset file."""
    ensure_data_dirs()
    if not name_or_style:
        return None
    direct = ASSETS_DIR / name_or_style
    if direct.is_file() and direct.suffix.lower() in SUPPORTED_EXTS:
        return direct

    norm = _normalize_id(name_or_style)
    for p in ASSETS_DIR.iterdir():
        if p.is_file() and p.suffix.lower() in SUPPORTED_EXTS:
            if p.name.lower() == name_or_style.lower() or _normalize_id(p.stem) == norm:
                return p
    return None


def _lookup_entry(asset_id: str) -> AssetEntry | None:
    cmap = _get_catalog_map()
    norm = _normalize_id(asset_id)
    if norm in cmap:
        return cmap[norm]
    # Tag match fallback (e.g. "cat" -> "cat-face" or "blossom")
    for entry in cmap.values():
        if norm in entry.tags:
            return entry
    return None


def _download_one(entry: AssetEntry) -> Path | None:
    target = ASSETS_DIR / entry.filename
    if target.is_file() and target.stat().st_size > 1024:
        return target
    if not entry.url:
        return None
    tmp = ASSETS_DIR / f"{entry.filename}.part"
    try:
        ensure_data_dirs()
        req = urllib.request.Request(entry.url, headers={"User-Agent": "DailyOverlay/0.4"})
        with urllib.request.urlopen(req, timeout=12) as resp:
            data = resp.read()
        if len(data) < 512:
            return None
        tmp.write_bytes(data)
        tmp.replace(target)
        return target
    except Exception as exc:
        logger.debug("Download failed for %s: %s", entry.id, exc)
        if tmp.exists():
            tmp.unlink(missing_ok=True)
        return None


def ensure_spotlight_asset_async(
    asset_id: str,
    on_ready: Callable[[Path], None] | None = None,
) -> Path | None:
    """Return local cached path immediately if present, else download in background."""
    existing = resolve_asset_path(asset_id)
    if existing is not None:
        return existing

    entry = _lookup_entry(asset_id)
    if entry is None:
        return None

    with _in_flight_lock:
        if entry.id in _in_flight:
            return None
        _in_flight.add(entry.id)

    def _run() -> None:
        try:
            path = _download_one(entry)
            if path and on_ready:
                on_ready(path)
        finally:
            with _in_flight_lock:
                _in_flight.discard(entry.id)

    threading.Thread(target=_run, name=f"AssetDL-{entry.id}", daemon=True).start()
    return None


def bulk_download_public_pack(
    limit: int = 120,
    max_workers: int = 8,
    progress_cb: Callable[[int, int], None] | None = None,
) -> tuple[int, int]:
    """Download a rich offline library of public animated WebP assets once for months of variety.

    Returns (downloaded_count, total_local_count).
    """
    catalog = sync_public_catalog(force=False)
    # Balance selection evenly across all aesthetic categories
    by_cat: dict[str, list[AssetEntry]] = {}
    for a in catalog.assets:
        by_cat.setdefault(a.category, []).append(a)

    selected: list[AssetEntry] = []
    idx = 0
    cats = sorted(by_cat.keys())
    while len(selected) < limit:
        added_any = False
        for c in cats:
            lst = by_cat[c]
            if idx < len(lst):
                selected.append(lst[idx])
                added_any = True
                if len(selected) >= limit:
                    break
        if not added_any:
            break
        idx += 1

    to_fetch = [e for e in selected if not (ASSETS_DIR / e.filename).is_file()]
    total = len(to_fetch)
    done = 0
    ok = 0

    if to_fetch:
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futs = [pool.submit(_download_one, entry) for entry in to_fetch]
            for fut in as_completed(futs):
                done += 1
                if fut.result() is not None:
                    ok += 1
                if progress_cb:
                    progress_cb(done, total)

    return ok, len(list_local_assets())
