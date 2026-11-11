#!/usr/bin/env python3
# Rebuild a News Central database from a backup folder written by newscentral/backup.py.
# Usage: py tools/restore_backup.py <backup_dir> <new_db_path>
# Refuses to touch an existing database file.
import gzip
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from newscentral.backup import DAY_TABLES, STATE_RE, STATE_TABLES      # noqa: E402
from newscentral.store import Store                                    # noqa: E402


def insert_rows(db, table, rows, verb):
    """Insert the rows using only the columns present in both the row and the current table."""
    cols = [r[1] for r in db.execute('PRAGMA table_info("%s")' % table)]
    n = 0
    for row in rows:
        use = [c for c in cols if c in row]
        if not use:
            continue
        if verb == "REPLACE" and table == "translations":
            # REPLACE does not fire the delete trigger that keeps translations_fts in step.
            db.execute("DELETE FROM translations WHERE item_id = ? AND field = ? AND lang = ?",
                       [row.get("item_id"), row.get("field"), row.get("lang")])
        sql = 'INSERT OR %s INTO "%s" (%s) VALUES (%s)' % (
            verb, table, ",".join('"%s"' % c for c in use), ",".join("?" * len(use)))
        n += max(db.execute(sql, [row[c] for c in use]).rowcount, 0)
    return n


def restore(backup_dir, db_path):
    if os.path.exists(db_path):
        raise SystemExit("refusing to overwrite existing %s" % db_path)
    store = Store(db_path)
    counts = {}
    try:
        with store.lock:
            for table, _ in DAY_TABLES:
                tdir = os.path.join(backup_dir, table)
                total = 0
                for year in sorted(os.listdir(tdir)) if os.path.isdir(tdir) else []:
                    ydir = os.path.join(tdir, year)
                    for name in sorted(os.listdir(ydir)):
                        if not name.endswith(".ndjson.gz"):
                            continue
                        with gzip.open(os.path.join(ydir, name), "rt", encoding="utf-8") as f:
                            rows = [json.loads(line) for line in f if line.strip()]
                        # Day files go in ascending date order, so for translations the newest
                        # text of an (item, field, lang) wins; items never change, first one stays.
                        total += insert_rows(store.db, table, rows, "REPLACE" if table == "translations" else "IGNORE")
                counts[table] = total
            sdir = os.path.join(backup_dir, "state")
            names = sorted(n for n in os.listdir(sdir) if STATE_RE.match(n)) if os.path.isdir(sdir) else []
            if names:
                with gzip.open(os.path.join(sdir, names[-1]), "rt", encoding="utf-8") as f:
                    state = json.load(f)
                for table in STATE_TABLES:
                    counts[table] = insert_rows(store.db, table, state.get("tables", {}).get(table, []), "REPLACE")
            store.db.commit()
    finally:
        store.db.close()
    Store(db_path).db.close()      # reopen so ensure_fts builds the search index
    return counts


def main(argv):
    if len(argv) != 3:
        print(__doc__)
        return 2
    for table, n in restore(argv[1], argv[2]).items():
        print("%-12s %d" % (table, n))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
