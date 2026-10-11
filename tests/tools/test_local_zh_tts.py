"""Unit tests for the local Mandarin TTS tool and the local TTS service.

No model is loaded: the tool tests patch requests, and the service tests run
the FastAPI app with a fake backend.
"""

from __future__ import annotations

import io
import wave
from pathlib import Path
from unittest.mock import patch

import pytest
import requests
from fastapi.testclient import TestClient

from services.local_tts.backends import Backend
from services.local_tts.server import create_app
from tools.audio.local_zh_tts import LocalZhTTS
from tools.base_tool import ToolStatus


def _wav_bytes(seconds: float = 0.5, rate: int = 16000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(b"\x00\x00" * int(seconds * rate))
    return buf.getvalue()


class _Resp:
    def __init__(self, status_code=200, content=b"", json_data=None, headers=None):
        self.status_code = status_code
        self.content = content
        self._json = json_data
        self.headers = headers or {}
        self.text = str(json_data)

    def json(self):
        if self._json is None:
            raise ValueError("no json")
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")


@pytest.fixture
def service_url(monkeypatch):
    monkeypatch.setenv("LOCAL_TTS_URL", "http://127.0.0.1:9880/")
    return "http://127.0.0.1:9880"


class TestToolStatus:
    def test_unavailable_without_url(self, monkeypatch):
        monkeypatch.delenv("LOCAL_TTS_URL", raising=False)
        with patch("tools.audio.local_zh_tts.requests.get") as get:
            assert LocalZhTTS().get_status() == ToolStatus.UNAVAILABLE
        get.assert_not_called()

    def test_unavailable_when_service_down(self, service_url):
        with patch("tools.audio.local_zh_tts.requests.get", side_effect=requests.ConnectionError()):
            assert LocalZhTTS().get_status() == ToolStatus.UNAVAILABLE

    def test_degraded_while_model_not_loaded(self, service_url):
        with patch("tools.audio.local_zh_tts.requests.get", return_value=_Resp(json_data={"loaded": False})):
            assert LocalZhTTS().get_status() == ToolStatus.DEGRADED

    def test_available_when_loaded(self, service_url):
        with patch("tools.audio.local_zh_tts.requests.get", return_value=_Resp(json_data={"loaded": True})) as get:
            assert LocalZhTTS().get_status() == ToolStatus.AVAILABLE
        assert get.call_args.args[0] == f"{service_url}/health"


class TestToolExecute:
    def test_writes_audio_and_reports_duration(self, service_url, tmp_path):
        resp = _Resp(content=_wav_bytes(0.5), headers={"X-TTS-Backend": "indextts2", "X-TTS-Model": "IndexTTS-2"})
        out = tmp_path / "narration" / "s1.wav"
        with patch("tools.audio.local_zh_tts.requests.post", return_value=resp) as post:
            result = LocalZhTTS().execute({"text": " 你好 ", "voice_id": "narrator", "speed": 1.1, "output_path": str(out)})
        assert result.success, result.error
        assert post.call_args.args[0] == f"{service_url}/tts"
        assert post.call_args.kwargs["json"] == {"text": "你好", "speed": 1.1, "voice": "narrator"}
        assert out.read_bytes() == resp.content
        assert result.data["audio_duration_seconds"] == 0.5
        assert result.data["backend"] == "indextts2"
        assert result.model == "IndexTTS-2"
        assert result.cost_usd == 0.0

    def test_service_error_detail_is_surfaced(self, service_url, tmp_path):
        resp = _Resp(status_code=404, json_data={"detail": "Unknown voice 'x'"})
        with patch("tools.audio.local_zh_tts.requests.post", return_value=resp):
            result = LocalZhTTS().execute({"text": "你好", "voice": "x", "output_path": str(tmp_path / "a.wav")})
        assert not result.success
        assert "Unknown voice 'x'" in result.error

    def test_unreachable_service(self, service_url, tmp_path):
        with patch("tools.audio.local_zh_tts.requests.post", side_effect=requests.ConnectionError("refused")):
            result = LocalZhTTS().execute({"text": "你好", "output_path": str(tmp_path / "a.wav")})
        assert not result.success
        assert "unreachable" in result.error

    def test_requires_url(self, monkeypatch):
        monkeypatch.delenv("LOCAL_TTS_URL", raising=False)
        result = LocalZhTTS().execute({"text": "你好"})
        assert not result.success
        assert "LOCAL_TTS_URL" in result.error

    def test_registered_as_free_local_tts(self):
        tool = LocalZhTTS()
        info = tool.get_info()
        assert info["capability"] == "tts"
        assert info["provider"] == "local_tts"
        assert tool.estimate_cost({"text": "你好"}) == 0.0


class _FakeBackend(Backend):
    name = "fake"

    def __init__(self, needs_voice=True, fail_load=False):
        super().__init__(None)
        self.needs_voice = needs_voice
        self.fail_load = fail_load
        self.calls = []

    def load(self):
        if self.fail_load:
            raise RuntimeError("no weights")

    def synthesize(self, text, voice, speed, out_path: Path):
        self.calls.append((text, voice.name if voice else None, speed))
        out_path.write_bytes(_wav_bytes(0.25))


@pytest.fixture
def voices_dir(tmp_path):
    d = tmp_path / "voices"
    d.mkdir()
    (d / "narrator.wav").write_bytes(_wav_bytes(0.1))
    (d / "narrator.txt").write_text("参考音频的文字。", encoding="utf-8")
    (d / "calm.wav").write_bytes(_wav_bytes(0.1))
    return d


class TestService:
    def test_health_lists_voices(self, voices_dir):
        with TestClient(create_app(_FakeBackend(), voices_dir)) as client:
            body = client.get("/health").json()
        assert body["status"] == "ok"
        assert body["loaded"] is True
        assert body["voices"] == ["calm", "narrator"]

    def test_voices_report_transcripts(self, voices_dir):
        with TestClient(create_app(_FakeBackend(), voices_dir)) as client:
            voices = {v["name"]: v["has_transcript"] for v in client.get("/voices").json()["voices"]}
        assert voices == {"calm": False, "narrator": True}

    def test_tts_returns_wav(self, voices_dir):
        backend = _FakeBackend()
        with TestClient(create_app(backend, voices_dir)) as client:
            resp = client.post("/tts", json={"text": "你好", "voice": "narrator", "speed": 1.2})
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "audio/wav"
        assert resp.headers["X-TTS-Backend"] == "fake"
        assert backend.calls == [("你好", "narrator", 1.2)]

    def test_cloning_backend_requires_voice(self, voices_dir, monkeypatch):
        monkeypatch.delenv("LOCAL_TTS_DEFAULT_VOICE", raising=False)
        with TestClient(create_app(_FakeBackend(needs_voice=True), voices_dir)) as client:
            resp = client.post("/tts", json={"text": "你好"})
        assert resp.status_code == 400

    def test_default_voice_from_env(self, voices_dir, monkeypatch):
        monkeypatch.setenv("LOCAL_TTS_DEFAULT_VOICE", "calm")
        backend = _FakeBackend()
        with TestClient(create_app(backend, voices_dir)) as client:
            assert client.post("/tts", json={"text": "你好"}).status_code == 200
        assert backend.calls[0][1] == "calm"

    def test_unknown_voice_is_404(self, voices_dir):
        with TestClient(create_app(_FakeBackend(), voices_dir)) as client:
            resp = client.post("/tts", json={"text": "你好", "voice": "nobody"})
        assert resp.status_code == 404

    def test_speed_is_bounded(self, voices_dir):
        with TestClient(create_app(_FakeBackend(), voices_dir)) as client:
            resp = client.post("/tts", json={"text": "你好", "voice": "calm", "speed": 3})
        assert resp.status_code == 422

    def test_load_failure_reported_by_health(self, voices_dir):
        with TestClient(create_app(_FakeBackend(fail_load=True), voices_dir)) as client:
            body = client.get("/health").json()
            resp = client.post("/tts", json={"text": "你好", "voice": "calm"})
        assert body["status"] == "error"
        assert "no weights" in body["error"]
        assert resp.status_code == 503
