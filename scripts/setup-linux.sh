#!/usr/bin/env bash
# Set up reed on Debian- and Ubuntu-based Linux. Requires uv first.

set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repo_dir="$(cd -- "$script_dir/.." && pwd)"
cd "$repo_dir"

if ! command -v uv >/dev/null 2>&1; then
    echo "Error: uv is required but was not found on PATH." >&2
    echo "Install it first: https://docs.astral.sh/uv/getting-started/installation/" >&2
    exit 1
fi

if ! command -v apt-get >/dev/null 2>&1; then
    echo "Error: this setup script supports Debian- and Ubuntu-based systems only." >&2
    exit 1
fi

if [[ $EUID -eq 0 ]]; then
    apt_prefix=()
elif command -v sudo >/dev/null 2>&1; then
    apt_prefix=(sudo)
else
    echo "Error: run this script as root or install sudo." >&2
    exit 1
fi

echo "Installing system dependencies..."
"${apt_prefix[@]}" apt-get update
"${apt_prefix[@]}" apt-get install --yes ffmpeg espeak-ng

echo "Creating the Python environment..."
uv sync --locked

command -v ffmpeg >/dev/null
command -v espeak-ng >/dev/null
.venv/bin/reed --help >/dev/null

echo
echo "reed is ready. Activate the environment, then start the web interface:"
echo "  source .venv/bin/activate"
echo "  reed web"
