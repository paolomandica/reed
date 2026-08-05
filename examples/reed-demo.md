# How reed turns a saved article into an audiobook

*By Paolo Mandica*

Source: https://github.com/paolomandica/reed

---

## What reed does

Reed takes the long-form articles you save from the web and converts each
one into three useful formats: a Kindle-ready EPUB, clean Markdown, and an
MP3 audiobook narrated by a natural-sounding local voice.

The whole pipeline runs on your own machine. Your articles never leave your
computer, and no API keys are required. The narration comes from Kokoro-82M,
a small open-weight text-to-speech model that is downloaded once and cached
locally.

## Try it in under a minute

Save an article page from your browser, then run one command per format:

- Save the page as HTML with your browser's Save As option.
- Run reed epub to make a copy for your Kindle.
- Run reed audiobook to hear it read aloud.

Prefer plain text? Reed can also write clean Markdown without navigation,
ads, or other page chrome.

## Why the audio sounds natural

Reed splits the article into narration segments that respect its structure.
Titles and headings get a longer pause, paragraphs flow as continuous
speech, and quotes are introduced naturally. That keeps the audiobook easy
to follow even when you are not looking at the screen.

> Reading and listening are different rhythms. Reed tries to honor both.

That is the whole idea: one saved article, three ways to enjoy it later.
