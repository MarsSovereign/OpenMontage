---
name: review-loop
description: Visual self-review loop for any render — build contact sheets at every timeline beat with the contact_sheet tool, look at them, critique against a checklist, fix, re-render, repeat; then check SFX sync and loudness on a beat-marked waveform. Use after any change to a video's visuals or timing and before presenting a video as done, especially for motion graphics, text animation and unattended runs.
---

# Review loop

You can't watch the video, but you can look at frames. Never report a video as done without looking at contact sheets of the current render.

Adapted from claude-motion (https://github.com/whaleyxbt/claude-motion, MIT) for OpenMontage's `contact_sheet` tool. It complements the compose director's post-render self-review (ffprobe, transcription): that proves the file is valid; this proves every beat looks right.

## Beats

A beat is any timeline moment where something changes on screen: a cut, a title landing, a word reveal, a chart bar arriving, a callout popping in. Collect them in output-timeline seconds:

- From `edit_decisions.cuts`: pass `cuts` and the tool lays primary cuts end to end.
- For motion graphics: the start times you gave the animation (HyperFrames GSAP timeline positions, Remotion `Sequence from` / fps, section starts in props). Keep them in one list and reuse it for SFX cues, so picture and sound share one clock.

## Loop

1. Render a draft (half resolution is fine for iteration).
2. Contact sheet at every beat, 0.3 s after it so motion has settled:

   ```python
   from tools.analysis.contact_sheet import ContactSheet
   r = ContactSheet().execute({
       "operation": "sheet",
       "input_path": "projects/<p>/renders/draft.mp4",
       "beats": beats,                 # or "cuts": edit_decisions["cuts"]
       "output_dir": "projects/<p>/review",
   })
   # r.data["sheets"]: 4x2 tiles per PNG, each stamped t=<seconds>
   ```
3. Open every sheet image and go through the checklist below.
4. Zoom in on anything suspicious:
   - scrub a transition: `{"operation": "sheet", "every": 0.2, "from_seconds": 3, "to_seconds": 5}`
   - exact moments: `{"operation": "sheet", "timestamps": [3.7, 3.75, 3.8]}`
   - one full-size frame: `tile_width: 1920` with a single timestamp.
5. Fix, re-render, go back to step 2. Stop when a full pass finds nothing.
6. Final render: one more sheet pass on the final file, then the waveform check.

## Checklist

Composition
- One focal point per frame. If two things compete, one of them waits.
- Nothing collides or overlaps unintentionally, especially mid-transition where an exiting and an entering element share space.
- Safe area: nothing important within ~60 px of an edge (more on 9:16 where platform UI covers the bottom and right).
- Frame 0 is a real image, not black: feeds autoplay muted and frame 0 is often the thumbnail.

Type
- Nothing clipped by a mask, container or the frame when it shouldn't be.
- The key message still reads when the tile is shrunk to phone width.
- Text isn't moving while it's meant to be read.
- Chinese text: no missing-glyph boxes (tofu), no line broken in the middle of a word.

Timing
- Every readable state holds after it settles: at least 1 s for a short phrase, plus ~0.25 s per word beyond three (for Chinese, about 4–5 characters per second), at least 2 s for the final hook. If a mid-hold tile shows text still animating, the hold is too short.
- Scenes overlap instead of cutting to empty frames.
- Exits are about half as long as entrances.
- One hero moment per video; it isn't spent early.

Consistency
- Only the palette's colors; the accent marks the point of the frame.
- The same element looks the same in every scene it appears in.
- AI-generated shots: same character, outfit and setting across consecutive tiles (no face swaps between cuts).

Sound (after the final mux)

```python
ContactSheet().execute({
    "operation": "waveform",
    "input_path": "projects/<p>/renders/final.mp4",
    "beats": beats,
    "output_dir": "projects/<p>/review",
})
```
- Transients sit on (or a hair after) the coral beat lines in the waveform PNG. A hit between lines means a hardcoded time or a stale SFX file.
- `loudness_ok` is true: −14 LUFS ±1 and true peak ≤ −1 dBFS. If not, re-run `audio_mixer` with its loudness target rather than hand-gaining.

## Report

Say what you checked, what you fixed, and which sheet shows it. Don't claim a fix you haven't re-rendered and looked at. Record the sheet paths and frame count in `final_review.checks.visual_spotcheck`.
