"""Interface de terminal (Textual) inspirada no spotify-player.

Em cima, o painel Playback: capa, metadados, estado (repeat, shuffle, volume),
barra de progresso e atalhos. Embaixo, Directories: a árvore de pastas do
Drive e de "Este computador" (pasta pessoal, HD externo, pendrive) à esquerda
e o conteúdo da pasta selecionada à direita; Enter numa
música começa a tocar a pasta a partir dela.

O mpv manda eventos por uma thread própria; a interface só lê o estado num
timer, então nada de rede ou IPC bloqueia a tela.
"""

from __future__ import annotations

import io
import os
import queue
import re
import threading
from typing import Callable, Optional

from rich.text import Text
from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widget import Widget
from textual.widgets import DataTable, Static, Tree
from textual.widgets.tree import TreeNode

from nimbus.drive import FOLDER_MIME, DriveItem, shared_with_me
from nimbus.local import LOCAL_ROOT, is_local, local_path, local_root
from nimbus.metadata import MetadataResolver, TrackInfo
from nimbus.playback import Controller, PlayQueue

KEYS_LINE = "␣ play/pause · n/p faixa · </> ±10s · +/- vol · r repeat · s shuffle · ? ajuda · q sair"

HELP_TEXT = """\
[b]Reprodução[/b]
  espaço       tocar / pausar
  n  p         próxima / anterior
  >  <         avança / volta 10s  (também . e ,)
  +  -         volume
  m            mudo
  r            repeat: off → all → one
  s            shuffle liga/desliga

[b]Navegação (estilo vim)[/b]
  j  k         desce / sobe
  h  l         na árvore: fecha ou sobe / abre a pasta (Drive: lista só aqui)
               na lista: volta para a árvore / abre a pasta
  g  G         primeiro / último item
  Tab          alterna entre árvore e lista
  Enter        na lista: toca a música (a pasta vira a fila)
               ou entra na subpasta
  ?            esta ajuda
  q            sair

[dim]Esc ou ? fecha[/dim]"""

_ID_IN_URL = re.compile(r"/files/([^?/]+)")
_SIZE_UNITS = ("B", "KB", "MB", "GB")


def fmt_time(seconds: Optional[float]) -> str:
    if seconds is None or seconds < 0:
        return "--:--"
    s = int(seconds)
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}"


def fmt_size(size: Optional[int]) -> str:
    if size is None:
        return ""
    value = float(size)
    for unit in _SIZE_UNITS:
        if value < 1024 or unit == "GB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return ""


def progress_text(width: int, pos: Optional[float], dur: Optional[float]) -> Text:
    """Barra cheia até a posição atual, com o tempo logo depois do trecho cheio."""
    width = max(width, 1)
    label = f"{fmt_time(pos)}/{fmt_time(dur)}"
    frac = (pos or 0) / dur if dur else 0.0
    filled = max(0, min(width, round(width * frac)))
    start = max(0, min(filled, width - len(label)))
    chars = [" "] * width
    for i, ch in enumerate(label[: width - start]):
        chars[start + i] = ch
    text = Text()
    for i, ch in enumerate(chars):
        in_label = start <= i < start + len(label)
        if i < filled:
            style = "bold #1e1e2e on #a6e3a1" if in_label else "on #a6e3a1"
        else:
            style = "bold #f9e2af on #45475a" if in_label else "on #45475a"
        text.append(ch, style)
    return text


# --- widgets ------------------------------------------------------------------

class ProgressLine(Static):
    def __init__(self, **kw):
        super().__init__(**kw)
        self.pos: Optional[float] = None
        self.dur: Optional[float] = None

    def set_progress(self, pos: Optional[float], dur: Optional[float]) -> None:
        if (pos, dur) != (self.pos, self.dur):
            self.pos, self.dur = pos, dur
            self.refresh()

    def render(self) -> Text:
        return progress_text(self.size.width, self.pos, self.dur)


class CoverArt(Widget):
    """Capa do álbum. image_factory cria o widget de imagem (textual-image)."""

    DEFAULT_CSS = """
    CoverArt { width: 14; height: 7; }
    CoverArt > .placeholder { width: 100%; height: 100%; content-align: center middle;
                              background: $boost; color: $text-muted; }
    CoverArt > .image { width: 100%; height: 100%; }
    """

    def __init__(self, image_factory: Optional[Callable], **kw):
        super().__init__(**kw)
        self._factory = image_factory
        self.has_image = False

    def compose(self) -> ComposeResult:
        yield Static("♪", classes="placeholder")

    def show(self, image) -> None:
        """image: PIL.Image ou None para o desenho padrão."""
        self.remove_children()
        if image is not None and self._factory is not None:
            try:
                self.mount(self._factory(image, classes="image"))
                self.has_image = True
                return
            except Exception:
                pass
        self.has_image = False
        self.mount(Static("♪", classes="placeholder"))


