"""Generate audiobooks from articles using OmniVoice TTS model.

Audio processing uses ``soundfile`` (WAV I/O), ``numpy`` (concatenation),
and ``ffmpeg`` (MP3 encoding).  No third-party audio libraries are required
beyond omnivoice, soundfile, and a system ffmpeg installation.
"""

import io
import logging
import os
import subprocess
import tempfile
from pathlib import Path

import click
import numpy as np
from omnivoice import OmniVoice, VoiceClonePrompt
from scipy.io import wavfile

from ..models import Article, ContentSection, SectionType

logger = logging.getLogger(__name__)

# Suppress tqdm progress bars from the TTS model internals — we show our own.
os.environ.setdefault("TQDM_DISABLE", "1")

# ---------------------------------------------------------------------------
# Model loading
# ---------------------------------------------------------------------------

_tts_model: OmniVoice | None = None
_MAX_CHARS = 2000


def _load_tts_model(device: str = "cpu") -> OmniVoice:
    """Load (or return cached) OmniVoice model.

    The model is downloaded from Hugging Face on first load and cached
    locally by the Hugging Face hub for subsequent runs.
    """
    global _tts_model
    if _tts_model is None:
        device_map = device if device == "cpu" else "cuda:0"
        click.echo(f"Loading OmniVoice model on {device_map}...")
        _tts_model = OmniVoice.from_pretrained(
            "k2-fsa/OmniVoice",
            device_map=device_map,
            dtype="float16" if device == "cuda" else None,
        )
        click.echo("Model loaded.")
    return _tts_model


# ---------------------------------------------------------------------------
# Speech generation
# ---------------------------------------------------------------------------


def _prepare_reference_audio(reference_audio_path: str) -> str:
    """Convert reference audio to 16kHz mono WAV via ffmpeg.

    OmniVoice expects clean audio input.  This normalises user-supplied
    reference clips (MP3, stereo, other sample rates) and returns the path
    to a temporary WAV.

    Returns the original path unchanged if it is already a 16kHz mono WAV.
    """
    # Quick probe: if already a 16kHz mono WAV, use as-is
    try:
        sr, audio = wavfile.read(reference_audio_path)
        if sr == 16000 and (audio.ndim == 1 or audio.shape[1] == 1):
            return reference_audio_path
    except Exception:
        pass

    tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    tmp_path = tmp.name

    cmd = [
        "ffmpeg",
        "-y",
        "-i", reference_audio_path,
        "-ar", "16000",
        "-ac", "1",
        "-c:a", "pcm_s16le",
        tmp_path,
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=30)
    except FileNotFoundError:
        raise RuntimeError(
            "ffmpeg is not installed or not on your PATH."
        ) from None
    except subprocess.TimeoutExpired:
        raise RuntimeError(
            "ffmpeg timed out while converting reference audio."
        ) from None

    if proc.returncode != 0:
        stderr = proc.stderr.decode("utf-8", errors="replace")[-400:]
        raise RuntimeError(
            f"ffmpeg failed to convert reference audio:\n{stderr}"
        )

    return tmp_path


def _wav_bytes_to_numpy(wav_bytes: bytes) -> tuple[int, np.ndarray]:
    """Read WAV bytes and return ``(sample_rate, audio)``."""
    return wavfile.read(io.BytesIO(wav_bytes))


def _numpy_to_wav_bytes(sample_rate: int, audio: np.ndarray) -> bytes:
    """Write a numpy array as WAV bytes in memory."""
    buf = io.BytesIO()
    wavfile.write(buf, sample_rate, audio)
    return buf.getvalue()


