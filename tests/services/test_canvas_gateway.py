"""Canvas gateway: OpenAI-style requests from infinite-canvas map onto local runs."""

from __future__ import annotations

import json
import sys
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from services.canvas_gateway.adapters import frames_for
from services.canvas_gateway.server import Gateway, create_app
from services.canvas_gateway.services import ServiceManager, ServiceSpec

WORKFLOW = {
    "1": {"class_type": "CLIPTextEncode", "inputs": {"text": ""}},
    "2": {"class_type": "KSampler", "inputs": {"seed": 0}},
    "3": {"class_type": "EmptyLatent", "inputs": {"width": 0, "height": 0, "length": 0}},
    "4": {"class_type": "LoadImage", "inputs": {"image": ""}},
    "5": {"class_type": "LoadAudio", "inputs": {"audio": ""}},
    "9": {"class_type": "SaveVideo", "inputs": {}},
}


class FakeComfy:
    def __init__(self) -> None:
        self.submitted: list[dict] = []
        self.uploads: list[str] = []

    def upload_file(self, path: Path) -> str:
        self.uploads.append(Path(path).name)
        return f"canvas/{Path(path).name}"

    def generate(self, workflow, output_node, dest, timeout=600):
        self.submitted.append(workflow)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"fake-output")
        return [dest]


class FakeServices(ServiceManager):
    def __init__(self, specs):
        super().__init__(specs, probe=lambda url: True)
        self.ensured: list[str] = []

    def ensure(self, name: str) -> None:
        self.ensured.append(name)


@pytest.fixture()
def setup(tmp_path):
    wf_path = tmp_path / "wf_api.json"
    wf_path.write_text(json.dumps(WORKFLOW), encoding="utf-8")
    config = {
        "work_dir": str(tmp_path / "jobs"),
        "services": {"comfyui": {"url": "http://127.0.0.1:8189"}, "tts": {"url": "http://127.0.0.1:9880"}},
        "models": [
            {
                "name": "local-qwen-image", "capability": "image", "type": "comfyui", "service": "comfyui",
                "workflow": str(wf_path), "output_node": "9", "default_size": [768, 1344], "size_multiple": 16,
                "inputs": {"prompt": ["1", "text"], "seed": ["2", "seed"], "width": ["3", "width"], "height": ["3", "height"]},
            },
            {
                "name": "local-h3-i2v-video", "capability": "video", "type": "comfyui", "service": "comfyui",
                "workflow": str(wf_path), "output_node": "9", "fps": 24, "frame_grid": [17, 5], "min_images": 1,
                "inputs": {"prompt": ["1", "text"], "seeds": [["2", "seed"]], "width": ["3", "width"],
                           "height": ["3", "height"], "frames": ["3", "length"], "images": [["4", "image"]],
                           "audios": [["5", "audio"]]},
            },
            {
                "name": "local-talk-video", "capability": "video", "type": "command", "min_images": 1, "min_audios": 1,
                "command": [sys.executable, "-c",
                            "import pathlib,shutil,sys; shutil.copy(sys.argv[1], sys.argv[2]); "
                            "pathlib.Path(sys.argv[3], 'note.txt').write_text('分 2 段出片', encoding='utf-8')",
                            "{audio1}", "{out}", "{job_dir}"],
                "easy": {"button": "数字人口播", "fields": [{"type": "image"}, {"type": "speech"}]},
            },
            {"name": "local-tts-lyra", "capability": "audio", "type": "tts", "service": "tts", "voice": "lyra",
             "easy": {"button": "配音 Lyra"}},
        ],
    }
    comfy = FakeComfy()
    services = FakeServices({n: ServiceSpec.from_config(n, raw) for n, raw in config["services"].items()})
    gateway = Gateway(config, services=services, comfy_client=lambda url: comfy)
    stop = threading.Event()
    threading.Thread(target=gateway.worker, args=(stop,), daemon=True).start()
    client = TestClient(create_app(gateway, start_worker=False))
    yield client, gateway, comfy, services, config
    stop.set()


def test_frames_land_on_the_h3_grid():
    assert frames_for(5, 24, (17, 5)) == 124
    assert frames_for(10, 24, (17, 5)) == 243
    assert frames_for(3, 24, None) == 72


def test_models_are_listed_with_names_the_canvas_can_classify(setup):
    client, *_ = setup
    ids = [m["id"] for m in client.get("/v1/models").json()["data"]]
    assert ids == ["local-qwen-image", "local-h3-i2v-video", "local-talk-video", "local-tts-lyra"]


