"""Alibaba Cloud Bailian (DashScope) Wan video generation.

Calls the first-party DashScope video-synthesis API, so a mainland China
account can pay in CNY with no third-party gateway. Two model families:

- ``wan2.6-i2v-flash`` (and other wan2.x i2v models): image-to-video from a
  first frame. Silent output (``audio=false``) is billed at the lowest rate.
- ``wan3.0-video``: text-to-video, or image-to-video via a ``first_frame``
  media item. Output always carries native audio.

API flow: POST .../services/aigc/video-generation/video-synthesis with
``X-DashScope-Async: enable`` -> poll GET .../tasks/{task_id} ->
download output.video_url (valid about 24h).
"""

from __future__ import annotations

import base64
import mimetypes
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from tools.base_tool import (
    BaseTool,
    Determinism,
    ExecutionMode,
    ResourceProfile,
    RetryPolicy,
    ToolResult,
    ToolRuntime,
    ToolStability,
    ToolStatus,
    ToolTier,
)

_DEFAULT_BASE_URL = "https://dashscope.aliyuncs.com/api/v1"
_SUBMIT_PATH = "/services/aigc/video-generation/video-synthesis"
_TASK_PATH = "/tasks/{task_id}"

_MODEL_I2V = "wan2.6-i2v-flash"
_MODEL_T2V = "wan3.0-video"

# Models that take the image as input.img_url and accept parameters.audio.
_LEGACY_I2V_MODELS = {"wan2.6-i2v-flash", "wan2.6-i2v", "wan2.5-i2v-preview", "wan2.2-i2v-flash", "wan2.2-i2v-plus"}
# Models that take images through input.media and always output audio.
_MEDIA_MODELS = {"wan3.0-video", "wan3.0-video-prime"}

# List prices in CNY per output second (help.aliyun.com/zh/model-studio/model-pricing,
# checked 2026-10-12). Promotions are ignored so estimates stay conservative.
_CNY_PER_SECOND = {
    ("wan2.6-i2v-flash", "silent"): {"480P": 0.15, "720P": 0.15, "1080P": 0.25},
    ("wan2.2-i2v-flash", "silent"): {"480P": 0.20, "720P": 0.20, "1080P": 0.48},
    ("wan3.0-video", "audio"): {"480P": 0.6, "720P": 0.6, "1080P": 1.2},
    ("wan3.0-video-prime", "audio"): {"480P": 0.9, "720P": 0.9, "1080P": 1.8},
}
# Used for anything not in the table (including flash with audio), on purpose high.
_FALLBACK_CNY_PER_SECOND = 1.0


