"""Speech backends for the local TTS service.

Each backend loads one model once and synthesizes a WAV file per request.
Model imports happen inside ``load()`` so the service module (and its tests)
import without any of the heavy TTS packages installed.

Voices are reference clips in ``LOCAL_TTS_VOICES_DIR``: ``<name>.wav`` plus an
optional ``<name>.txt`` holding the clip's transcript (CosyVoice zero-shot
cloning uses it; IndexTTS2 does not need it).
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class Voice:
    name: str
    audio_path: Path
    transcript: Optional[str] = None


def list_voices(voices_dir: Path) -> dict[str, Voice]:
    voices: dict[str, Voice] = {}
    if not voices_dir.is_dir():
        return voices
    for wav in sorted(voices_dir.glob("*.wav")):
        txt = wav.with_suffix(".txt")
        transcript = txt.read_text(encoding="utf-8").strip() if txt.is_file() else None
        voices[wav.stem] = Voice(wav.stem, wav, transcript)
    return voices


class Backend:
    name = "base"
    needs_voice = True

    def __init__(self, model_dir: Optional[Path]):
        self.model_dir = model_dir
        self.model_label = name_from(model_dir) or self.name

    def load(self) -> None:
        raise NotImplementedError

    def synthesize(self, text: str, voice: Optional[Voice], speed: float, out_path: Path) -> None:
        raise NotImplementedError


def name_from(path: Optional[Path]) -> Optional[str]:
    return path.name if path else None


class IndexTTS2Backend(Backend):
    """IndexTTS2 (github.com/index-tts/index-tts), zero-shot voice cloning."""

    name = "indextts2"

    def load(self) -> None:
        from indextts.infer_v2 import IndexTTS2

        model_dir = self.model_dir or Path("checkpoints")
        self._tts = IndexTTS2(
            cfg_path=str(model_dir / "config.yaml"),
            model_dir=str(model_dir),
            use_fp16=True,
        )

    def synthesize(self, text: str, voice: Optional[Voice], speed: float, out_path: Path) -> None:
        self._tts.infer(
            spk_audio_prompt=str(voice.audio_path),
            text=text,
            output_path=str(out_path),
        )
        if speed != 1.0:
            _retime(out_path, speed)


class CosyVoiceBackend(Backend):
    """CosyVoice 2 (github.com/FunAudioLLM/CosyVoice), zero-shot voice cloning."""

    name = "cosyvoice"

    def load(self) -> None:
        from cosyvoice.cli.cosyvoice import CosyVoice2

        model_dir = self.model_dir or Path("pretrained_models/CosyVoice2-0.5B")
        self._tts = CosyVoice2(str(model_dir), load_jit=False, load_trt=False, fp16=True)

    def synthesize(self, text: str, voice: Optional[Voice], speed: float, out_path: Path) -> None:
        import torch
        import torchaudio
        from cosyvoice.utils.file_utils import load_wav

        prompt = load_wav(str(voice.audio_path), 16000)
        if voice.transcript:
            chunks = self._tts.inference_zero_shot(
                text, voice.transcript, prompt, stream=False, speed=speed
            )
        else:
            chunks = self._tts.inference_cross_lingual(text, prompt, stream=False, speed=speed)
        speech = torch.cat([c["tts_speech"] for c in chunks], dim=1)
        torchaudio.save(str(out_path), speech, self._tts.sample_rate)


class PiperBackend(Backend):
    """Piper, CPU-only. Lets the service run (and be tested) without a GPU."""

    name = "piper"
    needs_voice = False

    def load(self) -> None:
        if shutil.which("piper") is None:
            raise RuntimeError("piper executable not found (pip install piper-tts)")
        model = os.environ.get("LOCAL_TTS_PIPER_MODEL")
        if not model or not Path(model).is_file():
            raise RuntimeError("Set LOCAL_TTS_PIPER_MODEL to a Piper .onnx voice file")
        self._model = model
        self.model_label = Path(model).stem

    def synthesize(self, text: str, voice: Optional[Voice], speed: float, out_path: Path) -> None:
        proc = subprocess.run(
            [
                "piper",
                "--model", self._model,
                "--length-scale", str(round(1.0 / speed, 3)),
                "--output_file", str(out_path),
            ],
            input=text,
            capture_output=True,
            text=True,
            timeout=300,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"piper failed: {proc.stderr.strip()[-500:]}")


BACKENDS = {b.name: b for b in (IndexTTS2Backend, CosyVoiceBackend, PiperBackend)}


def _retime(path: Path, speed: float) -> None:
    """Change tempo without changing pitch (for backends with no speed control)."""
    tmp = path.with_suffix(".retime.wav")
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-i", str(path), "-filter:a", f"atempo={speed}", str(tmp)],
        check=True,
    )
    tmp.replace(path)
