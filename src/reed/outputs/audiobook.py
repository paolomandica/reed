"""Generate audiobooks from articles using Hugging Face TTS models.

Audio processing is done with the stdlib ``wave`` module (WAV I/O)
and ``ffmpeg`` (concatenation + MP3 encoding).  No third-party audio
libraries are required beyond a system ffmpeg installation.
"""

import io
import logging
import os
import subprocess
import sys
import wave
from pathlib import Path

import click
import httpx

from ..models import Article, ContentSection, SectionType

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Model registry
# ---------------------------------------------------------------------------

HF_TTS_API = "https://api-inference.huggingface.co/models/{model_id}"

MODELS = [
    {
        "id": "FunAudioLLM/CosyVoice2-0.5B",
        "name": "CosyVoice2 (FunAudioLLM)",
        "max_chars": 400,
    },
    {
        "id": "ResembleAI/chatterbox-turbo",
        "name": "Chatterbox Turbo (ResembleAI)",
        "max_chars": 400,
    },
]

# ---------------------------------------------------------------------------
# Model selection
# ---------------------------------------------------------------------------


def select_model() -> dict:
    """Prompt the user to choose a TTS model interactively.

    Returns the chosen model dictionary from *MODELS*.
    """
    click.echo("\nAvailable TTS models:")
    for i, model in enumerate(MODELS, start=1):
        click.echo(f"  {i}. {model['name']}")

    while True:
        try:
            choice = click.prompt(
                "Choose a model",
                type=click.IntRange(1, len(MODELS)),
                default=1,
                show_default=True,
            )
            model = MODELS[choice - 1]
            click.echo(f"→ Using: {model['name']}")
            return model
        except click.Abort:
            click.echo("Cancelled.", err=True)
            sys.exit(1)


# ---------------------------------------------------------------------------
# Hugging Face Inference API
# ---------------------------------------------------------------------------


def _get_token() -> str:
    """Read ``HF_TOKEN`` from the environment, exiting with a clear message
    if it is not set."""
    token = os.environ.get("HF_TOKEN")
    if not token:
        click.echo(
            "Error: HF_TOKEN environment variable is not set.\n"
            "Create a Hugging Face token at https://huggingface.co/settings/tokens\n"
            "and export it:\n\n"
            "  export HF_TOKEN=hf_...\n",
            err=True,
        )
        sys.exit(1)
    return token


def generate_audio_chunk(text: str, model_id: str) -> bytes:
    """Call the Hugging Face Inference API to generate speech for *text*.

    Args:
        text: The text to convert to speech.
        model_id: Hugging Face model ID (e.g. ``FunAudioLLM/CosyVoice2-0.5B``).

    Returns:
        Raw audio bytes (typically WAV).

    Raises:
        RuntimeError: If the API returns an error or non-200 status.
    """
    token = _get_token()
    url = HF_TTS_API.format(model_id=model_id)

    logger.info("Calling HF TTS API: %s (text_len=%d)", model_id, len(text))

    try:
        response = httpx.post(
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json={"inputs": text},
            timeout=120,
        )
    except httpx.HTTPError as exc:
        raise RuntimeError(
            f"Failed to reach Hugging Face API: {exc}"
        ) from exc

    if response.status_code != 200:
        detail = _extract_error(response)
        raise RuntimeError(
            f"Hugging Face API returned {response.status_code} for model "
            f"'{model_id}':\n{detail}\n\n"
            f"Tip: The model may not support the serverless Inference API. "
            f"Try the other model, or check the model page on huggingface.co."
        )

    content_type = response.headers.get("content-type", "")
    if "audio" not in content_type:
        logger.warning(
            "Unexpected content-type: %s (len=%d bytes)",
            content_type,
            len(response.content),
        )

    logger.info("Received %d bytes of audio", len(response.content))
    return response.content


def _extract_error(response: httpx.Response) -> str:
    """Try to extract a human-readable error from an HF API response."""
    try:
        body = response.json()
        if isinstance(body, dict):
            return body.get("error", response.text)
        return response.text
    except ValueError:
        return response.text[:500]


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
# Audio helpers (stdlib wave + ffmpeg)
# ---------------------------------------------------------------------------


def _read_wav_params(wav_bytes: bytes) -> tuple[int, int, int]:
    """Return ``(nchannels, sampwidth, framerate)`` from WAV data in memory."""
    with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
        return wf.getnchannels(), wf.getsampwidth(), wf.getframerate()


