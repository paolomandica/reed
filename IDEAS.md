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
