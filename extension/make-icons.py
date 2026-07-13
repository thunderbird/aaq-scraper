#!/usr/bin/env python3
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""
Regenerate the extension's PNG icons (the 🕷 spider — this is the *scraper*).

Extensions need raster icons (Chrome rejects SVG/emoji), so we render the emoji
to PNG at the standard sizes via the Chromium we already use for scraping. Run:

    uv run python extension/make-icons.py
"""

import base64
import os

from playwright.sync_api import sync_playwright

EMOJI = "\U0001F577"  # 🕷 spider
SIZES = [16, 32, 48, 128]
OUT_DIR = os.path.join(os.path.dirname(__file__), "icons")

RENDER_JS = """
([emoji, size]) => {
  const c = document.createElement('canvas');
  c.width = c.height = size;
  const ctx = c.getContext('2d');
  ctx.clearRect(0, 0, size, size);
  ctx.textAlign = 'center';
  ctx.textBaseline = 'middle';
  ctx.font = `${Math.floor(size * 0.82)}px "Apple Color Emoji","Noto Color Emoji",sans-serif`;
  ctx.fillText(emoji, size / 2, size / 2 + size * 0.04);
  return c.toDataURL('image/png');
}
"""


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content("<!doctype html><meta charset=utf-8><body></body>")
        for size in SIZES:
            data_url = page.evaluate(RENDER_JS, [EMOJI, size])
            png = base64.b64decode(data_url.split(",", 1)[1])
            path = os.path.join(OUT_DIR, f"icon-{size}.png")
            with open(path, "wb") as f:
                f.write(png)
            print(f"wrote {path} ({len(png)} bytes)")
        browser.close()


if __name__ == "__main__":
    main()
