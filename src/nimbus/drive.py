"""Leitura de pastas e arquivos de áudio no Google Drive (somente leitura)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, Iterable, Iterator

FOLDER_MIME = "application/vnd.google-apps.folder"
SHORTCUT_MIME = "application/vnd.google-apps.shortcut"

# O Drive nem sempre identifica áudio pelo MIME (FLAC costuma vir como
# application/octet-stream), então a extensão também conta.
AUDIO_EXTENSIONS = {
    ".mp3", ".flac", ".ogg", ".oga", ".opus", ".m4a", ".m4b", ".aac",
    ".wav", ".aif", ".aiff", ".alac", ".wma", ".ape", ".wv", ".mka",
}

# Pasta virtual com o que outras pessoas compartilharam com você. A API não
# tem um ID para ela: os itens vêm da consulta "sharedWithMe = true".
SHARED_WITH_ME = "sharedWithMe"
SHARED_NAME = "Compartilhados comigo"
SHARED_ALIASES = {"compartilhados comigo", "shared with me", "@compartilhados", "@shared"}

_FIELDS = "nextPageToken, files(id, name, mimeType, size, shortcutDetails)"
_ID_RE = re.compile(r"^[A-Za-z0-9_-]{15,}$")
_URL_ID_RE = re.compile(r"(?:/folders/|/file/d/|[?&]id=)([A-Za-z0-9_-]{15,})")


class DriveError(Exception):
    pass


@dataclass(frozen=True)
class DriveItem:
    id: str
    name: str
    mime_type: str
    size: int | None = None

    @property
    def is_folder(self) -> bool:
        return self.mime_type == FOLDER_MIME

    @property
    def is_audio(self) -> bool:
        if self.mime_type.startswith("application/vnd.google-apps."):
            return False
        if self.mime_type.startswith("audio/"):
            return True
        dot = self.name.rfind(".")
        return dot != -1 and self.name[dot:].lower() in AUDIO_EXTENSIONS

    @classmethod
    def from_api(cls, f: dict) -> "DriveItem":
        # Atalhos apontam para outro arquivo ou pasta: usamos o alvo.
        if f.get("mimeType") == SHORTCUT_MIME and f.get("shortcutDetails"):
            d = f["shortcutDetails"]
            return cls(id=d["targetId"], name=f["name"], mime_type=d.get("targetMimeType", ""))
        size = f.get("size")
        return cls(
            id=f["id"],
            name=f["name"],
            mime_type=f.get("mimeType", ""),
            size=int(size) if size is not None else None,
        )


def media_url(file_id: str) -> str:
    """URL que devolve o conteúdo do arquivo; aceita Range, então dá para fazer streaming."""
    return f"https://www.googleapis.com/drive/v3/files/{file_id}?alt=media&supportsAllDrives=true"


def _quote(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def parse_folder_ref(ref: str) -> str | None:
    """Extrai o ID de uma URL do Drive, ou devolve None se não for uma URL."""
    m = _URL_ID_RE.search(ref)
    return m.group(1) if m else None


def shared_with_me() -> DriveItem:
    return DriveItem(id=SHARED_WITH_ME, name=SHARED_NAME, mime_type=FOLDER_MIME)


def collect_audio(iter_children: Callable[[str], Iterable[DriveItem]], folder_id: str,
                  recursive: bool = False, key: Callable[[str], str] = lambda i: i) -> list[DriveItem]:
    """Áudio de uma pasta e, com recursive, das subpastas (profundidade primeiro, na ordem).

    key identifica uma pasta já visitada; no disco local é o caminho real, para
    que um link simbólico que aponta para cima não vire um laço infinito.
    """
    tracks: list[DriveItem] = []
    seen = {key(folder_id)}
    pending = [folder_id]
    while pending:
        current = pending.pop(0)
        subfolders = []
        for item in iter_children(current):
            if item.is_folder:
                k = key(item.id)
                if k not in seen:
                    seen.add(k)
                    subfolders.append(item.id)
            elif item.is_audio:
                tracks.append(item)
        if recursive:
            pending[:0] = subfolders
    return tracks


class Drive:
    def __init__(self, service):
        self._service = service

    @classmethod
    def from_credentials(cls, creds) -> "Drive":
        from googleapiclient.discovery import build

        return cls(build("drive", "v3", credentials=creds, cache_discovery=False))

    def user_email(self) -> str:
        about = self._service.about().get(fields="user(emailAddress)").execute()
        return about["user"]["emailAddress"]

    def _files(self):
        return self._service.files()

    def iter_children(self, folder_id: str = "root") -> Iterator[DriveItem]:
        if folder_id == SHARED_WITH_ME:
            query = "sharedWithMe = true and trashed = false"
        else:
            query = f"{_quote(folder_id)} in parents and trashed = false"
        page_token = None
        while True:
            resp = self._files().list(
                q=query,
                fields=_FIELDS,
                orderBy="folder,name_natural",
                pageSize=1000,
                pageToken=page_token,
                supportsAllDrives=True,
                includeItemsFromAllDrives=True,
            ).execute()
            for f in resp.get("files", []):
                yield DriveItem.from_api(f)
            page_token = resp.get("nextPageToken")
            if not page_token:
                return

    def list_children(self, folder_id: str = "root") -> list[DriveItem]:
        return list(self.iter_children(folder_id))

    def list_audio(self, folder_id: str = "root", recursive: bool = False) -> list[DriveItem]:
        """Arquivos de áudio da pasta, em ordem natural de nome; subpastas depois, se recursive."""
        return collect_audio(self.iter_children, folder_id, recursive)

    def get(self, file_id: str) -> DriveItem:
        f = self._files().get(
            fileId=file_id,
            fields="id, name, mimeType, size, shortcutDetails",
            supportsAllDrives=True,
        ).execute()
        return DriveItem.from_api(f)

    def find_child_folder(self, parent_id: str, name: str) -> DriveItem | None:
        folders = [i for i in self.iter_children(parent_id) if i.is_folder]
        for item in folders:
            if item.name == name:
                return item
        lowered = name.casefold()
        for item in folders:
            if item.name.casefold() == lowered:
                return item
        return None

    def resolve_path(self, path: str) -> DriveItem:
        """Resolve 'Música/Rock' a partir de Meu Drive, ou 'Compartilhados comigo/Rock'."""
        parts = [p for p in path.strip("/").split("/") if p]
        current = DriveItem(id="root", name="Meu Drive", mime_type=FOLDER_MIME)
        if parts and parts[0].casefold() in SHARED_ALIASES:
            current = shared_with_me()
            parts = parts[1:]
        for part in parts:
            child = self.find_child_folder(current.id, part)
            if child is None:
                raise DriveError(f"Pasta não encontrada: '{part}' dentro de '{current.name}'")
            current = child
        return current

    def resolve_folder(self, ref: str | None) -> DriveItem:
        """Aceita caminho (em Meu Drive ou 'Compartilhados comigo/…'), URL de pasta do Drive ou ID de pasta."""
        ref = (ref or "").strip()
        if ref in ("", "/", "root"):
            return DriveItem(id="root", name="Meu Drive", mime_type=FOLDER_MIME)
        folder_id = parse_folder_ref(ref)
        if folder_id is None:
            try:
                return self.resolve_path(ref)
            except DriveError:
                if "/" in ref or not _ID_RE.match(ref):
                    raise
                folder_id = ref
        try:
            item = self.get(folder_id)
        except Exception as e:
            raise DriveError(f"Não consegui abrir a pasta '{ref}': {e}") from e
        if not item.is_folder:
            raise DriveError(f"'{item.name}' não é uma pasta")
        return item
