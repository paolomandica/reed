"""Flask web server for reed.

Provides a browser-based interface to convert articles to EPUBs,
audiobooks, and Markdown files.  Run with ``reed web``.

Routes:
    GET  /                   Serve the static frontend
    GET  /api/models         List available TTS models
    POST /api/generate       Start generation, return task ID
    GET  /api/task/<id>      Poll task status / progress
    GET  /api/download/<id>  Download completed file
"""

import logging
import tempfile
import threading
import uuid
from io import BytesIO
from pathlib import Path

from flask import Flask, jsonify, request, send_file, send_from_directory

from .inputs import extract_from_html, extract_from_markdown
from .models import Article
from .outputs import generate_audiobook, generate_epub, generate_markdown

logger = logging.getLogger(__name__)

_HERE = Path(__file__).parent
STATIC_DIR = _HERE / "static"

# ---------------------------------------------------------------------------
# In-memory task store (thread-safe)
# ---------------------------------------------------------------------------

_task_store: dict[str, dict] = {}
_task_lock = threading.Lock()


def _make_progress_callback(task_id: str):
    """Return a callback that updates the task store with chunk progress."""

    def cb(current: int, total: int, message: str) -> None:
        pct = int(current / total * 100) if total > 0 else 0
        with _task_lock:
            t = _task_store.get(task_id)
            if t:
                t["progress"] = pct
                t["message"] = message

    return cb


# ---------------------------------------------------------------------------
# Background generation (runs in a thread)
# ---------------------------------------------------------------------------


def _run_generation(
    task_id: str,
    *,
    fmt: str,
    source_type: str,
    article: Article,
    speed: float = 1.0,
    voice: str = "af_heart",
    max_chunks: int = 0,
) -> None:
    """Run the output generation in a background thread.

    Updates the task store on completion or failure.
    """
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

            cancel_event = _task_store.get(task_id, {}).get("cancel_event")

            generate_audiobook(
                article,
                output_tmp,
                voice=voice,
                speed=speed,
                max_chunks=max_chunks,
                progress_callback=_make_progress_callback(task_id),
                cancel_check=(lambda: cancel_event.is_set()) if cancel_event else None,
            )

            output_bytes = BytesIO(output_tmp.read_bytes())
            download_name = article.output_filename().replace(".epub", ".mp3")
            mime = "audio/mpeg"

        # Store the result file for later download
        with tempfile.NamedTemporaryFile(
            suffix=Path(download_name).suffix,
            delete=False,
        ) as result_file:
            result_file.write(output_bytes.getvalue())
            result_path = Path(result_file.name)

        with _task_lock:
            t = _task_store.get(task_id)
            if t:
                t["status"] = "done"
                t["progress"] = 100
                t["output_path"] = str(result_path)
                t["download_name"] = download_name
                t["mime"] = mime

    except RuntimeError as exc:
        if str(exc) == "cancelled":
            logger.info("Generation cancelled for task %s", task_id)
            with _task_lock:
                t = _task_store.get(task_id)
                if t:
                    t["status"] = "cancelled"
                    t["message"] = "Cancelled by user."
        else:
            logger.exception("Generation failed for task %s", task_id)
            with _task_lock:
                t = _task_store.get(task_id)
                if t:
                    t["status"] = "error"
                    t["error"] = str(exc)
    except Exception as exc:
        logger.exception("Generation failed for task %s", task_id)
        with _task_lock:
            t = _task_store.get(task_id)
            if t:
                t["status"] = "error"
                t["error"] = str(exc)
    finally:
        # Clean up the output tmp file (we copied the bytes to the result file)
        if output_tmp is not None:
            try:
                output_tmp.unlink()
            except OSError:
                pass


# ---------------------------------------------------------------------------
# Request handler — validate & start background generation
# ---------------------------------------------------------------------------


