# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

# Browser-free image: the old Playwright/Chromium approach is dead (issue #28);
# this runs the plain-httpx scraper. git + a shell are needed at runtime because
# the entrypoint clones aaq-scraper, runs the refresh, and commits CSVs back.
FROM python:3.12-slim

# uv for dependency management (matches local/CI: uv sync / uv run).
COPY --from=ghcr.io/astral-sh/uv:0.5.11 /uv /usr/local/bin/uv

RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY . .

# Activate venv by adding it to PATH so `python` resolves to the venv's interpreter.
ENV PATH="/app/.venv/bin:$PATH"

# Non-root. The clone/commit workdir is a writable emptyDir mounted at runtime
# (the root FS is read-only in the pod), so this user only needs to read /app.
RUN useradd --create-home --uid 65532 appuser
USER 65532

CMD ["python", "-c", "import sumo; print('image ok')"]
