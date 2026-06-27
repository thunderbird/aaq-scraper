#!/usr/bin/env python3
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""Shared CSV-output safety helpers."""

# Leading chars a spreadsheet may treat as the start of a formula. SUMO content
# is untrusted user input, so prefix any such string with ' (CSV/Excel
# formula-injection mitigation) before writing it out. Idempotent: an
# already-escaped value starts with ' and is left alone.
FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def escape_formula(value):
    if isinstance(value, str) and value[:1] in FORMULA_PREFIXES:
        return "'" + value
    return value