def _handle_generate() -> tuple:
    """Validate inputs, start background generation, return task ID.

    Returns a ``(response, status_code)`` tuple suitable for Flask.
    """
    # -- validate format ------------------------------------------------------
    fmt = (request.form.get("format") or "").strip().lower()
    if fmt not in ("epub", "audiobook", "markdown"):
        return (
            jsonify({"error": "format must be 'epub', 'audiobook', or 'markdown'"}),
            400,
        )

    # -- parse speed / voice (audiobook only) -----------------------------------
    speed: float = 1.0
    voice: str = "af_heart"
    max_chunks: int = 0
    if fmt == "audiobook":
        try:
            speed_str = (request.form.get("speed") or "1.0").strip()
            speed = float(speed_str)
        except (ValueError, TypeError):
            return jsonify({"error": "speed must be a number"}), 400
        if speed < 0.5 or speed > 2.0:
            return jsonify({"error": "speed must be between 0.5 and 2.0"}), 400

        voice = (request.form.get("voice") or "af_heart").strip()

        try:
            max_chunks = int(request.form.get("max_chunks") or "0")
        except (ValueError, TypeError):
            max_chunks = 0

    # -- resolve article from uploaded file or pasted text -------------------
    source_type = (request.form.get("source_type") or "file").strip().lower()
    if source_type not in ("file", "paste"):
        return jsonify({"error": "source_type must be 'file' or 'paste'"}), 400

    tmp_html: Path | None = None

    try:
        if source_type == "paste":
            text = (request.form.get("text") or "").strip()
            if not text:
                return (
                    jsonify({"error": "text is required when source_type is 'paste'"}),
                    400,
                )
            with tempfile.NamedTemporaryFile(
                suffix=".md", mode="w", encoding="utf-8", delete=False
            ) as tmp:
                tmp.write(text)
                tmp_html = Path(tmp.name)

            article = extract_from_markdown(tmp_html)

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

    # -- start background generation -----------------------------------------
    task_id = str(uuid.uuid4())
    cancel_event = threading.Event()

    with _task_lock:
        _task_store[task_id] = {
            "status": "generating",
            "progress": 0,
            "message": "Starting…",
            "output_path": None,
            "download_name": "",
            "mime": "",
            "error": "",
            "cancel_event": cancel_event,
        }

    thread = threading.Thread(
        target=_run_generation,
        args=(task_id,),
        kwargs={
            "fmt": fmt,
            "source_type": source_type,
            "article": article,
            "speed": speed,
            "voice": voice,
            "max_chunks": max_chunks,
        },
        daemon=True,
    )
    thread.start()

    return jsonify({"task_id": task_id}), 202


# ---------------------------------------------------------------------------
# Application factory
# ---------------------------------------------------------------------------


def create_app(debug: bool = False) -> Flask:
    """Create and configure the Flask application."""
    app = Flask(__name__)

    app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024  # 16 MiB
    app.config["REED_DEBUG"] = debug

    @app.route("/")
    def index():
        """Serve the single-page frontend."""
        return send_from_directory(STATIC_DIR, "index.html")

    @app.route("/api/config")
    def get_config():
        """Return runtime configuration for the frontend."""
        return jsonify({"debug": app.config.get("REED_DEBUG", False)})

    @app.route("/api/models")
    def get_models():
        """Return the list of available TTS models and voices."""
        try:
            from .outputs.audiobook import _KOKORO_VOICES
        except ImportError:
            _KOKORO_VOICES = ["af_heart", "af_bella"]
        return jsonify(
            {
                "voices": _KOKORO_VOICES,
            }
        )

    @app.route("/api/generate", methods=["POST"])
    def generate():
        """Start generation and return a task ID for polling."""
        return _handle_generate()

    @app.route("/api/task/<task_id>")
    def get_task(task_id: str):
        """Poll for task progress.

        Returns a JSON object with:
          - status: "generating" | "done" | "error"
          - progress: 0–100
          - message: human-readable progress string
          - download_url: (when done) relative URL to download the file
          - error: (when error) error message
        """
        with _task_lock:
            task = _task_store.get(task_id)
        if not task:
            return jsonify({"error": "task not found"}), 404

        resp = {
            "status": task["status"],
            "progress": task["progress"],
            "message": task["message"],
        }
        if task["status"] == "done":
            resp["download_url"] = f"/api/download/{task_id}"
        if task["status"] == "error":
            resp["error"] = task["error"]
        if task["status"] == "cancelled":
            resp["cancelled"] = True
        return jsonify(resp)

    @app.route("/api/download/<task_id>")
    def download_task(task_id: str):
        """Serve a completed generation result as a file download."""
        with _task_lock:
            task = _task_store.pop(task_id, None)
        if not task or task["status"] != "done":
            return jsonify({"error": "task not found or not ready"}), 404

        output_path = task.get("output_path")
        if not output_path or not Path(output_path).exists():
            return jsonify({"error": "output file not found"}), 404

        return send_file(
            output_path,
            mimetype=task.get("mime", "application/octet-stream"),
            as_attachment=True,
            download_name=task.get("download_name", "download"),
        )

    @app.route("/api/task/<task_id>/stop", methods=["POST"])
    def stop_task(task_id: str):
        """Request cancellation of a running generation task."""
        with _task_lock:
            task = _task_store.get(task_id)
        if not task:
            return jsonify({"error": "task not found"}), 404
        if task["status"] != "generating":
            return jsonify({"error": "task is not running"}), 409

        cancel_event = task.get("cancel_event")
        if cancel_event:
            cancel_event.set()
        return jsonify({"status": "ok"})

    return app
