"""Contract and loopback tests for the MiniMax-H3 Studio provider tools.

A small fake Studio on 127.0.0.1 speaks the real Studio's job API
(POST /api/jobs multipart, GET /api/jobs/{id}, /files/...), so the tools are
exercised end to end without a GPU or network access.
"""

from __future__ import annotations

import json
from email.parser import BytesParser
from email.policy import HTTP
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qsl

import pytest

from tools._h3_studio.client import H3StudioClient, last_line
from tools.analysis.h3_studio_transcriber import H3StudioTranscriber
from tools.audio.h3_studio_music import H3StudioMusic
from tools.audio.h3_studio_tts import H3StudioTTS
from tools.base_tool import BaseTool, ToolRuntime, ToolStatus
from tools.graphics.h3_studio_image import H3StudioImage, snap_ratio
from tools.tool_registry import ToolRegistry
from tools.video.h3_studio_video import H3StudioVideo

TOOLS = [H3StudioVideo, H3StudioImage, H3StudioTTS, H3StudioMusic, H3StudioTranscriber]
OUTPUT_KEY = {"video": ("video_url", "videos"), "image": ("picture_url", "pictures"), "speech": ("audio_url", "speech"), "music": ("audio_url", "music")}


class FakeStudio:
    """Records submissions; jobs finish on the first poll unless told to stay queued or fail."""

    def __init__(self, token: str = ""):
        self.token = token
        self.jobs: dict[str, dict] = {}
        self.submissions: list[dict] = []
        self.auth_headers: list[str | None] = []
        self.hold = False
        self.fail_with: str | None = None
        studio = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _json(self, status, body):
                payload = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def _authorized(self):
                studio.auth_headers.append(self.headers.get("Authorization"))
                return not studio.token or self.headers.get("Authorization") == f"Bearer {studio.token}"

            def do_GET(self):
                if self.path.startswith("/files/"):
                    payload = f"bytes of {self.path}".encode()
                    self.send_response(200)
                    self.send_header("Content-Length", str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)
                    return
                if not self._authorized():
                    return self._json(401, {"error": "missing or invalid API token"})
                job = studio.jobs.get(self.path.rsplit("/", 1)[-1])
                if job is None:
                    return self._json(404, {"error": "not found"})
                if job["status"] == "queued" and not studio.hold:
                    if studio.fail_with:
                        job.update(status="failed", error=f"Traceback (most recent call last):\n  ...\n{studio.fail_with}\n")
                    elif job["kind"] == "asr":
                        job.update(
                            status="done",
                            transcript_urls={"srt": f"/files/transcripts/{job['id']}.srt", "json": f"/files/transcripts/{job['id']}.json"},
                            result={"transcript": {
                                "text": "你好世界", "language": "Chinese", "duration": 2.0, "timing_source": "forced_alignment",
                                "words": [{"start": 0.1, "end": 0.5, "text": "你好"}, {"start": 0.6, "end": 1.0, "text": "世界"}],
                                "segments": [{"start": 0.1, "end": 1.0, "text": "你好世界"}],
                            }},
                        )
                    else:
                        key, folder = OUTPUT_KEY[job["kind"]]
                        job.update(status="done", **{key: f"/files/{folder}/{job['id']}.bin"})
                self._json(200, job)

            def do_POST(self):
                if not self._authorized():
                    return self._json(401, {"error": "missing or invalid API token"})
                body = self.rfile.read(int(self.headers["Content-Length"]))
                fields, files = {}, []
                # Like the real Studio, accept multipart (with uploads) and urlencoded (fields only).
                if self.headers["Content-Type"].startswith("application/x-www-form-urlencoded"):
                    fields = dict(parse_qsl(body.decode()))
                message = BytesParser(policy=HTTP).parsebytes(
                    f"Content-Type: {self.headers['Content-Type']}\r\n\r\n".encode() + body
                )
                for part in message.iter_parts() if message.is_multipart() else ():
                    name = part.get_param("name", header="content-disposition")
                    filename = part.get_filename()
                    value = part.get_payload(decode=True)
                    if filename:
                        files.append((name, filename, value))
                    else:
                        fields[name] = value.decode()
                studio.submissions.append({"fields": fields, "files": files})
                if fields.get("lora") == "nope":
                    return self._json(400, {"error": "unknown lora nope"})
                job_id = f"job{len(studio.jobs) + 1}"
                studio.jobs[job_id] = {
                    "id": job_id, "kind": fields.get("kind"), "status": "queued", "queue_position": 1,
                    "seed": 42, "prompt": fields.get("prompt"), "mode": "r2v", "lora": fields.get("lora") or "fasth3-omniref",
                    "preset": fields.get("resolution"), "duration": 5.0, "width": 1344, "height": 768,
                    "image_model": fields.get("image_model"), "audio_model": fields.get("audio_model"),
                    "audio_options": {"speech_mode": fields.get("speech_mode")}, "result": {"seconds": 47.5},
                    "models": {"base": "H3 ref2va"},
                }
                self._json(201, studio.jobs[job_id])

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def studio(monkeypatch):
    fake = FakeStudio(token="s3cret")
    monkeypatch.setenv("H3_STUDIO_URL", fake.url + "/")
    monkeypatch.setenv("H3_STUDIO_TOKEN", "s3cret")
    yield fake
    fake.close()


