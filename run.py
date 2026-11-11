"""Sports betting paper-trading bot.

  python run.py run              one live run: settle, scan markets, place paper bets
  python run.py status           headline numbers
  python run.py backtest         replay the strategy on 6 seasons of European football
  python run.py dashboard        rebuild site/index.html
  python run.py sports           list in-season sport keys (free)
  python run.py books            list bookmaker keys (costs 4 credits)
  BOT_SYNTHETIC=1 python run.py simulate --days 60    fake markets, fast-forwarded, to test everything
"""
import argparse
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

from bets import backtest, dashboard, engine
from bets.oddsapi import FakeOddsAPI, make_client

ROOT = Path(__file__).resolve().parent


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("command", choices=["run", "status", "backtest", "dashboard", "sports", "books", "simulate"])
    ap.add_argument("--days", type=int, default=60)
    ap.add_argument("--sport", default="soccer_epl")
    a = ap.parse_args()
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text())
    now = datetime.now(timezone.utc)

    if a.command == "run":
        engine.run(cfg, make_client(now, cfg["budget"]["monthly_credits"]))
        dashboard.build(cfg)
    elif a.command == "status":
        for k, v in engine.summary(engine.load_state(cfg)).items():
            print(f"{k:16} {v:.4f}" if isinstance(v, float) else f"{k:16} {v}")
    elif a.command == "backtest":
        backtest.run(cfg)
        dashboard.build(cfg)
    elif a.command == "dashboard":
        dashboard.build(cfg)
    elif a.command == "sports":
        c = make_client(now, 0)
        for s in c.sports():
            print(f"{s['key']:40} {s.get('title', '')}")
        print(f"credits remaining: {c.remaining}")
    elif a.command == "books":
        for key, title in make_client(now, 0).all_books(a.sport):
            print(f"{key:24} {title}")
    elif a.command == "simulate":
        if os.environ.get("BOT_SYNTHETIC") != "1":
            raise SystemExit("simulate only works with fake data: BOT_SYNTHETIC=1 python run.py simulate")
        state_file = engine.STATE
        if state_file.exists():
            state_file.unlink()
        t = now - timedelta(days=a.days)
        fake = FakeOddsAPI(t, cfg["budget"]["monthly_credits"])
        month = t.month
        while t < now:
            if t.month != month:      # credits reset each month
                fake.remaining, month = cfg["budget"]["monthly_credits"], t.month
            fake.now, fake.used = t, 0
            engine.run(cfg, fake, now=t, quiet=True)
            t += timedelta(hours=24 / cfg["budget"]["runs_per_day"])
        for k, v in engine.summary(engine.load_state(cfg)).items():
            print(f"{k:16} {v}")
        dashboard.build(cfg)


if __name__ == "__main__":
    main()
