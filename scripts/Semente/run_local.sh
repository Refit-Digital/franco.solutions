#!/bin/bash
# Local backup for Semente's Ticket Tailor reconcile scripts.
#
# Runs the same reconcile.py / reconcile_courses.py that GitHub Actions runs
# daily at midnight Lisbon (.github/workflows/reconcile.yml), as a safety net
# in case that schedule is ever disabled, mis-pathed, or fails silently.
#
# Installed as a launchd LaunchAgent — see com.franco.semente-reconcile.plist
# in this same folder for the install/uninstall commands.

set -u
export PATH="/usr/bin:/bin:/usr/sbin:/sbin:/usr/local/bin:/opt/homebrew/bin:$PATH"

REPO_DIR="$HOME/Desktop/franco.solutions/scripts/Semente"
LOG="$HOME/Library/Logs/semente-reconcile.log"
mkdir -p "$(dirname "$LOG")"

log() { printf '%s %s\n' "$(date '+%Y-%m-%d %H:%M:%S %Z')" "$1" >> "$LOG"; }

cd "$REPO_DIR" || { log "FATAL: cannot cd to $REPO_DIR"; exit 1; }

PY="$(command -v python3)"
if [ -z "$PY" ]; then
  log "FATAL: python3 not found on PATH"
  osascript -e 'display notification "python3 not found — local Ticket Tailor reconcile did not run" with title "Semente reconcile failed" sound name "Basso"' 2>/dev/null
  exit 1
fi

status=0
log "--- run start ---"

out1=$("$PY" reconcile.py --apply 2>&1); rc1=$?
printf '%s\n' "$out1" >> "$LOG"
log "reconcile.py --apply exit $rc1"
[ "$rc1" -ne 0 ] && status=1

out2=$("$PY" reconcile_courses.py --apply 2>&1); rc2=$?
printf '%s\n' "$out2" >> "$LOG"
log "reconcile_courses.py --apply exit $rc2"
[ "$rc2" -ne 0 ] && status=1

log "--- run end (status=$status) ---"

if [ "$status" -ne 0 ]; then
  osascript -e 'display notification "Check ~/Library/Logs/semente-reconcile.log" with title "Semente reconcile: oversold or error" sound name "Basso"' 2>/dev/null
fi

exit "$status"