@pytest.fixture
def media(tmp_path):
    paths = {}
    for name in ("first.png", "last.png", "a.png", "b.png", "ref.mp4", "guide.wav", "voice.wav"):
        path = tmp_path / name
        path.write_bytes(b"x")
        paths[name] = str(path)
    return paths


# ------------------------------------------------------------------
# Contract compliance
# ------------------------------------------------------------------

@pytest.mark.parametrize("cls", TOOLS, ids=lambda c: c.name)
class TestContract:
    def test_identity(self, cls):
        tool = cls()
        assert isinstance(tool, BaseTool)
        assert tool.provider == "h3_studio"
        assert tool.runtime == ToolRuntime.LOCAL_GPU
        assert tool.name.startswith("h3_studio_")
        assert {"output_path", "output_dir"} & set(tool.input_schema["required"])
        assert tool.fallback_tools

    def test_free_and_estimated(self, cls):
        tool = cls()
        assert tool.estimate_cost({"prompt": "x", "text": "x"}) == 0.0
        assert tool.estimate_runtime({"prompt": "x", "text": "x"}) > 0

    def test_status_follows_env(self, cls, monkeypatch):
        monkeypatch.delenv("H3_STUDIO_URL", raising=False)
        assert cls().get_status() == ToolStatus.UNAVAILABLE
        monkeypatch.setenv("H3_STUDIO_URL", "http://127.0.0.1:9")
        assert cls().get_status() == ToolStatus.AVAILABLE

    def test_documented_in_env_example(self, cls):
        from pathlib import Path

        env = (Path(__file__).resolve().parents[2] / ".env.example").read_text()
        assert "H3_STUDIO_URL=" in env and "H3_STUDIO_TOKEN=" in env


def test_registry_discovers_tools_under_their_capabilities():
    registry = ToolRegistry()
    registry.discover()
    for cls in TOOLS:
        assert registry.get(cls.name) is not None
        assert cls.name in [tool.name for tool in registry.get_by_capability(cls.capability)]


# ------------------------------------------------------------------
# Request mapping
# ------------------------------------------------------------------

def test_video_operations_map_to_studio_fields(media):
    fields, files = H3StudioVideo.build_request({"prompt": "p"})
    assert files == [] and fields["aspect_ratio"] == "16:9" and fields["resolution"] == "768p"

    fields, files = H3StudioVideo.build_request(
        {"prompt": "p", "reference_image_path": media["first.png"], "last_image_path": media["last.png"], "duration": 8}
    )
    assert [f for f, _ in files] == ["image", "last_image"]
    assert fields["aspect_ratio"] == "image" and fields["duration"] == 8

    fields, files = H3StudioVideo.build_request({
        "prompt": "p", "reference_image_paths": [media["a.png"], media["b.png"]],
        "reference_video_paths": [media["ref.mp4"]], "audio_path": media["guide.wav"], "audio_start": 2.5,
        "model": "pika-flex-ref2va-sparse-all", "resolution": "544p",
    })
    assert [f for f, _ in files] == ["ref_image", "ref_image", "ref_video", "audio"]
    assert fields["lora"] == "pika-flex-ref2va-sparse-all" and fields["audio_start"] == 2.5 and fields["resolution"] == "544p"


