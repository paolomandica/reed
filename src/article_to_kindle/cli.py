"""CLI entry point for article-to-kindle."""

import logging
import sys
from pathlib import Path

import click

from .download import download_tweet
from .extract import extract_content
from .html_extractor import extract_from_html
from .epub import generate_epub
from .models import Article


@click.command()
@click.argument("url", required=False)
@click.option("-o", "--output", type=click.Path(path_type=Path), help="Output EPUB path")
@click.option("--html", "html_file", type=click.Path(exists=True, path_type=Path),
              help="Use a local HTML file instead of downloading")
@click.option("--verbose", "-v", is_flag=True, help="Show detailed progress")
@click.version_option(version="0.1.0", prog_name="article-to-kindle")
def main(
    url: str | None,
    output: Path | None,
    html_file: Path | None,
    verbose: bool,
) -> None:
    """Convert an X.com article to a Kindle-compatible EPUB.

    URL should be a link to an X.com tweet or thread, e.g.:

        https://x.com/username/status/123456789

    The tool downloads the article, extracts its content and metadata,
    and generates a Kindle-optimized EPUB file.

    For X Articles (long-form posts), use --html with a saved HTML file:

        article-to-kindle --html article.html
    """
    if verbose:
        logging.basicConfig(level=logging.INFO, format="%(message)s")

    logger = logging.getLogger(__name__)

    article: Article

    try:
        if html_file:
            logger.info("Extracting from HTML file: %s", html_file)
            article = extract_from_html(html_file)
        elif url:
            logger.info("Downloading: %s", url)
            downloaded = download_tweet(url)
            logger.info(
                "Downloaded: author=%s (@%s), text_len=%d, is_article=%s",
                downloaded.author_name,
                downloaded.author_handle,
                len(downloaded.text),
                downloaded.is_article,
            )
            article = extract_content(downloaded)
        else:
            click.echo(
                "Error: Please provide either a URL or --html flag.\n\n"
                "Try: article-to-kindle --help",
                err=True,
            )
            sys.exit(1)

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


if __name__ == "__main__":
    main()
