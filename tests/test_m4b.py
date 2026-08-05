from __future__ import annotations

import io
import json
import shutil
import subprocess
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest import mock

import numpy as np
from click.testing import CliRunner

from reed.cli import main
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


class ChapterRangeTests(unittest.TestCase):
    def test_no_headings_yields_single_fallback_chapter(self) -> None:
        segments = [
            audiobook.NarrationSegment(text="Title.", pause_after_ms=1000),
            audiobook.NarrationSegment(text="Body.", pause_after_ms=500),
        ]

        ranges = audiobook._build_chapter_ranges(
            segments, [100, 200], fallback_title="The Book"
        )

        self.assertEqual(ranges, [("The Book", 0, 1300)])

    def test_headings_split_content_into_chapters(self) -> None:
        segments = [
            audiobook.NarrationSegment(text="Title.", pause_after_ms=1000, chapter_title=""),
            audiobook.NarrationSegment(
                text="By Tester.", pause_after_ms=1000, chapter_title=""
            ),
            audiobook.NarrationSegment(
                text="First Heading.", pause_after_ms=1000, chapter_title="First Heading"
            ),
            audiobook.NarrationSegment(
                text="Paragraph.", pause_after_ms=500, chapter_title="First Heading"
            ),
            audiobook.NarrationSegment(
                text="Second Heading.", pause_after_ms=1000, chapter_title="Second Heading"
            ),
        ]

        ranges = audiobook._build_chapter_ranges(
            segments, [100] * 5, fallback_title="Introduction"
        )

        # Introduction: title 100 + pause 1000 + author 100 + pause 1000.
        # First Heading: heading 100 + pause 1000 + paragraph 100 + pause 500.
        # Second Heading: heading 100, no trailing pause.
        self.assertEqual(
            ranges,
            [
                ("Introduction", 0, 2200),
                ("First Heading", 2200, 3900),
                ("Second Heading", 3900, 4000),
            ],
        )

    def test_segment_count_mismatch_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "same length"):
            audiobook._build_chapter_ranges(
                [audiobook.NarrationSegment("A.", 0)], [1, 2], fallback_title="X"
            )


class FfmetadataTests(unittest.TestCase):
    def test_writes_chapter_blocks_with_escaping(self) -> None:
        text = audiobook._ffmetadata_text(
            "T = The Book",
            "A; Author",
            [("Intro", 0, 1500), ("Chapter 1", 1500, 3200)],
        )

        self.assertTrue(text.startswith(";FFMETADATA1\n"))
        self.assertIn("title=T \\= The Book\n", text)
        self.assertIn("artist=A\\; Author\n", text)
        self.assertIn("album=T \\= The Book\n", text)
        self.assertIn("[CHAPTER]\nTIMEBASE=1/1000\nSTART=0\nEND=1500\ntitle=Intro\n", text)
        self.assertIn("START=1500\nEND=3200\ntitle=Chapter 1\n", text)

    def test_album_falls_back_to_reed_without_a_title(self) -> None:
        text = audiobook._ffmetadata_text("", "Author", [("Intro", 0, 1000)])
        header = text.split("[CHAPTER]")[0]
        self.assertIn("album=reed\n", header)
        self.assertNotIn("title=", header)


class GenerateAudiobookM4bTests(unittest.TestCase):
    def test_m4b_is_the_default_output_format(self) -> None:
        with tempfile.TemporaryDirectory() as directory, _heavy_deps_patched(), mock.patch.object(
            audiobook, "_wav_to_m4b"
        ) as wav_m4b:
            audiobook.generate_audiobook(_demo_article(), Path(directory) / "out.m4b")

        wav_m4b.assert_called_once()
        self.assertTrue(callable(wav_m4b.call_args.kwargs["progress_callback"]))

    def test_m4b_format_builds_chapters_and_encodes(self) -> None:
        with tempfile.TemporaryDirectory() as directory, _heavy_deps_patched(), mock.patch.object(
            audiobook, "_wav_to_m4b"
        ) as wav_m4b:
            audiobook.generate_audiobook(
                _demo_article(),
                Path(directory) / "out.m4b",
                output_format="m4b",
            )

        wav_m4b.assert_called_once()
        chapters = wav_m4b.call_args.kwargs["chapters"]
        self.assertEqual(
            [(title, start) for title, start, _end in chapters],
            [
                ("Introduction", 0),
                ("First Heading", 2200),
                ("Second Heading", 3900),
            ],
        )
        self.assertEqual(chapters[0][2], 2200)
        self.assertEqual(chapters[1][2], 3900)

    def test_invalid_output_format_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory, _heavy_deps_patched():
            with self.assertRaisesRegex(ValueError, "output_format"):
                audiobook.generate_audiobook(
                    _demo_article(),
                    Path(directory) / "out.wav",
                    output_format="wav",
                )


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


