"""Canvas gateway: an OpenAI-style API in front of local ComfyUI workflows and TTS.

infinite-canvas only talks to "generation APIs". This service pretends to be
one, so the canvas runs our local models instead of paid ones:

    GET  /v1/models                  models from the config (names carry image/video/tts)
    POST /v1/images/generations      text to image (waits for the result)
    POST /v1/images/edits            image with references
    POST /v1/videos                  queue a video job, returns {id, status}
    GET  /v1/videos/{id}             job status; waits up to 20 s while it runs
    GET  /v1/videos/{id}/content     the finished mp4
    POST /v1/audio/speech            local TTS, one model per voice
    GET  /health                     queue, current job, services we started

All jobs share one GPU, so a single worker runs them in order. The built
canvas (web/dist) can be served from the same address, which avoids CORS.

Run:
    python -m services.canvas_gateway.server --config canvas_gateway.json
"""

from __future__ import annotations

import argparse
import base64
import json
import logging
import queue
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response

from services.canvas_gateway.adapters import (
    AdapterError,
    CommandAdapter,
    ComfyWorkflowAdapter,
    GenRequest,
    TTSAdapter,
    save_uploads,
)
from services.canvas_gateway.services import ServiceManager, ServiceSpec

log = logging.getLogger("canvas_gateway")

STATUS_WAIT_SECONDS = 20.0
_KIND_CN = {"image": "出图", "video": "视频", "audio": "配音"}


@dataclass
class Job:
    id: str
    model: str
    kind: str
    created: float
    dir: Path
    request: GenRequest
    status: str = "queued"
    error: str | None = None
    output: Path | None = None
    started: float | None = None
    finished: float | None = None
    done: threading.Event = field(default_factory=threading.Event)

    def public(self) -> dict:
        body: dict[str, Any] = {
            "id": self.id,
            "object": "video" if self.kind == "video" else self.kind,
            "model": self.model,
            "status": self.status,
            "created_at": int(self.created),
            "progress": 100 if self.status == "completed" else 0,
        }
        if self.error:
            body["error"] = {"message": self.error}
        return body

    def save(self) -> None:
        meta = {
            "id": self.id, "model": self.model, "kind": self.kind, "status": self.status,
            "error": self.error, "output": str(self.output) if self.output else None,
            "created": self.created, "started": self.started, "finished": self.finished,
            "prompt": self.request.prompt,
        }
        (self.dir / "job.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")


def load_config(path: Path) -> dict:
    config = json.loads(path.read_text(encoding="utf-8"))
    names = [m["name"] for m in config.get("models", [])]
    if len(names) != len(set(names)):
        raise SystemExit("配置里有重名的模型。")
    return config


def _client_factory(url: str):
    from tools._comfyui.client import ComfyUIClient

    return ComfyUIClient(server_url=url)


