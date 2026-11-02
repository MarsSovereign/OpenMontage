"""Export an ai-short-film project as an Infinite Canvas storyboard.

Builds an importable canvas archive (the canvas's own `projects.json` v3
format plus media files) from a project's artifacts: character and location
cards wired to their frozen reference images, one shot node per scene wired to
its references in prompt order, each accepted clip as a video node behind its
shot, and a timeline. The node types come from the canvas `film` plugin
(`film:shot`, `film:card`, `film:timeline`); without the plugin the canvas
keeps them as placeholders and the images and videos still show.

The same layout is returned as Canvas Agent operations (`canvas_apply_ops`)
for live sync. Those carry no file bytes, so live sync shows media only when
`media_base_url` points at a server the browser can reach.
"""

from __future__ import annotations

import hashlib
import json
import mimetypes
import shutil
import subprocess
import time
import zipfile
from datetime import datetime, timezone
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

ARTIFACT_NAMES = ("proposal_packet", "script", "scene_plan", "asset_manifest")
CARD_W, CARD_H, IMAGE_W = 340, 150, 160
SHOT_W, SHOT_H, TAKE_W = 360, 300, 320
GAP = 48


def load_artifacts(project_dir: Path) -> dict[str, dict]:
    """artifacts/<name>.json first, then artifacts embedded in checkpoints."""
    found: dict[str, dict] = {}
    for name in ARTIFACT_NAMES:
        path = project_dir / "artifacts" / f"{name}.json"
        if path.is_file():
            found[name] = json.loads(path.read_text(encoding="utf-8"))
    for checkpoint in sorted(project_dir.glob("checkpoint_*.json"), key=lambda p: p.stat().st_mtime):
        try:
            payload = json.loads(checkpoint.read_text(encoding="utf-8")).get("artifacts") or {}
        except (OSError, ValueError):
            continue
        for name, value in payload.items():
            if name in ARTIFACT_NAMES and name not in found and isinstance(value, dict):
                found[name] = value
    return found


def resolve(project_dir: Path, raw: str) -> Path | None:
    if not raw:
        return None
    path = Path(raw)
    for candidate in ([path] if path.is_absolute() else [project_dir / path, Path.cwd() / path]):
        if candidate.is_file():
            return candidate
    return None


def media_info(path: Path) -> dict[str, Any]:
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    info: dict[str, Any] = {"mimeType": mime, "bytes": path.stat().st_size}
    if mime.startswith("image/"):
        try:
            from PIL import Image

            with Image.open(path) as image:
                info["naturalWidth"], info["naturalHeight"] = image.size
        except Exception:
            pass
    elif mime.startswith("video/") and shutil.which("ffprobe"):
        try:
            out = subprocess.run(
                ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height:format=duration", "-of", "json", str(path)],
                capture_output=True, text=True, check=True,
            ).stdout
            data = json.loads(out)
            stream = (data.get("streams") or [{}])[0]
            info["naturalWidth"], info["naturalHeight"] = stream.get("width"), stream.get("height")
            info["durationMs"] = round(float(data.get("format", {}).get("duration", 0)) * 1000)
        except (subprocess.CalledProcessError, ValueError):
            pass
    return info


def fitted_height(info: dict[str, Any], width: int, fallback: int) -> int:
    w, h = info.get("naturalWidth"), info.get("naturalHeight")
    return round(width * h / w) if w and h else fallback


