"""Controle do mpv pelo socket JSON IPC.

O mpv roda em segundo plano, ocioso, e recebe cada faixa como URL HTTP. Ele
lê o arquivo por partes (requisições Range), então a música começa a tocar sem
baixar o arquivo inteiro. O token de acesso vai pelo socket, nunca pela linha
de comando, para não aparecer em `ps`.
"""

from __future__ import annotations

import itertools
import json
import os
import queue
import shutil
import socket
import subprocess
import tempfile
import threading
import time
from typing import Any, Iterable

DEFAULT_ARGS = (
    "--idle=yes",
    "--no-video",
    "--no-terminal",
    "--ytdl=no",  # nunca repassar URLs do Drive para yt-dlp
    # Lê só uns 2 minutos à frente: começa rápido e não baixa o arquivo todo.
    "--cache=yes",
    "--cache-secs=120",
    "--demuxer-max-bytes=32MiB",
    "--demuxer-max-back-bytes=16MiB",
)


class MpvError(Exception):
    pass


class MpvNotFound(MpvError):
    pass


class MpvPlayer:
    def __init__(self, mpv_path: str | None = None, extra_args: Iterable[str] = ()):
        self._mpv = mpv_path or os.environ.get("NIMBUS_MPV") or shutil.which("mpv")
        if not self._mpv:
            raise MpvNotFound(
                "mpv não encontrado. Instale com 'brew install mpv' (macOS) "
                "ou 'sudo apt install mpv' (Debian/Ubuntu)."
            )
        self._extra_args = tuple(extra_args)
        self._proc: subprocess.Popen | None = None
        self._sock: socket.socket | None = None
        self._tmpdir: str | None = None
        self._reader: threading.Thread | None = None
        self._ids = itertools.count(1)
        self._pending: dict[int, queue.Queue] = {}
        self._lock = threading.Lock()
        self._send_lock = threading.Lock()
        self.events: queue.Queue[dict] = queue.Queue()

    # ciclo de vida

    def start(self, timeout: float = 5.0) -> None:
        self._tmpdir = tempfile.mkdtemp(prefix="nimbus-")
        path = os.path.join(self._tmpdir, "mpv.sock")
        self._proc = subprocess.Popen(
            [self._mpv, *DEFAULT_ARGS, f"--input-ipc-server={path}", *self._extra_args],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        deadline = time.monotonic() + timeout
        while True:
            if self._proc.poll() is not None:
                raise MpvError(f"mpv encerrou ao iniciar (código {self._proc.returncode})")
            try:
                s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                s.connect(path)
                break
            except OSError:
                s.close()
                if time.monotonic() > deadline:
                    self.close()
                    raise MpvError("mpv não abriu o socket de controle a tempo")
                time.sleep(0.05)
        self._sock = s
        self._reader = threading.Thread(target=self._read_loop, name="mpv-ipc", daemon=True)
        self._reader.start()

    def close(self) -> None:
        if self._sock is not None:
            try:
                self._send({"command": ["quit"]})
            except OSError:
                pass
        if self._proc is not None:
            try:
                self._proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self._proc.kill()
                self._proc.wait()
        if self._sock is not None:
            self._sock.close()
            self._sock = None
        if self._tmpdir:
            shutil.rmtree(self._tmpdir, ignore_errors=True)
            self._tmpdir = None

    def __enter__(self) -> "MpvPlayer":
        self.start()
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    @property
    def running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    # protocolo

    def _send(self, msg: dict) -> None:
        if self._sock is None:
            raise MpvError("mpv não está rodando")
        data = (json.dumps(msg) + "\n").encode()
        with self._send_lock:
            self._sock.sendall(data)

    def _read_loop(self) -> None:
        buf = b""
        sock = self._sock
        while True:
            try:
                chunk = sock.recv(65536)
            except OSError:
                chunk = b""
            if not chunk:
                break
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                if not line.strip():
                    continue
                try:
                    msg = json.loads(line)
                except ValueError:
                    continue
                if "event" in msg:
                    self.events.put(msg)
                elif "request_id" in msg:
                    with self._lock:
                        waiter = self._pending.pop(msg["request_id"], None)
                    if waiter is not None:
                        waiter.put(msg)
        self.events.put({"event": "nimbus-mpv-exited"})
        with self._lock:
            waiters, self._pending = list(self._pending.values()), {}
        for w in waiters:
            w.put({"error": "mpv exited"})

    def command(self, *args: Any, timeout: float = 5.0) -> Any:
        req_id = next(self._ids)
        waiter: queue.Queue = queue.Queue(maxsize=1)
        with self._lock:
            self._pending[req_id] = waiter
        self._send({"command": list(args), "request_id": req_id})
        try:
            resp = waiter.get(timeout=timeout)
        except queue.Empty:
            with self._lock:
                self._pending.pop(req_id, None)
            raise MpvError(f"mpv não respondeu a {args[0]!r}")
        if resp.get("error") != "success":
            raise MpvError(f"{args[0]}: {resp.get('error')}")
        return resp.get("data")

    def get_property(self, name: str, default: Any = None) -> Any:
        try:
            return self.command("get_property", name)
        except MpvError:
            return default  # ex.: time-pos sem faixa carregada

    def set_property(self, name: str, value: Any) -> None:
        self.command("set_property", name, value)

    # reprodução

    def play(self, url: str, headers: dict[str, str] | None = None) -> None:
        fields = [f"{k}: {v}" for k, v in (headers or {}).items()]
        self.set_property("http-header-fields", fields)
        self.command("loadfile", url, "replace")

    def toggle_pause(self) -> bool:
        self.command("cycle", "pause")
        return bool(self.get_property("pause", False))

    def stop(self) -> None:
        self.command("stop")

    def seek(self, seconds: float, mode: str = "relative") -> None:
        try:
            self.command("seek", seconds, mode)
        except MpvError:
            pass  # sem faixa tocando

    def add_volume(self, delta: float) -> float:
        self.command("add", "volume", delta)
        return float(self.get_property("volume", 0.0))

    def position(self) -> tuple[float | None, float | None]:
        return self.get_property("time-pos"), self.get_property("duration")
