"""CLI entry point for reed."""

import logging
import shutil
import sys
import webbrowser
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

import click

from .inputs import extract_from_html, extract_from_markdown
from .models import Article
from .outputs import generate_epub, generate_markdown
from .sample import sample_article_path as _sample_article_path

logger = logging.getLogger(__name__)


def _resolve_article(
    html_file: Path | None = None,
    md_file: Path | None = None,
) -> Article:
    """Resolve an Article from a local HTML file or Markdown file.

    Exactly one of *html_file* or *md_file* must be provided.
    """
    if html_file:
        logger.info("Extracting from HTML file: %s", html_file)
        return extract_from_html(html_file)

    if md_file:
        logger.info("Extracting from Markdown file: %s", md_file)
        return extract_from_markdown(md_file)

    click.echo(
        "Error: Please provide either --html or --md flag.\n\n"
        "Try: reed --help",
        err=True,
    )
    sys.exit(1)


def _setup_logging(verbose: bool) -> None:
    if verbose:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(levelname)s %(name)s: %(message)s",
            datefmt="%H:%M:%S",
        )


def _package_version() -> str:
    """Return the installed reed version, falling back for source checkouts."""
    try:
        return version("reed-cli")
    except PackageNotFoundError:
        from reed import __version__

        return __version__


# ---------------------------------------------------------------------------
# Click group
# ---------------------------------------------------------------------------


@click.group(invoke_without_command=True)
@click.version_option(version=_package_version(), prog_name="reed")
@click.pass_context
def main(ctx: click.Context) -> None:
    """Convert articles to ebooks and audiobooks.

    \b
    Commands:
      epub        Generate a Kindle-compatible EPUB
      audiobook   Generate an MP3 or M4B audiobook using Kokoro-82M TTS
      markdown    Generate a Markdown file
      demo        Generate all three formats from a bundled sample
      web         Start a browser-based web interface
      doctor      Check audiobook dependencies

    \b
    Examples:
      reed demo
      reed epub --html saved_article.html
      reed epub --md article.md
      reed markdown --html saved_article.html
      reed audiobook --html saved_article.html
      reed audiobook --md article.md
      reed audiobook -o out.mp3 --html article.html
      reed web
    """
    if ctx.invoked_subcommand is None:
        click.echo(ctx.get_help())
        return


# ---------------------------------------------------------------------------
# doctor subcommand
# ---------------------------------------------------------------------------


@main.command("doctor")
def doctor_cmd() -> None:
    """Check whether reed is ready to generate audiobooks."""
    problems: list[str] = []

    if not _check_python():
        problems.append("python")

    _check_uv()

    missing_binaries = _check_system_binaries()
    if missing_binaries:
        problems.extend(missing_binaries)
        click.echo(_system_dependency_hint(missing_binaries))

    if not _check_tts_libraries():
        problems.append("tts-libraries")

    model_cache = Path.home() / ".cache" / "huggingface" / "hub" / "models--hexgrad--Kokoro-82M"
    if model_cache.exists():
        click.echo("✓ Kokoro model: cached")
    else:
        click.echo("○ Kokoro model: will download on first audiobook generation")

    if problems:
        click.echo("", err=True)
        click.echo("Fix the items above, then run `reed doctor` again.", err=True)
        raise click.ClickException("reed is missing required components")

    click.echo("reed is ready for audiobook generation.")


def _check_python() -> bool:
    """Return True when the running Python satisfies reed's requirement."""
    version = sys.version.split()[0]
    if sys.version_info >= (3, 13):  # noqa: UP036 — friendly check for older interpreters
        click.echo(f"✓ Python: {version}")
        return True
    click.echo(f"✗ Python: {version} (reed requires Python 3.13+)")
    return False


def _check_uv() -> None:
    """Report whether uv is available (needed only for tool installs)."""
    path = shutil.which("uv")
    if path:
        click.echo(f"✓ uv: {path}")
    else:
        click.echo(
            "○ uv: not found on PATH — needed only for `uv tool install reed-cli`.\n"
            "  Install: https://docs.astral.sh/uv/getting-started/installation/"
        )


def _check_system_binaries() -> list[str]:
    """Check ffmpeg/espeak-ng and return the names that are missing."""
    missing: list[str] = []
    for name in ("ffmpeg", "espeak-ng"):
        path = shutil.which(name)
        if path:
            click.echo(f"✓ {name}: {path}")
        else:
            click.echo(f"✗ {name}: not found on PATH")
            missing.append(name)
    return missing


