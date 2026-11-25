"""Deterministic checks that run on every draft, so the critic gets hard numbers
instead of only its own opinion."""
from __future__ import annotations

import re
from collections import Counter

SLOP = [
    "results-driven", "results driven", "passionate", "dynamic", "proven track record",
    "highly motivated", "cutting-edge", "cutting edge", "spearhead", "seamless",
    "synerg", "pivotal", "demonstrated exceptional", "responsible for", "robust",
    "utilize", "utilized", "fast-paced", "game-changer", "meticulous", "adept at",
    "testament", "delve", "go-getter", "detail-oriented", "self-starter",
    "world-class", "best-in-class", "leverag", "orchestrat", "empower",
    "drive success", "driving success", "overall growth", "various tasks",
    "wide range of", "a variety of", "in order to", "successfully",
]

FILLER_ENDINGS = [
    r"to drive (business )?(success|growth|value|results)\.?$",
    r"ensuring (seamless|smooth|efficient) .*$",
    r"contributing to (the )?(overall|organizational|team) .*$",
    r"(and|while) (fostering|promoting) a culture of .*$",
    r"to (enhance|improve) (overall )?(efficiency|productivity|performance)\.?$",
]


def slop_hits(lines: list[str]) -> list[dict]:
    hits = []
    for n, line in enumerate(lines):
        low = line.lower()
        for phrase in SLOP:
            if phrase in low:
                hits.append({"line": n, "phrase": phrase, "text": line})
        for pat in FILLER_ENDINGS:
            if re.search(pat, low):
                hits.append({"line": n, "phrase": "filler ending", "text": line})
    return hits


def repeated_openers(bullets: list[str], limit: int = 2) -> dict[str, int]:
    """Opening verbs used more than `limit` times across the resume."""
    first = Counter(b.split()[0].lower().strip(",.") for b in bullets if b.split())
    return {w: c for w, c in first.items() if c > limit}


def keyword_coverage(text: str, keywords: dict[str, list[str]]) -> dict:
    """keywords: {"tier1": [...], "tier2": [...], "tier3": [...]}.
    Returns which ones appear (case-insensitive, word-boundary) in the text."""
    low = text.lower()
    out = {}
    for tier, words in keywords.items():
        found, missing = [], []
        for w in words:
            pat = r"(?<![a-z0-9])" + re.escape(w.lower()) + r"(?![a-z0-9])"
            (found if re.search(pat, low) else missing).append(w)
        out[tier] = {
            "found": found,
            "missing": missing,
            "pct": round(100 * len(found) / len(words)) if words else 100,
        }
    return out


def report(lines: list[str], bullets: list[str], keywords: dict[str, list[str]]) -> dict:
    return {
        "slop": slop_hits(lines),
        "repeated_openers": repeated_openers(bullets),
        "coverage": keyword_coverage("\n".join(lines), keywords),
    }