class WanDashscopeVideo(BaseTool):
    """Wan video generation through Alibaba Cloud Bailian (DashScope)."""

    name = "wan_dashscope_video"
    version = "0.1.0"
    tier = ToolTier.GENERATE
    capability = "video_generation"
    provider = "dashscope"
    stability = ToolStability.EXPERIMENTAL
    execution_mode = ExecutionMode.ASYNC
    determinism = Determinism.STOCHASTIC
    runtime = ToolRuntime.API

    dependencies = ["env:DASHSCOPE_API_KEY"]
    install_instructions = (
        "Set DASHSCOPE_API_KEY to your Alibaba Cloud Bailian API key.\n"
        "  Get it at https://bailian.console.aliyun.com/ (API-KEY page).\n"
        "  Optionally set DASHSCOPE_BASE_URL (default https://dashscope.aliyuncs.com/api/v1)."
    )
    agent_skills = ["ai-video-gen", "dashscope"]

    capabilities = ["text_to_video", "image_to_video"]
    supports = {
        "text_to_video": True,
        "image_to_video": True,
        "native_audio": True,
        "seed": True,
    }
    best_for = [
        "cheapest cloud image-to-video: wan2.6-i2v-flash silent at about 0.15 CNY/s for 720P",
        "top-ranked quality at moderate cost: wan3.0-video",
        "mainland China accounts paying in CNY, pay as you go with no minimum top-up",
        "Chinese-language prompts",
    ]
    not_good_for = [
        "offline generation",
        "clips longer than the model's per-request limit",
    ]
    fallback_tools = ["minimax_video", "kling_official_video", "seedance_ark", "wan_video"]

    input_schema = {
        "type": "object",
        "required": ["prompt"],
        "properties": {
            "prompt": {"type": "string", "description": "Shot description. Chinese or English."},
            "operation": {
                "type": "string",
                "enum": ["text_to_video", "image_to_video"],
                "default": "text_to_video",
            },
            "model": {
                "type": "string",
                "description": (
                    "DashScope model ID. Defaults to wan2.6-i2v-flash for image_to_video "
                    "and wan3.0-video for text_to_video. wan3.0-video also does image_to_video."
                ),
            },
            "reference_image_path": {
                "type": "string",
                "description": "Local first-frame image for image_to_video. Sent inline as base64.",
            },
            "reference_image_url": {
                "type": "string",
                "description": "Public first-frame image URL for image_to_video.",
            },
            "resolution": {
                "type": "string",
                "enum": ["480P", "720P", "1080P", "480p", "720p", "1080p"],
                "default": "720P",
            },
            "duration": {
                "type": "integer",
                "default": 5,
                "description": "Clip length in seconds. Numeric strings such as '5' are accepted.",
            },
            "aspect_ratio": {
                "type": "string",
                "description": "Ratio for text_to_video on wan3.0 (e.g. 16:9, 9:16, 1:1). Image input uses the image's ratio.",
            },
            "audio": {
                "type": "boolean",
                "default": False,
                "description": (
                    "Keep generated audio. False sends audio=false to wan2.x i2v models "
                    "(cheapest billing); wan3.0 always generates audio, so it is stripped locally."
                ),
            },
            "negative_prompt": {"type": "string"},
            "seed": {"type": "integer", "minimum": 0, "maximum": 2147483647},
            "prompt_extend": {"type": "boolean", "default": True},
            "watermark": {"type": "boolean", "default": False},
            "output_path": {"type": "string"},
            "poll_interval_seconds": {"type": "number", "minimum": 2, "default": 10.0},
            "timeout_seconds": {"type": "integer", "minimum": 60, "default": 900},
        },
    }

    resource_profile = ResourceProfile(
        cpu_cores=1, ram_mb=512, vram_mb=0, disk_mb=500, network_required=True,
    )
    retry_policy = RetryPolicy(
        max_retries=2,
        backoff_seconds=2.0,
        retryable_errors=["rate_limit", "timeout"],
    )
    idempotency_key_fields = [
        "prompt",
        "operation",
        "model",
        "reference_image_path",
        "reference_image_url",
        "resolution",
        "duration",
        "aspect_ratio",
        "audio",
        "negative_prompt",
        "seed",
    ]
    side_effects = [
        "writes video file to output_path",
        "calls Alibaba Cloud DashScope video-synthesis API (billed per output second)",
    ]
    user_visible_verification = [
        "Watch the clip for motion coherence and prompt adherence",
        "Check the first frame matches the reference image for image_to_video",
    ]

    # ------------------------------------------------------------------
    # Contract methods
    # ------------------------------------------------------------------

    @staticmethod
    def _api_key() -> str | None:
        val = os.environ.get("DASHSCOPE_API_KEY", "").strip()
        if val and not val.startswith("#"):
            return val
        return None

    @staticmethod
    def _base_url() -> str:
        base = os.environ.get("DASHSCOPE_BASE_URL", "").strip() or _DEFAULT_BASE_URL
        if not base.startswith("https://"):
            raise ValueError("DASHSCOPE_BASE_URL must be an https:// URL")
        return base.rstrip("/")

    def get_status(self) -> ToolStatus:
        return ToolStatus.AVAILABLE if self._api_key() else ToolStatus.UNAVAILABLE

    def estimate_cost(self, inputs: dict[str, Any]) -> float:
        cny = self.estimate_cost_cny(inputs)
        rate = float(os.environ.get("DASHSCOPE_CNY_PER_USD", "7.2"))
        return round(cny / rate, 4)

    def estimate_cost_cny(self, inputs: dict[str, Any]) -> float:
        model = self._resolve_model(inputs)
        resolution = self._resolution(inputs)
        billed_audio = model in _MEDIA_MODELS or bool(inputs.get("audio", False))
        prices = _CNY_PER_SECOND.get((model, "audio" if billed_audio else "silent"))
        per_second = prices.get(resolution, _FALLBACK_CNY_PER_SECOND) if prices else _FALLBACK_CNY_PER_SECOND
        return round(per_second * self._duration(inputs), 4)

    def estimate_runtime(self, inputs: dict[str, Any]) -> float:
        return 180.0

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        api_key = self._api_key()
        if not api_key:
            return ToolResult(success=False, error="DASHSCOPE_API_KEY not set. " + self.install_instructions)

        operation = inputs.get("operation", "text_to_video")
        model = self._resolve_model(inputs)
        has_image = bool(inputs.get("reference_image_path") or inputs.get("reference_image_url"))
        if inputs.get("reference_image_path") and inputs.get("reference_image_url"):
            return ToolResult(success=False, error="Provide only one of reference_image_path or reference_image_url.")
        if operation == "image_to_video" and not has_image:
            return ToolResult(
                success=False,
                error="image_to_video requires reference_image_path or reference_image_url.",
            )
        if operation == "text_to_video" and model in _LEGACY_I2V_MODELS:
            return ToolResult(
                success=False,
                error=f"Model '{model}' only does image_to_video. Use wan3.0-video for text_to_video.",
            )

        start = time.time()
        try:
            result = self._generate(inputs, api_key=api_key, model=model)
        except Exception as exc:
            return ToolResult(success=False, error=f"DashScope Wan video generation failed: {self._safe_error(exc)}")
        result.duration_seconds = round(time.time() - start, 2)
        return result

    def _generate(self, inputs: dict[str, Any], *, api_key: str, model: str) -> ToolResult:
        import requests

        from tools.video._shared import probe_output

        base = self._base_url()
        body = self._build_body(inputs, model=model)
        submit = requests.post(
            base + _SUBMIT_PATH,
            headers={**self._headers(api_key), "X-DashScope-Async": "enable"},
            json=body,
            timeout=60,
        )
        data = self._json_or_raise(submit)
        self._check_error(data, submit.status_code)
        task_id = (data.get("output") or {}).get("task_id")
        if not task_id:
            raise RuntimeError(f"submit returned no task_id: {data}")

        task = self._poll(
            task_id,
            api_key=api_key,
            base=base,
            poll_interval=float(inputs.get("poll_interval_seconds", 10.0)),
            timeout_seconds=int(inputs.get("timeout_seconds", 900)),
        )
        video_url = (task.get("output") or {}).get("video_url")
        if not video_url:
            raise RuntimeError(f"task {task_id} succeeded without output.video_url")

        download = requests.get(video_url, timeout=300)
        download.raise_for_status()
        output_path = Path(inputs.get("output_path") or f"wan_dashscope_{task_id}.mp4")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(download.content)

        audio_stripped = False
        if model in _MEDIA_MODELS and not inputs.get("audio", False):
            audio_stripped = self._strip_audio(output_path)

        usage = task.get("usage") or {}
        return ToolResult(
            success=True,
            data={
                "provider": "dashscope",
                "model": model,
                "prompt": inputs["prompt"],
                "operation": inputs.get("operation", "text_to_video"),
                "resolution": self._resolution(inputs),
                "duration_requested": self._duration(inputs),
                "audio": bool(inputs.get("audio", False)),
                "audio_stripped_locally": audio_stripped,
                "task_id": task_id,
                "usage": usage,
                "estimated_cost_cny": self.estimate_cost_cny(inputs),
                "output": str(output_path),
                "format": "mp4",
                **probe_output(output_path),
            },
            artifacts=[str(output_path)],
            cost_usd=self.estimate_cost(inputs),
            seed=inputs.get("seed"),
            model=model,
        )

    # ------------------------------------------------------------------
    # Request building
    # ------------------------------------------------------------------

    def _build_body(self, inputs: dict[str, Any], *, model: str) -> dict[str, Any]:
        input_block: dict[str, Any] = {"prompt": inputs["prompt"]}
        parameters: dict[str, Any] = {
            "resolution": self._resolution(inputs),
            "duration": self._duration(inputs),
            "prompt_extend": bool(inputs.get("prompt_extend", True)),
            "watermark": bool(inputs.get("watermark", False)),
        }
        if inputs.get("negative_prompt"):
            input_block["negative_prompt"] = inputs["negative_prompt"]
        if inputs.get("seed") is not None:
            parameters["seed"] = int(inputs["seed"])

        image = self._image_ref(inputs)
        if model in _MEDIA_MODELS:
            if image:
                input_block["media"] = [{"type": "first_frame", "url": image}]
                parameters["ratio"] = "adaptive"
            elif inputs.get("aspect_ratio"):
                parameters["ratio"] = inputs["aspect_ratio"]
        else:
            if image:
                input_block["img_url"] = image
            # wan2.6 flash defaults to audio on; silent must be explicit.
            parameters["audio"] = bool(inputs.get("audio", False))

        return {"model": model, "input": input_block, "parameters": parameters}

    @staticmethod
    def _image_ref(inputs: dict[str, Any]) -> str | None:
        if inputs.get("reference_image_url"):
            return str(inputs["reference_image_url"])
        path_value = inputs.get("reference_image_path")
        if not path_value:
            return None
        path = Path(path_value)
        if not path.is_file():
            raise FileNotFoundError(f"Image not found: {path}")
        raw = path.read_bytes()
        if len(raw) > 10 * 1024 * 1024:
            raise ValueError(f"Image too large ({len(raw)} bytes); DashScope accepts up to 10MB.")
        mime = mimetypes.guess_type(path.name)[0] or "image/png"
        return f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}"

    @staticmethod
    def _resolve_model(inputs: dict[str, Any]) -> str:
        if inputs.get("model"):
            return str(inputs["model"])
        if inputs.get("operation") == "image_to_video":
            return _MODEL_I2V
        return _MODEL_T2V

    @staticmethod
    def _resolution(inputs: dict[str, Any]) -> str:
        return str(inputs.get("resolution") or "720P").upper()

    @staticmethod
    def _duration(inputs: dict[str, Any]) -> int:
        try:
            return max(1, int(float(inputs.get("duration") or 5)))
        except (TypeError, ValueError):
            return 5

    # ------------------------------------------------------------------
    # HTTP helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _headers(api_key: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    def _poll(
        self, task_id: str, *, api_key: str, base: str, poll_interval: float, timeout_seconds: int,
    ) -> dict[str, Any]:
        import requests

        url = base + _TASK_PATH.format(task_id=task_id)
        deadline = time.time() + timeout_seconds
        while time.time() < deadline:
            time.sleep(poll_interval)
            resp = requests.get(url, headers=self._headers(api_key), timeout=30)
            data = self._json_or_raise(resp)
            self._check_error(data, resp.status_code)
            output = data.get("output") or {}
            status = output.get("task_status", "")
            if status == "SUCCEEDED":
                return data
            if status in ("FAILED", "CANCELED", "UNKNOWN"):
                code = output.get("code", status)
                message = output.get("message", "no message")
                raise RuntimeError(f"task {task_id} {status}: {code}: {message}")
            if status not in ("PENDING", "RUNNING"):
                raise RuntimeError(f"task {task_id} returned unknown status: {status}")
        raise TimeoutError(f"task {task_id} did not finish within {timeout_seconds}s")

    @staticmethod
    def _json_or_raise(response: Any) -> dict[str, Any]:
        try:
            return response.json()
        except ValueError as exc:
            raise RuntimeError(f"non-JSON response from DashScope: HTTP {response.status_code}") from exc

    @staticmethod
    def _check_error(data: dict[str, Any], status_code: int) -> None:
        if data.get("code") and not (data.get("output") or {}).get("task_id"):
            raise RuntimeError(f"HTTP {status_code}: {data.get('code')}: {data.get('message', '')}")
        if status_code >= 400:
            raise RuntimeError(f"HTTP {status_code}: {data}")

    @staticmethod
    def _strip_audio(path: Path) -> bool:
        """Drop the audio track in place. Returns False if ffmpeg is missing or fails."""
        if not shutil.which("ffmpeg"):
            return False
        tmp = path.with_suffix(".noaudio.mp4")
        proc = subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-i", str(path), "-an", "-c:v", "copy", str(tmp)],
            capture_output=True,
        )
        if proc.returncode != 0:
            tmp.unlink(missing_ok=True)
            return False
        tmp.replace(path)
        return True

    @staticmethod
    def _safe_error(exc: Exception) -> str:
        msg = str(exc)
        key = os.environ.get("DASHSCOPE_API_KEY", "")
        if key:
            msg = msg.replace(key, "[redacted]")
        return msg
