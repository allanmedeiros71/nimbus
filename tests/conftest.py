import pytest


@pytest.fixture(autouse=True)
def _isolated_cache(monkeypatch, tmp_path):
    """Nenhum teste lê ou grava o cache de verdade em ~/.cache/nimbus."""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
