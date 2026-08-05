"""Flask web server for reed.

Provides a browser-based interface to convert articles to EPUBs,
audiobooks, and Markdown files.  Run with ``reed web``.

Routes:
    GET  /                   Serve the static frontend
    GET  /api/models         List available TTS models
    GET  /api/preview        Short cached voice sample (voice, speed)
    POST /api/generate       Start generation, return task ID
    POST /api/demo           Start all three formats from the bundled sample
    GET  /api/task/<id>      Poll task status / progress
    GET  /api/download/<id>  Download completed file
    POST /api/task/<id>/stop Cancel a running task
"""

import atexit
import logging
import signal
import tempfile
import threading
import time
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

# Serializes voice-preview synthesis so concurrent requests don't load the
# model or write the same cache file twice.
_preview_lock = threading.Lock()

# ---------------------------------------------------------------------------
# Task lifecycle (expiry + cleanup)
# ---------------------------------------------------------------------------

_TASK_TTL_SECONDS = 60 * 60  # finished results older than this are swept
_AUDIOBOOK_CONCURRENCY = 1
_audiobook_slot = threading.BoundedSemaphore(_AUDIOBOOK_CONCURRENCY)


def _remove_task_output(task: dict) -> None:
    """Unlink a completed task's temp file, if any."""
    output_path = task.get("output_path")
    if output_path:
        try:
            Path(output_path).unlink()
        except OSError:
            pass


def _sweep_expired_tasks() -> None:
    """Remove finished tasks older than the TTL and delete their temp files."""
    now = time.time()
    with _task_lock:
        expired = [
            task_id
            for task_id, task in _task_store.items()
            if task["status"] in ("done", "error", "cancelled")
            and now - task.get("created_at", 0) > _TASK_TTL_SECONDS
        ]
        for task_id in expired:
            task = _task_store.pop(task_id)
            _remove_task_output(task)


def _cleanup_all_tasks() -> None:
    """Cancel running tasks and delete every temp file (shutdown path)."""
    with _task_lock:
        running = [t for t in _task_store.values() if t["status"] == "generating"]
    for task in running:
        cancel_event = task.get("cancel_event")
        if cancel_event:
            cancel_event.set()
    with _task_lock:
        for task in list(_task_store.values()):
            _remove_task_output(task)
        _task_store.clear()