@pytest.mark.parametrize("inputs, message", [
    ({"operation": "image_to_video"}, "first frame"),
    ({"operation": "text_to_video", "reference_image_path": "x.png"}, "takes no images"),
    ({"operation": "reference_to_video", "reference_image_path": "x.png"}, "not first/last"),
    ({"reference_image_paths": ["x.png"] * 7}, "at most 6"),
])
def test_video_rejects_inconsistent_inputs(inputs, message):
    with pytest.raises(ValueError, match=message):
        H3StudioVideo.build_request({"prompt": "p", **inputs})


def test_image_mapping_snaps_ratio_and_edits(media):
    assert snap_ratio(1024, 1536) == "3:4" and snap_ratio(1920, 1080) == "16:9"
    fields, files = H3StudioImage.build_request({"prompt": "p", "width": 1080, "height": 1920})
    assert fields["aspect_ratio"] == "9:16" and fields["image_model"] == "qwen-image-21-turbo" and files == []
    fields, files = H3StudioImage.build_request({"prompt": "p", "reference_image_paths": [media["a.png"]]})
    assert fields["aspect_ratio"] == "image" and files == [("ref_image", media["a.png"])]
    with pytest.raises(ValueError, match="only generates"):
        H3StudioImage.build_request({"prompt": "p", "model": "moss", "reference_image_paths": [media["a.png"]]})


def test_tts_infers_mode(media):
    fields, files = H3StudioTTS.build_request({"text": "hi", "voice": "Serena"})
    assert fields["speech_mode"] == "generate" and fields["voice"] == "Serena" and files == []
    fields, files = H3StudioTTS.build_request({"text": "hi", "instructions": "an old sailor"})
    assert fields["speech_mode"] == "design" and fields["voice"] is None
    fields, files = H3StudioTTS.build_request({"text": "hi", "voice_reference_path": media["voice.wav"], "reference_text": "ref"})
    assert fields["speech_mode"] == "clone" and files == [("voice_audio", media["voice.wav"])]
    with pytest.raises(ValueError, match="voice must be one of"):
        H3StudioTTS.build_request({"text": "hi", "voice": "alloy"})


def test_music_range():
    with pytest.raises(ValueError):
        H3StudioMusic.build_request({"prompt": "p", "duration_seconds": 5})


def test_last_line_picks_the_exception():
    assert last_line("Traceback\n  File x\nRuntimeError: boom\n") == "RuntimeError: boom"


# ------------------------------------------------------------------
# End to end against the fake Studio
# ------------------------------------------------------------------

def test_reference_video_job_end_to_end(studio, media, tmp_path):
    out = tmp_path / "projects" / "film" / "assets" / "video" / "shot01.mp4"
    result = H3StudioVideo().execute({
        "prompt": "<Subject 1> walks in", "reference_image_paths": [media["a.png"]],
        "reference_video_paths": [media["ref.mp4"]], "audio_path": media["guide.wav"],
        "duration": 6, "output_path": str(out),
    })
    assert result.success, result.error
    assert out.read_bytes() == b"bytes of /files/videos/job1.bin"
    assert result.data["job_id"] == "job1" and result.data["seed"] == 42 and result.cost_usd == 0.0
    sent = studio.submissions[0]
    assert sent["fields"]["kind"] == "video" and sent["fields"]["duration"] == "6"
    assert [(name, filename) for name, filename, _ in sent["files"]] == [
        ("ref_image", "a.png"), ("ref_video", "ref.mp4"), ("audio", "guide.wav"),
    ]
    assert set(studio.auth_headers) == {"Bearer s3cret"}


