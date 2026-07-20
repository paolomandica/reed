"""Generate audiobooks from articles using ResembleAI Chatterbox TTS.

Install::

    pip install chatterbox-tts soundfile click numpy

Audio I/O uses ``soundfile`` (WAV), ``numpy`` (concatenation), and system
``ffmpeg`` (MP3).  The model is loaded from Hugging Face
``ResembleAI/chatterbox`` via ``ChatterboxTTS.from_pretrained``.
"""

from __future__ import annotations

import io
import logging
import os
import re
import subprocess
import tempfile
from pathlib import Path

import click
import numpy as np
import soundfile as sf
import torch
from chatterbox.tts import ChatterboxTTS

from ..models import Article, SectionType

logger = logging.getLogger(__name__)

os.environ.setdefault("TQDM_DISABLE", "1")

# ---------------------------------------------------------------------------
# Constants / model cache
# ---------------------------------------------------------------------------

_tts_model: ChatterboxTTS | None = None
_MAX_CHARS = 500  # Chatterbox is happiest with shorter turns
_REF_SAMPLE_RATE = 16_000

_SENTENCE_RE = re.compile(r"(?<=[.!?…])\s+|\n+")


# ---------------------------------------------------------------------------
# Model loading
# ---------------------------------------------------------------------------


def _resolve_device(device: str) -> str:
    """Map a user-facing device string to what Chatterbox expects."""
    d = device.lower().strip()
    if d in {"cpu", "mps"}:
        return d
    if d in {"cuda", "gpu"}:
        return "cuda"
    if d.startswith("cuda:"):
        # Chatterbox uses a single "cuda" device string; pin via CUDA_VISIBLE_DEVICES
        # if you need a non-zero GPU index.
        if d != "cuda:0":
            logger.warning(
                "Chatterbox uses device='cuda'; for multi-GPU pin "
                "CUDA_VISIBLE_DEVICES (requested %s).",
                d,
            )
        return "cuda"
    raise ValueError(f"Unsupported device {device!r}. Use cpu, cuda, or mps.")


def _load_tts_model(device: str = "mps") -> ChatterboxTTS:
    """Load (or return cached) Chatterbox TTS model.

    Weights are downloaded from Hugging Face ``ResembleAI/chatterbox`` on
    first load and cached by the hub thereafter.
    """
    global _tts_model
    if _tts_model is None:
        device_str = _resolve_device(device)
        # Fall back to CPU if requested device is MPS but unavailable
        if device_str == "mps" and not getattr(torch.backends.mps, "is_available", lambda: False)():
            click.echo("MPS not available — falling back to CPU.")
            device_str = "cpu"
        click.echo(f"Loading Chatterbox TTS on {device_str}...")
        _tts_model = ChatterboxTTS.from_pretrained(device=device_str)
        click.echo(f"Model loaded (sample rate={_tts_model.sr} Hz).")
    return _tts_model


# ---------------------------------------------------------------------------
# Reference audio / voice conditionals
# ---------------------------------------------------------------------------


def _is_wav_usable(path: str) -> bool:
    """True if *path* is a readable mono-or-stereo WAV (any rate)."""
    try:
        info = sf.info(path)
    except Exception:
        return False
    return info.frames > 0 and info.format == "WAV"


def _prepare_reference_audio(reference_audio_path: str) -> tuple[str, bool]:
    """Ensure reference audio is a WAV Chatterbox/librosa can load cleanly.

    Chatterbox loads the prompt with librosa internally.  Converting exotic
    containers (m4a, etc.) up front avoids opaque load failures.

    Returns:
        ``(path, is_temporary)`` — delete *path* when *is_temporary* is True.
    """
    if _is_wav_usable(reference_audio_path):
        return reference_audio_path, False

    fd, tmp_path = tempfile.mkstemp(suffix=".wav")
    os.close(fd)

    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        reference_audio_path,
        "-ar",
        str(_REF_SAMPLE_RATE),
        "-ac",
        "1",
        "-c:a",
        "pcm_s16le",
        tmp_path,
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=30)
    except FileNotFoundError:
        Path(tmp_path).unlink(missing_ok=True)
        raise RuntimeError("ffmpeg is not installed or not on your PATH.") from None
    except subprocess.TimeoutExpired:
        Path(tmp_path).unlink(missing_ok=True)
        raise RuntimeError(
            "ffmpeg timed out while converting reference audio."
        ) from None

    if proc.returncode != 0:
        Path(tmp_path).unlink(missing_ok=True)
        stderr = proc.stderr.decode("utf-8", errors="replace")[-400:]
        raise RuntimeError(f"ffmpeg failed to convert reference audio:\n{stderr}")

    return tmp_path, True


