"""Kindle-optimized CSS for EPUB files.

Kindle-specific constraints:
- No flexbox or grid (breaks on some e-ink models)
- No position: absolute/fixed
- No background-image
- No overflow properties
- Simple serif fonts (let Kindle choose the rendering font)
- page-break-before for chapter headings
"""

KINDLE_CSS = """
body {
    font-family: serif;
    line-height: 1.6;
    margin: 0;
    padding: 0.5em;
    widows: 2;
    orphans: 2;
}

h1 {
    text-align: left;
    font-size: 1.8em;
    font-weight: bold;
    margin: 1.2em 0 0.6em 0;
    page-break-before: always;
    page-break-after: avoid;
}

h2 {
    text-align: left;
    font-size: 1.4em;
    font-weight: bold;
    margin: 1em 0 0.5em 0;
    page-break-after: avoid;
}

h3 {
    text-align: left;
    font-size: 1.2em;
    font-weight: bold;
    margin: 0.8em 0 0.4em 0;
    page-break-after: avoid;
}

h4, h5, h6 {
    text-align: left;
    font-weight: bold;
    margin: 0.6em 0 0.3em 0;
    page-break-after: avoid;
}

p {
    margin: 0 0 0.8em 0;
    text-indent: 0;
    text-align: justify;
}

blockquote {
    margin: 0.8em 1em;
    padding: 0.3em 0.8em;
    border-left: 3px solid #999;
    font-style: italic;
    color: #333;
}

ul, ol {
    margin: 0.5em 0 0.5em 1em;
    padding: 0;
}

li {
    margin: 0.2em 0;
}

img {
    max-width: 100%;
    height: auto;
    display: block;
    margin: 0.8em auto;
}

pre, code {
    font-family: monospace;
    font-size: 0.9em;
}

pre {
    margin: 0.8em 0;
    padding: 0.5em;
    white-space: pre-wrap;
    word-wrap: break-word;
}

hr {
    margin: 1.5em 0;
    border: none;
    text-align: center;
}

hr::after {
    content: "* * *";
    color: #666;
}

a {
    color: inherit;
    text-decoration: underline;
}

/* Title page */
.title-page {
    text-align: center;
    padding-top: 20%;
}

.title-page h1 {
    font-size: 2em;
    margin-bottom: 0.5em;
    page-break-before: avoid;
    text-align: center;
}

.title-page .author {
    font-size: 1.2em;
    color: #555;
    margin-bottom: 1em;
}

.title-page .date {
    font-size: 0.9em;
    color: #888;
}

.title-page .source {
    font-size: 0.8em;
    color: #aaa;
    margin-top: 3em;
}
""".strip()
