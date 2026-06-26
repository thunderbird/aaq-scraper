# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project status

This repository is currently a stub: it contains only `README.md` and a Mozilla Public License 2.0 `LICENSE`. There is no source code, build tooling, dependency manifest, or tests yet. When scaffolding the project, update this file with the actual build/lint/test commands and architecture as they are introduced.

## Purpose

`aaq-scraper` is intended to scrape the Mozilla support "Ask a Question" (AAQ) API. Per the README, the goal is to access the API as a browser would, pending an official API, while avoiding behavior that could amount to a denial-of-service against the upstream service. Any implementation should be deliberately rate-limited and respectful of the upstream service.

## Contribution requirement

Per the README, all participants must agree to and adhere to the [Mozilla Community Participation Guidelines](https://www.mozilla.org/about/governance/policies/participation/).

## License

Mozilla Public License 2.0 (MPL-2.0). New source files should carry the MPL-2.0 header.
