"""Tests for the contact_sheet review tool (needs ffmpeg)."""

from __future__ import annotations

import shutil
import subprocess

import pytest

from tools.analysis.contact_sheet import ContactSheet, beats_from_cuts
from tools.audio.sfx_synth import SfxSynth

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")


@pytest.fixture(scope="module")
def video(tmp_path_factory):
    path = tmp_path_factory.mktemp("cs") / "v.mp4"
    subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=320x180:d=4:r=24",
         "-pix_fmt", "yuv420p", str(path)],
        check=True,
    )
    return path


def _size(png):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=width,height", "-of", "csv=p=0", str(png)],
        capture_output=True, check=True,
    ).stdout.decode().strip()
    return tuple(int(x) for x in out.split(","))


def test_beats_from_cuts_lays_primary_cuts_end_to_end():
    cuts = [
        {"id": "a", "source": "x", "in_seconds": 5, "out_seconds": 7},
        {"id": "o", "source": "y", "in_seconds": 0, "out_seconds": 9, "layer": "overlay"},
        {"id": "b", "source": "z", "in_seconds": 0, "out_seconds": 3, "speed": 2},
        {"id": "c", "source": "z", "in_seconds": 3, "out_seconds": 4},
    ]
    assert beats_from_cuts(cuts) == [0.0, 2.0, 3.5]


def test_sheet_at_beats_with_offset(video, tmp_path):
    r = ContactSheet().execute({
        "operation": "sheet", "input_path": str(video), "beats": [0, 1, 2, 3, 3.9],
        "output_dir": str(tmp_path), "tile_width": 160,
    })
    assert r.success, r.error
    assert r.data["times"] == [0.3, 1.3, 2.3, 3.3, 3.99]  # last one clamped inside the video
    assert len(r.data["sheets"]) == 1
    w, h = _size(r.data["sheets"][0])
    assert w > 160 * 4 and h > 90 * 2


def test_short_sheet_shrinks_to_its_frames(video, tmp_path):
    r = ContactSheet().execute({
        "operation": "sheet", "input_path": str(video), "timestamps": [1.0, 2.0],
        "output_dir": str(tmp_path), "tile_width": 160,
    })
    assert r.success, r.error
    w, h = _size(r.data["sheets"][0])
    assert w < 160 * 3 and h < 90 * 2


def test_scrub_and_paging(video, tmp_path):
    r = ContactSheet().execute({
        "operation": "sheet", "input_path": str(video), "every": 0.25,
        "from_seconds": 1, "to_seconds": 3, "output_dir": str(tmp_path), "tile_width": 120,
    })
    assert r.success, r.error
    assert r.data["frame_count"] == 9
    assert len(r.data["sheets"]) == 2  # 4x2 per sheet


def test_needs_something_to_sample(video, tmp_path):
    r = ContactSheet().execute({"operation": "sheet", "input_path": str(video), "output_dir": str(tmp_path)})
    assert not r.success


def test_waveform_reports_loudness(tmp_path):
    wav = tmp_path / "m.wav"
    SfxSynth().execute({
        "duration_seconds": 3, "output_path": str(wav), "mode": "master", "transitions": [1.5],
        "cues": [{"time": 0, "sound": "pad", "params": {"chords": [[0, [220.0, 277.18, 329.63]]]}, "gain": 0.05}],
    })
    r = ContactSheet().execute({"operation": "waveform", "input_path": str(wav), "beats": [1.5],
                                "output_dir": str(tmp_path)})
    assert r.success, r.error
    assert abs(r.data["lufs"] - (-14.0)) <= 1.0  # engine and ffmpeg ebur128 agree
    assert r.data["loudness_ok"]
    assert _size(r.data["waveform"]) == (1800, 280)
