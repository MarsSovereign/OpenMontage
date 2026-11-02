# Edit Director - AI Short Film Pipeline

## When To Use

All shots are accepted. Decide the cut list for the clean master.

## Prerequisites

| Layer | Resource | Purpose |
|-------|----------|---------|
| Schema | `schemas/artifacts/edit_decisions.schema.json` | Artifact validation |
| Prior artifacts | `scene_plan`, `asset_manifest`, `script` | Order, accepted clips, dialogue |

## Process

1. **Use real lengths.** Read each accepted clip's frame count (ffprobe or `clip_assembler` output);
   H3 snaps durations, so planned and actual times differ slightly. Cuts use actual times.
2. **Story order, hard cuts.** One cut per accepted clip, in script order, `render_runtime: "ffmpeg"`
   carried unchanged from the proposal. Generated shots end on designed handovers; dissolves rarely help
   and blur lip-synced faces.
3. **Never trim inside speech.** Trim only silent handles, and only when a clip's ending clearly drags.
   Note any trim in the cut's metadata so the assembler input is a trimmed copy, not the original take.
4. **Music.** If a film-length cue exists, plan it under the native audio at a level that never masks
   dialogue (ducked roughly 12-18 dB under speech); otherwise rely on the shots' own sound.
5. **Subtitles.** Set `subtitles: {"enabled": false}` (nothing is burned into the picture) and record the
   soft-track plan in `metadata.soft_subtitles`, e.g.
   `{"source_language": "zho", "translation_language": "eng", "default": "zho"}`; compose muxes them.

## Quality Gate

- every accepted clip appears exactly once, in story order,
- runtime equals the sum of actual clip lengths (minus any logged trims),
- no dialogue is cut mid-word.

## Common Pitfalls

- Planning with requested durations instead of actual frame counts.
- Adding transitions to hide continuity problems that should have been retaken.
