"""Generate audiobooks from articles using Kokoro-82M TTS.

Install::

    pip install kokoro soundfile click numpy

Kokoro requires the ``espeak-ng`` system package::

    brew install espeak-ng   # macOS
    apt install espeak-ng    # Linux

Audio I/O uses ``soundfile`` (WAV), ``numpy`` (concatenation), and system
``ffmpeg`` (MP3).  The model is downloaded from Hugging Face on first use
and cached by the hub thereafter.
"""

from __future__ import annotations

import io
import logging
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from queue import Empty, Queue
from typing import Any

import click
import numpy as np
import soundfile as sf

from ..models import Article, SectionType

logger = logging.getLogger(__name__)

os.environ.setdefault("TQDM_DISABLE", "1")

# ---------------------------------------------------------------------------
# Constants / model cache
# ---------------------------------------------------------------------------

_kokoro_pipeline: object | None = None  # KPipeline
_MAX_CHARS = 500
_SENTENCE_RE = re.compile(r"(?<=[.!?…])\s+|\n+")
_FFMPEG_TIMEOUT_SECONDS = 120

# Fixed sentence used for voice previews so the audio can be cached and
# reused across requests (same voice + speed → identical clip).
_PREVIEW_TEXT = (
    "Hello — this is a preview of how your audiobook will sound. "
    "Pick the voice you like best."
)

# American English voices for Kokoro, with Hugging Face quality grades.
# af_ = American female, am_ = American male.  Insertion order is quality order.
_KOKORO_VOICE_GRADES: dict[str, str] = {
    "af_heart":   "A",
    "af_bella":   "A-",
    "af_nicole":  "B-",
    "af_aoede":   "C+",
    "af_kore":    "C+",
    "af_sarah":   "C+",
    "af_alloy":   "C",
    "af_nova":    "C",
    "af_sky":     "C-",
    "af_jessica": "D",
    "af_river":   "D",
    "am_fenrir":  "C+",
    "am_michael": "C+",
    "am_puck":    "C+",
    "am_echo":    "D",
    "am_eric":    "D",
    "am_liam":    "D",
    "am_onyx":    "D",
    "am_santa":   "D-",
    "am_adam":    "F+",
}

# Flat list of voice IDs (used for validation and as the default ordering).
_KOKORO_VOICES = list(_KOKORO_VOICE_GRADES)


def kokoro_voice_catalog() -> list[dict[str, str]]:
    """Return structured metadata for every voice: id, grade, and gender."""
    return [
        {
            "id": voice_id,
            "grade": grade,
            "gender": "female" if voice_id.startswith("af_") else "male",
        }
        for voice_id, grade in _KOKORO_VOICE_GRADES.items()
    ]


# ---------------------------------------------------------------------------
# Model loading
# ---------------------------------------------------------------------------


def _load_kokoro_pipeline() -> object:
    """Load (or return cached) Kokoro TTS pipeline.

    Downloads ``hexgrad/Kokoro-82M`` from Hugging Face on first use.
    Uses American English (lang_code='a').

    On Apple Silicon, sets ``PYTORCH_ENABLE_MPS_FALLBACK=1`` so Kokoro's
    ops that don't have native MPS kernels fall back to CPU gracefully.
    """
    from kokoro import KPipeline

    global _kokoro_pipeline
    if _kokoro_pipeline is None:
        # Enable MPS fallback on Apple Silicon
        if hasattr(os, "uname") and os.uname().sysname == "Darwin":
            os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")
        click.echo("Loading Kokoro-82M pipeline (lang=en)...")
        _kokoro_pipeline = KPipeline(lang_code="a")
        click.echo("Kokoro pipeline loaded (sample rate=24000 Hz).")
    return _kokoro_pipeline


def _pipeline_device_label(pipeline: object) -> str:
    """Return the device used by a loaded Kokoro model."""
    model = getattr(pipeline, "model", None)
    if model is None:
        return "UNKNOWN"
    try:
        device = next(model.parameters()).device
    except (AttributeError, StopIteration):
        device = getattr(model, "device", None)

    return str(device).upper() if device is not None else "UNKNOWN"


# ---------------------------------------------------------------------------
# Speech generation
# ---------------------------------------------------------------------------


