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
from collections.abc import Callable
from pathlib import Path

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

# American English voices for Kokoro (by quality grade)
_KOKORO_VOICES = [
    "af_heart",   # A  ❤️
    "af_bella",   # A- 🔥
    "af_nicole",  # B- 🎧
    "af_aoede",   # C+
    "af_kore",    # C+
    "af_sarah",   # C+
    "af_alloy",   # C
    "af_nova",    # C
    "af_sky",     # C-
    "af_jessica", # D
    "af_river",   # D
    "am_adam",    # F+
    "am_fenrir",  # C+
    "am_michael", # C+
    "am_puck",    # C+
    "am_echo",    # D
    "am_eric",    # D
    "am_liam",    # D
    "am_onyx",    # D
    "am_santa",   # D-
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


# ---------------------------------------------------------------------------
# Speech generation
# ---------------------------------------------------------------------------


def _generate_kokoro_speech(
    text: str,
    pipeline: object,
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


def article_text_for_tts(article: Article, max_chars: int) -> list[str]:
    """Build TTS text chunks from an article's sections.

    Each chunk respects *max_chars* and starts at a section boundary when
    possible (heading, paragraph, etc.).
    """
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0

    def _flush() -> None:
        nonlocal current, current_len
        if current:
            chunks.append(" ".join(current))
            current = []
            current_len = 0

    for section in article.sections:
        text = section.text.strip()
        if not text:
            continue

        if section.type in (SectionType.HEADING, SectionType.TITLE):
            prefix = text if text.endswith((".", "!", "?", "…")) else f"{text}."
        elif section.type == SectionType.BLOCKQUOTE:
            prefix = f"Quote: {text}"
        else:
            prefix = text

        if len(prefix) > max_chars:
            _flush()
            chunks.extend(_split_long_text(prefix, max_chars))
            continue

        if current and current_len + len(prefix) + 1 > max_chars:
            _flush()

        current.append(prefix)
        current_len += len(prefix) + (1 if current_len else 0)

    _flush()
    return chunks


def _split_long_text(text: str, max_chars: int) -> list[str]:
    """Split long text into sentence-aware chunks ≤ *max_chars*."""
    text = " ".join(text.split())
    if len(text) <= max_chars:
        return [text]

    raw_parts = [p.strip() for p in _SENTENCE_RE.split(text) if p and p.strip()]
    sentences: list[str] = []
    for part in raw_parts:
        if sentences and part in {".", "!", "?", "…"}:
            sentences[-1] = sentences[-1] + part
        else:
            sentences.append(part)

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
    audio_chunks: list[tuple[int, np.ndarray]],
    silence_ms: int = 500,
) -> tuple[int, np.ndarray]:
    if not audio_chunks:
        raise ValueError("No audio chunks to concatenate.")

    sr0, _ = audio_chunks[0]
    silence = _make_silence(silence_ms, sr0, np.float32)
    parts: list[np.ndarray] = []

    for i, (ch_sr, ch_audio) in enumerate(audio_chunks):
        if ch_sr != sr0:
            raise ValueError(f"Chunk {i} has different sample rate: {ch_sr} vs {sr0}")
        parts.append(_as_float32_mono(ch_audio))
        if i < len(audio_chunks) - 1 and silence_ms > 0:
            parts.append(silence)

    return sr0, np.concatenate(parts)


def _numpy_to_wav_bytes(sample_rate: int, audio: np.ndarray) -> bytes:
    buf = io.BytesIO()
    sf.write(buf, _as_float32_mono(audio), sample_rate, format="WAV")
    return buf.getvalue()


def _ffmpeg_metadata_value(value: str) -> str:
    return re.sub(r"[\x00-\x1f\x7f]", " ", value).strip()


def _wav_to_mp3(
    sample_rate: int,
    audio: np.ndarray,
    output_path: Path,
    title: str = "",
    artist: str = "",
    bitrate: str = "64k",
) -> None:
    wav_bytes = _numpy_to_wav_bytes(sample_rate, audio)

    cmd = [
        "ffmpeg",
        "-hide_banner",
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

    try:
        proc = subprocess.run(
            cmd,
            input=wav_bytes,
            capture_output=True,
            timeout=120,
        )
    except FileNotFoundError:
        raise RuntimeError(
            "ffmpeg is not installed or not on your PATH.\n"
            "Install it with:  brew install ffmpeg   (macOS)\n"
            "                  apt install ffmpeg     (Linux)\n"
            "                  winget install ffmpeg  (Windows)"
        ) from None
    except subprocess.TimeoutExpired:
        raise RuntimeError("ffmpeg timed out while encoding audio.") from None

    if proc.returncode != 0:
        stderr = proc.stderr.decode("utf-8", errors="replace")[-800:]
        raise RuntimeError(f"ffmpeg failed:\n{stderr}")


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
    speed: float = 1.0,
    progress_callback: Callable[[int, int, str], None] | None = None,
    cancel_check: Callable[[], bool] | None = None,
    max_chunks: int = 0,
) -> Path:
    """Convert an article to spoken audio and save as MP3.

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
        speed: Playback speed multiplier (default ``1.0``).  Applied
            natively during generation — no ffmpeg post-processing needed.
        progress_callback: Optional callback ``(current, total, message)``
            called after each TTS chunk completes.
        cancel_check: Optional callable that returns ``True`` when
            generation should be cancelled early.
        max_chunks: If > 0, only generate the first *max_chunks* chunks
            (useful for quick testing).  Default ``0`` = all chunks.

    Returns:
        Path to the generated MP3.
    """
    pipeline = _load_kokoro_pipeline()

    chunks = article_text_for_tts(article, max_chars)
    if not chunks:
        raise ValueError("Article has no text content to convert to speech.")

    if max_chunks > 0 and len(chunks) > max_chunks:
        click.echo(
            f"Test mode: limiting to first {max_chunks} of {len(chunks)} chunks."
        )
        chunks = chunks[:max_chunks]

    click.echo(
        f"\nGenerating audio for {len(chunks)} chunk(s) "
        f"using Kokoro-82M ({voice})..."
    )

    audio_chunks: list[tuple[int, np.ndarray]] = []
    failed = False

    with click.progressbar(
        length=len(chunks),
        label="Generating audio",
        show_pos=True,
    ) as bar:
        for i, chunk in enumerate(chunks, start=1):
            try:
                sr, audio = _generate_kokoro_speech(
                    chunk, pipeline, voice=voice, speed=speed
                )
            except Exception as exc:
                logger.exception("TTS failed on chunk %s/%s", i, len(chunks))
                click.echo(
                    f"\nError on chunk {i}/{len(chunks)}: {exc}",
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

            audio_chunks.append((sr, audio))
            bar.update(1)
            if progress_callback:
                progress_callback(i, len(chunks), "Generating audio…")
            if cancel_check and cancel_check():
                click.echo("\nGeneration cancelled by user.")
                raise RuntimeError("cancelled")
            click.echo(f"  Chunk {i}/{len(chunks)} done")

    if not audio_chunks:
        raise RuntimeError("No audio was generated. Check the errors above.")

    click.echo("\nConcatenating audio chunks...")
    combined_sr, combined_audio = _concat_audio(audio_chunks, silence_ms=silence_ms)

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    click.echo("Encoding to MP3...")
    title = getattr(article.metadata, "title", "") or ""
    artist = getattr(article.metadata, "author", "") or ""
    _wav_to_mp3(
        combined_sr,
        combined_audio,
        output_path,
        title=title,
        artist=artist,
        bitrate=mp3_bitrate,
    )

    status = "partial audiobook" if failed else "Audiobook"
    click.echo(f"\n✓ {status} generated: {output_path}")
    return output_path
