"""lemo_opuscar_compose and video_compose's render_runtime='lemo-opuscar' route, against a stub library.

The stub library has the files the tool looks for and a `mars/build.py` that writes a fake film, so no Node,
browser or ffmpeg work happens here. Set LEMO_OPUSCAR_E2E=1 with LEMO_OPUSCAR_HOME pointing at a real installed
library to also render a two-second film for real.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import textwrap
from pathlib import Path

import pytest

from tools.video.lemo_opuscar_compose import LemoOpuscarCompose, library_home
from tools.video.video_compose import VideoCompose

STUB_BUILD = textwrap.dedent(
    """
    import argparse, json, os, sys
    from pathlib import Path
    ap = argparse.ArgumentParser()
    ap.add_argument('film'); ap.add_argument('--name'); ap.add_argument('--fps'); ap.add_argument('--grain')
    ap.add_argument('--workers'); ap.add_argument('--from', dest='start'); ap.add_argument('--size'); ap.add_argument('--q')
    a = ap.parse_args()
    film = Path(a.film)
    if (film / 'FAIL').exists():
        print('mix.py exploded', file=sys.stderr); sys.exit(3)
    (film / f'{a.name}.mp4').write_bytes(b'fake mp4')
    (film / 'args.json').write_text(json.dumps(vars(a)))
    print('    I:         -14.1 LUFS'); print('    LRA:         6.0 LU'); print('    Peak:        -1.3 dBFS')
    """
)


@pytest.fixture
def stub_lib(tmp_path, monkeypatch) -> Path:
    lib = tmp_path / "lemo-opuscar"
    (lib / "core" / "render").mkdir(parents=True)
    (lib / "core" / "render" / "video.mjs").write_text("// stub")
    (lib / "mars").mkdir()
    (lib / "mars" / "build.py").write_text(STUB_BUILD)
    (lib / "node_modules" / "playwright-core").mkdir(parents=True)
    style = lib / "styles" / "ink-wash"
    style.mkdir(parents=True)
    (style / "style.json").write_text(json.dumps(
        {"slug": "ink-wash", "en": "Chinese Ink Wash", "cn": "中国水墨", "category_en": "Hand-drawn & Painting",
         "category_cn": "手绘与绘画", "uses": ["Short drama"]}, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv("LEMO_OPUSCAR_HOME", str(lib))
    return lib


@pytest.fixture
def film(tmp_path) -> Path:
    f = tmp_path / "project" / "lemo" / "river"
    f.mkdir(parents=True)
    (f / "index.html").write_text("<!doctype html>")
    return f


@pytest.fixture
def ready(monkeypatch):
    """Pretend node/ffmpeg are present so the stub library counts as ready on any CI machine."""
    real = LemoOpuscarCompose._runtime_check

    def fake(self):
        info = real(self)
        info["missing"] = [m for m in info["missing"] if not m.startswith(("node", "ffmpeg", "ffprobe"))]
        info["runtime_available"] = not info["missing"]
        return info

    monkeypatch.setattr(LemoOpuscarCompose, "_runtime_check", fake)


def test_library_home_and_styles(stub_lib):
    assert library_home() == stub_lib.resolve()
    r = LemoOpuscarCompose().execute({"operation": "styles"})
    assert r.success
    assert r.data["styles"][0]["cn"] == "中国水墨"
    assert r.data["styles"][0]["style_md"].endswith("STYLE.md")


def test_doctor_names_missing_library(tmp_path, monkeypatch):
    monkeypatch.setenv("LEMO_OPUSCAR_HOME", str(tmp_path / "nowhere"))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    r = LemoOpuscarCompose().execute({"operation": "doctor"})
    assert r.success and not r.data["runtime_available"]
    assert any("LEMO_OPUSCAR_HOME" in m for m in r.data["missing"])
    r = LemoOpuscarCompose().execute({"operation": "render", "film_dir": str(tmp_path)})
    assert not r.success and "not ready" in r.error


def test_render_copies_film_and_reports_loudness(stub_lib, film, ready, tmp_path):
    out = tmp_path / "renders" / "final.mp4"
    r = LemoOpuscarCompose().execute({"operation": "render", "film_dir": str(film), "output_path": str(out),
                                      "size": "1080x1920", "grain": 0})
    assert r.success, r.error
    assert out.read_bytes() == b"fake mp4"
    assert r.data["loudness"] == {"I": -14.1, "LRA": 6.0, "Peak": -1.3}
    args = json.loads((film / "args.json").read_text())
    assert args["size"] == "1080x1920" and args["grain"] == "0" and args["name"] == "river"


def test_render_failure_surfaces_log(stub_lib, film, ready):
    (film / "FAIL").write_text("")
    r = LemoOpuscarCompose().execute({"operation": "render", "film_dir": str(film)})
    assert not r.success
    assert "mix.py exploded" in r.error


def test_render_needs_index_html(stub_lib, ready, tmp_path):
    r = LemoOpuscarCompose().execute({"operation": "render", "film_dir": str(tmp_path)})
    assert not r.success and "index.html" in r.error


def test_video_compose_routes_lemo_runtime(stub_lib, film, ready, tmp_path, monkeypatch):
    monkeypatch.setattr(VideoCompose, "_run_final_review", lambda self, *a, **k: {"status": "pass", "issues_found": []})
    out = tmp_path / "renders" / "final.mp4"
    r = VideoCompose().execute({
        "operation": "render",
        "output_path": str(out),
        "edit_decisions": {"version": "1.0", "cuts": [], "render_runtime": "lemo-opuscar",
                           "lemo_opuscar": {"film_dir": str(film), "style": "ink-wash", "fps": 24}},
    })
    assert r.success, r.error
    assert out.exists()
    assert r.data["final_review_status"] == "pass"


def test_video_compose_lemo_runtime_needs_film_dir():
    r = VideoCompose().execute({"operation": "render",
                                "edit_decisions": {"version": "1.0", "cuts": [], "render_runtime": "lemo-opuscar"}})
    assert not r.success and "film_dir" in r.error


def test_video_compose_lemo_runtime_blocks_when_unavailable(tmp_path, monkeypatch, film):
    monkeypatch.setenv("LEMO_OPUSCAR_HOME", str(tmp_path / "nowhere"))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    r = VideoCompose().execute({"operation": "render", "edit_decisions": {
        "version": "1.0", "cuts": [], "render_runtime": "lemo-opuscar", "lemo_opuscar": {"film_dir": str(film)}}})
    assert not r.success and "BLOCKER" in r.error


def test_render_engines_lists_lemo(stub_lib, ready):
    info = VideoCompose().get_info()
    assert info["render_engines"]["lemo-opuscar"] is True
    assert "lemo_opuscar_note" in info


def test_schemas_accept_lemo_runtime():
    import jsonschema
    root = Path(__file__).resolve().parents[2] / "schemas" / "artifacts"
    schema = json.loads((root / "edit_decisions.schema.json").read_text(encoding="utf-8"))
    jsonschema.validate({"version": "1.0", "cuts": [], "render_runtime": "lemo-opuscar",
                         "lemo_opuscar": {"film_dir": "projects/x/lemo/river", "size": "1080x1920"}}, schema)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({"version": "1.0", "cuts": [], "render_runtime": "lemo-opuscar",
                             "lemo_opuscar": {"film_dir": "x", "size": "vertical"}}, schema)


@pytest.mark.skipif(os.environ.get("LEMO_OPUSCAR_E2E") != "1" or not shutil.which("node"),
                    reason="set LEMO_OPUSCAR_E2E=1 and LEMO_OPUSCAR_HOME to an installed library")
def test_real_library_stills(tmp_path):
    f = tmp_path / "film"
    f.mkdir()
    (f / "index.html").write_text(
        "<!doctype html><meta charset=utf-8><canvas id=c width=1920 height=1080></canvas><script>"
        "const g=c.getContext('2d');window.DUR=2;window.render=t=>{g.fillStyle='#123';g.fillRect(0,0,1920,1080);"
        "g.fillStyle='#e8553a';g.fillRect(200+t*600,400,200,200)};window.READY=true;</script>", encoding="utf-8")
    r = LemoOpuscarCompose().execute({"operation": "stills", "film_dir": str(f), "times": [0.5, 1.5]})
    assert r.success, r.error
    assert len(r.data["stills"]) == 2 and all(Path(p).exists() for p in r.data["stills"])
