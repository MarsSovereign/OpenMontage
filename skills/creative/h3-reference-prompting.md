# H3 Reference-to-Video Prompting

## When To Use

Read before writing or reviewing any MiniMax-H3 reference-to-video prompt (`h3_studio_video` with
`operation: reference_to_video`, or another H3 route in reference mode), and whenever a shot must keep
characters, costumes and locations consistent across a multi-shot film.

The format summarizes MiniMax's official
[full-reference guide](https://github.com/MiniMax-AI/MiniMax-H3/blob/main/.agents/skills/h3-prompt-writing/references/ref-en.txt)
and [base audiovisual guide](https://github.com/MiniMax-AI/MiniMax-H3/blob/main/.agents/skills/h3-prompt-writing/references/base-en.txt).
The timing budgets and reference discipline are production conventions proven on MiniMax-H3 Studio.

## The Six Sections

Write six English sections, in this order:

1. `subject_definitions` — one line per uploaded reference. `<Subject N>` names reusable visible content
   (a person, a location, an essential prop); `<Picture N>` is the N-th uploaded image **in this request's
   upload order**. Say what each image supplies ("identity and costume only", "room layout, materials and
   lighting"). Reference videos are `<Video k>` and their soundtracks `<Audio k>`, numbered after the images.
2. `summary` — starts with `[reference generation]`, states the actual shot length (e.g. 10.125 seconds) and
   what changes in the shot.
3. `retention_analysis` — one line per subject: `fully_preserved`, `partially_preserved`,
   `attribute_transfer` or `weak_reference`, with what to keep. A portrait's backdrop is not part of a
   person's role.
4. `detailed_description` — the visual treatment first (style, palette, light, texture, and "No readable
   text, subtitles, captions, logos or watermarks"), then `[Shot 1]` and the action in prose with local
   elapsed times ("During the first two seconds…", "Between four and six seconds…").
5. `overall_soundscape` — ambience, physical effects and nonverbal sounds. Never repeat dialogue here.
6. `non_diegetic_music` — instrumentation, tempo and dynamics, or `N/A`.

## Rules That Keep a Film Consistent

- **One `[Shot 1]` per job.** Each generated segment is one continuous shot; express phases in prose.
  Later shot markers mean real cuts inside the job, which multi-shot films rarely want.
- **Fixed voices.** Every speaker has a stable ID `(S1)`, `(S2)`… and one fixed English voice description
  from the story bible (age, gender, pitch, timbre, pace, accent, language). Repeat the exact same string at
  every line, then the words: `<Subject 1>, in the low, lightly husky voice of an adult woman, speaking
  Mandarin Chinese with deliberate articulation and an unhurried pace (S1), says quietly: <d>[Chinese]
  这不是求救，是警告。</d>` Delivery notes stay outside `<d>`; the language tag and exact words go inside.
- **Narration** uses `says in an off-screen voiceover`, with its own speaker ID; visible characters' lips
  stay closed during it.
- **Speech budget.** Mandarin about 3 characters per spoken second, English 2–2.5 words. A 5 s shot holds one
  brief line (8–12 characters), 10 s one or two short lines (20–25), 15 s two or three short turns (30–40).
  Leave 0.5–1 s handles at the ends. Overloaded shots get shorter lines or a split, never "fast delivery".
- **Observable action.** Which hand, which object, where it moves, what resists, what changes, how the
  character reacts. Establish spatial anchors before moving the camera; one camera path per shot.
- **Closing state.** End on the pose, gaze and prop state the next segment opens with, and leave screen
  space for the cut.
- **Finish sentences inside the shot.** Separate jobs share no audio or motion state; `<scenetrans>` and
  `<cutoff>` only work within one job.
- **No text in picture.** Subtitles are muxed later as soft tracks.
- **Durations snap** to 24 fps on a 17k+5 frame grid: 5 s → 124 frames (5.1667 s), 10 s → 243 (10.125 s),
  12 s → 294 (12.25 s), 15 s → 362 (15.0833 s). Plan to the actual length.

## Complete Example: One Character Plus One Location

Mandarin, 16:9, a 10 s request (10.125 s actual); character image uploaded first, empty location second.

```text
subject_definitions:
<Subject 1> is Lin, a woman in her early thirties with a narrow face, straight black chin-length hair, a small notch in her left eyebrow, a charcoal cotton jacket and a faded rust scarf, shown in <Picture 1>. This image supplies her identity and costume only.
<Subject 2> is the empty mountain radio workshop shown in <Picture 2>, with a north-facing rain-streaked window on screen left, a scarred oak workbench beneath it, an unlit brass desk lamp, and a green steel door on screen right. This image supplies the room layout, materials and lighting.

summary:
[reference generation] A continuous 10.125-second shot follows <Subject 1> finding an impossible signal inside <Subject 2>. Her attention shifts from the radio to the closed door.

retention_analysis:
<Subject 1> (appears in [Shot 1]): fully_preserved - Keep Lin's facial proportions, eyebrow notch, haircut, jacket and rust scarf; the portrait background is not part of her reference role.
<Subject 2> (appears in [Shot 1]): fully_preserved - Keep the workshop's window, workbench, lamp and door in their established spatial relationship, with the same wet surfaces and cool daylight.

detailed_description:
Cinematic realism with restrained slate blue and rust colors, soft overcast window light, natural skin texture and gentle highlight roll-off. No readable text, subtitles, captions, logos or watermarks.
[Shot 1] A medium close view places <Subject 1> in the left half of the frame at the workbench in <Subject 2>. The green door remains visible on the right, separated from her by negative space. Her left hand rests beside the radio; her right thumb and forefinger hold its tuning dial. A slow, short push toward her face preserves the door in the background and keeps the camera on the room's established side of the axis.
During the first two seconds she rotates the dial a few millimeters. The worn knob offers slight resistance; the radio casing stays planted beneath her left palm. A thin electrical whistle breaks through the static. Her fingers stop immediately, while one bead of rainwater slips from her sleeve onto the oak surface.
Between two and four seconds she leans closer without changing her grip. Her mouth stays closed as she listens. Focus eases from the tuning fingers to her eyes.
Between four and six seconds, <Subject 1>, in the low, lightly husky voice of an adult woman, speaking Mandarin Chinese with deliberate articulation and an unhurried pace (S1), says quietly: <d>[Chinese] 这不是求救，是警告。</d> Her lips follow this line only. The dial remains still throughout the sentence.
From six seconds onward, a single dull knock comes from the door on screen right. She closes her mouth, holds her breath, then turns only her head and eyes toward it. In the final second she holds the rightward gaze, leaving the door shut and space on screen right for the following segment. There is no internal cut, no extra speech and no change to another room.

overall_soundscape:
Fine rain taps the window above a low room tone. Radio static briefly narrows into a whistle, a droplet touches wood, and one muted knock sounds at the green door. A faint breath stops after the knock.

non_diegetic_music:
N/A
```

## Common Pitfalls

- `<Picture N>` numbered by project asset ID instead of this request's upload order.
- Paraphrasing a character or voice per shot instead of pasting the bible text.
- Sending first/last frame images in reference mode (the Studio rejects mixing them).
- More references than the shot needs: normally one or two characters plus one location (max six images).
- Demanding several actions and three lines of dialogue from a 5-second shot.
