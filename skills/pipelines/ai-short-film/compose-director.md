# Compose Director - AI Short Film Pipeline

## When To Use

The cut is decided. Build the clean master, transcribe it, write both subtitle tracks and deliver the
film with switchable soft subtitles.

## Prerequisites

| Layer | Resource | Purpose |
|-------|----------|---------|
| Schema | `render_report.schema.json`, `final_review.schema.json` | Artifact validation |
| Prior artifacts | `edit_decisions`, `asset_manifest`, `script`, `proposal_packet` | Clips, languages |
| Tools | `clip_assembler`, `h3_studio_transcriber`, `subtitle_mux`, `audio_mixer` | Assembly, ASR, mux, score |

## Process

### 1. Clean Master

Honour the `render_runtime` locked at proposal and carried in `edit_decisions`. With `ffmpeg` (the default
for this pipeline) the shots are joined by `clip_assembler`. If the user chose Remotion or HyperFrames for
title or end cards, render only those cards with `video_compose` on that runtime and pass them to
`clip_assembler` as extra clips; never re-render the H3 shots through it. A silent runtime swap is a
governance violation.

`clip_assembler` with the accepted clips in cut order and
`output_path: projects/<id>/renders/<title>.clean.mp4`. It re-times every clip to 24 fps, pads or trims each
clip's audio to its own video length and encodes once, so native dialogue stays in sync. Its result must
report `frames_match_sum_of_clips: true` and an audio/video delta within 0.05 s; otherwise stop and fix.

If a score was planned, mix it under the master with `audio_mixer` (dialogue first) into a new file and
use that file from here on.

### 2. Transcribe the Final Edit

Run `h3_studio_transcriber` on the master (`language` = the spoken language, `timestamps: "word"`,
`output_dir: projects/<id>/assets/subtitles/`). Subtitles must come from what was actually spoken in the
deliverable, never from the script. Qwen's forced aligner provides real word timings; never spread
characters evenly to invent them. Media over 7200 s must be split with exact offsets.

### 3. Review the Source Track

Compare the transcript with the audio, the script and the names in the bible. Fix recognition errors,
homophones, names, punctuation, repetitions and hallucinated text in silences. Do **not** restore a
scripted line the take omitted or replace an audible paraphrase with the planned words. If essential
story information was never spoken, go back and regenerate that segment. Split cues at phrase boundaries
and pauses, keep line lengths readable, never cross a scene cut accidentally, no overlaps, nothing past
the end, and never show speaker IDs. Save `subtitles/source.reviewed.srt` and a correction log
(original, corrected, time, reason).

### 4. Translate

Translate the **reviewed** source track yourself into the requested language with the story context:
consistent names, terms, tone and implied meaning, same cue IDs and times. Save
`subtitles/translation.<lang>.srt` and review both tracks for omissions and consistency.

### 5. Mux Soft Tracks

`subtitle_mux` with the master, both SRTs (ISO 639-2 codes such as `zho`, `eng`; titles such as
"Chinese (Original)" and "English"; the agreed default) and `output_path: projects/<id>/renders/<title>.mp4`.
Video and audio are copied untouched. Players show one selected track at a time; if the user wants both
on screen together, that is a separate burn-in request to clarify.

### 6. Verify and Report

ffprobe the deliverable: 24 fps, frame count equal to the master, one video, one audio, the agreed
subtitle streams with the right languages and default. Watch the joins and several cues at the start,
middle, end and cuts. Write `render_report` (both MP4s) and `final_review` with the checks actually done.

## Quality Gate

- master frame count equals the sum of accepted clips; audio in sync,
- subtitles transcribed from the final edit and reviewed, translation made from the reviewed track,
- deliverable has switchable soft tracks and nothing burned into the picture.

## Common Pitfalls

- Concatenating clips with stream copy (AAC priming drifts lip sync over many shots).
- Generating subtitles from the script.
- Translating the raw ASR output.
