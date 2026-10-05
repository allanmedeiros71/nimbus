import time

import pytest
from test_drive import TREE, FakeService

from nimbus.completion import FolderCache, complete
from nimbus.drive import Drive


@pytest.fixture
def drive():
    return Drive(FakeService(TREE))


def test_root_lists_folders_and_shared(drive):
    assert complete(drive, "") == ["Compartilhados comigo/", "Música/"]
    assert complete(drive, "mú") == ["Música/"]
    assert complete(drive, "comp") == ["Compartilhados comigo/"]


def test_subfolders_keep_typed_parent(drive):
    assert complete(drive, "Música/") == ["Música/Rock/"]
    assert complete(drive, "música/r") == ["música/Rock/"]
    assert complete(drive, "Compartilhados comigo/disc") == ["Compartilhados comigo/Discos do Amigo/"]
    assert complete(drive, "Compartilhados comigo/Discos do Amigo/") == ["Compartilhados comigo/Discos do Amigo/Bônus/"]


def test_decomposed_accents_match(drive):
    assert complete(drive, "Música/") == ["Música/Rock/"]


def test_unknown_parent_raises(drive):
    with pytest.raises(Exception):
        complete(drive, "Nada/x")


def test_cache_avoids_api_calls(drive, tmp_path):
    cache = FolderCache(tmp_path / "c.json")
    complete(drive, "Música/", cache)
    calls = len(drive._service.files().queries)
    assert complete(drive, "Música/R", FolderCache(tmp_path / "c.json")) == ["Música/Rock/"]
    assert len(drive._service.files().queries) == calls


def test_cache_expires(tmp_path):
    cache = FolderCache(tmp_path / "c.json", ttl=0.01)
    cache.put("x", ["a"])
    time.sleep(0.02)
    assert cache.get("x") is None
