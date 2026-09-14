# reed — Prompt: article link to EPUB-ready Markdown (Verbatim)

<!--
How to use this template:
1. Copy this whole prompt.
2. Paste it into your AI chat (Gemini, ChatGPT, Claude, etc.).
3. The article link is appended at the end automatically.
4. Copy the Markdown the AI generates and paste it into the reed text box
   (or save it as a .md file and run `reed epub -i article.md`).
-->

You are converting an article at a given URL into clean Markdown for reed, a
tool that turns articles into EPUB ebooks. The Markdown you produce is rendered
as a readable document, so preserving the original text faithfully is the top
priority.

## Task

Read the article at the URL provided at the end of this prompt. Transcribe the
article into well-structured Markdown, preserving the original wording as
closely as possible. Do not rewrite, paraphrase, summarize, or rephrase the
author's text. Your job is to clean up page chrome and format the content as
Markdown — not to edit the prose.

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

   - `## Section Heading` — one per major section of the article. Mirror the
     sections the author used, or split the article into natural parts if it
     has none.
   - `### Subsection Heading` — only when a section genuinely needs
     sub-parts; use these sparingly.
   - Plain paragraphs — preserve the original paragraph breaks. Keep the
     author's sentences exactly as written.
   - `> Quote` — for verbatim or attributed quotes.
   - `- List item` — for enumerations, steps, or key points.
   - `[link text](url)` — keep meaningful hyperlinks as Markdown links.

## Formatting rules

- Remove page chrome: navigation, cookie banners, "share this" and subscribe
  prompts, ads, comments, related-article links, email signatures, headers,
  footers, and "view in browser" boilerplate.
- Drop tables, code blocks, inline code, footnotes, and raw HTML — unless the
  article's content is fundamentally about code, in which case keep code
  blocks as fenced Markdown.
- Keep meaningful image captions and alt text as a short prose sentence.
- No YAML front matter, no `Title:`-style labels inside the body, and no
  second occurrence of the H1 title later in the document.
- Preserve the author's original wording, spelling, and punctuation. Do not
  "improve" the writing — just format it as Markdown.
- If a fact is missing or unclear in the source, omit it; never guess.
- If the source text is not English, do not produce a document. Instead,
  reply with a single line explaining that the EPUB is English only.

## Output

Return only the finished Markdown document — no code fences, no explanation,
no summary, nothing before or after it. The reply must be savable directly
as a `.md` file.

## Article URL

