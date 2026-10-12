#!/bin/sh
# Phase 6 — database backup/restore helper.
#
#   scripts/backup_db.sh backup [dir]    # timestamped backup into dir (default ./backups)
#   scripts/backup_db.sh restore <file>  # restore from a backup file
#
# Handles both database backends:
#   - SQLite (SHOPEE_DATABASE_URL=sqlite:///./path.db)  -> online-backup file copy
#   - PostgreSQL (postgresql+psycopg://...)             -> pg_dump (custom format)
#
# In docker-compose.prod.yml a backup sidecar runs pg_dump on a 24h loop instead;
# this script is for manual/ops use and dev environments.
set -eu

CMD="${1:-backup}"
DB_URL="${SHOPEE_DATABASE_URL:-sqlite:///./shopee_ai_agent.db}"

sqlite_backup() {
    src="${DB_URL#sqlite:///}"
    [ -f "$src" ] || { echo "sqlite database not found: $src" >&2; exit 1; }
    dir="${2:-./backups}"
    mkdir -p "$dir"
    dest="$dir/$(basename "$src" .db)-$(date +%Y%m%d-%H%M%S).db"
    python3 - "$src" "$dest" <<'PY'
import sqlite3, sys
src, dest = sys.argv[1], sys.argv[2]
with sqlite3.connect(src) as con:
    with sqlite3.connect(dest) as dst:
        con.backup(dst)  # online backup — consistent even while the app runs
print(f"backed up {src} -> {dest}")
PY
}

sqlite_restore() {
    src="${DB_URL#sqlite:///}"
    [ -f "$1" ] || { echo "backup file not found: $1" >&2; exit 1; }
    python3 - "$src" "$1" <<'PY'
import sqlite3, sys, shutil
src, backup = sys.argv[1], sys.argv[2]
shutil.copyfile(backup, src)
with sqlite3.connect(src) as con:
    con.execute("PRAGMA integrity_check")
print(f"restored {backup} -> {src}")
PY
}

pg_backup() {
    dir="${2:-./backups}"
    mkdir -p "$dir"
    dest="$dir/shopbot-$(date +%Y%m%d-%H%M%S).dump"
    # parse user:pass@host:port/db from the SQLAlchemy URL
    rest="${DB_URL#postgresql+psycopg://}"
    creds="${rest%%@*}"; hostport="${rest#*@}"; host="${hostport%%:*}"; port="${hostport#*:}"; db="${port#*/}"; port="${port%%/*}"
    PGPASSWORD="${creds#*:}" pg_dump -h "$host" -p "$port" -U "${creds%%:*}" -d "$db" -Fc -f "$dest"
    echo "backed up postgres://$host:$port/$db -> $dest"
}

pg_restore() {
    [ -f "$1" ] || { echo "backup file not found: $1" >&2; exit 1; }
    rest="${DB_URL#postgresql+psycopg://}"
    creds="${rest%%@*}"; hostport="${rest#*@}"; host="${hostport%%:*}"; port="${hostport#*:}"; db="${port#*/}"; port="${port%%/*}"
    PGPASSWORD="${creds#*:}" pg_restore -h "$host" -p "$port" -U "${creds%%:*}" -d "$db" --clean --if-exists "$1"
    echo "restored $1 -> postgres://$host:$port/$db"
}

case "$DB_URL" in
    sqlite:///*)
        case "$CMD" in
            backup) sqlite_backup "$@" ;;
            restore) sqlite_restore "$2" ;;
            *) echo "usage: $0 backup|restore" >&2; exit 2 ;;
        esac
        ;;
    postgresql*)
        command -v pg_dump >/dev/null || { echo "pg_dump not found (install postgresql-client)" >&2; exit 1; }
        case "$CMD" in
            backup) pg_backup "$@" ;;
            restore) pg_restore "$2" ;;
            *) echo "usage: $0 backup|restore" >&2; exit 2 ;;
        esac
        ;;
    *)
        echo "unsupported SHOPEE_DATABASE_URL: $DB_URL" >&2; exit 1 ;;
esac