def _make_silence_wav(
    duration_ms: int,
    nchannels: int,
    sampwidth: int,
    framerate: int,
) -> bytes:
    """Generate a WAV file in memory containing *duration_ms* of silence."""
    nframes = int(framerate * duration_ms / 1000)
    silence_frames = b"\x00" * (nframes * nchannels * sampwidth)

    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(nchannels)
        wf.setsampwidth(sampwidth)
        wf.setframerate(framerate)
        wf.writeframes(silence_frames)
    return buf.getvalue()


def _concat_wavs_with_silence(
    wav_chunks: list[bytes],
    silence_ms: int = 500,
) -> bytes:
    """Concatenate WAV chunks with silence between them, return a single WAV.

    All chunks must share the same audio parameters (channels, sample width,
    framerate).

    Args:
        wav_chunks: List of WAV file contents as bytes.
        silence_ms: Milliseconds of silence to insert between chunks.

    Returns:
        A single concatenated WAV file as bytes.
    """
    if not wav_chunks:
        raise ValueError("No WAV chunks to concatenate.")

    nchannels, sampwidth, framerate = _read_wav_params(wav_chunks[0])

    # Read all PCM frames
    all_frames: list[bytes] = []
    silence_wav = _make_silence_wav(silence_ms, nchannels, sampwidth, framerate)

    # Read silence frames once
    with wave.open(io.BytesIO(silence_wav), "rb") as wf:
        silence_frames = wf.readframes(wf.getnframes())

    for i, wav_chunk in enumerate(wav_chunks):
        ch_nch, ch_sw, ch_fr = _read_wav_params(wav_chunk)
        if (ch_nch, ch_sw, ch_fr) != (nchannels, sampwidth, framerate):
            raise ValueError(
                f"Chunk {i} has different audio parameters: "
                f"({ch_nch}, {ch_sw}, {ch_fr}) vs "
                f"({nchannels}, {sampwidth}, {framerate})"
            )

        with wave.open(io.BytesIO(wav_chunk), "rb") as wf:
            all_frames.append(wf.readframes(wf.getnframes()))

        if i < len(wav_chunks) - 1:
            all_frames.append(silence_frames)

    # Write combined WAV
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(nchannels)
        wf.setsampwidth(sampwidth)
        wf.setframerate(framerate)
        for frames in all_frames:
            wf.writeframes(frames)
    return buf.getvalue()


def _wav_to_mp3(
    wav_bytes: bytes,
    output_path: Path,
    title: str = "",
    artist: str = "",
) -> None:
    """Encode WAV data to MP3 via ffmpeg.

    Raises:
        RuntimeError: If ffmpeg is not found on PATH.
    """
    cmd = [
        "ffmpeg",
        "-y",  # overwrite output
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
    model: dict,
    output_path: Path,
) -> Path:
    """Convert an article to spoken audio and save as MP3.

    Args:
        article: The structured article with content sections.
        model: A model dict from the :data:`MODELS` registry.
        output_path: Where to write the MP3 file.

    Returns:
        The path to the generated audio file.
    """
    max_chars = model["max_chars"]
    model_id = model["id"]
    chunks = article_text_for_tts(article, max_chars)

    if not chunks:
        raise ValueError("Article has no text content to convert to speech.")

    click.echo(
        f"\nGenerating audio for {len(chunks)} chunk(s) "
        f"using {model['name']}..."
    )

    wav_chunks: list[bytes] = []

    for i, chunk in enumerate(chunks, start=1):
        preview = chunk[:80] + ("..." if len(chunk) > 80 else "")
        click.echo(f"  [{i}/{len(chunks)}] {preview}")

        try:
            raw_audio = generate_audio_chunk(chunk, model_id)
        except RuntimeError as exc:
            click.echo(f"Error: {exc}", err=True)
            if i == 1:
                raise  # Fail fast on the first chunk
            click.echo(
                "Skipping remaining chunks due to API error.", err=True
            )
            break

        wav_chunks.append(raw_audio)

    if not wav_chunks:
        raise RuntimeError("No audio was generated. Check the errors above.")

    # Concatenate WAV chunks with 0.5 s silence between them
    click.echo("\nConcatenating audio chunks...")
    combined_wav = _concat_wavs_with_silence(wav_chunks, silence_ms=500)

    # Ensure output directory exists
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Encode to MP3
    click.echo("Encoding to MP3...")
    _wav_to_mp3(
        combined_wav,
        output_path,
        title=article.metadata.title,
        artist=article.metadata.author,
    )

    click.echo(f"\n✓ Audiobook generated: {output_path}")
    return output_path
