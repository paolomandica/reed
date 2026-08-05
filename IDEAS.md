# Feature ideas

A running list of features to consider for reed. Newest ideas at the bottom of
each section. Move items to **Done** when shipped.

---

## Proposed

### Multi-article collections (anthology EPUB)
Let users queue several saved HTML articles and bundle them into a **single
EPUB** with each article as a chapter and a combined table of contents.

- **Why:** The natural reading unit for a Kindle is often "this week's saved
  articles," not one at a time. Turns reed from a converter into a reading
  workflow.
- **Fit:** The EPUB builder already generates per-heading TOCs; extending it to
  concatenate N parsed articles as top-level chapters is mostly a loop plus one
  metadata pass. The web dropzone already handles files — accept `multiple`.

### Read-later import (Pocket / Instapaper / Omnivore)
Import saved articles straight from read-it-later services instead of saving
HTML manually.

- **Why:** The save→convert flow is the product's core; removing the manual
  "Save As" step turns reed into a real reading workflow.
- **Fit:** Each service has a simple read API; download the article HTML once,
  store it locally (e.g. `articles/<service>/`), then run the existing
  extractors unchanged. Requires optional per-service API tokens.

### Private podcast feed
Serve generated audiobooks as a private RSS podcast so they appear in
Apple Podcasts / Overcast with chapters intact.

- **Why:** The natural way to "listen to my saved articles" is a podcast app;
  M4B chapters map to podcast chapters.
- **Fit:** `reed web` already serves files; add a feed endpoint listing
  finished audiobooks as enclosures (auth via a random token URL), and let
  `reed audiobook` output into the feed's folder.

### Batch / watch folder
Convert a whole folder of saved articles in one command — or watch it for new
files.

- **Why:** The natural unit is "this week's articles," not one file at a time;
  pairs naturally with the anthology EPUB.
- **Fit:** `reed batch <dir>` loops the existing extractors/encoders; a
  `--watch` mode can reuse the web task machinery. Output: one anthology
  EPUB/M4B plus per-article files.

### RSS/Substack subscriptions
`reed feed add <url>`: subscribe to feeds and auto-convert new posts.

- **Why:** Turns reed into a personal audiobook "newspaper" that fills itself.
- **Fit:** feedparser for Atom/RSS; store posts as Markdown and reuse the
  batch/anthology pipeline; a cron-friendly `reed feed pull` for scheduling.

### PDF input
Extract articles from PDFs in addition to HTML/Markdown.

- **Why:** Papers, reports, and newsletters arrive as PDFs, which reed
  currently can't read.
- **Fit:** `pypdf`/`pdfplumber` text extraction feeding the existing Markdown
  section parser; best-effort heading detection for chapters.

### Multi-voice narration
Narrate with multiple Kokoro voices: a narrator plus distinct voices for
quotes or sections.

- **Why:** The biggest perceived-quality jump per unit of effort — makes
  audiobooks feel produced instead of read by one bot.
- **Fit:** segment mapping already knows section types; add per-section voice
  overrides (e.g. blockquotes get a second voice) and a CLI/web voice map.

### Smart pacing controls
Per-section pause tuning, silence trimming, and rate/pitch knobs.

- **Why:** The "feel" dial audiobook listeners care about; pauses today are
  fixed multiples of one `silence_ms`.
- **Fit:** `narration_segments_for_tts` already computes per-section pauses;
  expose `--silence`, `--rate`, and pitch (rate exists via Kokoro speed; pitch
  needs post-processing or model support).

### Chapter previews
Listen to a single chapter before committing to a full render.

- **Why:** Removes the "generate 30 minutes to hear one section" barrier and
  makes voice/speed comparisons instant.
- **Fit:** `--max-chunks` is already a test shortcut; extend it to named
  chapters (`--chapters 2`) and a per-chapter ▶ button in the web UI, cached
  like voice previews.

### Send to Kindle / phone
Push the EPUB to Kindle via email, and grab the M4B on your phone via QR.

- **Why:** Removes the "get the file onto my device" step — the last mile of
  the workflow.
- **Fit:** `reed send --kindle` uses SMTP with a user-configured Kindle
  address; the web result card shows a QR pointing at the LAN download URL.

### Web audiobook player
A small in-browser player with chapter markers and lightly synced text.

- **Why:** Makes the web demo self-contained: generate, listen, jump chapters
  without leaving the page.
- **Fit:** chunk timings are already tracked; expose chapter boundaries via
  the API and render a simple `<audio>` player with a chapter list.

### PWA / mobile web
Make the web UI installable and usable from a phone.

- **Why:** Lets users point a phone at their desktop machine and convert from
  anywhere in the house.
- **Fit:** add a manifest + service worker to the existing static UI; download
  links already work over LAN.

---

## Done

### Voice preview in the web UI
Audition a short sample of each voice (at the selected pace) before committing
to a full audiobook synthesis.

- **Shipped:** `GET /api/preview?voice=&speed=` narrates a fixed sentence via
  `generate_voice_preview()` and caches the MP3 on disk
  (`~/.cache/reed/voice-previews/<voice>_<speed>.mp3`), reusing it on repeat
  requests. Each voice card has a ▶ button with loading/playing states.

### Chaptered M4B audiobooks (instead of flat MP3)
Encode audiobooks as **M4B with chapter markers** derived from the article's
section headings, so players show real chapters with seek/skip — turning the
output from a "voice memo" into a proper audiobook.

- **Shipped (0.3.0):** `--format m4b` on `audiobook` and `demo`, now the
  default output. Chapters come from headings (pre-heading content becomes
  "Introduction"); timings are computed from real chunk durations plus pauses;
  web audiobooks download as `.m4b` (`audio/mp4`).

### CLI progress bars during generation
Live progress for audiobook generation instead of a silent wait.

- **Shipped (0.3.0):** TTY-aware chunk bar showing a narration snippet, a
  determinate ffmpeg encode bar (MP3 and M4B), non-TTY fallback lines, and no
  bar spam in web-server logs (the web API keeps its task callback).

### One-command demo (`reed demo`)
A bundled sample article plus a single command that generates every format.

- **Shipped (0.3.0):** `examples/reed-demo.md` ships inside the wheel;
  `reed demo [--no-audiobook] [--format mp3|m4b]` generates EPUB → Markdown →
  audiobook into `reed-demo/`. The web UI got the same flow via the
  "✨ Generate the demo" button (`POST /api/demo`, three tasks).

### MPS (Metal GPU) acceleration on Apple Silicon
Use the Mac GPU for Kokoro synthesis when available.

- **Shipped (0.3.0):** auto-detect `torch.backends.mps.is_available()`
  (mps → cuda → cpu), pass the device to Kokoro, and fall back to CPU with a
  warning if MPS initialization fails.
