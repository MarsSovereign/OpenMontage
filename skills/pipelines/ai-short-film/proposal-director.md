# Proposal Director - AI Short Film Pipeline

## When To Use

The user wants a story told as a film: a short film, an episode, an adapted chapter, a fable, a drama
scene. Dialogue, recurring characters and continuity across shots matter more than montage energy.
For trailers and mood edits use `cinematic`; for talking presenters use `avatar-spokesperson`.

## Prerequisites

| Layer | Resource | Purpose |
|-------|----------|---------|
| Schema | `schemas/artifacts/proposal_packet.schema.json`, `decision_log.schema.json` | Artifact validation |
| Registry | `registry.provider_menu_summary()` | Real provider envelope (preflight) |
| Tools | `h3_studio_video`, `h3_studio_image` (status), `video_selector` (alternatives) | Generation route |
| Skill | `skills/creative/h3-reference-prompting.md` | What a shot can hold |

## Process

### 1. Confirm the Film Settings Together

Ask once, reuse anything the user already said, and record the answers in
`proposal_packet.metadata.film`:

| Setting | Default to recommend | Notes |
|---------|---------------------|-------|
| Subject, audience, length | — | Required content and constraints |
| Frame | 16:9 for a film, 9:16 for mobile | `aspect_ratio` for every image and shot |
| Resolution | 768p (544p when speed matters) | Studio upscalers can lift the master later |
| Video route | `h3_studio_video`, reference to video, `model: fasth3-omniref` | Hosted H3 only with approval and a cost estimate |
| Spoken language | Ask; recommend from the story | Never assume it |
| Visual treatment | Recommend one with a reason (stylized animation for folklore, cinematic realism for present-day suspense) | One treatment for people and sets |
| Subtitles | Source-language track plus a translation the user names; ask which is default | Soft tracks, never burned in |

Do not generate images or video before these are settled.

### 2. Check the Studio

Run preflight. If `h3_studio_video` is unavailable, read its `install_instructions` to the user
(`H3_STUDIO_URL`, optional `H3_STUDIO_TOKEN`) and offer the hosted H3 routes from `video_selector`
with their cost. Do not switch routes silently.

### 3. Offer Concepts

Present 3-5 concepts with different emotional arcs: logline, stakes, the opening image or question,
the ending, the visual treatment and why it suits the story. Recommend one.

### 4. Estimate Honestly

- Shots: runtime / ~10 s per segment, plus ~30% retakes.
- GPU time on an RTX 4090 with `fasth3-omniref`: ~70 s per 5 s shot at 768p (~40 s at 544p), longer shots
  scale roughly linearly; reference images ~15 s each; the Studio runs one job at a time.
- Money: `0` on the Studio. List any hosted fallback separately with its price.

A 3-minute film is ~18 segments, ~25 with retakes: about 45-60 minutes of GPU time at 768p.

### 5. Lock the Runtime and Music Plan

- Assembly uses `clip_assembler` (FFmpeg) because the shots carry their own dialogue and sound, so the
  recommended `render_runtime` is `ffmpeg`. Check `video_compose.get_info()["render_engines"]`: when Remotion
  and hyperframes are available, present both per the "Present Both Composition Runtimes" rule, with what
  each would add here (authored title cards, end credits) and why plain FFmpeg keeps the shots untouched.
  Wait for the user's choice and log a `render_runtime_selection` decision listing every option in
  `options_considered`.
- Music: H3 can score inside each shot (`non_diegetic_music`), but separate shots do not share a
  continuous score. Offer a film-length cue from `h3_studio_music`, the music library, or in-shot music
  only, and record the choice.

### 6. Log Decisions

Append `decision_log` entries for provider, model, resolution, language, subtitles, runtime and music.

## Quality Gate

- every film setting confirmed by the user,
- one recommended concept with a concrete ending,
- GPU time and any money cost stated,
- runtime and music decisions logged with alternatives.

## Common Pitfalls

- Choosing the spoken or subtitle language for the user.
- Promising a 15-minute film without stating hours of GPU queue time.
- Offering a style list without a recommendation.

---

## Gate Reminder (Binding)

This stage gates on human approval (`human_approval_default: true`). After review passes:
checkpoint with `status="awaiting_human"`, present the summary (the Backlot board renders
the artifact), and **END YOUR TURN**. Do not start the next stage in the same response.
Approval is per-gate — an earlier "go ahead" does not cover this gate.
