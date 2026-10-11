"""Local TTS service: one GPU speech model behind a small HTTP API.

Run:
    LOCAL_TTS_BACKEND=indextts2 LOCAL_TTS_MODEL_DIR=~/models/IndexTTS-2 \\
    LOCAL_TTS_VOICES_DIR=~/voices python -m services.local_tts.server

Endpoints:
    GET  /health   backend, model, loaded state, voice names
    GET  /voices   voice names with whether a transcript exists
    POST /tts      {"text", "voice"?, "speed"?} -> audio/wav

Requests are serialized: the model holds the GPU, so concurrent calls queue.
OpenMontage reaches this service through tools/audio/local_zh_tts.py.
"""

from __future__ import annotations

import os
import tempfile
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field

from services.local_tts.backends import BACKENDS, Backend, list_voices

MAX_TEXT_CHARS = 2000


class TTSRequest(BaseModel):
    text: str = Field(min_length=1, max_length=MAX_TEXT_CHARS)
    voice: Optional[str] = None
    speed: float = Field(default=1.0, ge=0.5, le=2.0)


def _env_path(name: str) -> Optional[Path]:
    value = os.environ.get(name)
    return Path(value).expanduser() if value else None


def create_app(backend: Optional[Backend] = None, voices_dir: Optional[Path] = None) -> FastAPI:
    if backend is None:
        backend_name = os.environ.get("LOCAL_TTS_BACKEND", "indextts2")
        if backend_name not in BACKENDS:
            raise SystemExit(f"LOCAL_TTS_BACKEND must be one of {sorted(BACKENDS)}")
        backend = BACKENDS[backend_name](_env_path("LOCAL_TTS_MODEL_DIR"))
    voices_dir = voices_dir or _env_path("LOCAL_TTS_VOICES_DIR") or Path("voices")
    default_voice = os.environ.get("LOCAL_TTS_DEFAULT_VOICE")

    lock = threading.Lock()
    state = {"loaded": False, "error": None}

    def ensure_loaded() -> None:
        if state["loaded"]:
            return
        try:
            backend.load()
        except Exception as exc:
            state["error"] = f"{type(exc).__name__}: {exc}"
            raise HTTPException(status_code=503, detail=f"Backend failed to load: {state['error']}")
        state["loaded"], state["error"] = True, None

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        # Load eagerly so the first /tts call is not slow; a failure is
        # reported by /health instead of crashing the service.
        try:
            with lock:
                ensure_loaded()
        except HTTPException:
            pass
        yield

    app = FastAPI(title="OpenMontage local TTS", lifespan=lifespan)

    @app.get("/health")
    def health() -> dict:
        voices = list_voices(voices_dir)
        return {
            "status": "ok" if state["loaded"] else "error" if state["error"] else "loading",
            "backend": backend.name,
            "model": backend.model_label,
            "loaded": state["loaded"],
            "error": state["error"],
            "needs_voice": backend.needs_voice,
            "default_voice": default_voice,
            "voices": sorted(voices),
        }

    @app.get("/voices")
    def voices() -> dict:
        return {
            "voices": [
                {"name": v.name, "has_transcript": v.transcript is not None}
                for v in list_voices(voices_dir).values()
            ]
        }

    @app.post("/tts")
    def tts(req: TTSRequest) -> Response:
        voices = list_voices(voices_dir)
        voice_name = req.voice or default_voice
        voice = None
        if voice_name:
            voice = voices.get(voice_name)
            if voice is None:
                raise HTTPException(
                    status_code=404,
                    detail=f"Unknown voice {voice_name!r}; available: {sorted(voices)}",
                )
        elif backend.needs_voice:
            raise HTTPException(
                status_code=400,
                detail=f"Backend {backend.name} clones a reference voice; pass 'voice' "
                f"or set LOCAL_TTS_DEFAULT_VOICE. Available: {sorted(voices)}",
            )

        with lock:
            ensure_loaded()
            with tempfile.TemporaryDirectory() as tmp:
                out = Path(tmp) / "speech.wav"
                try:
                    backend.synthesize(req.text, voice, req.speed, out)
                except Exception as exc:
                    raise HTTPException(status_code=500, detail=f"Synthesis failed: {exc}")
                if not out.is_file() or out.stat().st_size == 0:
                    raise HTTPException(status_code=500, detail="Backend produced no audio")
                audio = out.read_bytes()

        return Response(
            content=audio,
            media_type="audio/wav",
            headers={"X-TTS-Backend": backend.name, "X-TTS-Model": backend.model_label},
        )

    return app


def main() -> None:
    import uvicorn

    host = os.environ.get("LOCAL_TTS_HOST", "127.0.0.1")
    port = int(os.environ.get("LOCAL_TTS_PORT", "9880"))
    uvicorn.run(create_app(), host=host, port=port)


if __name__ == "__main__":
    main()
