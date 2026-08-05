# reed — Production & Launch Plan

> How to use: work through the checklist top to bottom, one item at a time.
> Tick an item only when it is implemented, verified, and committed.

## Phase 1 — Easy install

- [ ] **Publish reed to PyPI** so install is `uv tool install reed-cli` / `pipx install reed-cli` (command stays `reed`)
  - Distribution name is `reed-cli` — the PyPI name `reed` is taken by an abandoned 0.0.x package. The import package and console command both stay `reed`.
  - Release version: 0.1.0 (matches current code; the 0.2.0 bump stays in Phase 2).
  - Metadata: add `authors` (paolomandica), `license = "Apache-2.0"`, keywords, classifiers, and `[project.urls]` → GitHub repo in `pyproject.toml`.
  - Add `LICENSE` (full Apache-2.0 text).
  - Flow: `uv build` → local wheel smoke test → TestPyPI → PyPI with API token.
  - README: lead Installation with `uv tool install reed-cli`; `reed doctor` explains missing system deps (ffmpeg/espeak-ng); keep the setup-script path for development.
  - Acceptance: fresh `uv tool install reed-cli` works; `reed --version`, `reed --help`, and `reed doctor` all pass.
- [ ] ~~Add a Dockerfile with model-cache and output volumes~~ — **deferred: not in current scope** (user decision)
- [ ] Make `reed doctor` the friendly first stop: detect missing system deps and print exact fix commands
- [ ] Verify macOS and Debian/Ubuntu setup scripts still work end to end

## Phase 2 — Production hardening

- [ ] Re-add GitHub Actions CI: ruff, mypy, pytest, package build check
- [ ] Add README badges (CI status, license, Python versions) — the LICENSE file itself is added in Phase 1 item 1
- [ ] Single source of truth for the version (package metadata); bump to 0.2.0; tag a GitHub release
- [ ] Harden the web server: result-file cleanup, task expiry, concurrency cap, graceful shutdown of running tasks
- [ ] Improve error UX: non-verbose CLI errors hint at `-v` for details; structured logging
- [ ] Expand tests: HTML parser fixtures, EPUB validation, web API task lifecycle, Markdown round-trip

## Phase 3 — Feature upgrades

- [ ] Chaptered M4B audiobooks from section headings (option alongside MP3)
- [ ] Multi-article anthology EPUB: queue several articles → one book with combined TOC
- [ ] CLI progress bar during audiobook generation
- [ ] Bundle a sample article (`examples/`) and add `reed demo` generating all three formats

## Phase 4 — Demo & README polish

- [ ] README top: one-line install, "try it in 60 seconds" section, web-UI screenshot/GIF
- [ ] Embed the demo video in the README
- [ ] Add a social/OG image for the repo

## Phase 5 — Record & launch on Twitter

- [ ] Script the 45–60s demo: drag in article → preview voices → generate → play chaptered audiobook
- [ ] Record with screen capture + captions
- [ ] Prepare launch thread: video, sample audio clip, repo link
- [ ] Post, pin the thread, and link the demo from the README

## Decisions log

- 2026-08-05 — Docker is deferred; not in current scope.
- 2026-08-05 — PyPI distribution name: `reed-cli` (command stays `reed`).
- 2026-08-05 — First release version: 0.1.0; publish via TestPyPI first, then PyPI with an API token.