def install_shutdown_hooks() -> None:
    """Clean up tasks on interpreter exit and on SIGTERM."""
    atexit.register(_cleanup_all_tasks)

    def _on_term(signum: int, frame: object) -> None:
        _cleanup_all_tasks()
        raise KeyboardInterrupt

    try:
        signal.signal(signal.SIGTERM, _on_term)
    except (ValueError, OSError):
        pass  # Not on the main thread (e.g. debug reloader worker).


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
    release_slot: bool = False,
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
                suffix=".m4b", delete=False
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
            download_name = article.output_filename().replace(".epub", ".m4b")
            mime = "audio/mp4"

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
        if release_slot:
            _audiobook_slot.release()
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
    # -- sweep stale results before accepting new work ----------------------
    _sweep_expired_tasks()

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
            fname_lower = (uploaded.filename or "").lower()
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
            "created_at": time.time(),
            "output_path": None,
            "download_name": "",
            "mime": "",
            "error": "",
            "cancel_event": cancel_event,
        }

    # One audiobook at a time — TTS loads the model into memory per run.
    release_slot = False
    if fmt == "audiobook":
        if not _audiobook_slot.acquire(blocking=False):
            with _task_lock:
                _task_store.pop(task_id, None)
            return (
                jsonify(
                    {
                        "error": (
                            "Another audiobook is already generating. "
                            "Try again when it finishes."
                        )
                    }
                ),
                429,
            )
        release_slot = True

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
            "release_slot": release_slot,
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
        """Return the list of available TTS models and voices.

        ``voices`` is a flat list of IDs; ``catalog`` adds per-voice metadata
        (grade, gender) used by the debug UI to audition every voice.
        """
        try:
            from .outputs.audiobook import _KOKORO_VOICES, kokoro_voice_catalog

            catalog = kokoro_voice_catalog()
        except ImportError:
            _KOKORO_VOICES = ["af_heart", "af_bella"]
            catalog = [{"id": v, "grade": "", "gender": ""} for v in _KOKORO_VOICES]
        return jsonify(
            {
                "voices": _KOKORO_VOICES,
                "catalog": catalog,
            }
        )

    @app.route("/api/preview")
    def preview_voice():
        """Return a short cached sample clip for a voice at a given speed.

        Query params:
          - voice: Kokoro voice name (must be a known voice)
          - speed: playback speed, 0.5–2.0 (default 1.0)

        The clip is a fixed sentence, so once generated it is cached on disk
        and reused on subsequent requests.
        """
        try:
            from .outputs.audiobook import _KOKORO_VOICES, generate_voice_preview
        except ImportError as exc:
            return jsonify({"error": f"audiobook support unavailable: {exc}"}), 503

        voice = (request.args.get("voice") or "af_heart").strip()
        if voice not in _KOKORO_VOICES:
            return jsonify({"error": f"unknown voice: {voice}"}), 400

        try:
            speed = float((request.args.get("speed") or "1.0").strip())
        except (ValueError, TypeError):
            return jsonify({"error": "speed must be a number"}), 400
        if speed < 0.5 or speed > 2.0:
            return jsonify({"error": "speed must be between 0.5 and 2.0"}), 400

        try:
            with _preview_lock:
                clip_path = generate_voice_preview(voice, speed=speed)
        except Exception as exc:  # noqa: BLE001 — surface any synthesis failure
            logger.exception("Voice preview failed for %s @ %sx", voice, speed)
            return jsonify({"error": str(exc)}), 500

        resp = send_file(clip_path, mimetype="audio/mpeg", conditional=True)
        resp.headers["Cache-Control"] = "public, max-age=86400"
        return resp

    @app.route("/api/generate", methods=["POST"])
    def generate():
        """Start generation and return a task ID for polling."""
        return _handle_generate()

    @app.route("/api/demo", methods=["POST"])
    def demo():
        """Start all three output formats from the bundled sample article.

        Returns ``{"tasks": [{"format": ..., "task_id": ...}, ...]}`` with one
        task per format (EPUB, Markdown, audiobook), pollable via
        ``/api/task/<id>`` and downloadable once done.
        """
        _sweep_expired_tasks()

        try:
            from .sample import sample_article_path

            sample = sample_article_path()
            if not sample.is_file():
                return jsonify({"error": "Bundled demo article not found."}), 500
            article = extract_from_markdown(sample)
        except Exception as exc:
            logger.exception("Demo sample could not be loaded")
            return jsonify({"error": str(exc)}), 500

        if not _audiobook_slot.acquire(blocking=False):
            return (
                jsonify(
                    {
                        "error": (
                            "Another audiobook is already generating. "
                            "Try again when it finishes."
                        )
                    }
                ),
                429,
            )

        tasks: list[dict[str, str]] = []
        try:
            for fmt in ("epub", "markdown", "audiobook"):
                task_id = str(uuid.uuid4())
                cancel_event = threading.Event()
                with _task_lock:
                    _task_store[task_id] = {
                        "status": "generating",
                        "progress": 0,
                        "message": "Starting…",
                        "created_at": time.time(),
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
                        "source_type": "paste",
                        "article": article,
                        "release_slot": fmt == "audiobook",
                    },
                    daemon=True,
                )
                thread.start()
                tasks.append({"format": fmt, "task_id": task_id})
        except Exception:
            # Never leave the audiobook slot held or half-created tasks behind.
            _audiobook_slot.release()
            with _task_lock:
                for task in tasks:
                    _task_store.pop(task["task_id"], None)
            raise

        return jsonify({"tasks": tasks}), 202

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
        _sweep_expired_tasks()
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
