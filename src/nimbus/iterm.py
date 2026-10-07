"""Capa pelo protocolo de imagens do iTerm2 (o mesmo do imgcat).

O WezTerm e o iTerm2 desenham a imagem em resolução real por esse protocolo,
que o textual-image não oferece. Reaproveita o widget Sixel dele, que já sabe
encaixar uma sequência de escape no lugar certo da tela, e troca só os dados:
em vez de Sixel, manda o PNG em base64 dentro de um OSC 1337.

Dentro do tmux a sequência vai embrulhada no "passthrough" dele (precisa de
`set -g allow-passthrough on`). O tmux entrega o passthrough ao terminal de
fora sem mexer no cursor, então a própria sequência leva o cursor à posição
absoluta da capa: a do painel do tmux somada à da capa dentro do painel.
"""

from __future__ import annotations

import base64
import io
import os
import subprocess
from typing import Iterable

from rich.segment import ControlType, Segment
from rich.style import Style
from textual.app import ComposeResult
from textual_image.widget.sixel import Image as _SixelImage
from textual_image.widget.sixel import _ImageSixelImpl, _NoopRenderable

_NULL_STYLE = Style()


def iterm_inline(image) -> str:
    """Sequência OSC 1337 com a imagem (PIL) no tamanho em pixels que ela já tem."""
    buf = io.BytesIO()
    image.save(buf, "PNG")
    data = buf.getvalue()
    args = f"inline=1;size={len(data)};width={image.width}px;height={image.height}px;preserveAspectRatio=0"
    return f"\x1b]1337;File={args}:{base64.b64encode(data).decode('ascii')}\x07"


def in_tmux() -> bool:
    return bool(os.environ.get("TMUX"))


def tmux_pane_offset() -> tuple[int, int]:
    """(coluna, linha) do canto do painel atual na janela do tmux."""
    try:
        out = subprocess.run(
            ["tmux", "display-message", "-p", "-t", os.environ.get("TMUX_PANE", ""), "#{pane_left} #{pane_top}"],
            capture_output=True, text=True, timeout=1,
        ).stdout.split()
        return int(out[0]), int(out[1])
    except (OSError, ValueError, IndexError, subprocess.SubprocessError):
        return 0, 0


def tmux_passthrough(seq: str, x: int, y: int) -> str:
    """Leva seq ao terminal de fora, desenhando na coluna x, linha y (base 0) da tela inteira."""
    inner = f"\x1b7\x1b[{y + 1};{x + 1}H{seq}\x1b8"
    return "\x1bPtmux;" + inner.replace("\x1b", "\x1b\x1b") + "\x1b\\"


class _ITermImpl(_ImageSixelImpl):
    def _image_to_sixels(self, image, sixel_options=None, background=None) -> str:
        return iterm_inline(image.convert("RGB"))

    def _get_sixel_segments(self, sixel_data: str) -> Iterable[Segment]:
        if not in_tmux():
            return super()._get_sixel_segments(sixel_data)
        region = self.screen.find_widget(self).visible_region
        left, top = tmux_pane_offset()
        data = tmux_passthrough(sixel_data, left + region.x, top + region.y)
        return [Segment(data, style=_NULL_STYLE, control=((ControlType.CURSOR_FORWARD, 0),))]


class ITermImage(_SixelImage, Renderable=_NoopRenderable):
    def compose(self) -> ComposeResult:
        yield _ITermImpl(self.image, self._sixel_options)
