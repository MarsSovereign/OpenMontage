"""Procedural sound effects: whooshes, pops, clicks, typing, risers, impacts.

Synthesizes every sound in pure Python (lib/sfx, ported from claude-motion),
so there are no samples, no licenses and no API cost, and the same cues give
the same WAV on every run. Times come from the edit timeline, so the sounds
land exactly on cuts and reveals.

Default output is an SFX stem for audio_mixer (role "sfx"). For a motion
graphic with no narration, mode "master" hits a loudness target by itself.
"""

from __future__ import annotations

import inspect
import time
from pathlib import Path
from typing import Any

from lib.sfx import Mix, synth
from tools.base_tool import (
    BaseTool,
    Determinism,
    ExecutionMode,
    ResourceProfile,
    ToolResult,
    ToolRuntime,
    ToolStability,
    ToolTier,
)

# Generators a cue can name. typing and pad are handled separately because
# they place many sounds or a stereo bed rather than one mono one-shot.
ONE_SHOTS = ("click", "key", "blip", "pop", "chime", "whoosh", "riser", "impact", "scratch")
SPECIAL = ("typing", "pad")

# Transition whoosh: the loudest point (peak fraction of its length) lands on the cut.
TRANSITION_WHOOSH = {"dur": 0.45, "f0": 400, "f1": 2600, "peak": 0.7}
TRANSITION_GAIN = 0.25


class SfxSynth(BaseTool):
    name = "sfx_synth"
    version = "0.1.0"
    tier = ToolTier.GENERATE
    capability = "sound_effects"
    provider = "sfx_engine"
    stability = ToolStability.EXPERIMENTAL
    execution_mode = ExecutionMode.SYNC
    determinism = Determinism.SEEDED
    runtime = ToolRuntime.LOCAL

    dependencies = []
    install_instructions = "No install needed: pure Python standard library."
    agent_skills = ["procedural-sfx"]

    capabilities = [
        "synthesize_sfx",
        "transition_whooshes",
        "typing_sounds",
        "music_pad",
        "loudness_mastering",
    ]
    supports = {"offline": True, "seeded": True, "royalty_free": True}
    best_for = [
        "whooshes on cuts and transitions, pops on text and badges, clicks on UI",
        "motion graphics and explainers that would otherwise be silent between words",
        "unattended runs: no API key, no cost, reproducible",
    ]
    not_good_for = [
        "realistic foley (footsteps, doors, crowds): use elevenlabs sound-effects",
        "music beds with melody (use music_gen); pad is a simple chord drone",
    ]
    fallback_tools = ["music_gen"]

    input_schema = {
        "type": "object",
        "properties": {
            "operation": {
                "type": "string",
                "enum": ["render", "list_sounds"],
                "default": "render",
                "description": "render: write a WAV from cues. list_sounds: describe the generators.",
            },
            "duration_seconds": {
                "type": "number",
                "exclusiveMinimum": 0,
                "description": "Length of the output WAV; use the video's duration.",
            },
            "output_path": {"type": "string"},
            "cues": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["time", "sound"],
                    "properties": {
                        "time": {"type": "number", "minimum": 0, "description": "Seconds; take it from the timeline."},
                        "sound": {"type": "string", "enum": list(ONE_SHOTS + SPECIAL)},
                        "params": {
                            "type": "object",
                            "description": "Generator arguments, e.g. whoosh {dur, f0, f1, peak}, "
                            "pop {f0, f1}, chime {freqs}, typing {end, chars}, pad {chords, volume}.",
                        },
                        "gain": {"type": "number", "minimum": 0, "default": 0.2},
                        "pan": {"type": "number", "minimum": -1, "maximum": 1, "default": 0},
                    },
                },
            },
            "transitions": {
                "type": "array",
                "items": {"type": "number", "minimum": 0},
                "description": "Cut times in seconds; each gets a whoosh whose peak lands on the cut.",
            },
            "transition_gain": {
                "type": "number",
                "minimum": 0,
                "default": TRANSITION_GAIN,
                "description": "Level of the transition whooshes (hero hits 0.25-0.35, subtle 0.1-0.15).",
            },
            "mode": {
                "type": "string",
                "enum": ["stem", "master"],
                "default": "stem",
                "description": "stem: peak-normalized for audio_mixer. master: hit target_lufs on its own.",
            },
            "target_lufs": {"type": "number", "default": -14.0},
            "seed": {"type": "integer", "default": 11},
        },
    }

    resource_profile = ResourceProfile(cpu_cores=1, ram_mb=1024, vram_mb=0, disk_mb=100)
    idempotency_key_fields = [
        "duration_seconds", "cues", "transitions", "transition_gain", "mode", "target_lufs", "seed",
    ]
    side_effects = ["writes a 48 kHz stereo WAV to output_path"]
    user_visible_verification = ["Listen at each cue time: every hit should sit on its visual event"]

    def estimate_cost(self, inputs: dict[str, Any]) -> float:
        return 0.0

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        if inputs.get("operation", "render") == "list_sounds":
            return ToolResult(success=True, data={"sounds": list_sounds()})

        duration = inputs.get("duration_seconds")
        if not duration or duration <= 0:
            return ToolResult(success=False, error="duration_seconds is required and must be > 0")
        cues = list(inputs.get("cues") or [])
        transitions = list(inputs.get("transitions") or [])
        if not cues and not transitions:
            return ToolResult(success=False, error="Pass cues, transitions, or both")

        start = time.time()
        mix = Mix(duration, seed=int(inputs.get("seed", 11)))
        try:
            gain = float(inputs.get("transition_gain", TRANSITION_GAIN))
            for i, t in enumerate(transitions):
                if not 0 <= float(t) < duration:
                    raise ValueError(f"transition {t} is outside 0..{duration}")
                place_transition(mix, float(t), pan=-0.3 if i % 2 == 0 else 0.3, gain=gain)
            for cue in cues:
                place_cue(mix, cue, duration)
        except (TypeError, ValueError) as exc:
            return ToolResult(success=False, error=f"Bad cue: {exc}")

        mode = inputs.get("mode", "stem")
        if mode == "master":
            stats = mix.master(target_lufs=float(inputs.get("target_lufs", -14.0)))
        else:
            stats = mix.normalize()

        output_path = Path(inputs.get("output_path", "sfx.wav"))
        mix.write(str(output_path))
        data = {
            "output": str(output_path),
            "duration_seconds": duration,
            "sounds_placed": mix.count,
            "mode": mode,
            "peak_db": round(stats["peak_db"], 2),
        }
        if mode == "master":
            data["lufs"] = round(stats["lufs"], 2)
            data["drive"] = stats["drive"]
        return ToolResult(
            success=True,
            data=data,
            artifacts=[str(output_path)],
            seed=int(inputs.get("seed", 11)),
            cost_usd=0.0,
            duration_seconds=round(time.time() - start, 2),
        )


