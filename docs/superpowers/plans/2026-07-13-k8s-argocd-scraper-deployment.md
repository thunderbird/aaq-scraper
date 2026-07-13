<!--
This Source Code Form is subject to the terms of the Mozilla Public
License, v. 2.0. If a copy of the MPL was not distributed with this
file, You can obtain one at https://mozilla.org/MPL/2.0/.
-->

# AAQ Scraper → ArgoCD CronJob Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run the SUMO AAQ scraper as an hourly ArgoCD CronJob on the workloads EKS cluster with a stable, allowlistable egress IP, replacing the (now-blocked) GitHub Actions browser approach.

**Architecture:** Two coordinated changes. **Phase A** (`aaq-scraper` repo, PR #44): swap `sumo.py`'s Playwright engine for a plain `httpx` client behind the *unchanged* `SumoBrowser`/`fetch_json`/`paginate` public API; package a lean, browser-free arm64 image whose entrypoint clones the repo, runs the existing refresh, and commits CSVs back to git; build that image in CI via OIDC → shared ECR. **Phase B** (`platform-infrastructure` repo, separate PR): Pulumi ECR repo + push role, the `services/aaq-scraper/deploy/` manifests, and the ArgoCD Application targeting the workloads cluster. The design doc is `docs/superpowers/specs/2026-07-13-k8s-argocd-scraper-deployment-design.md`.

**Tech Stack:** Python 3.10+ (`httpx`, `pytest`), Docker (`python:3.12-slim`, `linux/arm64`), Kubernetes (`batch/v1` CronJob), ArgoCD (raw-YAML app-of-apps), External Secrets Operator → AWS Secrets Manager, Pulumi (Python), GitHub Actions (OIDC → ECR).

## Global Constraints

- **License header on every new file** — MPL-2.0 block; Python after the shebang, YAML/Dockerfile/shell at the top. Copy verbatim from any existing file.
- **`SumoBrowser` public API is frozen.** The class name, constructor keyword args (`headless`, `settle_ms`, `max_attempts`, `backoff_base`, `retry_jitter_min_s`, `retry_jitter_max_s`, `max_429_wait_s`), context-manager protocol, and the `fetch_json(url)` / `paginate(first_url, on_page=None)` methods must keep identical signatures and behavior. Consumers (`scrape_questions.py`, `scrape_answers.py`, `find_updated_days.py`, `check_schema.py`, `run_refresh.py`) must not need edits.
- **`fetch_json` / `paginate` bodies stay unchanged** — only `__init__`, `__enter__`, `__exit__`, `_raw_fetch` are rewritten. `_raw_fetch(url)` must keep returning exactly `{"status": int, "json": dict|None, "snippet": str, "retry_after": str|None}`.
- **Exception contract preserved:** `ChallengeError` on exhausted 200-but-HTML; `RateLimitDeferral` when a 429 wait exceeds `max_429_wait_s`; `RuntimeError` on non-retryable 4xx; `DEFERRAL_EXIT_CODE = 75`.
- **Determinism preserved:** re-running a day yields byte-identical CSVs; the "nothing changed → no commit" path must remain.
- **Package manager is `uv`** — `uv sync` / `uv run`, never pip/venv.
- **Image:** `linux/arm64`, run as nonroot, shared ECR `826971876779.dkr.ecr.us-east-1.amazonaws.com/aaq-scraper`, immutable `git-<short-sha>` tags.
- **Deploys are human-gated:** CI only pushes the image; releasing means bumping the `image:` tag in `services/aaq-scraper/deploy/cronjob.yaml` via a `platform-infrastructure` PR.
- **Cluster/namespace:** workloads cluster `mzla-eks-workloads01` (eu-central-1), ArgoCD project `workloads`, namespace `aaq-scraper` (CreateNamespace via the Application).

---

# Phase A — `aaq-scraper` repo (PR #44)

Executes in this checkout (`/home/aatchison/src/tb/aaq-scraper`), branch `k8s-argocd-scraper-deployment`.

## Task A1: Lock the `fetch_json` contract with tests (safety net)

These regression tests stub `_raw_fetch`, so they pass against the **current Playwright code** and must keep passing after the httpx swap. They are the guardrail for Task A2.

**Files:**
- Create: `tests/__init__.py` (empty)
- Create: `tests/test_fetch_json.py`
- Modify: `pyproject.toml` (add a dev dependency group with `pytest`)

**Interfaces:**
- Consumes: `sumo.SumoBrowser`, `sumo.ChallengeError`, `sumo.RateLimitDeferral` (existing).
- Produces: nothing consumed by later tasks (test-only), but establishes the behaviors Task A2 must preserve.

- [ ] **Step 1: Add the pytest dev dependency**

In `pyproject.toml`, after the `dependencies = [...]` array, add:

```toml
[dependency-groups]
dev = [
    "pytest>=8",
]
```

- [ ] **Step 2: Sync so pytest is available**

Run: `uv sync`
Expected: resolves and installs `pytest` into the environment.

- [ ] **Step 3: Write the contract tests**

Create `tests/__init__.py` (empty file with just the MPL header as a comment) and `tests/test_fetch_json.py`:

```python
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""Contract/regression tests for SumoBrowser.fetch_json.

These stub _raw_fetch (no browser, no network), so they pin the retry /
deferral / challenge behavior independently of the transport. They must pass
both before and after the Playwright->httpx refactor (Task A2)."""
import pytest

import sumo


def _resp(status, json=None, snippet="", retry_after=None):
    return {"status": status, "json": json, "snippet": snippet,
            "retry_after": retry_after}


def _browser(monkeypatch, responses, **kwargs):
    """A SumoBrowser whose _raw_fetch yields the given canned responses in
    order, with sleeping/jitter neutralized so tests run instantly."""
    kwargs.setdefault("max_attempts", 5)
    kwargs.setdefault("backoff_base", 2.0)
    kwargs.setdefault("retry_jitter_min_s", 0)
    kwargs.setdefault("retry_jitter_max_s", 0)
    sb = sumo.SumoBrowser(**kwargs)
    seq = iter(responses)
    monkeypatch.setattr(sb, "_raw_fetch", lambda url: next(seq))
    monkeypatch.setattr(sumo.time, "sleep", lambda _s: None)
    monkeypatch.setattr(sumo.random, "uniform", lambda _a, _b: 0.0)
    return sb


def test_returns_json_on_200(monkeypatch):
    sb = _browser(monkeypatch, [_resp(200, {"count": 3})])
    assert sb.fetch_json("u") == {"count": 3}


def test_non_retryable_4xx_raises_plain_runtimeerror(monkeypatch):
    sb = _browser(monkeypatch, [_resp(404, snippet="nope")])
    with pytest.raises(RuntimeError) as ei:
        sb.fetch_json("u")
    assert not isinstance(ei.value, sumo.ChallengeError)
    assert not isinstance(ei.value, sumo.RateLimitDeferral)


def test_5xx_retried_then_success(monkeypatch):
    sb = _browser(monkeypatch, [_resp(503), _resp(200, {"ok": 1})])
    assert sb.fetch_json("u") == {"ok": 1}


def test_exhausted_200_non_json_raises_challenge_error(monkeypatch):
    sb = _browser(monkeypatch, [_resp(200, None, "<html>challenge</html>")] * 5)
    with pytest.raises(sumo.ChallengeError):
        sb.fetch_json("u")


def test_429_over_threshold_defers(monkeypatch):
    sb = _browser(monkeypatch, [_resp(429, retry_after="600")],
                  max_429_wait_s=120)
    with pytest.raises(sumo.RateLimitDeferral):
        sb.fetch_json("u")


def test_429_under_threshold_retries_then_succeeds(monkeypatch):
    sb = _browser(monkeypatch,
                  [_resp(429, retry_after="10"), _resp(200, {"ok": 1})],
                  max_429_wait_s=1000)
    assert sb.fetch_json("u") == {"ok": 1}
```

- [ ] **Step 4: Run the tests against the current (Playwright) code**

Run: `uv run pytest tests/test_fetch_json.py -v`
Expected: **6 passed** — `_raw_fetch` is stubbed, so no browser launches. If any fail, the stub/monkeypatch is wrong; fix before touching `sumo.py`.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml uv.lock tests/__init__.py tests/test_fetch_json.py
git commit -m "test: pin SumoBrowser.fetch_json retry/deferral/challenge contract"
```

## Task A2: Swap the `sumo.py` engine Playwright → httpx

Rewrite only `__init__`, `__enter__`, `__exit__`, `_raw_fetch`; leave `fetch_json` and `paginate` untouched. The Task A1 tests are the pass/fail gate, plus one new test for the httpx mapping.

**Files:**
- Modify: `sumo.py:13-18` (imports), `sumo.py:75-168` (class through `_raw_fetch`)
- Modify: `pyproject.toml` (`dependencies`: drop `playwright`, add `httpx`)
- Modify: `tests/test_fetch_json.py` (append the `_raw_fetch` mapping test)

**Interfaces:**
- Consumes: `sumo.API_BASE`, `sumo.HOME_URL`, `sumo.USER_AGENT` (existing module constants, keep them).
- Produces: `SumoBrowser` with the frozen public API (see Global Constraints); `_raw_fetch(url) -> {"status","json","snippet","retry_after"}`.

- [ ] **Step 1: Add the httpx mapping test (fails until the refactor lands)**

Append to `tests/test_fetch_json.py`:

```python
def test_raw_fetch_maps_httpx_response():
    """_raw_fetch turns an httpx response into the dict fetch_json expects."""
    import httpx

    def handler(request):
        return httpx.Response(429, headers={"retry-after": "30"},
                              text='{"a": 1}')

    sb = sumo.SumoBrowser()
    sb._client = httpx.Client(transport=httpx.MockTransport(handler))
    out = sb._raw_fetch("https://support.mozilla.org/api/2/question/")
    assert out == {"status": 429, "json": {"a": 1},
                   "snippet": '{"a": 1}', "retry_after": "30"}


def test_raw_fetch_non_json_body_sets_json_none():
    import httpx

    def handler(request):
        return httpx.Response(200, text="<html>challenge</html>")

    sb = sumo.SumoBrowser()
    sb._client = httpx.Client(transport=httpx.MockTransport(handler))
    out = sb._raw_fetch("https://support.mozilla.org/api/2/question/")
    assert out["status"] == 200 and out["json"] is None
    assert out["snippet"] == "<html>challenge</html>"
```

- [ ] **Step 2: Run to confirm the new tests fail**

Run: `uv run pytest tests/test_fetch_json.py -k raw_fetch -v`
Expected: FAIL — current `SumoBrowser` has no `_client` attribute / `_raw_fetch` calls `self._page`.

- [ ] **Step 3: Swap the dependency**

In `pyproject.toml`, change the `dependencies` array from:

```toml
dependencies = [
    "playwright==1.49.1",
]
```
to:
```toml
dependencies = [
    "httpx==0.28.1",
]
```

- [ ] **Step 4: Rewrite the imports (`sumo.py` lines 13-18)**

Replace:
```python
import csv
import random
import sys
import time

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeoutError
```
with:
```python
import csv
import json
import random
import sys
import time

import httpx
```

Also update the module docstring (lines 4-11) to describe the HTTP client instead of the browser:
```python
"""
Shared SUMO (support.mozilla.org) API client.

Historically drove a real Chromium (Playwright) to pass the Fastly JS/WAF
challenge; that path is now fingerprinted and blocked (issue #28), so this uses
a plain HTTP client (httpx). Once our egress IP is allowlisted by Mozilla
(issue #27) the API is reachable directly. The public API (SumoBrowser,
fetch_json, paginate) is unchanged so callers did not have to change.
"""
```

- [ ] **Step 5: Rewrite the class from `__init__` through `_raw_fetch`**

Replace `sumo.py` lines 75-168 (the class docstring, `__init__`, `__enter__`, `__exit__`, `_raw_fetch`) with:

```python
class SumoBrowser:
    """A SUMO API HTTP session.

    Named `SumoBrowser` for backwards compatibility with existing call sites;
    it no longer drives a browser. Use as a context manager:

        with SumoBrowser(headless=True) as sumo:
            data = sumo.fetch_json(sumo_url)
            for page in sumo.paginate(first_url):
                ...
    """

    def __init__(self, headless=False, settle_ms=6000,
                 max_attempts=5, backoff_base=2.0,
                 retry_jitter_min_s=60, retry_jitter_max_s=300,
                 max_429_wait_s=None):
        # `headless` and `settle_ms` are accepted for backwards-compatible call
        # sites but ignored: there is no browser to run headed/headless and
        # nothing to "settle" for a plain HTTP client.
        self.max_attempts = max_attempts
        self.backoff_base = backoff_base
        # If set, a 429 whose required wait (Retry-After / backoff) exceeds this
        # many seconds raises RateLimitDeferral instead of sleeping, so a caller
        # can defer the work rather than burn the run on one long window. None =
        # honour the wait in full (original behaviour).
        self.max_429_wait_s = max_429_wait_s
        # Extra random pause added on top of a 429 wait (default 1-5 min) so we
        # always retry strictly AFTER SUMO's Retry-After window and desync retries.
        self.retry_jitter_min_s = retry_jitter_min_s
        self.retry_jitter_max_s = retry_jitter_max_s
        self._client = None

    def __enter__(self):
        # A persistent client keeps a cookie jar across calls, so any edge
        # cookie handed out on the first request is reused (mirrors the old
        # browser context's cookie reuse). follow_redirects matches a browser.
        self._client = httpx.Client(
            headers={
                "User-Agent": USER_AGENT,
                "Accept-Language": "en-US,en;q=0.9",
            },
            timeout=60.0,
            follow_redirects=True,
        )
        # Warm up by loading the home page once so any cookie is captured before
        # the API call. Best-effort: a failure here is not fatal — a genuine
        # block surfaces later as ChallengeError from fetch_json.
        try:
            self._client.get(HOME_URL)
        except httpx.HTTPError as e:
            print(f"home warm-up failed (continuing): {e}",
                  file=sys.stderr, flush=True)
        return self

    def __exit__(self, exc_type, exc, tb):
        if self._client is not None:
            self._client.close()
        return False

    def _raw_fetch(self, url):
        """Do one HTTP GET; return {status, json, snippet, retry_after}.

        Parses the body as JSON the same way the old in-page fetch did (json is
        None when the body is not valid JSON — e.g. an HTML challenge page)."""
        res = self._client.get(url, headers={"Accept": "application/json"})
        text = res.text
        try:
            body = json.loads(text)
        except ValueError:
            body = None
        return {
            "status": res.status_code,
            "json": body,
            "snippet": text[:300],
            "retry_after": res.headers.get("retry-after"),
        }
```

- [ ] **Step 6: Regenerate the lockfile**

Run: `uv sync`
Expected: `playwright` removed, `httpx` added; environment resolves.

- [ ] **Step 7: Run the full test file — old contract + new mapping**

Run: `uv run pytest tests/test_fetch_json.py -v`
Expected: **8 passed** (the 6 contract tests from A1 still green, plus the 2 mapping tests).

- [ ] **Step 8: Verify consumers still import and parse args (no signature drift)**

Run:
```bash
uv run python -c "import scrape_questions, scrape_answers, find_updated_days, check_schema, run_refresh; print('imports ok')"
uv run python scrape_questions.py --help >/dev/null && echo "questions --help ok"
```
Expected: `imports ok` and `questions --help ok` (the frozen API means no consumer edits were needed).

- [ ] **Step 9: Commit**

```bash
git add sumo.py pyproject.toml uv.lock tests/test_fetch_json.py
git commit -m "refactor(sumo): replace Playwright with httpx behind the frozen SumoBrowser API"
```

## Task A3: Confirm the HTTP path reaches the edge (expected pre-allowlist block)

No new code — a manual verification that the refactor talks to the real API and correctly *classifies the current block*, which is the honest end-to-end check until the IP is allowlisted.

**Files:** none.

- [ ] **Step 1: Run a single-day scrape against the live API**

Run: `uv run python scrape_questions.py 2026 7 1 2026 7 1 2>&1 | tail -20`
Expected (today, pre-allowlist): it fails, and the failure is a **`ChallengeError`** ("200 with HTML" / "browser may not have passed the challenge") — i.e. the HTTP client reached the Fastly edge and got the challenge page, exactly as designed. It must NOT be a Playwright/import error. Record the outcome in the PR description as the known pre-allowlist state.

- [ ] **Step 2: No commit** (verification only).

## Task A4: Browser-free container image

`python:3.12-slim` (not distroless) because the entrypoint (Task A5) needs `git` and a shell at runtime.

**Files:**
- Create: `Dockerfile`
- Create: `.dockerignore`

**Interfaces:**
- Produces: an image whose default entrypoint is `deploy/entrypoint.sh` (created in Task A5). This task builds the image with a placeholder CMD and Task A5 wires the entrypoint; to keep tasks independently testable, define the Dockerfile to copy `deploy/entrypoint.sh` now and have Task A5 create the file — so build this image only after A5, OR temporarily set `CMD ["python", "-c", "import sumo; print('ok')"]`. Use the temporary CMD here; A5 flips it to the entrypoint.

- [ ] **Step 1: Write the Dockerfile**

Create `Dockerfile`:
```dockerfile
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

# Browser-free image: the old Playwright/Chromium approach is dead (issue #28);
# this runs the plain-httpx scraper. git + a shell are needed at runtime because
# the entrypoint clones aaq-scraper, runs the refresh, and commits CSVs back.
FROM python:3.12-slim

# uv for dependency management (matches local/CI: uv sync / uv run).
COPY --from=ghcr.io/astral-sh/uv:0.5.11 /uv /usr/local/bin/uv

RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY . .

# Non-root. The clone/commit workdir is a writable emptyDir mounted at runtime
# (the root FS is read-only in the pod), so this user only needs to read /app.
RUN useradd --create-home --uid 65532 appuser
USER 65532

CMD ["python", "-c", "import sumo; print('image ok')"]
```

- [ ] **Step 2: Write `.dockerignore`**

Create `.dockerignore`:
```
.git
.venv
__pycache__
*.pyc
20*/
docs/
backfill-reports/
tests/
```
(The data dirs `20*/` are excluded from the image — the pod clones fresh data at runtime; baking hundreds of MB of CSVs into the image is wasteful.)

- [ ] **Step 3: Build for arm64**

Run: `docker buildx build --platform linux/arm64 -t aaq-scraper:dev --load .`
Expected: build succeeds. (On an arm64 host `--load` works directly; on amd64 it still builds via emulation.)

- [ ] **Step 4: Smoke-test the image**

Run: `docker run --rm aaq-scraper:dev`
Expected: prints `image ok` (imports resolve, no Playwright).

- [ ] **Step 5: Commit**

```bash
git add Dockerfile .dockerignore
git commit -m "build: browser-free arm64 image for the scraper CronJob"
```

## Task A5: Container entrypoint — clone, refresh, commit, push

Reproduces the GitHub Actions `checkout → run_refresh → commit → push (rebase-retry)` flow inside the pod, using a PAT. Includes an integration test against a **local bare repo** (no network).

**Files:**
- Create: `deploy/entrypoint.sh`
- Create: `tests/test_entrypoint.py`
- Modify: `Dockerfile` (flip CMD to the entrypoint)

**Interfaces:**
- Consumes (env vars): `GIT_REPO_URL` (https URL of aaq-scraper), `GIT_BRANCH` (default `main`), `GITHUB_TOKEN` (fine-grained PAT, Contents:rw), `GIT_AUTHOR_NAME`, `GIT_AUTHOR_EMAIL`, `REFRESH_ARGS` (default `--soft-deadline 40 --max-429-wait 120`), and optional `REFRESH_CMD` (override the scrape command; used by tests).
- Produces: a commit `Hourly refresh <ts>` on `GIT_BRANCH` when `20*/` or `.refresh-hwm` changed; clean exit 0 with no commit when nothing changed; exit 75 propagated as "deferred".

- [ ] **Step 1: Write the entrypoint**

Create `deploy/entrypoint.sh`:
```bash
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

WORKDIR="$(mktemp -d)"
# Embed the token in the clone URL (https://x-access-token:TOKEN@github.com/...).
AUTH_URL="$(printf '%s' "$GIT_REPO_URL" | sed -E "s#https://#https://x-access-token:${GITHUB_TOKEN}@#")"

git clone --branch "$GIT_BRANCH" "$AUTH_URL" "$WORKDIR/repo"
cd "$WORKDIR/repo"
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

git add 20*/ .refresh-hwm 2>/dev/null || git add 20*/
if git diff --cached --quiet; then
  echo "No changes to commit."
  exit 0
fi
git commit -m "Hourly refresh $(date -u +%Y-%m-%dT%H:%MZ)"

# main also receives pushes from manual backfills, so rebase onto latest and
# retry with backoff rather than failing on a rejected push.
for attempt in 1 2 3 4 5; do
  if ! git pull --rebase origin "$GIT_BRANCH"; then
    git rebase --abort || true
    echo "rebase failed (attempt $attempt/5); retrying"; sleep $((attempt * 5)); continue
  fi
  if git push origin "$GIT_BRANCH"; then
    echo "pushed on attempt $attempt"; exit 0
  fi
  echo "push rejected (attempt $attempt/5); retrying"; sleep $((attempt * 5))
done
echo "ERROR: could not push after 5 attempts"; exit 1
```

- [ ] **Step 2: Make it executable**

Run: `chmod +x deploy/entrypoint.sh`

- [ ] **Step 3: Write the integration test (local bare repo, no network, no token)**

Create `tests/test_entrypoint.py`:
```python
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""Integration test for deploy/entrypoint.sh using a local bare repo.

Exercises the clone -> (fake) refresh -> commit -> push loop without a network
or a real token. The token is stripped from a file:// URL by git, so a dummy
value is fine."""
import os
import subprocess
from pathlib import Path


def _run(cmd, cwd=None, env=None):
    return subprocess.run(cmd, cwd=cwd, env=env, check=True,
                          capture_output=True, text=True)


def _git_env():
    env = dict(os.environ)
    env.update({
        "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@e",
        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@e",
    })
    return env


def test_entrypoint_commits_new_csv(tmp_path):
    genv = _git_env()
    # A bare "origin" with one commit on main containing an empty data dir marker.
    origin = tmp_path / "origin.git"
    _run(["git", "init", "--bare", "-b", "main", str(origin)], env=genv)
    seed = tmp_path / "seed"
    _run(["git", "clone", str(origin), str(seed)], env=genv)
    (seed / "2099").mkdir()
    (seed / "2099" / ".keep").write_text("")
    _run(["git", "add", "-A"], cwd=seed, env=genv)
    _run(["git", "commit", "-m", "seed"], cwd=seed, env=genv)
    _run(["git", "push", "origin", "main"], cwd=seed, env=genv)

    repo_root = Path(__file__).resolve().parent.parent
    env = _git_env()
    env.update({
        "GIT_REPO_URL": f"file://{origin}",
        "GITHUB_TOKEN": "dummy",
        "GIT_BRANCH": "main",
        # Fake refresh: write a CSV under 2099/ so there is something to commit.
        "REFRESH_CMD": "echo id > 2099/questions-test-2099-01-01.csv",
    })
    _run(["bash", str(repo_root / "deploy" / "entrypoint.sh")], env=env)

    # The bare origin now has the new file on main.
    log = _run(["git", "log", "--oneline", "-1", "main"], cwd=origin, env=genv)
    assert "Hourly refresh" in log.stdout
    show = _run(["git", "show",
                 "main:2099/questions-test-2099-01-01.csv"], cwd=origin, env=genv)
    assert show.stdout.strip() == "id"


def test_entrypoint_no_changes_no_commit(tmp_path):
    genv = _git_env()
    origin = tmp_path / "origin.git"
    _run(["git", "init", "--bare", "-b", "main", str(origin)], env=genv)
    seed = tmp_path / "seed"
    _run(["git", "clone", str(origin), str(seed)], env=genv)
    (seed / "2099").mkdir()
    (seed / "2099" / ".keep").write_text("")
    _run(["git", "add", "-A"], cwd=seed, env=genv)
    _run(["git", "commit", "-m", "seed"], cwd=seed, env=genv)
    _run(["git", "push", "origin", "main"], cwd=seed, env=genv)

    repo_root = Path(__file__).resolve().parent.parent
    env = _git_env()
    env.update({
        "GIT_REPO_URL": f"file://{origin}", "GITHUB_TOKEN": "dummy",
        "GIT_BRANCH": "main", "REFRESH_CMD": "true",  # writes nothing
    })
    _run(["bash", str(repo_root / "deploy" / "entrypoint.sh")], env=env)

    count = _run(["git", "rev-list", "--count", "main"], cwd=origin, env=genv)
    assert count.stdout.strip() == "1"  # still just the seed commit
```

- [ ] **Step 4: Run the entrypoint tests**

Run: `uv run pytest tests/test_entrypoint.py -v`
Expected: **2 passed** — a new CSV produces one `Hourly refresh` commit; an empty run produces none.

- [ ] **Step 5: Flip the Dockerfile CMD to the entrypoint**

In `Dockerfile`, replace the final line:
```dockerfile
CMD ["python", "-c", "import sumo; print('image ok')"]
```
with:
```dockerfile
ENTRYPOINT ["deploy/entrypoint.sh"]
```

- [ ] **Step 6: Rebuild and confirm the entrypoint is wired (fails fast without env, as designed)**

Run: `docker run --rm aaq-scraper:dev; echo "exit=$?"` after `docker buildx build --platform linux/arm64 -t aaq-scraper:dev --load .`
Expected: exits non-zero with `set GIT_REPO_URL` (the `:?` guard) — proving the entrypoint runs.

- [ ] **Step 7: Commit**

```bash
git add deploy/entrypoint.sh tests/test_entrypoint.py Dockerfile
git commit -m "feat: pod entrypoint clones, refreshes, and commits CSVs back to git"
```

## Task A6: Move the high-water mark into the repo

**Files:**
- Modify: `.gitignore` (remove the `/.refresh-hwm` ignore)

- [ ] **Step 1: Un-ignore the mark**

In `.gitignore`, delete these two lines:
```
# Incremental-refresh high-water mark (persisted via Actions cache, not git)
/.refresh-hwm
```

- [ ] **Step 2: Verify it is no longer ignored**

Run: `git check-ignore .refresh-hwm; echo "exit=$?"`
Expected: `exit=1` (not ignored). No `.refresh-hwm` file exists yet; the first CronJob run creates and commits it, and `run_refresh.py`'s 26h lookback covers its initial absence.

- [ ] **Step 3: Commit**

```bash
git add .gitignore
git commit -m "chore: track .refresh-hwm in git (state moves off the Actions cache)"
```

## Task A7: Image-build CI workflow (OIDC → ECR)

**Files:**
- Create: `.github/workflows/aaq-scraper-image.yml`

**Interfaces:**
- Consumes: GitHub Environment `image-aaq-scraper` (main-only) and repo variable `IMAGE_PUSH_ROLE_ARN` — the OIDC push role created in Task B1. Document this prerequisite in the workflow header.
- Produces: an immutable image `…/aaq-scraper:git-<short-sha>` in the shared ECR on every push to `main` that touches scraper source.

- [ ] **Step 1: Write the workflow**

Create `.github/workflows/aaq-scraper-image.yml` (adapt the `thundermail-ticket-spike-monitor-image.yml` external-repo-trust variant found in `platform-infrastructure/.github/workflows/`; copy its exact structure, changing names/paths):
```yaml
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Build & push the scraper image to the shared ECR via GitHub OIDC.
# One-time setup (see platform-infrastructure Task B1):
#   - GitHub Environment "image-aaq-scraper" (protected to main)
#   - repo variable IMAGE_PUSH_ROLE_ARN = the push-only role ARN
name: Build scraper image

on:
  push:
    branches: [main]
    paths:
      - "**.py"
      - "pyproject.toml"
      - "uv.lock"
      - "Dockerfile"
      - "deploy/entrypoint.sh"
      - ".github/workflows/aaq-scraper-image.yml"
  workflow_dispatch:
    inputs:
      tag:
        description: "Explicit image tag (default git-<sha>)"
        required: false

permissions:
  contents: read
  id-token: write

jobs:
  build:
    runs-on: ubuntu-latest
    environment: image-aaq-scraper
    env:
      ECR: 826971876779.dkr.ecr.us-east-1.amazonaws.com
      REPO: aaq-scraper
    steps:
      - uses: actions/checkout@v5
      - name: Compute tag
        id: tag
        run: |
          T="${{ github.event.inputs.tag }}"
          [ -n "$T" ] || T="git-$(git rev-parse --short HEAD)"
          echo "tag=$T" >> "$GITHUB_OUTPUT"
      - name: Configure AWS credentials (OIDC)
        uses: aws-actions/configure-aws-credentials@v4
        with:
          role-to-assume: ${{ vars.IMAGE_PUSH_ROLE_ARN }}
          aws-region: us-east-1
      - name: Login to ECR
        uses: aws-actions/amazon-ecr-login@v2
      - name: Set up Buildx
        uses: docker/setup-buildx-action@v3
      - name: Build and push
        uses: docker/build-push-action@v6
        with:
          context: .
          platforms: linux/arm64
          push: true
          tags: ${{ env.ECR }}/${{ env.REPO }}:${{ steps.tag.outputs.tag }}
```

- [ ] **Step 2: Lint the workflow**

Run: `command -v actionlint >/dev/null && actionlint .github/workflows/aaq-scraper-image.yml || echo "actionlint not installed; skip (validate YAML manually)"`
Expected: no errors (or a clean skip). Also confirm valid YAML: `uv run python -c "import yaml,sys; yaml.safe_load(open('.github/workflows/aaq-scraper-image.yml')); print('yaml ok')"`.

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/aaq-scraper-image.yml
git commit -m "ci: build and push the scraper image to ECR via OIDC"
```

## Task A8: Disable `schema-check.yml` with a reviewer note

**Files:**
- Modify: `.github/workflows/schema-check.yml`

- [ ] **Step 1: Disable the triggers, keep the file**

In `.github/workflows/schema-check.yml`, replace the `on:` block with a manual-only trigger and add a header note. Comment out the `schedule:` so it stops firing (and stops opening spurious `api-blocked` issues once the browser path is gone), but keep `workflow_dispatch` so it can still be run by hand:
```yaml
# NOTE (2026-07-13, #28): parked pending its own migration to the k8s runner.
# check_schema.py hits the live SUMO API and is blocked by the same Fastly
# challenge as the scraper, so the daily schedule is disabled to stop spurious
# api-blocked issues. Re-enable / migrate after the egress IP is allowlisted.
on:
  # schedule:
  #   - cron: "30 6 * * *"   # disabled — see NOTE above
  workflow_dispatch:
```

- [ ] **Step 2: Validate YAML**

Run: `uv run python -c "import yaml; yaml.safe_load(open('.github/workflows/schema-check.yml')); print('yaml ok')"`
Expected: `yaml ok`.

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/schema-check.yml
git commit -m "ci: park schema-check (blocked by the same challenge; #28)"
```

## Task A9: Documentation

**Files:**
- Modify: `CLAUDE.md` (Automation section), `README.md`

- [ ] **Step 1: Update CLAUDE.md**

In the **Automation (GitHub Actions)** section of `CLAUDE.md`, add a subsection documenting: the scraper now runs as an ArgoCD CronJob on the workloads cluster (`services/aaq-scraper/` in `platform-infrastructure`); `sumo.py` uses `httpx` (Playwright dropped, #28); `.refresh-hwm` is now committed to the repo (not the Actions cache); the pod pushes commits via a fine-grained PAT from AWS Secrets Manager (ESO); `schema-check.yml` is parked; `scrape.yml` stays until cutover. Keep it to a tight paragraph in the existing style.

- [ ] **Step 2: Update README.md**

In `README.md`, update the "drive a real browser" framing to note the httpx client + k8s deployment, and point at the design/plan docs under `docs/superpowers/`.

- [ ] **Step 3: Full test sweep before pushing**

Run: `uv run pytest -v`
Expected: all tests pass (fetch_json contract + httpx mapping + entrypoint).

- [ ] **Step 4: Commit and push the branch to PR #44**

```bash
git add CLAUDE.md README.md
git commit -m "docs: document the k8s CronJob deployment and httpx migration"
git push
```

---

# Phase B — `platform-infrastructure` repo (separate PR)

Executes in the `platform-infrastructure` checkout (`/home/aatchison/src/tb/platform-infrastructure`), on a new branch. These tasks use verification commands rather than unit tests (infra-as-code). Before writing manifests, **open the canonical example** `services/bamboohr-cal-sync/deploy/` and `argocd/workloads/apps/thundermail-ticket-spike-monitor.yaml` and copy their structure — especially any block this plan says to "copy verbatim".

## Task B1: Pulumi — ECR repo + OIDC push role

**Files:**
- Modify: `pulumi/environments/mzla-shared-services/config.prod.yaml`

- [ ] **Step 1: Add the ECR repo and push role**

Under `ecr.repositories`, add:
```yaml
    aaq-scraper:
      description: "SUMO AAQ scraper CronJob image"
```
Under `github_oidc.roles`, add a push-only role trusting the scraper repo (mirror the existing `thundermail-ticket-spike-monitor-image` role that trusts an external repo — copy its inline ECR-push policy verbatim, changing only the repository resource ARN to `…:repository/aaq-scraper` and the trusted repo to `thunderbird/aaq-scraper`):
```yaml
    aaq-scraper-image:
      github_repo: thunderbird/aaq-scraper
      github_environment: image-aaq-scraper
      managed_policy_arns: []
      # inline policy: ecr:GetAuthorizationToken + push actions scoped to
      #   arn:aws:ecr:us-east-1:826971876779:repository/aaq-scraper
```

- [ ] **Step 2: Preview**

Run: `cd pulumi/environments/mzla-shared-services && pulumi preview`
Expected: shows **create** for the ECR repo `aaq-scraper` and the OIDC role, no unexpected deletes/replacements.

- [ ] **Step 3: Apply (with approval), then record outputs**

Run: `pulumi up` (confirm). Note the created role ARN — it becomes `IMAGE_PUSH_ROLE_ARN` in the aaq-scraper repo (Task A7 prerequisite).

- [ ] **Step 4: Commit**

```bash
git add pulumi/environments/mzla-shared-services/config.prod.yaml
git commit -m "pulumi: add aaq-scraper ECR repo + OIDC push role"
```

## Task B2: Verify / ensure a stable NAT egress EIP (the IP for #27)

The whole effort's value depends on this. Investigation + possible small change.

**Files:** (only if a change is needed) the workloads-cluster networking Pulumi program.

- [ ] **Step 1: Find the workloads cluster's egress path**

Run (with the workloads AWS profile):
```bash
aws ec2 describe-nat-gateways --filter "Name=state,Values=available" \
  --query "NatGateways[].{id:NatGatewayId,eip:NatGatewayAddresses[].PublicIp,vpc:VpcId}" \
  --region eu-central-1 --output table
```
Expected: identify the NAT gateway(s) for the `mzla-eks-workloads01` VPC and their public EIP(s).

- [ ] **Step 2: Confirm stability**

Confirm the NAT gateway uses an **allocated Elastic IP** (stable across restarts), not an auto-assigned address, by checking the `AllocationId` is a managed EIP in the Pulumi/networking code. If it is already a fixed EIP: **record that IP** — it is what #27 hands Mozilla. If egress is ephemeral or per-AZ with multiple IPs: add/pin a single NAT EIP (or document all egress IPs for Mozilla to allowlist) as a change to the networking Pulumi program, `pulumi preview` → `up`.

- [ ] **Step 3: Post the egress IP to #27**

Add a comment on issue #27 with the confirmed egress IP(s) so Roland can forward to Mozilla for the allowlist. (No code change / commit if the EIP already existed.)

## Task B3: Kubernetes manifests

**Files:**
- Create: `services/aaq-scraper/deploy/serviceaccount.yaml`
- Create: `services/aaq-scraper/deploy/configmap.yaml`
- Create: `services/aaq-scraper/deploy/externalsecret.yaml`
- Create: `services/aaq-scraper/deploy/cronjob.yaml`
- Create: `services/aaq-scraper/deploy/vmrule.yaml`

**Interfaces:**
- Consumes: the image from Task A7; the SM secret `mzla/shared-services/aaq-scraper` (Task B5); the `aws-secrets-manager` ClusterSecretStore (existing on the workloads cluster).
- Produces: an hourly CronJob in namespace `aaq-scraper`.

- [ ] **Step 1: ServiceAccount** — create `serviceaccount.yaml` (sync-wave "0"):
```yaml
# MPL header
apiVersion: v1
kind: ServiceAccount
metadata:
  name: aaq-scraper
  namespace: aaq-scraper
  annotations:
    argocd.argoproj.io/sync-wave: "0"
automountServiceAccountToken: false
```

- [ ] **Step 2: ConfigMap** — create `configmap.yaml` (sync-wave "0"), non-secret env:
```yaml
# MPL header
apiVersion: v1
kind: ConfigMap
metadata:
  name: aaq-scraper-config
  namespace: aaq-scraper
  annotations:
    argocd.argoproj.io/sync-wave: "0"
data:
  GIT_REPO_URL: "https://github.com/thunderbird/aaq-scraper.git"
  GIT_BRANCH: "main"
  GIT_AUTHOR_NAME: "aaq-scraper-bot"
  GIT_AUTHOR_EMAIL: "aaq-scraper-bot@thunderbird.net"
  REFRESH_ARGS: "--soft-deadline 40 --max-429-wait 120"
```

- [ ] **Step 3: ExternalSecret** — create `externalsecret.yaml` (sync-wave "0"). Copy the `external-secrets.io/v1` shape from an existing app; map the PAT:
```yaml
# MPL header
# PREREQUISITE: create SM secret mzla/shared-services/aaq-scraper with key
# `githubToken` = a fine-grained PAT on thunderbird/aaq-scraper, Contents: RW.
apiVersion: external-secrets.io/v1
kind: ExternalSecret
metadata:
  name: aaq-scraper
  namespace: aaq-scraper
  annotations:
    argocd.argoproj.io/sync-wave: "0"
spec:
  refreshInterval: 1h
  secretStoreRef:
    name: aws-secrets-manager-shared   # cross-account store for mzla/shared-services/*
    kind: ClusterSecretStore
  target:
    name: aaq-scraper
    creationPolicy: Owner
  data:
    - secretKey: GITHUB_TOKEN
      remoteRef:
        key: mzla/shared-services/aaq-scraper
        property: githubToken
```
(Confirm the correct store name on the workloads cluster — the explore notes both `aws-secrets-manager` and `aws-secrets-manager-shared`; `mzla/shared-services/*` lives in the shared account, so use `-shared`.)

- [ ] **Step 4: CronJob** — create `cronjob.yaml` (sync-wave "1"). Copy the hardened `securityContext` from `bamboohr-cal-sync`; add the writable emptyDir workdir:
```yaml
# MPL header
apiVersion: batch/v1
kind: CronJob
metadata:
  name: aaq-scraper
  namespace: aaq-scraper
  annotations:
    argocd.argoproj.io/sync-wave: "1"
spec:
  schedule: "0 * * * *"
  timeZone: "Etc/UTC"
  concurrencyPolicy: Forbid
  startingDeadlineSeconds: 300
  successfulJobsHistoryLimit: 3
  failedJobsHistoryLimit: 3
  jobTemplate:
    spec:
      backoffLimit: 1
      activeDeadlineSeconds: 3300
      ttlSecondsAfterFinished: 3600
      template:
        spec:
          serviceAccountName: aaq-scraper
          restartPolicy: OnFailure
          securityContext:
            runAsNonRoot: true
            runAsUser: 65532
            seccompProfile: { type: RuntimeDefault }
          containers:
            - name: aaq-scraper
              image: 826971876779.dkr.ecr.us-east-1.amazonaws.com/aaq-scraper:REPLACE_WITH_git-<sha>
              envFrom:
                - configMapRef: { name: aaq-scraper-config }
                - secretRef: { name: aaq-scraper }
              env:
                - name: HOME
                  value: /work        # git config/creds must land on the writable volume
              resources:
                requests: { cpu: 100m, memory: 256Mi }
                limits: { cpu: "1", memory: 1Gi }
              securityContext:
                allowPrivilegeEscalation: false
                readOnlyRootFilesystem: true
                capabilities: { drop: [ALL] }
              volumeMounts:
                - name: work
                  mountPath: /work
          volumes:
            - name: work
              emptyDir: {}
```

- [ ] **Step 5: VMRule** — create `vmrule.yaml` (sync-wave "1"), alerting on CronJob failure via kube-state-metrics, shipped **suppressed** until cutover:
```yaml
# MPL header
# NOTE: the job fails every hour until our egress IP is allowlisted (#27), so
# this alert is intentionally inert until then. At cutover, remove the always-
# false guard (`and vector(0)`) to arm it.
apiVersion: operator.victoriametrics.com/v1beta1
kind: VMRule
metadata:
  name: aaq-scraper
  namespace: aaq-scraper
  annotations:
    argocd.argoproj.io/sync-wave: "1"
spec:
  groups:
    - name: aaq-scraper
      rules:
        - alert: AaqScraperJobFailing
          expr: |
            (max(kube_job_status_failed{namespace="aaq-scraper"}) > 0) and vector(0)
          for: 2h
          labels: { severity: warning }
          annotations:
            summary: "AAQ scraper CronJob has been failing"
            description: "The aaq-scraper CronJob has failed for >2h. (Suppressed until IP allowlisting — #27.)"
```

- [ ] **Step 6: Validate all manifests**

Run: `kubeconform -strict -ignore-missing-schemas services/aaq-scraper/deploy/*.yaml` (or `kubectl apply --dry-run=client -f services/aaq-scraper/deploy/`).
Expected: all valid (CRDs like ExternalSecret/VMRule need `-ignore-missing-schemas`).

- [ ] **Step 7: Commit**

```bash
git add services/aaq-scraper/deploy/
git commit -m "feat: aaq-scraper CronJob manifests for the workloads cluster"
```

## Task B4: ArgoCD Application

**Files:**
- Create: `argocd/workloads/apps/aaq-scraper.yaml`

- [ ] **Step 1: Write the Application**

Copy `argocd/workloads/apps/thundermail-ticket-spike-monitor.yaml` verbatim and change name/path/namespace. Keep its `destination.server` (the workloads cluster URL), the standard ESO `ignoreDifferences` block, and `syncPolicy` **exactly as in that file**:
```yaml
# MPL header
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: aaq-scraper
  namespace: argocd
  annotations:
    argocd.argoproj.io/sync-wave: "6"
spec:
  project: workloads
  source:
    repoURL: https://github.com/thunderbird/platform-infrastructure.git
    targetRevision: main
    path: services/aaq-scraper/deploy
  destination:
    server: https://9AFFF1C78D073BA9579F882E6626DE0A.sk1.eu-central-1.eks.amazonaws.com  # confirm from thundermail app
    namespace: aaq-scraper
  ignoreDifferences:
    # COPY VERBATIM from thundermail-ticket-spike-monitor.yaml (ESO block)
    []
  syncPolicy:
    automated: { prune: true, selfHeal: true }
    syncOptions: [ CreateNamespace=true, ServerSideApply=true, RespectIgnoreDifferences=true ]
```

- [ ] **Step 2: Validate**

Run: `kubeconform -strict -ignore-missing-schemas argocd/workloads/apps/aaq-scraper.yaml`
Expected: valid.

- [ ] **Step 3: Commit**

```bash
git add argocd/workloads/apps/aaq-scraper.yaml
git commit -m "feat: register aaq-scraper ArgoCD app on the workloads cluster"
```

## Task B5: Create the AWS Secrets Manager secret (runbook)

**Files:** none (operational).

- [ ] **Step 1: Mint a fine-grained PAT** on `thunderbird/aaq-scraper` with **Contents: Read and write** only, no expiry-less (set a calendar reminder to rotate).

- [ ] **Step 2: Store it in Secrets Manager** (shared account, us-east-1):
```bash
aws secretsmanager create-secret \
  --name mzla/shared-services/aaq-scraper \
  --secret-string '{"githubToken":"github_pat_..."}' \
  --region us-east-1
```
Expected: secret created; ESO will materialize it into the `aaq-scraper` Secret on next refresh.

## Task B6: Push Phase B branch and open the platform-infrastructure PR

- [ ] **Step 1: Push and open PR**

```bash
git push -u origin aaq-scraper-cronjob
gh pr create --repo thunderbird/platform-infrastructure --base main \
  --title "Add aaq-scraper CronJob (workloads cluster)" \
  --body "Deploys the SUMO AAQ scraper as an hourly CronJob on the workloads cluster. Pairs with thunderbird/aaq-scraper#44. Bump the image tag once the first image is built. Relates to thunderbird/aaq-scraper#27, #28."
```

---

# Cutover (follow-up, after Mozilla allowlists — not in these PRs)

- Bump the CronJob `image:` tag to the first built `git-<sha>` (platform-infra PR); confirm ArgoCD syncs and a manually-triggered Job commits real CSVs.
- Arm the VMRule (remove `and vector(0)`).
- Disable `scrape.yml` in the aaq-scraper repo.
- Decide on `schema-check.yml` migration.

---

## Self-Review

**Spec coverage:** §1 placement/egress → A (cluster consts) + B2/B4; §2 image+refactor → A2/A4; §3 image build → A4/A7/B1; §4 manifests → B3; §5 secrets → B3(externalsecret)/B5; §6 output/state → A5/A6; §7 alerting → B3(vmrule); §8 cutover → dedicated section. Out-of-scope items (schema-check disable → A8; scrape.yml stays → cutover) covered. No gaps.

**Placeholder scan:** The only intentional placeholders are `image: …:REPLACE_WITH_git-<sha>` (bumped at release, per the human-gated-deploy constraint) and two "copy verbatim from existing file" instructions (ESO `ignoreDifferences` block, thundermail push-role policy) — deliberate, because transcribing those blindly risks transcription bugs; the plan names the exact source file. No TBD/TODO/"handle edge cases".

**Type consistency:** `_raw_fetch` returns `{status, json, snippet, retry_after}` everywhere (A1 stub, A2 impl, A2 httpx test). Env var names match between `entrypoint.sh` (A5), `configmap.yaml` and `externalsecret.yaml` (B3): `GIT_REPO_URL`, `GIT_BRANCH`, `GIT_AUTHOR_NAME`, `GIT_AUTHOR_EMAIL`, `REFRESH_ARGS`, `GITHUB_TOKEN`. Image ref (`826971876779….amazonaws.com/aaq-scraper`) consistent across A7/B1/B3.