def _system_dependency_hint(missing: list[str]) -> str:
    """Return the exact install command for the current platform."""
    if sys.platform == "darwin":
        return "  Install with: brew install " + " ".join(missing)
    if sys.platform.startswith("linux") and shutil.which("apt-get"):
        return "  Install with: sudo apt-get install -y " + " ".join(missing)
    return (
        "  Install ffmpeg and espeak-ng for your operating system "
        "(see the README's System dependencies section)."
    )


def _check_tts_libraries() -> bool:
    """Check that the TTS libraries import correctly (no model download)."""
    try:
        import soundfile  # noqa: F401
        import torch  # noqa: F401
        from kokoro import KPipeline  # noqa: F401
    except ImportError as exc:
        click.echo(f"✗ TTS libraries: {exc}")
        click.echo(
            "  Reinstall reed with its dependencies: `uv tool install --force reed-cli`"
        )
        return False
    click.echo("✓ TTS libraries: kokoro, torch, soundfile")
    return True


# ---------------------------------------------------------------------------
# epub subcommand
# ---------------------------------------------------------------------------


@main.command("epub")
@click.option(
    "-o", "--output", type=click.Path(path_type=Path), help="Output EPUB path"
)
@click.option(
    "--html",
    "html_file",
    type=click.Path(exists=True, path_type=Path),
    help="Use a local HTML file",
)
@click.option(
    "--md",
    "md_file",
    type=click.Path(exists=True, path_type=Path),
    help="Use a local Markdown file",
)
@click.option("--verbose", "-v", is_flag=True, help="Show detailed progress")
def epub_cmd(
    output: Path | None,
    html_file: Path | None,
    md_file: Path | None,
    verbose: bool,
) -> None:
    """Generate a Kindle-compatible EPUB from an article.

    \b
    Use a local HTML or Markdown file:
        reed epub --html article.html
        reed epub --md article.md
    """
    _setup_logging(verbose)

    try:
        article = _resolve_article(html_file=html_file, md_file=md_file)

        logger.info(
            "Article: title=%r, author=%r, sections=%d",
            article.metadata.title,
            article.metadata.author,
            len(article.sections),
        )

        if output:
            output_path = output
        else:
            epubs_dir = Path("epubs")
            epubs_dir.mkdir(exist_ok=True)
            output_path = epubs_dir / article.output_filename()
        if output_path.suffix != ".epub":
            output_path = output_path.with_suffix(".epub")

        generate_epub(article, output_path)
        click.echo(f"✓ EPUB generated: {output_path}")

    except ValueError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)
    except Exception as e:
        click.echo(f"Unexpected error: {e}\nRun with -v for the full traceback.", err=True)
        if verbose:
            raise
        sys.exit(1)


# ---------------------------------------------------------------------------
# audiobook subcommand
# ---------------------------------------------------------------------------


@main.command("audiobook")
@click.option(
    "-o",
    "--output",
    type=click.Path(path_type=Path),
    help="Output audio file path (default: <title-slug>.m4b)",
)
@click.option(
    "--html",
    "html_file",
    type=click.Path(exists=True, path_type=Path),
    help="Use a local HTML file",
)
@click.option(
    "--md",
    "md_file",
    type=click.Path(exists=True, path_type=Path),
    help="Use a local Markdown file",
)
@click.option(
    "--voice",
    type=str,
    default="af_heart",
    show_default=True,
    help="Kokoro voice. Use --list-voices to see all options.",
)
@click.option(
    "--list-voices",
    "list_voices",
    is_flag=True,
    help="List available Kokoro voices and exit.",
)
@click.option(
    "--speed",
    type=float,
    default=1.0,
    show_default=True,
    help="Playback speed (0.5–2.0). 0.85 = 15%% slower, 1.0 = normal.",
)
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["mp3", "m4b"]),
    default="m4b",
    show_default=True,
    help="Audio container: m4b (chaptered from headings) or mp3 (flat).",
)
@click.option("--verbose", "-v", is_flag=True, help="Show detailed progress")
@click.option(
    "--max-sections",
    type=int,
    default=0,
    help="Only process the first N sections (0 = all). Quick-test shortcut.",
)
def audiobook_cmd(
    output: Path | None,
    html_file: Path | None,
    md_file: Path | None,
    voice: str,
    list_voices: bool,
    speed: float,
    output_format: str,
    verbose: bool,
    max_sections: int = 0,
) -> None:
    """Generate an MP3 or chaptered M4B audiobook using Kokoro-82M TTS.

    \b
    Kokoro-82M is a lightweight (82M params) open-weight TTS model with
    20 American English voices.  Requires the espeak-ng system package.

    The model is downloaded from Hugging Face on first run and cached locally.

    \b
    Examples:
        reed audiobook --html article.html
        reed audiobook --html article.html --voice af_bella
        reed audiobook --md article.md --speed 0.85
        reed audiobook --html article.html --format mp3
        reed audiobook -o out.m4b --html article.html
        reed audiobook --list-voices
    """
    # Lazy import — pulls in numpy, soundfile, kokoro (heavy)
    from .outputs import generate_audiobook

    # --list-voices just prints the voice table and exits
    if list_voices:
        from .outputs.audiobook import _KOKORO_VOICES

        click.echo("Kokoro American English voices:\n")
        for v in _KOKORO_VOICES:
            click.echo(f"  {v}")
        return

    _setup_logging(verbose)

    if speed < 0.5 or speed > 2.0:
        click.echo("Error: --speed must be between 0.5 and 2.0", err=True)
        sys.exit(1)

    try:
        article = _resolve_article(html_file=html_file, md_file=md_file)

        if max_sections > 0 and len(article.sections) > max_sections:
            article.sections = article.sections[:max_sections]

        logger.info(
            "Article: title=%r, author=%r, sections=%d",
            article.metadata.title,
            article.metadata.author,
            len(article.sections),
        )

        # Determine output path
        suffix = ".m4b" if output_format == "m4b" else ".mp3"
        if output:
            output_path = output
        else:
            audiobooks_dir = Path("audiobooks")
            audiobooks_dir.mkdir(exist_ok=True)
            output_path = audiobooks_dir / article.output_filename().replace(
                ".epub", suffix
            )
        if output_path.suffix != suffix:
            output_path = output_path.with_suffix(suffix)

        generate_audiobook(
            article,
            output_path,
            voice=voice,
            speed=speed,
            output_format=output_format,
        )

    except ValueError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)
    except Exception as e:
        click.echo(f"Unexpected error: {e}\nRun with -v for the full traceback.", err=True)
        if verbose:
            raise
        sys.exit(1)