def test_image_tts_music_end_to_end(studio, media, tmp_path):
    image = H3StudioImage().execute({"prompt": "card", "reference_image_paths": [media["a.png"]], "output_path": str(tmp_path / "card.png")})
    speech = H3StudioTTS().execute({"text": "hello", "instructions": "a calm narrator", "output_path": str(tmp_path / "line.wav")})
    music = H3StudioMusic().execute({"prompt": "piano", "duration_seconds": 60, "output_path": str(tmp_path / "score.mp3")})
    assert image.success and speech.success and music.success, (image.error, speech.error, music.error)
    assert (tmp_path / "card.png").read_bytes().endswith(b"/files/pictures/job1.bin")
    assert speech.data["mode"] == "design"
    assert music.data["audio_duration_seconds"] == 47.5
    assert [s["fields"]["kind"] for s in studio.submissions] == ["image", "speech", "music"]


def test_studio_validation_error_is_surfaced(studio, tmp_path):
    result = H3StudioVideo().execute({"prompt": "p", "model": "nope", "output_path": str(tmp_path / "x.mp4")})
    assert not result.success and "unknown lora nope" in result.error


def test_failed_job_reports_the_last_traceback_line(studio, tmp_path):
    studio.fail_with = "RuntimeError: CUDA out of memory"
    result = H3StudioVideo().execute({"prompt": "p", "output_path": str(tmp_path / "x.mp4")})
    assert not result.success and result.error.endswith("RuntimeError: CUDA out of memory")
    assert result.data["job_id"] == "job1" and result.data["timed_out"] is False


def test_timeout_keeps_the_job_and_resume_collects_it(studio, tmp_path):
    studio.hold = True
    out = tmp_path / "x.mp4"
    first = H3StudioVideo().execute({"prompt": "p", "output_path": str(out), "timeout_seconds": 0})
    assert not first.success and first.data == {"job_id": "job1", "timed_out": True}
    assert "resume_job_id='job1'" in first.error and not out.exists()
    studio.hold = False
    second = H3StudioVideo().execute({"prompt": "p", "output_path": str(out), "resume_job_id": "job1"})
    assert second.success and out.exists()
    assert len(studio.submissions) == 1


def test_wrong_token_is_reported(studio, monkeypatch, tmp_path):
    monkeypatch.setenv("H3_STUDIO_TOKEN", "wrong")
    result = H3StudioMusic().execute({"prompt": "p", "output_path": str(tmp_path / "x.mp3")})
    assert not result.success and "HTTP 401" in result.error and "invalid API token" in result.error


def test_client_without_url_explains_setup(monkeypatch):
    monkeypatch.delenv("H3_STUDIO_URL", raising=False)
    with pytest.raises(Exception, match="H3_STUDIO_URL"):
        H3StudioClient()


def test_transcriber_returns_aligned_words_and_srt(studio, media, tmp_path):
    out = tmp_path / "subs"
    result = H3StudioTranscriber().execute({"input_path": media["guide.wav"], "language": "zh", "output_dir": str(out)})
    assert result.success, result.error
    sent = studio.submissions[0]
    assert sent["fields"]["kind"] == "asr" and sent["fields"]["language"] == "zh" and sent["fields"]["timestamps"] == "word"
    assert [name for name, _, _ in sent["files"]] == ["media"]
    assert result.data["word_timestamps"] == [{"word": "你好", "start": 0.1, "end": 0.5}, {"word": "世界", "start": 0.6, "end": 1.0}]
    assert result.data["segments"][0]["text"] == "你好世界"
    assert (out / "guide.srt").read_bytes() == b"bytes of /files/transcripts/job1.srt"
    saved = json.loads((out / "guide_transcript.json").read_text(encoding="utf-8"))
    assert saved["timing_source"] == "forced_alignment" and saved["srt_path"].endswith("guide.srt")