def _generate_kokoro_speech(
    text: str,
    pipeline: Any,
    *,
    voice: str = "af_heart",
    speed: float = 1.0,
) -> tuple[int, np.ndarray]:
    """Generate speech for *text* using Kokoro.

    Returns:
        ``(sample_rate, audio)`` with audio shape ``(T,)`` at 24000 Hz.
    """
    generator = pipeline(text, voice=voice, speed=speed, split_pattern=r"\n+")
    segments: list[np.ndarray] = []
    for _gs, _ps, audio in generator:
        segments.append(np.asarray(audio, dtype=np.float32))

    if not segments:
        raise RuntimeError("Kokoro produced no audio for the given text.")

    return 24000, np.concatenate(segments)


# ---------------------------------------------------------------------------
# Article → text chunks
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class NarrationSegment:
    """A TTS input chunk and the pause to append after it."""

    text: str
    pause_after_ms: int
    chapter_title: str = ""


def _with_terminal_punctuation(text: str) -> str:
    text = text.strip()
    return text if text.endswith((".", "!", "?", "…")) else f"{text}."


def _is_duplicate_title(text: str, title: str) -> bool:
    return text.casefold().rstrip(".!?…") == title.casefold().rstrip(".!?…")


def narration_segments_for_tts(
    article: Article,
    max_chars: int,
    *,
    silence_ms: int = 500,
) -> list[NarrationSegment]:
    """Build boundary-aware TTS segments from article metadata and content.

    Logical article units are never merged. Oversized units are split at
    sentence or word boundaries, but only their final part receives the
    unit's pause so a long paragraph remains continuous.
    """
    if max_chars <= 0:
        raise ValueError("max_chars must be greater than zero")

    normal_pause = max(0, silence_ms)
    long_pause = normal_pause * 2
    short_pause = normal_pause // 2
    units: list[tuple[str, int, str]] = []
    chapter_title = ""

    title = article.metadata.title.strip()
    if title:
        units.append((_with_terminal_punctuation(title), long_pause, chapter_title))

    author = article.metadata.author.strip()
    if author and author.casefold() != "unknown":
        units.append(
            (_with_terminal_punctuation(f"By {author}"), long_pause, chapter_title)
        )

    for section in article.sections:
        text = section.text.strip()
        if not text:
            continue
        if (
            title
            and section.type in (SectionType.TITLE, SectionType.HEADING)
            and _is_duplicate_title(text, title)
        ):
            continue

        if section.type in (SectionType.HEADING, SectionType.TITLE):
            chapter_title = text
            units.append((_with_terminal_punctuation(text), long_pause, chapter_title))
        elif section.type == SectionType.BLOCKQUOTE:
            units.append((f"Quote: {text}", normal_pause, chapter_title))
        elif section.type == SectionType.LIST_ITEM:
            units.append((text, short_pause, chapter_title))
        else:
            units.append((text, normal_pause, chapter_title))

    segments: list[NarrationSegment] = []
    for text, pause_after_ms, chapter in units:
        chunks = _split_long_text(text, max_chars)
        for index, chunk in enumerate(chunks):
            segments.append(
                NarrationSegment(
                    text=chunk,
                    pause_after_ms=pause_after_ms if index == len(chunks) - 1 else 0,
                    chapter_title=chapter,
                )
            )
    return segments


def article_text_for_tts(article: Article, max_chars: int) -> list[str]:
    """Return the narration text chunks for callers that do not need pacing."""
    return [segment.text for segment in narration_segments_for_tts(article, max_chars)]


def _split_long_text(text: str, max_chars: int) -> list[str]:
    """Split long text into sentence-aware chunks no longer than *max_chars*."""
    if max_chars <= 0:
        raise ValueError("max_chars must be greater than zero")

    text = " ".join(text.split())
    if len(text) <= max_chars:
        return [text] if text else []

    sentences = [part.strip() for part in _SENTENCE_RE.split(text) if part.strip()]
    if not sentences:
        return _split_by_words(text, max_chars)

    chunks: list[str] = []
    current: list[str] = []
    current_len = 0

    for sentence in sentences:
        if len(sentence) > max_chars:
            if current:
                chunks.append(" ".join(current))
                current, current_len = [], 0
            chunks.extend(_split_by_words(sentence, max_chars))
            continue

        extra = len(sentence) + (1 if current else 0)
        if current and current_len + extra > max_chars:
            chunks.append(" ".join(current))
            current, current_len = [], 0
            extra = len(sentence)

        current.append(sentence)
        current_len += extra

    if current:
        chunks.append(" ".join(current))
    return chunks


