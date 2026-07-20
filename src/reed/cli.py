"""CLI entry point for reed."""

import logging
import sys
import tempfile
import webbrowser
from pathlib import Path

import click

from .inputs import extract_from_html, extract_from_markdown, extract_from_url
from .inputs.browser import (
    derive_filename,
    download_article_html,
    extract_text_from_html,
)
from .inputs.browser import save_auth as _browser_save_auth
from .models import Article
from .outputs import generate_audiobook, generate_epub, generate_markdown

logger = logging.getLogger(__name__)


def _resolve_article(
    url: str | None,
    html_file: Path | None = None,
    md_file: Path | None = None,
    browser_auth: Path | None = None,
    browser_headed: bool = False,
) -> Article:
    """Resolve an Article from a URL, a local HTML file, or a Markdown file.

    Exactly one of *url*, *html_file*, or *md_file* must be provided.

    For X Article URLs (long-form posts), automatically falls back to
    Playwright-based browser download if the API-based approach cannot
    retrieve the article body.
    """
    if html_file:
        logger.info("Extracting from HTML file: %s", html_file)
        return extract_from_html(html_file)

    if md_file:
        logger.info("Extracting from Markdown file: %s", md_file)
        return extract_from_markdown(md_file)

    if url:
        logger.info("Downloading: %s", url)
        try:
            return extract_from_url(url)
        except ValueError as exc:
            msg = str(exc)
            # If this looks like an X Article that needs a browser, try Playwright
            if ("X Article" in msg or "browser" in msg) and "html" in msg.lower():
                logger.info("API download failed, trying browser fallback...")
                try:
                    return _resolve_via_browser(url, browser_auth, browser_headed)
                except RuntimeError as browser_exc:
                    # Browser not available or failed — show original error
                    # plus browser install hint
                    click.echo(
                        f"Error: {exc}\n\n"
                        f"Browser fallback also failed: {browser_exc}",
                        err=True,
                    )
                    sys.exit(1)
            raise

    click.echo(
        "Error: Please provide either a URL, --html flag, or --md flag.\n\n"
        "Try: reed --help",
        err=True,
    )
    sys.exit(1)


def _resolve_via_browser(
    url: str,
    auth: Path | None,
    headed: bool,
) -> Article:
    """Download an X Article via Playwright and parse it with extract_from_html."""
    html = download_article_html(
        url,
        headed=headed,
        storage_state=str(auth) if auth else None,
    )

    # Write to temp file so extract_from_html can read it
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".html", encoding="utf-8", delete=False
    ) as tmp:
        tmp.write(html)
        tmp_path = Path(tmp.name)

    try:
        article = extract_from_html(tmp_path)
        # Patch the URL into metadata since extract_from_html can't get it
        article.metadata.url = url
        return article
    finally:
        try:
            tmp_path.unlink()
        except OSError:
            pass


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
    """Convert X.com articles to ebooks and audiobooks.

    \b
    Commands:
      epub        Generate a Kindle-compatible EPUB
      audiobook   Generate an MP3 audiobook using Chatterbox
      markdown    Generate a Markdown file
      fetch       Download fully-rendered HTML of an X.com article
      web         Start a browser-based web interface

    \b
    Examples:
      reed epub https://x.com/user/status/123
      reed epub --html saved_article.html
      reed epub --md article.md
      reed markdown https://x.com/user/status/123
      reed markdown --html saved_article.html
      reed audiobook --html saved_article.html
      reed audiobook --md article.md
      reed audiobook -o out.mp3 https://x.com/...
      reed fetch https://x.com/user/article/123
      reed fetch --save-auth cookies.json
      reed web
    """
    if ctx.invoked_subcommand is None:
        click.echo(ctx.get_help())
        return


# ---------------------------------------------------------------------------
# epub subcommand
# ---------------------------------------------------------------------------


