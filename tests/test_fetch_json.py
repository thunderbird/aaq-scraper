# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""Contract/regression tests for SumoBrowser.fetch_json.

These stub _raw_fetch (no browser, no network), so they pin the retry /
deferral / challenge behavior independently of the transport. They must pass
both before and after the Playwright->httpx refactor (Task A2)."""
import pytest

import sumo


def _resp(status, json=None, snippet="", retry_after=None):
    return {"status": status, "json": json, "snippet": snippet,
            "retry_after": retry_after}


def _browser(monkeypatch, responses, **kwargs):
    """A SumoBrowser whose _raw_fetch yields the given canned responses in
    order, with sleeping/jitter neutralized so tests run instantly."""
    kwargs.setdefault("max_attempts", 5)
    kwargs.setdefault("backoff_base", 2.0)
    kwargs.setdefault("retry_jitter_min_s", 0)
    kwargs.setdefault("retry_jitter_max_s", 0)
    sb = sumo.SumoBrowser(**kwargs)
    seq = iter(responses)
    monkeypatch.setattr(sb, "_raw_fetch", lambda url: next(seq))
    monkeypatch.setattr(sumo.time, "sleep", lambda _s: None)
    monkeypatch.setattr(sumo.random, "uniform", lambda _a, _b: 0.0)
    return sb


def test_returns_json_on_200(monkeypatch):
    sb = _browser(monkeypatch, [_resp(200, {"count": 3})])
    assert sb.fetch_json("u") == {"count": 3}


def test_non_retryable_4xx_raises_plain_runtimeerror(monkeypatch):
    sb = _browser(monkeypatch, [_resp(404, snippet="nope")])
    with pytest.raises(RuntimeError) as ei:
        sb.fetch_json("u")
    assert not isinstance(ei.value, sumo.ChallengeError)
    assert not isinstance(ei.value, sumo.RateLimitDeferral)


def test_5xx_retried_then_success(monkeypatch):
    sb = _browser(monkeypatch, [_resp(503), _resp(200, {"ok": 1})])
    assert sb.fetch_json("u") == {"ok": 1}


def test_exhausted_200_non_json_raises_challenge_error(monkeypatch):
    sb = _browser(monkeypatch, [_resp(200, None, "<html>challenge</html>")] * 5)
    with pytest.raises(sumo.ChallengeError):
        sb.fetch_json("u")


def test_429_over_threshold_defers(monkeypatch):
    sb = _browser(monkeypatch, [_resp(429, retry_after="600")],
                  max_429_wait_s=120)
    with pytest.raises(sumo.RateLimitDeferral):
        sb.fetch_json("u")


def test_429_under_threshold_retries_then_succeeds(monkeypatch):
    sb = _browser(monkeypatch,
                  [_resp(429, retry_after="10"), _resp(200, {"ok": 1})],
                  max_429_wait_s=1000)
    assert sb.fetch_json("u") == {"ok": 1}


def test_raw_fetch_maps_httpx_response():
    """_raw_fetch turns an httpx response into the dict fetch_json expects."""
    import httpx

    def handler(request):
        return httpx.Response(429, headers={"retry-after": "30"},
                              text='{"a": 1}')

    sb = sumo.SumoBrowser()
    sb._client = httpx.Client(transport=httpx.MockTransport(handler))
    out = sb._raw_fetch("https://support.mozilla.org/api/2/question/")
    assert out == {"status": 429, "json": {"a": 1},
                   "snippet": '{"a": 1}', "retry_after": "30"}


def test_raw_fetch_non_json_body_sets_json_none():
    import httpx

    def handler(request):
        return httpx.Response(200, text="<html>challenge</html>")

    sb = sumo.SumoBrowser()
    sb._client = httpx.Client(transport=httpx.MockTransport(handler))
    out = sb._raw_fetch("https://support.mozilla.org/api/2/question/")
    assert out["status"] == 200 and out["json"] is None
    assert out["snippet"] == "<html>challenge</html>"


def test_default_output_path_default_layout(monkeypatch):
    """Without AAQ_DATA_ROOT the layout is the committed one: <year>/..."""
    import scrape_questions as sq
    from datetime import datetime
    monkeypatch.delenv("AAQ_DATA_ROOT", raising=False)
    d = datetime(2026, 7, 1)
    assert sq.default_output_path("questions", "thunderbird", d, d) == \
        "2026/questions-thunderbird-desktop-2026-07-01.csv"


def test_default_output_path_honors_data_root(monkeypatch):
    """AAQ_DATA_ROOT relocates the whole <year>/ tree under a prefix, so a
    parallel writer never touches the committed CSVs."""
    import scrape_questions as sq
    from datetime import datetime
    monkeypatch.setenv("AAQ_DATA_ROOT", "cronjob-test")
    d = datetime(2026, 7, 1)
    assert sq.default_output_path("answers", "thunderbird-android", d, d) == \
        "cronjob-test/2026/answers-thunderbird-android-2026-07-01.csv"


def _refresh_delay_args(argv):
    """Build run_refresh's forwarded delay flags for a given CLI."""
    import run_refresh, sys
    from unittest import mock
    with mock.patch.object(sys, "argv", ["run_refresh.py", *argv]):
        # Re-run just the parser: main() would hit the network.
        import argparse
        p = argparse.ArgumentParser()
        p.add_argument("--random-delay", action="store_true")
        p.add_argument("--sleep", type=float, default=None)
        a, _ = p.parse_known_args(argv)
    out = ["--random-delay"] if a.random_delay else []
    if a.sleep is not None:
        out += ["--sleep", str(a.sleep)]
    return out


def test_refresh_defaults_to_fixed_delay_not_jitter():
    """Default is the scrapers' fixed 2s. The 2-10s jitter was anti-fingerprinting
    cover for an unallowlisted client and tripled runtime; it is now opt-in."""
    assert _refresh_delay_args([]) == []


def test_refresh_random_delay_is_opt_in():
    assert _refresh_delay_args(["--random-delay"]) == ["--random-delay"]


def test_refresh_sleep_is_forwarded():
    assert _refresh_delay_args(["--sleep", "1.5"]) == ["--sleep", "1.5"]
