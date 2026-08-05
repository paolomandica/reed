# AGENTS.md

Guidance for working in this repository. reed is a CLI + web tool that turns
saved articles into EPUB, Markdown, and audiobooks (Kokoro-82M TTS).

## Project layout

- `src/reed/` — import package (`reed`); the console command is also `reed`
- `src/reed/cli.py` — Click CLI: `epub`, `audiobook`, `markdown`, `demo`,
  `web`, `doctor`
- `src/reed/web.py` + `src/reed/static/` — Flask web UI
- `src/reed/outputs/` — EPUB / Markdown / audiobook (M4B default, MP3 opt-in)
- `src/reed/sample.py` — bundled demo article resolver (CLI + web share it)
- `examples/reed-demo.md` — demo sample, force-included into the wheel
- `tests/` — `unittest` suite (no pytest)
- `PLAN.md` — authoritative roadmap; tick items only after they are
  implemented, verified, and committed

## Development

- Python 3.13, managed with uv: `uv sync --locked --extra dev`
- Required checks (all must pass before committing):
  - `uv run ruff check .`
  - `uv run mypy src` (tests are intentionally excluded from mypy)
  - `uv run python -m unittest discover -s tests`
- `git commit` and `git push` require escalated approval in this workspace.

## Versioning

- Version is single-sourced from `pyproject.toml` (`[project] version`) and
  `src/reed/__init__.py` (`__version__`) — always bump **both together**.
  The CLI reports it via `importlib.metadata.version("reed-cli")`, falling
  back to `__version__` for source checkouts.
- `uv.lock` also pins the project version (as the editable `reed-cli` entry):
  after bumping, run `uv lock` and commit the refreshed lockfile, otherwise
  CI's `uv sync --locked` fails.
- Distribution name is **`reed-cli`** (the PyPI name `reed` is taken); the
  import package and console command stay `reed`.
- Semver in use: bump minor for features (e.g. 0.2.0 -> 0.3.0), patch for
  fixes. Keep release notes matching the diff.

## Release workflow (when a new version is ready)

The agent prepares everything; **the user performs the PyPI upload** because
they hold the API token. Pause and hand off the exact commands — never
publish yourself or assume token access.

1. Finish and commit the feature/fix work; update `PLAN.md` (tick completed
   items, add decisions-log entries) and README as needed.
2. Run the full check suite (see Development).
3. Bump the version in `pyproject.toml` **and** `src/reed/__init__.py`, run
   `uv lock`, and commit both files.
4. Build clean artifacts and sanity-check the wheel:
   ```bash
   rm -rf dist
   uv build
   unzip -l dist/*.whl | grep -E "reed/examples/reed-demo.md|static/"
   ```
5. Push main, tag the release, and push the tag:
   ```bash
   git push origin main
   git tag vX.Y.Z
   git push origin vX.Y.Z
   ```
   If the tag already exists and nothing was published yet, move it instead:
   `git tag -f vX.Y.Z` then `git push origin vX.Y.Z --force`.
6. Hand off to the user:
   - Publish to PyPI: `uv publish` (or
     `UV_PUBLISH_TOKEN=<token> uv publish`; TestPyPI first:
     `uv publish --publish-url https://test.pypi.org/legacy/`)
   - Create the GitHub release (`gh` is installed):
     `gh release create vX.Y.Z --title "reed-cli X.Y.Z" --notes "<summary>"`
     (add `dist/*` to attach the wheel + sdist)
   - Verify a fresh install: `uv tool install --force reed-cli`, then
     `reed --version`, `reed doctor`, `reed demo --no-audiobook`
7. After the user confirms the release is live, tick the PLAN.md release
   entry and add a decisions-log line.

## Workspace / sandbox notes

- uv cache, network, and model-download operations (`uv lock`, `uv sync`,
  `uv build`, `uv tool install`, `uv publish`) and `git commit`/`git push`
  need escalated approval here.
- The Codex sandbox blocks Metal, so `torch.backends.mps.is_available()`
  returns False inside it; sandboxed runs fall back to CPU by design. Verify
  MPS behavior with escalated commands (real runs on this M3 use MPS).
- `dist/` can accumulate stale artifacts from older versions — always
  `rm -rf dist` before `uv build`.