class CanvasStoryboard(BaseTool):
    name = "canvas_storyboard"
    version = "0.1.0"
    tier = ToolTier.PUBLISH
    capability = "storyboard_export"
    provider = "infinite_canvas"
    stability = ToolStability.BETA
    execution_mode = ExecutionMode.SYNC
    determinism = Determinism.DETERMINISTIC
    runtime = ToolRuntime.LOCAL

    dependencies = []
    install_instructions = (
        "No setup needed to export. To view it, open Infinite Canvas (github.com/MarsSovereign/infinite-canvas), "
        "install the 'film' plugin, and import the archive from the canvas library."
    )
    capabilities = ["export_canvas_archive", "canvas_agent_ops"]
    supports = {"reference_images": True, "accepted_clips": True, "live_sync_ops": True}
    best_for = [
        "reviewing an ai-short-film storyboard visually: cards, shots, references and takes on one canvas",
        "handing a storyboard to someone who will re-generate or swap shots by hand on the canvas",
    ]
    not_good_for = ["pipelines other than ai-short-film (needs story_bible and metadata.shots)"]
    resource_profile = ResourceProfile(cpu_cores=1, ram_mb=512, vram_mb=0, disk_mb=2000)
    side_effects = ["writes the canvas archive to output_path"]

    input_schema = {
        "type": "object",
        "required": ["project_dir"],
        "properties": {
            "project_dir": {"type": "string", "description": "projects/<project-id>"},
            "output_path": {"type": "string", "description": "Archive path; defaults to <project_dir>/renders/<id>.canvas.zip."},
            "title": {"type": "string", "description": "Canvas title; defaults to the script title."},
            "include_videos": {"type": "boolean", "default": True, "description": "Embed accepted clips (larger archive)."},
            "media_base_url": {"type": "string", "description": "Optional URL prefix that serves project files, used only in the live-sync ops."},
        },
    }
    output_schema = {
        "type": "object",
        "properties": {"output": {"type": "string"}, "nodes": {"type": "integer"}, "ops": {"type": "array"}, "warnings": {"type": "array"}},
    }

    def estimate_cost(self, inputs: dict[str, Any]) -> float:
        return 0.0

    def estimate_runtime(self, inputs: dict[str, Any]) -> float:
        return 5.0

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        started = time.monotonic()
        project_dir = Path(inputs["project_dir"])
        artifacts = load_artifacts(project_dir)
        missing = [name for name in ("script", "scene_plan") if name not in artifacts]
        if missing:
            return ToolResult(success=False, error=f"{project_dir} has no {', '.join(missing)} artifact yet")
        script, plan = artifacts["script"], artifacts["scene_plan"]
        manifest = artifacts.get("asset_manifest", {"assets": []})
        bible = (script.get("metadata") or {}).get("story_bible") or {}
        shots = (plan.get("metadata") or {}).get("shots") or {}
        sections = {s["id"]: s for s in script.get("sections", [])}
        assets = manifest.get("assets", [])
        warnings: list[str] = []
        include_videos = inputs.get("include_videos", True)
        base_url = (inputs.get("media_base_url") or "").rstrip("/")

        nodes: list[dict] = []
        connections: list[dict] = []
        files: list[tuple[str, Path, dict]] = []  # (storageKey, path, info)

        def media_node(node_id: str, kind: str, path: Path, title: str, x: float, y: float, width: int) -> dict:
            info = media_info(path)
            digest = hashlib.sha1(str(path.resolve()).encode()).hexdigest()[:16]
            key = f"{'image' if kind == 'image' else 'video'}:om-{digest}"
            if not any(f[0] == key for f in files):
                files.append((key, path, info))
            height = fitted_height(info, width, 180 if kind == "video" else width)
            # Image nodes are restored from storageKey only when content is non-empty; the key doubles as placeholder.
            metadata = {"content": key, "storageKey": key, "status": "success", **{k: v for k, v in info.items() if v is not None}}
            if base_url:
                metadata["liveUrl"] = f"{base_url}/{path.relative_to(project_dir).as_posix()}" if path.is_relative_to(project_dir) else ""
            node = {"id": node_id, "type": kind, "title": title, "position": {"x": x, "y": y}, "width": width, "height": height, "metadata": metadata}
            nodes.append(node)
            return node

        def connect(src: str, dst: str):
            connections.append({"id": f"conn-{src}-{dst}", "fromNodeId": src, "toNodeId": dst})

        # Cards: characters then locations, each with its canonical reference image on the left.
        card_ids: dict[str, str] = {}
        entries = [("character", c) for c in bible.get("characters", [])] + [("location", l) for l in bible.get("locations", [])]
        for column, (kind, entry) in enumerate(entries):
            ident = entry.get("id") or f"{kind}{column + 1}"
            x = column * (IMAGE_W + CARD_W + GAP * 2)
            y = 260
            ref = next(
                (
                    a for a in assets
                    if a.get("subtype") == f"{kind}_ref"
                    and (Path(a.get("path", "")).stem == ident or a.get("id") in (ident, f"ref-{ident}"))
                ),
                None,
            )
            ref_path = resolve(project_dir, ref.get("path", "")) if ref else None
            card_id = f"card-{ident}"
            card_ids[f"{kind}:{ident}"] = card_id
            if ref_path:
                image = media_node(f"ref-{ident}", "image", ref_path, f"{entry.get('name') or ident} 参考", x, y, IMAGE_W)
                connect(image["id"], card_id)
            else:
                warnings.append(f"no reference image for {kind}:{ident}")
            nodes.append({
                "id": card_id, "type": "film:card", "title": entry.get("name") or ident,
                "position": {"x": x + IMAGE_W + GAP, "y": y}, "width": CARD_W, "height": CARD_H,
                "metadata": {
                    "cardKind": kind, "cardName": entry.get("name") or ident, "speakerId": entry.get("speaker_id", ""),
                    "cardDescription": entry.get("card", ""), "voice": entry.get("voice", ""),
                },
            })

        # Shots in scene order, references wired in prompt order, accepted clip behind each shot.
        shots_y = 260 + CARD_H + 220
        accepted = 0
        for order, scene in enumerate(plan.get("scenes", []), 1):
            scene_id = scene["id"]
            spec = shots.get(scene_id, {})
            section = sections.get(scene.get("script_section_id") or scene_id, {})
            x = (order - 1) * (SHOT_W + GAP)
            clip = next((a for a in reversed(assets) if a.get("type") == "video" and a.get("scene_id") == scene_id), None)
            clip_path = resolve(project_dir, clip.get("path", "")) if clip else None
            shot_id = f"shot-{scene_id}"
            shot_node = {
                "id": shot_id, "type": "film:shot", "title": scene_id,
                "position": {"x": x, "y": shots_y}, "width": SHOT_W, "height": SHOT_H,
                "metadata": {
                    "shotId": scene_id, "order": order,
                    "duration": spec.get("duration") or round(scene["end_seconds"] - scene["start_seconds"], 3),
                    "dialogue": section.get("text", ""), "action": scene.get("description", ""),
                    "continuity": spec.get("continuity_to_next", ""), "prompt": spec.get("prompt", ""),
                    "shotStatus": "accepted" if clip_path else "planned", "takeCount": 1 if clip_path else 0,
                },
            }
            nodes.append(shot_node)
            for ref in spec.get("references", []):
                if ref in card_ids:
                    connect(card_ids[ref], shot_id)
                else:
                    warnings.append(f"{scene_id}: reference {ref} has no card in the story bible")
            if clip_path and include_videos:
                take = media_node(f"take-{scene_id}", "video", clip_path, f"{scene_id} · 采用", x + (SHOT_W - TAKE_W) / 2, shots_y + SHOT_H + GAP, TAKE_W)
                connect(shot_id, take["id"])
                shot_node["metadata"]["acceptedVideoId"] = take["id"]
            if clip_path:
                accepted += 1

        width = max(720, len(plan.get("scenes", [])) * (SHOT_W + GAP) - GAP)
        nodes.insert(0, {"id": "timeline", "type": "film:timeline", "title": "时间线", "position": {"x": 0, "y": 0}, "width": width, "height": 180, "metadata": {}})

        title = inputs.get("title") or script.get("title") or project_dir.name
        now = datetime.now(timezone.utc).isoformat()
        canvas_id = f"om-{hashlib.sha1(str(project_dir.resolve()).encode()).hexdigest()[:12]}"
        project = {
            "id": canvas_id, "title": title, "createdAt": now, "updatedAt": now,
            "nodes": nodes, "connections": connections, "chatSessions": [], "activeChatId": None,
            "backgroundMode": "dots", "showImageInfo": False, "viewport": {"x": 80, "y": 80, "k": 0.6},
        }
        export_files = []
        output = Path(inputs.get("output_path") or project_dir / "renders" / f"{project_dir.name}.canvas.zip")
        output.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(output, "w", zipfile.ZIP_STORED) as archive:
            for key, path, info in files:
                member = f"projects/{canvas_id}/files/{key.replace(':', '_')}{path.suffix.lower()}"
                archive.write(path, member)
                export_files.append({"storageKey": key, "path": member, "mimeType": info["mimeType"], "bytes": info["bytes"]})
            data = {"app": "infinite-canvas", "version": 3, "exportedAt": now, "projects": [{"project": project, "files": export_files}]}
            archive.writestr("projects.json", json.dumps(data, ensure_ascii=False, indent=2))

        ops = []
        for node in nodes:
            meta = dict(node["metadata"])
            if node["type"] in ("image", "video"):
                live = meta.pop("liveUrl", "")
                meta.pop("storageKey", None)
                meta["content"] = live
            ops.append({"type": "add_node", "id": node["id"], "nodeType": node["type"], "title": node["title"], "position": node["position"], "width": node["width"], "height": node["height"], "metadata": meta})
        ops += [{"type": "connect_nodes", "id": c["id"], "fromNodeId": c["fromNodeId"], "toNodeId": c["toNodeId"]} for c in connections]

        return ToolResult(
            success=True,
            data={
                "output": str(output),
                "canvas_id": canvas_id,
                "title": title,
                "nodes": len(nodes),
                "connections": len(connections),
                "shots": len(plan.get("scenes", [])),
                "accepted_shots": accepted,
                "embedded_files": len(export_files),
                "warnings": warnings,
                "ops": ops,
            },
            artifacts=[str(output)],
            cost_usd=0.0,
            duration_seconds=time.monotonic() - started,
        )
