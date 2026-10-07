"""Músicas do computador: listagem, discos externos, CLI, autocomplete e reprodução com mpv real."""

import asyncio
import io
import shutil

import pytest
from mutagen.id3 import APIC, ID3, TALB, TIT2, TPE1

from nimbus import cli, local
from nimbus.auth import NotLoggedIn
from nimbus.completion import complete
from nimbus.drive import DriveError
from nimbus.library import Library
from nimbus.metadata import CoverCache, MetadataResolver, TrackStore
from nimbus.playback import Controller, PlayQueue

from test_player import make_wav

needs_mpv = pytest.mark.skipif(shutil.which("mpv") is None, reason="mpv não instalado")


def tiny_png():
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (8, 8), (40, 40, 200)).save(buf, "PNG")
    return buf.getvalue()


@pytest.fixture
def music(tmp_path):
    root = tmp_path / "Música"
    (root / "Disco 2").mkdir(parents=True)
    (root / ".escondida").mkdir()
    tags = ID3()
    tags.add(TIT2(encoding=3, text="Faixa Dois"))
    tags.add(TPE1(encoding=3, text="Banda Local"))
    tags.add(TALB(encoding=3, text="Disco Local"))
    tags.add(APIC(encoding=3, mime="image/png", type=3, desc="", data=tiny_png()))
    buf = io.BytesIO()
    tags.save(buf)
    (root / "2 - Banda - Dois.mp3").write_bytes(buf.getvalue() + make_wav(1))
    (root / "10 - Banda - Dez.wav").write_bytes(make_wav(1))
    (root / "lista.m3u").write_text("")
    (root / "capa.jpg").write_bytes(b"jpg")
    (root / "Disco 2" / "01 - Outra.flac").write_bytes(b"x")
    (root / "Disco 2" / "volta").symlink_to(root)  # laço: não pode travar o recursive
    return root


def ids(items):
    return [i.name for i in items]


def test_listing_natural_order_and_audio_detection(music):
    items = list(local.iter_local_children(local.local_item(music).id))
    assert ids(items) == ["Disco 2", "2 - Banda - Dois.mp3", "10 - Banda - Dez.wav", "capa.jpg", "lista.m3u"]
    folder, dois, dez, capa, m3u = items
    assert folder.is_folder and not folder.is_audio
    assert dois.is_audio and dez.is_audio
    assert not m3u.is_audio and not capa.is_audio
    assert dois.size == (music / "2 - Banda - Dois.mp3").stat().st_size
    assert local.local_path(dois) == str(music / "2 - Banda - Dois.mp3")


def test_unreadable_folder_is_a_drive_error(tmp_path):
    with pytest.raises(DriveError):
        list(local.iter_local_children(local.LOCAL_PREFIX + str(tmp_path / "não existe")))


def test_recursive_audio_survives_symlink_loop(music):
    lib = Library(None)
    folder = lib.resolve_folder(str(music))
    assert ids(lib.list_audio(folder.id)) == ["2 - Banda - Dois.mp3", "10 - Banda - Dez.wav"]
    assert ids(lib.list_audio(folder.id, recursive=True)) == [
        "2 - Banda - Dois.mp3", "10 - Banda - Dez.wav", "01 - Outra.flac"]


def test_resolve_refs(music, monkeypatch):
    lib = Library(None)
    monkeypatch.chdir(music.parent)
    assert lib.resolve_folder("./Música").id == local.LOCAL_PREFIX + str(music)
    assert lib.resolve_folder("file://" + str(music).replace(" ", "%20")).name == "Música"
    assert lib.resolve_folder("Este computador").id == local.LOCAL_ROOT
    monkeypatch.setenv("HOME", str(music.parent))
    assert lib.resolve_folder("~/Música/Disco 2").name == "Disco 2"
    with pytest.raises(DriveError):
        lib.resolve_folder(str(music / "10 - Banda - Dez.wav"))  # arquivo, não pasta
    with pytest.raises(NotLoggedIn):
        lib.resolve_folder("Música/Rock")  # caminho do Drive sem login


def test_mounted_volumes_macos(tmp_path):
    vols = tmp_path / "Volumes"
    (vols / "Pendrive").mkdir(parents=True)
    (vols / "HD Externo").mkdir()
    (vols / ".Trashes").mkdir()
    (vols / "com.apple.TimeMachine.localsnapshots").mkdir()
    (vols / "Macintosh HD").symlink_to("/")
    found = local.mounted_volumes("darwin", bases=[str(vols)])
    assert ids(found) == ["HD Externo", "Pendrive"]
    assert all(f.is_folder for f in found)


def test_mounted_volumes_linux_user_dirs(tmp_path):
    media = tmp_path / "media"
    (media / "allan" / "PENDRIVE").mkdir(parents=True)
    (media / "allan" / "Backup").mkdir()
    found = local.mounted_volumes("linux", bases=[str(media), str(tmp_path / "nada")])
    assert ids(found) == ["Backup", "PENDRIVE"]


