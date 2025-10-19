#!/usr/bin/env bash
set -e
cd ~/fagni
TS=$(date +%Y-%m-%d_%H-%M)
cp db.sqlite3 "backups/db_${TS}.sqlite3"
ls -1t backups/db_*.sqlite3 | tail -n +8 | xargs -r rm --
echo "Backup OK: ${TS}"
