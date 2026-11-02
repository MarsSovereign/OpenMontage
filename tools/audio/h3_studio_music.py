"""Songs and scores with MiniMax Music 3 on a self-hosted MiniMax-H3 Studio (local GPU, no per-call cost)."""

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

DURATION_RANGE = (10, 300)


class H3StudioMusic(BaseTool):
    name = "h3_studio_music"
    version = "0.1.0"
    tier = ToolTier.GENERATE
    capability = "music_generation"
    provider = "h3_studio"
    stability = ToolStability.BETA
    execution_mode = ExecutionMode.SYNC
    determinism = Determinism.SEEDED
    runtime = ToolRuntime.LOCAL_GPU

    dependencies = ["env:H3_STUDIO_URL"]
    install_instructions = SETUP

    capabilities = ["text_to_music", "lyrics_to_song"]
    supports = {"seed": True, "lyrics": True, "instrumental": True, "offline": True, "max_duration_seconds": DURATION_RANGE[1]}
    best_for = ["a film score or theme song with lyrics on your own GPU, free per call", "10 s to 5 min tracks"]
    not_good_for = ["exact length (the model may stop before max duration)", "stems or loops"]
    fallback_tools = ["comfyui_music", "music_gen"]
    resource_profile = ResourceProfile(cpu_cores=1, ram_mb=256, vram_mb=0, disk_mb=50, network_required=True)
    idempotency_key_fields = ["prompt", "lyrics", "duration_seconds", "seed"]
    side_effects = ["queues a job on the MiniMax-H3 Studio GPU", "writes the MP3 to output_path"]

    input_schema = {
        "type": "object",
        "required": ["prompt", "output_path"],
        "properties": {
            "prompt": {"type": "string", "description": "Caption: genre, mood, instruments, tempo, vocals."},
            "lyrics": {"type": "string", "description": "Song lyrics with [verse]/[chorus] tags; empty for instrumental."},
            "duration_seconds": {
                "type": "number",
                "minimum": DURATION_RANGE[0],
                "maximum": DURATION_RANGE[1],
                "default": 60,
                "description": "Maximum length; the song may end earlier (the actual length is returned).",
            },
            "seed": {"type": "integer", "minimum": 0},
            "output_path": {"type": "string", "description": "Where to save the MP3 (under projects/<id>/assets/music/)."},
            "timeout_seconds": {"type": "number", "default": 3600},
            "resume_job_id": {"type": "string"},
        },
    }

    def get_status(self) -> ToolStatus:
        return ToolStatus.AVAILABLE if configured() else ToolStatus.UNAVAILABLE

    def estimate_cost(self, inputs: dict[str, Any]) -> float:
        return 0.0

    def estimate_runtime(self, inputs: dict[str, Any]) -> float:
        # Measured on an RTX 4090: 30 s ~60 s, 60 s ~100 s.
        return round(20 + float(inputs.get("duration_seconds") or 60) * 1.35, 1)

    @staticmethod
    def build_request(inputs: dict[str, Any]) -> tuple[dict[str, Any], list[tuple[str, str]]]:
        duration = float(inputs.get("duration_seconds") or 60)
        if not DURATION_RANGE[0] <= duration <= DURATION_RANGE[1]:
            raise ValueError(f"duration_seconds must be between {DURATION_RANGE[0]} and {DURATION_RANGE[1]}")
        fields = {"kind": "music", "prompt": inputs["prompt"], "lyrics": inputs.get("lyrics"), "duration": duration, "seed": inputs.get("seed")}
        return fields, []

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        started = time.monotonic()
        try:
            fields, files = self.build_request(inputs)
            job, path = run_job(
                fields,
                files,
                "audio_url",
                inputs["output_path"],
                timeout_seconds=float(inputs.get("timeout_seconds", 3600)),
                resume_job_id=inputs.get("resume_job_id"),
            )
        except ValueError as exc:
            return ToolResult(success=False, error=str(exc))
        except H3StudioError as exc:
            data = {"job_id": exc.job_id, "timed_out": isinstance(exc, H3StudioTimeout)} if exc.job_id else {}
            return ToolResult(success=False, error=str(exc), data=data, duration_seconds=time.monotonic() - started)
        result = job.get("result") or {}
        return ToolResult(
            success=True,
            data={
                "provider": self.provider,
                "model": "minimax-music-3",
                "models": job.get("models") or {},
                "job_id": job["id"],
                "seed": job.get("seed"),
                "requested_duration_seconds": fields["duration"],
                "audio_duration_seconds": result.get("seconds") or job.get("duration"),
                "output": str(path),
                "format": "mp3",
            },
            artifacts=[str(path)],
            cost_usd=0.0,
            duration_seconds=time.monotonic() - started,
            seed=job.get("seed"),
            model="minimax-music-3",
        )
