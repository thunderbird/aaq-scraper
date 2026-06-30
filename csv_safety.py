#!/usr/bin/env python3
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""Shared CSV-output safety helpers."""

import re

# Leading chars a spreadsheet may treat as the start of a formula. SUMO content
# is untrusted user input, so prefix any such string with ' (CSV/Excel
# formula-injection mitigation) before writing it out. Idempotent: an
# already-escaped value starts with ' and is left alone.
FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def escape_formula(value):
    if isinstance(value, str) and value[:1] in FORMULA_PREFIXES:
        return "'" + value
    return value


# SUMO content is untrusted: users sometimes paste live credentials into a
# question/answer, which we then scrape. We must not republish them (GitHub
# push protection rightly blocks such commits), so any match is replaced with
# the literal REDACTION below. Each pattern matches a contiguous token with no
# CSV delimiters, so substituting just the match preserves all other bytes /
# CSV quoting. Idempotent (REDACTION matches none of the patterns). Mirrored by
# the standalone redact_credentials.py, which back-fills committed CSVs.
REDACTION = "<credential_deleted>"

CREDENTIAL_PATTERNS = (
    re.compile(r"1//0[A-Za-z0-9_-]{20,}"),       # google oauth refresh token
    re.compile(r"AIza[0-9A-Za-z_-]{35}"),         # google api key
    re.compile(r"AKIA[0-9A-Z]{16}"),              # aws access key id
    re.compile(r"xox[baprs]-[0-9A-Za-z-]{10,}"),  # slack token
    re.compile(r"gh[ps]_[0-9A-Za-z]{36,}"),       # github pat
)


def redact_credentials(value):
    """Replace any leaked credential in a string cell with REDACTION."""
    if not isinstance(value, str):
        return value
    for pat in CREDENTIAL_PATTERNS:
        value = pat.sub(REDACTION, value)
    return value
