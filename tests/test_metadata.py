import io
import json

import pytest
from mutagen.flac import Picture
from mutagen.id3 import APIC, ID3, TIT2

from nimbus.drive import DriveItem
from nimbus.metadata import (
    CoverCache,
    MetadataResolver,
    OnlineLookup,
    OnlineResult,
    embedded_cover,
    folder_cover_candidates,
    from_filename,
    from_tags,
    parse_recording_search,
)

PNG = b"\x89PNG\r\n\x1a\n" + b"x" * 300_000  # grande o bastante para exigir mais de uma leitura


def fetcher_for(data, calls=None):
    def fetch(start, end):
        if calls is not None:
            calls.append((start, end))
        return data[start:end + 1]
    return fetch


def mp3_with_cover():
    tags = ID3()
    tags.add(TIT2(encoding=3, text="Música"))
    tags.add(APIC(encoding=3, mime="image/png", type=0, desc="verso", data=b"verso"))
    tags.add(APIC(encoding=3, mime="image/png", type=3, desc="frente", data=PNG))
    buf = io.BytesIO()
    tags.save(buf)
    return buf.getvalue() + b"\xff\xfb" + b"\x00" * 5_000_000  # "áudio" depois da tag


def flac_with_cover():
    streaminfo = bytes([0x00]) + (34).to_bytes(3, "big") + b"\x00" * 34
    pic = Picture()
    pic.type, pic.mime, pic.data = 3, "image/png", PNG
    body = pic.write()
    picture = bytes([0x80 | 6]) + len(body).to_bytes(3, "big") + body
    return b"fLaC" + streaminfo + picture + b"\xff\xf8" + b"\x00" * 5_000_000


def test_embedded_cover_mp3_reads_only_the_tag():
    data = mp3_with_cover()
    calls = []
    assert embedded_cover(fetcher_for(data, calls)) == PNG
    assert max(end for _, end in calls) < 1_000_000


def test_embedded_cover_flac():
    data = flac_with_cover()
    calls = []
    assert embedded_cover(fetcher_for(data, calls)) == PNG
    assert max(end for _, end in calls) < 1_000_000


def test_embedded_cover_absent():
    assert embedded_cover(fetcher_for(b"RIFF" + b"\x00" * 1000)) is None


def test_from_tags_normalizes_keys():
    info = from_tags({"TITLE": "Águas de Março", "Artist": "Elis Regina", "ALBUM": "Elis & Tom",
                      "date": "1974-01-01", "track": "1/14", "genre": ""})
    assert (info.title, info.artist, info.album, info.year, info.track, info.genre) == (
        "Águas de Março", "Elis Regina", "Elis & Tom", "1974", "1", "")


@pytest.mark.parametrize("name,expected", [
    ("01 - Elis Regina - Águas de Março.mp3", ("Elis Regina", "Águas de Março", "1")),
    ("Tom Jobim - Wave.flac", ("Tom Jobim", "Wave", "")),
    ("03. Corcovado.ogg", ("", "Corcovado", "3")),
    ("Garota_de_Ipanema.mp3", ("", "Garota de Ipanema", "")),
    ("1999.mp3", ("", "1999", "")),
])
def test_from_filename(name, expected):
    info = from_filename(name)
    assert (info.artist, info.title, info.track) == expected


def test_folder_cover_candidates_prefers_cover_names():
    items = [
        DriveItem("a", "scan2.jpg", "image/jpeg"),
        DriveItem("b", "Folder.JPG", "application/octet-stream"),
        DriveItem("c", "musica.mp3", "audio/mpeg"),
        DriveItem("d", "capa frente.png", "image/png"),
    ]
    assert [i.id for i in folder_cover_candidates(items)] == ["b", "d", "a"]


def test_parse_recording_search_prefers_official_album():
    data = {"recordings": [{
        "score": 100, "title": "Wave", "first-release-date": "1967",
        "artist-credit": [{"name": "Antônio Carlos Jobim", "joinphrase": ""}],
        "releases": [
            {"id": "comp", "title": "Best Of", "status": "Official", "date": "1990",
             "release-group": {"id": "rg-comp", "primary-type": "Album", "secondary-types": ["Compilation"]}},
            {"id": "orig", "title": "Wave", "status": "Official", "date": "1967",
             "release-group": {"id": "rg-wave", "primary-type": "Album"}},
        ],
    }]}
    r = parse_recording_search(data)
    assert (r.album, r.year, r.release_group, r.artist) == ("Wave", "1967", "rg-wave", "Antônio Carlos Jobim")


