"""The Odds API client (https://the-odds-api.com/liveapi/guides/v4/), plus an offline fake.

Costs on The Odds API:
  /sports, /events          free
  /odds                     markets x regions credits (every 10 bookmakers = 1 region)
                            a call that returns no games costs nothing
  /scores?daysFrom=3        2 credits
"""
from __future__ import annotations

import hashlib
import math
import os
import random
from datetime import datetime, timedelta, timezone

BASE = "https://api.the-odds-api.com/v4"


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class OddsAPI:
    def __init__(self, key: str):
        import requests
        self.key, self.http = key, requests.Session()
        self.remaining = None
        self.used = 0          # credits spent this run

    def _get(self, path, **params):
        params["apiKey"] = self.key
        r = self.http.get(f"{BASE}{path}", params=params, timeout=30)
        if "OUT_OF_USAGE" in r.text:
            raise RuntimeError("out of Odds API credits for this month")
        if r.status_code == 401:
            raise SystemExit("The Odds API rejected the key (401). Check the ODDS_API_KEY secret.")
        r.raise_for_status()
        if "x-requests-remaining" in r.headers:
            self.remaining = float(r.headers["x-requests-remaining"])
            self.used += float(r.headers.get("x-requests-last", 0) or 0)
        return r.json()

    def sports(self):
        return self._get("/sports")

    def events(self, sport, start, end):
        return self._get(f"/sports/{sport}/events", commenceTimeFrom=iso(start), commenceTimeTo=iso(end))

    def odds(self, sport, start, end, bookmakers=None, regions=None, markets=("h2h",)):
        p = dict(markets=",".join(markets), oddsFormat="decimal", dateFormat="iso",
                 commenceTimeFrom=iso(start), commenceTimeTo=iso(end))
        if bookmakers:
            p["bookmakers"] = ",".join(bookmakers)
        else:
            p["regions"] = ",".join(regions or ["au"])
        return self._get(f"/sports/{sport}/odds", **p)

    def scores(self, sport, days_from=3):
        return self._get(f"/sports/{sport}/scores", daysFrom=days_from, dateFormat="iso")

    def all_books(self, sport):
        """Which bookmaker keys exist (uses 4 credits: one call across every region)."""
        now = datetime.now(timezone.utc)
        data = self.odds(sport, now, now + timedelta(days=7), regions=["au", "uk", "eu", "us"])
        return sorted({(b["key"], b["title"]) for e in data for b in e["bookmakers"]})


# ---------------------------------------------------------------------------
# Offline fake: realistic-looking markets so everything can be tested without a key.
# A hidden "true" probability decides results; Pinnacle sits close to it, soft
# books are noisier with bigger margins, so there is genuine value to find.
# ---------------------------------------------------------------------------

FAKE_TEAMS = {
    "aussierules_afl": ["Hawthorn", "Geelong", "Brisbane", "Collingwood", "Carlton", "Sydney", "Fremantle", "Melbourne"],
    "rugbyleague_nrl": ["Storm", "Panthers", "Broncos", "Roosters", "Sharks", "Cowboys", "Eels", "Raiders"],
    "soccer_epl": ["Arsenal", "Liverpool", "Man City", "Chelsea", "Spurs", "Newcastle", "Villa", "Brighton"],
    "soccer_spain_la_liga": ["Real Madrid", "Barcelona", "Atletico", "Sevilla", "Betis", "Valencia"],
    "basketball_nba": ["Celtics", "Nuggets", "Lakers", "Bucks", "Knicks", "Thunder", "Suns", "Heat"],
    "basketball_nbl": ["Wildcats", "Kings", "36ers", "Phoenix", "Breakers", "Taipans"],
    "icehockey_nhl": ["Oilers", "Panthers", "Rangers", "Stars", "Avalanche", "Bruins"],
}
FAKE_BOOKS = ["pinnacle", "betfair_ex_au", "sportsbet", "tab", "ladbrokes_au", "neds",
              "pointsbetau", "betr_au", "unibet", "bet365_au"]


def _rng(*parts):
    return random.Random(int(hashlib.md5("|".join(map(str, parts)).encode()).hexdigest()[:12], 16))


