#!/usr/bin/env python3
"""
Daily NBA box scores, stats only (no fantasy rosters needed).

Saves every player's line from last night's games to reports/boxscores_<date>.csv.
Uses NBA.com's public box score files and falls back to ESPN if NBA.com refuses.

Usage:
  python nba_boxscores.py                    # last night (US Eastern date)
  python nba_boxscores.py --date 2026-10-21  # a specific night
Only the Python standard library is needed (Python 3.8+).
"""
import argparse, csv, datetime as dt, json, os, re, sys, time, unicodedata, urllib.request

NBA = "https://cdn.nba.com/static/json"
NBA_SCHEDULES = [NBA + "/staticData/scheduleLeagueV2_1.json", NBA + "/staticData/scheduleLeagueV2.json"]
NBA_BOX = NBA + "/liveData/boxscore/boxscore_{}.json"
ESPN_SCOREBOARD = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard?dates={}"
ESPN_SUMMARY = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/summary?event={}"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nba.com/",
    "Origin": "https://www.nba.com",
}
HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, "cache")
REPORTS = os.path.join(HERE, "reports")
CATS = ["fg%", "ft%", "3pm", "pts", "reb", "ast", "stl", "blk", "to"]


# ---------- helpers ----------
def norm(name):
    s = unicodedata.normalize("NFD", str(name))
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    s = re.sub(r"[.'\u2019-]", " ", s)
    s = re.sub(r"\b(jr|sr|ii|iii|iv)\b", "", s)
    s = " ".join(s.split())
    return ALIASES.get(s, s)


# Different spellings used by Yahoo / Hashtag / NBA.com / ESPN -> one name
_ALIAS_PAIRS = [
    ("alexandre sarr", "alex sarr"), ("nicolas claxton", "nic claxton"), ("herbert jones", "herb jones"),
    ("cameron johnson", "cam johnson"), ("moritz wagner", "moe wagner"), ("carlton carrington", "bub carrington"),
    ("luguentz dort", "lu dort"), ("ronald holland", "ron holland"), ("gregory jackson", "gg jackson"),
    ("jakob poeltl", "jakob poltl"), ("dennis schroder", "dennis schroeder"), ("nah shon hyland", "bones hyland"),
    ("kenyon martin", "kj martin"), ("cameron thomas", "cam thomas"), ("pj washington", "p j washington"),
    ("sviatoslav mykhailiuk", "svi mykhailiuk"), ("vit krejci", "vit krejci"), ("tidjane salaun", "tidjane salaun"),
]
ALIASES = {b: a for a, b in _ALIAS_PAIRS}


def get_json(url, cache_file=None, max_age=None):
    if cache_file and os.path.exists(cache_file):
        if max_age is None or time.time() - os.path.getmtime(cache_file) < max_age:
            with open(cache_file, encoding="utf-8") as f:
                return json.load(f)
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=30) as r:
        data = json.loads(r.read().decode("utf-8"))
    if cache_file:
        os.makedirs(os.path.dirname(cache_file), exist_ok=True)
        with open(cache_file, "w", encoding="utf-8") as f:
            json.dump(data, f)
    return data


def minutes(s):
    m = re.match(r"PT(\d+)M([\d.]+)S", s or "")
    return int(m.group(1)) + float(m.group(2)) / 60 if m else 0.0


