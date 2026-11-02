"""Contracts for the ai-short-film pipeline: every skill and tool it names exists and its gates hold."""

from pathlib import Path

import pytest
import yaml

from lib.checkpoint import _stage_requires_approval
from lib.pipeline_loader import load_pipeline
from tools.tool_registry import ToolRegistry

REPO_ROOT = Path(__file__).resolve().parents[2]
RAW = yaml.safe_load((REPO_ROOT / "pipeline_defs" / "ai-short-film.yaml").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def registry():
    reg = ToolRegistry()
    reg.discover()
    return reg


def test_manifest_loads_with_story_first_stage_order():
    manifest = load_pipeline("ai-short-film")
    assert [s["name"] for s in manifest["stages"]] == ["proposal", "script", "scene_plan", "assets", "edit", "compose", "publish"]


def test_every_named_skill_exists():
    skills = set(RAW["required_skills"]) | {stage["skill"] for stage in RAW["stages"]}
    missing = [s for s in sorted(skills) if not (REPO_ROOT / "skills" / f"{s}.md").is_file()]
    assert missing == []


def test_every_named_tool_is_registered(registry):
    names = set()
    for stage in RAW["stages"]:
        for key in ("tools_available", "required_tools", "optional_tools"):
            names.update(stage.get(key) or [])
    names.update(RAW["reference_input"]["analysis_tools"])
    assert sorted(n for n in names if registry.get(n) is None) == []


def test_creative_gates_require_approval():
    for stage in ("proposal", "script", "scene_plan", "assets", "publish"):
        assert _stage_requires_approval("ai-short-film", stage) is True
    for stage in ("edit", "compose"):
        assert _stage_requires_approval("ai-short-film", stage) is False


def test_gated_directors_end_the_turn():
    for stage in RAW["stages"]:
        text = (REPO_ROOT / "skills" / f"{stage['skill']}.md").read_text(encoding="utf-8")
        assert ("END YOUR TURN" in text) == stage["human_approval_default"], stage["name"]


def test_directors_use_the_studio_tools_they_rely_on():
    skills = REPO_ROOT / "skills" / "pipelines" / "ai-short-film"
    assert "h3_studio_video" in (skills / "asset-director.md").read_text(encoding="utf-8")
    compose = (skills / "compose-director.md").read_text(encoding="utf-8")
    for tool in ("clip_assembler", "h3_studio_transcriber", "subtitle_mux"):
        assert tool in compose


def test_artifact_shapes_described_by_the_directors_validate():
    """The JSON the directors tell agents to write must pass the strict artifact schemas."""
    from schemas.artifacts import validate_artifact

    validate_artifact("script", {
        "version": "1.0", "title": "Signal", "total_duration_seconds": 10.125,
        "sections": [{"id": "s01", "label": "discovery", "text": "(S1) 这不是求救，是警告。", "start_seconds": 0,
                      "end_seconds": 10.125, "speaker_directions": "quiet, controlled"}],
        "metadata": {"story_bible": {"characters": [{"id": "lin", "speaker_id": "S1"}], "locations": []}},
    })
    validate_artifact("scene_plan", {
        "version": "1.0",
        "scenes": [{"id": "s01", "type": "character_scene", "description": "Lin hears the signal",
                    "start_seconds": 0, "end_seconds": 10.125, "script_section_id": "s01",
                    "required_assets": [{"type": "image", "description": "character:lin", "source": "generate"}]}],
        "metadata": {"shots": {"s01": {"operation": "reference_to_video", "duration": 10,
                                       "references": ["character:lin", "location:workshop"], "prompt": "..."}}},
    })
    validate_artifact("asset_manifest", {
        "version": "1.0",
        "assets": [
            {"id": "ref-lin", "type": "image", "subtype": "character_ref", "path": "assets/images/refs/characters/lin.png",
             "source_tool": "h3_studio_image", "scene_id": "refs", "seed": 7, "model": "qwen-image-21-turbo",
             "generation_summary": "Studio job abc123, canonical of 3 candidates"},
            {"id": "s01", "type": "video", "path": "assets/video/s01.take1.mp4", "source_tool": "h3_studio_video",
             "scene_id": "s01", "seed": 42, "model": "fasth3-omniref", "cost_usd": 0.0, "duration_seconds": 10.125,
             "generation_summary": "Studio job def456; refs lin, workshop"},
        ],
    })
    validate_artifact("edit_decisions", {
        "version": "1.0", "render_runtime": "ffmpeg",
        "cuts": [{"id": "c01", "source": "s01", "in_seconds": 0, "out_seconds": 10.125}],
        "subtitles": {"enabled": False},
        "metadata": {"soft_subtitles": {"source_language": "zho", "translation_language": "eng", "default": "zho"}},
    })


def test_storyboard_export_is_offered_at_review_stages():
    for stage in RAW["stages"]:
        if stage["name"] in ("scene_plan", "assets", "publish"):
            assert "canvas_storyboard" in stage["tools_available"], stage["name"]
