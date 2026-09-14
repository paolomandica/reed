<p align="center">
  <img src="assets/reed-logo.png" alt="reed logo" width="180">
</p>

# reed

[![PyPI version](https://img.shields.io/pypi/v/reed-cli.svg)](https://pypi.org/project/reed-cli/)
[![License](https://img.shields.io/github/license/paolomandica/reed.svg)](LICENSE)
[![Python versions](https://img.shields.io/pypi/pyversions/reed-cli.svg)](https://pypi.org/project/reed-cli/)
[![CI](https://img.shields.io/github/actions/workflow/status/paolomandica/reed/ci.yml.svg)](https://github.com/paolomandica/reed/actions)

Convert articles to EPUBs, Markdown, and audiobooks.

Paste your article text or upload a Markdown file — reed handles the rest.

## Installation

### Quick install (recommended)

Install reed with [uv](https://docs.astral.sh/uv/getting-started/installation/):

```bash
uv tool install reed-cli
```

Or with [pipx](https://pipx.pypa.io/):

```bash
pipx install reed-cli
```

Then verify your setup:

```bash
reed doctor
```

`reed doctor` checks the Python version, the `ffmpeg` and `espeak-ng`
system dependencies, and the TTS libraries — printing the exact fix
command for your operating system if anything is missing. The Kokoro
TTS model is downloaded from Hugging Face on first audiobook generation
and cached locally; no API key is needed.

### Development setup

From a clone of this repository (macOS support currently requires Apple Silicon):

```bash
# macOS
bash scripts/setup-macos.sh

# Debian or Ubuntu
bash scripts/setup-linux.sh
```

The scripts install `ffmpeg` and `espeak-ng`, create the `.venv` environment, and install the locked Python dependencies.

Activate the environment before using reed (and once in each new terminal):

```bash
source .venv/bin/activate
```

You can now use `reed` directly.

## System dependencies

Audiobook generation requires `ffmpeg` and `espeak-ng` on your PATH.
`reed doctor` checks for them and prints the right command:

```bash
# macOS (Homebrew)
brew install ffmpeg espeak-ng

# Debian / Ubuntu
sudo apt-get install -y ffmpeg espeak-ng
```

## Web Interface

The browser interface is the recommended way to use reed:

```bash
reed web
```

This starts a local server and opens <http://127.0.0.1:8080>. Paste your
article text directly (default), or switch to uploading a Markdown (`.md`) or
plain text (`.txt`) file. Choose EPUB, Markdown, or audiobook and download the
result.

Markdown is recommended — headings become chapters, and metadata (author, date)
is detected automatically. Plain text works too, but with less structure.

The first audiobook or voice preview downloads the Kokoro model from Hugging Face and caches it locally; no API key is needed.

Want to see it work before touching your own files? Click **✨ Generate the
demo** to produce all three formats from a bundled sample article — no file
needed.

```text
Usage: reed web [OPTIONS]

Options:
  --host TEXT          Host address to bind to  [default: 127.0.0.1]
  --port INTEGER       Port to listen on  [default: 8080]
  --open / --no-open   Open browser automatically  [default: open]
  --debug              Enable Flask debug mode
  --help               Show this message and exit
```

Examples:

```bash
# Default: start on localhost:8080, open browser
reed web

# Custom port, do not auto-open a browser
reed web --port 3000 --no-open

# Make the interface available on your local network
reed web --host 0.0.0.0 --port 8080
```

## Command-line usage

```
Usage: reed [OPTIONS] COMMAND [ARGS]...

Commands:
  epub        Generate a Kindle-compatible EPUB
  audiobook   Generate an M4B or MP3 audiobook using Kokoro-82M TTS
  markdown    Generate a Markdown file
  demo        Try all three formats with a bundled sample article
  web         Start a browser-based web interface
```

### Demo

Try every output format in one command with a bundled sample article:

```bash
reed demo
```

This generates `reed-demo/<article-title>.epub`, `.md`, and a chaptered `.m4b`
audiobook. Add `--no-audiobook` to skip the audiobook (and its first-run
Kokoro model download):

```bash
reed demo --no-audiobook
```

### EPUB generation

Provide a Markdown (`.md`) or plain text (`.txt`) file:

```bash
reed epub -i article.md
```

The EPUB is saved to `epubs/article-title.epub` ready to send to your Kindle.

#### Options

```
Usage: reed epub [OPTIONS]

Options:
  -o, --output PATH     Output EPUB path (default: epubs/<title-slug>.epub)
  -i, --input PATH       Markdown (.md) or plain text (.txt) file
  -v, --verbose          Show detailed progress
  --help                 Show this message
```

### Markdown generation

Generate a Markdown file from an input file:

```bash
# From a Markdown file
reed markdown -i article.md

# From a plain text file
reed markdown -i article.txt

# Custom output path
reed markdown -i article.md -o output.md
```

Default output path: `articles/<title-slug>.md`

### Audiobook generation

Generate a chaptered M4B audiobook (or flat MP3) from an article using **Kokoro-82M**
(hexgrad/Kokoro-82M) — a lightweight 82M-parameter open-weight TTS model
with 20 American English voices, Apache-2.0 licensed.

On Apple Silicon Macs, reed automatically runs the model on the Metal GPU
(MPS) when available and falls back to CPU otherwise.

#### Prerequisites

- **ffmpeg** and **espeak-ng** installed (see [System dependencies](#system-dependencies))
- The Kokoro model is downloaded from Hugging Face on first run and cached locally — no API key needed.

#### Quick start

```bash
# Default: chaptered M4B audiobook (voice af_heart)
reed audiobook -i article.md

# Flat MP3 instead
reed audiobook -i article.md --format mp3

# Pick a different voice
reed audiobook -i article.md --voice af_bella

# List all available voices
reed audiobook --list-voices

# Adjust speed
reed audiobook -i article.md --speed 0.85   # 15% slower
reed audiobook -i article.md --speed 1.25   # faster

# Custom output path
reed audiobook -o my-article.m4b -i article.md
```

#### Chaptered M4B (Apple Books / VLC)

M4B is the default audiobook format. Chapters are derived from the article's
section headings, so players show and jump between sections:

```bash
reed audiobook -i article.md
```

Prefer a flat MP3 for maximum compatibility? Use `--format mp3` — the same
voices, speed, and progress bars apply to both.

#### Voices

Kokoro-82M includes 20 American English voices. The three featured in the web interface:

| Voice | Grade | Character |
|-------|-------|-----------|
| `af_heart` | A | ❤️ Warm, natural |
| `af_bella` | A- | 🔥 Expressive |
| `am_puck` | C+ | 🎧 Clear, balanced |

Use `reed audiobook --list-voices` for the full list.

#### Options

```
Usage: reed audiobook [OPTIONS]

Options:
  -o, --output PATH        Output audio file path (default:
                           audiobooks/<title-slug>.m4b)
  -i, --input PATH         Markdown (.md) or plain text (.txt) file
  --voice TEXT             Kokoro voice  [default: af_heart]
  --list-voices            List available Kokoro voices and exit.
  --speed FLOAT            Playback speed (0.5–2.0)  [default: 1.0]
  -v, --verbose            Show detailed progress
  --max-sections INTEGER   Only process the first N sections (0 = all) —
                           quick-test shortcut
  --help                   Show this message
```

The article text is split at natural narration boundaries and exported as a
64 kbps MP3. Titles, author credits, headings, paragraphs, and list items use
appropriately paced transitions; speed is applied natively during generation.

When the input is Markdown, reed supports its own metadata header and common
article Markdown. It recognizes an initial title (including `# Title: ...`),
leading author/date/source fields, headings, lists, blockquotes, links, and
image alt text. Audiobooks begin with the title and author, omit dates and
source URLs, retain link labels rather than URLs, and skip code blocks, inline
code, and tables. Embedded HTML is sanitized and normalized to Markdown, so
its prose, headings, lists, and image alt text are retained while scripts,
navigation, embeds, and other non-content elements are discarded.

## Kindle Compatibility

Generated EPUBs include:
- Proper metadata (title, author with handle, date, language)
- Kindle-optimized CSS (no flexbox, no absolute positioning, serif fonts)
- Auto-generated table of contents from article headings
- Title page with author and date

Send the EPUB to your Kindle using the Send-to-Kindle app or email.

## Markdown Format

reed works best with Markdown input. Here's a quick template:

```markdown
# Article Title

*By Author Name — January 15, 2024*

---

## First Section

Your article text goes here.

## Second Section

More text here.
```

The first `#` heading becomes the title. The `*By ...*` line is parsed for
the author and date. Headings (`##`, `###`) become chapters in audiobooks and
table-of-contents entries in EPUBs.

Plain text (`.txt`) files are also accepted — they're treated as a single
section with no metadata detection.

## How It Works

1. **Provide** your article — paste text in the web UI or upload a Markdown/plain text file
2. **Extract**: reed parses the input, detecting metadata (title, author, date) and structuring content into sections
3. **Convert**: the extracted content is converted to your chosen format:
   - **EPUB**: Kindle-optimized ebook with proper metadata, TOC, and styling
   - **Markdown**: structured Markdown with a metadata header
   - **Audiobook**: Content sections are split into TTS-friendly chunks, synthesized with Kokoro-82M, and encoded to M4B or MP3