# ---------------------------------------------------------------------------
# web subcommand
# ---------------------------------------------------------------------------


@main.command("web")
@click.option(
    "--host", default="127.0.0.1", show_default=True, help="Host address to bind to"
)
@click.option("--port", default=8080, show_default=True, help="Port to listen on")
@click.option(
    "--open/--no-open",
    "open_browser",
    default=True,
    help="Open browser automatically",
)
@click.option("--debug", is_flag=True, help="Enable Flask debug mode")
def web_cmd(host: str, port: int, open_browser: bool, debug: bool) -> None:
    """Start a web interface for reed.

    Launches a local web server with a browser-based UI for converting
    articles to EPUBs and audiobooks.
    """
    import os
    import socket

    from .web import create_app, install_shutdown_hooks

    install_shutdown_hooks()
    app = create_app(debug=debug)

    # 0.0.0.0 / :: are bind-all addresses, not browsable destinations — point
    # the displayed and auto-opened URL at loopback instead.
    browse_host = "127.0.0.1" if host in ("0.0.0.0", "::") else host
    url = f"http://{browse_host}:{port}"

    # Flask's debug reloader runs this command twice: a supervisor process
    # (which binds the socket) and a worker with WERKZEUG_RUN_MAIN=true (which
    # inherits it and serves). Probe/announce/open only from the initial launch
    # — before anything binds — so we don't print twice or probe a port the
    # supervisor already holds.
    is_main_launch = os.environ.get("WERKZEUG_RUN_MAIN") != "true"

    if is_main_launch:
        # Probe the port up front so a conflict fails with a clear message
        # instead of leaving the browser hanging on a socket nothing serves.
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind((host, port))
        except OSError:
            raise click.ClickException(
                f"Port {port} is already in use — another server (maybe a "
                f"previous 'reed web') is still running.\n"
                f"Stop it, or start on a different port with --port."
            )
        finally:
            probe.close()

        click.echo(f"Starting reed web interface on {url}")
        if browse_host != host:
            click.echo(
                f"Listening on all interfaces ({host}:{port}) — reachable from "
                f"other devices at http://<this-machine-ip>:{port}"
            )
        click.echo("Press Ctrl+C to stop.")
        if open_browser:
            webbrowser.open(url)

    app.run(host=host, port=port, debug=debug)


# ---------------------------------------------------------------------------
# markdown subcommand
# ---------------------------------------------------------------------------


