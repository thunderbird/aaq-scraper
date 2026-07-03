#!/usr/bin/env python3
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""Redact leaked credentials from scraped CSVs in place.

SUMO forum content is untrusted user input; occasionally a user pastes a live
credential (e.g. a Google OAuth refresh token, a JWT/bearer token, or a PEM
private-key block) into a question/answer, which we then scrape. GitHub push
protection rightly blocks such commits, and we should not republish someone's
secret regardless. This replaces each matched secret with the literal
``<credential_deleted>``.

The pattern set lives in ``csv_safety.CREDENTIAL_PATTERNS`` (imported below) so
the scrapers and this back-fill stay in lock-step -- see that module for the
full list and, crucially, the INVARIANT every pattern must satisfy: it matches a
single contiguous token with no CSV delimiters (comma/quote/newline). That lets
this script do a raw substring substitution on the whole file while preserving
every other byte and all CSV quoting -- redacted files stay byte-identical
except for the secret itself. Idempotent: re-running finds nothing once redacted.

    uv run python redact_credentials.py 2025/answers-...csv   # specific files
    uv run python redact_credentials.py                       # all 20*/*.csv

Mirror the same PATTERNS in the scrapers so freshly-scraped days are redacted at
write time (otherwise a deterministic re-scrape re-introduces the secret).
"""
import sys
from glob import glob

from csv_safety import CREDENTIAL_PATTERNS, REDACTION


def redact_text(text):
    n = 0
    for pat in CREDENTIAL_PATTERNS:
        text, c = pat.subn(REDACTION, text)
        n += c
    return text, n


def main(argv):
    files = argv or sorted(glob("20*/*.csv"))
    total = 0
    for path in files:
        with open(path, encoding="utf-8") as f:
            text = f.read()
        new, n = redact_text(text)
        if n:
            with open(path, "w", encoding="utf-8") as f:
                f.write(new)
            print(f"redacted {n} secret(s) in {path}", flush=True)
            total += n
    print(f"total secrets redacted: {total}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
