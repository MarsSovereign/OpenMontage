"""canvas_storyboard turns an ai-short-film project into an importable Infinite Canvas archive."""

from __future__ import annotations

import json
import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest
from PIL import Image

from tools.publishers.canvas_storyboard import CanvasStoryboard


def write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "projects" / "radio-signal"
    refs = root / "assets" / "images" / "refs"
    (refs / "characters").mkdir(parents=True)
    (refs / "locations").mkdir(parents=True)
    Image.new("RGB", (768, 1024), "red").save(refs / "characters" / "lin.png")
    Image.new("RGB", (1024, 576), "blue").save(refs / "locations" / "workshop.png")
    clip = root / "assets" / "video" / "s01.take2.mp4"
    clip.parent.mkdir(parents=True)
    if shutil.which("ffmpeg"):
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc=size=320x180:rate=24", "-frames:v", "24", str(clip)], check=True)
    else:
        clip.write_bytes(b"not a real video")
    write(root / "artifacts" / "script.json", {
        "version": "1.0", "title": "Signal", "total_duration_seconds": 15.3,
        "sections": [
            {"id": "s01", "text": "(S1) 这不是求救，是警告。", "start_seconds": 0, "end_seconds": 10.125},
            {"id": "s02", "text": "", "start_seconds": 10.125, "end_seconds": 15.29},
        ],
        "metadata": {"story_bible": {
            "characters": [{"id": "lin", "name": "Lin", "card": "narrow face, rust scarf", "speaker_id": "S1", "voice": "low husky adult woman"}],
            "locations": [{"id": "workshop", "name": "Radio workshop", "card": "rain window left, green door right"}],
        }},
    })
    write(root / "artifacts" / "scene_plan.json", {
        "version": "1.0",
        "scenes": [
            {"id": "s01", "type": "character_scene", "description": "Lin hears the signal", "start_seconds": 0, "end_seconds": 10.125, "script_section_id": "s01"},
            {"id": "s02", "type": "generated", "description": "The door, still shut", "start_seconds": 10.125, "end_seconds": 15.29, "script_section_id": "s02"},
        ],
        "metadata": {"shots": {
            "s01": {"duration": 10, "references": ["character:lin", "location:workshop"], "prompt": "subject_definitions: ...", "continuity_to_next": "cut on her look"},
            "s02": {"duration": 5, "references": ["location:workshop", "prop:knife"], "prompt": "subject_definitions: door"},
        }},
    })
    # Checkpoint-embedded artifact (no artifacts/ file) must be found too.
    write(root / "checkpoint_assets.json", {"artifacts": {"asset_manifest": {"version": "1.0", "assets": [
        {"id": "ref-lin", "type": "image", "subtype": "character_ref", "path": "assets/images/refs/characters/lin.png", "source_tool": "h3_studio_image", "scene_id": "refs"},
        {"id": "ref-workshop", "type": "image", "subtype": "location_ref", "path": "assets/images/refs/locations/workshop.png", "source_tool": "h3_studio_image", "scene_id": "refs"},
        {"id": "s01", "type": "video", "path": "assets/video/s01.take2.mp4", "source_tool": "h3_studio_video", "scene_id": "s01"},
    ]}}})
    return root


def test_archive_matches_the_canvas_import_format(project):
    result = CanvasStoryboard().execute({"project_dir": str(project)})
    assert result.success, result.error
    archive = Path(result.data["output"])
    assert archive == project / "renders" / "radio-signal.canvas.zip"
    with zipfile.ZipFile(archive) as zf:
        data = json.loads(zf.read("projects.json"))
        assert data["app"] == "infinite-canvas" and data["version"] == 3
        item = data["projects"][0]
        for entry in item["files"]:
            assert entry["path"] in zf.namelist() and entry["bytes"] > 0
            assert entry["storageKey"].split(":")[0] in ("image", "video")
    project_data = item["project"]
    nodes = {n["id"]: n for n in project_data["nodes"]}
    assert project_data["title"] == "Signal"
    assert {n["type"] for n in nodes.values()} == {"film:timeline", "film:card", "film:shot", "image", "video"}

    # Image nodes keep a non-empty content so the canvas restores them from storageKey.
    lin = nodes["ref-lin"]
    assert lin["metadata"]["content"] == lin["metadata"]["storageKey"] and lin["metadata"]["naturalWidth"] == 768
    assert lin["height"] == round(160 * 1024 / 768)

    shot = nodes["shot-s01"]["metadata"]
    assert shot["shotStatus"] == "accepted" and shot["acceptedVideoId"] == "take-s01" and shot["duration"] == 10
    assert shot["dialogue"] == "(S1) 这不是求救，是警告。" and shot["prompt"].startswith("subject_definitions")
    assert nodes["shot-s02"]["metadata"]["shotStatus"] == "planned" and nodes["card-lin"]["metadata"]["speakerId"] == "S1"

    # References are wired card -> shot in prompt order; reference images -> cards; shot -> accepted take.
    into = lambda target: [c["fromNodeId"] for c in project_data["connections"] if c["toNodeId"] == target]
    assert into("shot-s01") == ["card-lin", "card-workshop"]
    assert into("card-lin") == ["ref-lin"] and into("take-s01") == ["shot-s01"]
    assert result.data["accepted_shots"] == 1 and result.data["embedded_files"] == 3
    assert any("prop:knife" in w for w in result.data["warnings"])


def test_live_sync_ops_mirror_the_layout(project):
    result = CanvasStoryboard().execute({"project_dir": str(project), "include_videos": False, "media_base_url": "http://gpu-host:9000/radio-signal/"})
    ops = result.data["ops"]
    adds = {op["id"]: op for op in ops if op["type"] == "add_node"}
    assert "take-s01" not in adds and result.data["embedded_files"] == 2
    assert adds["ref-lin"]["metadata"]["content"] == "http://gpu-host:9000/radio-signal/assets/images/refs/characters/lin.png"
    assert "storageKey" not in adds["ref-lin"]["metadata"]
    assert adds["shot-s01"]["nodeType"] == "film:shot"
    assert sum(op["type"] == "connect_nodes" for op in ops) == result.data["connections"]


def test_needs_a_storyboard(tmp_path):
    result = CanvasStoryboard().execute({"project_dir": str(tmp_path)})
    assert not result.success and "script" in result.error
