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

### Chaptered M4B audiobooks (instead of flat MP3)
Encode audiobooks as **M4B with chapter markers** derived from the article's
section headings, so players show real chapters with seek/skip — turning the
output from a "voice memo" into a proper audiobook.

- **Why:** reed already splits by section and inserts pauses, but that structure
  is thrown away in a flat MP3. Preserving it as chapters is what makes
  something feel like an audiobook.
- **Fit:** ffmpeg is already used for WAV→MP3; M4B plus a chapter-metadata file
  is the same tool with different args, and section boundaries are already
  tracked during chunking (`narration_segments_for_tts`).

---

## Done

### Voice preview in the web UI
Audition a short sample of each voice (at the selected pace) before committing
to a full audiobook synthesis.

- **Shipped:** `GET /api/preview?voice=&speed=` narrates a fixed sentence via
  `generate_voice_preview()` and caches the MP3 on disk
  (`~/.cache/reed/voice-previews/<voice>_<speed>.mp3`), reusing it on repeat
  requests. Each voice card has a ▶ button with loading/playing states.