def parse_date(s):
    for fmt in ("%m/%d/%Y %H:%M:%S", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d"):
        try:
            return dt.datetime.strptime(s[:19] if "T" in s and "Z" not in s else s, fmt).date()
        except ValueError:
            pass
    return dt.date.fromisoformat(s[:10])


# ---------- data ----------
SOURCE = {"name": None}  # "nba" or "espn", chosen on first use


def _num(x):
    try:
        return int(float(x))
    except (TypeError, ValueError):
        return 0


def _pair(x):
    m = re.match(r"\s*(\d+)\s*-\s*(\d+)", str(x or ""))
    return (int(m.group(1)), int(m.group(2))) if m else (0, 0)


def _nba_schedule():
    last = None
    for url in NBA_SCHEDULES:
        try:
            return get_json(url, os.path.join(CACHE, "schedule.json"), max_age=12 * 3600)
        except Exception as e:
            last = e
    raise last


def day_games(day):
    """Games on a US date: list of {id, home, away, hs, as}. Tries NBA.com, falls back to ESPN."""
    if SOURCE["name"] in (None, "nba"):
        try:
            sched = _nba_schedule()
            SOURCE["name"] = "nba"
            out = []
            for gd in sched["leagueSchedule"]["gameDates"]:
                if parse_date(gd["gameDate"]) != day:
                    continue
                for g in gd["games"]:
                    if str(g.get("gameId", "")).startswith("002"):
                        out.append({"id": g["gameId"], "home": g["homeTeam"].get("teamTricode"),
                                    "away": g["awayTeam"].get("teamTricode"),
                                    "hs": g["homeTeam"].get("score", ""), "as": g["awayTeam"].get("score", "")})
            return out
        except Exception as e:
            if SOURCE["name"] == "nba":
                raise
            print(f"  NBA.com not reachable ({e}); using ESPN instead.", file=sys.stderr)
            SOURCE["name"] = "espn"
    sb = get_json(ESPN_SCOREBOARD.format(day.strftime("%Y%m%d")),
                  os.path.join(CACHE, f"espn_sb_{day}.json"), max_age=3 * 3600)
    out = []
    for ev in sb.get("events", []):
        stype = (ev.get("season") or {}).get("type")
        if stype is not None and int(stype) != 2:  # skip preseason / All-Star
            continue
        comp = ev["competitions"][0]
        teams = {c["homeAway"]: c for c in comp["competitors"]}
        out.append({"id": ev["id"], "home": teams["home"]["team"].get("abbreviation"),
                    "away": teams["away"]["team"].get("abbreviation"),
                    "hs": teams["home"].get("score", ""), "as": teams["away"].get("score", ""),
                    "final": bool(ev.get("status", {}).get("type", {}).get("completed"))})
    return out


def box_lines(game):
    """Player stat dicts for a finished game (finished games are cached forever)."""
    if SOURCE["name"] == "nba":
        cf = os.path.join(CACHE, f"box_{game['id']}.json")
        data = get_json(NBA_BOX.format(game["id"]), cf)
        g = data["game"]
        if int(g.get("gameStatus", 0)) != 3:
            os.remove(cf)
            return []
        rows = []
        for side in ("homeTeam", "awayTeam"):
            team = g[side]["teamTricode"]
            for p in g[side].get("players", []):
                s = p.get("statistics", {})
                rows.append({
                    "name": p.get("name") or f"{p.get('firstName','')} {p.get('familyName','')}".strip(),
                    "team": team, "played": str(p.get("played", "0")) == "1", "status": p.get("status", ""),
                    "reason": p.get("notPlayingReason", "") or p.get("notPlayingDescription", ""),
                    "min": round(minutes(s.get("minutes")), 1),
                    "pts": s.get("points", 0), "reb": s.get("reboundsTotal", 0), "ast": s.get("assists", 0),
                    "stl": s.get("steals", 0), "blk": s.get("blocks", 0), "to": s.get("turnovers", 0),
                    "3pm": s.get("threePointersMade", 0),
                    "fgm": s.get("fieldGoalsMade", 0), "fga": s.get("fieldGoalsAttempted", 0),
                    "ftm": s.get("freeThrowsMade", 0), "fta": s.get("freeThrowsAttempted", 0)})
        return rows
    # ESPN
    if not game.get("final", True):
        return []
    cf = os.path.join(CACHE, f"espn_box_{game['id']}.json")
    data = get_json(ESPN_SUMMARY.format(game["id"]), cf)
    rows = []
    for tb in data.get("boxscore", {}).get("players", []):
        team = tb.get("team", {}).get("abbreviation", "")
        for st in tb.get("statistics", []):
            labels = [str(x).upper() for x in (st.get("labels") or st.get("names") or [])]
            idx = {lab: i for i, lab in enumerate(labels)}
            for a in st.get("athletes", []):
                vals = a.get("stats") or []
                def v(lab):
                    i = idx.get(lab)
                    return vals[i] if i is not None and i < len(vals) else None
                played = bool(vals) and not a.get("didNotPlay", False)
                fgm, fga = _pair(v("FG"))
                tpm, _ = _pair(v("3PT"))
                ftm, fta = _pair(v("FT"))
                rows.append({
                    "name": a.get("athlete", {}).get("displayName", ""), "team": team, "played": played,
                    "status": "", "reason": a.get("reason", "") if not played else "",
                    "min": float(_num(v("MIN"))), "pts": _num(v("PTS")), "reb": _num(v("REB")),
                    "ast": _num(v("AST")), "stl": _num(v("STL")), "blk": _num(v("BLK")), "to": _num(v("TO")),
                    "3pm": tpm, "fgm": fgm, "fga": fga, "ftm": ftm, "fta": fta})
    return rows


def lines_for_dates(dates):
    out = []
    for d in dates:
        try:
            games = day_games(d)
        except Exception as e:
            print(f"  (could not get games for {d}: {e})", file=sys.stderr)
            continue
        for g in games:
            try:
                for r in box_lines(g):
                    r["date"] = d.isoformat()
                    out.append(r)
            except Exception as e:  # game not played yet / temporary error
                print(f"  (skipped game {g.get('id')}: {e})", file=sys.stderr)
    return out



FIELDS = ["date", "player", "team", "played", "reason", "min", "pts", "reb", "ast", "stl", "blk", "3pm", "to",
          "fgm", "fga", "ftm", "fta"]


def main():
    ap = argparse.ArgumentParser(description="Save every player's box score line for one NBA night as CSV.")
    ap.add_argument("--date", help="US game date YYYY-MM-DD (default: last night, US Eastern)")
    ap.add_argument("--days", type=int, default=2,
                    help="also (re)build this many earlier nights if their file is missing (default 2)")
    a = ap.parse_args()
    # Latest US night whose games are surely over: 12 hours back from now (UTC) gives the right
    # date from roughly 01:00 to 13:00 Athens time, whatever time the job runs.
    last = dt.date.fromisoformat(a.date) if a.date else \
        (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=12)).date()
    os.makedirs(REPORTS, exist_ok=True)
    for i in range(a.days, -1, -1):
        day = last - dt.timedelta(days=i)
        path = os.path.join(REPORTS, f"boxscores_{day}.csv")
        if i > 0 and os.path.exists(path):
            continue
        rows = lines_for_dates([day])
        if not rows:
            print(f"{day}: no finished games")
            continue
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(FIELDS)
            for r in sorted(rows, key=lambda r: (r["team"], -r["min"])):
                w.writerow([day.isoformat(), r["name"], r["team"], r["played"], r["reason"], r["min"], r["pts"],
                            r["reb"], r["ast"], r["stl"], r["blk"], r["3pm"], r["to"], r["fgm"], r["fga"],
                            r["ftm"], r["fta"]])
        print(f"{day}: saved {len(rows)} player lines ({'NBA.com' if SOURCE['name'] == 'nba' else 'ESPN'}) -> {path}")


if __name__ == "__main__":
    main()
