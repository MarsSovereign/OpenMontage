"""HTTP client for a self-hosted MiniMax-H3 Studio.

The Studio is a single-GPU job server: ``POST /api/jobs`` (multipart form)
queues a job, ``GET /api/jobs/{id}`` reports its status, and finished files are
served under ``/files/``. One GPU runs the queue in order, so a job may wait
behind others; :meth:`H3StudioClient.wait` keeps polling until it finishes or
the caller's timeout passes, and the job keeps running in the Studio either way.

Configuration:
    H3_STUDIO_URL    Studio address, e.g. http://gpu-host:8190 (required)
    H3_STUDIO_TOKEN  bearer token when the Studio sets H3_API_TOKEN (optional)
"""

from __future__ import annotations

import os
import time
from contextlib import ExitStack
from pathlib import Path
from typing import Any

import requests

SETUP = (
    "Deploy MiniMax-H3 Studio (https://github.com/MarsSovereign/minimax-h3-studio) "
    "on a GPU machine and set H3_STUDIO_URL (e.g. http://gpu-host:8190). "
    "Set H3_STUDIO_TOKEN too when the Studio uses H3_API_TOKEN."
)
FINISHED = ("done", "failed", "canceled")


class H3StudioError(RuntimeError):
    """A Studio request or job failed; ``job_id`` is set when a job exists."""

    def __init__(self, message: str, job_id: str | None = None):
        super().__init__(message)
        self.job_id = job_id


class H3StudioTimeout(H3StudioError):
    """The job was still queued or running when the caller stopped waiting."""


def configured() -> bool:
    return bool(os.environ.get("H3_STUDIO_URL", "").strip())


class H3StudioClient:
    def __init__(self, base_url: str | None = None, token: str | None = None, request_timeout: float = 60.0):
        base = (base_url or os.environ.get("H3_STUDIO_URL", "")).strip().rstrip("/")
        if not base:
            raise H3StudioError(f"H3_STUDIO_URL is not set. {SETUP}")
        self.base_url = base
        token = token if token is not None else os.environ.get("H3_STUDIO_TOKEN", "")
        self.headers = {"Authorization": f"Bearer {token.strip()}"} if token and token.strip() else {}
        self.request_timeout = request_timeout

    # --- HTTP -------------------------------------------------------------

    def _request(self, method: str, path: str, **kwargs: Any) -> requests.Response:
        try:
            response = requests.request(
                method,
                f"{self.base_url}{path}",
                headers=self.headers,
                timeout=kwargs.pop("timeout", self.request_timeout),
                **kwargs,
            )
        except requests.RequestException as exc:
            raise H3StudioError(f"Cannot reach the Studio at {self.base_url}: {exc}") from exc
        if response.status_code >= 400:
            try:
                message = response.json().get("error") or response.text
            except ValueError:
                message = response.text
            raise H3StudioError(f"Studio returned HTTP {response.status_code}: {message.strip()[:500]}")
        return response

    def health(self) -> dict[str, Any]:
        return self._request("get", "/api/health", timeout=10).json()

    def presets(self) -> dict[str, Any]:
        return self._request("get", "/api/presets").json()

    def job(self, job_id: str) -> dict[str, Any]:
        return self._request("get", f"/api/jobs/{job_id}").json()

    def submit(self, fields: dict[str, Any], files: list[tuple[str, str | Path]] = ()) -> dict[str, Any]:
        """Queue a job. ``fields`` are form values (None is skipped); ``files`` are (field, path) pairs."""
        data = [(key, str(value)) for key, value in fields.items() if value is not None and value != ""]
        with ExitStack() as stack:
            uploads = []
            for field, path in files:
                path = Path(path)
                if not path.is_file():
                    raise H3StudioError(f"{field} file not found: {path}")
                uploads.append((field, (path.name, stack.enter_context(path.open("rb")))))
            # Uploads can be large reference videos; allow them time to transfer.
            return self._request("post", "/api/jobs", data=data, files=uploads or None, timeout=600).json()

    def wait(self, job_id: str, timeout_seconds: float, poll_seconds: float = 3.0) -> dict[str, Any]:
        deadline = time.monotonic() + timeout_seconds
        while True:
            job = self.job(job_id)
            if job.get("status") in FINISHED:
                if job["status"] != "done":
                    raise H3StudioError(f"Studio job {job_id} {job['status']}: {last_line(job.get('error'))}", job_id)
                return job
            if time.monotonic() >= deadline:
                position = job.get("queue_position")
                where = f"queue position {position}" if position else f"{job.get('stage') or job.get('status')}"
                raise H3StudioTimeout(
                    f"Studio job {job_id} still {job.get('status')} ({where}) after {timeout_seconds:.0f}s; "
                    f"it keeps running in the Studio. Call again with resume_job_id={job_id!r} to collect it.",
                    job_id,
                )
            time.sleep(poll_seconds)

    def download(self, url: str, output_path: str | Path) -> Path:
        """Save a ``/files/...`` URL from a job to ``output_path``."""
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        part = output_path.with_name(output_path.name + ".part")
        response = self._request("get", url if url.startswith("/") else f"/{url}", stream=True, timeout=300)
        with part.open("wb") as out:
            for chunk in response.iter_content(1 << 20):
                out.write(chunk)
        part.replace(output_path)
        return output_path


def last_line(text: str | None) -> str:
    """The Studio stores tracebacks; the last line names the actual error."""
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    return lines[-1] if lines else "no error message"


def run_job(
    fields: dict[str, Any],
    files: list[tuple[str, str | Path]],
    output_key: str,
    output_path: str | Path,
    *,
    timeout_seconds: float,
    resume_job_id: str | None = None,
    client: H3StudioClient | None = None,
) -> tuple[dict[str, Any], Path]:
    """Submit (or resume) a job, wait for it and download its ``output_key`` URL."""
    client = client or H3StudioClient()
    job_id = resume_job_id or client.submit(fields, files)["id"]
    job = client.wait(job_id, timeout_seconds)
    url = job.get(output_key)
    if not url:
        raise H3StudioError(f"Studio job {job_id} finished without {output_key}", job_id)
    return job, client.download(url, output_path)
