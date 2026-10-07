"""Fila de reprodução e a ligação entre Drive, token e mpv.

Fica separado da CLI para que a interface TUI use a mesma lógica.
"""

from __future__ import annotations

import random
from typing import Callable, Sequence

from nimbus.drive import DriveItem, media_url
from nimbus.local import is_local, local_path
from nimbus.player import MpvPlayer


REPEAT_MODES = ("off", "all", "one")


class PlayQueue:
    def __init__(self, tracks: Sequence[DriveItem], shuffle: bool = False, seed: int | None = None,
                 start: int = 0):
        self._original = list(tracks)
        self.tracks = list(tracks)
        self.index = start if 0 <= start < len(self.tracks) else 0
        self.repeat = "off"
        self.shuffle = False
        self._rng = random.Random(seed)
        if shuffle:
            self.set_shuffle(True, keep_current=start != 0)

    def __len__(self) -> int:
        return len(self.tracks)

    @property
    def current(self) -> DriveItem | None:
        return self.tracks[self.index] if 0 <= self.index < len(self.tracks) else None

    def advance(self) -> bool:
        if self.index + 1 < len(self.tracks):
            self.index += 1
            return True
        if self.repeat == "all" and self.tracks:
            self.index = 0
            return True
        return False

    def back(self) -> bool:
        if self.index > 0:
            self.index -= 1
            return True
        if self.repeat == "all" and self.tracks:
            self.index = len(self.tracks) - 1
            return True
        return False

    def cycle_repeat(self) -> str:
        self.repeat = REPEAT_MODES[(REPEAT_MODES.index(self.repeat) + 1) % len(REPEAT_MODES)]
        return self.repeat

    def set_shuffle(self, on: bool, keep_current: bool = True) -> None:
        """Embaralha ou volta à ordem original sem trocar a faixa atual.

        Com keep_current, a faixa atual vira a primeira e só o resto é embaralhado.
        """
        current = self.current
        if on:
            rest = list(self._original)
            if keep_current and current is not None:
                rest.remove(current)
                self._rng.shuffle(rest)
                self.tracks = [current, *rest]
            else:
                self._rng.shuffle(rest)
                self.tracks = rest
            self.index = 0
        else:
            self.tracks = list(self._original)
            self.index = self.tracks.index(current) if current in self.tracks else 0
        self.shuffle = on


class Controller:
    """Toca a fila: pede um token fresco a cada faixa do Drive e avança no fim de cada uma."""

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
        if is_local(item):
            self.player.play(local_path(item))  # arquivo do disco: o mpv lê direto
        else:
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
        elif self.queue.repeat == "one":
            self.play_current()
            return True
        return self.next()
