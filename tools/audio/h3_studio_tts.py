"""Speech on a self-hosted MiniMax-H3 Studio (local GPU, no per-call cost).

Qwen3-TTS, VoxCPM2 and OmniVoice through the Studio's audio.cpp runtime:
preset voices, voice cloning from a short clip, and voice design from a
description. Output is WAV.
"""

from __future__ import annotations

import time
import wave
from pathlib import Path
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

SPEECH_MODELS = ("qwen3_tts", "voxcpm2", "omnivoice")
QWEN_VOICES = ("Vivian", "Serena", "Uncle_Fu", "Dylan", "Eric", "Ryan", "Aiden", "Ono_Anna", "Sohee")
LANGUAGES = ("auto", "zh", "en", "ja", "ko", "de", "fr", "ru", "pt", "es", "it")


def wav_seconds(path: Path) -> float | None:
    try:
        with wave.open(str(path)) as wav:
            return round(wav.getnframes() / float(wav.getframerate()), 3)
    except (wave.Error, OSError, ZeroDivisionError):
        return None


class H3StudioTTS(BaseTool):
    name = "h3_studio_tts"
    version = "0.1.0"
    tier = ToolTier.VOICE
    capability = "tts"
    provider = "h3_studio"
    stability = ToolStability.BETA
    execution_mode = ExecutionMode.SYNC
    determinism = Determinism.STOCHASTIC
    runtime = ToolRuntime.LOCAL_GPU

    dependencies = ["env:H3_STUDIO_URL"]
    install_instructions = SETUP

    capabilities = ["text_to_speech", "voice_clone", "voice_design"]
    supports = {"voice_clone": True, "voice_design": True, "offline": True, "languages": list(LANGUAGES)}
    best_for = [
        "narration and dialogue on your own GPU, free per call",
        "a consistent character voice cloned from a short clean clip",
        "designing a voice from a description (age, timbre, mood)",
    ]
    not_good_for = ["more than 4000 characters per call", "word timestamps (transcribe the result instead)"]
    fallback_tools = ["local_zh_tts", "piper_tts"]
    resource_profile = ResourceProfile(cpu_cores=1, ram_mb=256, vram_mb=0, disk_mb=50, network_required=True)
    side_effects = ["queues a job on the MiniMax-H3 Studio GPU", "writes the WAV to output_path"]

    input_schema = {
        "type": "object",
        "required": ["text", "output_path"],
        "properties": {
            "text": {"type": "string", "maxLength": 4000},
            "model": {"type": "string", "enum": list(SPEECH_MODELS), "default": "qwen3_tts"},
            "mode": {
                "type": "string",
                "enum": ["generate", "clone", "design"],
                "description": "generate = preset voice, clone = voice_reference_path, design = instructions. Inferred when omitted.",
            },
            "voice": {"type": "string", "description": f"Qwen3-TTS preset voice: {', '.join(QWEN_VOICES)} (default Vivian)."},
            "instructions": {"type": "string", "description": "Voice description for design mode, or a delivery hint."},
            "voice_reference_path": {"type": "string", "description": "Clean speech clip of the voice to clone."},
            "reference_text": {"type": "string", "description": "Transcript of voice_reference_path (improves cloning)."},
            "language": {"type": "string", "enum": list(LANGUAGES), "default": "auto"},
            "output_path": {"type": "string", "description": "Where to save the WAV (under projects/<id>/assets/audio/)."},
            "timeout_seconds": {"type": "number", "default": 1800},
            "resume_job_id": {"type": "string"},
        },
    }

    def get_status(self) -> ToolStatus:
        return ToolStatus.AVAILABLE if configured() else ToolStatus.UNAVAILABLE

    def estimate_cost(self, inputs: dict[str, Any]) -> float:
        return 0.0

    def estimate_runtime(self, inputs: dict[str, Any]) -> float:
        return round(5 + len(inputs.get("text") or "") * 0.05, 1)

    @staticmethod
    def build_request(inputs: dict[str, Any]) -> tuple[dict[str, Any], list[tuple[str, str]]]:
        model = inputs.get("model") or "qwen3_tts"
        if model not in SPEECH_MODELS:
            raise ValueError(f"model must be one of {', '.join(SPEECH_MODELS)}")
        mode = inputs.get("mode") or (
            "clone" if inputs.get("voice_reference_path") else "design" if inputs.get("instructions") and not inputs.get("voice") else "generate"
        )
        if mode == "clone" and not inputs.get("voice_reference_path"):
            raise ValueError("clone mode needs voice_reference_path")
        if mode == "design" and not inputs.get("instructions"):
            raise ValueError("design mode needs instructions describing the voice")
        voice = inputs.get("voice")
        if mode == "generate" and model == "qwen3_tts" and voice and voice not in QWEN_VOICES:
            raise ValueError(f"voice must be one of {', '.join(QWEN_VOICES)}")
        fields = {
            "kind": "speech",
            "audio_model": model,
            "speech_mode": mode,
            "prompt": inputs["text"],
            "voice": voice if mode == "generate" else None,
            "instruction": inputs.get("instructions"),
            "reference_text": inputs.get("reference_text") if mode == "clone" else None,
            "language": inputs.get("language"),
        }
        files = [("voice_audio", inputs["voice_reference_path"])] if mode == "clone" else []
        return fields, files

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        started = time.monotonic()
        try:
            fields, files = self.build_request(inputs)
            job, path = run_job(
                fields,
                files,
                "audio_url",
                inputs["output_path"],
                timeout_seconds=float(inputs.get("timeout_seconds", 1800)),
                resume_job_id=inputs.get("resume_job_id"),
            )
        except ValueError as exc:
            return ToolResult(success=False, error=str(exc))
        except H3StudioError as exc:
            data = {"job_id": exc.job_id, "timed_out": isinstance(exc, H3StudioTimeout)} if exc.job_id else {}
            return ToolResult(success=False, error=str(exc), data=data, duration_seconds=time.monotonic() - started)
        options = job.get("audio_options") or {}
        return ToolResult(
            success=True,
            data={
                "provider": self.provider,
                "model": job.get("audio_model") or fields["audio_model"],
                "models": job.get("models") or {},
                "mode": options.get("speech_mode", fields["speech_mode"]),
                "voice": options.get("voice") if options.get("speech_mode") == "generate" else None,
                "job_id": job["id"],
                "text_length": len(inputs["text"]),
                "output": str(path),
                "format": "wav",
                "audio_duration_seconds": wav_seconds(path),
            },
            artifacts=[str(path)],
            cost_usd=0.0,
            duration_seconds=time.monotonic() - started,
            model=job.get("audio_model") or fields["audio_model"],
        )
