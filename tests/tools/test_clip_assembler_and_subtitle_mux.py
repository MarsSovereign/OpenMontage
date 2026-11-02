"""clip_assembler keeps native audio in sync across shots; subtitle_mux adds switchable tracks."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from tools.subtitle.subtitle_mux import SubtitleMux
from tools.video.clip_assembler import ClipAssembler

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None, reason="needs ffmpeg")


def make_clip(path: Path, frames: int, audio_seconds: float | None, fps: int = 24) -> Path:
    cmd = ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"testsrc=size=320x180:rate={fps}"]
    if audio_seconds is not None:
        cmd += ["-f", "lavfi", "-t", f"{audio_seconds}", "-i", "sine=frequency=440:sample_rate=48000"]
    cmd += ["-frames:v", str(frames), "-c:v", "libx264", "-pix_fmt", "yuv420p"]
    if audio_seconds is not None:
        cmd += ["-c:a", "aac"]
    subprocess.run(cmd + [str(path)], check=True)
    return path


def streams(path: Path) -> list[dict]:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(path)], capture_output=True, text=True, check=True)
    return json.loads(out.stdout)["streams"]


def test_assembly_pads_and_trims_audio_to_each_clip(tmp_path):
    clips = [
        make_clip(tmp_path / "s1.mp4", 124, 5.6),   # audio runs 0.43 s past the picture
        make_clip(tmp_path / "s2.mp4", 48, None),   # silent shot
        make_clip(tmp_path / "s3.mp4", 72, 2.2),    # audio ends 0.8 s early
    ]
    out = tmp_path / "renders" / "film.clean.mp4"
    result = ClipAssembler().execute({"clips": [str(c) for c in clips], "output_path": str(out), "preset": "ultrafast"})
    assert result.success, result.error
    assert result.data["frames"] == 244 and result.data["checks"]["frames_match_sum_of_clips"]
    assert result.data["checks"]["audio_video_delta_seconds"] <= 0.05
    assert [s["had_audio"] for s in result.data["segments"]] == [True, False, True]
    assert result.data["checks"]["frame_rate"] == "24/1"


def test_assembly_rejects_mixed_sizes(tmp_path):
    a = make_clip(tmp_path / "a.mp4", 24, 1.0)
    b = tmp_path / "b.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc=size=640x360:rate=24", "-frames:v", "24", str(b)], check=True)
    result = ClipAssembler().execute({"clips": [str(a), str(b)], "output_path": str(tmp_path / "x.mp4")})
    assert not result.success and "different sizes" in result.error


def test_two_soft_subtitle_tracks(tmp_path):
    video = make_clip(tmp_path / "film.mp4", 48, 2.0)
    zh, en = tmp_path / "zh.srt", tmp_path / "en.srt"
    zh.write_text("1\n00:00:00,100 --> 00:00:01,500\n你好。\n", encoding="utf-8")
    en.write_text("1\n00:00:00,100 --> 00:00:01,500\nHello.\n", encoding="utf-8")
    out = tmp_path / "film.subbed.mp4"
    result = SubtitleMux().execute({
        "video_path": str(video),
        "tracks": [
            {"path": str(zh), "language": "zho", "title": "Chinese (Original)", "default": True},
            {"path": str(en), "language": "eng", "title": "English"},
        ],
        "output_path": str(out),
    })
    assert result.success, result.error
    assert result.data["tracks"] == [
        {"language": "zho", "title": "Chinese (Original)", "default": True},
        {"language": "eng", "title": "English", "default": False},
    ]
    kinds = [s["codec_type"] for s in streams(out)]
    assert kinds.count("video") == 1 and kinds.count("audio") == 1 and kinds.count("subtitle") == 2


def test_mux_refuses_to_overwrite_its_input(tmp_path):
    video = make_clip(tmp_path / "film.mp4", 24, 1.0)
    srt = tmp_path / "a.srt"
    srt.write_text("1\n00:00:00,000 --> 00:00:00,500\nx\n", encoding="utf-8")
    result = SubtitleMux().execute({"video_path": str(video), "tracks": [{"path": str(srt), "language": "eng"}], "output_path": str(video)})
    assert not result.success and "differ" in result.error
