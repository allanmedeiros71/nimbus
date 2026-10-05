import pytest

from nimbus.drive import FOLDER_MIME, Drive, DriveError, DriveItem, parse_folder_ref


class _Req:
    def __init__(self, result):
        self._result = result

    def execute(self):
        return self._result


class FakeFiles:
    """Imita files().list/get com uma árvore em memória e páginas de 2 itens."""

    def __init__(self, tree):
        self.tree = tree  # parent_id -> [file dict]
        self.by_id = {f["id"]: f for files in tree.values() for f in files}
        self.queries = []

    def list(self, q, pageToken=None, **kw):
        self.queries.append(q)
        assert kw["supportsAllDrives"] and kw["includeItemsFromAllDrives"]
        parent = q.split("'")[1]
        files = self.tree.get(parent, [])
        start = int(pageToken or 0)
        page = files[start:start + 2]
        resp = {"files": page}
        if start + 2 < len(files):
            resp["nextPageToken"] = str(start + 2)
        return _Req(resp)

    def get(self, fileId, **kw):
        if fileId not in self.by_id:
            raise RuntimeError("404")
        return _Req(self.by_id[fileId])


class FakeService:
    def __init__(self, tree):
        self._files = FakeFiles(tree)

    def files(self):
        return self._files


def folder(id, name):
    return {"id": id, "name": name, "mimeType": FOLDER_MIME}


def audio(id, name, mime="audio/mpeg", size="1000"):
    return {"id": id, "name": name, "mimeType": mime, "size": size}


TREE = {
    "root": [folder("musica00000000000001", "Música"), audio("a0", "solta.mp3"), {"id": "d1", "name": "notas", "mimeType": "application/vnd.google-apps.document"}],
    "musica00000000000001": [
        folder("rock0000000000000001", "Rock"),
        audio("a1", "01 Abertura.flac", mime="application/octet-stream"),
        audio("a2", "02 Fim.mp3"),
        {"id": "x", "name": "capa.jpg", "mimeType": "image/jpeg", "size": "10"},
        {"id": "s1", "name": "atalho.mp3", "mimeType": "application/vnd.google-apps.shortcut",
         "shortcutDetails": {"targetId": "a9", "targetMimeType": "audio/mpeg"}},
    ],
    "rock0000000000000001": [audio("a3", "rock.ogg", mime="audio/ogg")],
}


@pytest.fixture
def drive():
    return Drive(FakeService(TREE))


def test_audio_detection():
    assert DriveItem("1", "x.flac", "application/octet-stream").is_audio
    assert DriveItem("1", "x", "audio/mpeg").is_audio
    assert not DriveItem("1", "x.mp3", "application/vnd.google-apps.document").is_audio
    assert not DriveItem("1", "capa.jpg", "image/jpeg").is_audio


def test_list_children_paginates(drive):
    names = [i.name for i in drive.list_children("musica00000000000001")]
    assert names == ["Rock", "01 Abertura.flac", "02 Fim.mp3", "capa.jpg", "atalho.mp3"]


def test_list_audio_follows_shortcuts(drive):
    tracks = drive.list_audio("musica00000000000001")
    assert [t.id for t in tracks] == ["a1", "a2", "a9"]
    assert tracks[0].size == 1000


def test_list_audio_recursive(drive):
    assert [t.id for t in drive.list_audio("root", recursive=True)] == ["a0", "a1", "a2", "a9", "a3"]


def test_resolve_path_case_insensitive(drive):
    assert drive.resolve_folder("música/rock").id == "rock0000000000000001"
    assert drive.resolve_folder("").id == "root"


def test_resolve_url_and_id(drive):
    url = "https://drive.google.com/drive/folders/rock0000000000000001?usp=sharing"
    assert parse_folder_ref(url) == "rock0000000000000001"
    assert drive.resolve_folder(url).name == "Rock"
    assert drive.resolve_folder("rock0000000000000001").name == "Rock"


def test_resolve_errors(drive):
    with pytest.raises(DriveError):
        drive.resolve_folder("Música/Jazz")
    with pytest.raises(DriveError):
        drive.resolve_folder("https://drive.google.com/file/d/a0000000000000000000/view")


def test_query_escapes_quotes(drive):
    drive.list_children("it's")
    assert drive._service.files().queries[-1].startswith("'it\\'s' in parents")
