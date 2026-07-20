"""CLI entry point for reed."""

import logging
import sys
from pathlib import Path

import click

from .inputs import extract_from_url, extract_from_html
from .outputs import generate_epub, generate_audiobook
from .models import Article
from .outputs.audiobook import select_model

logger = logging.getLogger(__name__)


def _resolve_article(
    url: str | None,
    html_file: Path | None,
) -> Article:
    """Resolve an Article from either a URL or a local HTML file.

    Exactly one of *url* or *html_file* must be provided.
    """
    if html_file:
        logger.info("Extracting from HTML file: %s", html_file)
        return extract_from_html(html_file)

    if url:
        logger.info("Downloading: %s", url)
        return extract_from_url(url)

    click.echo(
        "Error: Please provide either a URL or --html flag.\n\n"
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
    """Convert X.com articles to ebooks and audiobooks.

    \b
    Commands:
      epub        Generate a Kindle-compatible EPUB
      audiobook   Generate an MP3 audiobook using Hugging Face TTS

    \b
    Examples:
      reed epub https://x.com/user/status/123
      reed epub --html saved_article.html
      reed audiobook --html saved_article.html
      reed audiobook -o out.mp3 https://x.com/...
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
@click.option("--verbose", "-v", is_flag=True, help="Show detailed progress")
def epub_cmd(
    url: str | None,
    output: Path | None,
    html_file: Path | None,
    verbose: bool,
) -> None:
    """Generate a Kindle-compatible EPUB from an X.com article.

    \b
    URL should be a link to an X.com tweet or thread:
        reed epub https://x.com/user/status/123

    \b
    For X Articles (long-form posts), use --html:
        reed epub --html article.html
    """
    _setup_logging(verbose)

    try:
        article = _resolve_article(url, html_file)

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
@click.option("--verbose", "-v", is_flag=True, help="Show detailed progress")
def audiobook_cmd(
    url: str | None,
    output: Path | None,
    html_file: Path | None,
    verbose: bool,
) -> None:
    """Generate an MP3 audiobook from an X.com article using Hugging Face TTS.

    \b
    Requires the HF_TOKEN environment variable to be set.
    You will be prompted to choose a TTS model interactively.

    \b
    Examples:
        reed audiobook https://x.com/user/status/123
        reed audiobook --html article.html
        reed audiobook -o my-article.mp3 --html article.html
    """
    _setup_logging(verbose)

    try:
        article = _resolve_article(url, html_file)

        logger.info(
            "Article: title=%r, author=%r, sections=%d",
            article.metadata.title,
            article.metadata.author,
            len(article.sections),
        )

        # Interactive model selection
        model = select_model()

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

        generate_audiobook(article, model, output_path)

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