def _generate_speech(
    text: str,
    model: OmniVoice,
    voice_clone_prompt: VoiceClonePrompt | None = None,
    ref_audio_path: str | None = None,
    ref_text: str = "",
) -> np.ndarray:
    """Generate speech for *text* and return a ``(sample_rate, audio)`` tuple.

    Args:
        text: The text to convert to speech.
        model: A loaded OmniVoice instance.
        voice_clone_prompt: Pre-computed voice prompt (fast path).
        ref_audio_path: Path to a reference audio clip for voice cloning.
        ref_text: Transcription of the reference audio.

    Returns:
        ``(24000, audio)`` — sample rate and numpy array of shape ``(T,)``.
    """
    if voice_clone_prompt is not None:
        audio = model.generate(
            text=text,
            voice_clone_prompt=voice_clone_prompt,
        )
    else:
        audio = model.generate(
            text=text,
            ref_audio=ref_audio_path,
            ref_text=ref_text,
        )
    return 24000, audio[0]


# ---------------------------------------------------------------------------
# Article → text chunks
# ---------------------------------------------------------------------------


def article_text_for_tts(article: Article, max_chars: int) -> list[str]:
    """Build a list of text chunks suitable for TTS from an article's sections.

    Each chunk respects *max_chars* and preserves section boundaries —
    a chunk always starts at a section boundary (heading, paragraph, etc.).
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

        # Headings get a slight vocal emphasis via natural pause (period)
        if section.type in (SectionType.HEADING, SectionType.TITLE):
            prefix = f"{text}." if not text.endswith((".", "!", "?")) else text
        elif section.type == SectionType.BLOCKQUOTE:
            prefix = f"Quote: {text}"
        else:
            prefix = text

        # If this section alone exceeds max_chars, split it further
        if len(prefix) > max_chars:
            _flush()
            for sub in _split_long_text(prefix, max_chars):
                chunks.append(sub)
            continue

        # Start a new chunk if appending would overflow
        if current_len + len(prefix) + 1 > max_chars:
            _flush()

        current.append(prefix)
        current_len += len(prefix) + 1  # +1 for the space join

    _flush()
    return chunks


def _split_long_text(text: str, max_chars: int) -> list[str]:
    """Split a single long text into sentence-aware chunks ≤ *max_chars*."""
    sentences = [s.strip() for s in text.replace("\n", " ").split(". ")]
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0

    for i, sentence in enumerate(sentences):
        # Re-add the period except on the last sentence
        s = sentence if i == len(sentences) - 1 or sentence.endswith(".") else sentence + "."

        if current_len + len(s) + 1 > max_chars and current:
            chunks.append(" ".join(current))
            current = []
            current_len = 0

        current.append(s)
        current_len += len(s) + 1

    if current:
        chunks.append(" ".join(current))

    return chunks


# ---------------------------------------------------------------------------
# Audio helpers (numpy + ffmpeg)
# ---------------------------------------------------------------------------


def _make_silence(
    duration_ms: int,
    sample_rate: int,
    dtype: np.dtype = np.float32,
) -> np.ndarray:
    """Return a numpy array of silence with the given parameters."""
    nframes = int(sample_rate * duration_ms / 1000)
    return np.zeros(nframes, dtype=dtype)


def _concat_audio(
    audio_chunks: list[tuple[int, np.ndarray]],
    silence_ms: int = 500,
) -> tuple[int, np.ndarray]:
    """Concatenate (sample_rate, audio) chunks with silence between them.

    Returns ``(sample_rate, combined_audio)``.
    """
    if not audio_chunks:
        raise ValueError("No audio chunks to concatenate.")

    sr, first = audio_chunks[0]
    dtype = first.dtype
    silence = _make_silence(silence_ms, sr, dtype)
    parts: list[np.ndarray] = []

    for i, (ch_sr, ch_audio) in enumerate(audio_chunks):
        if ch_sr != sr:
            raise ValueError(
                f"Chunk {i} has different sample rate: {ch_sr} vs {sr}"
            )
        parts.append(ch_audio)
        if i < len(audio_chunks) - 1:
            parts.append(silence)

    return sr, np.concatenate(parts)


def _wav_to_mp3(
    sample_rate: int,
    audio: np.ndarray,
    output_path: Path,
    title: str = "",
    artist: str = "",
) -> None:
    """Encode a numpy audio array to MP3 via ffmpeg pipe.

    Raises:
        RuntimeError: If ffmpeg is not found on PATH.
    """
    wav_bytes = _numpy_to_wav_bytes(sample_rate, audio)

    cmd = [
        "ffmpeg",
        "-y",
        "-f", "wav",
        "-i", "pipe:0",
        "-codec:a", "libmp3lame",
        "-b:a", "64k",
    ]
    if title:
        cmd += ["-metadata", f"title={title}"]
    if artist:
        cmd += ["-metadata", f"artist={artist}"]
    cmd += ["-metadata", "album=reed"]
    cmd.append(str(output_path))

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
            "                  apt install ffmpeg     (Linux)"
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
    ref_text: str = "",
    voice_prompt_path: str = "",
    save_prompt_path: str = "",
    device: str = "cpu",
) -> Path:
    """Convert an article to spoken audio and save as MP3.

    Uses OmniVoice locally — no API key or network call needed
    after the initial model download.

    Args:
        article: The structured article with content sections.
        output_path: Where to write the MP3 file.
        reference_audio_path: Path to a reference audio clip for voice cloning.
        ref_text: Transcription of the reference audio (optional).
        voice_prompt_path: Path to a pre-computed ``.pt`` prompt file
            (skips audio loading / ASR).
        save_prompt_path: If set, save the computed voice prompt to this
            ``.pt`` file for reuse in later sessions.

    Returns:
        The path to the generated audio file.
    """
    model = _load_tts_model(device)

    # -- Resolve voice clone prompt -------------------------------------------
    voice_clone_prompt: VoiceClonePrompt | None = None
    ref_path: str | None = None

    if voice_prompt_path:
        click.echo(f"Loading voice clone prompt: {voice_prompt_path}")
        voice_clone_prompt = VoiceClonePrompt.load(voice_prompt_path)
    else:
        if not reference_audio_path:
            raise ValueError(
                "Either --reference-audio or --voice-prompt is required."
            )
        ref_path = _prepare_reference_audio(reference_audio_path)
        click.echo("Creating voice clone prompt from reference audio...")
        voice_clone_prompt = model.create_voice_clone_prompt(
            ref_audio=ref_path,
            ref_text=ref_text,
        )
        if save_prompt_path:
            voice_clone_prompt.save(save_prompt_path)
            click.echo(f"Voice prompt saved: {save_prompt_path}")

    # -- Generate speech for each chunk ---------------------------------------
    chunks = article_text_for_tts(article, _MAX_CHARS)

    if not chunks:
        raise ValueError("Article has no text content to convert to speech.")

    click.echo(
        f"\nGenerating audio for {len(chunks)} chunk(s) "
        f"using OmniVoice ({device})..."
    )

    audio_chunks: list[tuple[int, np.ndarray]] = []

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
                    voice_clone_prompt=voice_clone_prompt,
                )
            except Exception as exc:
                click.echo(f"\nError: {exc}", err=True)
                if i == 1:
                    raise RuntimeError(
                        f"Speech generation failed: {exc}"
                    ) from exc
                click.echo(
                    "Skipping remaining chunks due to error.", err=True
                )
                break

            audio_chunks.append((sr, audio))
            bar.update(1)

    if not audio_chunks:
        raise RuntimeError("No audio was generated. Check the errors above.")

    # Concatenate with 0.5 s silence between chunks
    click.echo("\nConcatenating audio chunks...")
    combined_sr, combined_audio = _concat_audio(audio_chunks, silence_ms=500)

    # Ensure output directory exists
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Encode to MP3
    click.echo("Encoding to MP3...")
    _wav_to_mp3(
        combined_sr,
        combined_audio,
        output_path,
        title=article.metadata.title,
        artist=article.metadata.author,
    )

    # Clean up temp reference audio if we created one
    if ref_path and ref_path != reference_audio_path:
        try:
            Path(ref_path).unlink()
        except OSError:
            pass

    click.echo(f"\n✓ Audiobook generated: {output_path}")
    return output_path
