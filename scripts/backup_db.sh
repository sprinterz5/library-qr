#!/usr/bin/env bash
# Daily backup of gateway.db to a location OUTSIDE any git repo,
# so `git clean`/`git reset --hard`/redeploys can never touch backups.
set -euo pipefail

SRC_DB="/home/aidar/elibra-middleware/data/gateway.db"
BACKUP_DIR="/home/aidar/elibra-backups/gateway-db"
KEEP_DAYS=30
# Never prune the newest KEEP_MIN backups, however old: if the timer stops
# for weeks, the last good copies must survive the next run.
KEEP_MIN=7
TS="$(date +%Y%m%d-%H%M%S)"
DEST="$BACKUP_DIR/gateway-$TS.db"

mkdir -p "$BACKUP_DIR"

python3 - "$SRC_DB" "$DEST" <<'PY'
import sqlite3
import sys

src_path, dest_path = sys.argv[1], sys.argv[2]
src = sqlite3.connect(f"file:{src_path}?mode=ro", uri=True)
dest = sqlite3.connect(dest_path)
with dest:
    src.backup(dest)
src.close()
dest.close()
PY

echo "backup written: $DEST"

PREV="$(ls -1t "$BACKUP_DIR"/gateway-*.db 2>/dev/null | sed -n '2p')"
if [ -n "${PREV:-}" ]; then
    python3 - "$PREV" "$DEST" <<'PY'
import sqlite3
import sys

prev_path, cur_path = sys.argv[1], sys.argv[2]
tables = ["issued_books", "return_requests", "events"]

def counts(path):
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    result = {}
    for t in tables:
        try:
            result[t] = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        except sqlite3.OperationalError:
            pass
    con.close()
    return result

prev, cur = counts(prev_path), counts(cur_path)
for t in tables:
    if t in prev and t in cur and prev[t] > 5 and cur[t] < prev[t] * 0.5:
        print(f"WARNING: {t} dropped from {prev[t]} to {cur[t]} rows since last backup!", file=sys.stderr)
PY
fi

ls -1t "$BACKUP_DIR"/gateway-*.db 2>/dev/null | tail -n "+$((KEEP_MIN + 1))" | while read -r f; do
    if [ -n "$(find "$f" -mtime "+$KEEP_DAYS" 2>/dev/null)" ]; then rm -f -- "$f"; fi
done
