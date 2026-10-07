"""Músicas do próprio computador: pasta pessoal, disco do sistema, HD externo e pendrive.

Os itens locais usam a mesma DriveItem do Drive, com o ID "local:<caminho
absoluto>", para que a fila, a árvore da interface e os metadados tratem as duas
origens do mesmo jeito. "local:" sozinho é a raiz virtual "Este computador".
"""

from __future__ import annotations

import mimetypes
import os
import re
import sys
from pathlib import Path
from typing import Iterator

from nimbus.drive import FOLDER_MIME, DriveError, DriveItem

LOCAL_PREFIX = "local:"
LOCAL_ROOT = LOCAL_PREFIX
LOCAL_NAME = "Este computador"
LOCAL_ALIASES = {"este computador", "this computer", "@local"}

_NATURAL = re.compile(r"(\d+)")


def is_local_id(item_id: str) -> bool:
    return item_id.startswith(LOCAL_PREFIX)


def is_local(item: DriveItem) -> bool:
    return is_local_id(item.id)


def local_path(item_or_id) -> str:
    item_id = item_or_id if isinstance(item_or_id, str) else item_or_id.id
    return item_id[len(LOCAL_PREFIX):]


def local_root() -> DriveItem:
    return DriveItem(id=LOCAL_ROOT, name=LOCAL_NAME, mime_type=FOLDER_MIME)


def looks_local(ref: str) -> bool:
    """Caminhos que o usuário digitou e que só podem ser locais: /…, ~…, ./…, ../…, file://…"""
    ref = ref.strip()
    return ref.startswith(("/", "~", "./", "../", "file://")) or ref in (".", "..")


def local_item(path: str | os.PathLike, name: str | None = None) -> DriveItem:
    path = os.path.abspath(os.path.expanduser(os.fspath(path)))
    name = name or os.path.basename(path.rstrip(os.sep)) or path
    if os.path.isdir(path):
        return DriveItem(id=LOCAL_PREFIX + path, name=name, mime_type=FOLDER_MIME)
    try:
        size = os.path.getsize(path)
    except OSError:
        size = None
    return DriveItem(id=LOCAL_PREFIX + path, name=name, mime_type=_guess_mime(name), size=size)


def _guess_mime(name: str) -> str:
    mime = mimetypes.guess_type(name)[0] or ""
    # Listas de reprodução (.m3u, .pls) têm MIME audio/* mas não são músicas.
    return "" if "mpegurl" in mime or "scpls" in mime else mime


def _natural_key(name: str) -> list:
    return [int(p) if p.isdigit() else p.casefold() for p in _NATURAL.split(name)]


def _readable_dir(path: str) -> bool:
    return os.path.isdir(path) and os.access(path, os.R_OK | os.X_OK)


def mounted_volumes(platform: str | None = None, bases: list[str] | None = None) -> list[DriveItem]:
    """HDs externos, pendrives e outros discos montados.

    macOS: /Volumes (sem o disco do sistema, que lá é um atalho para /).
    Linux: /media/<usuário>/<disco> (Ubuntu, Debian), /run/media/<usuário>/<disco>
    (Fedora, Arch), /media/<disco> e /mnt/<disco>.
    """
    platform = platform or sys.platform
    if bases is None:
        bases = ["/Volumes"] if platform == "darwin" else ["/media", "/run/media", "/mnt"]
    found: list[DriveItem] = []
    seen: set[str] = set()

    def add(path: str) -> None:
        real = os.path.realpath(path)
        if real == "/" or real in seen or not _readable_dir(path):
            return
        seen.add(real)
        found.append(local_item(path, os.path.basename(path)))

    for base in bases:
        try:
            entries = sorted(os.scandir(base), key=lambda e: _natural_key(e.name))
        except OSError:
            continue
        for entry in entries:
            if entry.name.startswith(".") or entry.name.startswith("com.apple."):
                continue
            path = entry.path
            if platform == "darwin" or os.path.ismount(path):
                add(path)
            elif base != "/mnt" and _readable_dir(path):
                # /media/<usuário>: os discos ficam um nível abaixo.
                try:
                    subs = sorted(os.scandir(path), key=lambda e: _natural_key(e.name))
                except OSError:
                    continue
                for sub in subs:
                    if not sub.name.startswith(".") and sub.is_dir():
                        add(sub.path)
    return found


def local_places(platform: str | None = None) -> list[DriveItem]:
    """O que aparece dentro de "Este computador": pasta pessoal, discos externos e /."""
    home = str(Path.home())
    places = [local_item(home, f"Pasta pessoal ({os.path.basename(home) or home})")]
    places += mounted_volumes(platform)
    places.append(local_item("/", "Disco do sistema (/)"))
    return places


def iter_local_children(item_id: str, show_hidden: bool = False) -> Iterator[DriveItem]:
    """Pastas primeiro, depois arquivos, em ordem natural de nome."""
    if item_id == LOCAL_ROOT:
        yield from local_places()
        return
    path = local_path(item_id)
    try:
        entries = list(os.scandir(path))
    except OSError as e:
        raise DriveError(f"Não consegui abrir {path}: {e.strerror or e}") from e
    folders, files = [], []
    for entry in entries:
        if not show_hidden and entry.name.startswith("."):
            continue
        try:
            is_dir = entry.is_dir()  # segue links simbólicos
            size = None if is_dir else entry.stat().st_size
        except OSError:
            continue  # link quebrado ou sem permissão
        if is_dir:
            folders.append(DriveItem(id=LOCAL_PREFIX + entry.path, name=entry.name, mime_type=FOLDER_MIME))
        else:
            files.append(DriveItem(id=LOCAL_PREFIX + entry.path, name=entry.name,
                                   mime_type=_guess_mime(entry.name), size=size))
    folders.sort(key=lambda i: _natural_key(i.name))
    files.sort(key=lambda i: _natural_key(i.name))
    yield from folders
    yield from files


def resolve_local(ref: str) -> DriveItem:
    """Aceita /caminho, ~/caminho, ./caminho, file:///caminho ou 'Este computador'."""
    ref = ref.strip()
    if ref.casefold().rstrip("/") in LOCAL_ALIASES:
        return local_root()
    if ref.startswith("file://"):
        from urllib.parse import unquote, urlparse

        ref = unquote(urlparse(ref).path)
    path = os.path.abspath(os.path.expanduser(ref))
    if not os.path.exists(path):
        raise DriveError(f"Pasta não encontrada: {path}")
    if not os.path.isdir(path):
        raise DriveError(f"'{path}' não é uma pasta")
    return local_item(path, os.path.basename(path.rstrip(os.sep)) or path)


def local_fetcher(path: str):
    """Lê um trecho do arquivo, como o Range do Drive, para achar a capa embutida."""

    def fetch(start: int, end: int) -> bytes:
        with open(path, "rb") as f:
            f.seek(start)
            return f.read(end - start + 1)

    return fetch
