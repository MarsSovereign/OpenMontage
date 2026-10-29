"""Turn one canvas request into one local run: a ComfyUI workflow, a command, or TTS.

Each model in the gateway config names one adapter. The canvas sends a prompt,
a size, a duration and reference files; the adapter maps those onto the
workflow's node inputs (or a command line) and returns the produced file.
"""

from __future__ import annotations

import copy
import json
import math
import random
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import requests

from services.canvas_gateway.services import child_flags


@dataclass
class GenRequest:
    prompt: str = ""
    width: int | None = None
    height: int | None = None
    seconds: float | None = None
    seed: int | None = None
    speed: float = 1.0
    images: list[Path] = field(default_factory=list)
    videos: list[Path] = field(default_factory=list)
    audios: list[Path] = field(default_factory=list)


class AdapterError(RuntimeError):
    pass


def snap(value: int, multiple: int) -> int:
    return max(multiple, int(round(value / multiple)) * multiple)


def frames_for(seconds: float, fps: float, grid: tuple[int, int] | None) -> int:
    """Smallest frame count on the model's grid (k*step + offset) covering *seconds*."""
    want = max(1, int(math.ceil(seconds * fps)))
    if not grid:
        return want
    step, offset = grid
    k = max(0, int(math.ceil((want - offset) / step)))
    return k * step + offset


def _fit_size(model: dict, req: GenRequest) -> tuple[int, int]:
    default_w, default_h = model.get("default_size", [1280, 720])
    if not req.width or not req.height:
        return int(default_w), int(default_h)
    max_px = model.get("max_pixels")
    w, h = req.width, req.height
    if max_px and w * h > max_px:
        scale = math.sqrt(max_px / (w * h))
        w, h = w * scale, h * scale
    multiple = int(model.get("size_multiple", 32))
    return snap(int(w), multiple), snap(int(h), multiple)


def _set(workflow: dict, target: list | None, value: Any) -> None:
    if target is None:
        return
    node_id, input_name = str(target[0]), str(target[1])
    if node_id not in workflow:
        raise AdapterError(f"工作流里没有节点 {node_id}（{input_name}）。")
    workflow[node_id].setdefault("inputs", {})[input_name] = value


class ComfyWorkflowAdapter:
    """Fill an API-format workflow from the request, run it, download the output."""

    def __init__(self, model: dict, client) -> None:
        self.model = model
        self.client = client
        path = Path(model["workflow"])
        self.template = json.loads(path.read_text(encoding="utf-8"))
        if "nodes" in self.template and "links" in self.template:
            raise AdapterError(f"{path} 是界面格式；请在 ComfyUI 里用「导出 (API)」另存一份。")

    def build(self, req: GenRequest) -> dict:
        m = self.model
        inputs = m.get("inputs", {})
        wf = copy.deepcopy(self.template)
        for node_id, values in (m.get("fixed") or {}).items():
            for key, value in values.items():
                _set(wf, [node_id, key], value)
        prompt = req.prompt.strip() or m.get("default_prompt", "")
        if m.get("prompt_prefix"):
            prompt = f"{m['prompt_prefix']}\n{prompt}"
        _set(wf, inputs.get("prompt"), prompt)
        seed = req.seed if req.seed is not None else random.randint(0, 2**31 - 1)
        for target in inputs.get("seeds", []) or ([inputs["seed"]] if inputs.get("seed") else []):
            _set(wf, target, seed)
        w, h = _fit_size(m, req)
        _set(wf, inputs.get("width"), w)
        _set(wf, inputs.get("height"), h)
        if inputs.get("frames"):
            seconds = req.seconds or m.get("default_seconds", 5)
            seconds = min(seconds, m.get("max_seconds", seconds))
            grid = tuple(m["frame_grid"]) if m.get("frame_grid") else None
            _set(wf, inputs["frames"], frames_for(seconds, m.get("fps", 24), grid))
        for kind in ("images", "audios", "videos"):
            targets = inputs.get(kind) or []
            files = getattr(req, kind)
            required = int(m.get(f"min_{kind}", 0))
            if len(files) < required:
                raise AdapterError(f"{m['name']} 需要至少 {required} 个{_KIND_CN[kind]}，请连接到生成节点。")
            for target, path in zip(targets, files):
                _set(wf, target, self.client.upload_file(path))
        return wf

    def run(self, req: GenRequest, job_dir: Path) -> Path:
        wf = self.build(req)
        (job_dir / "workflow_api.json").write_text(json.dumps(wf, ensure_ascii=False, indent=1), encoding="utf-8")
        timeout = int(self.model.get("timeout_seconds", 7200))
        wanted = self.model.get("output_ext") or (".png" if self.model.get("capability") == "image" else ".mp4")
        paths = self.client.generate(wf, str(self.model["output_node"]), job_dir / f"output{wanted}", timeout=timeout)
        for path in paths:
            if path.suffix.lower() == wanted:
                return path
        raise AdapterError(f"工作流没有产出 {wanted} 文件（输出节点 {self.model['output_node']}）。")


