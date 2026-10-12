---
name: procedural-sfx
description: Procedural sound effects with the sfx_synth tool — whooshes on cuts, pops on text and badges, clicks, typing, risers, impacts, chimes and a simple pad, synthesized in pure Python on the edit timeline, then mixed as an sfx stem by audio_mixer. Use when adding or checking SFX for explainers, motion graphics and any video that would otherwise be silent between words.
---

# Procedural SFX

Most AI-made motion graphics are silent between the words. Sound is what makes a piece feel produced.

`sfx_synth` synthesizes every sound in pure Python stdlib (engine in `lib/sfx/`, ported from claude-motion, MIT): no samples, no licenses, no API key, no cost, and the same cues give the same WAV on every run. Times come from the same beat list the picture and the review loop use, so sound can't drift out of sync.

## Typical use: an SFX stem under narration

```python
from tools.audio.sfx_synth import SfxSynth
r = SfxSynth().execute({
    "duration_seconds": video_duration,
    "output_path": "projects/<p>/assets/audio/sfx.wav",
    "transitions": cut_times,                     # whoosh whose peak lands on each cut
    "transition_gain": 0.25,                      # 0.1-0.15 for subtle cuts
    "cues": [
        {"time": 3.2, "sound": "pop", "params": {"f0": 900, "f1": 400}, "gain": 0.25, "pan": -0.3},
        {"time": 7.0, "sound": "typing", "params": {"end": 8.4, "chars": 18}},
        {"time": 11.5, "sound": "riser", "params": {"dur": 1.5}},   # ends on the hit at 13.0
        {"time": 13.0, "sound": "impact", "gain": 0.35},
    ],
})
```

Then pass it to `audio_mixer` (`operation: "full_mix"`) as `{"path": ".../sfx.wav", "role": "sfx", "volume": 0.5}` next to the speech and music tracks. The default `mode: "stem"` peak-normalizes to −3 dBFS and keeps the cue gains' relative levels; the mixer sets the stem's final level and the loudness target. Keep SFX about 6 dB under the narration (see `skills/creative/sound-design.md`).

For a motion graphic with no narration, use `mode: "master"` (optionally with a `pad` bed): the engine soft-clips and sets gain to hit `target_lufs` (−14) with the true peak ≤ −2 dBFS. A mix of short hits with silence between can't reach −14 without crushing; it comes out quieter and says so. Add sustained sound (a pad, longer tails) instead of forcing it.

`{"operation": "list_sounds"}` returns every generator with its signature and what it's for.

## Visual event → sound

| On screen | Sound | Notes |
|---|---|---|
| Cut / scene transition | `transitions` (or `whoosh`) | peak on the cut, alternate pan |
| Typing | `typing` | params `end`, `chars` |
| Word / line reveal | `whoosh` short + `blip` on the landing | airy, low gain |
| Badge, chip, callout, bullet pops in | `pop` | raise `f0` across a sequence |
| Control snaps, UI tick | `click` | higher `freq` sounds smaller |
| Sketch / line draws on | `scratch` | |
| Build-up | `riser` | end exactly on the hit; one per video |
| Success / payoff | `chime` | chord in key |
| Hero moment | `whoosh` in, `impact` on the hit | one impact per video |
| Under everything | `pad` | params `chords: [[start_s, [freqs]], ...]`, `volume: [[t, v], ...]` |

Accent, don't narrate: not every motion needs a sound. Continuous motion (drift, grain, slow zoom) stays silent. Over AI-generated live-action footage, keep SFX to transitions and graphic overlays; realistic foley belongs to the clip or to the `sound-effects` (ElevenLabs) skill.

## Levels and space

- Hero hits: gain 0.25–0.35. Supporting sounds: 0.08–0.2. Pad: ~0.03–0.05.
- Pan follows on-screen position (−1 left … +1 right); sequences sweep across the field.
- Pick one key and keep every tonal sound (blip, pop pitch, chime, pad) in it; pitch rises with progress and resolves on the payoff. D major: D 293.66, F♯ 369.99, A 440.00; C major: C 261.63, E 329.63, G 392.00.

## Verify

Run the `review-loop` skill's waveform check on the final mux: every transient on a beat line, `loudness_ok` true.
