"""Filling caller-supplied ComfyUI workflows: node patches and file uploads."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tools._comfyui.client import ComfyUIClient, ComfyUIError
from tools.audio.comfyui_music import ComfyUIMusic
from tools.graphics.comfyui_image import ComfyUIImage
from tools.video.comfyui_video import ComfyUIVideo

API_WORKFLOW = {
    "3": {"class_type": "CLIPTextEncode", "inputs": {"text": "old"}},
    "7": {"class_type": "LoadImage", "inputs": {"image": "placeholder.png"}},
    "8": {"class_type": "LoadAudio", "inputs": {"audio": "placeholder.wav"}},
    "9": {"class_type": "SaveVideo", "inputs": {"filename_prefix": "x"}},
}


def _client() -> ComfyUIClient:
    return ComfyUIClient(server_url="http://127.0.0.1:8189")


def _upload_response(name: str, subfolder: str = "") -> MagicMock:
    resp = MagicMock()
    resp.json.return_value = {"name": name, "subfolder": subfolder, "type": "input"}
    return resp


def test_patches_apply_without_mutating_source():
    filled = _client().prepare_custom_workflow(
        API_WORKFLOW, patches={"3": {"text": "new prompt"}, "9": {"filename_prefix": "seg01"}}
    )
    assert filled["3"]["inputs"]["text"] == "new prompt"
    assert filled["9"]["inputs"]["filename_prefix"] == "seg01"
    assert API_WORKFLOW["3"]["inputs"]["text"] == "old"


def test_uploads_set_server_names_for_image_and_audio(tmp_path: Path):
    card = tmp_path / "card.png"
    card.write_bytes(b"png")
    voice = tmp_path / "voice.wav"
    voice.write_bytes(b"wav")
    with patch(
        "tools._comfyui.client.requests.post",
        side_effect=[_upload_response("card.png"), _upload_response("voice.wav", "refs")],
    ) as post:
        filled = _client().prepare_custom_workflow(
            API_WORKFLOW,
            uploads={"7": {"image": str(card)}, "8": {"audio": str(voice)}},
        )
    assert filled["7"]["inputs"]["image"] == "card.png"
    assert filled["8"]["inputs"]["audio"] == "refs/voice.wav"
    assert post.call_args_list[1].kwargs["files"]["image"][2].startswith("audio/")


def test_unknown_node_fails_before_any_upload(tmp_path: Path):
    card = tmp_path / "card.png"
    card.write_bytes(b"png")
    with patch("tools._comfyui.client.requests.post") as post:
        with pytest.raises(ComfyUIError, match="Node '42' not found"):
            _client().prepare_custom_workflow(
                API_WORKFLOW, patches={"42": {"text": "x"}}, uploads={"7": {"image": str(card)}}
            )
    post.assert_not_called()


def test_missing_upload_source_is_reported():
    with pytest.raises(ComfyUIError, match="Upload source not found"):
        _client().prepare_custom_workflow(API_WORKFLOW, uploads={"7": {"image": "/nope.png"}})


def test_ui_format_workflow_is_rejected_with_export_hint():
    ui = {"nodes": [], "links": [], "version": 0.4}
    with pytest.raises(ComfyUIError, match="Export \\(API\\)"):
        _client().prepare_custom_workflow(ui)


@pytest.mark.parametrize("cls", [ComfyUIImage, ComfyUIVideo, ComfyUIMusic], ids=lambda c: c.name)
def test_tools_expose_fill_inputs_and_key_the_cache_on_them(cls):
    tool = cls()
    props = tool.input_schema["properties"]
    assert "workflow_patches" in props and "upload_files" in props
    base = {"prompt": "p", "workflow_json": "{}", "output_node": "9", "seed": 1}
    key_a = tool.cache_key({**base, "workflow_patches": {"3": {"text": "a"}}})
    key_b = tool.cache_key({**base, "workflow_patches": {"3": {"text": "b"}}})
    assert key_a != key_b


def test_video_tool_submits_filled_workflow(tmp_path: Path):
    import json

    tool = ComfyUIVideo()
    tool._client = MagicMock(wraps=_client())
    tool._client.is_available = MagicMock(return_value=True)
    out = tmp_path / "seg01.mp4"
    tool._client.generate = MagicMock(return_value=[out])
    result = tool.execute(
        {
            "prompt": "Lyra says hello",
            "model_family": "minimax_h3_local",
            "workflow_json": json.dumps(API_WORKFLOW),
            "output_node": "9",
            "workflow_patches": {"3": {"text": "Lyra says hello"}},
            "output_path": str(out),
        }
    )
    assert result.success, result.error
    submitted = tool._client.generate.call_args.args[0]
    assert submitted["3"]["inputs"]["text"] == "Lyra says hello"


def test_image_tool_reports_the_workflows_own_settings(tmp_path: Path):
    import json

    workflow = {
        "3": {"class_type": "CLIPTextEncode", "inputs": {"text": "old"}},
        "5": {"class_type": "EmptyLatentImage", "inputs": {"width": 768, "height": 1344}},
        "6": {"class_type": "KSampler", "inputs": {"seed": 1, "steps": 8, "model": ["1", 0]}},
        "9": {"class_type": "SaveImage", "inputs": {"filename_prefix": "x"}},
    }
    tool = ComfyUIImage()
    tool._client = MagicMock(wraps=_client())
    tool._client.is_available = MagicMock(return_value=True)
    out = tmp_path / "cat.png"
    tool._client.generate = MagicMock(return_value=[out])
    result = tool.execute(
        {
            "prompt": "cat",
            "workflow_json": json.dumps(workflow),
            "output_node": "9",
            "workflow_patches": {"6": {"seed": 20261025}},
            "output_path": str(out),
        }
    )
    assert result.success, result.error
    assert (result.data["width"], result.data["height"], result.data["steps"]) == (768, 1344, 8)
    assert result.seed == 20261025
