from __future__ import annotations

import io
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest import mock

import numpy as np

from reed.models import Article, ArticleMetadata, ContentSection, SectionType
from reed.outputs import audiobook


def _demo_article() -> Article:
    return Article(
        metadata=ArticleMetadata(title="Progress Test", author="Tester"),
        sections=[
            ContentSection(SectionType.HEADING, "First Heading", 2),
            ContentSection(SectionType.PARAGRAPH, "Some body text for testing."),
            ContentSection(SectionType.HEADING, "Second Heading", 2),
        ],
    )


def _heavy_deps_patched() -> ExitStack:
    """Stub the TTS/ffmpeg machinery so generate_audiobook is fast and pure."""
    stack = ExitStack()
    stack.enter_context(
        mock.patch.object(audiobook, "_load_kokoro_pipeline", return_value=object())
    )
    stack.enter_context(
        mock.patch.object(
            audiobook,
            "_generate_kokoro_speech",
            return_value=(24000, np.zeros(2400, dtype=np.float32)),
        )
    )
    stack.enter_context(
        mock.patch.object(
            audiobook,
            "_concat_audio",
            return_value=(24000, np.zeros(2400, dtype=np.float32)),
        )
    )
    stack.enter_context(mock.patch.object(audiobook, "_wav_to_mp3"))
    return stack


class FfmpegProgressParsingTests(unittest.TestCase):
    def test_parses_out_time_ms(self) -> None:
        self.assertEqual(audiobook._parse_ffmpeg_progress_ms("out_time_ms=1234"), 1234)

    def test_parses_out_time_us(self) -> None:
        self.assertEqual(
            audiobook._parse_ffmpeg_progress_ms("out_time_us=2500000"), 2500
        )

    def test_ignores_other_progress_lines(self) -> None:
        self.assertIsNone(audiobook._parse_ffmpeg_progress_ms("frame=42"))
        self.assertIsNone(audiobook._parse_ffmpeg_progress_ms("progress=continue"))
        self.assertIsNone(audiobook._parse_ffmpeg_progress_ms("not a kv line"))
        self.assertIsNone(audiobook._parse_ffmpeg_progress_ms("out_time_ms=oops"))


class NarrationSnippetTests(unittest.TestCase):
    def test_short_segment_is_returned_as_is(self) -> None:
        segment = audiobook.NarrationSegment(text="Short.", pause_after_ms=0)
        self.assertEqual(audiobook._narration_snippet(segment), " Short.")

    def test_long_segment_is_truncated(self) -> None:
        segment = audiobook.NarrationSegment(text="word " * 20, pause_after_ms=0)
        snippet = audiobook._narration_snippet(segment)
        self.assertLessEqual(len(snippet), 42)
        self.assertTrue(snippet.endswith("…"))

    def test_none_renders_empty(self) -> None:
        self.assertEqual(audiobook._narration_snippet(None), "")


class GenerateAudiobookProgressTests(unittest.TestCase):
    def test_callback_reports_monotonic_chunks_and_suppresses_cli_output(
        self,
    ) -> None:
        calls: list[tuple[int, int, str]] = []
        echo = mock.Mock()

        with tempfile.TemporaryDirectory() as directory, _heavy_deps_patched(), mock.patch.object(
            audiobook.click, "echo", echo
        ):
            audiobook.generate_audiobook(
                _demo_article(),
                Path(directory) / "out.mp3",
                progress_callback=lambda c, t, m: calls.append((c, t, m)),
            )
            wav_mock = audiobook._wav_to_mp3

        self.assertGreater(len(calls), 1)
        self.assertEqual([c[0] for c in calls], list(range(1, len(calls) + 1)))
        self.assertEqual({c[1] for c in calls}, {len(calls)})
        self.assertEqual({c[2] for c in calls}, {"Generating audio…"})

        for call in echo.call_args_list:
            if call.args:
                self.assertNotIn("Chunk ", call.args[0])

        _, kwargs = wav_mock.call_args
        self.assertTrue(callable(kwargs["progress_callback"]))

    def test_non_tty_cli_path_prints_chunk_progress(self) -> None:
        echo = mock.Mock()

        with tempfile.TemporaryDirectory() as directory, _heavy_deps_patched(), mock.patch.object(
            audiobook, "_stdout_is_tty", return_value=False
        ), mock.patch.object(audiobook.click, "echo", echo), mock.patch.object(
            audiobook.click.utils, "echo"
        ):
            audiobook.generate_audiobook(_demo_article(), Path(directory) / "out.mp3")
            wav_mock = audiobook._wav_to_mp3

        printed = [c.args[0] for c in echo.call_args_list if c.args]
        self.assertTrue(any("Chunk 1/5 done" in text for text in printed))
        self.assertTrue(any("Chunk 5/5 done" in text for text in printed))

        _, kwargs = wav_mock.call_args
        self.assertTrue(callable(kwargs["progress_callback"]))


class _FakeStdin:
    def __init__(self) -> None:
        self.data = b""

    def write(self, data: bytes) -> int:
        self.data += data
        return len(data)

    def close(self) -> None:
        pass


class _FakePopen:
    def __init__(
        self, progress_lines: list[bytes], returncode: int = 0
    ) -> None:
        self.stdin = _FakeStdin()
        self.stdout = io.BytesIO(b"\n".join(progress_lines) + b"\n")
        self.stderr = io.BytesIO(b"")
        self.returncode = returncode

    def wait(self, timeout: float | None = None) -> int:
        return self.returncode

    def kill(self) -> None:
        self.returncode = 1


class WavToMp3ProgressTests(unittest.TestCase):
    def test_streams_encoding_progress(self) -> None:
        progress: list[tuple[int, int]] = []
        fake = _FakePopen(
            [
                b"frame=1",
                b"out_time_ms=500",
                b"out_time_ms=1000",
                b"progress=end",
            ]
        )

        with mock.patch.object(audiobook.subprocess, "Popen", return_value=fake), mock.patch.object(
            audiobook, "_numpy_to_wav_bytes", return_value=b"wav"
        ):
            audiobook._wav_to_mp3(
                24000,
                np.zeros(24000, dtype=np.float32),  # 1 second of audio
                Path("out.mp3"),
                progress_callback=lambda c, t, m: progress.append((c, t)),
            )

        self.assertEqual(progress, [(500, 1000), (1000, 1000)])
        self.assertEqual(fake.stdin.data, b"wav")

    def test_reports_ffmpeg_failure(self) -> None:
        fake = _FakePopen([b"out_time_ms=0"], returncode=1)
        fake.stderr = io.BytesIO(b"muxing failed")

        with mock.patch.object(audiobook.subprocess, "Popen", return_value=fake), mock.patch.object(
            audiobook, "_numpy_to_wav_bytes", return_value=b"wav"
        ):
            with self.assertRaisesRegex(RuntimeError, "ffmpeg failed"):
                audiobook._wav_to_mp3(
                    24000,
                    np.zeros(24000, dtype=np.float32),
                    Path("out.mp3"),
                )


if __name__ == "__main__":
    unittest.main()
