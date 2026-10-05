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


@pytest.mark.parametrize("value,junk", [
    ("DEEJAYKADEIRA 62992131650 WHATSAPP", True),
    ("Baixe em www.musicasgratis.com.br", True),
    ("(62) 99213-1650", True),
    ("@djfulano", True),
    ("Gravado por DJ Fulano", True),
    ("Insensível", False),
    ("Greatest Hits 1990-2000", False),
    ("Titãs", False),
    ("Acústico MTV", False),
])
def test_is_junk_tag(value, junk):
    from nimbus.metadata import is_junk_tag
    assert is_junk_tag(value) is junk


def test_spam_tags_fall_back_to_filename_and_skip_spam_cover(tmp_path):
    item = DriveItem("f1", "16 Titas - INSENSIVEL.mp3", "audio/mpeg")
    spam = "DEEJAYKADEIRA 62992131650 WHATSAPP"
    tags = {"title": spam, "artist": spam, "album": "100 Mais Pop&Rock Brasil"}
    resolver = MetadataResolver(lambda: "t", online=None, covers=CoverCache(tmp_path),
                                fetcher=lambda fid: fetcher_for(mp3_with_cover()))
    out = resolver.resolve(item, tags, folder_name="POP ROCK BRASIL")
    assert (out.info.artist, out.info.title, out.info.track) == ("Titas", "INSENSIVEL", "16")
    assert out.info.album == "100 Mais Pop&Rock Brasil"
    assert out.cover is None  # a capa embutida era a arte do DJ


def test_title_equal_to_artist_prefers_filename():
    item = DriveItem("f1", "02 Titas - NAO VOU ME ADAPTAR.mp3", "audio/mpeg")
    info = MetadataResolver(lambda: "t").basic(item, {"title": "Titas", "artist": "Titas"})
    assert (info.artist, info.title) == ("Titas", "NAO VOU ME ADAPTAR")


def test_good_tags_win_over_filename():
    item = DriveItem("f1", "16 Titas - INSENSIVEL.mp3", "audio/mpeg")
    info = MetadataResolver(lambda: "t").basic(item, {"title": "Insensível", "artist": "Titãs"})
    assert (info.artist, info.title) == ("Titãs", "Insensível")


def test_corrections_are_saved_and_reused(tmp_path):
    from nimbus.metadata import TrackStore

    item = DriveItem("f1", "16 Titas - INSENSIVEL.mp3", "audio/mpeg")
    spam = "DJ 62992131650 WHATSAPP"
    http = FakeHttp({
        "/release-group?": {"release-groups": [{"score": 100, "id": "rg", "title": "Õ Blésq Blom",
                                                "first-release-date": "1989"}]},
        "/recording?": {"recordings": [{"score": 100, "title": "Insensível", "first-release-date": "1989",
                                         "releases": [{"id": "rel", "title": "Õ Blésq Blom", "status": "Official",
                                                       "release-group": {"id": "rg", "primary-type": "Album"}}]}]},
        "/release-group/rg?": {"genres": [{"name": "rock", "count": 3}]},
        "coverartarchive.org/release-group/rg/front": b"CAPA",
    })
    calls = []

    def fetcher(fid):
        calls.append(fid)
        return fetcher_for(mp3_with_cover())

    def make(http_):
        return MetadataResolver(lambda: "t", online=OnlineLookup(http=http_, cache=tmp_path),
                                covers=CoverCache(tmp_path / "c"), fetcher=fetcher,
                                store=TrackStore(tmp_path / "tracks.json"))

    first = make(http).resolve(item, {"title": spam, "artist": spam})
    assert (first.info.title, first.info.artist, first.info.genre, first.cover) == ("INSENSIVEL", "Titas", "rock", b"CAPA")

    # Outra sessão do nimbus: a correção vem do disco, sem Drive nem internet.
    calls.clear()
    offline = FakeHttp({})
    resolver = make(offline)
    assert resolver.basic(item, None).title == "INSENSIVEL"  # já na hora de começar a tocar
    again = resolver.resolve(item, {"title": spam, "artist": spam})
    assert (again.info.title, again.info.genre, again.cover, again.cover_source) == (
        "INSENSIVEL", "rock", b"CAPA", "cover art archive")
    assert "salvo" in again.info.sources
    assert offline.urls == [] and calls == []


