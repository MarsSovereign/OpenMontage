"""Local Mandarin TTS through the OpenMontage local TTS service.

The model (IndexTTS2 or CosyVoice) runs in services/local_tts as its own
process so it keeps the GPU loaded between calls and never queues behind
ComfyUI video jobs. This tool only talks HTTP to that service.
"""

from __future__ import annotations

import os
import time
import wave
from pathlib import Path
from typing import Any

import requests

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

HEALTH_TIMEOUT_SECONDS = 2
SYNTH_TIMEOUT_SECONDS = 600


class LocalZhTTS(BaseTool):
    name = "local_zh_tts"
    version = "0.1.0"
    tier = ToolTier.VOICE
    capability = "tts"
    provider = "local_tts"
    stability = ToolStability.EXPERIMENTAL
    execution_mode = ExecutionMode.SYNC
    determinism = Determinism.STOCHASTIC
    runtime = ToolRuntime.LOCAL

    dependencies = ["env:LOCAL_TTS_URL"]
    install_instructions = (
        "Start the local TTS service (see services/local_tts/README.md), then set\n"
        "  LOCAL_TTS_URL=http://127.0.0.1:9880\n"
        "in .env."
    )
    agent_skills = ["text-to-speech"]

    capabilities = [
        "text_to_speech",
        "offline_generation",
        "voice_cloning",
    ]
    supports = {
        "voice_cloning": True,
        "multilingual": True,
        "offline": True,
        "native_audio": True,
    }
    best_for = [
        "natural Mandarin narration without a cloud API",
        "fallback when cloud TTS quota or budget is exhausted",
        "consistent cloned narrator voice from a local reference clip",
    ]
    not_good_for = [
        "machines without a GPU (use piper_tts)",
        "word-level timestamps (run the transcriber on the output)",
    ]

    input_schema = {
        "type": "object",
        "required": ["text"],
        "properties": {
            "text": {"type": "string", "minLength": 1, "maxLength": 2000},
            "voice": {
                "type": "string",
                "description": "Reference voice name from the service's voices folder. "
                "Defaults to the service's LOCAL_TTS_DEFAULT_VOICE.",
            },
            "voice_id": {"type": "string", "description": "Alias for voice."},
            "speed": {"type": "number", "minimum": 0.5, "maximum": 2.0, "default": 1.0},
            "output_path": {"type": "string"},
        },
    }

    resource_profile = ResourceProfile(
        cpu_cores=2, ram_mb=4000, vram_mb=8000, disk_mb=10000, network_required=False
    )
    retry_policy = RetryPolicy(max_retries=1, retryable_errors=["ConnectionError", "Timeout"])
    idempotency_key_fields = ["text", "voice", "speed"]
    side_effects = ["writes audio file to output_path"]
    user_visible_verification = ["Listen to generated audio for naturalness and pronunciation"]

    @staticmethod
    def _base_url() -> str:
        return os.environ.get("LOCAL_TTS_URL", "").rstrip("/")

    def _health(self) -> dict[str, Any] | None:
        base = self._base_url()
        if not base:
            return None
        try:
            resp = requests.get(f"{base}/health", timeout=HEALTH_TIMEOUT_SECONDS)
            resp.raise_for_status()
            return resp.json()
        except (requests.RequestException, ValueError):
            return None

    def get_status(self) -> ToolStatus:
        health = self._health()
        if health is None:
            return ToolStatus.UNAVAILABLE
        if not health.get("loaded"):
            return ToolStatus.DEGRADED
        return ToolStatus.AVAILABLE

    def estimate_cost(self, inputs: dict[str, Any]) -> float:
        return 0.0

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        base = self._base_url()
        if not base:
            return ToolResult(success=False, error="LOCAL_TTS_URL is not set. " + self.install_instructions)
        text = (inputs.get("text") or "").strip()
        if not text:
            return ToolResult(success=False, error="text is required")

        payload: dict[str, Any] = {"text": text, "speed": float(inputs.get("speed", 1.0))}
        voice = inputs.get("voice") or inputs.get("voice_id")
        if voice:
            payload["voice"] = voice

        start = time.time()
        try:
            resp = requests.post(f"{base}/tts", json=payload, timeout=SYNTH_TIMEOUT_SECONDS)
        except requests.RequestException as exc:
            return ToolResult(success=False, error=f"Local TTS service unreachable at {base}: {exc}")
        if resp.status_code != 200:
            try:
                detail = resp.json().get("detail", resp.text)
            except ValueError:
                detail = resp.text
            return ToolResult(success=False, error=f"Local TTS service error {resp.status_code}: {detail}")
        if not resp.content:
            return ToolResult(success=False, error="Local TTS service returned empty audio")

        output_path = Path(inputs.get("output_path", "local_tts_output.wav"))
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(resp.content)

        model = resp.headers.get("X-TTS-Model", "unknown")
        return ToolResult(
            success=True,
            data={
                "provider": self.provider,
                "backend": resp.headers.get("X-TTS-Backend"),
                "model": model,
                "voice": voice,
                "text_length": len(text),
                "output": str(output_path),
                "format": "wav",
                "audio_duration_seconds": _wav_duration(output_path),
            },
            artifacts=[str(output_path)],
            model=model,
            cost_usd=0.0,
            duration_seconds=round(time.time() - start, 2),
        )


def _wav_duration(path: Path) -> float | None:
    try:
        with wave.open(str(path), "rb") as wav:
            return round(wav.getnframes() / float(wav.getframerate()), 3)
    except (wave.Error, EOFError, OSError):
        return None
