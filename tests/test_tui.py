"""A interface inteira com um mpv real, um Drive falso e um servidor HTTP local."""

import asyncio
import io
import shutil

import pytest
from mutagen.id3 import APIC, ID3, TALB, TIT2, TPE1

from nimbus import playback
from nimbus.drive import FOLDER_MIME, DriveItem, SHARED_WITH_ME
from nimbus.metadata import CoverCache, MetadataResolver
from nimbus.player import MpvPlayer
from nimbus.tui import BlockImage, CoverArt, NimbusApp, TrackTable, image_factory_from_env, progress_text

from test_player import TOKEN, FakeDrive, make_wav

pytestmark = pytest.mark.skipif(shutil.which("mpv") is None, reason="mpv não instalado")


def tiny_png():
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (8, 8), (200, 40, 40)).save(buf, "PNG")
    return buf.getvalue()


def tagged_wav(seconds, title, artist, album, cover=None):
    tags = ID3()
    tags.add(TIT2(encoding=3, text=title))
    tags.add(TPE1(encoding=3, text=artist))
    tags.add(TALB(encoding=3, text=album))
    if cover:
        tags.add(APIC(encoding=3, mime="image/png", type=3, desc="", data=cover))
    buf = io.BytesIO()
    tags.save(buf)
    return buf.getvalue() + make_wav(seconds)


class FakeDriveApi:
    def __init__(self, tree):
        self.tree = tree
        self.calls = []

    def list_children(self, folder_id):
        self.calls.append(folder_id)
        return list(self.tree.get(folder_id, []))


ROCK = DriveItem("rock", "Rock", FOLDER_MIME)
JAZZ = DriveItem("jazz", "Jazz", FOLDER_MIME)
T1 = DriveItem("t1", "01 - Banda - Primeira.wav", "audio/wav")
T2 = DriveItem("t2", "02 - Banda - Segunda.wav", "audio/wav")
DOC = DriveItem("doc", "letras.pdf", "application/pdf")


@pytest.fixture
def setup(monkeypatch, tmp_path):
    files = {
        "t1": tagged_wav(30, "Primeira Faixa", "A Banda", "O Disco", cover=tiny_png()),
        "t2": make_wav(30),
    }
    srv = FakeDrive(files)
    monkeypatch.setattr(playback, "media_url", srv.url)
    drive = FakeDriveApi({
        "root": [ROCK, JAZZ, DOC],
        "rock": [T1, T2],
        "jazz": [],
        SHARED_WITH_ME: [],
    })

    def fetcher(file_id):
        data = files[file_id]
        return lambda start, end: data[start:end + 1]

    resolver = MetadataResolver(lambda: TOKEN, online=None, covers=CoverCache(tmp_path), fetcher=fetcher)
    with MpvPlayer(extra_args=["--ao=null"]) as player:
        yield drive, player, resolver
    srv.close()


async def wait_until(pilot, cond, timeout=10.0):
    for _ in range(int(timeout / 0.05)):
        if cond():
            return True
        await pilot.pause(0.05)
    return False


def text_of(app, selector):
    return str(app.query_one(selector).render())


def test_browse_and_play(setup):
    drive, player, resolver = setup

    async def scenario():
        app = NimbusApp(drive, player, lambda: TOKEN, resolver, image_factory=None)
        async with app.run_test(size=(110, 32)) as pilot:
            table = app.query_one(TrackTable)
            # Meu Drive abre sozinho: pastas na árvore, pastas e áudio na lista (o PDF some).
            assert await wait_until(pilot, lambda: table.row_count == 2)
            assert [r.value for r in table.rows] == ["rock", "jazz"]

            # Tab vai para a lista; Enter na pasta "Rock" entra nela.
            await pilot.press("tab")
            await pilot.press("enter")
            assert await wait_until(pilot, lambda: [r.value for r in table.rows] == ["t1", "t2"])
            assert "Rock" in str(table.border_title)

            # Enter na primeira música toca a pasta a partir dela.
            await pilot.press("tab")  # foco volta para a árvore
            await pilot.press("tab")
            await pilot.press("enter")
            assert await wait_until(pilot, lambda: "Primeira Faixa" in text_of(app, "#pb-title"))
            assert "A Banda" in text_of(app, "#pb-title")
            assert "O Disco" in text_of(app, "#pb-album")
            assert await wait_until(pilot, lambda: "capa: arquivo" in text_of(app, "#pb-source"))
            assert "faixa 1/2" in text_of(app, "#pb-state")
            assert await wait_until(pilot, lambda: (app.state.get("time-pos") or 0) > 0.2)

            # Marcador de tocando na lista.
            assert str(table.get_cell("t1", "mark")) == "▶"

            # Controles: pausa, repeat, shuffle, volume, próxima.
            await pilot.press("space")
            assert await wait_until(pilot, lambda: app.state.get("pause") is True)
            assert await wait_until(pilot, lambda: "❚❚" in text_of(app, "#pb-title"))  # próximo _tick
            await pilot.press("space")
            assert await wait_until(pilot, lambda: app.state.get("pause") is False)

            await pilot.press("r")
            assert "repeat: all" in text_of(app, "#pb-state")
            await pilot.press("s")
            assert "shuffle: on" in text_of(app, "#pb-state")
            await pilot.press("s")

            vol = app.state.get("volume")
            await pilot.press("minus")
            assert await wait_until(pilot, lambda: app.state.get("volume") == vol - 5)

            await pilot.press("n")
            # A segunda faixa não tem tags: o nome do arquivo vira título e artista.
            assert await wait_until(pilot, lambda: "Segunda" in text_of(app, "#pb-title"))
            assert "Banda" in text_of(app, "#pb-title")
            assert await wait_until(pilot, lambda: str(table.get_cell("t2", "mark")) == "▶")
            assert str(table.get_cell("t1", "mark")) == ""
            assert "faixa 2/2" in text_of(app, "#pb-state")

            # repeat all: depois da última volta para a primeira.
            await pilot.press("n")
            assert await wait_until(pilot, lambda: "Primeira Faixa" in text_of(app, "#pb-title"))

            await pilot.press("question_mark")
            await pilot.pause(0.1)
            assert app.screen.__class__.__name__ == "HelpScreen"
            await pilot.press("escape")
            await pilot.press("q")

    asyncio.run(scenario())