def test_network_error_keeps_retrying(tmp_path):
    from nimbus.metadata import TrackStore

    class Offline(FakeHttp):
        def get(self, url, headers=None):
            raise OSError("sem rede")

    item = DriveItem("f1", "Artista - Nome.wav", "audio/wav")
    store = TrackStore(tmp_path / "tracks.json")
    resolver = MetadataResolver(lambda: "t", online=OnlineLookup(http=Offline({}), cache=tmp_path),
                                covers=CoverCache(tmp_path / "c"),
                                fetcher=lambda fid: fetcher_for(b"RIFF" + b"\0" * 100), store=store)
    resolver.resolve(item, None)
    assert store.get("f1")["complete"] is False
    assert resolver.basic(item, None).title == "Nome"  # mostra o que tem enquanto isso


COMPILATION = [
    DriveItem("a1", "01 Capital Inicial - FOGO.mp3", "audio/mpeg"),
    DriveItem("a2", "02 Titas - NAO VOU ME ADAPTAR.mp3", "audio/mpeg"),
    DriveItem("a3", "17 Legiao Urbana - TEMPO PERDIDO.mp3", "audio/mpeg"),
    DriveItem("img", "capa.jpg", "image/jpeg"),
]


def test_is_compilation():
    from nimbus.metadata import is_compilation
    assert is_compilation(COMPILATION)
    album = [DriveItem(str(i), f"{i:02d} Legiao Urbana - Faixa {i}.mp3", "audio/mpeg") for i in range(1, 9)]
    assert not is_compilation(album)
    assert not is_compilation([DriveItem("x", "01 Tempo Perdido.mp3", "audio/mpeg")])


def test_compilation_prefers_per_track_cover(tmp_path):
    from nimbus.metadata import TrackStore

    files = {"a3": mp3_with_cover(), "img": b"CAPA DA PASTA"}
    http = FakeHttp({
        "/recording?": {"recordings": [{"score": 100, "title": "Tempo Perdido", "first-release-date": "1986",
                                         "releases": [{"id": "rel", "title": "Dois", "status": "Official",
                                                       "release-group": {"id": "rg-dois", "primary-type": "Album"}}]}]},
        "coverartarchive.org/release-group/rg-dois/front": b"CAPA DO DISCO DOIS",
    })
    resolver = MetadataResolver(lambda: "t", online=OnlineLookup(http=http, cache=tmp_path),
                                covers=CoverCache(tmp_path / "c"), fetcher=lambda fid: fetcher_for(files[fid]),
                                store=TrackStore(tmp_path / "t.json"))
    out = resolver.resolve(COMPILATION[2], {"album": "100 Mais Pop&Rock Brasil", "date": "2000", "genre": "rock"},
                           siblings=COMPILATION)
    assert out.cover == b"CAPA DO DISCO DOIS" and out.cover_source == "cover art archive"
    assert any("recording" in u and "release%3A" not in u for u in http.urls)  # busca pela música, não pela coletânea


def test_compilation_falls_back_to_folder_image(tmp_path):
    from nimbus.metadata import TrackStore

    files = {"a1": b"RIFF" + b"\0" * 100, "img": b"CAPA DA PASTA"}
    resolver = MetadataResolver(lambda: "t", online=OnlineLookup(http=FakeHttp({}), cache=tmp_path),
                                covers=CoverCache(tmp_path / "c"), fetcher=lambda fid: fetcher_for(files[fid]),
                                store=TrackStore(tmp_path / "t.json"))
    out = resolver.resolve(COMPILATION[0], None, siblings=COMPILATION)
    assert out.cover == b"CAPA DA PASTA" and out.cover_source == "pasta"