def test_image_generation_fills_prompt_size_and_returns_b64(setup):
    client, gateway, comfy, services, _ = setup
    resp = client.post("/v1/images/generations", json={"model": "local-qwen-image", "prompt": "一只猫", "n": 2, "size": "1024x1536"})
    assert resp.status_code == 200
    assert len(resp.json()["data"]) == 2
    wf = comfy.submitted[0]
    assert wf["1"]["inputs"]["text"] == "一只猫"
    assert (wf["3"]["inputs"]["width"], wf["3"]["inputs"]["height"]) == (1024, 1536)
    assert services.ensured == ["comfyui", "comfyui"]


def test_video_job_runs_and_serves_the_mp4(setup):
    client, gateway, comfy, *_ = setup
    resp = client.post(
        "/v1/videos",
        data={"model": "local-h3-i2v-video", "prompt": "她挥手", "seconds": "5", "size": "1280x720", "mode": "frames"},
        files=[("first_frame", ("first.png", b"img", "image/png")), ("images", ("first.png", b"img", "image/png"))],
    )
    assert resp.status_code == 200
    job_id = resp.json()["id"]
    status = client.get(f"/v1/videos/{job_id}").json()
    assert status["status"] == "completed"
    wf = comfy.submitted[-1]
    assert wf["3"]["inputs"]["length"] == 124
    assert wf["4"]["inputs"]["image"].startswith("canvas/")
    assert comfy.uploads == ["01.png"]  # first_frame and its duplicate under images upload once
    content = client.get(f"/v1/videos/{job_id}/content")
    assert content.status_code == 200 and content.content == b"fake-output"


def test_missing_reference_fails_with_a_readable_message(setup):
    client, *_ = setup
    job_id = client.post("/v1/videos", data={"model": "local-h3-i2v-video", "prompt": "x"}).json()["id"]
    status = client.get(f"/v1/videos/{job_id}").json()
    assert status["status"] == "failed"
    assert "图片" in status["error"]["message"]


def test_command_model_gets_files_as_placeholders(setup):
    client, *_ = setup
    job_id = client.post(
        "/v1/videos",
        data={"model": "local-talk-video", "prompt": "你好"},
        files=[("images", ("card.png", b"img", "image/png")), ("audio[]", ("line.mp3", b"voice", "audio/mpeg"))],
    ).json()["id"]
    status = client.get(f"/v1/videos/{job_id}").json()
    assert status["status"] == "completed"
    assert status["note"] == "分 2 段出片"   # job_dir/note.txt left by the command reaches the page
    assert client.get(f"/v1/videos/{job_id}/content").content == b"voice"


def test_tts_uses_the_voice_from_the_model(setup, monkeypatch):
    client, *_ = setup
    calls = {}

    class Resp:
        status_code = 200
        content = b"RIFFwav"
        text = ""

    def fake_post(url, json=None, timeout=None):
        calls.update(url=url, body=json)
        return Resp()

    monkeypatch.setattr("services.canvas_gateway.adapters.requests.post", fake_post)
    resp = client.post("/v1/audio/speech", json={"model": "local-tts-lyra", "input": "大家好", "voice": "alloy"})
    assert resp.status_code == 200 and resp.content == b"RIFFwav"
    assert calls["url"] == "http://127.0.0.1:9880/tts"
    assert calls["body"]["voice"] == "lyra"


def test_wrong_capability_is_rejected(setup):
    client, *_ = setup
    resp = client.post("/v1/images/generations", json={"model": "local-h3-i2v-video", "prompt": "x"})
    assert resp.status_code == 400


def test_finished_jobs_survive_a_restart(setup):
    client, gateway, comfy, services, config = setup
    job_id = client.post(
        "/v1/videos", data={"model": "local-h3-i2v-video", "prompt": "x"},
        files=[("images", ("a.png", b"img", "image/png"))],
    ).json()["id"]
    assert client.get(f"/v1/videos/{job_id}").json()["status"] == "completed"
    reborn = Gateway(config, services=services, comfy_client=lambda url: comfy)
    assert reborn.jobs[job_id].status == "completed"


