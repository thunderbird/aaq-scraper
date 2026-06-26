# aaq-scraper

Until we get an official API that has a way to prevent DOS-ing, we scrape the SUMO
(support.mozilla.org) API by driving a real browser. Since ~June 2026 the API sits
behind a JavaScript challenge that blocks headless HTTP (see
[thunderbird/github-action-thunderbird-aaq#34](https://github.com/thunderbird/github-action-thunderbird-aaq/issues/34));
a real browser passes the challenge, then we call the JSON API from inside the
browser's authenticated context.

## Proof of concept (Bucket 0)

```sh
uv sync
uv run playwright install chromium
uv run python poc.py            # headed — most likely to pass the challenge
uv run python poc.py --headless # try headless (closer to CI)
uv run python poc.py --dump     # also write the raw first API page to poc-sample.json
```

Success = the script prints a non-zero `count` and real question records (not
challenge HTML), and reports where `taken_by` / `operating_system` /
`thunderbird_version` live in the API response.

* We require all those who participate in this repo to agree and adhere to the [Mozilla Community Participation Guidelines](https://www.mozilla.org/about/governance/policies/participation/)

