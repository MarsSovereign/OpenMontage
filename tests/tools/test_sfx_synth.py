"""Tests for the procedural SFX engine and the sfx_synth tool."""

from __future__ import annotations

import wave

from lib.sfx import Mix, synth
from lib.sfx.loudness import integrated_lufs
from tools.audio.sfx_synth import SfxSynth
from tools.tool_registry import ToolRegistry


def _wav(path):
    with wave.open(str(path), "rb") as w:
        return w.getnchannels(), w.getframerate(), w.getnframes(), w.readframes(w.getnframes())


def test_registers_under_sound_effects():
    reg = ToolRegistry()
    reg.discover()
    tool = reg.get("sfx_synth")
    assert tool is not None
    assert tool.capability == "sound_effects"
    assert tool.estimate_cost({}) == 0.0
    assert "procedural-sfx" in tool.agent_skills


def test_list_sounds_covers_every_cue_sound():
    r = SfxSynth().execute({"operation": "list_sounds"})
    assert r.success
    names = {s["sound"] for s in r.data["sounds"]}
    assert {"whoosh", "pop", "click", "typing", "riser", "impact", "chime", "pad"} <= names
    assert all(s["use"] for s in r.data["sounds"])


def test_stem_render_is_48k_stereo_and_peak_normalized(tmp_path):
    out = tmp_path / "sfx.wav"
    r = SfxSynth().execute({
        "duration_seconds": 3,
        "output_path": str(out),
        "transitions": [1.0, 2.0],
        "cues": [
            {"time": 0.2, "sound": "pop", "params": {"f0": 900, "f1": 400}},
            {"time": 0.5, "sound": "typing", "params": {"end": 0.9, "chars": 6}},
        ],
    })
    assert r.success, r.error
    channels, rate, frames, _ = _wav(out)
    assert (channels, rate, frames) == (2, 48000, 3 * 48000)
    assert r.data["peak_db"] == -3.0
    assert r.data["sounds_placed"] == 2 + 1 + 6


def test_same_seed_gives_identical_audio(tmp_path):
    args = {"duration_seconds": 1.5, "transitions": [0.8], "cues": [{"time": 0.1, "sound": "click"}]}
    a, b = tmp_path / "a.wav", tmp_path / "b.wav"
    SfxSynth().execute({**args, "output_path": str(a)})
    SfxSynth().execute({**args, "output_path": str(b)})
    assert _wav(a)[3] == _wav(b)[3]


def test_master_mode_hits_loudness_target(tmp_path):
    r = SfxSynth().execute({
        "duration_seconds": 3,
        "output_path": str(tmp_path / "m.wav"),
        "mode": "master",
        "cues": [{"time": 0, "sound": "pad", "params": {"chords": [[0, [146.83, 220.0, 293.66]]]}, "gain": 0.05}],
    })
    assert r.success, r.error
    assert abs(r.data["lufs"] - (-14.0)) < 0.5
    assert r.data["peak_db"] <= -2.0 + 1e-6


def test_transition_whoosh_peaks_on_the_cut():
    mix = Mix(2.0)
    from tools.audio.sfx_synth import place_transition

    place_transition(mix, 1.0)
    env = [abs(x) for x in mix.left]
    window = int(0.02 * synth.SR)
    loudest = max(range(0, len(env) - window, window), key=lambda i: sum(env[i:i + window]))
    assert abs(loudest / synth.SR - 1.0) < 0.12


def test_rejects_bad_input(tmp_path):
    tool = SfxSynth()
    assert not tool.execute({"cues": [{"time": 0, "sound": "pop"}]}).success
    assert not tool.execute({"duration_seconds": 2}).success
    r = tool.execute({"duration_seconds": 2, "output_path": str(tmp_path / "x.wav"),
                      "cues": [{"time": 3, "sound": "pop"}]})
    assert not r.success and "outside" in r.error
    r = tool.execute({"duration_seconds": 2, "output_path": str(tmp_path / "x.wav"),
                      "cues": [{"time": 1, "sound": "kazoo"}]})
    assert not r.success and "unknown sound" in r.error


def test_loudness_of_silence_is_minus_inf():
    assert integrated_lufs([0.0] * 48000, [0.0] * 48000) == float("-inf")


def test_transition_gain_and_range(tmp_path):
    tool = SfxSynth()
    quiet = tool.execute({"duration_seconds": 2, "output_path": str(tmp_path / "q.wav"), "mode": "master",
                          "transitions": [1.0], "transition_gain": 0.1,
                          "cues": [{"time": 0.2, "sound": "pop", "params": {"f0": 900, "f1": 400}, "gain": 0.3}]})
    loud = tool.execute({"duration_seconds": 2, "output_path": str(tmp_path / "l.wav"), "mode": "master",
                         "transitions": [1.0], "transition_gain": 0.5,
                         "cues": [{"time": 0.2, "sound": "pop", "params": {"f0": 900, "f1": 400}, "gain": 0.3}]})
    assert quiet.success and loud.success
    assert _wav(tmp_path / "q.wav")[3] != _wav(tmp_path / "l.wav")[3]
    r = tool.execute({"duration_seconds": 2, "output_path": str(tmp_path / "x.wav"), "transitions": [2.5]})
    assert not r.success and "transition" in r.error