_KIND_CN = {"images": "图片", "audios": "音频", "videos": "视频"}


class CommandAdapter:
    """Run a local script (e.g. make_film.py) with placeholders filled from the request."""

    def __init__(self, model: dict) -> None:
        self.model = model

    def run(self, req: GenRequest, job_dir: Path) -> Path:
        m = self.model
        for kind in ("images", "audios", "videos"):
            required = int(m.get(f"min_{kind}", 0))
            if len(getattr(req, kind)) < required:
                raise AdapterError(f"{m['name']} 需要至少 {required} 个{_KIND_CN[kind]}，请连接到生成节点。")
        out = job_dir / f"output{m.get('output_ext', '.mp4')}"
        prompt_file = job_dir / "prompt.txt"
        prompt_file.write_text(req.prompt, encoding="utf-8")
        w, h = _fit_size(m, req)
        values: dict[str, str] = {
            "prompt": req.prompt,
            "prompt_file": str(prompt_file),
            "out": str(out),
            "job_dir": str(job_dir),
            "width": str(w),
            "height": str(h),
            "seconds": str(req.seconds or m.get("default_seconds", 5)),
            "seed": str(req.seed if req.seed is not None else random.randint(0, 2**31 - 1)),
        }
        for kind, single in (("images", "image"), ("audios", "audio"), ("videos", "video")):
            for i, path in enumerate(getattr(req, kind), start=1):
                values[f"{single}{i}"] = str(path)
            values[f"{kind}_dir"] = str(job_dir / "in" / kind)
        args = []
        for part in m["command"]:
            try:
                args.append(str(part).format(**values))
            except KeyError as exc:
                raise AdapterError(f"命令里的 {exc} 没有对应的输入（缺少参考图/音频？）。") from exc
        log_path = job_dir / "log.txt"
        with open(log_path, "w", encoding="utf-8", errors="replace") as log:
            proc = subprocess.run(args, cwd=m.get("cwd"), stdout=log, stderr=subprocess.STDOUT,
                                  stdin=subprocess.DEVNULL, creationflags=child_flags(),
                                  timeout=int(m.get("timeout_seconds", 14400)))
        if proc.returncode != 0:
            tail = log_path.read_text(encoding="utf-8", errors="replace")[-600:]
            raise AdapterError(f"命令失败（返回码 {proc.returncode}）：{tail}")
        if not out.is_file() or out.stat().st_size == 0:
            raise AdapterError(f"命令跑完了，但没有生成 {out.name}。日志：{log_path}")
        return out


class TTSAdapter:
    """One canvas audio model per voice: the voice comes from the model, not the canvas."""

    def __init__(self, model: dict, base_url: str) -> None:
        self.model = model
        self.base_url = base_url.rstrip("/")

    def run(self, req: GenRequest, job_dir: Path) -> Path:
        text = req.prompt.strip()
        if not text:
            raise AdapterError("配音需要文字内容。")
        resp = requests.post(
            f"{self.base_url}/tts",
            json={"text": text, "voice": self.model.get("voice"), "speed": req.speed},
            timeout=int(self.model.get("timeout_seconds", 600)),
        )
        if resp.status_code != 200:
            raise AdapterError(f"配音失败：{resp.text[:300]}")
        out = job_dir / "speech.wav"
        out.write_bytes(resp.content)
        return out


def save_uploads(job_dir: Path, kind: str, uploads: list[tuple[str, bytes]]) -> list[Path]:
    folder = job_dir / "in" / kind
    folder.mkdir(parents=True, exist_ok=True)
    paths = []
    for i, (name, data) in enumerate(uploads, start=1):
        suffix = Path(name or "").suffix or {"images": ".png", "audios": ".mp3", "videos": ".mp4"}[kind]
        path = folder / f"{i:02d}{suffix}"
        path.write_bytes(data)
        paths.append(path)
    return paths


def copy_result(src: Path, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if src.resolve() != dest.resolve():
        shutil.copyfile(src, dest)
    return dest
