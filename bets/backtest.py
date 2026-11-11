"""Historical test of the same strategy on European football.

football-data.co.uk publishes free results with Pinnacle opening and closing odds
plus Bet365 and best-available ("Max") odds for every game. That lets us replay:
  fair price = Pinnacle opening odds with the margin removed
  bet if Bet365 (or the best price anywhere) beats it by the edge threshold
  judge it on results AND on closing line value (did Pinnacle move our way?)
"""
from __future__ import annotations

import io
import json
from pathlib import Path

import pandas as pd

from . import model

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "data"
OUT = ROOT / "state" / "backtest.json"
URL = "https://www.football-data.co.uk/mmz4281/{season}/{league}.csv"


def load(league, season):
    CACHE.mkdir(exist_ok=True)
    f = CACHE / f"{league}_{season}.csv"
    if not f.exists():
        import requests
        r = requests.get(URL.format(season=season, league=league), timeout=60)
        r.raise_for_status()
        f.write_bytes(r.content)
    df = pd.read_csv(io.BytesIO(f.read_bytes()), encoding="latin-1", on_bad_lines="skip")
    df["Date"] = pd.to_datetime(df["Date"], dayfirst=True, errors="coerce")
    return df


def replay(games: pd.DataFrame, book_cols, cfg, bankroll):
    s_cfg, k_cfg = cfg["strategy"], cfg["staking"]
    bank, bets, curve = bankroll, [], []
    for _, g in games.iterrows():
        try:
            pin = [float(g["PSH"]), float(g["PSD"]), float(g["PSA"])]
            close = [float(g["PSCH"]), float(g["PSCD"]), float(g["PSCA"])]
            offer = [float(g[c]) for c in book_cols]
        except (KeyError, ValueError, TypeError):
            continue
        if any(pd.isna(x) or x <= 1 for x in pin + close + offer):
            continue
        fair = model.devig(pin, s_cfg["devig"])
        fair_close = model.devig(close, s_cfg["devig"])
        best = None
        for i, (p, o) in enumerate(zip(fair, offer)):
            ev = p * o - 1
            if s_cfg["min_edge"] <= ev <= s_cfg["max_edge"] and s_cfg["min_odds"] <= o <= s_cfg["max_odds"]:
                if not best or ev > best[1]:
                    best = (i, ev, p, o)
        if not best:
            continue
        i, ev, p, o = best
        stake = model.stake_for(p, o, bank, k_cfg)
        if not stake:
            continue
        won = "HDA"[i] == g["FTR"]
        profit = stake * (o - 1) if won else -stake
        bank += profit
        bets.append({"stake": stake, "profit": profit, "edge": ev, "clv": o * fair_close[i] - 1, "won": won})
        curve.append([g["Date"].strftime("%Y-%m-%d"), round(bank, 2)])
    return bets, curve, bank


def stats(bets, bank, start):
    t = sum(b["stake"] for b in bets)
    return {"bets": len(bets), "turnover": round(t, 2), "profit": round(sum(b["profit"] for b in bets), 2),
            "roi": sum(b["profit"] for b in bets) / t if t else 0,
            "expected_roi": sum(b["stake"] * b["edge"] for b in bets) / t if t else 0,
            "avg_clv": sum(b["clv"] for b in bets) / len(bets) if bets else 0,
            "beat_close": sum(b["clv"] > 0 for b in bets) / len(bets) if bets else 0,
            "win_rate": sum(b["won"] for b in bets) / len(bets) if bets else 0,
            "final_bankroll": round(bank, 2), "start": start}


def run(cfg, out=OUT):
    frames = []
    for league, name in cfg["backtest"]["leagues"].items():
        for season in cfg["backtest"]["seasons"]:
            try:
                df = load(league, season)
                df["League"] = name
                frames.append(df)
            except Exception as e:
                print(f"  skip {league} {season}: {e}")
    if not frames:
        raise SystemExit("no historical data downloaded")
    games = pd.concat(frames, ignore_index=True).dropna(subset=["Date", "FTR"]).sort_values("Date")
    print(f"{len(games)} games loaded")
    result = {"games": len(games), "from": games["Date"].min().strftime("%Y-%m-%d"),
              "to": games["Date"].max().strftime("%Y-%m-%d"), "strategies": []}
    variants = [("Bet365 only (realistic: one soft bookmaker)", ["B365H", "B365D", "B365A"]),
                ("Best price anywhere (upper bound: needs accounts everywhere)", ["MaxH", "MaxD", "MaxA"])]
    for label, cols in variants:
        if not all(c in games for c in cols):
            continue
        bets, curve, bank = replay(games, cols, cfg, cfg["bankroll"])
        st = stats(bets, bank, cfg["bankroll"])
        step = max(1, len(curve) // 300)
        result["strategies"].append({"name": label, **st, "curve": curve[::step] + curve[-1:]})
        print(f"{label}: {st['bets']} bets, ROI {st['roi']:+.2%}, CLV {st['avg_clv']:+.2%}, "
              f"bankroll {cfg['bankroll']} -> {st['final_bankroll']}")
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(result))
    return result
