"""Lemo-Opuscar: render a film made in one of the library's code-drawn styles.

Lemo-Opuscar (https://github.com/MarsSovereign/lemo-opuscar, fork of lemomo-ai/lemo-opuscar) is a library of 66+
film styles. A film is a folder with an ``index.html`` exposing ``window.render(t)``; the library renders it frame
by frame in headless Chrome, mixes the film's own ``mix.py`` and masters to -14 LUFS. This tool drives the library's
cross-platform tools (``mars/build.py``, ``core/render/still.mjs``) so the film is a normal compose step:
``video_compose`` routes ``render_runtime="lemo-opuscar"`` here.

The film folder is authored by the agent following the library's guides (Layer 3 skill: ``lemo-opuscar``).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Optional

from tools.base_tool import (
    BaseTool,
    Determinism,
    ExecutionMode,
    ResourceProfile,
    RetryPolicy,
    ToolResult,
    ToolRuntime,
    ToolStability,
    ToolStatus,
    ToolTier,
)

RENDER_TIMEOUT_SECONDS = 4 * 3600
STILLS_TIMEOUT_SECONDS = 900


def library_home() -> Optional[Path]:
    """The Lemo-Opuscar library: LEMO_OPUSCAR_HOME, else ~/lemo-opuscar. None when neither looks like the library."""
    for cand in (os.environ.get("LEMO_OPUSCAR_HOME"), str(Path.home() / "lemo-opuscar")):
        if cand:
            p = Path(cand).expanduser()
            if (p / "core" / "render" / "video.mjs").is_file():
                return p.resolve()
    return None


def library_python(lib: Path) -> str:
    """The library's own .venv (it has numpy/soundfile for the film's mix.py); else this interpreter."""
    for rel in ("Scripts/python.exe", "bin/python"):
        p = lib / ".venv" / rel
        if p.exists():
            return str(p)
    return sys.executable


def _utf8_env(**extra: str) -> dict[str, str]:
    env = dict(os.environ)
    env.setdefault("PYTHONUTF8", "1")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.update(extra)
    return env


class LemoOpuscarCompose(BaseTool):
    name = "lemo_opuscar_compose"
    version = "0.1.0"
    tier = ToolTier.CORE
    capability = "video_post"
    provider = "lemo_opuscar"
    stability = ToolStability.EXPERIMENTAL
    execution_mode = ExecutionMode.SYNC
    determinism = Determinism.DETERMINISTIC
    runtime = ToolRuntime.LOCAL

    dependencies = ["cmd:node", "cmd:ffmpeg", "cmd:ffprobe"]
    install_instructions = (
        "Clone the library and install it (Windows or macOS/Linux):\n"
        "  git clone https://github.com/MarsSovereign/lemo-opuscar\n"
        "  cd lemo-opuscar && python mars/setup.py deps voice\n"
        "then set LEMO_OPUSCAR_HOME=<that folder> in .env. Needs Node 20+ and ffmpeg 7+ on PATH."
    )
    agent_skills = ["lemo-opuscar"]

    capabilities = ["styled_film_render", "review_stills", "style_catalog"]
    supports = {"deterministic_frames": True, "custom_resolution": True, "offline": True}
    best_for = [
        "films in one of the library's code-drawn styles (ink wash, impasto, woodcut, pixel RPG, data story, ...)",
        "explainers and data stories where every number and caption must be exact",
        "mixing AI plates / H3 clips with code-drawn motion, captions and charts on one timeline",
    ]
    not_good_for = [
        "photoreal people or talking heads (use avatar-spokesperson / H3)",
        "stock-scene templated explainers (use remotion)",
    ]

    input_schema = {
        "type": "object",
        "required": ["operation"],
        "properties": {
            "operation": {"type": "string", "enum": ["doctor", "styles", "stills", "render"]},
            "film_dir": {"type": "string", "description": "Film folder with index.html (stills, render)."},
            "output_path": {"type": "string", "description": "render: where the finished MP4 is copied."},
            "times": {
                "type": "array",
                "items": {"type": "number"},
                "description": "stills: seconds to capture.",
            },
            "range": {"type": "string", "description": "stills: 'start:end:step', e.g. '0:40:2'."},
            "fps": {"type": "number", "default": 24},
            "size": {"type": "string", "pattern": "^\\d+x\\d+$", "description": "e.g. 1080x1920 for vertical."},
            "grain": {"type": "number", "default": 2, "description": "Film grain in mux; 0 for pixel/vector styles."},
            "workers": {"type": "integer", "minimum": 1, "maximum": 6, "default": 3},
            "query": {"type": "string", "description": "Passed to the page as location.search (k=v&...)."},
            "from_step": {"type": "string", "enum": ["events", "video", "mix", "mux"], "default": "events"},
        },
    }

    resource_profile = ResourceProfile(cpu_cores=4, ram_mb=4000, vram_mb=2000, disk_mb=4000, network_required=False)
    retry_policy = RetryPolicy(max_retries=0)
    idempotency_key_fields = ["operation", "film_dir", "fps", "size", "grain", "query"]
    side_effects = ["writes frames, mix and the film into film_dir/out and film_dir; copies the film to output_path"]
    user_visible_verification = [
        "Watch the film once at full speed with sound",
        "Check the contact sheet from operation='stills' against the style's STYLE.md before the full render",
    ]

    # ------------------------------------------------------------------ status

    def _runtime_check(self) -> dict[str, Any]:
        lib = library_home()
        node = shutil.which("node")
        node_major = None
        if node:
            try:
                node_major = int(subprocess.run([node, "-p", "process.versions.node.split('.')[0]"],
                                                capture_output=True, text=True, timeout=20).stdout.strip())
            except (ValueError, OSError, subprocess.SubprocessError):
                node_major = None
        missing = []
        if lib is None:
            missing.append("library (set LEMO_OPUSCAR_HOME)")
        elif not (lib / "mars" / "build.py").is_file():
            missing.append("mars/ tools in the library (use the MarsSovereign fork)")
        elif not (lib / "node_modules" / "playwright-core").is_dir():
            missing.append("library packages (python mars/setup.py deps)")
        if not node or (node_major or 0) < 20:
            missing.append("node 20+")
        for b in ("ffmpeg", "ffprobe"):
            if not shutil.which(b):
                missing.append(b)
        return {
            "runtime_available": not missing,
            "library": str(lib) if lib else None,
            "node_major": node_major,
            "python": library_python(lib) if lib else None,
            "missing": missing,
        }

    def get_status(self) -> ToolStatus:
        return ToolStatus.AVAILABLE if self._runtime_check()["runtime_available"] else ToolStatus.UNAVAILABLE

    def estimate_cost(self, inputs: dict[str, Any]) -> float:
        return 0.0

    # ------------------------------------------------------------------ operations

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        op = inputs.get("operation")
        start = time.time()
        if op == "doctor":
            info = self._runtime_check()
            return ToolResult(success=True, data=info, duration_seconds=time.time() - start)
        check = self._runtime_check()
        if op == "styles":
            if not check["library"]:
                return ToolResult(success=False, error="Lemo-Opuscar library not found. " + self.install_instructions)
            return ToolResult(success=True, data={"styles": self._styles(Path(check["library"]))},
                              duration_seconds=time.time() - start)
        if op not in ("stills", "render"):
            return ToolResult(success=False, error=f"unknown operation {op!r}")
        if not check["runtime_available"]:
            return ToolResult(success=False, error="Lemo-Opuscar is not ready: missing " + ", ".join(check["missing"])
                              + ".\n" + self.install_instructions)
        lib = Path(check["library"])
        film = Path(inputs.get("film_dir") or "").expanduser()
        if not film.is_absolute():
            film = film.resolve()
        if not (film / "index.html").is_file():
            return ToolResult(success=False, error=f"film_dir has no index.html: {film}")
        if op == "stills":
            return self._stills(lib, film, inputs, start)
        return self._render(lib, film, inputs, start)

    @staticmethod
    def _styles(lib: Path) -> list[dict[str, Any]]:
        out = []
        for f in sorted((lib / "styles").glob("*/style.json")):
            try:
                d = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            out.append({k: d.get(k) for k in ("slug", "en", "cn", "category_en", "category_cn", "uses")}
                       | {"style_md": str(f.parent / "STYLE.md")})
        return out

    @staticmethod
    def _page_args(inputs: dict[str, Any]) -> list[str]:
        args = []
        if inputs.get("size"):
            if not re.fullmatch(r"\d+x\d+", str(inputs["size"])):
                raise ValueError("size must look like 1080x1920")
            args += ["--size", str(inputs["size"])]
        if inputs.get("query"):
            args += ["--q", str(inputs["query"])]
        return args

    def _stills(self, lib: Path, film: Path, inputs: dict[str, Any], start: float) -> ToolResult:
        times = [str(t) for t in inputs.get("times") or []]
        rng = inputs.get("range")
        if not times and not rng:
            return ToolResult(success=False, error="stills needs 'times' or 'range'")
        out_dir = film / "stills"
        cmd = [shutil.which("node"), "core/render/still.mjs", str(film), *times,
               *(["--range", str(rng)] if rng else []), "--out", str(out_dir), *self._page_args(inputs)]
        r = subprocess.run(cmd, cwd=str(lib), capture_output=True, text=True, encoding="utf-8", errors="replace",
                           env=_utf8_env(), timeout=STILLS_TIMEOUT_SECONDS)
        if r.returncode:
            return ToolResult(success=False, error=f"still.mjs failed (exit {r.returncode}):\n{(r.stderr or r.stdout)[-2000:]}")
        # still.mjs prints "<path> <n>ms" per frame; the path may be relative to the library root
        stills = [str((lib / ln.rsplit(" ", 1)[0]).resolve()) for ln in r.stdout.splitlines()
                  if ln.strip().endswith("ms") and ln.rsplit(" ", 1)[0].endswith(".jpg")]
        sheet = film / "stills" / "_sheet.jpg"
        s = subprocess.run([library_python(lib), "core/render/sheet.py", str(sheet), *stills, "--cols", "4", "--w", "480"],
                           cwd=str(lib), capture_output=True, text=True, encoding="utf-8", errors="replace", env=_utf8_env())
        artifacts = stills + ([str(sheet)] if s.returncode == 0 and sheet.exists() else [])
        return ToolResult(success=True, data={"stills": stills, "contact_sheet": str(sheet) if sheet.exists() else None},
                          artifacts=artifacts, duration_seconds=time.time() - start)

    def _render(self, lib: Path, film: Path, inputs: dict[str, Any], start: float) -> ToolResult:
        name = film.name
        cmd = [library_python(lib), str(lib / "mars" / "build.py"), str(film), "--name", name,
               "--fps", str(inputs.get("fps", 24)), "--grain", str(inputs.get("grain", 2)),
               "--workers", str(inputs.get("workers", 3)), "--from", inputs.get("from_step", "events")]
        if inputs.get("size"):
            cmd += ["--size", str(inputs["size"])]
        if inputs.get("query"):
            cmd += ["--q", str(inputs["query"])]
        try:
            r = subprocess.run(cmd, cwd=str(lib), capture_output=True, text=True, encoding="utf-8", errors="replace",
                               env=_utf8_env(LEMO_OPUSCAR_HOME=str(lib)), timeout=RENDER_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            return ToolResult(success=False, error=f"render timed out after {RENDER_TIMEOUT_SECONDS} s")
        log = (r.stdout or "") + (r.stderr or "")
        built = film / f"{name}.mp4"
        if r.returncode or not built.is_file():
            return ToolResult(success=False, error=f"Lemo-Opuscar build failed (exit {r.returncode}):\n{log[-3000:]}",
                              data={"log_tail": log[-3000:]})
        out = Path(inputs.get("output_path") or built)
        if out.resolve() != built.resolve():
            out.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(built, out)
        loud = {k: float(v) for k, v in re.findall(r"^\s+(I|LRA|Peak):\s+(-?[\d.]+)", log, re.M)}
        warnings = [ln for ln in log.splitlines() if "warning" in ln.lower()]
        return ToolResult(
            success=True,
            data={"output_path": str(out), "film_dir": str(film), "loudness": loud, "warnings": warnings,
                  "render_runtime": "lemo-opuscar"},
            artifacts=[str(out)],
            duration_seconds=time.time() - start,
        )
