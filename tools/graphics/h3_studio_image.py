"""Pictures on a self-hosted MiniMax-H3 Studio (local GPU, no per-call cost).

Qwen Image 2.1 Turbo (8 steps), FLUX.2 Klein 4B (4 steps) and MOSS-Image.
Reference images turn a job into an edit, which is how character cards,
costume variants and empty location plates are kept consistent for H3
reference-to-video shots.
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

IMAGE_MODELS = ("qwen-image-21-turbo", "flux-klein-4b", "moss")
ASPECT_RATIOS = {"16:9": 16 / 9, "9:16": 9 / 16, "4:3": 4 / 3, "3:4": 3 / 4, "1:1": 1.0}
MAX_INPUTS = 4


def snap_ratio(width: Any, height: Any) -> str:
    target = float(width) / float(height)
    return min(ASPECT_RATIOS, key=lambda key: abs(ASPECT_RATIOS[key] / target - 1))


class H3StudioImage(BaseTool):
    name = "h3_studio_image"
    version = "0.1.0"
    tier = ToolTier.GENERATE
    capability = "image_generation"
    provider = "h3_studio"
    stability = ToolStability.BETA
    execution_mode = ExecutionMode.SYNC
    determinism = Determinism.SEEDED
    runtime = ToolRuntime.LOCAL_GPU

    dependencies = ["env:H3_STUDIO_URL"]
    install_instructions = SETUP
    agent_skills = ["flux-best-practices"]

    capabilities = ["text_to_image", "image_edit"]
    supports = {"seed": True, "reference_images": True, "image_edit": True, "offline": True, "max_reference_images": MAX_INPUTS}
    best_for = [
        "character reference cards and empty location plates for H3 reference-to-video, free per call",
        "editing a locked character into new outfits, ages or poses with Qwen Image 2.1 Turbo",
        "fast drafts with FLUX.2 Klein 4B",
    ]
    not_good_for = ["sizes beyond about 1 megapixel", "transparent backgrounds", "parallel batches (one GPU queue)"]
    fallback_tools = ["comfyui_image", "flux_image"]
    resource_profile = ResourceProfile(cpu_cores=1, ram_mb=256, vram_mb=0, disk_mb=50, network_required=True)
    idempotency_key_fields = ["prompt", "model", "aspect_ratio", "width", "height", "seed", "reference_image_paths"]
    side_effects = ["queues a job on the MiniMax-H3 Studio GPU", "writes the PNG to output_path"]

    input_schema = {
        "type": "object",
        "required": ["prompt", "output_path"],
        "properties": {
            "prompt": {"type": "string"},
            "model": {
                "type": "string",
                "enum": list(IMAGE_MODELS),
                "default": "qwen-image-21-turbo",
                "description": "MOSS only generates; Qwen and Klein also edit reference images.",
            },
            "reference_image_paths": {
                "type": "array",
                "items": {"type": "string"},
                "maxItems": MAX_INPUTS,
                "description": "Images to edit or combine. Qwen keeps the first image's canvas.",
            },
            "aspect_ratio": {
                "type": "string",
                "enum": [*ASPECT_RATIOS, "image"],
                "description": "Defaults to 1:1, or to `image` (follow the first input) when editing.",
            },
            "width": {"type": "integer", "description": "With height, snapped to the nearest supported ratio (~1 MP)."},
            "height": {"type": "integer"},
            "seed": {"type": "integer", "minimum": 0},
            "output_path": {"type": "string", "description": "Where to save the PNG (under projects/<id>/assets/images/)."},
            "timeout_seconds": {"type": "number", "default": 1800},
            "resume_job_id": {"type": "string"},
        },
    }

    def get_status(self) -> ToolStatus:
        return ToolStatus.AVAILABLE if configured() else ToolStatus.UNAVAILABLE

    def estimate_cost(self, inputs: dict[str, Any]) -> float:
        return 0.0

    def estimate_runtime(self, inputs: dict[str, Any]) -> float:
        return {"moss": 45.0, "flux-klein-4b": 8.0}.get(inputs.get("model") or "", 15.0)

    @staticmethod
    def build_request(inputs: dict[str, Any]) -> tuple[dict[str, Any], list[tuple[str, str]]]:
        model = inputs.get("model") or "qwen-image-21-turbo"
        if model not in IMAGE_MODELS:
            raise ValueError(f"model must be one of {', '.join(IMAGE_MODELS)}")
        refs = list(inputs.get("reference_image_paths") or [])
        if len(refs) > MAX_INPUTS:
            raise ValueError(f"at most {MAX_INPUTS} reference images")
        if refs and model == "moss":
            raise ValueError("moss only generates; use qwen-image-21-turbo or flux-klein-4b to edit")
        aspect = inputs.get("aspect_ratio")
        if not aspect:
            if refs:
                aspect = "image"
            elif inputs.get("width") and inputs.get("height"):
                aspect = snap_ratio(inputs["width"], inputs["height"])
            else:
                aspect = "1:1"
        fields = {"kind": "image", "image_model": model, "prompt": inputs["prompt"], "aspect_ratio": aspect, "seed": inputs.get("seed")}
        return fields, [("ref_image", path) for path in refs]

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        started = time.monotonic()
        try:
            fields, files = self.build_request(inputs)
            job, path = run_job(
                fields,
                files,
                "picture_url",
                inputs["output_path"],
                timeout_seconds=float(inputs.get("timeout_seconds", 1800)),
                resume_job_id=inputs.get("resume_job_id"),
            )
        except ValueError as exc:
            return ToolResult(success=False, error=str(exc))
        except H3StudioError as exc:
            data = {"job_id": exc.job_id, "timed_out": isinstance(exc, H3StudioTimeout)} if exc.job_id else {}
            return ToolResult(success=False, error=str(exc), data=data, duration_seconds=time.monotonic() - started)
        return ToolResult(
            success=True,
            data={
                "provider": self.provider,
                "model": job.get("image_model") or fields["image_model"],
                "models": job.get("models") or {},
                "mode": job.get("mode"),
                "job_id": job["id"],
                "seed": job.get("seed"),
                "width": job.get("width"),
                "height": job.get("height"),
                "prompt": job.get("prompt"),
                "output": str(path),
                "format": "png",
            },
            artifacts=[str(path)],
            cost_usd=0.0,
            duration_seconds=time.monotonic() - started,
            seed=job.get("seed"),
            model=job.get("image_model") or fields["image_model"],
        )
