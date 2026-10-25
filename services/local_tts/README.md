# Local TTS service

Runs one local speech model behind a small HTTP API so OpenMontage can narrate
in natural Mandarin without a cloud key. The `local_zh_tts` tool
(`tools/audio/local_zh_tts.py`) calls it and joins the normal TTS selector.

| Backend | Model | GPU memory | Notes |
|---|---|---|---|
| `indextts2` | [IndexTTS2](https://github.com/index-tts/index-tts) | ~8 GB fp16 | Zero-shot cloning from a reference clip, emotion-aware |
| `cosyvoice` | [CosyVoice 2](https://github.com/FunAudioLLM/CosyVoice) 0.5B | ~4 GB | Lighter and faster; uses the clip's transcript when present |
| `indextts25` | IndexTTS2.5 (`indextts.infer_v2_5`, same repo) | ~8 GB bf16 | Faster than 2; native speed via `duration_factor`; `LOCAL_TTS_LANG` (default `ZH`) |
| `cosyvoice3` | [Fun-CosyVoice3](https://github.com/FunAudioLLM/CosyVoice) 0.5B via `AutoModel` | ~5 GB | Dialects and `[breath]` tags; the system prefix is added for you |
| `piper` | Piper `.onnx` voice | CPU | No GPU; for testing the service end to end |

The model adapters target the upstream inference APIs as of October 2026
(`indextts.infer_v2.IndexTTS2.infer`, `cosyvoice.cli.cosyvoice.CosyVoice2`).
If upstream changes them, only `backends.py` needs updating.

## Install

Each model has heavy, conflicting dependencies, so give each its own virtual
environment and add the service's two packages to it.

### IndexTTS2

```bash
git clone https://github.com/index-tts/index-tts ~/index-tts && cd ~/index-tts
uv sync --all-extras            # follow the upstream README if this changes
uv pip install fastapi uvicorn modelscope
# Weights (ModelScope mirrors HuggingFace and is reachable from mainland China):
uv run modelscope download --model IndexTeam/IndexTTS-2 --local_dir checkpoints
```

### CosyVoice 2

```bash
git clone --recursive https://github.com/FunAudioLLM/CosyVoice ~/CosyVoice && cd ~/CosyVoice
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt fastapi uvicorn "setuptools<81"   # CosyVoice still imports pkg_resources
python -c "from modelscope import snapshot_download; snapshot_download('iic/CosyVoice2-0.5B', local_dir='pretrained_models/CosyVoice2-0.5B')"
```

## Voices

Put reference clips in a folder (`LOCAL_TTS_VOICES_DIR`):

```
voices/
  narrator.wav     # 5–15 s of clean speech, one speaker
  narrator.txt     # optional: exactly what narrator.wav says (improves CosyVoice)
```

The file name is the voice name. Set `LOCAL_TTS_DEFAULT_VOICE=narrator` so
requests without a `voice` use it. Only clone voices you have the right to use.

## Run

Run from the OpenMontage root with the model's environment active and the
model repo on `PYTHONPATH`:

```bash
cd ~/OpenMontage
PYTHONPATH=~/index-tts \
LOCAL_TTS_BACKEND=indextts2 \
LOCAL_TTS_MODEL_DIR=~/index-tts/checkpoints \
LOCAL_TTS_VOICES_DIR=~/voices \
LOCAL_TTS_DEFAULT_VOICE=narrator \
~/index-tts/.venv/bin/python -m services.local_tts.server
```

| Variable | Default | Meaning |
|---|---|---|
| `LOCAL_TTS_BACKEND` | `indextts2` | `indextts2`, `indextts25`, `cosyvoice`, `cosyvoice3` or `piper` |
| `LOCAL_TTS_LANG` | `ZH` | Language passed to IndexTTS2.5 |
| `LOCAL_TTS_MODEL_DIR` | backend default | Model weights folder |
| `LOCAL_TTS_VOICES_DIR` | `./voices` | Reference clips |
| `LOCAL_TTS_DEFAULT_VOICE` | none | Voice used when a request names none |
| `LOCAL_TTS_PIPER_MODEL` | none | `.onnx` file for the `piper` backend |
| `LOCAL_TTS_HOST` / `LOCAL_TTS_PORT` | `127.0.0.1` / `9880` | Listen address |

Then set `LOCAL_TTS_URL=http://127.0.0.1:9880` in OpenMontage's `.env`.

To start it at boot under WSL2 (with `systemd=true` in `/etc/wsl.conf`), save
the command above as `~/.config/systemd/user/local-tts.service`:

```ini
[Unit]
Description=OpenMontage local TTS

[Service]
WorkingDirectory=%h/OpenMontage
Environment=PYTHONPATH=%h/index-tts
Environment=LOCAL_TTS_BACKEND=indextts2
Environment=LOCAL_TTS_MODEL_DIR=%h/index-tts/checkpoints
Environment=LOCAL_TTS_VOICES_DIR=%h/voices
Environment=LOCAL_TTS_DEFAULT_VOICE=narrator
ExecStart=%h/index-tts/.venv/bin/python -m services.local_tts.server
Restart=on-failure

[Install]
WantedBy=default.target
```

and run `systemctl --user enable --now local-tts`.

## API

```
GET  /health  -> {"status", "backend", "model", "loaded", "error", "voices", ...}
GET  /voices  -> {"voices": [{"name", "has_transcript"}]}
POST /tts     {"text": "...", "voice": "narrator", "speed": 1.0} -> audio/wav
```

Requests are processed one at a time because the model holds the GPU.
`text` is limited to 2000 characters; split long scripts by section.
