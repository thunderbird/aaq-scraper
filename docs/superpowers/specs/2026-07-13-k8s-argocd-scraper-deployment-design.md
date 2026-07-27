<!--
This Source Code Form is subject to the terms of the Mozilla Public
License, v. 2.0. If a copy of the MPL was not distributed with this
file, You can obtain one at https://mozilla.org/MPL/2.0/.
-->

# Design: run the AAQ scraper as an ArgoCD CronJob on the workloads EKS cluster

**Date:** 2026-07-13
**Status:** approved; Phase A implemented (PR #44), Phase B implemented
(platform-infrastructure)

> **Update 2026-07-27 — the "blocked until allowlisted" premise below is
> obsolete.** This document was written assuming the API would stay blocked
> until Mozilla allowlisted a new egress IP, so the deployment was scoped as
> scaffolding that would sit idle until then. That is wrong: the workloads
> cluster's NAT egress IPs are **already allowlisted**. Verified 2026-07-27 from
> a pod in each AZ using the scraper's exact httpx client — `3.67.52.124`
> (eu-central-1a) and `63.182.70.185` (eu-central-1b) both return HTTP 200 +
> JSON, while the identical request from a non-allowlisted network returns the
> challenge HTML. The earlier "still blocked" conclusion came from testing on a
> workstation rather than in the cluster.
>
> Consequences: the CronJob works as soon as it is deployed (no wait on #27); its
> alerting ships **armed**, not suppressed; and the remaining gate is purely
> mechanical (build the image, create the PAT secret, unsuspend). Sections below
> that describe waiting for an allowlist should be read as historical.
**Related:** #27 (Mozilla API-access / IP-allowlist request), #28 (move off GitHub
Actions to a self-hosted runner / static egress IP), github-action-thunderbird-aaq#34,
bitergia-deploy#50.

## Problem

As of ~2026-07-09 the SUMO `/api/2/` API sits behind a Fastly bot-challenge that
fingerprints the **automated browser itself** — it blocks headless *and* headed
Playwright/real-Chrome, so the "drive a real browser past the challenge" approach
that resolved #34 no longer works, and there is **no path forward on GitHub
Actions** (its shared, rotating egress IPs cannot be allowlisted). The agreed fix
(#27/#28) is: run from an environment with a **single, stable egress IP**, hand
that IP to Mozilla to allowlist at the edge, and then call the API directly over
**plain HTTP** (no browser).

This spec covers the **egress-IP-first scaffolding**: build the full ArgoCD
CronJob deployment now so it (1) produces the known static egress IP we hand
Mozilla, and (2) starts scraping successfully the moment that IP is allowlisted —
with no further code change required.

## Scope

**In scope**

- A new `aaq-scraper` CronJob app deployed to the **workloads EKS cluster**
  (`mzla-eks-workloads01`, eu-central-1, ArgoCD project `workloads`) via the
  existing app-of-apps, following the `bamboohr-cal-sync` /
  `thundermail-ticket-spike-monitor` pattern in `platform-infrastructure`.
- A lean, browser-free container image built from the `aaq-scraper` repo.
- A modest `sumo.py` refactor: **drop Playwright, fetch over a plain HTTP
  client**, preserving the existing `fetch_json` retry/deferral contract.
- Pulumi additions: ECR repo + push-only GitHub-OIDC role; **confirm the
  workloads cluster egresses via a stable NAT-gateway EIP** (the IP we hand
  Mozilla).
- Secrets via External Secrets Operator (ESO) → AWS Secrets Manager: a
  **fine-grained GitHub PAT** the pod uses to commit/push CSVs back to
  `aaq-scraper`.
- CronJob-failure alerting via a `VMRule` over existing kube-state-metrics,
  shipped **suppressed** (the job fails hourly until allowlisting) with a note to
  un-suppress at cutover.

**Out of scope (tracked separately)**

- Actually obtaining the Mozilla allowlist — that is #27, an upstream ask.
- Migrating `schema-check.yml` to k8s. It also queries the live API and will
  break the same way, so **this effort disables that workflow** (with a
  reviewer note in the file) and leaves its migration for a follow-up.
- `kitsune-api-watch.yml` — hits only the GitHub API, unaffected, stays in
  Actions.
- Disabling `scrape.yml` — stays running until the k8s job is verified green
  post-allowlist, then disabled in a follow-up (no flag-day).

## Approach

### 1. Placement & the egress IP

Deploy to the **workloads cluster**, namespace **`aaq-scraper`** (CreateNamespace
via the ArgoCD Application). This is deliberate: **bitergia-deploy** — the other
Thunderbird consumer of SUMO `/api/2/` — already runs on this cluster, so its
NAT-gateway egress IP is shared. Co-locating means Mozilla allowlists **one IP
for both consumers** (this + bitergia #50), which is exactly the #27 ask.

**Critical early verification:** confirm (in the Pulumi networking for the
workloads cluster) that outbound traffic leaves via a NAT gateway with a **fixed
Elastic IP**, not a per-AZ ephemeral address. If it is already a stable EIP, that
IP is what #27 hands Mozilla — no new infra. If egress is ephemeral, add/confirm a
NAT EIP. This fact gates the entire effort's value and should be checked first in
the implementation plan.

### 2. Container image & the `sumo.py` refactor

- Base: `python:3.12-slim` (or distroless-python), **`linux/arm64`**, run as
  nonroot. **No Chromium** — the image is small and browser-free.
- Dependency change: replace `playwright` with a plain HTTP client (`httpx`).
- **`sumo.py`:** replace the Playwright `SumoBrowser` (launch Chromium → acquire
  challenge cookies → in-page `page.evaluate(fetch)`) with a direct HTTP client
  that calls `API_BASE` endpoints. **Preserve the existing `fetch_json`
  contract** so nothing downstream changes:
  - exponential backoff on HTTP 429 (honour `Retry-After`), 5xx, and a
    200-but-non-JSON hiccup;
  - `RateLimitDeferral` when a 429 wait exceeds `max_429_wait_s`;
  - `ChallengeError` on a persistent 200-but-HTML (this is how the job will
    "fail loudly" every hour until the IP is allowlisted — the expected
    pre-allowlist state).
  - `DEFERRAL_EXIT_CODE` (75) and atomic CSV writes unchanged.
- `run_refresh.py`, `scrape_questions.py`, `scrape_answers.py`,
  `find_updated_days.py`, CSV flattening/escaping/redaction, and determinism are
  **unchanged**. The `SumoBrowser(headless=…)` constructor call sites adapt to the
  new client; the `--headless` CLI flag becomes a no-op (kept for compatibility)
  or is removed — decided in the plan.

### 3. Image build & release (aaq-scraper repo)

- New `.github/workflows/aaq-scraper-image.yml` (copy the
  `thundermail-ticket-spike-monitor-image.yml` external-repo-trust variant):
  build on push to `main` touching scraper source / `Dockerfile` / deps; auth via
  **GitHub OIDC** assuming a push-only role; `amazon-ecr-login` +
  `docker/build-push-action` (`platforms: linux/arm64`); push immutable
  `git-<short-sha>` tags to the shared ECR
  `826971876779.dkr.ecr.us-east-1.amazonaws.com/aaq-scraper`.
- **Deploys stay human-gated:** bump the `image:` tag in
  `services/aaq-scraper/deploy/cronjob.yaml` via a `platform-infrastructure` PR;
  ArgoCD syncs on merge. No image-updater.
- One-time setup: GitHub Environment `image-aaq-scraper` (main-only) + repo var
  `IMAGE_PUSH_ROLE_ARN` in the aaq-scraper repo.

### 4. Kubernetes manifests (`platform-infrastructure/services/aaq-scraper/deploy/`)

Raw YAML (no Kustomize/Helm), ordered by `argocd.argoproj.io/sync-wave`:

- `serviceaccount.yaml` (wave 0) — dedicated SA, `automountServiceAccountToken:
  false`, no IRSA (needs only HTTPS egress + ESO-materialized secrets).
- `configmap.yaml` (wave 0) — non-secret env (target repo URL/branch, product
  slugs, refresh flags).
- `externalsecret.yaml` (wave 0) — `external-secrets.io/v1`, `ClusterSecretStore`
  `aws-secrets-manager`, target Secret `aaq-scraper` mapping SM key
  `mzla/shared-services/aaq-scraper` → env-named keys (see §5).
- `cronjob.yaml` (wave 1):
  - `schedule: "0 * * * *"`, `timeZone: "Etc/UTC"`.
  - `concurrencyPolicy: Forbid` (equivalent intent to today's
    `cancel-in-progress`), `startingDeadlineSeconds`, `successfulJobsHistoryLimit`
    / `failedJobsHistoryLimit: 3`.
  - `jobTemplate`: `backoffLimit`, `activeDeadlineSeconds` ~3300 (≈ the current
    55-min GHA hard cap; keep it above the 40-min soft deadline),
    `ttlSecondsAfterFinished`, `restartPolicy: OnFailure`.
  - Hardened `securityContext` (`runAsNonRoot`, `readOnlyRootFilesystem`,
    `allowPrivilegeEscalation: false`, `capabilities: drop [ALL]`,
    `seccompProfile: RuntimeDefault`). Note: git clone/commit needs a **writable
    workdir** — mount an `emptyDir` for the checkout since the root FS is
    read-only.
  - `envFrom` the ConfigMap + the ESO Secret.
  - Command runs the entrypoint (see §6) which wraps
    `run_refresh.py --soft-deadline 40 --max-429-wait 120` — same flags the
    hourly workflow uses today (exit-75 deferral semantics preserved).
- `vmrule.yaml` (wave 1) — see §7.

### 5. Secrets (ESO → AWS Secrets Manager)

- SM secret `mzla/shared-services/aaq-scraper` holding a **fine-grained GitHub
  PAT** scoped to the `aaq-scraper` repo with **Contents: read/write** only,
  materialized to an env var / git credential in the pod.
- Created manually in Secrets Manager before first deploy (documented in the
  `externalsecret.yaml` header, per repo convention).
- The `ExternalSecret` Application manifest carries the standard ESO
  `ignoreDifferences` block (copied verbatim from an existing app).
- PAT expiry is an operational risk: document a rotation reminder; the job
  simply fails to push (loudly) if it expires — no silent data loss.

### 6. Output & state persistence — stateless pod, CSVs in git

The pod reproduces the GitHub Actions checkout→run→commit→push flow itself,
preserving the "**CSVs tracked in git**" contract that downstream Ruby reports
depend on. An entrypoint script (baked into the image):

1. `git clone` (or shallow fetch) `aaq-scraper` `main` into the emptyDir workdir,
   using the PAT.
2. Read the high-water mark from **`.refresh-hwm` committed in the repo**.
3. Run `run_refresh.py --soft-deadline 40 --max-429-wait 120`.
4. `git add 20*/ .refresh-hwm`; if nothing changed, exit 0 (the deterministic
   "nothing to commit" path).
5. `git commit -m "Hourly refresh <ts>"` and push with the **same
   rebase-onto-latest-main retry/backoff loop** the workflow uses today
   (main receives concurrent pushes from manual backfills).

**High-water-mark change:** `.refresh-hwm` moves from the (now-unavailable)
Actions cache into the **repo** (un-gitignored, committed each active run) —
CLAUDE.md's documented "make it durable" option. The 26h `--lookback-hours`
fallback in `run_refresh.py` still covers a missing/stale mark, so a first run or
a wiped mark self-heals.

### 7. Alerting

- `vmrule.yaml` (VictoriaMetrics `VMRule`) alerting on **CronJob failure /
  last-success staleness** using existing **kube-state-metrics**
  (`kube_job_status_failed`, last-successful-run age) — **no app-side metrics
  push**, so the scraper needs no instrumentation change.
- Because the job **fails every hour until the IP is allowlisted**, ship the
  alert **suppressed/inhibited** with an inline comment, and un-suppress it at
  cutover (tracked in the cutover follow-up).

### 8. Cutover (follow-up, not this PR)

`scrape.yml` keeps running until the k8s CronJob is verified green after
allowlisting. Then, in a separate PR: disable `scrape.yml`, un-suppress the
VMRule, and (optionally) migrate `schema-check.yml`.

## Repos & files touched

**aaq-scraper repo**

- `sumo.py` — Playwright → HTTP client refactor (preserve `fetch_json` contract).
- `pyproject.toml` / `uv.lock` — drop `playwright`, add `httpx`.
- `Dockerfile` — new, arm64, nonroot, browser-free.
- entrypoint script (clone/run/commit/push) — new.
- `.github/workflows/aaq-scraper-image.yml` — new (OIDC ECR push).
- `.github/workflows/schema-check.yml` — **disabled** with a reviewer note.
- `.gitignore` — un-ignore `.refresh-hwm`; commit the current mark.
- `CLAUDE.md` / `README.md` — document the k8s deployment and the state-in-repo
  change.

**platform-infrastructure repo**

- `services/aaq-scraper/deploy/{serviceaccount,configmap,externalsecret,cronjob,vmrule}.yaml`
  — new.
- `argocd/workloads/apps/aaq-scraper.yaml` — new ArgoCD Application (project
  `workloads`, `path: services/aaq-scraper/deploy`, standard ESO
  `ignoreDifferences` + `syncPolicy`, `CreateNamespace=true`).
- `pulumi/environments/mzla-shared-services/config.prod.yaml` — add
  `ecr.repositories.aaq-scraper` and `github_oidc.roles.aaq-scraper-image`
  (trusting the `thunderbird/aaq-scraper` repo).
- Pulumi networking — **verify/ensure** the workloads NAT-gateway stable EIP.

## Risks & open items

- **Egress-IP assumption:** the whole effort's value depends on the workloads
  cluster having a stable, allowlistable NAT EIP. Verify first.
- **Post-allowlist unknown:** we don't yet know whether Mozilla will grant an IP
  allowlist vs. an API token, or whether plain HTTP from the allowlisted IP fully
  bypasses the challenge. The design accommodates either (a token would become
  another ESO secret + a header in the HTTP client).
- **PAT lifecycle:** expiry causes loud push failures (acceptable — no silent
  loss), but needs a rotation runbook.
- **Read-only root FS + git:** requires a writable emptyDir workdir; ensure the
  git config/credential helper writes there, not `$HOME`.
- **Determinism preserved:** the "no changes → nothing to commit" path must keep
  working so idle hours don't create empty commits.
