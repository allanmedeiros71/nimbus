"""Onde o nimbus guarda credenciais e configurações."""

from __future__ import annotations

import os
from pathlib import Path


def config_dir() -> Path:
    """Diretório de configuração: $NIMBUS_CONFIG_DIR, $XDG_CONFIG_HOME/nimbus ou ~/.config/nimbus."""
    override = os.environ.get("NIMBUS_CONFIG_DIR")
    if override:
        return Path(override).expanduser()
    base = os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config"
    return Path(base).expanduser() / "nimbus"


def client_secret_path() -> Path:
    return config_dir() / "client_secret.json"


def token_path() -> Path:
    return config_dir() / "token.json"
