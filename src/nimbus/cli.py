"""CLI mínima: login, listar pastas e tocar uma pasta."""

from __future__ import annotations

import argparse
import contextlib
import os
import queue
import select
import shutil
import sys
import time
from pathlib import Path

from nimbus import __version__, auth
from nimbus.drive import SHARED_NAME, Drive, DriveError
from nimbus.player import MpvError, MpvPlayer
from nimbus.playback import Controller, PlayQueue

HELP_KEYS = "espaço pausa · n próxima · p anterior · ←/→ 10s · +/- volume · q sair"


def _fmt_time(seconds: float | None) -> str:
    if seconds is None:
        return "--:--"
    s = int(seconds)
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60:02d}:{s % 60:02d}"


def _fmt_size(size: int | None) -> str:
    if size is None:
        return ""
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return ""


def _drive():
    creds = auth.load_credentials()
    return Drive.from_credentials(creds), creds


def cmd_login(args) -> int:
    creds = auth.login(args.client_secret)
    print(f"Login feito como {Drive.from_credentials(creds).user_email()}.")
    return 0


def cmd_logout(args) -> int:
    print("Token removido." if auth.logout() else "Nenhum login salvo.")
    return 0


def cmd_ls(args) -> int:
    drive, _ = _drive()
    folder = drive.resolve_folder(args.folder)
    print(f"{folder.name}  ({folder.id})")
    shown = 0
    if folder.id == "root":
        print(f"  📁 {SHARED_NAME}/")
        shown += 1
    for item in drive.iter_children(folder.id):
        if item.is_folder:
            print(f"  📁 {item.name}/")
        elif item.is_audio:
            print(f"  ♪  {item.name}  {_fmt_size(item.size)}")
        elif args.all:
            print(f"     {item.name}")
        else:
            continue
        shown += 1
    if not shown:
        print("  (nenhuma pasta ou música)")
    return 0


@contextlib.contextmanager
def _raw_terminal():
    """Lê teclas sem Enter (Linux e macOS). Fora de um terminal, não faz nada."""
    if not sys.stdin.isatty():
        yield False
        return
    import termios
    import tty

    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        yield True
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def _read_key(timeout: float) -> str | None:
    ready, _, _ = select.select([sys.stdin], [], [], timeout)
    if not ready:
        return None
    ch = os.read(sys.stdin.fileno(), 1).decode(errors="ignore")
    if ch == "\x1b":
        ready, _, _ = select.select([sys.stdin], [], [], 0.05)
        if ready:
            seq = os.read(sys.stdin.fileno(), 2).decode(errors="ignore")
            return {"[C": "right", "[D": "left", "[A": "up", "[B": "down"}.get(seq)
        return "esc"
    return ch


def _status_line(ctl: Controller, paused: bool) -> str:
    item = ctl.queue.current
    if item is None:
        return ""
    pos, dur = ctl.player.position()
    icon = "⏸" if paused else "▶"
    line = f"{icon} {ctl.queue.index + 1}/{len(ctl.queue)}  {item.name}  {_fmt_time(pos)} / {_fmt_time(dur)}"
    width = shutil.get_terminal_size((80, 20)).columns - 1
    return line[:width].ljust(width)


def cmd_play(args) -> int:
    drive, creds = _drive()
    folder = drive.resolve_folder(args.folder)
    print(f"Lendo {folder.name}…", file=sys.stderr)
    tracks = drive.list_audio(folder.id, recursive=args.recursive)
    if not tracks:
        print("Nenhum arquivo de áudio nessa pasta." + ("" if args.recursive else " Tente --recursive."))
        return 1
    play_queue = PlayQueue(tracks, shuffle=args.shuffle)

    with MpvPlayer() as player:
        ctl = Controller(player, play_queue, lambda: auth.access_token(creds))
        print(f"{len(tracks)} faixas. {HELP_KEYS}", file=sys.stderr)
        ctl.play_current()
        paused = False
        with _raw_terminal() as interactive:
            try:
                while not ctl.finished:
                    while True:
                        try:
                            event = player.events.get_nowait()
                        except queue.Empty:
                            break
                        if ctl.handle_event(event):
                            paused = False
                        if ctl.last_error:
                            sys.stderr.write(f"\r\033[KErro ao tocar {ctl.last_error}\n")
                            ctl.last_error = None
                    if ctl.finished:
                        break
                    sys.stderr.write("\r" + _status_line(ctl, paused))
                    sys.stderr.flush()
                    if interactive:
                        key = _read_key(0.5)
                    else:
                        time.sleep(0.5)
                        key = None
                    if key in ("q", "Q", "esc"):
                        break
                    elif key == " ":
                        paused = player.toggle_pause()
                    elif key in ("n", "N"):
                        paused = False
                        ctl.next()
                    elif key in ("p", "P"):
                        paused = False
                        ctl.prev()
                    elif key == "right":
                        player.seek(10)
                    elif key == "left":
                        player.seek(-10)
                    elif key in ("+", "=", "up"):
                        player.add_volume(5)
                    elif key in ("-", "_", "down"):
                        player.add_volume(-5)
            except KeyboardInterrupt:
                pass
        sys.stderr.write("\r\033[K")
    return 0


def cmd_completion(args) -> int:
    from nimbus.completion import SCRIPTS

    sys.stdout.write(SCRIPTS[args.shell])
    return 0


def cmd_complete(args) -> int:
    """Usado pelos scripts de autocomplete; falhas não podem sujar o terminal."""
    from nimbus.completion import FolderCache, complete

    try:
        drive, _ = _drive()
        for path in complete(drive, args.partial, FolderCache()):
            print(path)
    except Exception:
        pass
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="nimbus", description="Toca em streaming as músicas das suas pastas do Google Drive.")
    p.add_argument("--version", action="version", version=f"nimbus {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True, metavar="{login,logout,ls,play,completion}")

    s = sub.add_parser("login", help="autoriza o acesso somente leitura ao seu Drive")
    s.add_argument("--client-secret", type=lambda v: Path(v).expanduser(),
                   help="JSON do OAuth client (padrão: ~/.config/nimbus/client_secret.json)")
    s.set_defaults(func=cmd_login)

    s = sub.add_parser("logout", help="apaga o token salvo")
    s.set_defaults(func=cmd_logout)

    folder_help = ("caminho em Meu Drive (ex.: 'Música/Rock') ou em 'Compartilhados comigo/…', "
                   "URL ou ID da pasta; vazio = raiz")
    s = sub.add_parser("ls", help="lista pastas e músicas")
    s.add_argument("folder", nargs="?", default="", help=folder_help)
    s.add_argument("-a", "--all", action="store_true", help="mostra também arquivos que não são áudio")
    s.set_defaults(func=cmd_ls)

    s = sub.add_parser("play", help="toca as músicas de uma pasta")
    s.add_argument("folder", help=folder_help)
    s.add_argument("-r", "--recursive", action="store_true", help="inclui subpastas")
    s.add_argument("-s", "--shuffle", action="store_true", help="ordem aleatória")
    s.set_defaults(func=cmd_play)

    s = sub.add_parser("completion", help="imprime o script de autocomplete (zsh ou bash)")
    s.add_argument("shell", choices=["zsh", "bash"])
    s.set_defaults(func=cmd_completion)

    s = sub.add_parser("_complete")
    s.add_argument("partial", nargs="?", default="")
    s.set_defaults(func=cmd_complete)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (auth.AuthError, DriveError, MpvError) as e:
        print(f"nimbus: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
