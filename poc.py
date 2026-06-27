#!/usr/bin/env python3
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""
aaq-scraper — Bucket 0 proof of concept.

Goal: prove a real browser can pass the JavaScript challenge that SUMO
(support.mozilla.org) now puts in front of its API, then read one page of
/api/2/question/ as JSON from inside the browser's authenticated context.

It also inspects one full question record so we can ground the Bucket 1 column
mapping in real data: does `taken_by` exist, and where do `operating_system` /
`thunderbird_version` live (top-level vs inside `metadata`)?

Run:
    pip install -r requirements.txt
    playwright install chromium
    python poc.py                 # headed (most likely to pass the challenge)
    python poc.py --headless      # try headless (closer to CI)
"""

import argparse
import json
import sys

from playwright.sync_api import sync_playwright

HOME_URL = "https://support.mozilla.org/"
API_URL = (
    "https://support.mozilla.org/api/2/question/"
    "?format=json&product=thunderbird&ordering=-created"
)
# A realistic, current desktop UA reduces the chance of being flagged.
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)


def fetch_via_page_evaluate(page):
    """Primary technique: in-page fetch() reusing the page's cookies/origin."""
    return page.evaluate(
        """async (url) => {
            const res = await fetch(url, {
                headers: { 'Accept': 'application/json' },
                credentials: 'include',
            });
            const text = await res.text();
            let json = null;
            try { json = JSON.parse(text); } catch (e) {}
            return { status: res.status, json, snippet: text.slice(0, 300) };
        }""",
        API_URL,
    )


def report_field_locations(record):
    """Confirm where the new Bucket 1 columns will come from."""
    print("\n=== Field-location findings (for Bucket 1 columns) ===")
    top_level_keys = sorted(record.keys())
    print(f"Top-level question keys: {top_level_keys}")

    has_taken_by = "taken_by" in record
    print(f"taken_by present at top level: {has_taken_by}"
          + (f"  -> {record.get('taken_by')!r}" if has_taken_by else ""))

    metadata = record.get("metadata")
    print(f"metadata type: {type(metadata).__name__}")
    if isinstance(metadata, list):
        # SUMO often returns metadata as a list of {name, value} dicts.
        names = [m.get("name") for m in metadata if isinstance(m, dict)]
        print(f"metadata entry names: {names}")
        for m in metadata:
            if isinstance(m, dict) and m.get("name") in ("os", "operating_system",
                                                          "version", "ff_version",
                                                          "app_version"):
                print(f"  candidate -> {m}")
    elif isinstance(metadata, dict):
        print(f"metadata keys: {sorted(metadata.keys())}")


def main():
    parser = argparse.ArgumentParser(description="aaq-scraper PoC")
    parser.add_argument("--headless", action="store_true",
                        help="run headless (closer to CI; may fail the challenge)")
    parser.add_argument("--dump", metavar="PATH", nargs="?", const="poc-sample.json",
                        help="write the raw first page of API JSON to PATH "
                             "(default: poc-sample.json)")
    args = parser.parse_args()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=args.headless)
        context = browser.new_context(
            user_agent=USER_AGENT,
            locale="en-US",
            viewport={"width": 1280, "height": 800},
        )
        page = context.new_page()

        print(f"Navigating to {HOME_URL} to acquire challenge cookies...")
        page.goto(HOME_URL, wait_until="domcontentloaded", timeout=60000)
        # Give any JS challenge a moment to resolve and set cookies.
        page.wait_for_timeout(6000)
        print(f"Landed on: {page.url}")
        print(f"Page title: {page.title()!r}")

        print(f"\nFetching one page of the API:\n  {API_URL}")
        result = fetch_via_page_evaluate(page)
        print(f"HTTP status: {result['status']}")

        data = result["json"]
        if not data:
            print("Did NOT get JSON back. First 300 chars of response:")
            print(result["snippet"])
            print("\n=> Browser likely did not pass the challenge in this mode.")
            browser.close()
            sys.exit(1)

        count = data.get("count")
        results = data.get("results", [])
        print(f"count: {count}")
        print(f"results on this page: {len(results)}")

        if args.dump:
            with open(args.dump, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            print(f"Wrote raw first page ({len(results)} records) to {args.dump}")

        print("\n=== Sample records ===")
        for r in results[:3]:
            print(json.dumps({
                "id": r.get("id"),
                "created": r.get("created"),
                "creator": r.get("creator"),
                "title": r.get("title"),
            }, ensure_ascii=False))

        if results:
            report_field_locations(results[0])
            print("\n=== Full first record (for reference) ===")
            print(json.dumps(results[0], ensure_ascii=False, indent=2)[:4000])

        browser.close()
        print("\nPoC succeeded: browser passed the challenge and read the API.")


if __name__ == "__main__":
    main()
