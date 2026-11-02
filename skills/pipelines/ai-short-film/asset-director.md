# Asset Director - AI Short Film Pipeline

## When To Use

The storyboard is approved. Produce and freeze the references, then generate, review and accept every shot.

## Prerequisites

| Layer | Resource | Purpose |
|-------|----------|---------|
| Schema | `schemas/artifacts/asset_manifest.schema.json` | Artifact validation |
| Prior artifacts | `scene_plan` (`metadata.shots`), `script` (`metadata.story_bible`) | What to make |
| Tools | `h3_studio_image`, `h3_studio_video`, `frame_sampler` | References, shots, QC |
| Skill | `skills/creative/h3-reference-prompting.md` | Prompt rules |

Announce the tool, provider and model before the first call (Decision Communication Contract).
Write everything under `projects/<project-id>/assets/`.

## Process

### 1. Freeze References First

**Lock every character before any video, and every location before any shot set there.**

- Characters: `h3_studio_image`, `model: qwen-image-21-turbo` (or `flux-klein-4b`), the confirmed
  `aspect_ratio`, the bible card in full plus the visual treatment. One clearly visible person, readable
  face, hair, costume and marks, neutral expression, plain background, full or three-quarter view.
- Locations: the empty set with no people, readable layout and landmarks, agreed light and weather.
- Make 2-4 candidates, inspect them, pick one canonical image per card. Derive costume, age or angle
  variants from the canonical image with an edit (`reference_image_paths: [canonical]`), never by
  re-prompting from scratch; check identity after every edit.
- Save to `assets/images/refs/characters/<id>.png` and `assets/images/refs/locations/<id>.png`, and
  record each in the manifest (`type: "image"`, `subtype: "character_ref"` or `"location_ref"`,
  `scene_id: "refs"`, prompt, model, seed, and the Studio job id in `generation_summary`).
- Show the references to the user before video generation when the gate policy calls for it. Never
  replace a canonical reference halfway through the film.

### 2. Generate Shots

For each shot in `scene_plan.metadata.shots`:

```python
h3_studio_video.execute({
    "operation": "reference_to_video",
    "prompt": shot["prompt"],
    "reference_image_paths": [ref_path(r) for r in shot["references"]],  # prompt order
    "duration": shot["duration"],
    "resolution": film["resolution"],
    "aspect_ratio": film["aspect_ratio"],
    "model": "fasth3-omniref",
    "output_path": f"projects/{pid}/assets/video/{scene_id}.take1.mp4",
    "scene_id": scene_id,
})
```

- Do not override sampler or steps; the model has fixed settings.
- The Studio queues jobs in order on one GPU. On a timeout the result carries `job_id`; call again with
  `resume_job_id` instead of resubmitting. Never duplicate a job because polling timed out.
- Refresh the checkpoint's `metadata.partial_progress` after each accepted shot.

### 3. Review Every Take and Every Join

Sample frames with `frame_sampler` (first, middle, last) and check identity and costume, set geometry and
light, the intended action and reaction, props, anatomy, unwanted text, and that the closing state hands
over to the next shot. Check speech is present and complete (listen, or transcribe the clip with
`h3_studio_transcriber`). Redo missing story actions, wrong or duplicated characters, broken anatomy,
set discontinuities, truncated essential speech or burned-in text. If a problem is structural, fix the
prompt or the bible first; a new seed alone rarely fixes it. Keep rejected takes (`.take1`, `.take2`) and
log why. Accept minor variations instead of chasing endless takes.

### 4. Optional Score

If the proposal chose a film-length cue, generate it with `h3_studio_music` once the cut length is known
(after edit timings are clear), `duration_seconds` = runtime, and record it as `type: "music"`.

### 5. Show It on the Canvas (Optional, at the Gate)

If the user works in Infinite Canvas, run `canvas_storyboard` with `project_dir` and give them the archive
(`renders/<id>.canvas.zip`) to import from the canvas library, with the "film" plugin enabled. It lays out
character and location cards with their reference images, one shot node per scene wired to its
references in `<Picture N>` order, accepted clips behind their shots, and a timeline. When Canvas Agent is
connected, send the returned `ops` through its `canvas_apply_ops` tool instead to update the open canvas
(pass `media_base_url` if a server the browser can reach serves the project files; otherwise the live
nodes have no media). Edits made on the canvas are not read back automatically: ask the user what they
changed before relying on the artifacts.

## Quality Gate

- references frozen and recorded before the first video job,
- every scene has exactly one accepted clip in the manifest with job id and seed,
- rejected takes and reasons are logged,
- every file exists on disk.

## Common Pitfalls

- Generating a shot before its location reference is locked.
- Uploading references in a different order than the prompt numbers them.
- Cancelling a healthy running GPU job to iterate faster.

---

## Gate Reminder (Binding)

This stage gates on human approval (`human_approval_default: true`). After review passes:
checkpoint with `status="awaiting_human"`, present the summary (the Backlot board renders
the artifact), and **END YOUR TURN**. Do not start the next stage in the same response.
Approval is per-gate — an earlier "go ahead" does not cover this gate.
