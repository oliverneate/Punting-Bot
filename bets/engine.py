"""One bot run: settle finished games, scan markets, place paper bets, save state."""
from __future__ import annotations

import calendar
import fnmatch
import json
import math
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import model
from .oddsapi import iso

STATE = Path(__file__).resolve().parent.parent / "state" / "ledger.json"


# ---------- state ----------------------------------------------------------------

def load_state(cfg, path=STATE):
    if path.exists():
        return json.loads(path.read_text())
    return {"started": None, "start_bankroll": cfg["bankroll"], "cash": cfg["bankroll"],
            "open": [], "settled": [], "history": [], "runs": [], "last_scan": [], "credits_remaining": None}


def save_state(state, path=STATE):
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(state, indent=1))


def equity(state):
    return state["cash"] + sum(b["stake"] for b in state["open"])


# ---------- helpers -------------------------------------------------------------

def credits_per_odds_call(cfg):
    n = len(cfg["markets"])
    return n * (math.ceil(len(cfg["bookmakers"]) / 10) if cfg.get("bookmakers") else len(cfg["regions"]))


def resolve_sports(cfg, active_keys):
    """config sport patterns -> {api_key: info} for sports in season right now."""
    out = {}
    for pattern, info in cfg["sports"].items():
        if info.get("enabled", True) is False:
            continue
        for key in active_keys:
            if fnmatch.fnmatch(key, pattern):
                out[key] = info
    return out


def winner_of(score_row):
    """List of outcome names that 'won' (two names means a tie)."""
    def num(s):
        try:
            return float(str(s).split("/")[0])
        except ValueError:
            return None
    pts = {s["name"]: num(s["score"]) for s in (score_row.get("scores") or [])}
    if len(pts) < 2 or None in pts.values():
        return None
    top = max(pts.values())
    return [k for k, v in pts.items() if v == top]


def settle_bet(bet, winners, now):
    stake, odds, sel = bet["stake"], bet["odds"], bet["selection"]
    if len(winners) == 1:
        payout = stake * odds if sel == winners[0] else 0.0
        result = "won" if payout else "lost"
    elif "Draw" in bet["outcomes"]:
        payout = stake * odds if sel == "Draw" else 0.0
        result = "won" if payout else "lost"
    else:  # tie in a two-way market: Australian dead-heat rule, half the stake at full odds
        payout = stake * odds / 2 if sel in winners else 0.0
        result = "dead heat" if payout else "lost"
    bet.update(result=result, payout=round(payout, 2), profit=round(payout - stake, 2), settled_at=iso(now))
    return payout


# ---------- the run -----------------------------------------------------------------