def test_parse_recording_search_ignores_low_scores():
    assert parse_recording_search({"recordings": [{"score": 40, "title": "x"}]}) is None


class FakeHttp:
    def __init__(self, routes):
        self.routes = routes
        self.urls = []

    def get(self, url, headers=None):
        self.urls.append(url)
        for prefix, body in self.routes.items():
            if prefix in url:
                return body if isinstance(body, bytes) else json.dumps(body).encode()
        import urllib.error
        raise urllib.error.HTTPError(url, 404, "not found", {}, None)

    def json(self, url):
        return json.loads(self.get(url))


def test_resolver_order_and_online_fill(tmp_path):
    item = DriveItem("f1", "02 - Tom Jobim - Wave.wav", "audio/wav")
    http = FakeHttp({
        "/recording?": {"recordings": [{"score": 95, "title": "Wave", "first-release-date": "1967-01",
                                         "releases": [{"id": "rel", "title": "Wave", "status": "Official",
                                                       "release-group": {"id": "rg", "primary-type": "Album"}}]}]},
        "/release-group/rg?": {"genres": [{"name": "bossa nova", "count": 5}, {"name": "jazz", "count": 2}]},
        "coverartarchive.org/release-group/rg/front": b"CAPA",
    })
    online = OnlineLookup(http=http, cache=tmp_path)
    covers = CoverCache(tmp_path / "covers")
    resolver = MetadataResolver(lambda: "t", online=online, covers=covers,
                                fetcher=lambda fid: fetcher_for(b"RIFF" + b"\0" * 100))
    out = resolver.resolve(item, tags=None, siblings=[], folder_name="Bossa")
    assert (out.info.title, out.info.artist, out.info.album, out.info.year, out.info.genre) == (
        "Wave", "Tom Jobim", "Wave", "1967", "bossa nova")
    assert out.cover == b"CAPA" and out.cover_source == "cover art archive"
    assert "pasta" not in out.info.sources  # o nome da pasta era só palpite

    # Segunda vez vem do cache, sem internet.
    http.urls.clear()
    again = resolver.resolve(item, tags=None, siblings=[], folder_name="Bossa")
    assert again.cover == b"CAPA" and http.urls == []


def test_resolver_uses_folder_image_before_internet(tmp_path):
    item = DriveItem("f1", "faixa.wav", "audio/wav")
    files = {"f1": b"RIFF" + b"\0" * 100, "img": b"IMAGEM"}
    http = FakeHttp({})
    resolver = MetadataResolver(lambda: "t", online=OnlineLookup(http=http, cache=tmp_path),
                                covers=CoverCache(tmp_path / "c"), fetcher=lambda fid: fetcher_for(files[fid]))
    tags = {"title": "T", "artist": "A", "album": "Al", "date": "2001", "genre": "rock"}
    out = resolver.resolve(item, tags, siblings=[DriveItem("img", "cover.jpg", "image/jpeg")])
    assert out.cover == b"IMAGEM" and out.cover_source == "pasta"
    assert http.urls == []  # tags completas e capa achada: nada de internet


def test_resolver_offline(tmp_path):
    item = DriveItem("f1", "Artista - Nome.mp3", "audio/mpeg")
    resolver = MetadataResolver(lambda: "t", online=None, covers=CoverCache(tmp_path),
                                fetcher=lambda fid: fetcher_for(mp3_with_cover()))
    out = resolver.resolve(item, None, folder_name="Disco")
    assert out.cover == PNG and out.cover_source == "arquivo"
    assert (out.info.artist, out.info.title, out.info.album) == ("Artista", "Nome", "Disco")


def test_network_errors_are_not_cached(tmp_path):
    item = DriveItem("f1", "x.mp3", "audio/mpeg")
    state = {"fail": True}

    def fetcher(fid):
        def fetch(start, end):
            if state["fail"]:
                raise OSError("sem rede")
            return mp3_with_cover()[start:end + 1]
        return fetch

    resolver = MetadataResolver(lambda: "t", online=None, covers=CoverCache(tmp_path), fetcher=fetcher)
    assert resolver.resolve(item, None).cover is None
    state["fail"] = False
    assert resolver.resolve(item, None).cover == PNG


def test_online_result_roundtrip_in_cache(tmp_path):
    http = FakeHttp({"/recording?": {"recordings": []}})
    online = OnlineLookup(http=http, cache=tmp_path)
    from nimbus.metadata import TrackInfo
    assert online.lookup(TrackInfo(title="Nada")) is None
    assert OnlineLookup(http=FakeHttp({}), cache=tmp_path).lookup(TrackInfo(title="Nada")) is None
    assert isinstance(OnlineResult(), OnlineResult)
