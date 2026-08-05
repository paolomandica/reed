from __future__ import annotations

import time
import unittest
from unittest import mock

from reed import web


def _fake_run_generation(
    task_id: str,
    *,
    fmt: str,
    source_type: str,
    article: object,
    release_slot: bool = False,
    **kwargs: object,
) -> None:
    """Stand-in for web._run_generation that completes tasks instantly."""
    if release_slot:
        web._audiobook_slot.release()
    suffix = "epub" if fmt == "epub" else "md" if fmt == "markdown" else "mp3"
    with web._task_lock:
        task = web._task_store.get(task_id)
        if task is None:
            return
        task["status"] = "done"
        task["progress"] = 100
        task["message"] = "done"
        task["download_name"] = f"demo.{suffix}"
        task["mime"] = "application/octet-stream"
        task["output_path"] = None


class WebApiTests(unittest.TestCase):
    def setUp(self) -> None:
        with web._task_lock:
            web._task_store.clear()
        self.app = web.create_app(debug=False)
        self.client = self.app.test_client()

    def tearDown(self) -> None:
        web._cleanup_all_tasks()

    def _wait_for_done(self, task_id: str) -> dict:
        for _ in range(100):
            resp = self.client.get(f"/api/task/{task_id}")
            self.assertEqual(resp.status_code, 200)
            data = resp.get_json()
            if data["status"] in ("done", "error"):
                return data
            time.sleep(0.02)
        self.fail("task did not finish in time")

    def test_epub_generation_lifecycle(self) -> None:
        resp = self.client.post(
            "/api/generate",
            data={
                "format": "epub",
                "source_type": "paste",
                "text": "# Hello\n\nSome body text.",
            },
        )
        self.assertEqual(resp.status_code, 202)
        task_id = resp.get_json()["task_id"]

        data = self._wait_for_done(task_id)
        self.assertEqual(data["status"], "done")
        self.assertEqual(data["progress"], 100)

        download = self.client.get(data["download_url"])
        self.assertEqual(download.status_code, 200)
        self.assertIn("application/epub+zip", download.headers["Content-Type"])
        self.assertTrue(download.data.startswith(b"PK"))
        download.close()

    def test_unknown_format_is_rejected(self) -> None:
        resp = self.client.post(
            "/api/generate",
            data={"format": "docx", "source_type": "paste", "text": "Hello"},
        )
        self.assertEqual(resp.status_code, 400)

    def test_missing_file_is_rejected(self) -> None:
        resp = self.client.post(
            "/api/generate",
            data={"format": "epub", "source_type": "file"},
        )
        self.assertEqual(resp.status_code, 400)

    def test_unknown_task_returns_404(self) -> None:
        resp = self.client.get("/api/task/does-not-exist")
        self.assertEqual(resp.status_code, 404)

    def test_audiobook_concurrency_is_limited(self) -> None:
        acquired = web._audiobook_slot.acquire(blocking=False)
        self.assertTrue(acquired)
        try:
            resp = self.client.post(
                "/api/generate",
                data={
                    "format": "audiobook",
                    "source_type": "paste",
                    "text": "Hello.",
                },
            )
        finally:
            web._audiobook_slot.release()

        self.assertEqual(resp.status_code, 429)
        self.assertIn("already generating", resp.get_json()["error"])

    def test_expired_results_are_swept(self) -> None:
        task_id = "stale-task"
        with web._task_lock:
            web._task_store[task_id] = {
                "status": "done",
                "progress": 100,
                "message": "done",
                "created_at": time.time() - web._TASK_TTL_SECONDS - 1,
                "output_path": None,
                "download_name": "",
                "mime": "",
                "error": "",
                "cancel_event": None,
            }

        resp = self.client.get(f"/api/task/{task_id}")
        self.assertEqual(resp.status_code, 404)

    def test_demo_creates_all_three_formats(self) -> None:
        with mock.patch.object(web, "_run_generation", side_effect=_fake_run_generation):
            resp = self.client.post("/api/demo")

        self.assertEqual(resp.status_code, 202)
        tasks = resp.get_json()["tasks"]
        self.assertEqual(
            [task["format"] for task in tasks], ["epub", "markdown", "audiobook"]
        )

        for task in tasks:
            data = self._wait_for_done(task["task_id"])
            self.assertEqual(data["status"], "done")
            self.assertEqual(data["progress"], 100)

    def test_demo_rejected_when_audiobook_busy(self) -> None:
        acquired = web._audiobook_slot.acquire(blocking=False)
        self.assertTrue(acquired)
        try:
            with mock.patch.object(web, "_run_generation"):
                resp = self.client.post("/api/demo")
        finally:
            web._audiobook_slot.release()

        self.assertEqual(resp.status_code, 429)
        self.assertIn("already generating", resp.get_json()["error"])
        with web._task_lock:
            self.assertEqual(web._task_store, {})