def test_vim_keys_in_tree(setup):
    drive, player, resolver = setup

    async def scenario():
        app = NimbusApp(drive, player, lambda: TOKEN, resolver, image_factory=None)
        async with app.run_test(size=(110, 32)) as pilot:
            tree = app.query_one("#tree")
            table = app.query_one(TrackTable)
            assert await wait_until(pilot, lambda: len(tree.cursor_node.children) == 2)

            # Pasta do Drive: passar pelo cursor não lista nada.
            await pilot.press("j")
            assert tree.cursor_node.data.id == "rock"
            await pilot.pause(0.4)
            assert "rock" not in drive.calls
            assert "Enter ou →" in str(table.get_row_at(0)[1])

            await pilot.press("l")  # → abre
            assert await wait_until(pilot, lambda: [r.value for r in table.rows] == ["t1", "t2"])
            assert drive.calls.count("rock") == 1
            await pilot.press("l")  # já aberta: vai para a lista
            assert app.focused is table
            await pilot.press("h")
            assert app.focused is tree

            await pilot.press("j")
            assert tree.cursor_node.data.id == "jazz"
            await pilot.press("enter")  # Enter também abre
            assert await wait_until(pilot, lambda: "nenhuma" in str(table.get_row_at(0)[1]))

            # Voltar a uma pasta já listada mostra na hora, sem nova chamada.
            await pilot.press("k")
            assert await wait_until(pilot, lambda: [r.value for r in table.rows] == ["t1", "t2"])
            assert drive.calls.count("rock") == 1

            await pilot.press("h")
            assert tree.cursor_node.data.id == "root"
            await pilot.press("G")
            assert tree.cursor_node.data.name == "Compartilhados comigo"

    asyncio.run(scenario())


def test_local_folders_preview_while_navigating(setup):
    drive, player, resolver = setup

    async def scenario():
        app = NimbusApp(drive, player, lambda: TOKEN, resolver, image_factory=None, is_remote=lambda item: False)
        async with app.run_test(size=(110, 32)) as pilot:
            tree = app.query_one("#tree")
            table = app.query_one(TrackTable)
            assert await wait_until(pilot, lambda: len(tree.cursor_node.children) == 2)
            await pilot.press("j")
            assert await wait_until(pilot, lambda: [r.value for r in table.rows] == ["t1", "t2"])

    asyncio.run(scenario())


def test_cover_widget_mounts_image(setup):
    drive, player, resolver = setup

    async def scenario():
        app = NimbusApp(drive, player, lambda: TOKEN, resolver, image_factory=image_factory_from_env())
        async with app.run_test(size=(110, 32)) as pilot:
            cover = app.query_one(CoverArt)
            app.start_playback(ROCK, [T1, T2], [T1, T2], 0)
            assert await wait_until(pilot, lambda: cover.has_image)
            await pilot.pause(0.1)
            drawn = cover.query_one(BlockImage).render()
            assert drawn.plain.count("▀") == 14 * 7  # só meios-blocos, nada de texto de Sixel

    asyncio.run(scenario())


def test_progress_text():
    t = progress_text(20, 30, 120)
    assert t.plain.strip() == "0:30/2:00"
    assert len(t.plain) == 20
    assert len(progress_text(10, None, None).plain) == 10  # o rótulo é cortado, a barra não estoura


def test_cover_mode_defaults_to_blocks(monkeypatch):
    monkeypatch.delenv("NIMBUS_COVER", raising=False)
    assert image_factory_from_env() is BlockImage
    monkeypatch.setenv("NIMBUS_COVER", "off")
    assert image_factory_from_env() is None
    monkeypatch.setenv("NIMBUS_COVER", "qualquer-coisa")
    assert image_factory_from_env() is BlockImage