def _split_by_words(text: str, max_chars: int) -> list[str]:
    """Last-resort word wrap so no chunk exceeds *max_chars*."""
    if max_chars <= 0:
        raise ValueError("max_chars must be greater than zero")

    words = text.split()
    if not words:
        return []

    chunks: list[str] = []
    current: list[str] = []
    current_len = 0

    for word in words:
        if len(word) > max_chars:
            if current:
                chunks.append(" ".join(current))
                current, current_len = [], 0
            for i in range(0, len(word), max_chars):
                chunks.append(word[i : i + max_chars])
            continue

        extra = len(word) + (1 if current else 0)
        if current and current_len + extra > max_chars:
            chunks.append(" ".join(current))
            current, current_len = [], 0
            extra = len(word)

        current.append(word)
        current_len += extra

    if current:
        chunks.append(" ".join(current))
    return chunks

# ---------------------------------------------------------------------------
# Audio helpers (numpy + ffmpeg)
# ---------------------------------------------------------------------------


def _make_silence(
    duration_ms: int,
    sample_rate: int,
    dtype: np.dtype = np.dtype(np.float32),
) -> np.ndarray:
    nframes = max(0, int(sample_rate * duration_ms / 1000))
    return np.zeros(nframes, dtype=dtype)


def _as_float32_mono(audio: np.ndarray) -> np.ndarray:
    x = np.asarray(audio)
    if x.ndim > 1:
        x = x.mean(axis=-1)
    if np.issubdtype(x.dtype, np.integer):
        max_abs = float(np.iinfo(x.dtype).max) or 1.0
        x = x.astype(np.float32) / max_abs
    else:
        x = x.astype(np.float32, copy=False)
    return x


def _concat_audio(
    audio_chunks: Sequence[tuple[int, np.ndarray] | tuple[int, np.ndarray, int]],
    silence_ms: int = 500,
) -> tuple[int, np.ndarray]:
    """Concatenate audio, honoring an optional pause on each chunk."""
    if not audio_chunks:
        raise ValueError("No audio chunks to concatenate.")

    sr0 = audio_chunks[0][0]
    parts: list[np.ndarray] = []

    for index, chunk in enumerate(audio_chunks):
        ch_sr, ch_audio = chunk[0], chunk[1]
        if ch_sr != sr0:
            raise ValueError(f"Chunk {index} has different sample rate: {ch_sr} vs {sr0}")
        parts.append(_as_float32_mono(ch_audio))

        if index == len(audio_chunks) - 1:
            continue
        pause_ms = chunk[2] if len(chunk) == 3 else silence_ms
        if pause_ms > 0:
            parts.append(_make_silence(pause_ms, sr0, np.dtype(np.float32)))

    return sr0, np.concatenate(parts)


def _numpy_to_wav_bytes(sample_rate: int, audio: np.ndarray) -> bytes:
    buf = io.BytesIO()
    sf.write(buf, _as_float32_mono(audio), sample_rate, format="WAV")
    return buf.getvalue()


def _ffmpeg_metadata_value(value: str) -> str:
    return re.sub(r"[\x00-\x1f\x7f]", " ", value).strip()


def _stdout_is_tty() -> bool:
    """Return True when stdout is attached to an interactive terminal."""
    return sys.stdout.isatty()


