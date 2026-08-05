# reed — Production & Launch Plan

> How to use: work through the checklist top to bottom, one item at a time.
> Tick an item only when it is implemented, verified, and committed.

## Phase 1 — Easy install

- [x] **Publish reed to PyPI** so install is `uv tool install reed-cli` / `pipx install reed-cli` (command stays `reed`)
  - Distribution name is `reed-cli` — the PyPI name `reed` is taken by an abandoned 0.0.x package. The import package and console command both stay `reed`.
  - Release version: 0.1.0 (matches current code; the 0.2.0 bump stays in Phase 2).
  - Metadata: add `authors` (paolomandica), `license = "Apache-2.0"`, keywords, classifiers, and `[project.urls]` → GitHub repo in `pyproject.toml`.
  - Add `LICENSE` (full Apache-2.0 text).
  - Flow: `uv build` → local wheel smoke test → TestPyPI → PyPI with API token.
  - README: lead Installation with `uv tool install reed-cli`; `reed doctor` explains missing system deps (ffmpeg/espeak-ng); keep the setup-script path for development.
  - Acceptance: fresh `uv tool install reed-cli` works; `reed --version`, `reed --help`, and `reed doctor` all pass.
- [ ] ~~Add a Dockerfile with model-cache and output volumes~~ — **deferred: not in current scope** (user decision)
- [x] Make `reed doctor` the friendly first stop: detect missing system deps and print exact fix commands
- [x] Verify macOS and Debian/Ubuntu setup scripts still work end to end (Linux run pending on a clean Ubuntu machine)

## Phase 2 — Production hardening

- [x] Re-add GitHub Actions CI: ruff, mypy, unittest, package build check
- [x] Add README badges (CI status, license, Python versions) — the LICENSE file itself is added in Phase 1 item 1
- [x] Single source of truth for the version (package metadata); bump to 0.2.0; tag a GitHub release
- [x] Harden the web server: result-file cleanup, task expiry, concurrency cap, graceful shutdown of running tasks
- [x] Improve error UX: non-verbose CLI errors hint at `-v` for details; structured logging
- [x] Expand tests: HTML parser fixtures, EPUB validation, web API task lifecycle, Markdown round-trip

## Phase 3 — Feature upgrades

- [x] **Chaptered M4B audiobooks from section headings (option alongside MP3)**
  - `reed audiobook --format m4b` encodes AAC/M4B via ffmpeg and embeds chapter markers derived from the article's section headings; pre-heading content becomes an "Introduction" chapter, and heading-less articles get a single chapter named by the title.
  - Chapter timings are computed from real chunk durations plus pauses as audio is generated, so markers stay in sync with the narration (verified with ffprobe).
  - `reed demo --format m4b` passes the option through; MP3 stays the default everywhere, and the web UI keeps MP3 for now. *(Updated before the 0.3.0 release: M4B is now the default; `--format mp3` opts into flat MP3, and web audiobooks serve `.m4b`.)*
- [ ] Multi-article anthology EPUB: queue several articles → one book with combined TOC
- [x] **CLI progress bar during audiobook generation**
  - TTS chunk bar is TTY-aware and shows a live snippet of the narration segment; per-chunk `Chunk i/n` lines only print when stdout is not a TTY (piped runs).
  - Determinate ffmpeg encode bar driven by `-progress pipe:1` (`out_time_ms` parsed against the known audio duration).
  - Web mode keeps its `progress_callback` as the only progress channel — no bars or chunk lines leak into server logs; encode progress is reported through the callback too.
- [x] **Bundle a sample article (`examples/`) and add `reed demo` generating all three formats**
  - Sample: `examples/reed-demo.md` — original ~200-word Markdown article (headings, blockquote, list), force-included into the wheel as `reed/examples/reed-demo.md`; a resolver falls back to the repo copy in source checkouts.
  - `reed demo [--output-dir reed-demo] [--voice af_heart] [--speed 1.0] [--max-chunks 0] [--no-audiobook]` generates EPUB → Markdown → MP3 into one folder. Audiobook is on by default; `--no-audiobook` skips it (and the ffmpeg/espeak-ng pre-check); missing deps print the fix command plus a `reed doctor` hint.
  - CI asserts the wheel contains `reed/examples/reed-demo.md`.

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
- 2026-08-05 — Phase 1 item 1: local packaging verified (build, wheel install, publish dry-run); TestPyPI/PyPI uploads await tokens.
- 2026-08-05 — Phase 1 item 1: done — `reed-cli` 0.1.0 published to PyPI; fresh `uv tool install reed-cli` verified.
- 2026-08-05 — Phase 1 item 3: done — expanded `reed doctor` with Python/uv checks, per-OS fix commands, and TTS-library check.
- 2026-08-05 — Phase 1 item 4: macOS script verified locally; Linux end-to-end run pending on a clean Ubuntu machine (user step).
- 2026-08-05 — Phase 2 CI: Ubuntu + macOS on Python 3.13; ruff, mypy, unittest, build; badges added.
- 2026-08-05 — Phase 2 web: 1 concurrent audiobook generation (429 on overload), 1-hour result expiry, shutdown cleanup.
- 2026-08-05 — Phase 2 tests: 26 passing (parser fixtures, EPUB round-trip, web API lifecycle).
- 2026-08-05 — Phase 2 version: single-sourced via package metadata, bumped to 0.2.0; tag v0.2.0, GitHub release, and PyPI upload pending.
- 2026-08-05 — Phase 2 version: done — v0.2.0 tagged, GitHub release created, and reed-cli 0.2.0 published to PyPI (fresh install verified).
- 2026-08-05 — Phase 3 progress bar: TTY-aware chunk bar with narration snippet plus determinate ffmpeg encode bar; web keeps callback-only progress.
- 2026-08-05 — Phase 3 demo: sample is original Markdown at `examples/reed-demo.md`, shipped inside the wheel; audiobook on by default with `--no-audiobook` opt-out.
- 2026-08-05 — Phase 3 M4B: `--format mp3|m4b` CLI option on `audiobook` and `demo`; chapters from headings.
- 2026-08-05 — M4B is the default audiobook format (CLI, `demo`, and web); `--format mp3` opts into flat MP3.
- 2026-08-05 — MPS autodetect: Apple Silicon uses the Metal GPU (mps) when `torch.backends.mps.is_available()`; otherwise CUDA, then CPU; a failed MPS init falls back to CPU with a warning. Verified on this M3 MacBook Air (the Codex sandbox blocks Metal, so sandboxed runs use CPU).
- 2026-08-05 — Web demo: "✨ Generate the demo" button runs all three formats from the bundled sample via `POST /api/demo` (three pollable/downloadable tasks; audiobook respects the 1-at-a-time slot). Sample resolver moved to `src/reed/sample.py` so CLI and web share it.
- 2026-08-05 — v0.3.0 prepared: M4B chapters, progress bars, demo (CLI + web), MPS autodetect. Tag `v0.3.0` pushed to GitHub; PyPI publish pending (user step).
- 2026-08-05 — Release workflow documented in `AGENTS.md` (version bump incl. `uv.lock`, build, tag, publish hand-off, `gh release create`).
- 2026-08-05 — v0.3.0 released: `reed-cli` 0.3.0 published to PyPI and GitHub release created (tag `v0.3.0`).