class Gateway:
    def __init__(
        self,
        config: dict,
        *,
        services: ServiceManager | None = None,
        comfy_client: Callable[[str], Any] = _client_factory,
    ) -> None:
        self.config = config
        self.work_dir = Path(config.get("work_dir", "canvas_gateway_jobs"))
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self.models = {m["name"]: m for m in config.get("models", [])}
        specs = {n: ServiceSpec.from_config(n, raw) for n, raw in (config.get("services") or {}).items()}
        self.services = services or ServiceManager(specs)
        self.exclusive_gpu = bool(config.get("exclusive_gpu", True))
        self._comfy_client = comfy_client
        self.jobs: dict[str, Job] = {}
        self.queue: "queue.Queue[Job]" = queue.Queue()
        self.current: Job | None = None
        self._load_finished_jobs()

    # -- jobs --------------------------------------------------------------

    def _load_finished_jobs(self) -> None:
        """Keep finished videos reachable after a restart (the canvas polls by id)."""
        for meta_path in self.work_dir.glob("*/job.json"):
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            job = Job(id=meta["id"], model=meta["model"], kind=meta["kind"], created=meta["created"],
                      dir=meta_path.parent, request=GenRequest(prompt=meta.get("prompt", "")))
            if meta["status"] == "completed" and meta.get("output") and Path(meta["output"]).is_file():
                job.status, job.output = "completed", Path(meta["output"])
            else:
                job.status = "failed"
                job.error = meta.get("error") or "网关重启时这个任务还没跑完，请重新生成。"
            job.done.set()
            self.jobs[job.id] = job

    def model(self, name: str, kind: str) -> dict:
        model = self.models.get(name)
        if model is None:
            raise HTTPException(404, f"没有这个本地模型：{name}。可用：{sorted(self.models)}")
        if model.get("capability") != kind:
            raise HTTPException(400, f"{name} 不是{_KIND_CN[kind]}模型。")
        return model

    def submit(self, model_name: str, kind: str, req_builder: Callable[[Path], GenRequest]) -> Job:
        job_id = f"{kind}_{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
        job_dir = self.work_dir / job_id
        job_dir.mkdir(parents=True)
        job = Job(id=job_id, model=model_name, kind=kind, created=time.time(), dir=job_dir,
                  request=req_builder(job_dir))
        job.save()
        self.jobs[job_id] = job
        self.queue.put(job)
        return job

    def run_job(self, job: Job) -> None:
        model = self.models[job.model]
        self.current = job
        job.status, job.started = "in_progress", time.time()
        job.save()
        try:
            needed = list(model.get("services") or ([model["service"]] if model.get("service") else []))
            if self.exclusive_gpu:
                self.services.stop_owned(keep=set(needed))
            for name in needed:
                self.services.ensure(name)
            job.output = self._adapter(model).run(job.request, job.dir)
            job.status = "completed"
        except Exception as exc:  # keep the worker alive whatever a workflow does
            if not isinstance(exc, (AdapterError, RuntimeError)):
                log.exception("job %s crashed", job.id)
            job.status, job.error = "failed", str(exc) or type(exc).__name__
        finally:
            job.finished = time.time()
            for name in model.get("services") or ([model["service"]] if model.get("service") else []):
                self.services.touch(name)
            job.save()
            self.current = None
            job.done.set()

    def _adapter(self, model: dict):
        kind = model.get("type", "comfyui")
        if kind == "comfyui":
            url = self.services.specs[model.get("service", "comfyui")].url
            return ComfyWorkflowAdapter(model, self._comfy_client(url))
        if kind == "command":
            return CommandAdapter(model)
        if kind == "tts":
            return TTSAdapter(model, self.services.specs[model.get("service", "tts")].url)
        raise AdapterError(f"未知的适配器类型：{kind}")

    def worker(self, stop: threading.Event) -> None:
        while not stop.is_set():
            try:
                job = self.queue.get(timeout=5)
            except queue.Empty:
                self.services.stop_idle(busy=False)
                continue
            self.run_job(job)

    def wait(self, job: Job, timeout: float) -> Job:
        job.done.wait(timeout)
        if job.status == "failed":
            raise HTTPException(500, job.error or "生成失败")
        if job.status != "completed":
            raise HTTPException(504, "本地生成超时，请稍后到网关目录查看结果。")
        return job


# -- request parsing ----------------------------------------------------------

def _parse_size(value: Any) -> tuple[int | None, int | None]:
    text = str(value or "").lower()
    if "x" in text:
        w, _, h = text.partition("x")
        if w.isdigit() and h.isdigit():
            return int(w), int(h)
    return None, None


