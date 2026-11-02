# Publish Director - AI Short Film Pipeline

## When To Use

The film passed its final review. Package it for release.

## Prerequisites

| Layer | Resource | Purpose |
|-------|----------|---------|
| Schema | `schemas/artifacts/publish_log.schema.json` | Artifact validation |
| Prior artifacts | `render_report`, `final_review`, `proposal_packet`, `script` | Files, settings, story |
| Tools | `h3_studio_image` | Cover |

## Process

1. **Cover.** Generate a poster with `h3_studio_image` (edit mode with the canonical character and
   location references, in the film's treatment). The cover may carry the title even though the film has
   no burned-in text; check the spelling.
2. **Publish notes** (`renders/publish.md`): title, a concise description, an AI-generation disclosure,
   8-12 fitting hashtags, runtime, resolution and the subtitle languages.
3. **Package:** the film with embedded tracks, the clean master, the cover, both SRT files as sidecars
   and the notes. Record each in `publish_log.entries`.
4. Optionally export the final storyboard with `canvas_storyboard` (cards, shots and accepted takes) as
   part of the package, for later revisions on the canvas.
5. Tell the user what QC was actually done and any unresolved issue, and where every file is.

## Quality Gate

- cover matches the locked look and the title is spelled correctly,
- notes disclose AI generation,
- every delivered file exists.

---

## Gate Reminder (Binding)

This stage gates on human approval (`human_approval_default: true`). After review passes:
checkpoint with `status="awaiting_human"`, present the summary (the Backlot board renders
the artifact), and **END YOUR TURN**. Do not start the next stage in the same response.
Approval is per-gate — an earlier "go ahead" does not cover this gate.