def _parse_ffmpeg_progress_ms(line: str) -> int | None:
    """Return encoded milliseconds from an ffmpeg ``-progress`` line.

    ffmpeg emits ``out_time_ms=...`` (and ``out_time_us=...``) once per
    encoded block; all other lines in the stream are ignored.
    """
    key, sep, value = line.strip().partition("=")
    if not sep:
        return None
    if key == "out_time_ms":
        try:
            return max(0, int(value))
        except ValueError:
            return None
    if key == "out_time_us":
        try:
            return max(0, int(value) // 1000)
        except ValueError:
            return None
    return None


def _wav_to_mp3(
    sample_rate: int,
    audio: np.ndarray,
    output_path: Path,
    title: str = "",
    artist: str = "",
    bitrate: str = "64k",
    progress_callback: Callable[[int, int, str], None] | None = None,
) -> None:
    """Encode *audio* as MP3 with ffmpeg, optionally reporting progress."""
    wav_bytes = _numpy_to_wav_bytes(sample_rate, audio)
    total_ms = int(len(audio) / sample_rate * 1000)

    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-nostats",
        "-progress",
        "pipe:1",
        "-loglevel",
        "error",
        "-y",
        "-f",
        "wav",
        "-i",
        "pipe:0",
        "-codec:a",
        "libmp3lame",
        "-b:a",
        bitrate,
    ]
    if title:
        cmd += ["-metadata", f"title={_ffmpeg_metadata_value(title)}"]
    if artist:
        cmd += ["-metadata", f"artist={_ffmpeg_metadata_value(artist)}"]
    cmd += ["-metadata", "album=reed", str(output_path)]

    _run_ffmpeg(
        cmd,
        wav_bytes,
        total_ms,
        progress_callback,
        progress_message="Encoding MP3\u2026",
    )


def _run_ffmpeg(
    cmd: list[str],
    wav_bytes: bytes,
    total_ms: int,
    progress_callback: Callable[[int, int, str], None] | None,
    *,
    progress_message: str = "Encoding MP3\u2026",
) -> None:
    """Run ffmpeg with piped WAV input, streaming ``-progress`` updates.

    Raises a user-friendly :class:`RuntimeError` when ffmpeg is missing,
    times out, or exits non-zero.
    """
    try:
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except FileNotFoundError:
        raise RuntimeError(
            "ffmpeg is not installed or not on your PATH.\n"
            "Install it with:  brew install ffmpeg   (macOS)\n"
            "                  apt install ffmpeg     (Linux)\n"
            "                  winget install ffmpeg  (Windows)"
        ) from None

    def _feed() -> None:
        try:
            if proc.stdin is not None:
                proc.stdin.write(wav_bytes)
        except (BrokenPipeError, OSError):
            pass
        finally:
            if proc.stdin is not None:
                proc.stdin.close()

    progress: Queue[bytes | None] = Queue()

    def _read() -> None:
        if proc.stdout is None:
            progress.put(None)
            return
        for raw in proc.stdout:
            progress.put(raw)
        progress.put(None)

    feeder = threading.Thread(target=_feed, daemon=True)
    reader = threading.Thread(target=_read, daemon=True)
    feeder.start()
    reader.start()

    last_ms = -1
    try:
        deadline = time.monotonic() + _FFMPEG_TIMEOUT_SECONDS
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(cmd, _FFMPEG_TIMEOUT_SECONDS)
            try:
                raw = progress.get(timeout=remaining)
            except Empty:
                continue
            if raw is None:
                break
            ms = _parse_ffmpeg_progress_ms(raw.decode("utf-8", errors="replace"))
            if ms is not None and ms != last_ms and progress_callback:
                progress_callback(min(ms, total_ms), total_ms, progress_message)
                last_ms = ms
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
        raise RuntimeError("ffmpeg timed out while encoding audio.") from None
    finally:
        reader.join(timeout=1)
        feeder.join(timeout=1)

    stderr = proc.stderr.read() if proc.stderr else b""
    returncode = proc.wait()
    if returncode != 0:
        raise RuntimeError(
            "ffmpeg failed:\n" + stderr.decode("utf-8", errors="replace")[-800:]
        )


def _escape_ffmetadata(value: str) -> str:
    """Escape a value for ffmpeg's ffmetadata format."""
    value = _ffmpeg_metadata_value(value)
    return (
        value.replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace("#", "\\#")
        .replace("=", "\\=")
    )


