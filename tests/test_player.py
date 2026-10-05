"""Testes de ponta a ponta com um mpv real e um servidor HTTP local que imita o Drive."""

import io
import os
import shutil
import struct
import threading
import time
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from nimbus import playback
from nimbus.drive import DriveItem
from nimbus.player import MpvPlayer
from nimbus.playback import Controller, PlayQueue

pytestmark = pytest.mark.skipif(shutil.which("mpv") is None, reason="mpv não instalado")

TOKEN = "token-de-teste"


def make_wav(seconds, rate=44100):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(struct.pack("<hh", 0, 0) * int(rate * seconds))
    return buf.getvalue()


class FakeDrive:
    """Serve /files/<id> exigindo o token e respeitando Range, como a API do Drive."""

    def __init__(self, files):
        self.files = files
        self.requests = []  # (id, authorization, range)
        self.bytes_sent = {}
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                file_id = self.path.split("/files/")[1].split("?")[0]
                auth = self.headers.get("Authorization")
                rng = self.headers.get("Range")
                outer.requests.append((file_id, auth, rng))
                if auth != f"Bearer {TOKEN}":
                    self.send_error(401)
                    return
                data = outer.files.get(file_id)
                if data is None:
                    self.send_error(404)
                    return
                start, end = 0, len(data) - 1
                if rng and rng.startswith("bytes="):
                    a, _, b = rng[6:].partition("-")
                    start = int(a or 0)
                    end = int(b) if b else end
                    self.send_response(206)
                    self.send_header("Content-Range", f"bytes {start}-{end}/{len(data)}")
                else:
                    self.send_response(200)
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Content-Type", "audio/wav")
                self.send_header("Content-Length", str(end - start + 1))
                self.end_headers()
                try:
                    pos = start
                    while pos <= end:
                        chunk = data[pos:min(pos + 65536, end + 1)]
                        self.wfile.write(chunk)
                        pos += len(chunk)
                        outer.bytes_sent[file_id] = outer.bytes_sent.get(file_id, 0) + len(chunk)
                except (BrokenPipeError, ConnectionResetError):
                    pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def url(self, file_id):
        return f"http://127.0.0.1:{self.server.server_port}/files/{file_id}?alt=media"

    def close(self):
        self.server.shutdown()


@pytest.fixture
def fake_drive(monkeypatch):
    srv = FakeDrive({"curta1": make_wav(0.5), "curta2": make_wav(0.5), "longa": make_wav(300)})
    monkeypatch.setattr(playback, "media_url", srv.url)
    yield srv
    srv.close()


@pytest.fixture
def player():
    with MpvPlayer(extra_args=["--ao=null"]) as p:
        yield p


def wait_for(cond, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cond():
            return True
        time.sleep(0.05)
    return False


def drain(ctl, timeout=10.0):
    deadline = time.monotonic() + timeout
    while not ctl.finished and time.monotonic() < deadline:
        try:
            ctl.handle_event(ctl.player.events.get(timeout=0.2))
        except Exception:
            pass


def test_queue_plays_through_with_token(fake_drive, player):
    q = PlayQueue([DriveItem("curta1", "1.wav", "audio/wav"), DriveItem("curta2", "2.wav", "audio/wav")])
    ctl = Controller(player, q, lambda: TOKEN)
    ctl.play_current()
    drain(ctl)
    assert ctl.finished and ctl.last_error is None
    assert [r[0] for r in fake_drive.requests if r[0].startswith("curta")][:1] == ["curta1"]
    assert {r[0] for r in fake_drive.requests} == {"curta1", "curta2"}
    assert all(r[1] == f"Bearer {TOKEN}" for r in fake_drive.requests)


def test_streams_without_downloading_whole_file(fake_drive, player):
    q = PlayQueue([DriveItem("longa", "longa.wav", "audio/wav")])
    ctl = Controller(player, q, lambda: TOKEN)
    ctl.play_current()
    assert wait_for(lambda: (player.get_property("time-pos") or 0) > 0.3)
    assert player.get_property("duration") == pytest.approx(300, abs=1)

    # Pular para perto do fim gera uma requisição Range em vez de ler tudo até lá.
    player.seek(280, "absolute")
    assert wait_for(lambda: (player.get_property("time-pos") or 0) > 280)
    size = len(fake_drive.files["longa"])
    assert any(r[2] and not r[2].startswith("bytes=0-") for r in fake_drive.requests)
    assert fake_drive.bytes_sent["longa"] < size * 0.6


def test_bad_token_reports_error_and_skips(fake_drive, player):
    q = PlayQueue([DriveItem("curta1", "1.wav", "audio/wav")])
    ctl = Controller(player, q, lambda: "token-errado")
    ctl.play_current()
    deadline = time.monotonic() + 10
    while not ctl.finished and time.monotonic() < deadline:
        try:
            ev = player.events.get(timeout=0.2)
        except Exception:
            continue
        ctl.handle_event(ev)
        if ctl.last_error:
            break
    assert ctl.last_error and ctl.last_error.startswith("1.wav")


@pytest.mark.skipif(not os.path.exists("/proc"), reason="precisa de /proc")
def test_token_not_on_command_line(fake_drive, player):
    q = PlayQueue([DriveItem("longa", "longa.wav", "audio/wav")])
    Controller(player, q, lambda: TOKEN).play_current()
    with open(f"/proc/{player._proc.pid}/cmdline", "rb") as f:
        assert TOKEN.encode() not in f.read()