class WavToM4bTests(unittest.TestCase):
    def test_builds_ffmetadata_command_and_cleans_up(self) -> None:
        fake = _FakePopen([b"out_time_ms=0", b"progress=end"])
        captured: dict[str, list[str]] = {}

        def fake_popen(cmd: list[str], **kwargs: object) -> _FakePopen:
            captured["cmd"] = cmd
            return fake

        with tempfile.TemporaryDirectory() as directory, mock.patch.object(
            audiobook.subprocess, "Popen", side_effect=fake_popen
        ), mock.patch.object(
            audiobook, "_numpy_to_wav_bytes", return_value=b"wav"
        ):
            audiobook._wav_to_m4b(
                24000,
                np.zeros(24000, dtype=np.float32),
                Path(directory) / "book.m4b",
                title="Book",
                artist="Author",
                chapters=[("Intro", 0, 1000)],
            )

        cmd = captured["cmd"]
        self.assertIn("-f", cmd)
        self.assertIn("ffmetadata", cmd)
        self.assertIn("-codec:a", cmd)
        self.assertIn("aac", cmd)
        self.assertTrue(str(cmd[-1]).endswith(".m4b"))
        self.assertEqual(fake.stdin.data, b"wav")
        leftovers = list(Path(tempfile.gettempdir()).glob("reed-chapters-*.txt"))
        self.assertEqual(leftovers, [])


@unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg not available")
class RealM4bEncodingTests(unittest.TestCase):
    def test_wav_to_m4b_encodes_with_chapters(self) -> None:
        sample_rate = 24000
        t = np.linspace(0, 1.0, sample_rate, endpoint=False)
        audio = (0.1 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)

        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / "book.m4b"
            audiobook._wav_to_m4b(
                sample_rate,
                audio,
                out,
                title="Book",
                artist="Author",
                chapters=[("Intro", 0, 500), ("Chapter 1", 500, 1000)],
            )

            self.assertTrue(out.is_file())
            self.assertGreater(out.stat().st_size, 0)
            self.assertIn(b"ftyp", out.read_bytes()[:16])

            if shutil.which("ffprobe"):
                probe = subprocess.run(
                    [
                        "ffprobe",
                        "-v",
                        "error",
                        "-show_chapters",
                        "-of",
                        "json",
                        str(out),
                    ],
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                self.assertEqual(probe.returncode, 0, probe.stderr)
                data = json.loads(probe.stdout)
                chapters = data.get("chapters", [])
                self.assertEqual(len(chapters), 2)
                self.assertEqual(chapters[0]["tags"]["title"], "Intro")
                self.assertEqual(chapters[1]["tags"]["title"], "Chapter 1")


class AudiobookFormatCliTests(unittest.TestCase):
    def test_m4b_is_default_format_in_cli(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            md = Path(directory) / "article.md"
            md.write_text("# Title\n\nBody.\n", encoding="utf-8")
            with mock.patch(
                "reed.outputs.generate_audiobook", return_value=Path("out.m4b")
            ) as generate:
                result = CliRunner().invoke(
                    main,
                    [
                        "audiobook",
                        "--md",
                        str(md),
                        "-o",
                        str(Path(directory) / "book"),
                    ],
                )

        self.assertEqual(result.exit_code, 0, result.output)
        generate.assert_called_once()
        self.assertEqual(generate.call_args.args[1].suffix, ".m4b")
        self.assertEqual(generate.call_args.kwargs["output_format"], "m4b")

    def test_invalid_format_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            md = Path(directory) / "article.md"
            md.write_text("# Title\n\nBody.\n", encoding="utf-8")
            result = CliRunner().invoke(
                main, ["audiobook", "--format", "wav", "--md", str(md)]
            )

        self.assertEqual(result.exit_code, 2)
        self.assertIn("Invalid value", result.output)

    def test_m4b_format_uses_m4b_suffix_and_passes_through(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            md = Path(directory) / "article.md"
            md.write_text("# Title\n\nBody.\n", encoding="utf-8")
            with mock.patch(
                "reed.outputs.generate_audiobook", return_value=Path("out.m4b")
            ) as generate:
                result = CliRunner().invoke(
                    main,
                    [
                        "audiobook",
                        "--format",
                        "m4b",
                        "--md",
                        str(md),
                        "-o",
                        str(Path(directory) / "book.mp3"),
                    ],
                )

        self.assertEqual(result.exit_code, 0, result.output)
        generate.assert_called_once()
        self.assertEqual(generate.call_args.args[1].suffix, ".m4b")
        self.assertEqual(generate.call_args.kwargs["output_format"], "m4b")


if __name__ == "__main__":
    unittest.main()