def _ffmetadata_text(
    title: str,
    artist: str,
    chapters: Sequence[tuple[str, int, int]],
) -> str:
    """Build an ffmpeg ffmetadata document with chapter markers."""
    lines = [";FFMETADATA1"]
    if title:
        lines.append(f"title={_escape_ffmetadata(title)}")
    if artist:
        lines.append(f"artist={_escape_ffmetadata(artist)}")
    lines.append("album=reed")
    for chapter_title, start_ms, end_ms in chapters:
        lines.append("[CHAPTER]")
        lines.append("TIMEBASE=1/1000")
        lines.append(f"START={max(0, int(start_ms))}")
        lines.append(f"END={max(0, int(end_ms))}")
        lines.append(f"title={_escape_ffmetadata(chapter_title)}")
    return "\n".join(lines) + "\n"


def _wav_to_m4b(
    sample_rate: int,
    audio: np.ndarray,
    output_path: Path,
    title: str = "",
    artist: str = "",
    chapters: Sequence[tuple[str, int, int]] | None = None,
    bitrate: str = "64k",
    progress_callback: Callable[[int, int, str], None] | None = None,
) -> None:
    """Encode *audio* as a chaptered M4B audiobook with ffmpeg.

    Chapter markers are supplied as an ffmetadata input and mapped onto the
    MP4 container, so players such as Apple Books and VLC show real chapters
    with seek and skip.
    """
    wav_bytes = _numpy_to_wav_bytes(sample_rate, audio)
    total_ms = int(len(audio) / sample_rate * 1000)

    fd, metadata_path_str = tempfile.mkstemp(prefix="reed-chapters-", suffix=".txt")
    metadata_path = Path(metadata_path_str)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(_ffmetadata_text(title, artist, chapters or []))

        cmd = [
            "ffmpeg",
            "-hide_banner",
            "-nostats",
            "-progress",
            "pipe:1",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "wav",
            "-i",
            "pipe:0",
            "-f",
            "ffmetadata",
            "-i",
            str(metadata_path),
            "-map_metadata",
            "1",
            "-codec:a",
            "aac",
            "-b:a",
            bitrate,
            "-movflags",
            "+faststart",
            str(output_path),
        ]
        _run_ffmpeg(
            cmd,
            wav_bytes,
            total_ms,
            progress_callback,
            progress_message="Encoding M4B\u2026",
        )
    finally:
        metadata_path.unlink(missing_ok=True)


def _build_chapter_ranges(
    segments: Sequence[NarrationSegment],
    durations_ms: Sequence[int],
    fallback_title: str,
) -> list[tuple[str, int, int]]:
    """Map narration segments to chapter ranges ``(title, start_ms, end_ms)``.

    A new chapter starts at every segment whose :attr:`chapter_title` differs
    from the current one; *fallback_title* names pre-heading content. Timings
    follow the final concatenated audio: each segment's spoken duration plus
    its trailing pause (skipped for the last segment), matching
    :func:`_concat_audio`.
    """
    if len(segments) != len(durations_ms):
        raise ValueError("segments and durations_ms must be the same length")
    if not segments:
        return []

    chapters: list[tuple[str, int, int]] = []
    current_title: str | None = None
    position_ms = 0
    last_index = len(segments) - 1

    for index, (segment, duration_ms) in enumerate(zip(segments, durations_ms)):
        chapter = segment.chapter_title or fallback_title
        if chapter != current_title:
            if chapters and position_ms > chapters[-1][1]:
                prev_title, prev_start, _ = chapters[-1]
                chapters[-1] = (prev_title, prev_start, position_ms)
                chapters.append((chapter, position_ms, position_ms))
            elif chapters:
                # Back-to-back boundary with no content in between: retitle the
                # open range instead of emitting a zero-length chapter.
                _, prev_start, _ = chapters[-1]
                chapters[-1] = (chapter, prev_start, prev_start)
            else:
                chapters.append((chapter, position_ms, position_ms))
            current_title = chapter
        position_ms += max(0, duration_ms)
        if index < last_index:
            position_ms += max(0, segment.pause_after_ms)

    title, start, _ = chapters[-1]
    chapters[-1] = (title, start, position_ms)
    return chapters


# ---------------------------------------------------------------------------
# Voice preview (short, cached sample clip)
# ---------------------------------------------------------------------------


