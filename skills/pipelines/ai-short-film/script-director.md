# Script Director - AI Short Film Pipeline

## When To Use

The concept is approved. Write the **whole** story, the story bible and the dialogue before any shot
is planned or any image is generated.

## Prerequisites

| Layer | Resource | Purpose |
|-------|----------|---------|
| Schema | `schemas/artifacts/script.schema.json` | Artifact validation |
| Prior artifacts | `proposal_packet` (`metadata.film`) | Language, frame, treatment, length |
| Skill | `skills/creative/h3-reference-prompting.md` | Speech budget and voice rules |
| Skill | `skills/creative/storytelling.md` | Structure |

## Process

### 1. Story

Logline, setting, stakes, complete beginning, middle and end, emotional arc and runtime. The opening
earns attention within 5-15 seconds with an image, event, question or contradiction, and the film pays
it off later. Give context through action; use narration only for what the picture cannot show.

### 2. Story Bible (`script.metadata.story_bible`)

```json
{
  "characters": [{
    "id": "lin", "name": "Lin", "role": "lead",
    "card": "woman in her early thirties, narrow face, straight black chin-length hair, small notch in left eyebrow, charcoal cotton jacket, faded rust scarf",
    "speaker_id": "S1",
    "voice": "the low, lightly husky voice of an adult woman, speaking Mandarin Chinese with deliberate articulation and an unhurried pace",
    "voice_note_for_user": "低沉微哑的成年女声，语速从容",
    "variants": []
  }],
  "narrator": null,
  "locations": [{
    "id": "workshop", "name": "Mountain radio workshop",
    "card": "north-facing rain-streaked window on screen left, scarred oak workbench beneath it, unlit brass desk lamp, green steel door on screen right",
    "light": "overcast cool daylight", "sound": "fine rain on glass, low room tone"
  }],
  "film_language": "palette, contrast, lens tendencies, camera behavior, texture, sound strategy",
  "continuity_rules": ["Lin's scarf is wet until segment 6", "The door stays shut until segment 4"]
}
```

- Principal and recurring characters get a full card and one fixed voice string; keep voices clearly
  distinguishable. One-off extras may stay prompt-only.
- Any change of age, clothing or accessory is a new `variants` entry with the segments it applies to.
- Locations get layout, landmarks, entrances, materials, light, weather and recurring sound.
- Choose one coherent visual grammar: deliberate composition and colour dramaturgy, clear action
  geography and physical cause and effect. Write it concretely; a director's name alone is not direction.

### 3. Segments (`script.sections`)

Each section is one future H3 shot of 5-15 seconds:

- `text`: the exact spoken words of that segment, with speaker IDs (empty string for a silent shot),
- `start_seconds` / `end_seconds`: planned with actual snapped durations (5 s → 5.1667 s, 10 s → 10.125 s),
- `speaker_directions`: intention and delivery,
- `label`: the story beat.

Check every segment against the speech budget in `h3-reference-prompting.md`. Finish each sentence inside
its segment and place cuts at natural pauses.

## Quality Gate

- the full story exists, start to end, before scene planning,
- every recurring character and location has a card, every speaker a fixed voice,
- no segment exceeds its speech budget or 15 seconds,
- the language matches `metadata.film`.

## Common Pitfalls

- Writing only the first scenes and improvising the rest during generation.
- Two characters with near-identical voice descriptions.
- Dialogue that explains what the picture already shows.

---

## Gate Reminder (Binding)

This stage gates on human approval (`human_approval_default: true`). After review passes:
checkpoint with `status="awaiting_human"`, present the summary (the Backlot board renders
the artifact), and **END YOUR TURN**. Do not start the next stage in the same response.
Approval is per-gate — an earlier "go ahead" does not cover this gate.
