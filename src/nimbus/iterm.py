"""Capa pelo protocolo de imagens do iTerm2 (o mesmo do imgcat).

O WezTerm e o iTerm2 desenham a imagem em resolução real por esse protocolo,
que o textual-image não oferece. Reaproveita o widget Sixel dele, que já sabe
encaixar uma sequência de escape no lugar certo da tela, e troca só os dados:
em vez de Sixel, manda o PNG em base64 dentro de um OSC 1337.
"""

from __future__ import annotations

import base64
import io

from textual.app import ComposeResult
from textual_image.widget.sixel import Image as _SixelImage
from textual_image.widget.sixel import _ImageSixelImpl, _NoopRenderable


def iterm_inline(image) -> str:
    """Sequência OSC 1337 com a imagem (PIL) no tamanho em pixels que ela já tem."""
    buf = io.BytesIO()
    image.save(buf, "PNG")
    data = buf.getvalue()
    args = f"inline=1;size={len(data)};width={image.width}px;height={image.height}px;preserveAspectRatio=0"
    return f"\x1b]1337;File={args}:{base64.b64encode(data).decode('ascii')}\x07"


class _ITermImpl(_ImageSixelImpl):
    def _image_to_sixels(self, image, sixel_options=None, background=None) -> str:
        return iterm_inline(image.convert("RGB"))


class ITermImage(_SixelImage, Renderable=_NoopRenderable):
    def compose(self) -> ComposeResult:
        yield _ITermImpl(self.image, self._sixel_options)