def run(cfg, client, now=None, state_path=STATE, quiet=False):
    now = now or datetime.now(timezone.utc)
    log = (lambda *a: None) if quiet else print
    state = load_state(cfg, state_path)
    state["started"] = state["started"] or iso(now)
    s_cfg, k_cfg, b_cfg = cfg["strategy"], cfg["staking"], cfg["budget"]
    run_log = {"t": iso(now), "settled": 0, "placed": 0, "scanned": [], "skipped_for_budget": []}

    active = [s["key"] for s in client.sports() if s.get("active", True)]
    sports = resolve_sports(cfg, active)
    log(f"{len(sports)} configured sports in season: {', '.join(sports) or 'none'}")

    # 1. settle finished games (only call /scores for sports that need it)
    due = {}
    for b in state["open"]:
        if model.parse_iso(b["commence"]) + timedelta(hours=3) < now:
            due.setdefault(b["sport"], []).append(b)
    for sport, bets in due.items():
        try:
            rows = {r["id"]: r for r in client.scores(sport, days_from=3)}
        except Exception as e:  # never let one sport break the run
            log(f"  scores failed for {sport}: {e}")
            continue
        for b in bets:
            r = rows.get(b["event_id"])
            w = winner_of(r) if r and r.get("completed") else None
            if w:
                state["cash"] += settle_bet(b, w, now)
            elif model.parse_iso(b["commence"]) + timedelta(days=4) < now:
                b.update(result="void", payout=b["stake"], profit=0.0, settled_at=iso(now))
                state["cash"] += b["stake"]
            else:
                continue
            state["open"].remove(b)
            state["settled"].append(b)
            run_log["settled"] += 1
            log(f"  settled {b['selection']} @ {b['odds']}: {b['result']} ({b['profit']:+.2f})")

    # 2. decide which sports we can afford to price this run
    start = now + timedelta(minutes=s_cfg["min_minutes_to_start"])
    end = now + timedelta(hours=s_cfg["lookahead_hours"])
    remaining = client.remaining if client.remaining is not None else state.get("credits_remaining")
    days_left = calendar.monthrange(now.year, now.month)[1] - now.day + 1
    runs_left = max(1, days_left * b_cfg["runs_per_day"])
    cost = credits_per_odds_call(cfg)
    if remaining is None:
        calls_allowed = len(sports)
    else:
        calls_allowed = max(0, int((remaining - b_cfg["reserve"]) / runs_left // cost))
    open_sports = {b["sport"] for b in state["open"] if model.parse_iso(b["commence"]) > now}
    ranked = []
    for sport in sports:
        try:
            n = len(client.events(sport, start, end))
        except Exception as e:
            log(f"  events failed for {sport}: {e}")
            continue
        if n:
            ranked.append((sport in open_sports, n, sport))
    ranked.sort(reverse=True)
    chosen = [s for _, _, s in ranked[:calls_allowed]]
    run_log["skipped_for_budget"] = [s for _, _, s in ranked[calls_allowed:]]
    log(f"credits left {remaining}, can afford {calls_allowed} odds calls, pricing: {', '.join(chosen) or 'nothing'}")

    # 3. pull odds, update closing lines, find value
    candidates, scan = [], []
    open_by_event = {}
    for b in state["open"]:
        open_by_event.setdefault(b["event_id"], []).append(b)
    for sport in chosen:
        try:
            events = client.odds(sport, start, end, bookmakers=cfg.get("bookmakers"),
                                 regions=cfg.get("regions"), markets=cfg["markets"])
        except Exception as e:
            log(f"  odds failed for {sport}: {e}")
            continue
        run_log["scanned"].append(sport)
        for ev in events:
            cons = model.consensus_all(ev, s_cfg)
            for b in open_by_event.get(ev["id"], []):       # closing line value tracking
                if cons["fair"].get(b["selection"]) and model.parse_iso(ev["commence_time"]) > now:
                    b["close_fair_prob"] = cons["fair"][b["selection"]]
                    b["close_best_odds"] = cons["best"].get(b["selection"])
                    b["close_seen"] = iso(now)
            for v in model.find_value(ev, s_cfg):
                v.update(event_id=ev["id"], sport=sport, league=sports[sport]["name"],
                         country=sports[sport]["country"], commence=ev["commence_time"],
                         game=f"{ev['home_team']} v {ev['away_team']}",
                         outcomes=sorted(cons["fair"]))
                candidates.append(v)
    candidates.sort(key=lambda v: -v["edge"])

    # 4. place paper bets with Kelly staking inside the risk limits
    bank = equity(state)
    exposure = sum(b["stake"] for b in state["open"])
    bet_events = {b["event_id"] for b in state["open"]}
    seen = set()
    for v in candidates:
        key = (v["event_id"], v["selection"])
        if key in seen:      # best price for this selection only
            continue
        seen.add(key)
        status = "bet"
        stake = model.stake_for(v["fair_prob"], v["odds"], bank, k_cfg)
        if k_cfg.get("one_bet_per_game") and v["event_id"] in bet_events:
            status, stake = "already bet this game", 0
        elif not stake:
            status = "stake too small"
        elif exposure + stake > k_cfg["max_open_exposure_pct"] * bank:
            status, stake = "exposure limit", 0
        if stake and stake <= state["cash"]:
            bet = {**v, "id": uuid.uuid4().hex[:10], "placed": iso(now), "stake": stake,
                   "close_fair_prob": v["fair_prob"], "close_best_odds": v["odds"], "close_seen": iso(now)}
            state["open"].append(bet)
            state["cash"] -= stake
            exposure += stake
            bet_events.add(v["event_id"])
            run_log["placed"] += 1
            log(f"  BET ${stake:.2f} {v['selection']} @ {v['odds']} ({v['book']}) "
                f"{v['league']}: {v['game']}  fair {v['fair_odds']:.2f}, edge {v['edge']:+.1%}")
        scan.append({k: v[k] for k in ("league", "game", "selection", "book", "odds", "fair_odds", "edge", "commence")}
                    | {"status": status})

    # 5. record
    state["last_scan"] = scan[:40]
    state["credits_remaining"] = client.remaining
    run_log["credits_used"] = client.used
    state["runs"] = (state["runs"] + [run_log])[-200:]
    state["history"].append({"t": iso(now), "equity": round(equity(state), 2), "cash": round(state["cash"], 2)})
    save_state(state, state_path)
    log(f"done: {run_log['placed']} bets placed, {run_log['settled']} settled, "
        f"bankroll ${equity(state):.2f}, credits used {client.used}")
    return state


def summary(state):
    """Headline numbers used by the dashboard and the CLI."""
    s = [b for b in state["settled"] if b["result"] != "void"]
    turnover = sum(b["stake"] for b in s)
    profit = sum(b["profit"] for b in s)
    expected = sum(b["stake"] * b["edge"] for b in s)
    clv = [b["odds"] * b["close_fair_prob"] - 1 for b in s + state["open"] if b.get("close_fair_prob")]
    return {"bets": len(s), "open": len(state["open"]), "turnover": round(turnover, 2), "profit": round(profit, 2),
            "roi": profit / turnover if turnover else 0, "expected_profit": round(expected, 2),
            "win_rate": sum(b["result"] == "won" for b in s) / len(s) if s else 0,
            "avg_edge": sum(b["edge"] for b in s) / len(s) if s else 0,
            "avg_clv": sum(clv) / len(clv) if clv else 0, "beat_close": sum(c > 0 for c in clv) / len(clv) if clv else 0,
            "equity": round(equity(state), 2)}