class FolderTree(Tree):
    BINDINGS = [
        Binding("j", "cursor_down", show=False),
        Binding("k", "cursor_up", show=False),
        Binding("g", "scroll_home", show=False),
        Binding("G", "scroll_end", show=False),
        Binding("l", "open", show=False),
        Binding("right", "open", show=False),
        Binding("h", "close", show=False),
        Binding("left", "close", show=False),
    ]

    def action_open(self) -> None:
        node = self.cursor_node
        if node is None:
            return
        already_shown = self.app.open_folder(node)
        if node.allow_expand and not node.is_expanded:
            node.expand()
        elif already_shown:
            self.app.query_one(TrackTable).focus()

    def action_close(self) -> None:
        node = self.cursor_node
        if node is None:
            return
        if node.is_expanded and node.children:  # sem subpastas, fechar não muda nada: sobe
            node.collapse()
        elif node.parent is not None and node.parent is not self.root:
            self.move_cursor(node.parent)


class TrackTable(DataTable):
    BINDINGS = [
        Binding("j", "cursor_down", show=False),
        Binding("k", "cursor_up", show=False),
        Binding("g", "first", show=False),
        Binding("G", "last", show=False),
        Binding("l", "select_cursor", show=False),
        Binding("h", "back", show=False),
    ]

    def action_first(self) -> None:
        self.move_cursor(row=0)

    def action_last(self) -> None:
        self.move_cursor(row=max(self.row_count - 1, 0))

    def action_back(self) -> None:
        self.app.query_one(FolderTree).focus()


class HelpScreen(ModalScreen):
    BINDINGS = [Binding("escape,question_mark,q", "dismiss", show=False)]
    DEFAULT_CSS = """
    HelpScreen { align: center middle; }
    HelpScreen > Static { width: auto; height: auto; padding: 1 3; border: round $accent;
                          background: $surface; }
    """

    def compose(self) -> ComposeResult:
        yield Static(HELP_TEXT)


# --- app ----------------------------------------------------------------------

