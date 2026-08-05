"""Bundled demo sample article (used by the CLI and the web interface)."""

from pathlib import Path


def sample_article_path() -> Path:
    """Locate the bundled demo article (wheel data file or source checkout)."""
    try:
        from importlib.resources import files

        resource = files("reed").joinpath("examples", "reed-demo.md")
        if resource.is_file():
            return Path(resource)  # type: ignore[arg-type]
    except (ModuleNotFoundError, OSError, TypeError):
        pass
    return Path(__file__).resolve().parents[2] / "examples" / "reed-demo.md"
