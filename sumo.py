# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""
Shared SUMO (support.mozilla.org) API client.

Historically drove a real Chromium (Playwright) to pass the Fastly JS/WAF
challenge; that path is now fingerprinted and blocked (issue #28), so this uses
a plain HTTP client (httpx). Once our egress IP is allowlisted by Mozilla
(issue #27) the API is reachable directly. The public API (SumoBrowser,
fetch_json, paginate) is unchanged so callers did not have to change.
"""

import csv
import json
import random
import sys
import time

import httpx


def _max_csv_field_size():
    """Always allow maximum-size CSV fields (question/answer content can be very
    large). Set here so every module that imports sumo inherits it."""
    limit = sys.maxsize
    while True:
        try:
            csv.field_size_limit(limit)
            return
        except OverflowError:
            limit //= 10


_max_csv_field_size()

HOME_URL = "https://support.mozilla.org/"
API_BASE = "https://support.mozilla.org/api/2/"


class ChallengeError(RuntimeError):
    """The API returned a 200 that isn't JSON — i.e. the Fastly edge served the
    JS/WAF bot-challenge page (HTML) instead of the API, and the browser did not
    pass it within the retry budget.

    Subclasses RuntimeError so existing ``except RuntimeError`` handlers still
    catch it, while callers that care (e.g. check_schema.py) can distinguish a
    *blocked* API from ordinary schema drift or a transient 5xx. See
    docs/js-challenge-edge-waf.md (upstream mozilla/sumo#3124,
    thunderbird/bitergia-deploy#50)."""


class RateLimitDeferral(RuntimeError):
    """A 429 asked us to wait longer than `SumoBrowser.max_429_wait_s`, so instead
    of blocking the whole run on one long rate-limit window we abort this fetch and
    let the caller DEFER the unit of work (a created-day) to a later run.

    Subclasses RuntimeError so existing handlers still catch it. run_refresh.py
    treats a deferred day specially: it leaves that day's CSV untouched and holds
    the high-water mark below the day's earliest change so the next run retries it.
    Deferral is OFF unless `max_429_wait_s` is set (default None = wait as before)."""


# Exit code a scraper subprocess uses to signal "deferred, not a hard failure"
# (chosen from the 64-113 sysexits range, distinct from 0/1/2). run_refresh.py
# distinguishes this from a real error when deciding whether a day completed.
DEFERRAL_EXIT_CODE = 75

# A realistic, current desktop UA reduces the chance of being flagged.
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)


class SumoBrowser:
    """A SUMO API HTTP session.

    Named `SumoBrowser` for backwards compatibility with existing call sites;
    it no longer drives a browser. Use as a context manager:

        with SumoBrowser(headless=True) as sumo:
            data = sumo.fetch_json(sumo_url)
            for page in sumo.paginate(first_url):
                ...
    """

    def __init__(self, headless=False, settle_ms=6000,
                 max_attempts=5, backoff_base=2.0,
                 retry_jitter_min_s=60, retry_jitter_max_s=300,
                 max_429_wait_s=None):
        # `headless` and `settle_ms` are accepted for backwards-compatible call
        # sites but ignored: there is no browser to run headed/headless and
        # nothing to "settle" for a plain HTTP client.
        self.max_attempts = max_attempts
        self.backoff_base = backoff_base
        # If set, a 429 whose required wait (Retry-After / backoff) exceeds this
        # many seconds raises RateLimitDeferral instead of sleeping, so a caller
        # can defer the work rather than burn the run on one long window. None =
        # honour the wait in full (original behaviour).
        self.max_429_wait_s = max_429_wait_s
        # Extra random pause added on top of a 429 wait (default 1-5 min) so we
        # always retry strictly AFTER SUMO's Retry-After window and desync retries.
        self.retry_jitter_min_s = retry_jitter_min_s
        self.retry_jitter_max_s = retry_jitter_max_s
        self._client = None

    def __enter__(self):
        # A persistent client keeps a cookie jar across calls, so any edge
        # cookie handed out on the first request is reused (mirrors the old
        # browser context's cookie reuse). follow_redirects matches a browser.
        self._client = httpx.Client(
            headers={
                "User-Agent": USER_AGENT,
                "Accept-Language": "en-US,en;q=0.9",
            },
            timeout=60.0,
            follow_redirects=True,
        )
        # Warm up by loading the home page once so any cookie is captured before
        # the API call. Best-effort: a failure here is not fatal — a genuine
        # block surfaces later as ChallengeError from fetch_json.
        try:
            self._client.get(HOME_URL)
        except httpx.HTTPError as e:
            print(f"home warm-up failed (continuing): {e}",
                  file=sys.stderr, flush=True)
        return self

    def __exit__(self, exc_type, exc, tb):
        if self._client is not None:
            self._client.close()
        return False

    def _raw_fetch(self, url):
        """Do one HTTP GET; return {status, json, snippet, retry_after}.

        Parses the body as JSON (json is None when the body is not valid
        JSON — e.g. an HTML challenge page)."""
        res = self._client.get(url, headers={"Accept": "application/json"})
        text = res.text
        try:
            body = json.loads(text)
        except ValueError:
            body = None
        return {
            "status": res.status_code,
            "json": body,
            "snippet": text[:300],
            "retry_after": res.headers.get("retry-after"),
        }

    def fetch_json(self, url):
        """Fetch `url` via the HTTP client, retrying transient failures.

        Reuses the client's cookie jar so the JS challenge stays satisfied.
        Retries with exponential backoff on HTTP 429 and 5xx (honouring
        Retry-After for 429). A 200 that isn't JSON is treated as a challenge
        hiccup and retried too. Other 4xx fail immediately. Raises RuntimeError
        once attempts are exhausted.
        """
        last = None
        for attempt in range(1, self.max_attempts + 1):
            result = self._raw_fetch(url)
            status = result["status"]
            last = result

            if status == 200 and result["json"] is not None:
                return result["json"]

            transient = status == 429 or status >= 500 or status == 200
            if not transient:
                raise RuntimeError(
                    f"HTTP {status} (non-retryable) for {url}\n"
                    f"First 300 chars: {result['snippet']!r}"
                )
            if attempt == self.max_attempts:
                break

            wait = self.backoff_base * (2 ** (attempt - 1))
            if status == 429:
                # Honour SUMO's Retry-After in full (it can be ~600s), then add
                # a random 1-5 min on top so we always retry after the server's
                # window and don't synchronise retries across calls.
                if result.get("retry_after"):
                    try:
                        wait = max(wait, float(result["retry_after"]))
                    except (TypeError, ValueError):
                        pass
                # Defer instead of blocking when the server's required wait is
                # long: waiting out a ~12 min Retry-After (repeatedly) is what
                # made the hourly refresh exceed its job timeout. The threshold is
                # the server-demanded wait, before our own retry jitter.
                if (self.max_429_wait_s is not None
                        and wait > self.max_429_wait_s):
                    raise RateLimitDeferral(
                        f"429 requires waiting {wait:.0f}s "
                        f"(> max_429_wait {self.max_429_wait_s:.0f}s) for {url}; "
                        "deferring to a later run."
                    )
                wait += random.uniform(self.retry_jitter_min_s,
                                       self.retry_jitter_max_s)
            reason = (f"HTTP {status}" if status != 200
                      else "200 but non-JSON (challenge?)")
            print(f"  retry {attempt}/{self.max_attempts - 1} after {wait:.0f}s "
                  f"({reason}) for {url}", file=sys.stderr)
            time.sleep(wait)

        # A give-up on a 200 means every attempt returned non-JSON: the Fastly
        # edge is serving the HTML bot-challenge instead of the API. Raise the
        # distinct ChallengeError so callers can alert on "API blocked" rather
        # than misreport it as schema drift or a server error.
        exc = ChallengeError if last["status"] == 200 else RuntimeError
        raise exc(
            f"Gave up after {self.max_attempts} attempts; last status "
            f"{last['status']} for {url}\nFirst 300 chars: {last['snippet']!r}\n"
            "If this is a 200 with HTML, the client may not have passed the "
            "challenge / the edge served the challenge page."
        )

    def paginate(self, first_url, on_page=None):
        """Yield each result list across paginated pages, following `next`.

        `on_page(page_json)` is called with the raw page dict (for early-stop
        decisions); if it returns True, pagination stops after that page.
        """
        url = first_url
        while url:
            page = self.fetch_json(url)
            stop = on_page(page) if on_page else False
            yield page.get("results", [])
            if stop:
                break
            url = page.get("next")