class NimbusApp(App):
    TITLE = "nimbus"
    CSS = """
    Screen { layout: vertical; }
    #playback { height: 11; border: round $primary; border-title-color: $text;
                padding: 0 1; }
    #pb-top { height: 7; }
    #pb-info { padding-left: 2; height: 7; }
    #pb-title { color: #94e2d5; text-style: bold; }
    #pb-album { color: #fab387; }
    #pb-state { color: $text-muted; }
    #pb-source { color: $text-muted; }
    #pb-keys { color: $text-muted; }
    #pb-progress { height: 1; margin-top: 1; }
    #browser { height: 1fr; }
    #tree { width: 35%; border: round $primary; }
    #tracks { width: 1fr; height: 100%; border: round $primary; }
    #tree { height: 100%; }
    #tree:focus, #tracks:focus { border: round $accent; }
    """

    BINDINGS = [
        Binding("space", "toggle_pause", "play/pause", priority=True),
        Binding("n", "next", "próxima", priority=True),
        Binding("p", "prev", "anterior", priority=True),
        Binding("greater_than_sign,full_stop", "seek(10)", "+10s", priority=True),
        Binding("less_than_sign,comma", "seek(-10)", "-10s", priority=True),
        Binding("plus,equals_sign", "volume(5)", "vol+", priority=True),
        Binding("minus,underscore", "volume(-5)", "vol-", priority=True),
        Binding("m", "mute", "mudo", priority=True),
        Binding("r", "repeat", "repeat", priority=True),
        Binding("s", "shuffle", "shuffle", priority=True),
        Binding("question_mark", "help", "ajuda"),
        Binding("q", "quit", "sair"),
    ]

    def __init__(
        self,
        drive,
        player,
        token: Callable[[], str],
        resolver: MetadataResolver,
        start_folder: Optional[DriveItem] = None,
        image_factory: Optional[Callable] = None,
        is_remote: Optional[Callable[[DriveItem], bool]] = None,
    ):
        super().__init__()
        # Pastas remotas (Drive, e no futuro OneDrive) só são listadas no painel
        # da direita com Enter ou →, para não gastar banda nem chamadas de API
        # a cada passo na árvore. Pastas locais aparecem enquanto se navega.
        self.is_remote = is_remote or (lambda item: not is_local(item))
        self._opened: set[str] = set()  # pastas remotas que o usuário pediu para abrir
        self.drive = drive
        self.player = player
        self.token = token
        self.resolver = resolver
        self.start_folder = start_folder
        self.image_factory = image_factory

        self._drive_lock = threading.Lock()  # o cliente da API do Google não é thread-safe
        self._listings: dict[str, list[DriveItem]] = {}
        self._loaded_nodes: set[int] = set()
        self._loading_nodes: set[int] = set()
        self._select_after_load: Optional[tuple] = None  # (id do nó pai, id da subpasta)
        self._errors: queue.Queue[str] = queue.Queue()
        self._shown_folder: Optional[str] = None
        self._show_timer = None

        self._ctl_lock = threading.RLock()
        self.ctl: Optional[Controller] = None
        self.repeat = "off"
        self.shuffle = False
        self.play_folder: Optional[DriveItem] = None
        self.play_siblings: list[DriveItem] = []

        self.state: dict = {"pause": False, "volume": None, "mute": False, "time-pos": None, "duration": None}
        self.info = TrackInfo()
        self.cover_source = ""
        self.online_note = ""
        self.playing_id: Optional[str] = None
        self._marked_id: Optional[str] = None
        self._gen = 0
        self._pending_cover = None  # (gen, PIL.Image | None)
        self._stop = threading.Event()
        self._events: Optional[threading.Thread] = None

    # layout

    def compose(self) -> ComposeResult:
        with Vertical(id="playback"):
            with Horizontal(id="pb-top"):
                yield CoverArt(self.image_factory, id="cover")
                with Vertical(id="pb-info"):
                    yield Static(id="pb-title")
                    yield Static(id="pb-album")
                    yield Static(id="pb-state")
                    yield Static(id="pb-source")
                    yield Static(KEYS_LINE, id="pb-keys")
            yield ProgressLine(id="pb-progress")
        with Horizontal(id="browser"):
            yield FolderTree("Drive", id="tree")
            yield TrackTable(id="tracks", cursor_type="row", zebra_stripes=False)

    def on_mount(self) -> None:
        self.query_one("#playback").border_title = "Playback"
        tree = self.query_one(FolderTree)
        tree.border_title = "Directories"
        tree.show_root = False
        tree.guide_depth = 2
        table = self.query_one(TrackTable)
        table.add_column(" ", key="mark", width=1)
        table.add_column("Nome", key="name")
        table.add_column("Tamanho", key="size")
        table.border_title = "—"

        roots = []
        explicit_start = self.start_folder is not None and self.start_folder.id not in ("root",)
        if explicit_start:
            roots.append(self.start_folder)
        if getattr(self.drive, "has_drive", True):
            roots += [DriveItem(id="root", name="Meu Drive", mime_type=FOLDER_MIME), shared_with_me()]
        roots.append(local_root())
        nodes = [tree.root.add(self._folder_label(item), data=item, allow_expand=True) for item in roots]
        tree.root.expand()
        if explicit_start:  # "Meu Drive" e as demais raízes começam fechadas (issue #9)
            nodes[0].expand()
        tree.move_cursor(nodes[0])
        tree.focus()
        self.open_folder(nodes[0])  # o cursor já está na linha 0: não há evento de destaque
        if not getattr(self.drive, "has_drive", True):
            self.notify("Sem login no Google: só as músicas deste computador. Para ver o Drive, rode "
                        "'nimbus login'.", timeout=8)

        try:
            self.player.observe("pause", "volume", "mute", "time-pos", "duration")
        except Exception:
            pass
        self._events = threading.Thread(target=self._event_loop, name="nimbus-events", daemon=True)
        self._events.start()
        self.set_interval(0.25, self._tick)
        self._tick()

    def on_unmount(self) -> None:
        self._stop.set()

    @staticmethod
    def _folder_label(item: DriveItem) -> Text:
        return Text(("💻 " if item.id == LOCAL_ROOT else "📁 ") + item.name)

    # Drive e disco

    def list_folder(self, folder_id: str) -> list[DriveItem]:
        """Lista uma pasta (com cache). Chamar só fora da thread da interface."""
        cached = self._listings.get(folder_id)
        if cached is not None:
            return cached
        with self._drive_lock:
            if folder_id not in self._listings:  # outra thread pode ter acabado de listar
                self._listings[folder_id] = list(self.drive.list_children(folder_id))
        return self._listings[folder_id]

    def _error(self, message: str) -> None:
        """Pode ser chamada de qualquer thread; a mensagem aparece no próximo _tick."""
        self._errors.put(message)

    # árvore

    @on(Tree.NodeExpanded)
    def _on_expand(self, event: Tree.NodeExpanded) -> None:
        self._load_node(event.node)

    def _load_node(self, node: TreeNode) -> None:
        if node.data is None or node.id in self._loaded_nodes or node.id in self._loading_nodes:
            return
        cached = self._listings.get(node.data.id)
        if cached is not None:
            self._fill_node(node, cached)
            return
        self._loading_nodes.add(node.id)
        node.set_label(Text("📂 " + node.data.name + "  …"))
        self.run_worker(lambda: self._load_children(node), thread=True, group=f"node-{node.id}")

    def _load_children(self, node: TreeNode) -> None:
        try:
            items = self.list_folder(node.data.id)
        except Exception as e:
            self._loading_nodes.discard(node.id)
            self.call_from_thread(node.set_label, self._folder_label(node.data))
            self._error(f"Não consegui abrir {node.data.name}: {e}")
            return
        self.call_from_thread(self._fill_node, node, items)

    def _fill_node(self, node: TreeNode, items: list[DriveItem]) -> None:
        self._loading_nodes.discard(node.id)
        self._loaded_nodes.add(node.id)
        node.set_label(self._folder_label(node.data))
        node.remove_children()
        folders = [i for i in items if i.is_folder]
        for item in folders:
            node.add(self._folder_label(item), data=item, allow_expand=True)
        if not folders:
            node.allow_expand = False
        pending, self._select_after_load = self._select_after_load, None
        if pending is not None and pending[0] == node.id:
            self._move_to_child(node, pending[1])

    @on(Tree.NodeHighlighted)
    def _on_highlight(self, event: Tree.NodeHighlighted) -> None:
        if event.node.data is None:
            return
        if self._show_timer is not None:
            self._show_timer.stop()
        node = event.node
        self._show_timer = self.set_timer(0.15, lambda: self._preview_folder(node))

    @on(Tree.NodeSelected)
    def _on_select(self, event: Tree.NodeSelected) -> None:
        if event.node.data is not None:
            self.open_folder(event.node)

    def open_folder(self, node: TreeNode) -> bool:
        """Enter ou →: mostra o conteúdo da pasta. Devolve True se ele já estava na tela."""
        item: DriveItem = node.data
        if item is None:
            return False
        self._opened.add(item.id)
        if self._shown_folder == item.id:
            return True
        self._show_folder(node)
        return False

    def _preview_folder(self, node: TreeNode) -> None:
        """Cursor parou numa pasta: local mostra já; remota só se já foi listada ou aberta."""
        if not self.is_running or not self.screen.query(TrackTable):
            return  # o timer disparou com a interface fechando
        item: DriveItem = node.data
        if not self.is_remote(item) or item.id in self._listings or item.id in self._opened:
            self._show_folder(node)
            return
        table = self.query_one(TrackTable)
        table.border_title = self._node_path(node)
        table.clear()
        table.add_row("", Text("Enter ou → para abrir esta pasta", style="dim italic"), "")
        self._shown_folder = None

    def _show_folder(self, node: TreeNode) -> None:
        item: DriveItem = node.data
        table = self.query_one(TrackTable)
        table.border_title = self._node_path(node)
        if item.id in self._listings:
            self._fill_table(item.id, self._listings[item.id])
            return
        table.clear()
        table.add_row("", Text("carregando…", style="dim"), "")
        self._shown_folder = item.id
        self.run_worker(lambda: self._load_table(item), thread=True, exclusive=True, group="table")

    def _load_table(self, item: DriveItem) -> None:
        try:
            items = self.list_folder(item.id)
        except Exception as e:
            self._error(f"Não consegui listar {item.name}: {e}")
            return
        self.call_from_thread(self._fill_table, item.id, items)

    @staticmethod
    def _node_path(node: TreeNode) -> str:
        parts = []
        while node is not None and node.data is not None:
            parts.append(node.data.name)
            node = node.parent
        return " / ".join(reversed(parts))

    def _current_tree_folder(self) -> Optional[TreeNode]:
        node = self.query_one(FolderTree).cursor_node
        return node if node is not None and node.data is not None else None

    def _fill_table(self, folder_id: str, items: list[DriveItem]) -> None:
        node = self._current_tree_folder()
        if node is None or node.data.id != folder_id:
            return  # o usuário já foi para outra pasta
        self._shown_folder = folder_id
        table = self.query_one(TrackTable)
        table.clear()
        shown = [i for i in items if i.is_folder or i.is_audio]
        for item in shown:
            if item.is_folder:
                name = Text("📁 " + item.name, style="bold")
                size = ""
            else:
                name = Text("♪  " + item.name)
                size = fmt_size(item.size)
            mark = Text("▶", style="bold #a6e3a1") if item.id == self.playing_id else ""
            table.add_row(mark, name, size, key=item.id)
        if not shown:
            table.add_row("", Text("(nenhuma pasta ou música)", style="dim"), "")
        self._marked_id = self.playing_id

    @on(DataTable.RowSelected)
    def _on_row(self, event: DataTable.RowSelected) -> None:
        node = self._current_tree_folder()
        if node is None or event.row_key.value is None:
            return
        items = self._listings.get(node.data.id, [])
        chosen = next((i for i in items if i.id == event.row_key.value), None)
        if chosen is None:
            return
        if chosen.is_folder:
            self._enter_subfolder(node, chosen)
        elif chosen.is_audio:
            audio = [i for i in items if i.is_audio]
            self.start_playback(node.data, items, audio, audio.index(chosen))

    def _enter_subfolder(self, node: TreeNode, folder: DriveItem) -> None:
        self._load_node(node)
        node.expand()
        if node.id in self._loaded_nodes:
            self._move_to_child(node, folder.id)
        else:
            self._select_after_load = (node.id, folder.id)

    def _move_to_child(self, node: TreeNode, folder_id: str) -> None:
        # Nós recém-criados só ganham linha (node._line) depois que a árvore se
        # redesenha; mover antes disso jogaria o cursor para o topo.
        self.call_after_refresh(self._move_to_child_now, node, folder_id)

    def _move_to_child_now(self, node: TreeNode, folder_id: str) -> None:
        tree = self.query_one(FolderTree)
        for child in node.children:
            if child.data is not None and child.data.id == folder_id:
                self._opened.add(folder_id)  # Enter na lista é pedido explícito
                tree.move_cursor(child)
                tree.scroll_to_node(child)
                return

    # reprodução

    def start_playback(self, folder: DriveItem, siblings: list[DriveItem], audio: list[DriveItem], index: int) -> None:
        self.play_folder, self.play_siblings = folder, siblings
        item = audio[index]
        self._new_track(item)

        def run() -> None:
            with self._ctl_lock:
                q = PlayQueue(audio, start=index)
                q.repeat = self.repeat
                if self.shuffle:
                    q.set_shuffle(True)
                self.ctl = Controller(self.player, q, self.token)
                try:
                    self.ctl.play_current()
                except Exception as e:
                    self._error(f"Erro ao tocar {item.name}: {e}")

        self.run_worker(run, thread=True, group="control")

    def _new_track(self, item: Optional[DriveItem]) -> None:
        """Mostra já o que dá para saber pelo nome do arquivo; tags e capa chegam depois."""
        self._gen += 1
        self.playing_id = item.id if item else None
        if item is not None:
            self.info = self.resolver.basic(item, None, self.play_folder.name if self.play_folder else "")
        self.cover_source = ""
        self.online_note = ""
        self._pending_cover = (self._gen, None)

    def _control(self, fn: Callable[[Controller], None], changes_track: bool = False) -> None:
        def run() -> None:
            with self._ctl_lock:
                if self.ctl is None:
                    return
                try:
                    fn(self.ctl)
                except Exception as e:
                    self._error(str(e))
                current = self.ctl.queue.current
            if changes_track:  # fora do lock: call_from_thread espera a thread da interface
                self.call_from_thread(self._new_track, current)

        self.run_worker(run, thread=True, group="control")

    def action_toggle_pause(self) -> None:
        if self.ctl is None:
            return
        if self.ctl.finished:
            self._control(lambda c: c.play_current(), changes_track=True)
        else:
            self._control(lambda c: c.player.toggle_pause())

    def action_next(self) -> None:
        self._control(lambda c: c.next(), changes_track=True)

    def action_prev(self) -> None:
        self._control(lambda c: c.prev(), changes_track=True)

    def action_seek(self, seconds: int) -> None:
        self._control(lambda c: c.player.seek(seconds))

    def action_volume(self, delta: int) -> None:
        self._control(lambda c: c.player.add_volume(delta))

    def action_mute(self) -> None:
        self._control(lambda c: c.player.command("cycle", "mute"))

    def action_repeat(self) -> None:
        modes = ("off", "all", "one")
        self.repeat = modes[(modes.index(self.repeat) + 1) % len(modes)]
        with self._ctl_lock:
            if self.ctl is not None:
                self.ctl.queue.repeat = self.repeat
        self._tick()

    def action_shuffle(self) -> None:
        self.shuffle = not self.shuffle
        with self._ctl_lock:
            if self.ctl is not None:
                self.ctl.queue.set_shuffle(self.shuffle)
        self._tick()

    def action_help(self) -> None:
        self.push_screen(HelpScreen())

    # eventos do mpv (thread própria)

    def _event_loop(self) -> None:
        while not self._stop.is_set():
            try:
                event = self.player.events.get(timeout=0.3)
            except queue.Empty:
                continue
            name = event.get("event")
            if name == "property-change":
                self.state[event.get("name")] = event.get("data")
                continue
            if name == "nimbus-mpv-exited":
                self.state["exited"] = True
                self._error("O mpv encerrou.")
                return
            with self._ctl_lock:
                ctl = self.ctl
                if ctl is None:
                    continue
                try:
                    changed = ctl.handle_event(event)
                except Exception as e:
                    self._error(str(e))
                    changed = False
                error, ctl.last_error = ctl.last_error, None
                current = ctl.queue.current
            if error:
                self._error(f"Erro ao tocar {error}")
            if changed:
                self.call_from_thread(self._new_track, current)
            if name == "file-loaded":
                self._resolve_metadata()

    def _resolve_metadata(self) -> None:
        path = self.player.get_property("path") or ""
        m = _ID_IN_URL.search(path)
        with self._ctl_lock:
            tracks = list(self.ctl.queue.tracks) if self.ctl else []
        if m:
            item = next((t for t in tracks if t.id == m.group(1)), None)
        else:  # arquivo local: o mpv devolve o próprio caminho
            item = next((t for t in tracks if is_local(t) and local_path(t) == path), None)
        if item is None:
            return
        gen = self._gen
        folder_name = self.play_folder.name if self.play_folder else ""
        siblings = list(self.play_siblings)
        tags = self.player.get_property("metadata")

        def run() -> None:
            basic = self.resolver.basic(item, tags, folder_name)
            if gen != self._gen:
                return
            self.info = basic
            try:
                resolved = self.resolver.resolve(item, tags, siblings, folder_name)
            except Exception as e:
                self._error(f"Metadados: {e}")
                return
            image = None
            if resolved.cover:
                try:
                    from PIL import Image

                    image = Image.open(io.BytesIO(resolved.cover))
                    image.load()
                    image = image.convert("RGB")
                except Exception:
                    image = None
            if gen != self._gen:
                return
            self.info = resolved.info
            self.cover_source = resolved.cover_source if image is not None else ""
            self.online_note = resolved.note
            self._pending_cover = (gen, image)

        threading.Thread(target=run, name="nimbus-meta", daemon=True).start()

    # tela

    def _tick(self) -> None:
        if not self.is_mounted:
            return
        while True:
            try:
                self.notify(self._errors.get_nowait(), severity="error", timeout=6)
            except queue.Empty:
                break
        state = self.state
        ctl = self.ctl
        info = self.info
        finished = ctl is None or ctl.finished
        if self.playing_id is None:
            icon = "■"
        elif finished:
            icon = "■"
        else:
            icon = "❚❚" if state.get("pause") else "▶"

        title = Text(f"{icon}  ", style="bold #a6e3a1")
        if self.playing_id is None:
            title.append("nada tocando", style="dim")
        else:
            title.append(info.title or "?")
            if info.artist:
                title.append(" • " + info.artist)
        self.query_one("#pb-title", Static).update(title)

        album = Text()
        if self.playing_id is not None:
            album.append(info.album or "álbum desconhecido", style="" if info.album else "italic dim")
            album.append(" • ")
            album.append(info.genre or "sem gênero", style="" if info.genre else "italic dim")
            if info.year:
                album.append(" • " + info.year)
        else:
            album.append("Escolha uma música embaixo e aperte Enter", style="dim")
        self.query_one("#pb-album", Static).update(album)

        vol = state.get("volume")
        vol_text = "mudo" if state.get("mute") else (f"{vol:.0f}%" if isinstance(vol, (int, float)) else "--")
        parts = [f"repeat: {self.repeat}", f"shuffle: {'on' if self.shuffle else 'off'}", f"volume: {vol_text}"]
        if ctl is not None and len(ctl.queue):
            parts.append(f"faixa {ctl.queue.index + 1}/{len(ctl.queue)}")
            if ctl.finished:
                parts.append("fim da fila")
        if self.play_folder is not None:
            parts.append(f"pasta: {self.play_folder.name}")
        self.query_one("#pb-state", Static).update(Text(" | ".join(parts)))

        sources = [s for s in info.sources] if self.playing_id else []
        src = ""
        if sources:
            src = "dados: " + "+".join(sources)
        if self.cover_source:
            src += ("  ·  " if src else "") + "capa: " + self.cover_source
        if self.online_note and self.playing_id:
            src += ("  ·  " if src else "") + self.online_note
        self.query_one("#pb-source", Static).update(Text(src))

        pos = state.get("time-pos") if self.playing_id and not finished else None
        dur = state.get("duration") if self.playing_id and not finished else None
        self.query_one(ProgressLine).set_progress(pos, dur)

        pending, self._pending_cover = self._pending_cover, None
        if pending is not None and pending[0] == self._gen:
            self.query_one(CoverArt).show(pending[1])

        if self.playing_id != self._marked_id:
            self._update_marks()

    def _update_marks(self) -> None:
        table = self.query_one(TrackTable)
        keys = {r.value for r in table.rows}
        if self._marked_id in keys:
            table.update_cell(self._marked_id, "mark", "")
        if self.playing_id in keys:
            table.update_cell(self.playing_id, "mark", Text("▶", style="bold #a6e3a1"))
        self._marked_id = self.playing_id


