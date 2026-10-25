"""IndexTTS2.5 and CosyVoice3 backends of the local TTS service, with stub models."""

import sys
import types
from pathlib import Path

import pytest

from services.local_tts.backends import (
    BACKENDS,
    CosyVoice3Backend,
    IndexTTS25Backend,
    Voice,
)


@pytest.fixture
def voice(tmp_path: Path) -> Voice:
    wav = tmp_path / "narrator.wav"
    wav.write_bytes(b"wav")
    return Voice("narrator", wav, "参考原文")


def _install(monkeypatch, name: str, **attrs):
    module = types.ModuleType(name)
    for key, value in attrs.items():
        setattr(module, key, value)
    monkeypatch.setitem(sys.modules, name, module)


def test_new_backends_are_selectable():
    assert BACKENDS["indextts25"] is IndexTTS25Backend
    assert BACKENDS["cosyvoice3"] is CosyVoice3Backend


def test_indextts25_passes_lang_and_native_duration(monkeypatch, tmp_path, voice):
    calls = {}

    class FakeIndexTTS2:
        def __init__(self, **kwargs):
            calls["init"] = kwargs

        def infer(self, spk_audio_prompt, text, output_path, lang, duration_factor=1.0):
            calls["infer"] = dict(text=text, lang=lang, duration_factor=duration_factor)

    _install(monkeypatch, "indextts", __path__=[])
    _install(monkeypatch, "indextts.infer_v2_5", IndexTTS2=FakeIndexTTS2)
    backend = IndexTTS25Backend(tmp_path / "checkpoints_25")
    backend.load()
    backend.synthesize("你好", voice, 1.25, tmp_path / "out.wav")
    assert calls["init"]["use_bf16"] is True
    assert calls["init"]["cfg_path"].endswith("config.yaml")
    assert calls["infer"] == {"text": "你好", "lang": "ZH", "duration_factor": 0.8}


def test_cosyvoice3_adds_system_prefix(monkeypatch, tmp_path, voice):
    torch = pytest.importorskip("torch")
    pytest.importorskip("torchaudio")
    calls = {}

    class FakeAutoModel:
        sample_rate = 24000

        def __init__(self, model_dir):
            calls["model_dir"] = model_dir

        def inference_zero_shot(self, text, prompt_text, prompt_wav, stream, speed):
            calls["prompt_text"] = prompt_text
            return [{"tts_speech": torch.zeros(1, 240)}]

    _install(monkeypatch, "cosyvoice", __path__=[])
    _install(monkeypatch, "cosyvoice.cli", __path__=[])
    _install(monkeypatch, "cosyvoice.cli.cosyvoice", AutoModel=FakeAutoModel)
    backend = CosyVoice3Backend(None)
    backend.load()
    backend.synthesize("你好", voice, 1.0, tmp_path / "out.wav")
    assert calls["model_dir"].endswith("Fun-CosyVoice3-0.5B")
    assert calls["prompt_text"] == "You are a helpful assistant.<|endofprompt|>参考原文"
