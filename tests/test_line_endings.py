# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""Tests for the CRLF -> LF back-fill (normalize_line_endings.py)."""
import normalize_line_endings as nle


def _w(tmp_path, name, data: bytes):
    p = tmp_path / name; p.write_bytes(data); return str(p)


def test_converts_crlf_and_is_byte_minimal(tmp_path):
    """Only the record separators change; every other byte survives."""
    p = _w(tmp_path, "a.csv", b'id,content\r\n1,"has, comma"\r\n2,plain\r\n')
    assert nle.normalize(p) == "converted"
    assert open(p, "rb").read() == b'id,content\n1,"has, comma"\n2,plain\n'


def test_idempotent(tmp_path):
    p = _w(tmp_path, "a.csv", b"id\n1\n")
    assert nle.normalize(p) == "clean"
    assert open(p, "rb").read() == b"id\n1\n"
    assert nle.normalize(p) == "clean"


def test_refuses_bare_cr_before_crlf(tmp_path):
    r"""Regression, found in review of PR #63.

    `\r\r\n` parses as an extra EMPTY row. The original guard only scanned
    CELLS for a bare CR/LF, and an empty row has no cells -- so the check passed
    vacuously while the byte replace silently dropped the blank row, changing
    the parsed CSV while still satisfying `old.replace(b"\r\n", b"\n") == new`.
    The guard now compares the parse itself, so this must be refused.
    """
    data = b"id,content\r\n1,plain\r\r\n2,x\r\n"
    p = _w(tmp_path, "a.csv", data)
    assert nle.normalize(p) == "skipped"
    assert open(p, "rb").read() == data  # untouched


def test_undecodable_file_is_refused(tmp_path):
    """Can't prove parse-equivalence on bytes we can't decode -> refuse."""
    p = _w(tmp_path, "a.csv", b"id\r\n\xff\xfe bad utf8\r\n")
    assert nle.normalize(p) == "skipped"


def test_refuses_when_a_cell_contains_an_embedded_crlf(tmp_path):
    """The dangerous case: a naive byte replace would corrupt user content."""
    data = b'id,content\r\n1,"line one\r\nline two"\r\n'
    p = _w(tmp_path, "a.csv", data)
    assert nle.normalize(p) == "skipped"
    assert open(p, "rb").read() == data  # untouched


def test_embedded_lf_is_safe_to_convert(tmp_path):
    r"""An embedded LF is NOT part of a \r\n, so the replace can't touch it.

    The first guard (a cell scan for any CR/LF) refused this needlessly. The
    parse-equivalence guard permits it, because the parsed CSV is provably
    unchanged -- and the embedded LF must survive verbatim.
    """
    data = b'id,content\r\n1,"line one\nline two"\r\n'
    p = _w(tmp_path, "a.csv", data)
    assert nle.normalize(p) == "converted"
    assert open(p, "rb").read() == b'id,content\n1,"line one\nline two"\n'
    import csv, io
    rows = list(csv.reader(io.StringIO(open(p).read(), newline="")))
    assert rows[1][1] == "line one\nline two"  # embedded LF preserved


def test_quoted_cr_alone_is_preserved_when_file_is_already_lf(tmp_path):
    data = b'id,content\n1,"carriage\rreturn"\n'
    p = _w(tmp_path, "a.csv", data)
    assert nle.normalize(p) == "clean"
    assert open(p, "rb").read() == data


def test_header_detection_not_fooled_by_cr_in_content(tmp_path):
    """An LF file whose CONTENT contains \\r\\n must not be treated as CRLF."""
    p = _w(tmp_path, "a.csv", b'id,content\n1,"a\r\nb"\n')
    assert nle.is_crlf_terminated(p) is False
