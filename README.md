# reed

Convert saved article pages to EPUBs, Markdown, and MP3 audiobooks.

Works with saved HTML from Substack, blogs, news sites, and any
long-form article page.

## Installation

```bash
uv sync
```

### System dependencies

- **ffmpeg** — required for audiobook generation (WAV → MP3 encoding)
- **espeak-ng** — required for Kokoro TTS (phoneme processing)

  ```bash
  brew install ffmpeg espeak-ng   # macOS
  apt install ffmpeg espeak-ng    # Linux
  ```

## Usage

```
Usage: reed [OPTIONS] COMMAND [ARGS]...

Commands:
  epub        Generate a Kindle-compatible EPUB
  audiobook   Generate an MP3 audiobook using Kokoro-82M TTS
  markdown    Generate a Markdown file
  web         Start a browser-based web interface
```

### EPUB generation

Save any article page from your browser (File → Save As → Webpage, HTML Only), then:

```bash
reed epub --html article.html
```

This works with Substack, blogs, news sites, and most long-form
article pages. The parser automatically detects the page structure and
extracts the title, author, date, and body content.

The EPUB is saved to `epubs/article-title.epub` ready to send to your Kindle.

#### Options

```
Usage: reed epub [OPTIONS]

Options:
  -o, --output PATH    Output EPUB path (default: epubs/<title-slug>.epub)
  --html PATH          Use a local HTML file
  --md PATH            Use a local Markdown file
  --verbose, -v        Show detailed progress
  --help               Show this message
```

### Markdown generation

Generate a Markdown file from a saved article:

```bash
# From a saved HTML file (Substack, blogs, or any article page)
reed markdown --html article.html

# From a previously generated Markdown file (round-trip)
reed markdown --md article.md

# Custom output path
reed markdown --html article.html -o article.md
```

Default output path: `articles/<title-slug>.md`

### Audiobook generation

Generate an MP3 audiobook from an article using **Kokoro-82M**
(hexgrad/Kokoro-82M) — a lightweight 82M-parameter open-weight TTS model
with 20 American English voices, Apache-2.0 licensed.

Works with Substack, blogs, news sites, and any saved HTML article page.

#### Prerequisites

- **ffmpeg** and **espeak-ng** installed (see [System dependencies](#system-dependencies))
- The Kokoro model is downloaded from Hugging Face on first run and cached locally — no API key needed.

#### Quick start

```bash
# Default voice (af_heart)
reed audiobook --html article.html

# Pick a different voice
reed audiobook --html article.html --voice af_bella

# List all available voices
reed audiobook --list-voices

# Adjust speed
reed audiobook --html article.html --speed 0.85   # 15% slower
reed audiobook --html article.html --speed 1.25   # faster

# Custom output path
reed audiobook -o my-article.mp3 --html article.html

# From a Markdown file
reed audiobook --md article.md --voice am_puck
```

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
                           audiobooks/<title-slug>.mp3)
  --html PATH              Use a local HTML file
  --md PATH                Use a local Markdown file
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

### Web Interface

The easiest way to use reed is through the browser:

```bash
reed web
```

This starts a local web server and opens `http://127.0.0.1:8080` in your browser.
From there you can upload a saved HTML or Markdown file, or paste text directly
into the editor. Pick EPUB, Markdown, or audiobook, and download the result —
no terminal needed.

```
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

# Custom port, don't auto-open browser
reed web --port 3000 --no-open

# Bind to all interfaces (accessible from other devices on your network)
reed web --host 0.0.0.0 --port 8080
```

## Kindle Compatibility

Generated EPUBs include:
- Proper metadata (title, author with handle, date, language)
- Kindle-optimized CSS (no flexbox, no absolute positioning, serif fonts)
- Auto-generated table of contents from article headings
- Title page with author and date

Send the EPUB to your Kindle using the Send-to-Kindle app or email.

## Supported Sources

### Substack and other article pages

Save the page as HTML from your browser (File → Save As → Webpage, HTML Only),
then use `--html`:

```bash
reed epub --html young-adults-are-poor.html
reed markdown --html young-adults-are-poor.html
reed audiobook --html young-adults-are-poor.html
```

The HTML parser uses heuristics to find the article body, title, author, and
date — it works with Substack, personal blogs, and most CMS-generated article
pages without site-specific selectors.

### Round-tripping

reed can read its own Markdown output back as input — useful for editing
content before regenerating:

```bash
reed markdown --html article.html -o article.md
# ... edit article.md ...
reed epub --md article.md
reed audiobook --md article.md
```

## How It Works

1. **Save** the article page from your browser as HTML (File → Save As → Webpage, HTML Only)
2. **Extract**: reed parses the HTML — detecting metadata (title, author, date) and the article body using heuristics that work across Substack, blogs, news sites, and most CMS platforms
3. **Convert**: the extracted content is converted to your chosen format:
   - **EPUB**: Kindle-optimized ebook with proper metadata, TOC, and styling
   - **Markdown**: HTML body is converted via `markdownify` with a metadata header
   - **Audiobook**: Content sections are split into TTS-friendly chunks, synthesized with Kokoro-82M, and encoded to MP3
