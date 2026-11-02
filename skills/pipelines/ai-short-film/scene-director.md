# Scene Director - AI Short Film Pipeline

## When To Use

The script and bible are approved. Turn each segment into a storyboard entry and a complete,
submission-ready H3 prompt.

## Prerequisites

| Layer | Resource | Purpose |
|-------|----------|---------|
| Schema | `schemas/artifacts/scene_plan.schema.json` | Artifact validation |
| Prior artifacts | `script` (sections + `metadata.story_bible`), `proposal_packet` | Segments, cards, voices, settings |
| Skill | `skills/creative/h3-reference-prompting.md` | **Mandatory** prompt format |
| Layer 3 | `.agents/skills/minimax-h3/SKILL.md` | Route and operation details |

## Process

### 1. One Scene per Segment

`scene_plan.scenes[i]` mirrors `script.sections[i]`: `type: "character_scene"` (or `"generated"` for an empty
establishing shot), the planned times, `framing`, `movement`, `shot_intent`, `character_actions`, and
`required_assets` listing each reference (`{"type": "image", "description": "character:lin", "source": "generate"}`).

### 2. Shot Specs (`scene_plan.metadata.shots`)

```json
{
  "s01": {
    "operation": "reference_to_video",
    "duration": 10,
    "actual_seconds": 10.125,
    "references": ["character:lin", "location:workshop"],
    "prompt": "subject_definitions: ...",
    "opening_state": "Lin at the bench, right hand on the dial, door shut on screen right",
    "closing_state": "Lin's gaze held toward the door, space on screen right",
    "continuity_to_next": "cut on her look to the door; keep the axis; rain sound continues"
  }
}
```

- `references` order **is** the upload order: the first becomes `<Picture 1>`.
- Use only what the shot needs: usually one or two characters plus one location; even an establishing
  shot carries its location. Never more than six images.
- Paste card and voice text from the bible verbatim. Resolve every placeholder now; nothing in
  `prompt` may still say TODO, `<character>` or similar.

### 3. Continuity Ledger

For each neighbouring pair write screen direction, eyelines, axis, pose, speed, prop state, geography,
light and sound. Cut on reactions, matched motion, reveals or an establishing shot for a new location.
Never repeat the same action on both sides of a cut or teleport a prop between hands.

### 4. Shot Discipline

- Opening state, composition (size, viewpoint, lens feel, depth layers, focus, one motivated camera move),
  timed action with consequence and reaction, exact lines with speaker IDs, synchronous sound, closing state.
- No readable text, captions, logos or watermarks in the picture.

### 5. Show It on the Canvas (Optional)

If the user works in Infinite Canvas, run `canvas_storyboard` with `project_dir` and give them the archive
(`renders/<id>.canvas.zip`) to import from the canvas library, with the "film" plugin enabled. It lays out
character and location cards with their reference images, one shot node per scene wired to its
references in `<Picture N>` order, accepted clips behind their shots, and a timeline. When Canvas Agent is
connected, send the returned `ops` through its `canvas_apply_ops` tool instead to update the open canvas
(pass `media_base_url` if a server the browser can reach serves the project files; otherwise the live
nodes have no media). Edits made on the canvas are not read back automatically: ask the user what they
changed before relying on the artifacts.

## Quality Gate

- every segment has a scene and a complete six-section prompt,
- reference lists match the prompts' `<Picture N>` numbering,
- every join has a continuity note,
- durations and speech fit the budget.

## Common Pitfalls

- Paraphrasing a character per shot, which drifts identity.
- Shot prompts that describe a portrait backdrop as the location.
- Several internal `[Shot N]` cuts inside one segment.

---

## Gate Reminder (Binding)

This stage gates on human approval (`human_approval_default: true`). After review passes:
checkpoint with `status="awaiting_human"`, present the summary (the Backlot board renders
the artifact), and **END YOUR TURN**. Do not start the next stage in the same response.
Approval is per-gate — an earlier "go ahead" does not cover this gate.
