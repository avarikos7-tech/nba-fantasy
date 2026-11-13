#!/usr/bin/env python3
"""
Save the latest official NBA injury report (the one teams file with the league)
to reports/injuries_latest.csv and reports/injuries_<date>.csv.

Uses the nbainjuries package (pip install nbainjuries; needs Java for tabula-py).
Reports are published in 15/30-minute snapshots (US Eastern time); this script
walks back from now until it finds the most recent one.
"""
import csv
import os
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from nbainjuries import injury

HERE = os.path.dirname(os.path.abspath(__file__))
REPORTS = os.path.join(HERE, "reports")
ET = ZoneInfo("America/New_York")


def latest_report_time(max_hours=36):
    now = datetime.now(ET).replace(second=0, microsecond=0, tzinfo=None)
    t = now - timedelta(minutes=now.minute % 15)
    for _ in range(max_hours * 4):
        try:
            if injury.check_reportvalid(t):
                return t
        except Exception:
            pass
        t -= timedelta(minutes=15)
    return None


def first_last(name):
    # "Brown, Jaylen" -> "Jaylen Brown"; keeps suffixes like "Jr." on the surname side
    if "," in name:
        last, first = [p.strip() for p in name.split(",", 1)]
        return f"{first} {last}".strip()
    return name.strip()


def main():
    t = latest_report_time()
    if t is None:
        print("No injury report found in the last 36 hours.")
        return 0
    rows = injury.get_reportdata(t)
    if isinstance(rows, str):
        import json
        rows = json.loads(rows)
    os.makedirs(REPORTS, exist_ok=True)
    fields = ["report_time_et", "game_date", "game_time", "matchup", "team", "player",
              "player_raw", "status", "reason"]
    out = []
    for r in rows or []:
        raw = (r.get("Player Name") or "").strip()
        if not raw:
            continue
        out.append({
            "report_time_et": t.strftime("%Y-%m-%d %H:%M"),
            "game_date": r.get("Game Date", ""), "game_time": r.get("Game Time", ""),
            "matchup": r.get("Matchup", ""), "team": r.get("Team", ""),
            "player": first_last(raw), "player_raw": raw,
            "status": r.get("Current Status", ""), "reason": r.get("Reason", ""),
        })
    for path in (os.path.join(REPORTS, "injuries_latest.csv"),
                 os.path.join(REPORTS, f"injuries_{t.date()}.csv")):
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader()
            w.writerows(out)
    print(f"Saved {len(out)} injury entries from the report of {t:%Y-%m-%d %H:%M} ET")
    return 0


if __name__ == "__main__":
    sys.exit(main())
