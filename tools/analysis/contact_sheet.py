"""Contact sheets and a beat-synced waveform for the post-render review loop.

The agent can't watch a video, but it can look at frames. This tool grabs one
frame per timeline beat (a little after it, so motion has settled), stamps
each with its timestamp and tiles them into a few sheet images the agent reads
in one look. The waveform operation draws the audio with every beat on top and
measures integrated loudness and true peak, so sync and level are checked
together.

Ported from claude-motion's review scripts (https://github.com/whaleyxbt/claude-motion, MIT).
"""

from __future__ import annotations

import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from tools.base_tool import (
    BaseTool,
    Determinism,
    ExecutionMode,
    ResourceProfile,
    ToolResult,
    ToolRuntime,
    ToolStability,
    ToolTier,
)

TARGET_LUFS = -14.0
LUFS_TOLERANCE = 1.0
MAX_TRUE_PEAK_DB = -1.0
WAVE_SIZE = (1800, 280)


class ContactSheet(BaseTool):
    name = "contact_sheet"
    version = "0.1.0"
    tier = ToolTier.CORE
    capability = "analysis"
    provider = "ffmpeg"
    stability = ToolStability.EXPERIMENTAL
    execution_mode = ExecutionMode.SYNC
    determinism = Determinism.DETERMINISTIC
    runtime = ToolRuntime.LOCAL

    dependencies = ["cmd:ffmpeg", "cmd:ffprobe"]
    install_instructions = "Install FFmpeg: https://ffmpeg.org/download.html"
    agent_skills = ["review-loop", "ffmpeg"]

    capabilities = [
        "beat_contact_sheet",
        "scrub_transition",
        "beat_waveform",
        "loudness_check",
    ]
    best_for = [
        "reviewing every beat of a render in a few images before presenting it",
        "scrubbing a transition frame by frame to catch collisions and clipping",
        "checking SFX sync and -14 LUFS / true peak on the final mux",
    ]
    not_good_for = ["semantic understanding of footage (use video_understand)"]

    input_schema = {
        "type": "object",
        "required": ["operation", "input_path"],
        "properties": {
            "operation": {
                "type": "string",
                "enum": ["sheet", "waveform"],
                "description": "sheet: tiled, timestamped frames. waveform: beat-marked waveform PNG plus loudness.",
            },
            "input_path": {"type": "string", "description": "Rendered video (sheet) or audio/video (waveform)."},
            "beats": {
                "type": "array",
                "items": {"type": "number", "minimum": 0},
                "description": "Timeline seconds of every beat: cuts, reveals, text landings.",
            },
            "cuts": {
                "type": "array",
                "items": {"type": "object"},
                "description": "edit_decisions cuts; the start of each primary cut on the output timeline becomes a beat.",
            },
            "timestamps": {
                "type": "array",
                "items": {"type": "number", "minimum": 0},
                "description": "Exact seconds to grab, no offset (sheet only).",
            },
            "every": {"type": "number", "exclusiveMinimum": 0, "description": "Grab a frame every N seconds (sheet only)."},
            "from_seconds": {"type": "number", "minimum": 0, "default": 0},
            "to_seconds": {"type": "number", "minimum": 0},
            "offset": {
                "type": "number",
                "default": 0.8,
                "description": "Seconds after each beat, so springs and fades have settled. "
                "Drop to 0.2-0.3 to catch an element mid-entrance.",
            },
            "include_ends": {
                "type": "boolean",
                "default": True,
                "description": "With beats/cuts, also grab frame 0 (often the thumbnail) and the last frame, without offset.",
            },
            "cols": {"type": "integer", "minimum": 1, "default": 4},
            "rows": {"type": "integer", "minimum": 1, "default": 2},
            "tile_width": {"type": "integer", "minimum": 120, "default": 540},
            "name": {"type": "string", "description": "Sheet file prefix (default beats / every / picked)."},
            "output_dir": {"type": "string", "description": "Where sheets or the waveform go (default: next to input, review/)."},
            "target_lufs": {"type": "number", "default": TARGET_LUFS},
        },
    }

    resource_profile = ResourceProfile(cpu_cores=1, ram_mb=512, vram_mb=0, disk_mb=200)
    idempotency_key_fields = [
        "operation", "input_path", "beats", "cuts", "timestamps", "every", "from_seconds", "to_seconds",
        "offset", "include_ends", "cols", "rows", "tile_width", "name", "output_dir", "target_lufs",
    ]
    side_effects = ["writes PNG sheets or a waveform PNG to output_dir"]
    user_visible_verification = ["Open every sheet and go through the review-loop checklist"]

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        input_path = Path(inputs["input_path"])
        if not input_path.exists():
            return ToolResult(success=False, error=f"Input not found: {input_path}")
        output_dir = Path(inputs.get("output_dir") or input_path.parent / "review")
        output_dir.mkdir(parents=True, exist_ok=True)

        beats = [float(b) for b in inputs.get("beats") or []]
        if inputs.get("cuts"):
            beats += beats_from_cuts(inputs["cuts"])

        start = time.time()
        try:
            if inputs["operation"] == "sheet":
                data = self._sheet(input_path, output_dir, beats, inputs)
            elif inputs["operation"] == "waveform":
                data = self._waveform(input_path, output_dir, beats, inputs)
            else:
                return ToolResult(success=False, error=f"Unknown operation: {inputs['operation']}")
        except (subprocess.CalledProcessError, ValueError) as exc:
            detail = exc.stderr.decode(errors="ignore")[-500:] if getattr(exc, "stderr", None) else str(exc)
            return ToolResult(success=False, error=detail)

        artifacts = data.get("sheets") or [data["waveform"]]
        return ToolResult(
            success=True,
            data=data,
            artifacts=artifacts,
            duration_seconds=round(time.time() - start, 2),
        )

    def _sheet(self, video: Path, output_dir: Path, beats: list[float], inputs: dict[str, Any]) -> dict[str, Any]:
        duration = probe_duration(video)
        if inputs.get("timestamps"):
            times = [float(t) for t in inputs["timestamps"]]
            name = inputs.get("name") or "picked"
        elif inputs.get("every"):
            step = float(inputs["every"])
            t = float(inputs.get("from_seconds", 0))
            end = float(inputs.get("to_seconds", duration))
            times = []
            while t <= end + 1e-6:
                times.append(round(t, 3))
                t += step
            name = inputs.get("name") or f"every{step:g}"
        elif beats:
            offset = float(inputs.get("offset", 0.8))
            times = [b + offset for b in beats]
            if inputs.get("include_ends", True):
                times += [0.0, duration]
            name = inputs.get("name") or "beats"
        else:
            raise ValueError("Pass beats, cuts, timestamps or every")

        last = max(duration - 1 / 120, 0)
        times = sorted({round(min(max(t, 0.0), last), 2) for t in times})

        frame_dir = output_dir / name
        shutil.rmtree(frame_dir, ignore_errors=True)
        frame_dir.mkdir(parents=True)
        width = int(inputs.get("tile_width", 540))
        labels = True
        for i, t in enumerate(times):
            frame = frame_dir / f"{i:03d}.png"
            scale = f"scale={width}:-2"
            label = (
                f"drawtext=text='t={t:.2f}':x=10:y=10:fontsize={round(width / 24)}"
                ":fontcolor=white:box=1:boxcolor=black@0.6:boxborderw=6"
            )
            if labels:
                try:
                    _grab(video, t, f"{scale},{label}", frame)
                    continue
                except subprocess.CalledProcessError:
                    labels = False  # ffmpeg built without drawtext/fontconfig
            _grab(video, t, scale, frame)

        cols, rows = int(inputs.get("cols", 4)), int(inputs.get("rows", 2))
        per_sheet = cols * rows
        sheets = []
        for s in range(0, len(times), per_sheet):
            out = output_dir / f"{name}-{s // per_sheet + 1}.png"
            count = min(per_sheet, len(times) - s)
            c, r = min(cols, count), -(-count // cols)  # shrink a short last sheet
            _ffmpeg([
                "-framerate", "1", "-start_number", str(s), "-i", str(frame_dir / "%03d.png"),
                "-frames:v", "1", "-vf", f"tile={c}x{r}:padding=6:color=0x333333", str(out),
            ])
            sheets.append(str(out))

        return {
            "operation": "sheet",
            "times": times,
            "frame_count": len(times),
            "sheets": sheets,
            "frames_dir": str(frame_dir),
            "timestamps_labelled": labels,
        }

    def _waveform(self, media: Path, output_dir: Path, beats: list[float], inputs: dict[str, Any]) -> dict[str, Any]:
        duration = probe_duration(media)
        w, h = WAVE_SIZE
        shown = [b for b in sorted(set(beats)) if 0 <= b <= duration]
        marks = "".join(
            f",drawbox=x={round(b / duration * w)}:y=0:w=2:h=ih:color=0xD97757@0.9:t=fill" for b in shown
        )
        # Beat times printed along the top, alternating rows so neighbours don't overlap.
        labels = "".join(
            f",drawtext=text='{b:.2f}':x={round(b / duration * w) + 4}:y={6 + (i % 2) * 16}"
            ":fontsize=13:fontcolor=0xD97757"
            for i, b in enumerate(shown)
        )
        grid = f",drawgrid=w={w / duration:.3f}:h={h}:color=white@0.12"
        png = output_dir / f"{media.stem}-wave.png"
        base = (
            f"color=c=0x141413:s={w}x{h}:d=1[bg];"
            f"[0:a]aformat=channel_layouts=mono,showwavespic=s={w}x{h}:scale=sqrt:colors=0x8A877F[w];"
            f"[bg][w]overlay=format=auto{grid}{marks}"
        )
        try:
            _ffmpeg(["-i", str(media), "-filter_complex", base + labels, "-frames:v", "1", str(png)])
        except subprocess.CalledProcessError:
            if not labels:
                raise
            _ffmpeg(["-i", str(media), "-filter_complex", base, "-frames:v", "1", str(png)])  # no drawtext

        lufs, peak = measure_loudness(media)
        target = float(inputs.get("target_lufs", TARGET_LUFS))
        warnings = []
        if lufs is None or abs(lufs - target) > LUFS_TOLERANCE:
            warnings.append(f"Integrated loudness {lufs} LUFS is outside {target} ±{LUFS_TOLERANCE:g}")
        if peak is not None and peak > MAX_TRUE_PEAK_DB:
            warnings.append(f"True peak {peak} dBFS is above {MAX_TRUE_PEAK_DB:g}; AAC encoding may clip")
        return {
            "operation": "waveform",
            "waveform": str(png),
            "beats_marked": len(set(beats)),
            "lufs": lufs,
            "true_peak_db": peak,
            "target_lufs": target,
            "loudness_ok": not warnings,
            "warnings": warnings,
        }


def beats_from_cuts(cuts: list[dict[str, Any]]) -> list[float]:
    """Output-timeline start of each primary cut, laid end to end in order."""
    beats, t = [], 0.0
    for cut in cuts:
        if cut.get("layer", "primary") != "primary":
            continue
        beats.append(round(t, 3))
        speed = float(cut.get("speed") or 1.0)
        t += (float(cut["out_seconds"]) - float(cut["in_seconds"])) / speed
    return beats


def probe_duration(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        capture_output=True, check=True,
    ).stdout.decode().strip()
    try:
        return float(out)
    except ValueError as exc:
        raise ValueError(f"Could not read duration of {path}") from exc


def measure_loudness(path: Path) -> tuple[float | None, float | None]:
    """Integrated LUFS and true peak (dBFS) from ffmpeg's ebur128 summary."""
    r = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-af", "ebur128=peak=true", "-f", "null", "-"],
        capture_output=True,
    )
    err = r.stderr.decode(errors="ignore")
    summary = err[err.rfind("Summary"):]
    lufs = re.search(r"I:\s+(-?[\d.]+) LUFS", summary)
    peak = re.search(r"Peak:\s+(-?[\d.]+) dBFS", summary)
    return (float(lufs.group(1)) if lufs else None, float(peak.group(1)) if peak else None)


def _grab(video: Path, t: float, vf: str, out: Path) -> None:
    _ffmpeg(["-ss", f"{t}", "-i", str(video), "-frames:v", "1", "-vf", vf, str(out)])


def _ffmpeg(args: list[str]) -> None:
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", *args], capture_output=True, check=True)
