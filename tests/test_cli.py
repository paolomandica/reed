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
                patch("reed.cli.shutil.which", side_effect=["/usr/bin/ffmpeg", "/usr/bin/espeak-ng"]),
                patch("reed.cli.Path.home", return_value=cache_dir),
            ):
                result = CliRunner().invoke(main, ["doctor"])

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("✓ ffmpeg: /usr/bin/ffmpeg", result.output)
        self.assertIn("✓ espeak-ng: /usr/bin/espeak-ng", result.output)
        self.assertIn("Kokoro model: will download", result.output)
        self.assertIn("reed is ready", result.output)
