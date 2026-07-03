<!-- This Source Code Form is subject to the terms of the Mozilla Public
     License, v. 2.0. If a copy of the MPL was not distributed with this
     file, You can obtain one at https://mozilla.org/MPL/2.0/. -->

# Redacted credentials audit

SUMO forum content is untrusted user input; users occasionally paste a live
credential into a public question, which we then scrape into a CSV. We must not
re-publish those secrets in this repo, so `csv_safety.redact_credentials`
replaces each match with the literal `<credential_deleted>` at scrape time, and
`redact_credentials.py` back-fills the same transform onto already-committed
CSVs. See `csv_safety.CREDENTIAL_PATTERNS` for the pattern set and the
delimiter-free invariant every pattern must satisfy.

This file audits the one-time back-fill run after **JWT/bearer-token** and
**PEM private-key** patterns were added (previously only Google OAuth/API, AWS
access-key-id, Slack, and GitHub-PAT tokens were matched). Tokens are shown
masked (first 8 / last 6 chars + length) — never in full.

## Redacted cells (3 questions, 4 token occurrences)

| Question | Credential type | Masked token | File · cell |
|---|---|---|---|
| [1443736](https://support.mozilla.org/questions/1443736) | JWT / bearer token | `eyJhbGci…6Re5wQ` (192 chars) | `2024/questions-thunderbird-desktop-2024-04-01.csv` · `content` |
| [1457470](https://support.mozilla.org/questions/1457470) | JWT / bearer token | `eyJhbGci…r6exJ4` (187 chars, pasted twice in the cell) | `2024/questions-thunderbird-desktop-2024-08-10.csv` · `content` |
| [1563257](https://support.mozilla.org/questions/1563257) | JWT / bearer token | `eyJza3Np…2jYDuQ` (1763 chars) | `2026/questions-thunderbird-desktop-2026-01-31.csv` · `content` |

All three are genuine JWTs (base64url `header.payload.signature`, headers
`{"alg":…}` / `{"sksid":…}`) pasted by users while troubleshooting. Redaction is
byte-safe: each JWT is a single contiguous token with no CSV delimiter, so only
the token bytes change and the surrounding row/quoting is preserved. A
deterministic re-scrape now produces the same redacted output (the scrapers
share the pattern set), so no drift is introduced.
