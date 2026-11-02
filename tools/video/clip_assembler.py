"""Frame-accurate assembly of generated clips that carry their own audio.

Model clips with native audio (MiniMax-H3 and similar) often have audio
streams slightly longer or shorter than their video, and AAC encoder priming
shifts audio when clips are joined with the concat demuxer and stream copy.
Over a dozen shots that drifts lip sync. This tool re-times every clip to a
constant frame rate, pads or trims each clip's audio to that clip's exact
video duration, and concatenates everything in one filter graph with a single
encode, then verifies the result with ffprobe.
"""

from __future__ import annotations

import json
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


def probe(path: Path) -> dict[str, Any]:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-count_frames", "-show_streams", "-show_format", "-of", "json", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout
    return json.loads(out)


def video_stream(info: dict[str, Any]) -> dict[str, Any]:
    return next(s for s in info["streams"] if s["codec_type"] == "video")


def has_audio(info: dict[str, Any]) -> bool:
    return any(s["codec_type"] == "audio" for s in info["streams"])


class ClipAssembler(BaseTool):
    name = "clip_assembler"
    version = "0.1.0"
    tier = ToolTier.CORE
    capability = "video_post"
    provider = "ffmpeg"
    stability = ToolStability.BETA
    execution_mode = ExecutionMode.SYNC
    determinism = Determinism.DETERMINISTIC
    runtime = ToolRuntime.LOCAL

    dependencies = ["cmd:ffmpeg", "cmd:ffprobe"]
    install_instructions = "Install FFmpeg (includes ffprobe) and make sure both are on PATH."

    capabilities = ["concat_with_native_audio", "frame_accurate_assembly"]
    supports = {"native_audio": True, "silent_clips": True, "verification": True}
    best_for = [
        "joining AI-generated shots that carry their own dialogue and sound, without sync drift",
        "the clean master of a multi-shot film before subtitles and music",
    ]
    not_good_for = ["transitions other than hard cuts (use video_stitch or video_compose)", "mixing different aspect ratios"]
    fallback_tools = ["video_stitch"]
    resource_profile = ResourceProfile(cpu_cores=4, ram_mb=2048, vram_mb=0, disk_mb=4000)
    idempotency_key_fields = ["clips", "fps", "crf", "preset", "audio_bitrate"]
    side_effects = ["writes the assembled MP4 to output_path"]

    input_schema = {
        "type": "object",
        "required": ["clips", "output_path"],
        "properties": {
            "clips": {"type": "array", "items": {"type": "string"}, "minItems": 1, "description": "Accepted clips in story order."},
            "output_path": {"type": "string", "description": "Clean master, e.g. projects/<id>/renders/<title>.clean.mp4."},
            "fps": {"type": "integer", "default": 24},
            "crf": {"type": "integer", "default": 16},
            "preset": {"type": "string", "default": "slow"},
            "audio_bitrate": {"type": "string", "default": "192k"},
        },
    }
    output_schema = {
        "type": "object",
        "properties": {
            "output": {"type": "string"},
            "frames": {"type": "integer"},
            "duration_seconds": {"type": "number"},
            "segments": {"type": "array"},
            "checks": {"type": "object"},
        },
    }

    def estimate_cost(self, inputs: dict[str, Any]) -> float:
        return 0.0

    def estimate_runtime(self, inputs: dict[str, Any]) -> float:
        return 10.0 + 8.0 * len(inputs.get("clips") or [])

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        started = time.monotonic()
        fps = int(inputs.get("fps") or 24)
        clips = [Path(c) for c in inputs["clips"]]
        missing = [str(c) for c in clips if not c.is_file()]
        if missing:
            return ToolResult(success=False, error=f"clips not found: {', '.join(missing)}")
        try:
            infos = [probe(c) for c in clips]
        except (subprocess.CalledProcessError, StopIteration, ValueError) as exc:
            return ToolResult(success=False, error=f"ffprobe could not read a clip: {exc}")

        sizes = {(video_stream(i)["width"], video_stream(i)["height"]) for i in infos}
        if len(sizes) > 1:
            return ToolResult(success=False, error=f"clips have different sizes {sorted(sizes)}; regenerate or scale them first")
        width, height = sizes.pop()

        segments, parts, labels = [], [], []
        for index, (clip, info) in enumerate(zip(clips, infos)):
            frames = int(video_stream(info)["nb_read_frames"])
            seconds = frames / fps
            segments.append({"clip": str(clip), "frames": frames, "seconds": round(seconds, 6), "had_audio": has_audio(info)})
            parts.append(f"[{index}:v]setpts=N/{fps}/TB,setsar=1[v{index}]")
            if has_audio(info):
                parts.append(
                    f"[{index}:a]aresample=48000,aformat=channel_layouts=stereo,"
                    f"asetpts=PTS-STARTPTS,apad=whole_dur={seconds:.6f},atrim=0:{seconds:.6f}[a{index}]"
                )
            else:
                parts.append(f"anullsrc=r=48000:cl=stereo,atrim=0:{seconds:.6f}[a{index}]")
            labels.append(f"[v{index}][a{index}]")
        parts.append(f"{''.join(labels)}concat=n={len(clips)}:v=1:a=1[v][a]")

        output = Path(inputs["output_path"])
        output.parent.mkdir(parents=True, exist_ok=True)
        cmd = ["ffmpeg", "-y", "-v", "error"]
        for clip in clips:
            cmd += ["-i", str(clip)]
        cmd += [
            "-filter_complex", ";".join(parts), "-map", "[v]", "-map", "[a]",
            "-fps_mode", "cfr", "-r", str(fps),
            "-c:v", "libx264", "-crf", str(inputs.get("crf", 16)), "-preset", inputs.get("preset") or "slow",
            "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", inputs.get("audio_bitrate") or "192k",
            "-movflags", "+faststart", str(output),
        ]
        try:
            self.run_command(cmd)
            result = probe(output)
        except subprocess.CalledProcessError as exc:
            return ToolResult(success=False, error=f"ffmpeg failed: {exc}")

        stream = video_stream(result)
        frames = int(stream["nb_read_frames"])
        expected = sum(s["frames"] for s in segments)
        video_seconds = frames / fps
        audio = next((s for s in result["streams"] if s["codec_type"] == "audio"), None)
        audio_seconds = float(audio.get("duration") or 0) if audio else 0.0
        checks = {
            "frame_rate": stream.get("r_frame_rate"),
            "frames_match_sum_of_clips": frames == expected,
            "expected_frames": expected,
            "size": f"{stream['width']}x{stream['height']}",
            "audio_video_delta_seconds": round(abs(audio_seconds - video_seconds), 3),
        }
        ok = checks["frames_match_sum_of_clips"] and checks["audio_video_delta_seconds"] <= 0.05
        return ToolResult(
            success=ok,
            error=None if ok else f"assembly verification failed: {checks}",
            data={
                "output": str(output),
                "frames": frames,
                "duration_seconds": round(video_seconds, 3),
                "width": width,
                "height": height,
                "segments": segments,
                "checks": checks,
            },
            artifacts=[str(output)],
            cost_usd=0.0,
            duration_seconds=time.monotonic() - started,
        )
