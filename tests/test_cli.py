from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from click.testing import CliRunner

from reed.cli import main


class DoctorCommandTests(TestCase):

    def test_doctor_reports_ready_when_dependencies_are_available(self) -> None:
        with TemporaryDirectory() as directory:
            cache_dir = Path(directory)
            with (
                patch(
                    "reed.cli.shutil.which",
                    side_effect=["/usr/bin/uv", "/usr/bin/ffmpeg", "/usr/bin/espeak-ng"],
                ),
                patch("reed.cli.Path.home", return_value=cache_dir),
                patch("reed.cli._check_tts_libraries", return_value=True),
            ):
                result = CliRunner().invoke(main, ["doctor"])

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("✓ ffmpeg: /usr/bin/ffmpeg", result.output)
        self.assertIn("✓ espeak-ng: /usr/bin/espeak-ng", result.output)
        self.assertIn("Kokoro model: will download", result.output)
        self.assertIn("reed is ready", result.output)

    def test_doctor_reports_missing_binary_with_install_command(self) -> None:
        with TemporaryDirectory() as directory:
            with (
                patch("reed.cli.sys.platform", "darwin"),
                patch(
                    "reed.cli.shutil.which",
                    side_effect=["/usr/bin/uv", "/usr/bin/ffmpeg", None],
                ),
                patch("reed.cli.Path.home", return_value=Path(directory)),
                patch("reed.cli._check_tts_libraries", return_value=True),
            ):
                result = CliRunner().invoke(main, ["doctor"])

        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("✗ espeak-ng: not found on PATH", result.output)
        self.assertIn("brew install espeak-ng", result.output)

    def test_doctor_reports_missing_tts_libraries(self) -> None:
        import builtins

        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "torch":
                raise ImportError("No module named 'torch'")
            return real_import(name, *args, **kwargs)

        with TemporaryDirectory() as directory:
            with (
                patch("builtins.__import__", side_effect=fake_import),
                patch(
                    "reed.cli.shutil.which",
                    side_effect=["/usr/bin/uv", "/usr/bin/ffmpeg", "/usr/bin/espeak-ng"],
                ),
                patch("reed.cli.Path.home", return_value=Path(directory)),
            ):
                result = CliRunner().invoke(main, ["doctor"])

        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("✗ TTS libraries: No module named 'torch'", result.output)
        self.assertIn("uv tool install --force reed-cli", result.output)

    def test_doctor_reports_missing_misaki(self) -> None:
        import builtins

        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "misaki":
                raise ImportError("No module named 'misaki'")
            return real_import(name, *args, **kwargs)

        with TemporaryDirectory() as directory:
            with (
                patch("builtins.__import__", side_effect=fake_import),
                patch(
                    "reed.cli.shutil.which",
                    side_effect=["/usr/bin/uv", "/usr/bin/ffmpeg", "/usr/bin/espeak-ng"],
                ),
                patch("reed.cli.Path.home", return_value=Path(directory)),
            ):
                result = CliRunner().invoke(main, ["doctor"])

        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("✗ TTS libraries: No module named 'misaki'", result.output)
        self.assertIn("uv tool install --force reed-cli", result.output)


class InputOptionTests(TestCase):
    """Tests for the --input / -i option replacing --html and --md."""

    def test_html_flag_is_no_longer_available(self) -> None:
        result = CliRunner().invoke(main, ["epub", "--help"])
        self.assertEqual(result.exit_code, 0)
        self.assertNotIn("--html", result.output)
        self.assertNotIn("--md", result.output)
        self.assertIn("--input", result.output)

    def test_epub_generates_from_markdown_input(self) -> None:
        with TemporaryDirectory() as directory:
            md_path = Path(directory) / "article.md"
            md_path.write_text("# Test Article\n\nSome body text.\n", encoding="utf-8")
            output_path = Path(directory) / "out.epub"

            with patch("reed.cli.generate_epub") as mock_gen:
                result = CliRunner().invoke(
                    main, ["epub", "-i", str(md_path), "-o", str(output_path)]
                )

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("EPUB generated", result.output)
        mock_gen.assert_called_once()

    def test_epub_generates_from_txt_input(self) -> None:
        with TemporaryDirectory() as directory:
            txt_path = Path(directory) / "article.txt"
            txt_path.write_text("# Test Article\n\nSome body text.\n", encoding="utf-8")
            output_path = Path(directory) / "out.epub"

            with patch("reed.cli.generate_epub") as mock_gen:
                result = CliRunner().invoke(
                    main, ["epub", "--input", str(txt_path), "-o", str(output_path)]
                )

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("EPUB generated", result.output)
        mock_gen.assert_called_once()

    def test_epub_without_input_shows_error(self) -> None:
        result = CliRunner().invoke(main, ["epub"])
        self.assertNotEqual(result.exit_code, 0)
