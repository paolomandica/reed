# reed — Prompt: raw article text to audiobook-ready Markdown

<!--
How to use this template:
1. Copy this whole file (the prompt below this comment).
2. Replace the "PASTE SOURCE TEXT HERE" block at the bottom with the text you
   copied from the webpage, email, or wherever it lives.
3. Send it to any LLM. Its reply is a ready-to-use Markdown article.
4. Save the reply as a .md file, then run `reed audiobook --md article.md`
   (or paste it into the text box in `reed web`).
-->

You are converting raw article text into clean Markdown for reed, a tool that
turns articles into chaptered audiobooks with text-to-speech. The Markdown you
produce is read aloud by an American English TTS voice, and its structure
controls both the narration pacing and the audiobook chapter markers, so the
formatting rules below matter.

## Task

Rewrite the source text into a single, well-structured Markdown article. Keep
every piece of substantive content — facts, examples, names, numbers, dates,
and arguments — and preserve the author's tone where possible. Clean up the
writing and formatting, but do not summarize, truncate, or invent anything
that is not in the source.

## Required document structure

1. Start with the article title as an H1 heading:

   `# Exact Article Title`

2. Follow it with metadata when known — a byline, plus date and source.
   Either of these forms works:

   `*By Author Name (@handle) — January 5, 2026*`

   or plain lines:

   ```text
   Author: Author Name (@handle)
   Date: January 5, 2026
   Source: https://example.com/article
   ```

   If the author, date, or source is unknown, omit that field entirely.

3. Close the header with a horizontal rule:

   `---`

4. Write the body using only these Markdown elements:

   - `## Section Heading` — one per major section of the article. Each
     becomes a chapter in the audiobook, so mirror the sections the author
     used, or split the article into natural parts if it has none.
   - `### Subsection Heading` — only when a section genuinely needs
     sub-parts; use these sparingly.
   - Plain paragraphs — complete sentences, one idea per paragraph, with a
     blank line between paragraphs. Text before the first `##` becomes the
     audiobook's Introduction chapter.
   - `> Quote` — for verbatim or attributed quotes only; the audiobook
     announces them with "Quote:".
   - `- List item` — for enumerations, steps, or key points; the audiobook
     reads list items with a short pause between them.

## Formatting rules

- Remove page chrome: navigation, cookie banners, "share this" and subscribe
  prompts, ads, comments, related-article links, email signatures, headers,
  footers, and "view in browser" boilerplate.
- Drop tables, code blocks, inline code, footnotes, raw HTML, and URLs.
  Fold link text into the prose instead of keeping links — write "read the
  documentation", not "read [the docs](https://…)".
- Keep meaningful image captions and alt text as a short prose sentence.
- No YAML front matter, no `Title:`-style labels inside the body, and no
  second occurrence of the H1 title later in the document.
- Write for the ear, not the eye: spell out abbreviations TTS may mangle
  ("e.g." → "for example", "i.e." → "that is", "vs." → "versus", "&" →
  "and"), remove emoji and decorative symbols, keep headings short and free
  of trailing punctuation, and end sentences with clear terminal
  punctuation.
- If a fact is missing or unclear in the source, omit it; never guess.
- If the source text is not English, do not produce a document. Instead,
  reply with a single line explaining that the audiobook voice is American
  English only.

## Output

Return only the finished Markdown document — no code fences, no explanation,
no summary, nothing before or after it. The reply must be savable directly
as a `.md` file.

## Article source text

PASTE SOURCE TEXT HERE