def _prepare_voice(
    model: ChatterboxTTS,
    *,
    reference_audio_path: str = "",
    voice_prompt_path: str = "",
    save_prompt_path: str = "",
    exaggeration: float = 0.5,
) -> None:
    """Attach voice conditionals to *model* (mutates ``model.conds``).

    Prefer a saved ``.pt`` conditionals file when available; otherwise build
    them from *reference_audio_path* once via ``prepare_conditionals``.
    """
    if voice_prompt_path:
        click.echo(f"Loading voice conditionals: {voice_prompt_path}")
        # Conditionals lives on the chatterbox.tts module
        from chatterbox.tts import Conditionals

        model.conds = Conditionals.load(
            voice_prompt_path,
            map_location=model.device,
        ).to(model.device)
        return

    if not reference_audio_path:
        # Built-in default voice shipped with the checkpoint (conds.pt)
        if model.conds is None:
            raise ValueError(
                "Either reference_audio_path or voice_prompt_path is required "
                "(and this checkpoint has no built-in default voice)."
            )
        click.echo("Using built-in default Chatterbox voice.")
        return

    ref_path, is_temp = _prepare_reference_audio(reference_audio_path)
    try:
        click.echo("Preparing voice conditionals from reference audio...")
        model.prepare_conditionals(ref_path, exaggeration=exaggeration)
    finally:
        if is_temp:
            Path(ref_path).unlink(missing_ok=True)

    if save_prompt_path and model.conds is not None:
        model.conds.save(Path(save_prompt_path))
        click.echo(f"Voice conditionals saved: {save_prompt_path}")


# ---------------------------------------------------------------------------
# Speech generation
# ---------------------------------------------------------------------------


def _tensor_to_numpy(wav: torch.Tensor | np.ndarray) -> np.ndarray:
    """Convert Chatterbox output to mono float32 numpy."""
    if isinstance(wav, torch.Tensor):
        wav = wav.detach().float().cpu().numpy()
    x = np.asarray(wav, dtype=np.float32)
    if x.ndim > 1:
        # Chatterbox returns shape (1, T)
        x = np.squeeze(x, axis=0) if x.shape[0] == 1 else x.mean(axis=0)
    return x.reshape(-1)


def _generate_speech(
    text: str,
    model: ChatterboxTTS,
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
        raise RuntimeError("Voice conditionals are not set. Call _prepare_voice first.")

    wav = model.generate(
        text,
        exaggeration=exaggeration,
        cfg_weight=cfg_weight,
        temperature=temperature,
        repetition_penalty=repetition_penalty,
        # Do not pass audio_prompt_path here — conditionals are already prepared
        # so we avoid re-encoding the reference on every chunk.
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
    reference_audio_path: str = "",
    voice_prompt_path: str = "",
    save_prompt_path: str = "",
    device: str = "cpu",
    *,
    max_chars: int = _MAX_CHARS,
    silence_ms: int = 500,
    exaggeration: float = 0.5,
    cfg_weight: float = 0.5,
    temperature: float = 0.8,
    repetition_penalty: float = 1.2,
    mp3_bitrate: str = "64k",
) -> Path:
    """Convert an article to spoken audio and save as MP3.

    Uses local Chatterbox (``ResembleAI/chatterbox``) — no API key needed
    after the initial model download.

    Args:
        article: Structured article with content sections.
        output_path: Destination MP3 path.
        reference_audio_path: Reference clip for zero-shot voice cloning
            (~5–30 s of clean speech). Optional if the checkpoint has a
            built-in default voice or *voice_prompt_path* is set.
        voice_prompt_path: Precomputed conditionals ``.pt`` file (from a
            previous ``save_prompt_path`` run).
        save_prompt_path: If set, save prepared conditionals here for reuse.
        device: ``cpu``, ``cuda``, or ``mps``.
        max_chars: Max characters per TTS chunk (keep modest; Chatterbox
            generation is token-capped).
        silence_ms: Silence inserted between chunks.
        exaggeration: Emotion / intensity (default ``0.5``; try ``~0.7``
            for more dramatic delivery).
        cfg_weight: Classifier-free guidance (default ``0.5``; lower ~``0.3``
            if the reference speaker is fast or speech feels rushed).
        temperature: Sampling temperature (default ``0.8``).
        repetition_penalty: Token repetition penalty (default ``1.2``).
        mp3_bitrate: LAME bitrate string, e.g. ``64k``.

    Returns:
        Path to the generated MP3.
    """
    model = _load_tts_model(device)

    _prepare_voice(
        model,
        reference_audio_path=reference_audio_path,
        voice_prompt_path=voice_prompt_path,
        save_prompt_path=save_prompt_path,
        exaggeration=exaggeration,
    )

    chunks = article_text_for_tts(article, max_chars)
    if not chunks:
        raise ValueError("Article has no text content to convert to speech.")

    click.echo(
        f"\nGenerating audio for {len(chunks)} chunk(s) "
        f"using Chatterbox ({_resolve_device(device)})..."
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
                sr, audio = _generate_speech(
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
