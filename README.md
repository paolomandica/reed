# article-to-kindle

Convert X.com (Twitter) articles and threads to Kindle-compatible EPUBs.

## Installation

```bash
uv sync
```

## Usage

### From a saved HTML file (always works)

Save the X.com article page from your browser (File → Save As → Webpage, Complete), then:

```bash
article-to-kindle --html article.html
```

This produces `article-title.epub` ready to send to your Kindle.

### From a URL (regular tweets and threads)

```bash
article-to-kindle https://x.com/username/status/123456789
```

Works for tweets and threads where the text content is in the tweet body. Uses the public FxTwitter API (no authentication required).

### Options

```
Usage: article-to-kindle [OPTIONS] [URL]

Options:
  -o, --output PATH    Output EPUB path (default: <title-slug>.epub)
  --html PATH          Use a local HTML file instead of downloading
  --verbose, -v        Show detailed progress
  --help               Show this message
```

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
3. Run: `article-to-kindle --html saved_page.html`

The tool will extract the title, author, date, and full article body from the saved HTML.
