"""The approved knowledge base: the only facts the AI (or the offline engine) may state."""
import json
import re
from functools import lru_cache
from pathlib import Path

TIME_RE = re.compile(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b", re.IGNORECASE)


@lru_cache(maxsize=4)
def load(path: str) -> dict:
    kb = json.loads(Path(path).read_text(encoding="utf-8"))
    kb["_by_code"] = {t["code"]: t for t in kb["tours"]}
    return kb


def tour(kb: dict, code: str | None) -> dict | None:
    return kb["_by_code"].get(code) if code else None


def minutes(hour: str, minute: str | None, ampm: str) -> int:
    h = int(hour) % 12 + (12 if ampm.lower() == "pm" else 0)
    return h * 60 + int(minute or 0)


def _all_text(kb: dict) -> str:
    return json.dumps({k: v for k, v in kb.items() if not k.startswith("_")}, ensure_ascii=False)


def allowed_prices(kb: dict) -> set[int]:
    """Whole-dollar prices that may appear in a reply (per person prices only)."""
    prices = set()
    for t in kb["tours"]:
        prices.update({t["adult_price"], t["child_price"]})
    return prices


def allowed_times(kb: dict) -> set[int]:
    """Every clock time written anywhere in the knowledge base, in minutes after midnight."""
    return {minutes(h, m, ap) for h, m, ap in TIME_RE.findall(_all_text(kb))}


def policy_sentences(kb: dict) -> list[str]:
    return list(kb["policies"].values())
