#!/usr/bin/env bash
# Month-boundary watcher for the historical backfill (run_backfill_months.py).
# Polls the backfill log; when a month finishes it generates a markdown report,
# commits + pushes it under backfill-reports/, and exits with an EVENT line so
# the driving session is re-invoked to surface it. Exits on stall/death too.
# NOT part of the scraper; safe to delete.
# (macOS ships bash 3.2 — no `mapfile`, and empty-array reads trip `set -u`,
# so this stays 3.2-compatible and avoids `set -u`.)

REPO="/Users/roland/Documents/GIT/aaq-scraper"
RDIR="$REPO/backfill-reports"
LOG="$(cat /tmp/backfill-resume-logpath.txt)"
PID="$1"                 # backfill process pid
STALL_MIN=45             # minutes of log silence -> treat as stall/timeout
EVENTFILE="$RDIR/.events.log"
cd "$REPO" || { echo "EVENT error no_repo"; exit 0; }

emit() { echo "$1" >> "$EVENTFILE"; echo "$1"; }

# rows = (lines - 1) summed over matching CSVs; 0 if none.
rows() { local n=0 l; for f in "$@"; do [ -f "$f" ] || continue; l=$(wc -l < "$f"); n=$((n + l - 1)); done; echo "$n"; }

gen_report() {
  # NB: separate `local` lines — bash 3.2 evaluates all RHS in a single
  # `local a=.. b=$a` before assigning, so b would not see a.
  local month="$1"
  local year="${month%%-*}"
  local f="$RDIR/$month.md"
  local qd ad qdrows adrows qa aa qarows aarows fails commit ts done_n
  qd=$(ls "$year"/questions-thunderbird-desktop-"$month"-??.csv 2>/dev/null | wc -l | tr -d ' ')
  ad=$(ls "$year"/answers-thunderbird-desktop-"$month"-??.csv 2>/dev/null | wc -l | tr -d ' ')
  qa=$(ls "$year"/questions-thunderbird-android-"$month"-??.csv 2>/dev/null | wc -l | tr -d ' ')
  aa=$(ls "$year"/answers-thunderbird-android-"$month"-??.csv 2>/dev/null | wc -l | tr -d ' ')
  qdrows=$(rows "$year"/questions-thunderbird-desktop-"$month"-??.csv)
  adrows=$(rows "$year"/answers-thunderbird-desktop-"$month"-??.csv)
  qarows=$(rows "$year"/questions-thunderbird-android-"$month"-??.csv)
  aarows=$(rows "$year"/answers-thunderbird-android-"$month"-??.csv)
  commit=$(git log --pretty='%h %s' --grep="Backfill $month " -1 2>/dev/null)
  [ -z "$commit" ] && commit="(no data-commit — deterministic/no changes)"
  fails=$(grep -E "^$month-[0-9]{2}" backfill-failures.txt 2>/dev/null)
  [ -z "$fails" ] && fails="none"
  ts=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  done_n=$(ls "$RDIR"/[0-9]*.md 2>/dev/null | wc -l | tr -d ' ')
  {
    echo "# Backfill report — $month"
    echo
    echo "- Completed (UTC): $ts"
    echo "- Data commit: $commit"
    echo "- Progress: month $((done_n + 1))/22 of the 2024-10 → 2023-01 backfill"
    echo
    echo "## Desktop (thunderbird)"
    echo "- question days: $qd | question rows: $qdrows"
    echo "- answer days:   $ad | answer rows:   $adrows"
    if [ "$qa" -gt 0 ] || [ "$aa" -gt 0 ]; then
      echo
      echo "## Android (thunderbird-android)"
      echo "- question days: $qa | question rows: $qarows"
      echo "- answer days:   $aa | answer rows:   $aarows"
    else
      echo
      echo "## Android"
      echo "- none (pre-launch; Android started 2024-10)"
    fi
    echo
    echo "## Failures this month"
    echo '```'
    echo "$fails"
    echo '```'
  } > "$f"
  echo "$f"
}

commit_push_report() {
  local month="$1" attempt
  git add "$RDIR" >/dev/null 2>&1
  git diff --cached --quiet && { echo "no report change"; return 0; }
  git commit -q -m "Backfill report $month" >/dev/null 2>&1
  for attempt in 1 2 3 4 5; do
    if ! git pull --rebase --quiet >/dev/null 2>&1; then
      git rebase --abort >/dev/null 2>&1; sleep 8; continue
    fi
    git push --quiet >/dev/null 2>&1 && return 0
    sleep 8
  done
  emit "EVENT warn push_failed_report $month"
  return 1
}

while true; do
  # months whose header has appeared, in order
  headers=()
  while IFS= read -r hline; do
    [ -n "$hline" ] && headers+=("$hline")
  done < <(grep -oE 'MONTH [0-9]+/[0-9]+: [0-9]{4}-[0-9]{2}' "$LOG" 2>/dev/null | grep -oE '[0-9]{4}-[0-9]{2}')
  complete=$(grep -c 'MONTH BACKFILL COMPLETE' "$LOG" 2>/dev/null)
  alive=0; kill -0 "$PID" 2>/dev/null && alive=1

  # a month is finished once the NEXT month's header appears (or backfill done)
  finished=()
  if [ "${#headers[@]}" -gt 0 ]; then
    last=$((${#headers[@]} - 1))
    for i in "${!headers[@]}"; do
      if [ "$i" -lt "$last" ] || [ "$complete" -gt 0 ]; then finished+=("${headers[$i]}"); fi
    done
  fi

  # report any finished month lacking a report file
  # Persistent: generate+commit+push a report for every finished month lacking
  # one, then KEEP watching. Exiting per-boundary meant a slow re-arm (harness
  # notification latency) could let the backfill finish several months with no
  # report written; staying alive guarantees each month is reported on time.
  for m in "${finished[@]}"; do
    [ -f "$RDIR/$m.md" ] && continue
    gen_report "$m" >/dev/null
    commit_push_report "$m"
    emit "EVENT month_done $m"
  done

  if [ "$complete" -gt 0 ] && [ "$alive" -eq 0 ]; then emit "EVENT complete"; exit 0; fi
  if [ "$alive" -eq 0 ]; then emit "EVENT process_died"; exit 0; fi
  # stall: log untouched for STALL_MIN minutes while process still alive
  if [ -n "$(find "$LOG" -mmin +$STALL_MIN 2>/dev/null)" ]; then emit "EVENT stalled"; exit 0; fi

  sleep 60
done
