"""nimbus: toca em streaming as músicas das suas pastas do Google Drive."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("nimbus-player")
except PackageNotFoundError:  # rodando direto do código, sem instalar
    __version__ = "0.0.0"
