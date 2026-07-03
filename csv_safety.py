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
# the literal REDACTION below. Idempotent (REDACTION matches none of the
# patterns). The same tuple is reused by the standalone redact_credentials.py,
# which back-fills committed CSVs by substituting on the RAW FILE TEXT.
#
# INVARIANT for any pattern added here: it must match a single contiguous token
# containing NO CSV delimiters -- no comma, double-quote, or newline (\r/\n).
# The file-level redactor does a raw substring substitution, so a pattern that
# spanned a delimiter could corrupt CSV structure. Content newlines are already
# collapsed to spaces at scrape time, so a multi-line secret (e.g. a PEM key)
# appears on one line with spaces; match spaces, never \s (which includes \n).
# This is also why we do NOT match bare AWS secret keys (a 40-char base64 blob
# would false-positive on ordinary public base64/hashes) or context-based
# assignments (they'd need to span quotes/whitespace, breaking the invariant).
REDACTION = "<credential_deleted>"

CREDENTIAL_PATTERNS = (
    re.compile(r"1//0[A-Za-z0-9_-]{20,}"),       # google oauth refresh token
    re.compile(r"AIza[0-9A-Za-z_-]{35}"),         # google api key
    re.compile(r"AKIA[0-9A-Z]{16}"),              # aws access key id
    re.compile(r"xox[baprs]-[0-9A-Za-z-]{10,}"),  # slack token
    re.compile(r"gh[ps]_[0-9A-Za-z]{36,}"),       # github pat
    # PEM private key block. Newlines are collapsed to spaces before redaction,
    # so match a single-line form; body is base64 + spaces only (no delimiter).
    re.compile(
        r"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY-----"
        r"[A-Za-z0-9+/= -]*?"
        r"-----END (?:[A-Z0-9]+ )*PRIVATE KEY-----"
    ),
    # JWT / bearer token: three base64url segments (header.payload.signature),
    # header always begins `eyJ` (base64 of '{"'). Contains no CSV delimiter.
    re.compile(r"eyJ[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{6,}"),
)


def redact_credentials(value):
    """Replace any leaked credential in a string cell with REDACTION."""
    if not isinstance(value, str):
        return value
    for pat in CREDENTIAL_PATTERNS:
        value = pat.sub(REDACTION, value)
    return value
