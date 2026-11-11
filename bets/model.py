"""The maths: remove bookmaker margin, build a fair probability, find value, size stakes.

Idea in one line: the average of many bookmakers' prices (weighted towards sharp
books like Pinnacle and Betfair) predicts results better than any one bookmaker.
When one book offers odds higher than that fair price implies, the bet has
positive expected value.
"""
from __future__ import annotations

import math
from datetime import datetime


# ---------- removing the margin ("de-vigging") ----------------------------------

def devig(odds: list[float], method: str = "power") -> list[float]:
    """Turn one bookmaker's decimal odds for every outcome into probabilities summing to 1."""
    q = [1.0 / o for o in odds]
    if method == "multiplicative":
        s = sum(q)
        return [x / s for x in q]
    if method == "power":
        # find k with sum(q_i ** k) == 1. Shades long shots more than favourites,
        # matching the well-known favourite-longshot bias.
        lo, hi = 0.2, 5.0
        for _ in range(60):
            k = (lo + hi) / 2
            if sum(x ** k for x in q) > 1:
                lo = k
            else:
                hi = k
        k = (lo + hi) / 2
        p = [x ** k for x in q]
        s = sum(p)
        return [x / s for x in p]
    if method == "shin":
        # Shin (1993): margin comes from bookmakers protecting against insiders.
        s = sum(q)
        def probs(z):
            return [(math.sqrt(z * z + 4 * (1 - z) * x * x / s) - z) / (2 * (1 - z)) for x in q]
        lo, hi = 0.0, 0.4
        for _ in range(60):
            z = (lo + hi) / 2
            if sum(probs(z)) > 1:
                lo = z
            else:
                hi = z
        p = probs((lo + hi) / 2)
        t = sum(p)
        return [x / t for x in p]
    raise ValueError(f"unknown devig method {method}")


def overround(odds: list[float]) -> float:
    return sum(1.0 / o for o in odds) - 1.0


# ---------- consensus fair probability ---------------------------------------

def parse_iso(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def book_lines(event: dict, market: str = "h2h", stale_minutes: float = 90) -> dict[str, dict]:
    """{book_key: {outcome_name: price}} for books pricing every outcome, stale books dropped."""
    lines, times = {}, {}
    for b in event.get("bookmakers", []):
        for m in b.get("markets", []):
            if m.get("key") != market:
                continue
            prices = {o["name"]: float(o["price"]) for o in m.get("outcomes", []) if o.get("price", 0) > 1}
            if len(prices) >= 2:
                lines[b["key"]] = prices
                times[b["key"]] = parse_iso(m.get("last_update") or b.get("last_update"))
    if not lines:
        return {}
    # every book must price the same outcomes (drops 2-way books in 3-way soccer markets)
    outcome_sets = {}
    for k, v in lines.items():
        outcome_sets.setdefault(frozenset(v), []).append(k)
    main = max(outcome_sets.items(), key=lambda kv: (len(kv[0]), len(kv[1])))[0]
    newest = max(times.values())
    return {k: v for k, v in lines.items()
            if frozenset(v) == main and (newest - times[k]).total_seconds() / 60 <= stale_minutes}


def fair_probs(lines: dict[str, dict], cfg: dict, exclude: str | None = None) -> tuple[dict, int, bool]:
    """Weighted average of each book's de-vigged probabilities.

    `exclude` leaves one book out so a book is never compared with itself.
    Returns (probabilities by outcome, books used, whether a sharp book was included).
    """
    weights = cfg.get("book_weights", {})
    default_w = cfg.get("default_weight", 1)
    sharps = set(cfg.get("sharp_books", []))
    total, acc, n, has_sharp = 0.0, {}, 0, False
    for book, prices in lines.items():
        if book == exclude:
            continue
        names = list(prices)
        p = devig([prices[x] for x in names], cfg.get("devig", "power"))
        w = weights.get(book, default_w)
        for name, pi in zip(names, p):
            acc[name] = acc.get(name, 0.0) + w * pi
        total += w
        n += 1
        has_sharp = has_sharp or book in sharps
    if not total:
        return {}, 0, False
    return {k: v / total for k, v in acc.items()}, n, has_sharp


def find_value(event: dict, cfg: dict) -> list[dict]:
    """Every (book, outcome) on this event whose price beats the fair price by enough."""
    lines = book_lines(event, stale_minutes=cfg.get("stale_minutes", 90))
    bettable = set(cfg.get("bettable_books", []))
    found = []
    for book, prices in lines.items():
        if bettable and book not in bettable:
            continue
        fair, n, has_sharp = fair_probs(lines, cfg, exclude=book)
        if n < cfg.get("min_books", 4):
            continue
        need = cfg.get("min_edge", 0.03) + (0 if has_sharp else cfg.get("no_sharp_extra_edge", 0.02))
        for name, price in prices.items():
            p = fair.get(name)
            if not p:
                continue
            ev = p * price - 1
            if need <= ev <= cfg.get("max_edge", 0.2) and cfg.get("min_odds", 1.3) <= price <= cfg.get("max_odds", 5):
                found.append({"book": book, "selection": name, "odds": price, "fair_prob": p,
                              "fair_odds": 1 / p, "edge": ev, "books_used": n, "sharp": has_sharp})
    return sorted(found, key=lambda x: -x["edge"])


def consensus_all(event: dict, cfg: dict) -> dict:
    """Fair probabilities using every book (for tracking the closing line)."""
    lines = book_lines(event, stale_minutes=cfg.get("stale_minutes", 90))
    fair, n, _ = fair_probs(lines, cfg)
    best = {}
    for prices in lines.values():
        for k, v in prices.items():
            best[k] = max(best.get(k, 0), v)
    return {"fair": fair, "best": best, "books": n}


# ---------- staking -----------------------------------------------------------

def kelly_fraction(p: float, odds: float) -> float:
    """Full-Kelly share of bankroll for decimal odds: (p*o - 1) / (o - 1)."""
    return max(0.0, (p * odds - 1) / (odds - 1))


def stake_for(p: float, odds: float, bankroll: float, scfg: dict) -> float:
    f = kelly_fraction(p, odds) * scfg.get("kelly_fraction", 0.25)
    f = min(f, scfg.get("max_stake_pct", 0.02))
    stake = round(bankroll * f, 2)
    return stake if stake >= scfg.get("min_stake", 5) else 0.0
