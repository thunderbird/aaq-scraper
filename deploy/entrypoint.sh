#!/usr/bin/env bash
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Pod entrypoint: clone aaq-scraper, run the incremental refresh, commit changed
# CSVs (and the high-water mark) back to the branch. Mirrors the logic that used
# to live in .github/workflows/scrape.yml (checkout + run_refresh + commit with
# rebase-onto-latest-main retry). The pod is stateless; the repo IS the state.
set -euo pipefail

: "${GIT_REPO_URL:?set GIT_REPO_URL}"
: "${GITHUB_TOKEN:?set GITHUB_TOKEN}"
GIT_BRANCH="${GIT_BRANCH:-main}"
GIT_AUTHOR_NAME="${GIT_AUTHOR_NAME:-aaq-scraper-bot}"
GIT_AUTHOR_EMAIL="${GIT_AUTHOR_EMAIL:-aaq-scraper-bot@thunderbird.net}"
REFRESH_ARGS="${REFRESH_ARGS:---soft-deadline 40 --max-429-wait 120}"
# Paths staged for the commit. Defaults to the real data tree; a shakedown
# deployment writing under AAQ_DATA_ROOT sets this to that directory instead so
# it never stages the committed CSVs. Word-split deliberately (multiple paths).
GIT_ADD_PATHS="${GIT_ADD_PATHS:-20*/ .refresh-hwm}"

# $HOME is the writable emptyDir mounted in the pod (Phase B sets HOME=/work);
# /tmp is on the read-only root filesystem (readOnlyRootFilesystem: true), so
# mktemp must be pointed at $HOME rather than its default /tmp base.
WORKDIR="$(mktemp -d "${HOME:-/tmp}/aaq-scraper.XXXXXX")"
trap 'rm -rf "$WORKDIR"' EXIT

# Supply the PAT via a credential helper that reads $GITHUB_TOKEN from the
# environment at call time, so only the variable NAME (never its value) ever
# appears in argv (visible via ps / /proc/<pid>/cmdline). The clone/pull/push
# all use the plain GIT_REPO_URL -- no token spliced into the URL.
export GIT_TERMINAL_PROMPT=0
CRED_HELPER='!f() { echo "username=x-access-token"; echo "password=${GITHUB_TOKEN}"; }; f'

git -c credential.helper="$CRED_HELPER" clone --branch "$GIT_BRANCH" "$GIT_REPO_URL" "$WORKDIR/repo"
cd "$WORKDIR/repo"
git config credential.helper "$CRED_HELPER"
git config user.name "$GIT_AUTHOR_NAME"
git config user.email "$GIT_AUTHOR_EMAIL"

# Run the refresh. REFRESH_CMD lets tests inject a fake; default is the real one.
# Preserve the deferral exit code (75) as a non-error signal.
set +e
if [ -n "${REFRESH_CMD:-}" ]; then
  eval "$REFRESH_CMD"
else
  uv run python run_refresh.py $REFRESH_ARGS
fi
rc=$?
set -e
if [ "$rc" -eq 75 ]; then
  echo "refresh deferred (exit 75); committing whatever completed"
elif [ "$rc" -ne 0 ]; then
  echo "refresh failed (exit $rc)"; exit "$rc"
fi

# Stage each configured path independently and tolerate ones that do not exist
# (a fresh checkout has no .refresh-hwm; a shakedown root has no 20*/ dirs).
# $GIT_ADD_PATHS is intentionally unquoted so the shell word-splits and expands
# the 20*/ glob before git sees it.
# shellcheck disable=SC2086
for _p in $GIT_ADD_PATHS; do
  git add -- "$_p" 2>/dev/null || true
done
if git diff --cached --quiet; then
  echo "No changes to commit."
  exit 0
fi
git commit -m "Hourly refresh $(date -u +%Y-%m-%dT%H:%MZ)"

# main also receives pushes from manual backfills, so rebase onto latest and
# retry with backoff rather than failing on a rejected push.
#
# --autostash: the scrape can leave unstaged files in the clone (anything not
# staged by GIT_ADD_PATHS), and a plain `git pull --rebase` REFUSES to run with
# a dirty tree -- which would wedge this loop for all 5 attempts and fail the
# run even though the commit itself is fine.
for attempt in 1 2 3 4 5; do
  if ! git pull --rebase --autostash origin "$GIT_BRANCH"; then
    git rebase --abort || true
    echo "rebase failed (attempt $attempt/5); retrying"; sleep $((attempt * 5)); continue
  fi
  if git push origin "$GIT_BRANCH"; then
    echo "pushed on attempt $attempt"; exit 0
  fi
  echo "push rejected (attempt $attempt/5); retrying"; sleep $((attempt * 5))
done
echo "ERROR: could not push after 5 attempts"; exit 1
