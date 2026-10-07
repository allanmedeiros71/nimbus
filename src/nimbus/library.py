"""Junta as duas origens de música: o Google Drive e o disco deste computador.

A interface e a CLI conversam só com a Library, que encaminha cada pedido para
o Drive ou para o sistema de arquivos conforme o ID do item. Sem login no
Google, a parte local continua funcionando.
"""

from __future__ import annotations

import os
from typing import Iterator, Optional

from nimbus import local
from nimbus.auth import NotLoggedIn
from nimbus.drive import Drive, DriveError, DriveItem, collect_audio


class Library:
    def __init__(self, drive: Optional[Drive] = None, login_error: str = ""):
        self.drive = drive
        self._login_error = login_error or "Você ainda não fez login. Rode: nimbus login"

    @property
    def has_drive(self) -> bool:
        return self.drive is not None

    def _need_drive(self) -> Drive:
        if self.drive is None:
            raise NotLoggedIn(self._login_error)
        return self.drive

    def iter_children(self, folder_id: str = "root") -> Iterator[DriveItem]:
        if local.is_local_id(folder_id):
            return local.iter_local_children(folder_id)
        return self._need_drive().iter_children(folder_id)

    def list_children(self, folder_id: str = "root") -> list[DriveItem]:
        return list(self.iter_children(folder_id))

    def list_audio(self, folder_id: str = "root", recursive: bool = False) -> list[DriveItem]:
        if not local.is_local_id(folder_id):
            return self._need_drive().list_audio(folder_id, recursive)

        def children(item_id: str):
            try:
                return list(local.iter_local_children(item_id))
            except DriveError:
                if item_id == folder_id:
                    raise
                return []  # subpasta sem permissão: pula

        return collect_audio(children, folder_id, recursive,
                             key=lambda i: os.path.realpath(local.local_path(i)) if i != local.LOCAL_ROOT else i)

    def resolve_folder(self, ref: Optional[str]) -> DriveItem:
        """Caminho local (/…, ~/…, ./…) ou, para o resto, pasta do Drive."""
        ref = (ref or "").strip()
        if local.looks_local(ref) or ref.casefold().rstrip("/") in local.LOCAL_ALIASES:
            return local.resolve_local(ref)
        return self._need_drive().resolve_folder(ref)