def test_places_start_with_home_and_end_with_root(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(local, "mounted_volumes", lambda platform=None: [local.local_item(tmp_path, "USB")])
    places = local.local_places()
    assert places[0].name.startswith("Pasta pessoal") and local.local_path(places[0]) == str(tmp_path)
    assert ids(places)[1:] == ["USB", "Disco do sistema (/)"]


def test_completion_local(music, monkeypatch):
    monkeypatch.chdir(music.parent)
    assert complete(None, "./Mú") == ["./Música/"]
    assert complete(None, "./Música/") == ["./Música/Disco 2/"]
    assert complete(None, str(music) + "/D") == [str(music) + "/Disco 2/"]
    monkeypatch.setenv("HOME", str(music.parent))
    assert complete(None, "~/M") == ["~/Música/"]


def test_cli_ls_local_without_login(music, monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    assert cli.main(["ls", str(music)]) == 0
    out = capsys.readouterr().out
    assert "📁 Disco 2/" in out and "♪  2 - Banda - Dois.mp3" in out
    assert "lista.m3u" not in out
    assert cli.main(["ls", "Música/Rock"]) == 1  # Drive continua exigindo login
    assert "nimbus login" in capsys.readouterr().err


def test_local_metadata_and_embedded_cover(music, tmp_path):
    lib = Library(None)
    folder = lib.resolve_folder(str(music))
    items = lib.list_children(folder.id)
    track = items[1]
    resolver = MetadataResolver(lambda: pytest.fail("arquivo local não usa token"), online=None,
                                covers=CoverCache(tmp_path / "covers"), store=TrackStore(tmp_path / "t.json"))
    resolved = resolver.resolve(track, {"title": "Faixa Dois", "artist": "Banda Local"}, items, folder.name)
    assert resolved.cover == tiny_png() and resolved.cover_source == "arquivo"

    sem_capa = items[2]
    resolved = resolver.resolve(sem_capa, None, items, folder.name)
    assert resolved.cover == b"jpg" and resolved.cover_source == "pasta"


@needs_mpv
def test_controller_plays_local_files(music):
    from nimbus.player import MpvPlayer

    lib = Library(None)
    tracks = lib.list_audio(lib.resolve_folder(str(music)).id)
    with MpvPlayer(extra_args=["--ao=null"]) as player:
        ctl = Controller(player, PlayQueue(tracks), lambda: pytest.fail("arquivo local não usa token"))
        ctl.play_current()
        played = []
        while not ctl.finished:
            event = player.events.get(timeout=10)
            if event.get("event") == "file-loaded":
                played.append(player.get_property("path"))
            ctl.handle_event(event)
            assert ctl.last_error is None
        assert played == [str(music / "2 - Banda - Dois.mp3"), str(music / "10 - Banda - Dez.wav")]


@needs_mpv
def test_tui_without_login_shows_this_computer(music, monkeypatch, tmp_path):
    from nimbus.player import MpvPlayer
    from nimbus.tui import NimbusApp, TrackTable

    from test_tui import text_of, wait_until

    monkeypatch.setattr(local, "local_places", lambda platform=None: [local.local_item(music.parent, "Pasta pessoal")])
    lib = Library(None)
    resolver = MetadataResolver(lambda: "", online=None, covers=CoverCache(tmp_path / "covers"),
                                store=TrackStore(tmp_path / "t.json"))

    async def scenario():
        with MpvPlayer(extra_args=["--ao=null"]) as player:
            app = NimbusApp(lib, player, lambda: "", resolver, image_factory=None)
            async with app.run_test(size=(110, 32)) as pilot:
                tree = app.query_one("#tree")
                table = app.query_one(TrackTable)
                assert [n.data.name for n in tree.root.children] == ["Este computador"]
                assert await wait_until(pilot, lambda: table.row_count == 1)
                await pilot.press("tab", "enter")  # entra em "Pasta pessoal"
                assert await wait_until(pilot, lambda: table.row_count == 1 and "Música" in str(table.get_row_at(0)[1]))
                # Entra em "Música", que ainda não foi aberta na árvore e tem subpasta.
                await pilot.press("tab", "tab", "enter")
                assert await wait_until(pilot, lambda: "Música" in str(table.border_title)
                                        and table.row_count == 3)
                await pilot.press("tab", "tab", "j", "enter")  # toca "2 - Banda - Dois.mp3"
                assert await wait_until(pilot, lambda: "Faixa Dois" in text_of(app, "#pb-title"))
                assert await wait_until(pilot, lambda: "capa: arquivo" in text_of(app, "#pb-source"))
                await pilot.press("q")

    asyncio.run(scenario())


@needs_mpv
def test_tui_previews_local_folders_but_not_drive(music, monkeypatch, tmp_path):
    from nimbus.player import MpvPlayer
    from nimbus.tui import NimbusApp, TrackTable

    from test_tui import wait_until

    class FakeDrive:
        calls = []

        def iter_children(self, folder_id):
            self.calls.append(folder_id)
            return iter([])

    monkeypatch.setattr(local, "local_places", lambda platform=None: [local.local_item(music.parent, "Pasta pessoal")])
    drive = FakeDrive()
    lib = Library(drive)
    resolver = MetadataResolver(lambda: "", online=None, covers=CoverCache(tmp_path / "covers"),
                                store=TrackStore(tmp_path / "t.json"))

    async def scenario():
        with MpvPlayer(extra_args=["--ao=null"]) as player:
            app = NimbusApp(lib, player, lambda: "", resolver, image_factory=None)
            async with app.run_test(size=(110, 32)) as pilot:
                tree = app.query_one("#tree")
                table = app.query_one(TrackTable)
                assert [n.data.name for n in tree.root.children] == [
                    "Meu Drive", "Compartilhados comigo", "Este computador"]
                await pilot.press("j", "j")  # passa por "Compartilhados comigo" sem abrir
                assert tree.cursor_node.data.name == "Este computador"
                assert await wait_until(pilot, lambda: table.row_count == 1
                                        and "Pasta pessoal" in str(table.get_row_at(0)[1]))
                assert "sharedWithMe" not in drive.calls

    asyncio.run(scenario())
