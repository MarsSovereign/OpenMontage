"""Qwen3-ASR transcription on a self-hosted MiniMax-H3 Studio (local GPU, no per-call cost).

Word timings come from Qwen's forced aligner, and the Studio only exports real
aligned spans, never spread-out estimates. Results are returned in the same
shape as ``transcriber`` (segments + word_timestamps) and the Studio's SRT,
word and JSON exports are saved next to the transcript.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from tools._h3_studio.client import SETUP, H3StudioClient, H3StudioError, H3StudioTimeout, configured
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

EXPORT_SUFFIXES = {"json": ".studio.json", "words": ".words.json", "srt": ".srt", "vtt": ".vtt", "txt": ".txt"}


class H3StudioTranscriber(BaseTool):
    name = "h3_studio_transcriber"
    version = "0.1.0"
    tier = ToolTier.ANALYZE
    capability = "analysis"
    provider = "h3_studio"
    stability = ToolStability.BETA
    execution_mode = ExecutionMode.SYNC
    determinism = Determinism.DETERMINISTIC
    runtime = ToolRuntime.LOCAL_GPU

    dependencies = ["env:H3_STUDIO_URL"]
    install_instructions = SETUP
    agent_skills = ["speech-to-text"]

    capabilities = ["transcribe", "word_timestamps", "subtitle_export"]
    supports = {"word_timestamps": True, "forced_alignment": True, "srt_export": True, "max_duration_seconds": 7200}
    best_for = [
        "subtitles for an assembled film: Qwen3-ASR with forced-aligned word timings",
        "Chinese and other Qwen3-ASR languages on your own GPU, free per call",
    ]
    not_good_for = ["media longer than 2 hours per call (split with exact offsets)", "speaker diarization"]
    fallback_tools = ["transcriber"]
    resource_profile = ResourceProfile(cpu_cores=1, ram_mb=256, vram_mb=0, disk_mb=50, network_required=True)
    idempotency_key_fields = []
    side_effects = ["queues a job on the MiniMax-H3 Studio GPU", "writes transcript files to output_dir"]

    input_schema = {
        "type": "object",
        "required": ["input_path", "output_dir"],
        "properties": {
            "input_path": {"type": "string", "description": "Audio or video file (at most 7200 s)."},
            "language": {"type": "string", "description": "ISO code (zh, en, ja, ...) or omit to auto-detect."},
            "timestamps": {"type": "string", "enum": ["word", "segment", "none"], "default": "word"},
            "output_dir": {"type": "string", "description": "Directory for the transcript and SRT (e.g. projects/<id>/assets/subtitles/)."},
            "timeout_seconds": {"type": "number", "default": 3600},
            "resume_job_id": {"type": "string"},
        },
    }
    output_schema = {
        "type": "object",
        "properties": {
            "segments": {"type": "array"},
            "word_timestamps": {"type": "array"},
            "language": {"type": "string"},
            "duration_seconds": {"type": "number"},
            "transcript_path": {"type": "string"},
            "srt_path": {"type": "string"},
        },
    }

    def get_status(self) -> ToolStatus:
        return ToolStatus.AVAILABLE if configured() else ToolStatus.UNAVAILABLE

    def estimate_cost(self, inputs: dict[str, Any]) -> float:
        return 0.0

    def estimate_runtime(self, inputs: dict[str, Any]) -> float:
        return 30.0

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        started = time.monotonic()
        source = Path(inputs["input_path"])
        output_dir = Path(inputs["output_dir"])
        try:
            client = H3StudioClient()
            job_id = inputs.get("resume_job_id")
            if not job_id:
                fields = {
                    "kind": "asr",
                    "audio_model": "qwen3_asr",
                    "prompt": f"Transcription of {source.name}",
                    "language": inputs.get("language"),
                    "timestamps": inputs.get("timestamps") or "word",
                }
                job_id = client.submit(fields, [("media", source)])["id"]
            job = client.wait(job_id, float(inputs.get("timeout_seconds", 3600)))
            files = {}
            for key, url in (job.get("transcript_urls") or {}).items():
                if key in EXPORT_SUFFIXES:
                    files[key] = str(client.download(url, output_dir / f"{source.stem}{EXPORT_SUFFIXES[key]}"))
        except H3StudioError as exc:
            data = {"job_id": exc.job_id, "timed_out": isinstance(exc, H3StudioTimeout)} if exc.job_id else {}
            return ToolResult(success=False, error=str(exc), data=data, duration_seconds=time.monotonic() - started)

        transcript = (job.get("result") or {}).get("transcript") or {}
        segments = [{"start": c["start"], "end": c["end"], "text": c["text"]} for c in transcript.get("segments", [])]
        words = [{"word": w["text"], "start": w["start"], "end": w["end"]} for w in transcript.get("words", [])]
        data = {
            "provider": self.provider,
            "model": "qwen3_asr",
            "job_id": job["id"],
            "segments": segments,
            "word_timestamps": words,
            "text": transcript.get("text", ""),
            "language": transcript.get("language") or inputs.get("language"),
            "duration_seconds": transcript.get("duration") or job.get("duration"),
            "timing_source": transcript.get("timing_source"),
            "srt_path": files.get("srt"),
            "exports": files,
        }
        output_dir.mkdir(parents=True, exist_ok=True)
        transcript_path = output_dir / f"{source.stem}_transcript.json"
        transcript_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        data["transcript_path"] = str(transcript_path)
        return ToolResult(
            success=True,
            data=data,
            artifacts=[str(transcript_path), *files.values()],
            cost_usd=0.0,
            duration_seconds=time.monotonic() - started,
            model="qwen3_asr",
        )
