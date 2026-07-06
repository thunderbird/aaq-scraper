<!-- This Source Code Form is subject to the terms of the Mozilla Public
   - License, v. 2.0. If a copy of the MPL was not distributed with this
   - file, You can obtain one at https://mozilla.org/MPL/2.0/. -->

# Kitsune API commits affecting this scraper (since 2026-01-01)

Summary of commits to [mozilla/kitsune](https://github.com/mozilla/kitsune)
landed on or after **2026-01-01** that touch the endpoints this repo depends on:

- **Question & Answer API** — `/api/2/question/`, `/api/2/answer/`
  (served by `kitsune/questions/api.py`)
- **Knowledge Base API** — `/api/1/kb/` (served by `kitsune/wiki/api.py`)

The KB API (`/api/1/kb/`) is defined by `DocumentShortSerializer`
(fields: `id, title, slug`) and `DocumentDetailSerializer`
(fields: `id, title, slug, url, locale, products, topics, summary, html`).

Generated 2026-07-05. Compare live with:
`gh api "repos/mozilla/kitsune/commits?path=kitsune/questions/api.py&since=2026-01-01T00:00:00Z"`
(and the same for `kitsune/wiki/api.py`).

---

## Question & Answer API (`kitsune/questions/api.py`)

### `948e80af` — Do not expose system accounts (#7621) — 2026-06-17
**Behavioral change.** `get_profile()` now returns `None` for system accounts
(e.g. the SuMo bot, `Profile.AccountType.SYSTEM`), matching `RegularProfileManager`
(which the reverse `user.profile` accessor otherwise bypasses). Effect on the API:
questions/answers whose `creator`/`updated_by`/`taken_by`/`solution.creator` is a
system account now serialize that user field as empty instead of exposing the bot
profile.
- **Impact on scraper:** username-derived columns (`creator`, `updated_by`,
  `solved_by`, `taken_by`) may go blank where a system account was previously
  shown. Blank is expected, not schema drift.

### `29401be3` — Fix simple N+1 reads in question and answer APIs — 2026-06-16
**Performance only, no field change.** `get_profile()` reverted to reading through
the `user.profile` one-to-one relation (served from the relation cache) instead of
querying `Profile` directly. `QuestionViewSet.queryset` gained `select_related`/
`prefetch_related` on `creator__profile`, `updated_by__profile`, `taken_by__profile`,
`solution__creator__profile`, `product`, `topic`, `answers__creator__profile`,
`metadata_set`, `tags`; `AnswerViewSet.queryset` added `select_related` on
`creator__profile`, `updated_by__profile`.
- **Impact on scraper:** none to output; faster/more reliable large ordered
  queries (e.g. `?ordering=updated&updated__gt=...`, exactly what the refresh uses).

### `b877d17e` — Make question/answer API profile serialization read-only (#7591) (#7592) — 2026-06-16
**Reliability fix.** `GET` serialization previously called
`get_or_create_profile` per related user per row — an N+1 **write-on-read** that
intermittently timed out with HTTP 500 (then 200 on retry) on large ordered
result sets. `get_profile` now never creates a `Profile`; a missing profile
serializes as `None`.
- **Impact on scraper:** directly addresses the intermittent 500s the
  `updated`-based refresh (`?ordering=updated&updated__gt=...`) could hit.
  Co-authored by the Thunderbird team.

### `6a2e2e6a` — Upgrade to Django 5.2 LTS (#7208) — 2026-02-10
Repo-wide upgrade; `questions/api.py` only changed by +2/-2 (incidental to the
`DateTimeField` / `timezone.now` conversions across models). No API field or
behavior change. Datetime storage semantics are UTC-correct as before.
- **Impact on scraper:** none expected.

### `284f5cec` — Upgrade to python 3.14 — 2026-04-03
Mechanical: added `from typing import override` and `@override` decorators to
overridden methods. No API field or behavior change.
- **Impact on scraper:** none.

---

## Knowledge Base API (`kitsune/wiki/api.py`)

### `284f5cec` — Upgrade to python 3.14 — 2026-04-03
Only KB-API commit in the window. Mechanical: added `from typing import override`
and `@override` on `DocumentList.get_queryset` and `DocumentDetail.get_object`.
No serializer fields, filtering, or locale-negotiation behavior changed.
- **Impact on scraper:** none.

> Note: `kitsune/wiki/permissions.py` exists but had no commits in this window.
> The Django 5.2 upgrade (`6a2e2e6a`) touched `kitsune/wiki/models.py` but **not**
> `wiki/api.py`, and did not alter any field exposed by the KB serializers.

---

## Bottom line

- **KB API (`/api/1/kb/`): no behavioral changes** since 2026-01-01 — only the
  cosmetic python-3.14 `@override` edit.
- **Q&A API (`/api/2/question|answer/`): three substantive changes**, all
  2026-06-16/17 and all favorable to this scraper: two reliability fixes for the
  intermittent HTTP 500 on `updated`-ordered pulls (`b877d17e`, `29401be3`) and
  one that hides system-account profiles (`948e80af`) — the latter is the only
  one that can change output values (some user columns may now be blank).
