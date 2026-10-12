"""Contract tests for the DashScope (Alibaba Cloud Bailian) Wan video tool.

No real credentials or network calls: requests is replaced with a fake module.

Run: pytest tests/contracts/test_wan_dashscope_video.py -v
"""

from __future__ import annotations

import base64
import json
import sys
import types

import pytest

from tools.base_tool import BaseTool, ExecutionMode, ToolRuntime, ToolStatus, ToolTier
from tools.video.wan_dashscope_video import WanDashscopeVideo


class FakeResponse:
    def __init__(self, json_data=None, content=b"", status_code=200):
        self._json = json_data
        self.content = content
        self.status_code = status_code
        self.text = json.dumps(json_data) if json_data is not None else ""

    def json(self):
        if self._json is None:
            raise ValueError("no json")
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def _install_fake_requests(monkeypatch, post_responses, get_responses):
    calls = {"post": [], "get": []}
    fake = types.ModuleType("requests")

    def fake_post(url, headers=None, json=None, timeout=None, **_):
        calls["post"].append({"url": url, "headers": headers, "json": json})
        return post_responses.pop(0)

    def fake_get(url, headers=None, timeout=None, **_):
        calls["get"].append({"url": url, "headers": headers})
        return get_responses.pop(0)

    fake.post = fake_post
    fake.get = fake_get
    monkeypatch.setitem(sys.modules, "requests", fake)
    return calls


