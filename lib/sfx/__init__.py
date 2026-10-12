"""Procedural sound effects in pure Python stdlib, 48 kHz stereo.

Ported from claude-motion's sfx engine (https://github.com/whaleyxbt/claude-motion,
MIT, see LICENSE in this directory). No samples and no licenses to track: every
sound is synthesized, and a fixed seed gives the same WAV on every run.

    from lib.sfx import Mix, synth
    mix = Mix(duration=12.0)
    mix.put(2.0, synth.whoosh(0.4, 500, 2400, peak=0.45), gain=0.3)
    mix.normalize()          # stem for audio_mixer; or mix.master() for a finished track
    mix.write("sfx.wav")

The sfx_synth tool (tools/audio/sfx_synth.py) wraps this for pipelines.
"""

from . import synth
from .mix import Mix
from .synth import SR

__all__ = ["Mix", "SR", "synth"]
