# reed

Convert X.com (Twitter) articles and threads to Kindle-compatible EPUBs and MP3 audiobooks.

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
  audiobook   Generate an MP3 audiobook using Hugging Face TTS
```

### EPUB generation

#### From a saved HTML file (always works)

Save the X.com article page from your browser (File → Save As → Webpage, Complete), then:

```bash
reed epub --html article.html
```

This produces `epubs/article-title.epub` ready to send to your Kindle.

#### From a URL (regular tweets and threads)

```bash
reed epub https://x.com/username/status/123456789
```

Works for tweets and threads where the text content is in the tweet body. Uses the public FxTwitter API (no authentication required).

#### Options

```
Usage: reed epub [OPTIONS] [URL]

Options:
  -o, --output PATH    Output EPUB path (default: epubs/<title-slug>.epub)
  --html PATH          Use a local HTML file instead of downloading
  --verbose, -v        Show detailed progress
  --help               Show this message
```

### Audiobook generation

Generate an MP3 audiobook from an article using Hugging Face text-to-speech models.

#### Prerequisites

1. **HF_TOKEN** environment variable — create a token at https://huggingface.co/settings/tokens
   ```bash
   export HF_TOKEN=hf_...
   ```
2. **ffmpeg** installed on your system (see [System dependencies](#system-dependencies))

#### Usage

```bash
# From a saved HTML file
reed audiobook --html article.html

# From a URL
reed audiobook https://x.com/username/status/123456789

# Custom output path
reed audiobook -o my-article.mp3 --html article.html

# Verbose output
reed audiobook -v --html article.html
```

#### Model selection

When you run the `audiobook` command, you'll be prompted to choose a TTS model:

```
Available TTS models:
  1. CosyVoice2 (FunAudioLLM)
  2. Chatterbox Turbo (ResembleAI)
Choose a model [1]:
```

Just enter `1` or `2` — no need to type the full model name.

The article text is automatically split into chunks that respect each model's character limit. Audio chunks are concatenated with a 0.5-second pause between sections and exported as a 64 kbps MP3.

Default output path: `audiobooks/<title-slug>.mp3`

## Kindle Compatibility

Generated EPUBs include:
- Proper metadata (title, author with handle, date, language)
- Kindle-optimized CSS (no flexbox, no absolute positioning, serif fonts)
- Auto-generated table of contents from article headings
- Title page with author and date

Send the EPUB to your Kindle using the Send-to-Kindle app or email.

## X Articles (Long-form posts)

X Articles (long-form posts published via the X Premium Articles feature) cannot be downloaded programmatically without a browser. For these:

1. Open the article on x.com
2. Save the page as HTML (File → Save As → Webpage, Complete)
3. Run: `reed epub --html saved_page.html`

The tool will extract the title, author, date, and full article body from the saved HTML.
