# newscentral/backup.py
# Daily incremental backup of what cannot be regenerated, into a folder a sync client uploads.
# Only the standard library. Day files are written once and never touched again; the small
# state file is rewritten on each run. No database snapshots, no derived tables (search index).
import gzip
import json
import os
import pathlib
import re
import sqlite3
import time
from datetime import datetime, timezone

DAY_TABLES = [("items", "first_seen")]          # (table, time column): one file per complete UTC day
STATE_TABLES = ["saved", "sources", "feed_state"]
STATE_KEEP = 14
STATE_RE = re.compile(r"^state-\d{4}-\d{2}-\d{2}\.json\.gz$")
DAY = 86400


def utc_date(epoch):
    return datetime.fromtimestamp(epoch, timezone.utc).strftime("%Y-%m-%d")


def _connect_ro(db_path):
    uri = pathlib.Path(os.path.abspath(db_path)).as_uri() + "?mode=ro"
    return sqlite3.connect(uri, uri=True, timeout=30)


def _columns(db, table):
    return [r[1] for r in db.execute('PRAGMA table_info("%s")' % table)]


def _rows(db, table, where="", params=(), order=""):
    cur = db.execute('SELECT * FROM "%s"%s%s' % (table, where, order), params)
    names = [d[0] for d in cur.description]
    return [dict(zip(names, r)) for r in cur]


def _json_default(v):
    if isinstance(v, (bytes, bytearray)):
        return v.hex()
    raise TypeError("not serializable: %r" % (v,))


def _write_gz(path, write_fn):
    """Write to a .tmp file in the same folder, then rename, so a sync client never sees half a file."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    try:
        with gzip.open(tmp, "wt", encoding="utf-8", compresslevel=9) as f:
            write_fn(f)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def run_backup(db_path, backup_dir, now=None):
    now = time.time() if now is None else now
    today_start = int(now // DAY) * DAY
    db = _connect_ro(db_path)
    written = {}
    try:
        db.isolation_level = None
        db.execute("BEGIN")                       # one consistent snapshot for the whole run
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        version = db.execute("PRAGMA user_version").fetchone()[0]

        for table, tcol in DAY_TABLES:
            if table not in tables:
                continue
            cols = _columns(db, table)
            if tcol not in cols:
                continue
            order = ' ORDER BY "%s"' % tcol + (', "id"' if "id" in cols else "")
            count = 0
            days = [r[0] for r in db.execute(
                'SELECT DISTINCT CAST("%s" / %d AS INTEGER) FROM "%s" WHERE "%s" < ?' % (tcol, DAY, table, tcol),
                (today_start,)) if r[0] is not None]
            for d in sorted(days):
                date = utc_date(d * DAY)
                path = os.path.join(backup_dir, table, date[:4], date + ".ndjson.gz")
                if os.path.exists(path):
                    continue
                rows = _rows(db, table, ' WHERE "%s" >= ? AND "%s" < ?' % (tcol, tcol),
                             (d * DAY, (d + 1) * DAY), order)

                def write(f, rows=rows):
                    for row in rows:
                        f.write(json.dumps(row, ensure_ascii=False, default=_json_default) + "\n")
                _write_gz(path, write)
                count += len(rows)
            written[table] = count

        state = {}
        for table in STATE_TABLES:
            if table in tables:
                state[table] = _rows(db, table)
        db.execute("ROLLBACK")
    finally:
        db.close()

    payload = {"schema_version": version, "created": now, "tables": state}
    state_dir = os.path.join(backup_dir, "state")
    _write_gz(os.path.join(state_dir, "state-%s.json.gz" % utc_date(now)),
              lambda f: f.write(json.dumps(payload, ensure_ascii=False, default=_json_default)))
    for name in sorted(n for n in os.listdir(state_dir) if STATE_RE.match(n))[:-STATE_KEEP]:
        os.remove(os.path.join(state_dir, name))
    for table, rows in state.items():
        written[table] = len(rows)

    summary = {"last_run": now, "last_run_utc": utc_date(now), "schema_version": version, "written": written}
    tmp = os.path.join(backup_dir, "backup.json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=1)
    os.replace(tmp, os.path.join(backup_dir, "backup.json"))
    return summary
