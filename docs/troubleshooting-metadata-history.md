<!-- This Source Code Form is subject to the terms of the Mozilla Public
   - License, v. 2.0. If a copy of the MPL was not distributed with this
   - file, You can obtain one at https://mozilla.org/MPL/2.0/. -->

# History of the `troubleshooting` metadata field in the Questions API

**Short answer:** `troubleshooting` was never added to the questions API
`metadata` field after the fact — it has been part of the AAQ (Ask a Question)
form since the feature's **first commit**.

- **Introduced:** [`daab8b849`](https://github.com/mozilla/kitsune/commit/daab8b849)
  — "Implemented \`Ask a Question\` (bug 569282)"
- **Date:** **2010-06-14** — the very first AAQ implementation in kitsune

## Where it came from

In that original commit, `troubleshooting` appears as one of the `extra_fields`
for the Firefox desktop product config (then `apps/questions/question_config.py`,
today `kitsune/questions/config.py`):

```python
'extra_fields': ['troubleshooting', 'ff_version', 'os', 'plugins'],
```

Those `extra_fields` are persisted as `QuestionMetaData` rows (a `name`/`value`
pair each). That table is exactly what the `/api/2/question/` API surfaces in its
`metadata` list — `[{name, value}, ...]`. So `troubleshooting` (the full Firefox
`about:support` JSON blob) has been an original member of that metadata set since
the AAQ launched.

## Later refinements (none add or remove the field)

| Commit | Date | Change |
|---|---|---|
| [`54ddbae07`](https://github.com/mozilla/kitsune/commit/54ddbae07) | 2013-06-06 | Apps moved `apps/` → `kitsune/` (bug 872538) |
| [`f9dd0ea53`](https://github.com/mozilla/kitsune/commit/f9dd0ea53) | 2013-11-25 | Config moved `question_config.py` → `config.py` |
| [`861a08a59`](https://github.com/mozilla/kitsune/commit/861a08a59) | 2022-10-27 | "remove username PII from troubleshooting data (#5271)" — scrubs PII from the blob's contents before saving, keeps the field |

## Why this matters for the scraper

`troubleshooting` is original, sparse (optional user-generated content), and can
be ~200KB (the full `about:support` JSON). For those reasons this scraper leaves
it in the flattened `metadata` column and does **not** promote it to its own
column (see `CLAUDE.md` → "CSV columns"). Its frequent absence on a given
question is normal, not schema drift.

---

*Traced from a `mozilla/kitsune` clone via `git log --follow` /
`git log -S "troubleshooting"` on 2026-07-05.*