def list_sounds() -> list[dict[str, str]]:
    """Name, signature and first docstring line of every cue sound."""
    out = []
    for name in ONE_SHOTS + ("pad",):
        fn = getattr(synth, name)
        doc = (fn.__doc__ or "").strip().splitlines()
        out.append({"sound": name, "signature": f"{name}{inspect.signature(fn)}", "use": doc[0] if doc else ""})
    out.append({
        "sound": "typing",
        "signature": "typing(end, chars)",
        "use": "One humanized keystroke per character from the cue time to end.",
    })
    return out


def place_transition(mix: Mix, t: float, pan: float = 0.0, gain: float = TRANSITION_GAIN) -> None:
    w = TRANSITION_WHOOSH
    samples = synth.whoosh(w["dur"], w["f0"], w["f1"], peak=w["peak"])
    mix.put(max(0.0, t - w["dur"] * w["peak"]), samples, gain, pan)


def place_cue(mix: Mix, cue: dict[str, Any], duration: float) -> None:
    t = float(cue["time"])
    if t < 0 or t >= duration:
        raise ValueError(f"time {t} is outside 0..{duration}")
    sound = cue["sound"]
    params = dict(cue.get("params") or {})
    gain = float(cue.get("gain", 0.2))
    pan = float(cue.get("pan", 0.0))

    if sound == "typing":
        end = float(params.get("end", t + 1.0))
        chars = int(params.get("chars", 20))
        mix.typing(t, min(end, duration), chars)
    elif sound == "pad":
        chords = [(float(c[0]), [float(f) for f in c[1]]) for c in params.get("chords", [])]
        if not chords:
            raise ValueError("pad needs params.chords = [[start_s, [freqs]], ...]")
        volume = params.get("volume")
        envelope = synth.envelope([(float(p[0]) - t, float(p[1])) for p in volume]) if volume else None
        left, right = synth.pad(duration - t, [(c0 - t, fs) for c0, fs in chords], volume=envelope)
        mix.put_stereo(t, left, right, gain)
    elif sound in ONE_SHOTS:
        mix.put(t, getattr(synth, sound)(**params), gain, pan)
    else:
        raise ValueError(f"unknown sound {sound!r}; use one of {', '.join(ONE_SHOTS + SPECIAL)}")