@pytest.fixture()
def dashscope_env(monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-fake-test-key")
    monkeypatch.delenv("DASHSCOPE_BASE_URL", raising=False)
    monkeypatch.setattr("tools.video.wan_dashscope_video.time.sleep", lambda _s: None)


def _submitted():
    return FakeResponse({"output": {"task_id": "t-1", "task_status": "PENDING"}, "request_id": "r"})


def _succeeded():
    return FakeResponse({
        "output": {"task_id": "t-1", "task_status": "SUCCEEDED", "video_url": "https://oss.example/v.mp4"},
        "usage": {"duration": 5, "video_count": 1},
    })


class TestContract:

    def test_identity(self):
        tool = WanDashscopeVideo()
        assert isinstance(tool, BaseTool)
        assert tool.name == "wan_dashscope_video"
        assert tool.provider == "dashscope"
        assert tool.capability == "video_generation"
        assert tool.tier == ToolTier.GENERATE
        assert tool.runtime == ToolRuntime.API
        assert tool.execution_mode == ExecutionMode.ASYNC

    def test_schema_avoids_fal_upload_path(self):
        # video_selector uploads reference_image_path to fal.ai when a tool
        # declares image_url; this tool inlines local images instead.
        props = WanDashscopeVideo().input_schema["properties"]
        assert "image_url" not in props
        assert "reference_image_path" in props

    def test_status(self, monkeypatch):
        monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
        assert WanDashscopeVideo().get_status() == ToolStatus.UNAVAILABLE
        monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-x")
        assert WanDashscopeVideo().get_status() == ToolStatus.AVAILABLE

    def test_dry_run(self):
        result = WanDashscopeVideo().dry_run({"prompt": "test"})
        assert result["tool"] == "wan_dashscope_video"


class TestCost:

    def test_flash_silent_720p_five_seconds(self):
        tool = WanDashscopeVideo()
        inputs = {"prompt": "x", "operation": "image_to_video", "duration": "5"}
        assert tool.estimate_cost_cny(inputs) == pytest.approx(0.75)

    def test_flash_1080p(self):
        tool = WanDashscopeVideo()
        inputs = {"prompt": "x", "operation": "image_to_video", "resolution": "1080p", "duration": 4}
        assert tool.estimate_cost_cny(inputs) == pytest.approx(1.0)

    def test_flash_with_audio_uses_conservative_fallback(self):
        tool = WanDashscopeVideo()
        inputs = {"prompt": "x", "operation": "image_to_video", "audio": True, "duration": 5}
        assert tool.estimate_cost_cny(inputs) == pytest.approx(5.0)

    def test_wan3_is_billed_with_audio_even_when_stripped(self):
        tool = WanDashscopeVideo()
        assert tool.estimate_cost_cny({"prompt": "x", "duration": 5}) == pytest.approx(3.0)

    def test_usd_conversion(self, monkeypatch):
        monkeypatch.setenv("DASHSCOPE_CNY_PER_USD", "7.5")
        tool = WanDashscopeVideo()
        assert tool.estimate_cost({"prompt": "x", "duration": 5}) == pytest.approx(0.4)


class TestBody:

    def test_flash_i2v_sends_img_url_and_audio_false(self, tmp_path):
        image = tmp_path / "frame.png"
        image.write_bytes(b"\x89PNGfake")
        body = WanDashscopeVideo()._build_body(
            {"prompt": "猫在草地上奔跑", "operation": "image_to_video", "reference_image_path": str(image)},
            model="wan2.6-i2v-flash",
        )
        assert body["model"] == "wan2.6-i2v-flash"
        img = body["input"]["img_url"]
        assert img.startswith("data:image/png;base64,")
        assert base64.b64decode(img.split(",", 1)[1]) == b"\x89PNGfake"
        assert body["parameters"]["audio"] is False
        assert body["parameters"]["resolution"] == "720P"
        assert body["parameters"]["duration"] == 5
        assert body["parameters"]["watermark"] is False

    def test_wan3_i2v_uses_first_frame_media(self):
        body = WanDashscopeVideo()._build_body(
            {"prompt": "x", "operation": "image_to_video", "reference_image_url": "https://a/b.png"},
            model="wan3.0-video",
        )
        assert body["input"]["media"] == [{"type": "first_frame", "url": "https://a/b.png"}]
        assert body["parameters"]["ratio"] == "adaptive"
        assert "audio" not in body["parameters"]
        assert "img_url" not in body["input"]

    def test_wan3_t2v_passes_ratio(self):
        body = WanDashscopeVideo()._build_body(
            {"prompt": "x", "aspect_ratio": "9:16", "seed": 7, "negative_prompt": "花朵"},
            model="wan3.0-video",
        )
        assert body["parameters"]["ratio"] == "9:16"
        assert body["parameters"]["seed"] == 7
        assert body["input"]["negative_prompt"] == "花朵"
        assert "media" not in body["input"]


class TestExecute:

    def test_missing_key(self, monkeypatch):
        monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
        result = WanDashscopeVideo().execute({"prompt": "x"})
        assert not result.success
        assert "DASHSCOPE_API_KEY" in result.error

    def test_i2v_requires_image(self, dashscope_env):
        result = WanDashscopeVideo().execute({"prompt": "x", "operation": "image_to_video"})
        assert not result.success
        assert "reference_image" in result.error

    def test_flash_rejects_text_to_video(self, dashscope_env):
        result = WanDashscopeVideo().execute({"prompt": "x", "model": "wan2.6-i2v-flash"})
        assert not result.success
        assert "wan3.0-video" in result.error

    def test_happy_path_flash(self, dashscope_env, monkeypatch, tmp_path):
        calls = _install_fake_requests(
            monkeypatch,
            [_submitted()],
            [FakeResponse({"output": {"task_id": "t-1", "task_status": "RUNNING"}}), _succeeded(),
             FakeResponse(content=b"mp4bytes")],
        )
        monkeypatch.setattr("tools.video._shared.probe_output", lambda p: {"file_size_bytes": 8})
        out = tmp_path / "shot.mp4"
        result = WanDashscopeVideo().execute({
            "prompt": "x",
            "operation": "image_to_video",
            "reference_image_url": "https://a/b.png",
            "output_path": str(out),
        })
        assert result.success, result.error
        assert out.read_bytes() == b"mp4bytes"
        submit = calls["post"][0]
        assert submit["url"] == "https://dashscope.aliyuncs.com/api/v1/services/aigc/video-generation/video-synthesis"
        assert submit["headers"]["X-DashScope-Async"] == "enable"
        assert submit["headers"]["Authorization"] == "Bearer sk-fake-test-key"
        assert calls["get"][0]["url"] == "https://dashscope.aliyuncs.com/api/v1/tasks/t-1"
        assert result.data["model"] == "wan2.6-i2v-flash"
        assert result.data["estimated_cost_cny"] == pytest.approx(0.75)
        assert result.data["audio_stripped_locally"] is False

    def test_base_url_override(self, dashscope_env, monkeypatch, tmp_path):
        monkeypatch.setenv("DASHSCOPE_BASE_URL", "https://ws-123.cn-beijing.maas.aliyuncs.com/api/v1/")
        calls = _install_fake_requests(monkeypatch, [_submitted()], [_succeeded(), FakeResponse(content=b"v")])
        monkeypatch.setattr("tools.video._shared.probe_output", lambda p: {})
        monkeypatch.setattr(WanDashscopeVideo, "_strip_audio", staticmethod(lambda p: True))
        result = WanDashscopeVideo().execute({"prompt": "x", "output_path": str(tmp_path / "a.mp4")})
        assert result.success, result.error
        assert calls["post"][0]["url"].startswith("https://ws-123.cn-beijing.maas.aliyuncs.com/api/v1/services/")
        assert result.data["audio_stripped_locally"] is True

    def test_task_failure_is_reported(self, dashscope_env, monkeypatch):
        _install_fake_requests(
            monkeypatch,
            [_submitted()],
            [FakeResponse({"output": {"task_id": "t-1", "task_status": "FAILED",
                                      "code": "DataInspectionFailed", "message": "bad input"}})],
        )
        result = WanDashscopeVideo().execute({"prompt": "x"})
        assert not result.success
        assert "DataInspectionFailed" in result.error

    def test_submit_error_redacts_key(self, dashscope_env, monkeypatch):
        _install_fake_requests(
            monkeypatch,
            [FakeResponse({"code": "InvalidApiKey", "message": "key sk-fake-test-key is invalid"}, status_code=401)],
            [],
        )
        result = WanDashscopeVideo().execute({"prompt": "x"})
        assert not result.success
        assert "InvalidApiKey" in result.error
        assert "sk-fake-test-key" not in result.error