@main.command("markdown")
@click.option(
    "-o", "--output", type=click.Path(path_type=Path), help="Output Markdown path"
)
@click.option(
    "--html",
    "html_file",
    type=click.Path(exists=True, path_type=Path),
    help="Use a local HTML file",
)
@click.option(
    "--md",
    "md_file",
    type=click.Path(exists=True, path_type=Path),
    help="Use a local Markdown file",
)
@click.option("--verbose", "-v", is_flag=True, help="Show detailed progress")
def markdown_cmd(
    output: Path | None,
    html_file: Path | None,
    md_file: Path | None,
    verbose: bool,
) -> None:
    """Generate a Markdown file from an article.

    \b
    Use a local HTML or Markdown file:
        reed markdown --html article.html
        reed markdown --md article.md
    """
    _setup_logging(verbose)

    try:
        article = _resolve_article(html_file=html_file, md_file=md_file)

        logger.info(
            "Article: title=%r, author=%r, sections=%d",
            article.metadata.title,
            article.metadata.author,
            len(article.sections),
        )

        if output:
            output_path = output
        else:
            articles_dir = Path("articles")
            articles_dir.mkdir(exist_ok=True)
            output_path = articles_dir / article.output_filename().replace(
                ".epub", ".md"
            )
        if output_path.suffix != ".md":
            output_path = output_path.with_suffix(".md")

        generate_markdown(article, output_path)
        click.echo(f"✓ Markdown generated: {output_path}")

    except ValueError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)
    except Exception as e:
        click.echo(f"Unexpected error: {e}\nRun with -v for the full traceback.", err=True)
        if verbose:
            raise
        sys.exit(1)


# ---------------------------------------------------------------------------
# demo subcommand
# ---------------------------------------------------------------------------


@main.command("demo")
@click.option(
    "--output-dir",
    "output_dir",
    type=click.Path(path_type=Path),
    default=Path("reed-demo"),
    show_default=True,
    help="Directory for the generated demo files",
)
@click.option(
    "--voice",
    type=str,
    default="af_heart",
    show_default=True,
    help="Kokoro voice for the audiobook",
)
@click.option(
    "--speed",
    type=float,
    default=1.0,
    show_default=True,
    help="Playback speed (0.5-2.0). 0.85 = 15%% slower, 1.0 = normal.",
)
@click.option(
    "--max-chunks",
    type=int,
    default=0,
    help="Only narrate the first N chunks (0 = all). Quick-test shortcut.",
)
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["mp3", "m4b"]),
    default="m4b",
    show_default=True,
    help="Audiobook container: m4b (chaptered from headings) or mp3 (flat).",
)
@click.option(
    "--no-audiobook",
    "no_audiobook",
    is_flag=True,
    help="Generate only EPUB and Markdown (skips Kokoro and ffmpeg).",
)
@click.option("--verbose", "-v", is_flag=True, help="Show detailed progress")
def demo_cmd(
    output_dir: Path,
    voice: str,
    speed: float,
    max_chunks: int,
    output_format: str,
    no_audiobook: bool,
    verbose: bool,
) -> None:
    """Generate EPUB, Markdown, and an MP3 or M4B audiobook from a sample.

    \b
    A zero-setup way to try every reed output format:
        reed demo
        reed demo --no-audiobook
        reed demo --voice af_bella --speed 0.85
        reed demo --format mp3
    """
    _setup_logging(verbose)

    try:
        sample = _sample_article_path()
        if not sample.is_file():
            raise ValueError(
                f"Bundled demo article not found: {sample}\n"
                "Reinstall reed (uv tool install --force reed-cli) to restore it."
            )
        logger.info("Demo sample article: %s", sample)
        article = extract_from_markdown(sample)

        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        epub_path = output_dir / article.output_filename()
        generate_epub(article, epub_path)
        click.echo(f"✓ EPUB: {epub_path}")

        md_path = epub_path.with_suffix(".md")
        generate_markdown(article, md_path)
        click.echo(f"✓ Markdown: {md_path}")

        suffix = ".m4b" if output_format == "m4b" else ".mp3"
        mp3_path = epub_path.with_suffix(suffix)
        if no_audiobook:
            click.echo("○ Audiobook: skipped (--no-audiobook)")
        else:
            if speed < 0.5 or speed > 2.0:
                raise ValueError("--speed must be between 0.5 and 2.0")

            missing = _check_system_binaries()
            if missing:
                click.echo(_system_dependency_hint(missing), err=True)
                click.echo("Audiobook dependencies are missing — run `reed doctor`.", err=True)
                sys.exit(1)

            # Lazy import — pulls in numpy, soundfile, kokoro (heavy)
            from .outputs import generate_audiobook

            generate_audiobook(
                article,
                mp3_path,
                voice=voice,
                speed=speed,
                max_chunks=max_chunks,
                output_format=output_format,
            )

        click.echo(f"\n✓ Demo complete — files are in {output_dir}")
    except ValueError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)
    except Exception as e:
        click.echo(f"Unexpected error: {e}\nRun with -v for the full traceback.", err=True)
        if verbose:
            raise
        sys.exit(1)


if __name__ == "__main__":
    main()
