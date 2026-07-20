# reed

Convert online articles to EPUBs, Markdown, and MP3 audiobooks.

Works with X.com (Twitter) articles/threads, Substack newsletters, and
saved HTML from any long-form article page.

## Installation

```bash
uv sync
```

### System dependencies

- **ffmpeg** — required for audiobook generation (WAV → MP3 encoding)
  ```bash
  brew install ffmpeg    # macOS
  apt install ffmpeg     # Linux
  ```

## Usage

```
Usage: reed [OPTIONS] COMMAND [ARGS]...

Commands:
  epub        Generate a Kindle-compatible EPUB
  audiobook   Generate an MP3 audiobook using Chatterbox TTS
  markdown    Generate a Markdown file
  fetch       Download fully-rendered HTML of an X.com article
  web         Start a browser-based web interface
```

### Fetch (download rendered HTML)

For X Articles (long-form posts at `x.com/<user>/article/<id>`) that are JavaScript-rendered,
use `reed fetch` to download the fully-rendered HTML via a headless browser:

```bash
# Basic usage — saves x_article_<id>.html in the current directory
reed fetch "https://x.com/JohannKurtz/article/2077143118524417439"

# Custom output path and text extraction
reed fetch "https://x.com/..." -o article.html --text article.txt

# With authentication (for articles behind a login wall)
reed fetch --save-auth cookies.json       # one-time: open browser, log in
reed fetch --auth cookies.json "https://x.com/..."  # reuse saved session

# Debugging: run browser visibly
reed fetch --headed "https://x.com/..."
```

```
Usage: reed fetch [OPTIONS] [URL]

Options:
  -o, --output PATH  Output HTML path (default: derived from article ID)
  --text PATH        Also extract plain text to this file
  --auth PATH        Playwright storage_state JSON for logged-in sessions
  --headed           Run browser visibly (for debugging or manual login)
  --timeout INTEGER  Navigation timeout in milliseconds  [default: 60000]
  --save-auth PATH   Open browser for manual login, then save cookies to this
                     path
  --help             Show this message and exit
```

**Requirements:** `reed fetch` needs Playwright and a Chromium browser binary:

```bash
uv sync --extra browser    # or: pip install reed[browser]
playwright install chromium
```

### Web Interface

The easiest way to use reed is through the browser:

```bash
reed web
```

This starts a local web server and opens `http://127.0.0.1:8080` in your browser.
From there you can paste an X.com URL or upload a saved HTML file (X.com,
Substack, or any article page), pick EPUB, Markdown, audiobook, or rendered
HTML, and download the result — no terminal needed.

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

### EPUB generation

#### From a saved HTML file (always works)

Save any article page from your browser (File → Save As → Webpage, Complete), then:

```bash
reed epub --html article.html
```

This works with X.com articles, Substack newsletters, and most long-form
article pages. The parser automatically detects the page structure and
extracts the title, author, date, and body content.

The EPUB is saved to `epubs/article-title.epub` ready to send to your Kindle.

#### From a URL (regular tweets and threads)

```bash
reed epub https://x.com/username/status/123456789
```

Works for tweets and threads where the text content is in the tweet body.
Uses the public FxTwitter API (no authentication required).

> **Note:** Substack and other non-X.com URLs are not directly supported via URL.
> Save the page as HTML first (`reed epub --html page.html`).

#### Options

```
Usage: reed epub [OPTIONS] [URL]

Options:
  -o, --output PATH    Output EPUB path (default: epubs/<title-slug>.epub)
  --html PATH          Use a local HTML file instead of downloading
  --verbose, -v        Show detailed progress
  --auth PATH          Playwright storage_state JSON for logged-in sessions
  --headed             Run browser visibly (for debugging)
  --help               Show this message
```

### Markdown generation

Generate a Markdown file from an article:

```bash
# From a URL
reed markdown https://x.com/username/status/123456789

# From a saved HTML file (X.com, Substack, or any article page)
reed markdown --html article.html

# Custom output path
reed markdown https://x.com/... -o article.md
```

Default output path: `articles/<title-slug>.md`

### Audiobook generation

