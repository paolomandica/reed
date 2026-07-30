"""CLI entry point for reed."""

import logging
import sys
import webbrowser
from pathlib import Path

import click

from .inputs import extract_from_html, extract_from_markdown
from .models import Article
from .outputs import generate_epub, generate_markdown

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
        logging.basicConfig(level=logging.INFO, format="%(message)s")


# ---------------------------------------------------------------------------
# Click group
# ---------------------------------------------------------------------------


@click.group(invoke_without_command=True)
@click.version_option(version="0.1.0", prog_name="reed")
@click.pass_context
def main(ctx: click.Context) -> None:
    """Convert articles to ebooks and audiobooks.

    \b
    Commands:
      epub        Generate a Kindle-compatible EPUB
      audiobook   Generate an MP3 audiobook using Chatterbox
      markdown    Generate a Markdown file
      web         Start a browser-based web interface

    \b
    Examples:
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
        click.echo(f"Unexpected error: {e}", err=True)
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
    help="Output audio file path (default: <title-slug>.mp3)",
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
    verbose: bool,
    max_sections: int = 0,
) -> None:
    """Generate an MP3 audiobook from an article using Kokoro-82M TTS.

    \b
    Kokoro-82M is a lightweight (82M params) open-weight TTS model with
    20 American English voices.  Requires the espeak-ng system package.

    The model is downloaded from Hugging Face on first run and cached locally.

    \b
    Examples:
        reed audiobook --html article.html
        reed audiobook --html article.html --voice af_bella
        reed audiobook --md article.md --speed 0.85
        reed audiobook -o out.mp3 --html article.html
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
        if output:
            output_path = output
        else:
            audiobooks_dir = Path("audiobooks")
            audiobooks_dir.mkdir(exist_ok=True)
            output_path = audiobooks_dir / article.output_filename().replace(
                ".epub", ".mp3"
            )
        if output_path.suffix != ".mp3":
            output_path = output_path.with_suffix(".mp3")

        generate_audiobook(
            article,
            output_path,
            voice=voice,
            speed=speed,
        )

    except ValueError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)
    except Exception as e:
        click.echo(f"Unexpected error: {e}", err=True)
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

    from .web import create_app

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
        click.echo(f"Unexpected error: {e}", err=True)
        if verbose:
            raise
        sys.exit(1)


if __name__ == "__main__":
    main()
