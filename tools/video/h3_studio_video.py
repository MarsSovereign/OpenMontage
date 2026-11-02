"""MiniMax-H3 video on a self-hosted MiniMax-H3 Studio (local GPU, no per-call cost).

Covers every H3 mode the Studio runs: text to video, first frame, first + last
frame, and reference to video with up to 6 subject images, 3 reference videos
and a guide audio track, all with native audio. Reference mode on FastH3
OmniRef is the Studio's route for consistent recurring characters, which is
what multi-shot short films need.
"""

from __future__ import annotations

import time
from typing import Any

from tools._h3_studio.client import SETUP, H3StudioError, H3StudioTimeout, configured, run_job
from tools.base_tool import (
    BaseTool,
    Determinism,
    ExecutionMode,
    ResourceProfile,
    ToolResult,
    ToolRuntime,
    ToolStability,
    ToolStatus,
    ToolTier,
)

ASPECT_RATIOS = ("16:9", "9:16", "4:3", "3:4", "1:1")
MAX_REF_IMAGES = 6
MAX_REF_VIDEOS = 3


class H3StudioVideo(BaseTool):
    name = "h3_studio_video"
    version = "0.1.0"
    tier = ToolTier.GENERATE
    capability = "video_generation"
    provider = "h3_studio"
    stability = ToolStability.BETA
    execution_mode = ExecutionMode.SYNC
    determinism = Determinism.SEEDED
    runtime = ToolRuntime.LOCAL_GPU

    dependencies = ["env:H3_STUDIO_URL"]
    install_instructions = SETUP
    agent_skills = ["minimax-h3", "ai-video-gen"]

    capabilities = ["text_to_video", "image_to_video", "reference_to_video"]
    supports = {
        "seed": True,
        "reference_image": True,
        "last_frame": True,
        "reference_images": True,
        "reference_videos": True,
        "guide_audio": True,
        "native_audio": True,
        "offline": True,
        "max_duration_seconds": 15,
    }
    best_for = [
        "MiniMax-H3 clips with native dialogue, sound and music on your own GPU, free per call",
        "recurring characters across shots via reference_to_video (FastH3 OmniRef, up to 6 subject images)",
        "first-frame and first + last frame shots locked to approved stills",
        "re-performing a reference video's motion with a new subject, or anchoring a shot to guide audio",
    ]
    not_good_for = [
        "shots longer than 15 seconds (split them)",
        "parallel batches: the Studio runs one GPU queue in order",
        "resolutions above 768p without a separate upscale pass",
    ]
    fallback_tools = ["comfyui_video", "minimax_video", "h3_max_video", "kling_video"]
    resource_profile = ResourceProfile(cpu_cores=1, ram_mb=512, vram_mb=0, disk_mb=500, network_required=True)
    idempotency_key_fields = [
        "prompt", "operation", "model", "lora_strength", "duration", "resolution", "aspect_ratio", "seed",
        "reference_image_path", "last_image_path", "reference_image_paths", "reference_video_paths",
        "audio_path", "audio_start",
    ]
    side_effects = ["queues a job on the MiniMax-H3 Studio GPU", "writes the MP4 to output_path"]
    user_visible_verification = ["Watch the clip: subject identity, motion, lip sync and audio"]

    input_schema = {
        "type": "object",
        "required": ["prompt", "output_path"],
        "properties": {
            "prompt": {
                "type": "string",
                "description": (
                    "H3 prompt. Frame modes may reference <Picture 1> (first) and <Picture 2> (last); the "
                    "Studio adds them when missing. Reference mode should follow the official ref2va layout "
                    "(subject_definitions / summary / retention_analysis / detailed_description / "
                    "overall_soundscape / non_diegetic_music) with <Subject N> for image N and <Video k> for video k."
                ),
            },
            "operation": {
                "type": "string",
                "enum": ["text_to_video", "image_to_video", "reference_to_video"],
                "description": "Inferred from the inputs when omitted.",
            },
            "reference_image_path": {"type": "string", "description": "First frame (image_to_video)."},
            "last_image_path": {"type": "string", "description": "Last frame (image_to_video with an end frame)."},
            "reference_image_paths": {
                "type": "array",
                "items": {"type": "string"},
                "maxItems": MAX_REF_IMAGES,
                "description": "Subject images for reference_to_video, <Subject 1>..<Subject 6> in order.",
            },
            "reference_video_paths": {
                "type": "array",
                "items": {"type": "string"},
                "maxItems": MAX_REF_VIDEOS,
                "description": "Reference videos (each at least 2 s) for reference_to_video.",
            },
            "audio_path": {"type": "string", "description": "Guide audio anchored at the shot start (any mode)."},
            "audio_start": {"type": "number", "minimum": 0, "default": 0, "description": "Offset into audio_path in seconds."},
            "duration": {"type": "number", "minimum": 2, "maximum": 15, "default": 5, "description": "Seconds (24 fps)."},
            "resolution": {"type": "string", "enum": ["544p", "768p"], "default": "768p"},
            "aspect_ratio": {
                "type": "string",
                "enum": [*ASPECT_RATIOS, "image"],
                "description": "Defaults to 16:9, or to the first frame's ratio in image_to_video.",
            },
            "model": {
                "type": "string",
                "description": (
                    "Studio model choice (a key of `loras` in the Studio's /api/presets), e.g. fasth3-omniref, "
                    "larryvrh-v4-sparse, pika-flex-ref2va-sparse-all. Omit for the Studio's recommended choice."
                ),
            },
            "lora_strength": {"type": "number", "minimum": 0, "maximum": 2},
            "seed": {"type": "integer", "minimum": 0},
            "output_path": {"type": "string", "description": "Where to save the MP4 (under projects/<id>/assets/video/)."},
            "timeout_seconds": {
                "type": "number",
                "default": 3600,
                "description": "How long to wait, including queue time; the job keeps running after a timeout.",
            },
            "resume_job_id": {"type": "string", "description": "Collect an already submitted Studio job instead of submitting."},
        },
    }
    output_schema = {
        "type": "object",
        "properties": {
            "output": {"type": "string"},
            "job_id": {"type": "string"},
            "mode": {"type": "string"},
            "seed": {"type": "integer"},
            "models": {"type": "object"},
        },
    }

    def get_status(self) -> ToolStatus:
        return ToolStatus.AVAILABLE if configured() else ToolStatus.UNAVAILABLE

    def is_operation_available(self, operation: str) -> bool:
        return operation in self.capabilities and configured()

    def estimate_cost(self, inputs: dict[str, Any]) -> float:
        return 0.0

    def estimate_runtime(self, inputs: dict[str, Any]) -> float:
        # Measured on an RTX 4090: a 5 s shot takes ~40 s at 544p and ~70 s at 768p; reference videos add more.
        seconds = float(inputs.get("duration") or 5)
        per_second = 8.0 if inputs.get("resolution") == "544p" else 14.0
        refs = len(inputs.get("reference_video_paths") or [])
        return round(20 + seconds * per_second * (1 + 2.5 * refs), 1)

    @staticmethod
    def build_request(inputs: dict[str, Any]) -> tuple[dict[str, Any], list[tuple[str, str]]]:
        """Map tool inputs to the Studio's POST /api/jobs form; raises ValueError on bad combinations."""
        images = list(inputs.get("reference_image_paths") or [])
        videos = list(inputs.get("reference_video_paths") or [])
        first, last = inputs.get("reference_image_path"), inputs.get("last_image_path")
        operation = inputs.get("operation") or (
            "reference_to_video" if images or videos else "image_to_video" if first else "text_to_video"
        )
        if len(images) > MAX_REF_IMAGES or len(videos) > MAX_REF_VIDEOS:
            raise ValueError(f"at most {MAX_REF_IMAGES} reference images and {MAX_REF_VIDEOS} reference videos")
        files: list[tuple[str, str]] = []
        if operation == "image_to_video":
            if not first:
                raise ValueError("image_to_video needs reference_image_path (the first frame)")
            files.append(("image", first))
            if last:
                files.append(("last_image", last))
        elif operation == "reference_to_video":
            if first or last:
                raise ValueError("reference_to_video takes reference_image_paths, not first/last frames")
            files += [("ref_image", path) for path in images] + [("ref_video", path) for path in videos]
        elif first or last or images or videos:
            raise ValueError("text_to_video takes no images or videos; set operation or drop them")
        if inputs.get("audio_path"):
            files.append(("audio", inputs["audio_path"]))
        aspect = inputs.get("aspect_ratio") or ("image" if operation == "image_to_video" else "16:9")
        fields = {
            "kind": "video",
            "prompt": inputs["prompt"],
            "duration": inputs.get("duration", 5),
            "resolution": inputs.get("resolution") or "768p",
            "aspect_ratio": aspect,
            "lora": inputs.get("model"),
            "lora_strength": inputs.get("lora_strength"),
            "seed": inputs.get("seed"),
            "audio_start": inputs.get("audio_start") if inputs.get("audio_path") else None,
        }
        return fields, files

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        started = time.monotonic()
        try:
            fields, files = self.build_request(inputs)
            job, path = run_job(
                fields,
                files,
                "video_url",
                inputs["output_path"],
                timeout_seconds=float(inputs.get("timeout_seconds", 3600)),
                resume_job_id=inputs.get("resume_job_id"),
            )
        except ValueError as exc:
            return ToolResult(success=False, error=str(exc))
        except H3StudioError as exc:
            data = {"job_id": exc.job_id, "timed_out": isinstance(exc, H3StudioTimeout)} if exc.job_id else {}
            return ToolResult(success=False, error=str(exc), data=data, duration_seconds=time.monotonic() - started)
        models = (job.get("models") or {})
        return ToolResult(
            success=True,
            data={
                "provider": self.provider,
                "model": job.get("lora") or models.get("lora"),
                "models": models,
                "mode": job.get("mode"),
                "job_id": job["id"],
                "seed": job.get("seed"),
                "duration": job.get("duration"),
                "resolution": job.get("preset"),
                "width": job.get("width"),
                "height": job.get("height"),
                "prompt": job.get("prompt"),
                "output": str(path),
                "format": "mp4",
                "native_audio": True,
            },
            artifacts=[str(path)],
            cost_usd=0.0,
            duration_seconds=time.monotonic() - started,
            seed=job.get("seed"),
            model=job.get("lora"),
        )
