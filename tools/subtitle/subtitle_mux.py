"""Embed subtitle files as selectable soft tracks without re-encoding picture or sound.

Burned-in captions cannot be switched off or translated afterwards. This tool
muxes one or more SRT files as separate ``mov_text`` streams (MP4) with
language tags, titles and a default track, copies video and audio untouched,
and verifies the streams and duration with ffprobe.
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


class SubtitleMux(BaseTool):
    name = "subtitle_mux"
    version = "0.1.0"
    tier = ToolTier.CORE
    capability = "subtitle"
    provider = "ffmpeg"
    stability = ToolStability.BETA
    execution_mode = ExecutionMode.SYNC
    determinism = Determinism.DETERMINISTIC
    runtime = ToolRuntime.LOCAL

    dependencies = ["cmd:ffmpeg", "cmd:ffprobe"]
    install_instructions = "Install FFmpeg (includes ffprobe) and make sure both are on PATH."

    capabilities = ["soft_subtitles", "multi_language_tracks"]
    supports = {"soft_subtitles": True, "multiple_tracks": True, "reencode": False}
    best_for = ["delivering a film with switchable source-language and translated subtitle tracks"]
    not_good_for = ["styled or always-visible captions (burn them with video_compose instead)"]
    fallback_tools = ["subtitle_gen"]
    resource_profile = ResourceProfile(cpu_cores=1, ram_mb=512, vram_mb=0, disk_mb=4000)
    idempotency_key_fields = ["video_path", "tracks"]
    side_effects = ["writes the muxed MP4 to output_path"]

    input_schema = {
        "type": "object",
        "required": ["video_path", "tracks", "output_path"],
        "properties": {
            "video_path": {"type": "string", "description": "Finished picture and sound (MP4)."},
            "tracks": {
                "type": "array",
                "minItems": 1,
                "items": {
                    "type": "object",
                    "required": ["path", "language"],
                    "properties": {
                        "path": {"type": "string", "description": "SRT file."},
                        "language": {"type": "string", "description": "ISO 639-2 code, e.g. zho, eng, jpn."},
                        "title": {"type": "string", "description": "Track name shown in players, e.g. 'Chinese (Original)'."},
                        "default": {"type": "boolean", "description": "Selected by default; at most one track."},
                    },
                },
            },
            "output_path": {"type": "string", "description": "Must differ from video_path."},
        },
    }

    def estimate_cost(self, inputs: dict[str, Any]) -> float:
        return 0.0

    def estimate_runtime(self, inputs: dict[str, Any]) -> float:
        return 5.0

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        started = time.monotonic()
        video, output = Path(inputs["video_path"]), Path(inputs["output_path"])
        tracks = inputs["tracks"]
        if output.resolve() == video.resolve():
            return ToolResult(success=False, error="output_path must differ from video_path")
        missing = [t["path"] for t in tracks if not Path(t["path"]).is_file()]
        if not video.is_file() or missing:
            return ToolResult(success=False, error=f"missing input: {[str(video)] if not video.is_file() else missing}")
        if sum(1 for t in tracks if t.get("default")) > 1:
            return ToolResult(success=False, error="at most one track can be the default")

        cmd = ["ffmpeg", "-y", "-v", "error", "-i", str(video)]
        for track in tracks:
            cmd += ["-i", track["path"]]
        cmd += ["-map", "0:v:0", "-map", "0:a?"]
        for index in range(len(tracks)):
            cmd += ["-map", f"{index + 1}:0"]
        cmd += ["-c:v", "copy", "-c:a", "copy", "-c:s", "mov_text"]
        for index, track in enumerate(tracks):
            cmd += [f"-metadata:s:s:{index}", f"language={track['language']}"]
            if track.get("title"):
                # MP4 players show the track's handler name; title covers other containers.
                cmd += [f"-metadata:s:s:{index}", f"title={track['title']}", f"-metadata:s:s:{index}", f"handler_name={track['title']}"]
            cmd += [f"-disposition:s:{index}", "default" if track.get("default") else "0"]
        output.parent.mkdir(parents=True, exist_ok=True)
        cmd += ["-movflags", "+faststart", str(output)]
        try:
            self.run_command(cmd)
            info = json.loads(subprocess.run(
                ["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(output)],
                capture_output=True, text=True, check=True,
            ).stdout)
            source = json.loads(subprocess.run(
                ["ffprobe", "-v", "error", "-show_format", "-of", "json", str(video)],
                capture_output=True, text=True, check=True,
            ).stdout)
        except subprocess.CalledProcessError as exc:
            return ToolResult(success=False, error=f"ffmpeg failed: {exc}")

        subs = [s for s in info["streams"] if s["codec_type"] == "subtitle"]
        found = [
            {
                "language": s.get("tags", {}).get("language"),
                "title": s.get("tags", {}).get("title") or s.get("tags", {}).get("handler_name"),
                "default": bool(s.get("disposition", {}).get("default")),
            }
            for s in subs
        ]
        checks = {
            "subtitle_tracks": len(subs),
            "languages_match": [t["language"] for t in found] == [t["language"] for t in tracks],
            "duration_delta_seconds": round(abs(float(info["format"]["duration"]) - float(source["format"]["duration"])), 3),
        }
        ok = checks["subtitle_tracks"] == len(tracks) and checks["languages_match"] and checks["duration_delta_seconds"] <= 0.1
        return ToolResult(
            success=ok,
            error=None if ok else f"subtitle mux verification failed: {checks}",
            data={"output": str(output), "tracks": found, "checks": checks},
            artifacts=[str(output)],
            cost_usd=0.0,
            duration_seconds=time.monotonic() - started,
        )