Generate an MP3 audiobook from an article using Chatterbox TTS with zero-shot
voice cloning. Works with X.com articles, Substack newsletters, and any saved
HTML article page.

#### Prerequisites

- **ffmpeg** installed on your system (see [System dependencies](#system-dependencies))
- A reference audio clip (~5–30s) for voice cloning is optional — the model has a built-in default voice
- The Chatterbox model is downloaded from Hugging Face on first run and cached locally — no API key needed.

#### Quick start

```bash
# Use the built-in default voice (no reference audio needed)
reed audiobook --html article.html

# Voice cloning with a reference clip
reed audiobook --html article.html -r voice.wav

# Cache the voice prompt for faster subsequent runs
reed audiobook --html article.html -r voice.wav --save-prompt my_voice.pt
reed audiobook --html article.html -p my_voice.pt

# Custom output path
reed audiobook -o my-article.mp3 --html article.html
```

#### Options

```
Usage: reed audiobook [OPTIONS] [URL]

Options:
  -o, --output PATH           Output audio file path (default: audiobooks/<title-slug>.mp3)
  --html PATH                 Use a local HTML file instead of downloading
  -r, --reference-audio PATH  Reference audio clip for voice cloning (optional)
  -p, --voice-prompt PATH     Pre-computed voice prompt (.pt) — skips audio loading / ASR
  --save-prompt PATH          Save the computed voice prompt to this .pt file for later reuse
  --device [cpu|cuda|mps]     Device to run the TTS model on  [default: mps]
  -v, --verbose               Show detailed progress
  --max-sections INTEGER      Only process the first N sections (0 = all) — quick-test shortcut
  --auth PATH                 Playwright storage_state JSON for logged-in sessions
  --headed                    Run browser visibly (for debugging)
  --help                      Show this message
```

The article text is automatically split into chunks. Audio chunks are concatenated with a 0.5-second pause between sections and exported as a 64 kbps MP3.

Default output path: `audiobooks/<title-slug>.mp3`

## Kindle Compatibility

Generated EPUBs include:
- Proper metadata (title, author with handle, date, language)
- Kindle-optimized CSS (no flexbox, no absolute positioning, serif fonts)
- Auto-generated table of contents from article headings
- Title page with author and date

Send the EPUB to your Kindle using the Send-to-Kindle app or email.

## Supported Sources

### X.com (Twitter)

Tweets and threads are downloaded via the public FxTwitter API. X Articles
(long-form posts at `x.com/<user>/article/<id>`) are JavaScript-rendered and
require the Playwright browser fallback.

### Substack and other article pages

Save the page as HTML from your browser (File → Save As → Webpage, Complete),
then use `--html`:

```bash
reed epub --html young-adults-are-poor.html
reed markdown --html young-adults-are-poor.html
reed audiobook --html young-adults-are-poor.html
```

The HTML parser uses heuristics to find the article body, title, author, and
date — it works with Substack, personal blogs, and most CMS-generated article
pages without site-specific selectors.

## X Articles (Long-form posts)

X Articles (long-form posts published via the X Premium Articles feature at
`x.com/<user>/article/<id>`) are JavaScript-rendered and cannot be downloaded
with a plain HTTP request. reed offers two ways to handle them:

### Automatic (with Playwright)

Install the browser extra and Chromium:

```bash
uv sync --extra browser
playwright install chromium
```

Then `reed epub`, `reed markdown`, and `reed audiobook` will automatically
use a headless browser for X Article URLs — no manual save step needed:

```bash
reed epub "https://x.com/user/article/123"
reed markdown "https://x.com/user/article/123"
reed audiobook "https://x.com/user/article/123"
```

If the article is behind a login wall, save your session first:

```bash
reed fetch --save-auth cookies.json    # log in manually in the opened browser
reed epub --auth cookies.json "https://x.com/user/article/123"
```

### Manual (no Playwright)

If you prefer not to install Playwright, you can still save the page manually:

1. Open the article on x.com
2. Save the page as HTML (File → Save As → Webpage, Complete)
3. Run: `reed epub --html saved_page.html`

The tool will extract the title, author, date, and full article body from the saved HTML.
