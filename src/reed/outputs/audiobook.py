"""Generate audiobooks from articles using Chatterbox or Kokoro TTS.

Install::

    pip install chatterbox-tts kokoro soundfile click numpy

Kokoro additionally requires the ``espeak-ng`` system package::

    brew install espeak-ng   # macOS
    apt install espeak-ng    # Linux

Audio I/O uses ``soundfile`` (WAV), ``numpy`` (concatenation), and system
``ffmpeg`` (MP3).  Models are downloaded from Hugging Face on first use
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
import torch

from ..models import Article, SectionType

logger = logging.getLogger(__name__)

os.environ.setdefault("TQDM_DISABLE", "1")

# ---------------------------------------------------------------------------
# Constants / model caches
# ---------------------------------------------------------------------------

_chatterbox_model: object | None = None  # ChatterboxTTS
_kokoro_pipeline: object | None = None  # KPipeline

_MAX_CHARS_CHATTERBOX = 400
_MAX_CHARS_KOKORO = 500

_SENTENCE_RE = re.compile(r"(?<=[.!?…])\s+|\n+")

# Top American English voices for Kokoro (by quality grade)
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
# Device resolution
# ---------------------------------------------------------------------------


def _resolve_device(device: str) -> str:
    """Map a user-facing device string to what each backend expects."""
    d = device.lower().strip()
    if d in {"cpu", "mps"}:
        return d
    if d in {"cuda", "gpu"}:
        return "cuda"
    if d.startswith("cuda:"):
        if d != "cuda:0":
            logger.warning(
                "For multi-GPU pin set CUDA_VISIBLE_DEVICES (requested %s).",
                d,
            )
        return "cuda"
    raise ValueError(f"Unsupported device {device!r}. Use cpu, cuda, or mps.")


# ---------------------------------------------------------------------------
# Chatterbox backend
# ---------------------------------------------------------------------------


def _load_chatterbox_model(device: str = "mps"):
    """Load (or return cached) Chatterbox TTS model.

    Weights are downloaded from Hugging Face ``ResembleAI/chatterbox`` on
    first load and cached by the hub thereafter.
    """
    from chatterbox.tts import ChatterboxTTS

    global _chatterbox_model
    if _chatterbox_model is None:
        device_str = _resolve_device(device)
        # Fall back to CPU if requested device is MPS but unavailable
        if (
            device_str == "mps"
            and not getattr(torch.backends.mps, "is_available", lambda: False)()
        ):
            click.echo("MPS not available — falling back to CPU.")
            device_str = "cpu"
        click.echo(f"Loading Chatterbox TTS on {device_str}...")
        _chatterbox_model = ChatterboxTTS.from_pretrained(device=device_str)
        click.echo(f"Model loaded (sample rate={_chatterbox_model.sr} Hz).")
    return _chatterbox_model


def _prepare_chatterbox_voice(
    model: object,
    *,
    exaggeration: float = 0.5,
) -> None:
    """Attach the built-in default voice conditionals to *model*.

    Uses the checkpoint's shipped default voice (``conds.pt``).
    No reference audio or voice cloning is used.
    """
    from chatterbox.tts import T3Cond

    if model.conds is None:
        raise ValueError(
            "This checkpoint has no built-in default voice. "
            "Try a different Chatterbox checkpoint."
        )
    click.echo("Using built-in default Chatterbox voice.")
    # Update exaggeration on the existing conditionals if needed
    if hasattr(model.conds, "t3") and exaggeration != 0.5:
        _cond = model.conds.t3
        model.conds.t3 = T3Cond(
            speaker_emb=_cond.speaker_emb,
            cond_prompt_speech_tokens=_cond.cond_prompt_speech_tokens,
            emotion_adv=exaggeration * torch.ones(1, 1, 1),
        ).to(device=model.device)


# ---------------------------------------------------------------------------
# Kokoro backend
# ---------------------------------------------------------------------------


def _load_kokoro_pipeline(device: str = "mps") -> object:
    """Load (or return cached) Kokoro TTS pipeline.

    Downloads ``hexgrad/Kokoro-82M`` from Hugging Face on first use.
    Uses American English (lang_code='a').

    On Apple Silicon, sets ``PYTORCH_ENABLE_MPS_FALLBACK=1`` so Kokoro's
    ops that don't have native MPS kernels fall back to CPU gracefully.
    """
    from kokoro import KPipeline

    global _kokoro_pipeline
    if _kokoro_pipeline is None:
        device_str = _resolve_device(device)
        if device_str == "mps":
            os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")
        elif device_str == "cuda":
            click.echo("Kokoro runs on GPU via PyTorch CUDA.")
        click.echo(f"Loading Kokoro-82M pipeline (lang=en, device={device_str})...")
        _kokoro_pipeline = KPipeline(lang_code="a")
        click.echo("Kokoro pipeline loaded (sample rate=24000 Hz).")
    return _kokoro_pipeline


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
# Speech generation helpers
# ---------------------------------------------------------------------------


def _tensor_to_numpy(wav: torch.Tensor | np.ndarray) -> np.ndarray:
    """Convert tensor output to mono float32 numpy."""
    if isinstance(wav, torch.Tensor):
        wav = wav.detach().float().cpu().numpy()
    x = np.asarray(wav, dtype=np.float32)
    if x.ndim > 1:
        # Chatterbox returns shape (1, T)
        x = np.squeeze(x, axis=0) if x.shape[0] == 1 else x.mean(axis=0)
    return x.reshape(-1)


def _generate_chatterbox_speech(
    text: str,
    model: object,
    *,
    exaggeration: float = 0.5,
    cfg_weight: float = 0.5,
    temperature: float = 0.8,
    repetition_penalty: float = 1.2,
) -> tuple[int, np.ndarray]:
    """Generate speech for *text* using conditionals already on *model*.

    Returns:
        ``(sample_rate, audio)`` with audio shape ``(T,)``.
    """
    if model.conds is None:
        raise RuntimeError(
            "Voice conditionals are not set. Call _prepare_chatterbox_voice first."
        )

    wav = model.generate(
        text,
        exaggeration=exaggeration,
        cfg_weight=cfg_weight,
        temperature=temperature,
        repetition_penalty=repetition_penalty,
    )
    return int(model.sr), _tensor_to_numpy(wav)


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


def _build_atempo_filters(speed: float) -> str:
    """Build a comma-separated chain of ffmpeg ``atempo`` filters.

    Each ``atempo`` accepts values in [0.5, 2.0]; for values outside
    that range we chain multiple instances.
    """
    remaining = speed
    filters: list[str] = []
    while remaining < 0.5:
        filters.append("atempo=0.5")
        remaining /= 0.5
    while remaining > 2.0:
        filters.append("atempo=2.0")
        remaining /= 2.0
    filters.append(f"atempo={remaining:.3f}")
    return ",".join(filters)


def _time_stretch(
    sample_rate: int,
    audio: np.ndarray,
    speed: float = 1.0,
) -> np.ndarray:
    """Time-stretch *audio* via ffmpeg's ``atempo`` filter.

    Pipes WAV bytes through ffmpeg with the appropriate ``atempo``
    chain and reads the result back into a numpy array.  The sample
    rate is preserved.

    Args:
        sample_rate: Sample rate of the input audio.
        audio: Mono float32 numpy array.
        speed: Playback speed multiplier (1.0 = no change,
               < 1.0 = slower, > 1.0 = faster).

    Returns:
        Time-stretched mono float32 numpy array at the same sample rate.
    """
    if speed == 1.0:
        return audio

    wav_bytes = _numpy_to_wav_bytes(sample_rate, audio)
    atempo = _build_atempo_filters(speed)

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
        "-filter:a",
        atempo,
        "-f",
        "wav",
        "pipe:1",
    ]

    try:
        proc = subprocess.run(
            cmd,
            input=wav_bytes,
            capture_output=True,
            timeout=120,
        )
    except FileNotFoundError:
        raise RuntimeError("ffmpeg is not installed or not on your PATH.") from None
    except subprocess.TimeoutExpired:
        raise RuntimeError("ffmpeg timed out during time-stretching.") from None

    if proc.returncode != 0:
        stderr = proc.stderr.decode("utf-8", errors="replace")[-800:]
        raise RuntimeError(f"ffmpeg time-stretch failed:\n{stderr}")

    stretched, out_sr = sf.read(io.BytesIO(proc.stdout))
    if out_sr != sample_rate:
        logger.warning(
            "ffmpeg time-stretch changed sample rate %d → %d — resampling.",
            sample_rate,
            out_sr,
        )
    return _as_float32_mono(stretched)


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
    device: str = "cpu",
    *,
    backend: str = "chatterbox",
    voice: str = "af_heart",
    max_chars: int | None = None,
    silence_ms: int = 500,
    exaggeration: float = 0.5,
    cfg_weight: float = 0.5,
    temperature: float = 0.8,
    repetition_penalty: float = 1.2,
    mp3_bitrate: str = "64k",
    speed: float = 1.0,
    progress_callback: Callable[[int, int, str], None] | None = None,
    cancel_check: Callable[[], bool] | None = None,
    max_chunks: int = 0,
) -> Path:
    """Convert an article to spoken audio and save as MP3.

    Supports two local TTS backends:

    * **chatterbox** — ``ResembleAI/chatterbox`` with built-in default voice.
    * **kokoro** — ``hexgrad/Kokoro-82M`` (82M params, 20 American English
      voices).  Requires ``espeak-ng`` system package.

    Args:
        article: Structured article with content sections.
        output_path: Destination MP3 path.
        device: ``cpu``, ``cuda``, or ``mps``.
        backend: ``"chatterbox"`` (default) or ``"kokoro"``.
        voice: Kokoro voice pack name (default ``"af_heart"``).  Ignored
            by Chatterbox.  See ``_KOKORO_VOICES`` for the full list.
        max_chars: Max characters per TTS chunk.  Defaults to a
            backend-appropriate value if not set.
        silence_ms: Silence inserted between chunks.
        exaggeration: (Chatterbox only) Emotion / intensity.
        cfg_weight: (Chatterbox only) Classifier-free guidance.
        temperature: (Chatterbox only) Sampling temperature.
        repetition_penalty: (Chatterbox only) Token repetition penalty.
        mp3_bitrate: LAME bitrate string, e.g. ``64k``.
        speed: Playback speed multiplier (default ``1.0``).  For Chatterbox
            this is applied via ffmpeg ``atempo`` post-processing.  Kokoro
            applies speed natively during generation.
        progress_callback: Optional callback ``(current, total, message)``
            called after each TTS chunk completes.
        cancel_check: Optional callable that returns ``True`` when
            generation should be cancelled early.
        max_chunks: If > 0, only generate the first *max_chunks* chunks
            (useful for quick testing).  Default ``0`` = all chunks.

    Returns:
        Path to the generated MP3.
    """
    backend = backend.lower().strip()
    if backend not in ("chatterbox", "kokoro"):
        raise ValueError(
            f"Unknown backend {backend!r}. Choose 'chatterbox' or 'kokoro'."
        )

    # ------------------------------------------------------------------
    # Load model / pipeline
    # ------------------------------------------------------------------
    if backend == "kokoro":
        pipeline = _load_kokoro_pipeline(device)
        if max_chars is None:
            max_chars = _MAX_CHARS_KOKORO
        backend_label = f"Kokoro-82M ({voice})"
    else:
        model = _load_chatterbox_model(device)
        _prepare_chatterbox_voice(model, exaggeration=exaggeration)
        if max_chars is None:
            max_chars = _MAX_CHARS_CHATTERBOX
        backend_label = "Chatterbox"

    # ------------------------------------------------------------------
    # Chunk text
    # ------------------------------------------------------------------
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
        f"using {backend_label} ({_resolve_device(device)})..."
    )

    # ------------------------------------------------------------------
    # Generate speech chunk by chunk
    # ------------------------------------------------------------------
    audio_chunks: list[tuple[int, np.ndarray]] = []
    failed = False

    with click.progressbar(
        length=len(chunks),
        label="Generating audio",
        show_pos=True,
    ) as bar:
        for i, chunk in enumerate(chunks, start=1):
            try:
                if backend == "kokoro":
                    sr, audio = _generate_kokoro_speech(
                        chunk, pipeline, voice=voice, speed=speed
                    )
                else:
                    sr, audio = _generate_chatterbox_speech(
                        chunk,
                        model,
                        exaggeration=exaggeration,
                        cfg_weight=cfg_weight,
                        temperature=temperature,
                        repetition_penalty=repetition_penalty,
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

    # ------------------------------------------------------------------
    # Concatenate, time-stretch, encode
    # ------------------------------------------------------------------
    click.echo("\nConcatenating audio chunks...")
    combined_sr, combined_audio = _concat_audio(audio_chunks, silence_ms=silence_ms)

    # Kokoro applies speed natively; only time-stretch for Chatterbox
    if backend != "kokoro" and speed != 1.0:
        click.echo(f"Applying speed adjustment: {speed:.2f}×...")
        combined_audio = _time_stretch(combined_sr, combined_audio, speed=speed)

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