def _seconds(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


async def _form_files(form, *names: str) -> list[tuple[str, bytes]]:
    out: list[tuple[str, bytes]] = []
    seen: set[int] = set()
    for name in names:
        for item in form.getlist(name):
            if hasattr(item, "read"):
                data = await item.read()
                key = hash(data)
                if data and key not in seen:
                    seen.add(key)
                    out.append((item.filename or "", data))
    return out


def create_app(gateway: Gateway, *, start_worker: bool = True, canvas_dist: Path | None = None) -> FastAPI:
    stop = threading.Event()
    app = FastAPI(title="OpenMontage canvas gateway")
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

    if start_worker:
        @app.on_event("startup")
        def _start() -> None:
            threading.Thread(target=gateway.worker, args=(stop,), daemon=True).start()

        @app.on_event("shutdown")
        def _stop() -> None:
            stop.set()
            gateway.services.stop_owned()

    @app.get("/health")
    def health() -> dict:
        current = gateway.current
        return {
            "status": "ok",
            "queued": gateway.queue.qsize(),
            "current": current.public() if current else None,
            "services_started_by_gateway": gateway.services.owned(),
            "models": sorted(gateway.models),
        }

    @app.get("/v1/models")
    def models() -> dict:
        return {"object": "list", "data": [
            {"id": name, "object": "model", "owned_by": "local", "capability": m.get("capability")}
            for name, m in gateway.models.items()
        ]}

    @app.post("/v1/images/generations")
    async def images_generations(request: Request) -> dict:
        body = await request.json()
        return _run_images(gateway, body.get("model", ""), body, [])

    @app.post("/v1/images/edits")
    async def images_edits(request: Request) -> dict:
        form = await request.form()
        files = await _form_files(form, "image", "image[]")
        return _run_images(gateway, str(form.get("model", "")), dict(form), files)

    @app.post("/v1/videos")
    async def create_video(request: Request) -> dict:
        form = await request.form()
        name = str(form.get("model", ""))
        gateway.model(name, "video")
        images = await _form_files(form, "images", "image[]", "first_frame", "last_frame")
        videos = await _form_files(form, "video[]", "video")
        audios = await _form_files(form, "audio[]", "audio")
        w, h = _parse_size(form.get("size"))

        def build(job_dir: Path) -> GenRequest:
            return GenRequest(
                prompt=str(form.get("prompt", "")), width=w, height=h,
                seconds=_seconds(form.get("seconds")),
                images=save_uploads(job_dir, "images", images),
                videos=save_uploads(job_dir, "videos", videos),
                audios=save_uploads(job_dir, "audios", audios),
            )

        return gateway.submit(name, "video", build).public()

    @app.get("/v1/videos/{job_id}")
    def get_video(job_id: str) -> dict:
        job = gateway.jobs.get(job_id)
        if job is None:
            raise HTTPException(404, "找不到这个视频任务（可能是网关的任务目录被清理了）。")
        # Hold the request while the job runs: the canvas gives up after 120
        # polls, so short answers would time out long renders.
        job.done.wait(STATUS_WAIT_SECONDS)
        return job.public()

    @app.get("/v1/videos/{job_id}/content")
    def video_content(job_id: str):
        job = gateway.jobs.get(job_id)
        if job is None or job.status != "completed" or not job.output:
            raise HTTPException(404, "视频还没有生成好。")
        return FileResponse(job.output, media_type="video/mp4", filename=f"{job_id}.mp4")

    @app.post("/v1/audio/speech")
    async def speech(request: Request) -> Response:
        body = await request.json()
        name = str(body.get("model", ""))
        gateway.model(name, "audio")
        text = str(body.get("input", ""))
        speed = float(body.get("speed") or 1.0)
        job = gateway.submit(name, "audio", lambda _d: GenRequest(prompt=text, speed=speed))
        gateway.wait(job, float(gateway.models[name].get("timeout_seconds", 600)))
        return Response(job.output.read_bytes(), media_type="audio/wav")

    if canvas_dist and (canvas_dist / "index.html").is_file():
        @app.get("/{path:path}")
        def canvas(path: str):
            target = (canvas_dist / path).resolve()
            if path and target.is_file() and canvas_dist.resolve() in target.parents:
                return FileResponse(target)
            return FileResponse(canvas_dist / "index.html")

    return app


def _run_images(gateway: Gateway, name: str, body: dict, files: list[tuple[str, bytes]]) -> dict:
    model = gateway.model(name, "image")
    count = max(1, min(int(model.get("max_count", 4)), int(body.get("n") or 1)))
    w, h = _parse_size(body.get("size"))
    jobs = []
    for _ in range(count):
        jobs.append(gateway.submit(name, "image", lambda job_dir: GenRequest(
            prompt=str(body.get("prompt", "")), width=w, height=h,
            images=save_uploads(job_dir, "images", files),
        )))
    timeout = float(model.get("timeout_seconds", 540))
    data = []
    for job in jobs:
        gateway.wait(job, timeout)
        data.append({"b64_json": base64.b64encode(job.output.read_bytes()).decode("ascii")})
    return {"created": int(time.time()), "data": data}


def main() -> None:
    import uvicorn

    parser = argparse.ArgumentParser(description="Local OpenAI-style gateway for infinite-canvas")
    parser.add_argument("--config", default="canvas_gateway.json")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    config = load_config(Path(args.config))
    dist = Path(config["canvas_dist"]) if config.get("canvas_dist") else None
    app = create_app(Gateway(config), canvas_dist=dist)
    uvicorn.run(app, host=config.get("host", "127.0.0.1"), port=int(config.get("port", 7860)))


if __name__ == "__main__":
    main()