class BlockImage(Widget):
    """Capa desenhada com meios-blocos (▀) coloridos: funciona em qualquer terminal com cores."""

    def __init__(self, image, **kw):
        super().__init__(**kw)
        self._image = image.convert("RGB")
        self._cache: tuple = ((0, 0), None)

    def render(self) -> Text:
        w, h = self.size.width, self.size.height
        if self._cache[0] != (w, h):
            self._cache = ((w, h), self._draw(w, h))
        return self._cache[1]

    def _draw(self, w: int, h: int) -> Text:
        text = Text()
        if w <= 0 or h <= 0:
            return text
        from PIL import Image

        img = self._image.resize((w, h * 2), Image.LANCZOS)
        px = img.load()
        for y in range(h):
            for x in range(w):
                top, bottom = px[x, 2 * y], px[x, 2 * y + 1]
                text.append("▀", f"rgb({top[0]},{top[1]},{top[2]}) on rgb({bottom[0]},{bottom[1]},{bottom[2]})")
            if y < h - 1:
                text.append("\n")
        return text


def image_factory_from_env() -> Optional[Callable]:
    """Escolhe como desenhar a capa pela variável NIMBUS_COVER.

    blocks (padrão): meios-blocos coloridos, funciona em qualquer terminal.
    auto: deixa o textual-image escolher Sixel ou o protocolo do kitty, se o
          terminal disser que aceita. Alguns terminais dizem que aceitam e
          mostram lixo, por isso não é o padrão.
    sixel, kitty: força um desses protocolos.
    off: sem capa.

    Precisa rodar antes do Textual assumir o terminal, porque o textual-image
    consulta o terminal ao ser importado.
    """
    mode = os.environ.get("NIMBUS_COVER", "blocks").strip().lower()
    if mode in ("off", "none", "0"):
        return None
    if mode not in ("auto", "sixel", "kitty", "tgp"):
        return BlockImage
    try:
        if mode == "sixel":
            from textual_image.widget import SixelImage as ImageWidget
        elif mode in ("kitty", "tgp"):
            from textual_image.widget import TGPImage as ImageWidget
        else:
            from textual_image.widget import Image as ImageWidget
    except Exception:
        return BlockImage
    return ImageWidget