def test_service_manager_stops_only_what_it_started(tmp_path):
    started = []

    class Proc:
        pid = 1
        returncode = None

        def poll(self):
            return None

        def terminate(self):
            started.append("terminated")

        def wait(self, timeout=None):
            return 0

    up = {"value": False}

    def popen(*args, **kwargs):
        up["value"] = True
        return Proc()

    now = {"t": 0.0}
    specs = {
        "comfyui": ServiceSpec("comfyui", "http://x", start=["run"], idle_stop_minutes=1),
        "mars": ServiceSpec("mars", "http://y"),
    }
    mgr = ServiceManager(specs, probe=lambda url: url.startswith("http://y") or up["value"],
                         popen=popen, clock=lambda: now["t"])
    mgr.ensure("mars")
    mgr.ensure("comfyui")
    assert mgr.owned() == ["comfyui"]
    now["t"] = 30
    assert mgr.stop_idle(busy=False) == []
    now["t"] = 120
    assert mgr.stop_idle(busy=True) == []
    assert mgr.stop_idle(busy=False) == ["comfyui"]
    assert mgr.owned() == []


class FakeTTS:
    status_code = 200
    content = b"RIFF-tts"
    text = ""


def test_easy_page_and_modes(setup):
    client, *_ = setup
    assert "一键出片" in client.get("/easy").text
    modes = client.get("/easy/modes").json()
    assert [m["button"] for m in modes["modes"]] == ["数字人口播", "配音 Lyra"]
    assert modes["modes"][1]["fields"] == [{"type": "prompt", "label": "要念的文字"}]
    assert modes["voices"] == [{"model": "local-tts-lyra", "label": "配音 Lyra"}]
    assert modes["default_voice"] == "local-tts-lyra"


def test_voices_without_a_button_stay_in_the_dropdown_only(setup):
    client, gateway, *_ = setup
    gateway.models["local-tts-dongbei"] = {"name": "local-tts-dongbei", "capability": "audio", "type": "tts",
                                           "easy": {"voice_label": "东北大姐"}}
    modes = client.get("/easy/modes").json()
    assert [m["button"] for m in modes["modes"]] == ["数字人口播", "配音 Lyra"]
    assert {"model": "local-tts-dongbei", "label": "东北大姐"} in modes["voices"]


def test_easy_talk_from_text_chains_tts_then_video(setup, monkeypatch):
    client, gateway, *_ = setup
    monkeypatch.setattr("services.canvas_gateway.adapters.requests.post", lambda *a, **k: FakeTTS())
    resp = client.post(
        "/easy/run",
        data={"model": "local-talk-video", "speech_text": "大家好，我是 Lyra", "voice": "local-tts-lyra"},
        files=[("image", ("me.png", b"img", "image/png"))],
    ).json()
    assert len(resp["needs"]) == 1
    job = gateway.jobs[resp["id"]]
    assert job.done.wait(10) and job.status == "completed", job.error
    # The talk "video" is the TTS audio copied through by the fake command.
    assert client.get(f"/jobs/{job.id}/file").content == b"RIFF-tts"
    listed = client.get("/v1/jobs").json()["data"]
    assert [j["id"] for j in listed[:2]] == [job.id, resp["needs"][0]]
    assert listed[0]["file"] == f"/jobs/{job.id}/file" and listed[0]["file_type"] == "video/mp4"


def test_easy_talk_stops_when_the_voice_over_fails(setup, monkeypatch):
    client, gateway, *_ = setup

    class Broken(FakeTTS):
        status_code = 500
        text = "显存不足"

    monkeypatch.setattr("services.canvas_gateway.adapters.requests.post", lambda *a, **k: Broken())
    resp = client.post(
        "/easy/run",
        data={"model": "local-talk-video", "speech_text": "你好", "voice": "local-tts-lyra"},
        files=[("image", ("me.png", b"img", "image/png"))],
    ).json()
    job = gateway.jobs[resp["id"]]
    job.done.wait(10)
    assert job.status == "failed" and "配音没有成功" in job.error


def test_easy_image_runs_async_and_serves_the_png(setup):
    client, gateway, *_ = setup
    resp = client.post("/easy/run", data={"model": "local-qwen-image", "prompt": "海边", "size": "768x1344"}).json()
    job = gateway.jobs[resp["id"]]
    assert job.done.wait(10) and job.status == "completed"
    file = client.get(f"/jobs/{job.id}/file")
    assert file.headers["content-type"] == "image/png" and file.content == b"fake-output"
    assert client.get(f"/jobs/{job.id}/file?download=true").headers["content-disposition"].startswith("attachment")