def _preview_cache_dir() -> Path:
    """Return the on-disk cache directory for voice-preview clips.

    Honors ``XDG_CACHE_HOME``; falls back to ``~/.cache``.  Created on demand.
    """
    base = os.environ.get("XDG_CACHE_HOME") or (Path.home() / ".cache")
    cache_dir = Path(base) / "reed" / "voice-previews"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir


def generate_voice_preview(
    voice: str = "af_heart",
    *,
    speed: float = 1.0,
    cache_dir: Path | None = None,
) -> Path:
    """Synthesize a short sample clip for *voice* at *speed*, cached on disk.

    A fixed sentence (:data:`_PREVIEW_TEXT`) is narrated so the result is
    deterministic and can be reused: the clip is written once to the preview
    cache and returned directly on subsequent calls with the same
    ``(voice, speed)`` pair — no re-generation.

    Args:
        voice: Kokoro voice pack name (see :data:`_KOKORO_VOICES`).
        speed: Playback speed multiplier (applied natively during synthesis).
        cache_dir: Override the cache directory (defaults to
            :func:`_preview_cache_dir`).

    Returns:
        Path to the cached MP3 clip.
    """
    if cache_dir is None:
        cache_dir = _preview_cache_dir()
    else:
        cache_dir = Path(cache_dir)
        cache_dir.mkdir(parents=True, exist_ok=True)

    cache_path = cache_dir / f"{voice}_{speed:.2f}.mp3"
    if cache_path.exists() and cache_path.stat().st_size > 0:
        logger.debug("Voice preview cache hit: %s", cache_path.name)
        return cache_path

    logger.debug("Voice preview cache miss, synthesizing: %s", cache_path.name)
    pipeline = _load_kokoro_pipeline()
    sample_rate, audio = _generate_kokoro_speech(
        _PREVIEW_TEXT, pipeline, voice=voice, speed=speed
    )

    # Write to a temp file first, then atomically move into place so a
    # concurrent reader never sees a half-written clip.
    tmp_path = cache_path.with_suffix(f".{os.getpid()}.tmp.mp3")
    try:
        _wav_to_mp3(
            sample_rate, audio, tmp_path, title="reed voice preview", artist=voice
        )
        tmp_path.replace(cache_path)
    finally:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass
    return cache_path


def _narration_snippet(segment: NarrationSegment | None) -> str:
    """Return a short single-line label for a narration segment."""
    if segment is None:
        return ""
    text = segment.text.strip()
    snippet = text if len(text) <= 40 else text[:40].rstrip() + "\u2026"
    return f" {snippet}"


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------


