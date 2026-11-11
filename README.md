# Betting Bot: value betting on the main sports, paper money only

Once a day this bot reads bookmaker odds for the main leagues in Australia,
England and Europe, and the USA. It works out what each team's real chance is
and places **pretend** bets wherever an Australian bookmaker is paying more than
that chance justifies. It runs free in the cloud on GitHub Actions and publishes
a dashboard. No real money is involved.

## The maths

1. **Remove the margin.** A bookmaker's odds add up to more than 100% (e.g. 104%).
   The extra is their cut. The bot strips it out to get the probabilities the
   book really believes. Three methods are available: `multiplicative`,
   `power` (default) and `shin`. Power and Shin shave long shots more than
   favourites, which matches the well-documented favourite–longshot bias.
2. **Build a fair price.** The bot takes a weighted average of every book's
   probabilities. Sharp books (Pinnacle ×4, Betfair exchange ×3) count most
   because their prices predict results best. The book being judged is left
   out of its own comparison. Stale prices are dropped.
3. **Find value.** Expected value = fair probability × odds − 1. The bot bets
   when that is at least **+3%** at odds of 1.30–5.00. Anything above +20% is
   ignored, because it's almost always a stale or wrong price.
4. **Size the bet.** Quarter-Kelly staking, capped at 2% of bankroll per bet,
   at most 20% of the bankroll on open bets, and one bet per game.
5. **Keep score honestly.** Every bet records its **closing line value (CLV)**:
   how its odds compare with the fair price just before the game. Profit over
   a hundred bets is mostly luck. Consistently beating the closing line is the
   evidence professionals use to tell whether an edge is real.

All of these numbers are in `config.yaml`.

## Sports covered

AFL, NRL, A-League, NBL and Big Bash; the Premier League, La Liga, Bundesliga,
Serie A, Ligue 1 and Champions League; the NFL, NBA, MLB and NHL. ATP and WTA
tennis are available but switched off because they use a lot of credits.
Out-of-season leagues are skipped automatically.

## Odds data and the free plan

Odds come from [The Odds API](https://the-odds-api.com). Its free plan gives
500 credits a month. Each sport priced in a run costs 1 credit (the 10
bookmakers in `config.yaml` count as one region). Checking results costs 2
credits per sport with finished bets. The bot spreads credits evenly across
the month. When it can't afford every sport, it prices the ones with the most
games coming up first, and lists the rest on the dashboard as skipped. With
about 10 leagues in season, the free plan covers one run a day.

## Run it on your computer

```bash
pip install -r requirements.txt
BOT_SYNTHETIC=1 python run.py simulate --days 60   # fake markets, 60 days fast-forwarded
open site/index.html                               # look at the dashboard
rm state/ledger.json                               # clear the fake bets before going live

export ODDS_API_KEY=your_key
python run.py sports        # what's in season (free)
python run.py run           # one real run
python run.py backtest      # downloads ~30 CSVs from football-data.co.uk
```

## Put it in the cloud (same steps as the trading bots)

1. Make a new **public** GitHub repo (e.g. `Punting-Bot`) and push this folder.
2. Get a free key at [the-odds-api.com](https://the-odds-api.com). Add it under
   Settings → Secrets and variables → Actions → New secret, named `ODDS_API_KEY`.
3. Settings → Pages → Source: **GitHub Actions**.
4. Settings → Actions → General → Workflow permissions → **Read and write**.
5. Actions → *betting-bot* → Run workflow → `backtest` once, then `run`.

After that it runs every evening at about 6:45pm Darwin time. Every bet is
committed to `state/ledger.json`, so the full history is in git.

## The historical test

`python run.py backtest` replays the same rules on six seasons of the top 5
European football leagues. It uses free data from football-data.co.uk, which
includes Pinnacle's opening and closing odds. It runs two versions:

- **Bet365 only:** the realistic version, with one soft bookmaker.
- **Best price anywhere:** an upper bound that needs accounts at every bookmaker.

If even the best-price version can't make money historically, don't expect the
live bot to.

## Honest caveats

- Bookmakers beat almost everyone in the long run. A 3% edge after their
  margin is small, and luck swamps it for hundreds of bets. Judge the bot on CLV
  over a few months, not on its profit.
- Australian bookmakers routinely restrict or close accounts that keep winning.
  Paper results here assume your bets would always be accepted at full stake.
- With one run a day, the "closing" price is the last price seen before
  kick-off, which can be up to 24 hours early. The paid plan lets you run it
  more often for a truer closing line.
- Only head-to-head (match winner) markets are covered. Lines, totals and
  player props are left out to save credits.
- This is a learning project, not betting advice. If you ever bet for real,
  set a limit. Free support is at
  [gamblinghelponline.org.au](https://www.gamblinghelponline.org.au), 1800 858 858.

## Files

```
config.yaml          sports, bookmakers, credit budget, maths and staking settings
run.py               CLI: run | status | backtest | dashboard | sports | books | simulate
bets/oddsapi.py      The Odds API client + offline simulator
bets/model.py        margin removal, fair price, value finder, Kelly staking
bets/engine.py       one run: settle, budget, scan, bet, track closing line
bets/backtest.py     historical replay on football-data.co.uk
bets/dashboard.py    builds site/index.html
.github/workflows/   daily cloud schedule
```