class FakeOddsAPI:
    def __init__(self, now: datetime, monthly=500):
        self.now, self.remaining, self.used = now, monthly, 0

    def _spend(self, n):
        self.used += n
        self.remaining -= n

    def sports(self):
        return [{"key": k, "title": k, "active": True} for k in FAKE_TEAMS]

    def _events_for_day(self, sport, day):
        r = _rng(sport, day.isoformat())
        teams = FAKE_TEAMS[sport]
        out = []
        for i in range(r.choice([0, 1, 2, 3])):
            h, a = r.sample(teams, 2)
            start = datetime(day.year, day.month, day.day, tzinfo=timezone.utc) + timedelta(hours=r.uniform(2, 22))
            draw = sport.startswith("soccer")
            strength = r.gauss(0.25, 0.9)
            ph = 1 / (1 + math.exp(-strength))
            if draw:
                pd = 0.27 - 0.1 * abs(ph - 0.5)
                probs = {h: ph * (1 - pd), "Draw": pd, a: (1 - ph) * (1 - pd)}
            else:
                probs = {h: ph, a: 1 - ph}
            out.append({"id": f"{sport}-{day.isoformat()}-{i}", "sport_key": sport, "commence_time": iso(start),
                        "home_team": h, "away_team": a, "_true": probs})
        return out

    def _events(self, sport, start, end):
        out, d = [], (start - timedelta(days=1)).date()
        while d <= end.date():
            out += [e for e in self._events_for_day(sport, d) if start <= datetime.fromisoformat(e["commence_time"][:-1] + "+00:00") <= end]
            d += timedelta(days=1)
        return out

    def events(self, sport, start, end):
        return [{k: v for k, v in e.items() if k != "_true"} for e in self._events(sport, start, end)]

    def odds(self, sport, start, end, bookmakers=None, regions=None, markets=("h2h",)):
        evs = self._events(sport, start, end)
        if evs:
            self._spend(max(1, math.ceil(len(bookmakers or FAKE_BOOKS) / 10)) * len(markets))
        out = []
        for e in evs:
            hours_left = (datetime.fromisoformat(e["commence_time"][:-1] + "+00:00") - self.now).total_seconds() / 3600
            books = []
            for b in (bookmakers or FAKE_BOOKS):
                r = _rng(e["id"], b, self.now.strftime("%Y%m%d%H"))
                sharp = b in ("pinnacle", "betfair_ex_au")
                noise = (0.010 if sharp else 0.022) * min(1.0, 0.3 + hours_left / 48)
                margin = 0.025 if sharp else r.uniform(0.045, 0.075)
                raw = {k: max(0.02, p * math.exp(r.gauss(0, noise / max(p, 0.2)))) for k, p in e["_true"].items()}
                s = sum(raw.values())
                outs = [{"name": k, "price": round(1 / (v / s * (1 + margin)), 2)} for k, v in raw.items()]
                books.append({"key": b, "title": b, "last_update": iso(self.now),
                              "markets": [{"key": "h2h", "last_update": iso(self.now), "outcomes": outs}]})
            out.append({**{k: v for k, v in e.items() if k != "_true"}, "bookmakers": books})
        return out

    def scores(self, sport, days_from=3):
        self._spend(2)
        out = []
        for e in self._events(sport, self.now - timedelta(days=days_from), self.now):
            start = datetime.fromisoformat(e["commence_time"][:-1] + "+00:00")
            done = self.now > start + timedelta(hours=3)
            scores = None
            if done:
                r = _rng(e["id"], "result")
                x, acc, winner = r.random(), 0, None
                for k, p in e["_true"].items():
                    acc += p
                    if x <= acc and winner is None:
                        winner = k
                winner = winner or list(e["_true"])[-1]
                hs, as_ = (2, 1) if winner == e["home_team"] else (1, 2) if winner == e["away_team"] else (1, 1)
                scores = [{"name": e["home_team"], "score": str(hs)}, {"name": e["away_team"], "score": str(as_)}]
            out.append({"id": e["id"], "sport_key": sport, "commence_time": e["commence_time"], "completed": done,
                        "home_team": e["home_team"], "away_team": e["away_team"], "scores": scores})
        return out


def make_client(now: datetime, monthly: int):
    if os.environ.get("BOT_SYNTHETIC") == "1":
        return FakeOddsAPI(now, monthly)
    key = os.environ.get("ODDS_API_KEY")
    if not key:
        raise SystemExit("Set ODDS_API_KEY (free key at the-odds-api.com), or BOT_SYNTHETIC=1 for fake data.")
    return OddsAPI(key)
