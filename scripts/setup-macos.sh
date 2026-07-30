#!/usr/bin/env bash
# Set up reed on macOS. Requires Homebrew and uv to be installed first.

set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repo_dir="$(cd -- "$script_dir/.." && pwd)"
cd "$repo_dir"

if ! /usr/bin/uname -m | /usr/bin/grep -qx arm64; then
    echo "Error: reed currently requires an Apple Silicon Mac (PyTorch has no macOS x86_64 wheel for this project)." >&2
    exit 1
fi

if ! command -v uv >/dev/null 2>&1; then
    echo "Error: uv is required but was not found on PATH." >&2
    echo "Install it first: https://docs.astral.sh/uv/getting-started/installation/" >&2
    exit 1
fi

if ! command -v brew >/dev/null 2>&1; then
    echo "Error: Homebrew is required but was not found on PATH." >&2
    echo "Install it first: https://brew.sh/" >&2
    exit 1
fi

echo "Installing system dependencies..."
brew install ffmpeg espeak-ng

echo "Creating the Python environment..."
uv sync --locked

command -v ffmpeg >/dev/null
command -v espeak-ng >/dev/null
.venv/bin/reed --help >/dev/null

echo
echo "reed is ready. Activate the environment, then start the web interface:"
echo "  source .venv/bin/activate"
echo "  reed web"