def test_recording_search_falls_back_to_looser_queries(tmp_path):
    from nimbus.metadata import TrackInfo

    class Picky(FakeHttp):
        def get(self, url, headers=None):
            self.urls.append(url)
            from urllib.parse import unquote
            q = unquote(url)
            if "/recording?" in url and "artist:(Legiao Urbana)" in q:
                return json.dumps({"recordings": [{"score": 90, "title": "Tempo Perdido",
                                                    "releases": [{"id": "r", "title": "Dois", "status": "Official",
                                                                  "release-group": {"id": "rg", "primary-type": "Album"}}]}]}).encode()
            if "/recording?" in url:
                return b'{"recordings": []}'
            return b'{}'

    online = OnlineLookup(http=Picky({}), cache=tmp_path)
    r = online.lookup(TrackInfo(title="TEMPO PERDIDO", artist="Legiao Urbana", album="100 Mais Pop&Rock Brasil"))
    assert r is not None and r.album == "Dois"


def test_online_note_explains_missing_cover(tmp_path):
    from nimbus.metadata import TrackStore

    class Down(FakeHttp):
        def get(self, url, headers=None):
            raise OSError("certificate verify failed")

    files = {"a1": b"RIFF" + b"\0" * 100, "img": b"CAPA DA PASTA"}
    resolver = MetadataResolver(lambda: "t", online=OnlineLookup(http=Down({}), cache=tmp_path),
                                covers=CoverCache(tmp_path / "c"), fetcher=lambda fid: fetcher_for(files[fid]),
                                store=TrackStore(tmp_path / "t.json"))
    out = resolver.resolve(COMPILATION[0], None, siblings=COMPILATION)
    assert out.cover_source == "pasta"
    assert out.note.startswith("internet: erro (certificate verify failed")


def test_store_cover_when_cover_art_archive_unreachable(tmp_path):
    from nimbus.metadata import TrackStore

    class CaaDown(FakeHttp):
        def get(self, url, headers=None):
            if "coverartarchive" in url:
                raise OSError("[Errno 111] Connection refused")
            return super().get(url, headers)

    http = CaaDown({
        "/recording?": {"recordings": [{"score": 100, "title": "A Via Láctea", "first-release-date": "1996",
                                         "releases": [{"id": "rel", "title": "A Tempestade", "status": "Official",
                                                       "release-group": {"id": "rg", "primary-type": "Album"}}]}]},
        "itunes.apple.com/search": {"results": [
            {"artistName": "Outro Artista", "artworkUrl100": "https://img/errada/100x100bb.jpg"},
            {"artistName": "Legião Urbana", "artworkUrl100": "https://img/certa/100x100bb.jpg"}]},
        "img/certa/300x300bb.jpg": b"CAPA ITUNES",
    })
    files = {"a3": b"RIFF" + b"\0" * 100, "img": b"CAPA DA PASTA"}
    store = TrackStore(tmp_path / "t.json")
    resolver = MetadataResolver(lambda: "t", online=OnlineLookup(http=http, cache=tmp_path),
                                covers=CoverCache(tmp_path / "c"), fetcher=lambda fid: fetcher_for(files[fid]),
                                store=store)
    item = DriveItem("a7", "07 Legiao Urbana - A VIA LACTEA.mp3", "audio/mpeg")
    files["a7"] = files["a3"]
    out = resolver.resolve(item, None, siblings=COMPILATION)
    assert (out.cover, out.cover_source, out.note) == (b"CAPA ITUNES", "itunes", "")
    assert store.get("a7")["complete"] is True


def test_same_name():
    from nimbus.metadata import _same_name
    assert _same_name("Legião Urbana", "LEGIAO URBANA")
    assert not _same_name("Legião Urbana", "Titãs")
