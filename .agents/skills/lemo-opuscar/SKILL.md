---
name: lemo-opuscar
description: |
  Author and render a film in one of the Lemo-Opuscar library's code-drawn styles (66+: ink wash, impasto, woodcut, ukiyo-e, pixel RPG, cel anime, data story, satellite briefing, news package, ...) as OpenMontage's `render_runtime="lemo-opuscar"`. Use when the approved concept names a Lemo-Opuscar style, or wants an illustrated / printmaking / data-led look drawn in code with exact text and numbers, optionally mixed with AI plates and H3 clips.
---

# Lemo-Opuscar films inside OpenMontage

Lemo-Opuscar (`MarsSovereign/lemo-opuscar`) is a library of film styles. Each style is a fixed visual and sonic
grammar (`styles/<slug>/STYLE.md`) plus one demo film. A film is a web page whose `window.render(t)` draws any
frame on its own; the library renders it frame by frame, mixes it and masters it. OpenMontage keeps the stages,
gates and artifacts; the library's own guides say how to direct and draw inside the style.

`lemo_opuscar_compose` does the rendering; `video_compose` calls it when `render_runtime="lemo-opuscar"`.

## Preflight

- `lemo_opuscar_compose` operation `doctor`: library path (`LEMO_OPUSCAR_HOME`), Node 20+, ffmpeg, installed packages.
- operation `styles`: the catalogue (slug, English and Chinese names, category, uses, path to `STYLE.md`). Saved
  personal styles live in `~/lemo-my-styles/` (its `README.md` lists them); offer those first.

## Stage by stage

| Stage | What to do with Lemo-Opuscar |
|---|---|
| proposal | Offer 2-3 styles that fit the topic (from `styles`), with the gallery link https://lemomo-ai.github.io/lemo-opuscar/. Lock `render_runtime: "lemo-opuscar"` and record the style in the decision log alongside the other runtimes considered. |
| script | Narration lines as `[{id, text, say?}]` (`say` = what is spoken: numbers written out, polyphones marked). |
| scene_plan | Write the library's `TREATMENT.md` (three candidate structures, then a choice: library `DIRECTOR.md` §4) **before** opening the style's `DEMO.md` or demo code. Never reuse the demo's story, shots or timings. |
| assets | Voice: `local_zh_tts` / `tts_selector`, or in the film folder `python <LIB>/mars/tts_local.py lines.json voices/` (same service, writes `dur.json`), then `python <LIB>/mars/asr.py lines.json voices/`. Plates: `python <LIB>/mars/gen_images.py <profile> shots.json plates/` (ComfyUI :8189). H3/LongCat clips: `python <LIB>/mars/media.py frames clip.mp4 clips/<name>` and `/mars/web/clip.js` in the page. Music: MiniMax Music3 through a ComfyUI profile, then `mars/media.py wav`. Style frames with the real drawing code, checked against `STYLE.md` (library `DIRECTOR.md` §5): `lemo_opuscar_compose` operation `stills`. |
| edit | Write `edit_decisions` with `render_runtime: "lemo-opuscar"`, `cuts: []` and `lemo_opuscar: {film_dir, style, fps?, size?, grain?}`. |
| compose | `video_compose` operation `render`. It runs events → video → `mix.py` → mux (−14 LUFS) and the final self-review. Rerun a failed step with `lemo_opuscar.from_step`. |

## Where the film lives

`projects/<project-id>/lemo/<film-name>/`:

```
index.html     window.DUR, window.render(t), window.READY, optional window.EV (sound cues) and window.TEXTS(t)
timeline.js    one timeline for picture, voice, music and checks
mix.py         reads events.json + voices/ + music → out/mix.wav (run with the library's .venv; LIB and FILM are set)
voices/  plates/  clips/  music/
```

Pages load library code with absolute URLs (`/core/lib.js`, `/mars/web/clip.js`, `/node_modules/three/...`).
A film outside the library folder is mounted at `/@film/` automatically.

## Read before authoring (in the library)

1. `AGENTS.md` → "Style and story": `STYLE.md` is fixed; story, structure and shots come from the user's topic.
2. `styles/<slug>/STYLE.md`, then `DIRECTOR.md` (story, sound, rhythm, camera, self-check) and `TECHNIQUE.md` (render, voice, music, mix).
3. `mars/README.md`: the Windows-native commands and the bridges to the local services used above.

## Rules

- Deterministic pages: no `Date.now()`, no unseeded `Math.random()`, no state carried between frames; a `<video>`
  element is never used for picture (extract stills with `mars/media.py frames`).
- AI plates carry no text: captions, labels, numbers and charts are drawn by the page.
- Hot-topic (民生热点) films take no background music; sound effects stay.
- A missing library or a failed render is a blocker to surface, not a reason to switch runtime silently.
- Films made for the user carry no LemoLab credit or watermark.
