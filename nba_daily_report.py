#!/usr/bin/env python3
"""
Daily fantasy basketball report (9-cat H2H) from NBA's public box score files.

Reads complete box scores from cdn.nba.com for last night's games and writes:
  * your players' lines
  * your weekly opponent's players' lines
  * week-to-date category totals: you vs. your opponent
  * the best free agents last night and over the last few days

Usage:
  python nba_daily_report.py                 # last night's games (US Eastern date)
  python nba_daily_report.py --date 2026-10-21
Output goes to ./reports/ (Markdown + CSV) and is printed to the screen.
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


# ---------- fantasy logic ----------
def week_of(day, cfg):
    w1s = dt.date.fromisoformat(cfg["week1_start"])
    w1e = dt.date.fromisoformat(cfg["week1_end"])
    if day <= w1e:
        return 1, w1s, w1e
    n = 2 + (day - (w1e + dt.timedelta(days=1))).days // 7
    start = w1e + dt.timedelta(days=1 + 7 * (n - 2))
    return n, start, start + dt.timedelta(days=6)


def totals(rows):
    t = {k: 0 for k in ["pts", "reb", "ast", "stl", "blk", "to", "3pm", "fgm", "fga", "ftm", "fta"]}
    for r in rows:
        if r["played"]:
            for k in t:
                t[k] += r[k]
    t["fg%"] = t["fgm"] / t["fga"] if t["fga"] else 0
    t["ft%"] = t["ftm"] / t["fta"] if t["fta"] else 0
    return t


def fantasy_score(r, punt):
    """Rough single-game 9-cat value, used only to rank free agents."""
    w = {"pts": 0.12, "reb": 0.25, "ast": 0.25, "stl": 1.0, "blk": 1.0, "3pm": 0.6, "to": -0.4}
    for k in punt:
        w[k] = 0
    v = sum(w[k] * r[k] for k in w)
    if "fg%" not in punt:
        v += 0.3 * (r["fgm"] - 0.47 * r["fga"])
    if "ft%" not in punt:
        v += 0.3 * (r["ftm"] - 0.78 * r["fta"])
    return round(v, 2)


def fmt_line(r):
    if not r["played"]:
        why = r["reason"] or r["status"] or "did not play"
        return f"| {r['name']} | {r['team']} | DNP ({why}) |" + " |" * 10
    return (f"| {r['name']} | {r['team']} | {r['min']:.0f} | {r['pts']} | {r['reb']} | {r['ast']} | {r['stl']} | "
            f"{r['blk']} | {r['3pm']} | {r['fgm']}/{r['fga']} | {r['ftm']}/{r['fta']} | {r['to']} |")


HEAD = "| Player | Team | MIN | PTS | REB | AST | STL | BLK | 3PM | FG | FT | TO |\n|---|---|---|---|---|---|---|---|---|---|---|---|"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="game date (US), YYYY-MM-DD. Default: yesterday in US Eastern time")
    ap.add_argument("--config", default=os.path.join(HERE, "league.json"))
    a = ap.parse_args()

    with open(a.config, encoding="utf-8") as f:
        cfg = json.load(f)
    # US Eastern "yesterday": UTC-5 is close enough for picking the date at 8am Athens time.
    day = dt.date.fromisoformat(a.date) if a.date else (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=5)).date() - dt.timedelta(days=1)

    owner = {}
    for team, names in cfg["rosters"].items():
        for n in names:
            owner[norm(n)] = team
    me = cfg["my_team"]
    wk, wstart, wend = week_of(day, cfg)
    opp = cfg["schedule"][wk - 1] if wk - 1 < len(cfg["schedule"]) else None
    punt = cfg.get("punt", [])

    print(f"Fetching NBA data for {day} (week {wk}: {wstart} to {wend})...")
    last_night = lines_for_dates([day])
    week_days = [wstart + dt.timedelta(days=i) for i in range((min(day, wend) - wstart).days + 1)]
    week_rows = lines_for_dates(week_days)
    look = cfg.get("free_agent_lookback_days", 7)
    season_start = dt.date.fromisoformat(cfg["week1_start"])
    recent_days = [d for d in (day - dt.timedelta(days=i) for i in range(look)) if d >= season_start]
    recent_rows = lines_for_dates(sorted(recent_days))

    def mine(r): return owner.get(norm(r["name"])) == me
    def theirs(r): return opp and owner.get(norm(r["name"])) == opp
    def free(r): return norm(r["name"]) not in owner

    try:
        games = day_games(day)
    except Exception:
        games = []
    out = [f"# Daily report: games of {day} (week {wk}{', vs ' + opp if opp else ''})", ""]
    if games:
        out += ["**Games:** " + " · ".join(f"{g['away']} {g['as']} @ {g['home']} {g['hs']}" for g in games),
                f"(data source: {'NBA.com' if SOURCE['name'] == 'nba' else 'ESPN'})", ""]
    else:
        out += ["No NBA games found for that day.", ""]

    my_last = [r for r in last_night if mine(r)]
    out += [f"## {me}: last night", HEAD] + [fmt_line(r) for r in my_last] + [""]
    played_names = {norm(r["name"]) for r in last_night}
    idle = [n for n in cfg["rosters"].get(me, []) if norm(n) not in played_names]
    if idle:
        out += ["No game / not in a box score: " + ", ".join(idle), ""]

    if opp:
        out += [f"## {opp}: last night", HEAD] + [fmt_line(r) for r in last_night if theirs(r)] + [""]
        a_t = totals([r for r in week_rows if mine(r)])
        b_t = totals([r for r in week_rows if theirs(r)])
        out += [f"## Week {wk} so far ({wstart} to {min(day, wend)})",
                f"| Cat | {me} | {opp} | Leader |", "|---|---|---|---|"]
        lead_me = 0
        for c in CATS:
            x, y = a_t[c], b_t[c]
            if c in ("fg%", "ft%"):
                xs, ys = f"{x:.3f}", f"{y:.3f}"
            else:
                xs, ys = str(x), str(y)
            if x == y:
                who = "tie"
            elif (x > y) != (c == "to"):
                who, lead_me = me, lead_me + 1
            else:
                who = opp
            out.append(f"| {c.upper()} | {xs} | {ys} | {who} |")
        out += ["", f"You lead {lead_me} of 9 categories. (Counts every game by rostered players, "
                    "so bench games and daily lineup limits are not reflected.)", ""]

    fa_last = sorted([r for r in last_night if free(r) and r["played"]], key=lambda r: -fantasy_score(r, punt))[:15]
    head_score = HEAD.split("\n")[0] + " Score |\n" + HEAD.split("\n")[1] + "---|"
    out += ["## Best free agents last night", head_score]
    out += [fmt_line(r) + f" {fantasy_score(r, punt)} |" for r in fa_last] + [""]

    agg = {}
    for r in recent_rows:
        if not (free(r) and r["played"]):
            continue
        k = norm(r["name"])
        a_ = agg.setdefault(k, {"name": r["name"], "team": r["team"], "g": 0, "score": 0.0, **{c: 0 for c in ["min", "pts", "reb", "ast", "stl", "blk", "3pm", "to", "fgm", "fga", "ftm", "fta"]}})
        a_["g"] += 1
        a_["score"] += fantasy_score(r, punt)
        for c in ["min", "pts", "reb", "ast", "stl", "blk", "3pm", "to", "fgm", "fga", "ftm", "fta"]:
            a_[c] += r[c]
    best = sorted(agg.values(), key=lambda x: -x["score"] / x["g"])[:20]
    out += [f"## Best free agents over the last {look} days (per game)",
            "| Player | Team | G | MIN | PTS | REB | AST | STL | BLK | 3PM | FG% | FT% | TO | Score/G |",
            "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for x in best:
        g = x["g"]
        fg = f"{x['fgm']/x['fga']:.3f}" if x["fga"] else "-"
        ft = f"{x['ftm']/x['fta']:.3f}" if x["fta"] else "-"
        out.append(f"| {x['name']} | {x['team']} | {g} | {x['min']/g:.1f} | {x['pts']/g:.1f} | {x['reb']/g:.1f} | "
                   f"{x['ast']/g:.1f} | {x['stl']/g:.1f} | {x['blk']/g:.1f} | {x['3pm']/g:.1f} | {fg} | {ft} | "
                   f"{x['to']/g:.1f} | {x['score']/g:.2f} |")
    out += ["", f"Free agent = not on any roster in league.json. Score ignores: {', '.join(punt) or 'nothing'}."]

    text = "\n".join(out)
    os.makedirs(REPORTS, exist_ok=True)
    md = os.path.join(REPORTS, f"report_{day}.md")
    with open(md, "w", encoding="utf-8") as f:
        f.write(text)
    with open(os.path.join(REPORTS, f"boxscores_{day}.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["fantasy_team", "player", "team", "played", "min", "pts", "reb", "ast", "stl", "blk", "3pm",
                    "to", "fgm", "fga", "ftm", "fta", "score"])
        for r in sorted(last_night, key=lambda r: (owner.get(norm(r["name"]), "~FREE AGENT"), r["name"])):
            w.writerow([owner.get(norm(r["name"]), "FREE AGENT"), r["name"], r["team"], r["played"], r["min"], r["pts"],
                        r["reb"], r["ast"], r["stl"], r["blk"], r["3pm"], r["to"], r["fgm"], r["fga"], r["ftm"],
                        r["fta"], fantasy_score(r, punt)])
    print(text)
    print(f"\nSaved {md}")


if __name__ == "__main__":
    main()