@main.command("epub")
@click.argument("url", required=False)
@click.option(
    "-o", "--output", type=click.Path(path_type=Path), help="Output EPUB path"
)
@click.option(
    "--html",
    "html_file",
    type=click.Path(exists=True, path_type=Path),
    help="Use a local HTML file instead of downloading",
)
@click.option(
    "--md",
    "md_file",
    type=click.Path(exists=True, path_type=Path),
    help="Use a local Markdown file instead of downloading",
)
@click.option("--verbose", "-v", is_flag=True, help="Show detailed progress")
@click.option(
    "--auth",
    "browser_auth",
    type=click.Path(exists=True, path_type=Path),
    help="Playwright storage_state JSON for logged-in X.com sessions",
)
@click.option(
    "--headed",
    "browser_headed",
    is_flag=True,
    help="Run browser visibly (for debugging)",
)
def epub_cmd(
    url: str | None,
    output: Path | None,
    html_file: Path | None,
    md_file: Path | None,
    verbose: bool,
    browser_auth: Path | None = None,
    browser_headed: bool = False,
) -> None:
    """Generate a Kindle-compatible EPUB from an X.com article.

    \b
    URL should be a link to an X.com tweet or thread:
        reed epub https://x.com/user/status/123

    \b
    For X Articles (long-form posts), Playwright is used automatically:
        reed epub https://x.com/user/article/123

    \b
    If behind a login wall, save your session first:
        reed fetch --save-auth cookies.json
        reed epub --auth cookies.json https://x.com/...

    \b
    Use a local file instead of downloading:
        reed epub --html article.html
        reed epub --md article.md
    """
    _setup_logging(verbose)

    try:
        article = _resolve_article(
            url, html_file=html_file, md_file=md_file,
            browser_auth=browser_auth, browser_headed=browser_headed,
        )

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
@click.argument("url", required=False)
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
    help="Use a local HTML file instead of downloading",
)
@click.option(
    "--md",
    "md_file",
    type=click.Path(exists=True, path_type=Path),
    help="Use a local Markdown file instead of downloading",
)
@click.option(
    "-r",
    "--reference-audio",
    "reference_audio",
    type=click.Path(exists=True, path_type=Path),
    help="Reference audio clip for voice cloning (optional — model has a built-in default voice)",
)
@click.option(
    "-p",
    "--voice-prompt",
    "voice_prompt",
    type=click.Path(exists=True, path_type=Path),
    help="Pre-computed voice prompt (.pt) — skips audio loading / ASR",
)
@click.option(
    "--save-prompt",
    "save_prompt",
    type=click.Path(path_type=Path),
    help="Save the computed voice prompt to this .pt file for later reuse",
)
@click.option(
    "--device",
    type=click.Choice(["cpu", "cuda", "mps"]),
    default="mps",
    show_default=True,
    help="Device to run the TTS model on",
)
@click.option("--verbose", "-v", is_flag=True, help="Show detailed progress")
@click.option(
    "--auth",
    "browser_auth",
    type=click.Path(exists=True, path_type=Path),
    help="Playwright storage_state JSON for logged-in X.com sessions",
)
@click.option(
    "--headed",
    "browser_headed",
    is_flag=True,
    help="Run browser visibly (for debugging)",
)
@click.option(
    "--max-sections",
    type=int,
    default=0,
    help="Only process the first N sections (0 = all). Quick-test shortcut.",
)
def audiobook_cmd(
    url: str | None,
    output: Path | None,
    html_file: Path | None,
    md_file: Path | None,
    reference_audio: Path | None,
    voice_prompt: Path | None,
    save_prompt: Path | None,
    device: str,
    verbose: bool,
    max_sections: int = 0,
    browser_auth: Path | None = None,
    browser_headed: bool = False,
) -> None:
    """Generate an MP3 audiobook from an X.com article using Chatterbox.

    \b
    Uses a built-in default voice.  Pass -r (reference audio) or
    -p (cached voice prompt) for zero-shot voice cloning.
    The TTS model is downloaded on first run and cached locally.

    \b
    Examples:
        reed audiobook --html article.html
        reed audiobook --md article.md
        reed audiobook --html article.html -r voice.wav --save-prompt my_voice.pt
        reed audiobook --html article.html -p my_voice.pt
        reed audiobook -o out.mp3 --html article.html
    """
    _setup_logging(verbose)

    try:
        article = _resolve_article(
            url, html_file=html_file, md_file=md_file,
            browser_auth=browser_auth, browser_headed=browser_headed,
        )

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
            reference_audio_path=str(reference_audio) if reference_audio else "",
            voice_prompt_path=str(voice_prompt) if voice_prompt else "",
            save_prompt_path=str(save_prompt) if save_prompt else "",
            device=device,
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
    X.com articles to EPUBs and audiobooks.
    """
    from .web import create_app

    app = create_app()
    url = f"http://{host}:{port}"

    click.echo(f"Starting reed web interface on {url}")
    click.echo("Press Ctrl+C to stop.")
    if open_browser:
        webbrowser.open(url)
    app.run(host=host, port=port, debug=debug)


# ---------------------------------------------------------------------------
# fetch subcommand
# ---------------------------------------------------------------------------


@main.command("fetch")
@click.argument("url", required=False)
@click.option(
    "-o",
    "--output",
    type=click.Path(path_type=Path),
    help="Output HTML path (default: derived from article ID)",
)
@click.option(
    "--text",
    type=click.Path(path_type=Path),
    help="Also extract plain text to this file",
)
@click.option(
    "--auth",
    "browser_auth",
    type=click.Path(exists=True, path_type=Path),
    help="Playwright storage_state JSON for logged-in sessions",
)
@click.option(
    "--headed",
    is_flag=True,
    help="Run browser visibly (for debugging or manual login)",
)
@click.option(
    "--timeout",
    type=int,
    default=60_000,
    show_default=True,
    help="Navigation timeout in milliseconds",
)
@click.option(
    "--save-auth",
    type=click.Path(path_type=Path),
    help="Open browser for manual login, then save cookies to this path",
)
def fetch_cmd(
    url: str | None,
    output: Path | None,
    text: Path | None,
    browser_auth: Path | None,
    headed: bool,
    timeout: int,
    save_auth: Path | None,
) -> None:
    """Download fully-rendered HTML of an X.com article via headless browser.

    \b
    X Articles (x.com/<user>/article/<id>) are JavaScript-rendered.
    This command uses Playwright/Chromium to wait for the page to load
    and saves the complete rendered HTML.

    \b
    Examples:
        reed fetch https://x.com/user/article/123
        reed fetch https://x.com/... -o article.html --text article.txt
        reed fetch --save-auth cookies.json
        reed fetch --auth cookies.json https://x.com/...

    \b
    Requires: pip install reed[browser] && playwright install chromium
    """
    # --save-auth mode (no URL needed) -----------------------------------------
    if save_auth is not None:
        _browser_save_auth(save_auth)
        return

    # --validate URL -----------------------------------------------------------
    if not url:
        click.echo(
            "Error: Please provide a URL or use --save-auth.\n\n"
            "Try: reed fetch --help",
            err=True,
        )
        sys.exit(1)

    # --download ---------------------------------------------------------------
    try:
        html = download_article_html(
            url,
            headed=headed,
            timeout_ms=timeout,
            storage_state=str(browser_auth) if browser_auth else None,
        )
    except RuntimeError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    # --save HTML --------------------------------------------------------------
    output_path = output or Path(derive_filename(url))
    output_path.write_text(html, encoding="utf-8")
    size_kb = output_path.stat().st_size / 1024
    click.echo(f"✓ HTML saved: {output_path} ({size_kb:.0f} KB)")

    # --extract text (optional) ------------------------------------------------
    if text is not None:
        plain = extract_text_from_html(html)
        text.write_text(plain, encoding="utf-8")
        size_kb = text.stat().st_size / 1024
        click.echo(f"✓ Text saved: {text} ({size_kb:.0f} KB)")


# ---------------------------------------------------------------------------
# markdown subcommand
# ---------------------------------------------------------------------------


@main.command("markdown")
@click.argument("url", required=False)
@click.option(
    "-o", "--output", type=click.Path(path_type=Path), help="Output Markdown path"
)
@click.option(
    "--html",
    "html_file",
    type=click.Path(exists=True, path_type=Path),
    help="Use a local HTML file instead of downloading",
)
@click.option(
    "--md",
    "md_file",
    type=click.Path(exists=True, path_type=Path),
    help="Use a local Markdown file instead of downloading",
)
@click.option("--verbose", "-v", is_flag=True, help="Show detailed progress")
@click.option(
    "--auth",
    "browser_auth",
    type=click.Path(exists=True, path_type=Path),
    help="Playwright storage_state JSON for logged-in X.com sessions",
)
@click.option(
    "--headed",
    "browser_headed",
    is_flag=True,
    help="Run browser visibly (for debugging)",
)
def markdown_cmd(
    url: str | None,
    output: Path | None,
    html_file: Path | None,
    md_file: Path | None,
    verbose: bool,
    browser_auth: Path | None = None,
    browser_headed: bool = False,
) -> None:
    """Generate a Markdown file from an X.com article.

    \b
    URL should be a link to an X.com tweet or thread:
        reed markdown https://x.com/user/status/123

    \b
    For X Articles (long-form posts), Playwright is used automatically:
        reed markdown https://x.com/user/article/123

    \b
    Use a local file instead of downloading:
        reed markdown --html article.html
        reed markdown --md article.md
    """
    _setup_logging(verbose)

    try:
        article = _resolve_article(
            url, html_file=html_file, md_file=md_file,
            browser_auth=browser_auth, browser_headed=browser_headed,
        )

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
