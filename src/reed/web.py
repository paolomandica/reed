"""Flask web server for reed.

Provides a browser-based interface to convert X.com articles to EPUBs
and audiobooks.  Run with ``reed web``.

Routes:
    GET  /              Serve the static frontend
    GET  /api/models    List available TTS models
    POST /api/generate  Generate EPUB or audiobook, return as download
"""

import logging
import tempfile
from io import BytesIO
from pathlib import Path

from flask import Flask, jsonify, request, send_file, send_from_directory

from .inputs import extract_from_html, extract_from_markdown, extract_from_url
from .inputs.browser import derive_filename, download_article_html
from .outputs import generate_audiobook, generate_epub, generate_markdown

logger = logging.getLogger(__name__)

_HERE = Path(__file__).parent
STATIC_DIR = _HERE / "static"



# ---------------------------------------------------------------------------
# Request handler
# ---------------------------------------------------------------------------


def _handle_generate() -> tuple:
    """Validate inputs, run the reed pipeline, and return a file response.

    Returns a ``(response, status_code)`` tuple suitable for Flask.
    """
    # -- validate source_type -------------------------------------------------
    source_type = (request.form.get("source_type") or "").strip().lower()
    if source_type not in ("url", "file"):
        return (
            jsonify({"error": "source_type must be 'url' or 'file'"}),
            400,
        )

    # -- validate format ------------------------------------------------------
    fmt = (request.form.get("format") or "").strip().lower()
    if fmt not in ("epub", "audiobook", "markdown", "fetch"):
        return (
            jsonify({"error": "format must be 'epub', 'audiobook', 'markdown', or 'fetch'"}),
            400,
        )

    # -- handle reference audio (audiobook only, optional) -------------------
    ref_audio_tmp: Path | None = None
    if fmt == "audiobook":
        ref_audio = request.files.get("reference_audio")
        if ref_audio is not None and ref_audio.filename != "":
            with tempfile.NamedTemporaryFile(
                suffix=".wav", delete=False
            ) as tmp:
                ref_audio.save(tmp.name)
                ref_audio_tmp = Path(tmp.name)

    # -- fetch format: download rendered HTML directly -------------------------
    if fmt == "fetch":
        if source_type != "url":
            return (
                jsonify({"error": "Fetch format requires source_type 'url' (not file)."}),
                400,
            )
        url = (request.form.get("url") or "").strip()
        if not url:
            return jsonify({"error": "url is required for fetch format."}), 400
        try:
            html = download_article_html(url)
        except RuntimeError as exc:
            return jsonify({"error": str(exc)}), 400

        download_name = derive_filename(url)
        return send_file(
            BytesIO(html.encode("utf-8")),
            mimetype="text/html",
            as_attachment=True,
            download_name=download_name,
        )

    # -- resolve article ------------------------------------------------------
    tmp_html: Path | None = None

    try:
        if source_type == "url":
            url = (request.form.get("url") or "").strip()
            if not url:
                return jsonify({"error": "url is required when source_type is 'url'"}), 400
            article = extract_from_url(url)

        else:  # file
            uploaded = request.files.get("file")
            if uploaded is None or uploaded.filename == "":
                return (
                    jsonify({"error": "file is required when source_type is 'file'"}),
                    400,
                )
            fname_lower = uploaded.filename.lower()
            if not (fname_lower.endswith((".html", ".htm")) or fname_lower.endswith(".md")):
                return (
                    jsonify(
                        {
                            "error": (
                                f"File must be an HTML file (.html/.htm) or "
                                f"Markdown file (.md), got: {uploaded.filename}"
                            )
                        }
                    ),
                    400,
                )

            # Save uploaded file to a temp location so extractors can read it
            is_md = fname_lower.endswith(".md")
            suffix = ".md" if is_md else ".html"
            with tempfile.NamedTemporaryFile(
                suffix=suffix, delete=False
            ) as tmp:
                uploaded.save(tmp.name)
                tmp_html = Path(tmp.name)

            if is_md:
                article = extract_from_markdown(tmp_html)
            else:
                article = extract_from_html(tmp_html)

    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    finally:
        if tmp_html is not None:
            try:
                tmp_html.unlink()
            except OSError:
                pass
        if ref_audio_tmp is not None:
            try:
                ref_audio_tmp.unlink()
            except OSError:
                pass

    # -- generate output ------------------------------------------------------
    output_tmp: Path | None = None
    output_bytes: BytesIO | None = None

    try:
        if fmt == "epub":
            with tempfile.NamedTemporaryFile(
                suffix=".epub", delete=False
            ) as tmp:
                output_tmp = Path(tmp.name)

            generate_epub(article, output_tmp)

            output_bytes = BytesIO(output_tmp.read_bytes())
            download_name = article.output_filename()
            mime = "application/epub+zip"

        elif fmt == "markdown":
            with tempfile.NamedTemporaryFile(
                suffix=".md", delete=False, mode="w", encoding="utf-8"
            ) as tmp:
                output_tmp = Path(tmp.name)

            generate_markdown(article, output_tmp)

            output_bytes = BytesIO(output_tmp.read_bytes())
            download_name = article.output_filename().replace(".epub", ".md")
            mime = "text/markdown"

        else:  # audiobook
            with tempfile.NamedTemporaryFile(
                suffix=".mp3", delete=False
            ) as tmp:
                output_tmp = Path(tmp.name)

            generate_audiobook(
                article,
                output_tmp,
                reference_audio_path=str(ref_audio_tmp) if ref_audio_tmp else "",
                device="mps",
            )

            output_bytes = BytesIO(output_tmp.read_bytes())
            download_name = article.output_filename().replace(".epub", ".mp3")
            mime = "audio/mpeg"

    except (RuntimeError, ValueError) as exc:
        return jsonify({"error": str(exc)}), 500
    except Exception as exc:
        logger.exception("Unexpected error during generation")
        return (
            jsonify({"error": f"An unexpected error occurred: {exc}"}),
            500,
        )
    finally:
        if output_tmp is not None:
            try:
                output_tmp.unlink()
            except OSError:
                pass

    return send_file(
        output_bytes,
        mimetype=mime,
        as_attachment=True,
        download_name=download_name,
    )


# ---------------------------------------------------------------------------
# Application factory
# ---------------------------------------------------------------------------


def create_app() -> Flask:
    """Create and configure the Flask application."""
    app = Flask(__name__)

    # Reasonable upload limit for saved X.com HTML pages
    app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024  # 16 MiB

    @app.route("/")
    def index():
        """Serve the single-page frontend."""
        return send_from_directory(STATIC_DIR, "index.html")

    @app.route("/api/models")
    def get_models():
        """Return the list of available TTS models."""
        return jsonify(
            [
                {
                    "id": "chatterbox",
                    "name": "Chatterbox Turbo",
                    "max_chars": 2000,
                }
            ]
        )

    @app.route("/api/generate", methods=["POST"])
    def generate():
        """Generate an EPUB or audiobook and return the file as a download."""
        return _handle_generate()

    return app
