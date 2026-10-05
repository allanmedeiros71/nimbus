"""Fila de reprodução e a ligação entre Drive, token e mpv.

Fica separado da CLI para que a interface TUI use a mesma lógica.
"""

from __future__ import annotations

import random
from typing import Callable, Sequence

from nimbus.drive import DriveItem, media_url
from nimbus.player import MpvPlayer


class PlayQueue:
    def __init__(self, tracks: Sequence[DriveItem], shuffle: bool = False, seed: int | None = None):
        self.tracks = list(tracks)
        if shuffle:
            random.Random(seed).shuffle(self.tracks)
        self.index = 0

    def __len__(self) -> int:
        return len(self.tracks)

    @property
    def current(self) -> DriveItem | None:
        return self.tracks[self.index] if 0 <= self.index < len(self.tracks) else None

    def advance(self) -> bool:
        if self.index + 1 < len(self.tracks):
            self.index += 1
            return True
        return False

    def back(self) -> bool:
        if self.index > 0:
            self.index -= 1
            return True
        return False


class Controller:
    """Toca a fila: pede um token fresco a cada faixa e avança no fim de cada uma."""

    def __init__(self, player: MpvPlayer, queue: PlayQueue, token_provider: Callable[[], str]):
        self.player = player
        self.queue = queue
        self._token = token_provider
        self.finished = False
        self.last_error: str | None = None

    def play_current(self) -> DriveItem | None:
        item = self.queue.current
        if item is None:
            self.finished = True
            return None
        self.player.play(media_url(item.id), {"Authorization": f"Bearer {self._token()}"})
        return item

    def next(self) -> bool:
        if self.queue.advance():
            self.play_current()
            return True
        self.finished = True
        self.player.stop()
        return False

    def prev(self) -> None:
        self.queue.back()
        self.play_current()

    def handle_event(self, event: dict) -> bool:
        """Trata um evento do mpv. Devolve True se a faixa mudou."""
        name = event.get("event")
        if name == "nimbus-mpv-exited":
            self.finished = True
            return False
        if name != "end-file":
            return False
        reason = event.get("reason")
        if reason == "error":
            item = self.queue.current
            self.last_error = f"{item.name if item else '?'}: {event.get('file_error', 'erro')}"
        elif reason != "eof":
            return False  # "stop" vem de loadfile replace ou de stop(): não avança
        return self.next()
