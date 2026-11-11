# Collection health numbers. Pure functions: no database, no clock.
from datetime import datetime, timedelta


def gaps(starts, interval, since, now):
    """starts: fetch-run start times, ascending: every run from `since`, preceded by the first
    run ever when that one is older (Store.run_starts returns exactly this).
    A gap is a stretch longer than twice the interval with no run starting.
    Uptime is the share of the window not lost to gaps; None when nothing was ever collected."""
    if not starts:
        return {"gaps": [], "longest": 0, "uptime": None}
    begin = since if starts[0] < since else starts[0]   # never count time before collection began
    points = [begin] + [t for t in starts if t > begin] + [now]
    found = []
    for a, b in zip(points, points[1:]):
        if b - a > 2 * interval:
            found.append({"from": a, "to": b, "seconds": b - a})
    span = max(now - begin, 1)
    lost = sum(g["seconds"] - interval for g in found)
    return {"gaps": found, "longest": max((g["seconds"] for g in found), default=0),
            "uptime": max(0.0, 1.0 - lost / span)}


def volume(rows):
    """rows: (days since 1970-01-01 UTC, lang, item count, character count)."""
    epoch = datetime(1970, 1, 1)
    return [{"day": (epoch + timedelta(days=int(d))).strftime("%Y-%m-%d"), "lang": lang or "",
             "items": items, "chars": chars or 0} for d, lang, items, chars in rows]
