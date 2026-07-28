# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""Tests for the deferral-fairness fix (issue #58).

The soft deadline always truncates the SAME deterministically-ordered work
list, so before this fix the tail could be starved forever while the
high-water mark stayed pinned below it -- a silent treadmill that reported
success every run. These pin the two mechanisms that break that cycle:
carrying the deferred set forward, and noticing when the mark stops moving.
"""
from datetime import datetime, timezone

import run_refresh as rr


def _dt(day, hour=0):
    return datetime(2026, 7, day, hour, tzinfo=timezone.utc)


def test_previously_deferred_pairs_run_first():
    """The starvation fix: days starved last run go to the FRONT next run."""
    items = [(("thunderbird", "2026-01-01"), _dt(1)),
             (("thunderbird", "2026-01-02"), _dt(2)),
             (("thunderbird-android", "2026-01-03"), _dt(3))]
    deferred = [("thunderbird-android", "2026-01-03")]
    out = rr.order_day_items(items, deferred)
    assert [k for k, _ in out] == [("thunderbird-android", "2026-01-03"),
                                   ("thunderbird", "2026-01-01"),
                                   ("thunderbird", "2026-01-02")]


def test_ordering_is_stable_for_non_deferred():
    items = [(("p", "2026-01-01"), _dt(1)), (("p", "2026-01-02"), _dt(2))]
    assert rr.order_day_items(items, []) == items


def test_deferred_pairs_no_longer_in_window_are_ignored():
    """A stale entry must not resurrect a day the discovery pass didn't find."""
    items = [(("p", "2026-01-01"), _dt(1))]
    out = rr.order_day_items(items, [("p", "2026-05-05")])
    assert [k for k, _ in out] == [("p", "2026-01-01")]


def test_deferred_state_round_trips(tmp_path):
    f = tmp_path / ".refresh-hwm.deferred"
    rr.write_deferred(str(f), [("thunderbird", "2026-01-25")], stalls=2)
    pairs, stalls = rr.read_deferred(str(f))
    assert pairs == [("thunderbird", "2026-01-25")]
    assert stalls == 2


def test_deferred_state_absent_is_empty(tmp_path):
    pairs, stalls = rr.read_deferred(str(tmp_path / "nope"))
    assert pairs == [] and stalls == 0


def test_deferred_state_corrupt_is_not_fatal(tmp_path):
    f = tmp_path / "bad"; f.write_text("{not json")
    assert rr.read_deferred(str(f)) == ([], 0)


def test_empty_deferred_removes_the_file(tmp_path):
    """A clean run must clear the carry-forward, not leave a stale one."""
    f = tmp_path / ".refresh-hwm.deferred"
    rr.write_deferred(str(f), [("p", "2026-01-01")], stalls=1)
    assert f.exists()
    rr.write_deferred(str(f), [], stalls=0)
    assert not f.exists()


def test_stall_counter_increments_when_mark_does_not_move():
    assert rr.next_stall_count(_dt(1), _dt(1), 0) == 1
    assert rr.next_stall_count(_dt(1), _dt(1), 3) == 4


def test_stall_counter_resets_when_mark_advances():
    assert rr.next_stall_count(_dt(1), _dt(2), 5) == 0


def test_stall_counter_zero_on_first_run():
    """No previous mark (cold start) is not a stall."""
    assert rr.next_stall_count(None, _dt(1), 0) == 0


def test_starvation_cycle_is_broken_across_runs(tmp_path):
    """End-to-end proof of the #58 fix.

    Five days, a budget that fits only two per run. Before the fix the work
    list was rebuilt in the same order every run, so runs processed [A,B]
    forever and C/D/E were NEVER touched. With carry-forward, every day is
    processed within a few runs.
    """
    state = str(tmp_path / ".refresh-hwm")
    dpath = rr.deferred_path(state)
    keys = [("p", f"2026-01-0{n}") for n in range(1, 6)]
    discovered = [(k, _dt(i + 1)) for i, k in enumerate(keys)]  # stable order
    BUDGET = 2

    processed_runs = []
    for _ in range(3):
        carried, stalls = rr.read_deferred(dpath)
        items = rr.order_day_items(list(discovered), carried)
        done = [k for k, _ in items[:BUDGET]]
        deferred = [k for k, _ in items[BUDGET:]]
        processed_runs.append(done)
        rr.write_deferred(dpath, deferred, stalls)

    seen = {k for run in processed_runs for k in run}
    assert seen == set(keys), f"starved days never ran: {set(keys) - seen}"
    # And specifically: the tail that used to starve ran on run 2.
    assert ("p", "2026-01-03") in processed_runs[1]
    assert ("p", "2026-01-04") in processed_runs[1]


def test_without_carry_forward_the_tail_starves():
    """Characterises the OLD behaviour, so the fix can't silently regress."""
    keys = [("p", f"2026-01-0{n}") for n in range(1, 6)]
    discovered = [(k, _dt(i + 1)) for i, k in enumerate(keys)]
    BUDGET = 2
    seen = set()
    for _ in range(3):
        items = rr.order_day_items(list(discovered), [])  # no carry-forward
        seen.update(k for k, _ in items[:BUDGET])
    assert seen == {("p", "2026-01-01"), ("p", "2026-01-02")}
    assert ("p", "2026-01-05") not in seen  # starved, exactly as reported