def generate_audiobook(
    article: Article,
    output_path: Path,
    *,
    voice: str = "af_heart",
    max_chars: int = _MAX_CHARS,
    silence_ms: int = 500,
    mp3_bitrate: str = "64k",
    output_format: str = "mp3",
    speed: float = 1.0,
    progress_callback: Callable[[int, int, str], None] | None = None,
    cancel_check: Callable[[], bool] | None = None,
    max_chunks: int = 0,
) -> Path:
    """Convert an article to spoken audio and save as MP3 or chaptered M4B.

    Uses ``hexgrad/Kokoro-82M`` (82M params, 20 American English voices,
    Apache-2.0 licensed).  Requires the ``espeak-ng`` system package.

    Args:
        article: Structured article with content sections.
        output_path: Destination MP3 path.
        voice: Kokoro voice pack name (default ``"af_heart"``).
            See ``_KOKORO_VOICES`` for the full list.
        max_chars: Max characters per TTS chunk (default 500).
        silence_ms: Silence inserted between chunks.
        mp3_bitrate: LAME bitrate string, e.g. ``64k``.
        output_format: Audio container, ``"mp3"`` (default) or ``"m4b"``
            (chaptered from article headings).
        speed: Playback speed multiplier (default ``1.0``).  Applied
            natively during generation — no ffmpeg post-processing needed.
        progress_callback: Optional callback ``(current, total, message)``
            called after each TTS chunk and during MP3 encoding. When set,
            interactive CLI progress output (bar and per-chunk lines) is
            suppressed; the callback is the only progress channel (used by
            the web server).
        cancel_check: Optional callable that returns ``True`` when
            generation should be cancelled early.
        max_chunks: If > 0, only generate the first *max_chunks* chunks
            (useful for quick testing).  Default ``0`` = all chunks.

    Returns:
        Path to the generated audio file.
    """
    if output_format not in ("mp3", "m4b"):
        raise ValueError("output_format must be 'mp3' or 'm4b'")

    pipeline = _load_kokoro_pipeline()
    click.echo(f"Kokoro model device: {_pipeline_device_label(pipeline)}")

    segments = narration_segments_for_tts(
        article, max_chars, silence_ms=silence_ms
    )
    if not segments:
        raise ValueError("Article has no text content to convert to speech.")

    if max_chunks > 0 and len(segments) > max_chunks:
        click.echo(
            f"Test mode: limiting to first {max_chunks} of {len(segments)} chunks."
        )
        segments = segments[:max_chunks]

    click.echo(
        f"\nGenerating audio for {len(segments)} chunk(s) "
        f"using Kokoro-82M ({voice})..."
    )

    audio_chunks: list[tuple[int, np.ndarray, int]] = []
    chunk_durations_ms: list[int] = []
    failed = False
    show_bar = progress_callback is None

    with click.progressbar(
        length=len(segments),
        label="Generating audio",
        show_pos=True,
        hidden=not show_bar,
        item_show_func=_narration_snippet,
    ) as bar:
        for i, segment in enumerate(segments, start=1):
            try:
                sr, audio = _generate_kokoro_speech(
                    segment.text, pipeline, voice=voice, speed=speed
                )
            except Exception as exc:
                logger.exception("TTS failed on chunk %s/%s", i, len(segments))
                click.echo(
                    f"\nError on chunk {i}/{len(segments)}: {exc}",
                    err=True,
                )
                if i == 1:
                    raise RuntimeError(f"Speech generation failed: {exc}") from exc
                click.echo(
                    "Stopping early; writing partial audiobook.",
                    err=True,
                )
                failed = True
                break

            audio_chunks.append((sr, audio, segment.pause_after_ms))
            chunk_durations_ms.append(int(len(audio) / sr * 1000))
            bar.update(1, current_item=segment)
            if progress_callback:
                progress_callback(i, len(segments), "Generating audio…")
            if cancel_check and cancel_check():
                click.echo("\nGeneration cancelled by user.")
                raise RuntimeError("cancelled")
            if show_bar and not _stdout_is_tty():
                click.echo(f"  Chunk {i}/{len(segments)} done")

    if not audio_chunks:
        raise RuntimeError("No audio was generated. Check the errors above.")

    click.echo("\nConcatenating audio chunks...")
    combined_sr, combined_audio = _concat_audio(audio_chunks, silence_ms=silence_ms)

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    title = getattr(article.metadata, "title", "") or ""
    artist = getattr(article.metadata, "author", "") or ""

    def _encode(encode_progress: Callable[[int, int, str], None]) -> None:
        if output_format == "m4b":
            has_headings = any(segment.chapter_title for segment in segments)
            fallback = "Introduction" if has_headings else (title or "Chapter 1")
            chapters = _build_chapter_ranges(segments, chunk_durations_ms, fallback)
            _wav_to_m4b(
                combined_sr,
                combined_audio,
                output_path,
                title=title,
                artist=artist,
                chapters=chapters,
                bitrate=mp3_bitrate,
                progress_callback=encode_progress,
            )
        else:
            _wav_to_mp3(
                combined_sr,
                combined_audio,
                output_path,
                title=title,
                artist=artist,
                bitrate=mp3_bitrate,
                progress_callback=encode_progress,
            )

    if progress_callback is not None:
        _encode(progress_callback)
    else:
        total_ms = max(1, int(len(combined_audio) / combined_sr * 1000))
        with click.progressbar(
            length=total_ms,
            label="Encoding M4B" if output_format == "m4b" else "Encoding MP3",
            show_pos=True,
        ) as encode_bar:
            last_ms = 0

            def _report_encode(current: int, total: int, message: str) -> None:
                nonlocal last_ms
                encode_bar.update(max(0, current - last_ms))
                last_ms = current

            _encode(_report_encode)

    status = "partial audiobook" if failed else "Audiobook"
    click.echo(f"\n✓ {status} generated: {output_path}")
    return output_path
