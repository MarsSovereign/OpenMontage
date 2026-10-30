"""Start local tools on demand and stop the ones we started once they sit idle.

Mars's PC runs nothing in the background: ComfyUI and the TTS service are
started only for a job. A service that was already running when we looked
(for example Mars's own ComfyUI) is used but never stopped by us.
"""

from __future__ import annotations

import logging
import os
import subprocess
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

import requests

log = logging.getLogger("canvas_gateway.services")


@dataclass
class ServiceSpec:
    name: str
    url: str
    health: str = "/"
    start: list[str] | None = None
    cwd: str | None = None
    env: dict[str, str] = field(default_factory=dict)
    start_timeout: int = 300
    idle_stop_minutes: float = 10

    @classmethod
    def from_config(cls, name: str, raw: dict[str, Any]) -> "ServiceSpec":
        return cls(
            name=name,
            url=str(raw["url"]).rstrip("/"),
            health=str(raw.get("health", "/")),
            start=list(raw["start"]) if raw.get("start") else None,
            cwd=raw.get("cwd"),
            env={str(k): str(v) for k, v in (raw.get("env") or {}).items()},
            start_timeout=int(raw.get("start_timeout", 300)),
            idle_stop_minutes=float(raw.get("idle_stop_minutes", 10)),
        )


def _default_probe(url: str) -> bool:
    try:
        return requests.get(url, timeout=3).status_code < 500
    except requests.RequestException:
        return False


def child_flags() -> int:
    """Windows: give every child its own hidden console.

    A child that inherits the gateway's console dies at start with 0xC0000142
    once the window that launched the gateway is closed. That killed ComfyUI
    and the ffmpeg it runs for VAE decode.
    """
    if os.name != "nt":
        return 0
    return subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW


def _kill_tree(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True)
    else:
        proc.terminate()
    try:
        proc.wait(timeout=30)
    except subprocess.TimeoutExpired:
        proc.kill()


class ServiceManager:
    def __init__(
        self,
        specs: dict[str, ServiceSpec],
        *,
        probe: Callable[[str], bool] = _default_probe,
        popen: Callable[..., subprocess.Popen] = subprocess.Popen,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.specs = specs
        self._probe = probe
        self._popen = popen
        self._clock = clock
        self._owned: dict[str, subprocess.Popen] = {}
        self._last_used: dict[str, float] = {}
        self._lock = threading.Lock()

    def is_up(self, name: str) -> bool:
        spec = self.specs[name]
        return self._probe(spec.url + spec.health)

    def ensure(self, name: str) -> None:
        """Make *name* reachable, starting it if it is down and we know how."""
        spec = self.specs[name]
        with self._lock:
            self._last_used[name] = self._clock()
            if self.is_up(name):
                return
            if not spec.start:
                raise RuntimeError(f"{name} 没有运行（{spec.url}），配置里也没有启动命令。")
            log.info("starting %s: %s", name, spec.start)
            env = {**os.environ, **spec.env}
            self._owned[name] = self._popen(spec.start, cwd=spec.cwd, env=env, creationflags=child_flags())
            deadline = self._clock() + spec.start_timeout
            while self._clock() < deadline:
                if self._owned[name].poll() is not None:
                    raise RuntimeError(f"{name} 启动后退出了（返回码 {self._owned[name].returncode}）。")
                if self.is_up(name):
                    return
                time.sleep(2)
            raise RuntimeError(f"{name} 在 {spec.start_timeout} 秒内没有启动好。")

    def touch(self, name: str) -> None:
        self._last_used[name] = self._clock()

    def stop_owned(self, keep: set[str] = frozenset()) -> list[str]:
        """Stop services we started, except *keep*. Returns the names stopped."""
        stopped = []
        with self._lock:
            for name, proc in list(self._owned.items()):
                if name in keep:
                    continue
                _kill_tree(proc)
                del self._owned[name]
                stopped.append(name)
        for name in stopped:
            log.info("stopped %s", name)
        return stopped

    def stop_idle(self, busy: bool) -> list[str]:
        """Stop owned services idle past their limit. Nothing stops while a job runs."""
        if busy:
            return []
        now = self._clock()
        keep = {
            name
            for name in self._owned
            if now - self._last_used.get(name, now) < self.specs[name].idle_stop_minutes * 60
        }
        return self.stop_owned(keep)

    def owned(self) -> list[str]:
        return sorted(self._owned)
