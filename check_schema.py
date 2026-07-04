#!/usr/bin/env python3
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""
Detect SUMO API schema drift.

Samples the live API (questions for both products + answers), derives the set of
JSON fields, and compares them against a committed baseline
(schema/expected-fields.json). Exits non-zero when fields are added or removed
so a scheduled workflow can open an issue.

Policy: MANUAL BUMP. This script never updates the baseline on its own. When the
live API legitimately changes, a maintainer reviews the drift and re-runs with
--update-baseline, then commits the result (the issue is the audit trail).

Run:
    uv run python check_schema.py --headless            # check, exit 1 on drift
    uv run python check_schema.py --headless --update-baseline   # manual bump
"""

import argparse
import json
import sys
from urllib.parse import urlencode

from sumo import API_BASE, SumoBrowser

BASELINE_PATH = "schema/expected-fields.json"
PRODUCTS = ["thunderbird", "thunderbird-android"]


def sample_questions(sumo, product, pages):
    """Union of top-level keys and metadata names across a few recent pages."""
    top, meta = set(), set()
    url = f"{API_BASE}question/?{urlencode({'format': 'json', 'product': product, 'ordering': '-created'})}"
    for _ in range(pages):
        if not url:
            break
        data = sumo.fetch_json(url)
        for q in data.get("results", []):
            top.update(q.keys())
            for m in (q.get("metadata") or []):
                if isinstance(m, dict) and m.get("name"):
                    meta.add(m["name"])
        url = data.get("next")
    return sorted(top), sorted(meta)


def sample_answers(sumo, pages):
    """Union of answer top-level keys across recent answers.

    Tries the unfiltered answer list first; if that yields nothing, falls back to
    a recent question that has answers.
    """
    top = set()
    url = f"{API_BASE}answer/?{urlencode({'format': 'json', 'ordering': '-created'})}"
    for _ in range(pages):
        if not url:
            break
        data = sumo.fetch_json(url)
        for a in data.get("results", []):
            top.update(a.keys())
        url = data.get("next")
    if top:
        return sorted(top)

    # Fallback: find a recent question with answers and read its answers.
    q = sumo.fetch_json(
        f"{API_BASE}question/?{urlencode({'format': 'json', 'product': 'thunderbird', 'ordering': '-created'})}"
    )
    for question in q.get("results", []):
        if question.get("num_answers"):
            a = sumo.fetch_json(
                f"{API_BASE}answer/?{urlencode({'format': 'json', 'question': question['id']})}"
            )
            for ans in a.get("results", []):
                top.update(ans.keys())
            if top:
                break
    return sorted(top)


def observe(headless, pages):
    obs = {"question_top_level": {}, "question_metadata": {}}
    with SumoBrowser(headless=headless) as sumo:
        for product in PRODUCTS:
            top, meta = sample_questions(sumo, product, pages)
            obs["question_top_level"][product] = top
            obs["question_metadata"][product] = meta
        obs["answer_top_level"] = sample_answers(sumo, pages)
    return obs


def diff(baseline, observed):
    """Return (added, removed) sorted lists."""
    b, o = set(baseline), set(observed)
    return sorted(o - b), sorted(b - o)


def build_report(baseline, obs):
    """Return (drift: bool, markdown: str)."""
    sections = []

    def add_section(title, base_list, obs_list, additive_only=False):
        # additive_only: metadata names are optional user-generated content, so a
        # given ~40-question sample legitimately lacks many of them (ff_version,
        # troubleshooting, solver_id, ...). Treating a missing name as "removed"
        # would false-alarm on every sample that happens not to include it, so we
        # only report NEWLY-appeared names here. (A metadata field genuinely
        # dropped by SUMO just leaves its derived column blank — not breaking.)
        added, removed = diff(base_list, obs_list)
        if additive_only:
            removed = []
        if not (added or removed):
            return False
        lines = [f"### {title}"]
        if removed:
            lines.append(f"- ⚠️ **Removed (breaking — columns will be empty):** "
                         f"`{'`, `'.join(removed)}`")
        if added:
            lines.append(f"- ➕ Added (consider a new column): "
                         f"`{'`, `'.join(added)}`")
        sections.append("\n".join(lines))
        return True

    drift = False
    for product in PRODUCTS:
        drift |= add_section(
            f"{product} — question fields",
            baseline.get("question_top_level", {}).get(product, []),
            obs["question_top_level"][product],
        )
        drift |= add_section(
            f"{product} — question metadata names",
            baseline.get("question_metadata", {}).get(product, []),
            obs["question_metadata"][product],
            additive_only=True,
        )
    drift |= add_section(
        "answer fields",
        baseline.get("answer_top_level", []),
        obs["answer_top_level"],
    )

    if not drift:
        return False, "No schema drift: the live API matches the baseline."

    body = (
        "The live SUMO API JSON fields no longer match "
        f"`{BASELINE_PATH}`.\n\n"
        + "\n\n".join(sections)
        + "\n\n---\n"
        "**To resolve:** review the change, then run "
        "`uv run python check_schema.py --headless --update-baseline` and commit "
        f"the updated `{BASELINE_PATH}`. If any field was *removed*, also update "
        "the scrapers so the affected CSV columns don't silently go blank."
    )
    return True, body


def main():
    p = argparse.ArgumentParser(description="Detect SUMO API schema drift")
    p.add_argument("--headless", action="store_true")
    p.add_argument("--pages", type=int, default=2,
                   help="recent pages to sample per endpoint (default 2)")
    p.add_argument("--baseline", default=BASELINE_PATH)
    p.add_argument("--update-baseline", action="store_true",
                   help="MANUAL BUMP: overwrite the baseline with observed fields")
    args = p.parse_args()

    obs = observe(args.headless, args.pages)

    if args.update_baseline:
        import os
        # Metadata names are optional user-generated content: union them with the
        # prior baseline so a bump taken from a sample that happens to lack an
        # optional field (ff_version, troubleshooting, solver_id, ...) doesn't
        # silently drop it. Top-level / answer fields are overwritten as observed.
        if os.path.exists(args.baseline):
            with open(args.baseline, encoding="utf-8") as f:
                prior = json.load(f)
            prior_meta = prior.get("question_metadata", {})
            for product, names in obs["question_metadata"].items():
                merged = set(names) | set(prior_meta.get(product, []))
                obs["question_metadata"][product] = sorted(merged)
        os.makedirs(os.path.dirname(args.baseline) or ".", exist_ok=True)
        with open(args.baseline, "w", encoding="utf-8") as f:
            json.dump(obs, f, indent=2, sort_keys=True)
            f.write("\n")
        print(f"Wrote baseline to {args.baseline}", file=sys.stderr)
        return

    with open(args.baseline, encoding="utf-8") as f:
        baseline = json.load(f)

    drift, report = build_report(baseline, obs)
    print(report)
    sys.exit(1 if drift else 0)


if __name__ == "__main__":
    main()
