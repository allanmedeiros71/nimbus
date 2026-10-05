"""Autocomplete de caminhos de pastas do Drive e do computador para zsh e bash.

O shell chama `nimbus _complete <texto digitado>` a cada Tab e recebe um
caminho candidato por linha. As listagens ficam num cache curto em disco para
que o Tab responda rápido e não consulte a API toda vez.
"""

from __future__ import annotations

import json
import os
import time
import unicodedata
from pathlib import Path

from nimbus.drive import SHARED_NAME, Drive
from nimbus.local import looks_local

CACHE_TTL = 300  # segundos


def cache_path() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache"
    return Path(base).expanduser() / "nimbus" / "complete.json"


def _norm(s: str) -> str:
    # O terminal do macOS pode mandar acentos decompostos (NFD).
    return unicodedata.normalize("NFC", s).casefold()


class FolderCache:
    def __init__(self, path: Path | None = None, ttl: float = CACHE_TTL):
        self.path = path or cache_path()
        self.ttl = ttl
        try:
            self.data = json.loads(self.path.read_text())
        except (OSError, ValueError):
            self.data = {}

    def get(self, key: str) -> list[str] | None:
        entry = self.data.get(key)
        if entry and time.time() - entry.get("t", 0) < self.ttl:
            return entry.get("names")
        return None

    def put(self, key: str, names: list[str]) -> None:
        now = time.time()
        self.data = {k: v for k, v in self.data.items() if now - v.get("t", 0) < self.ttl}
        self.data[key] = {"t": now, "names": names}
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.data))
            os.replace(tmp, self.path)
        except OSError:
            pass


def complete_local(partial: str) -> list[str]:
    """Pastas do computador; mantém o '~' ou o caminho relativo como foi digitado."""
    if partial in ("~", ".", ".."):
        return [partial + "/"]
    head, _, prefix = partial.rpartition("/")
    head = head + "/" if head or partial.startswith("/") else ""
    base = os.path.expanduser(head) if head else "."
    wanted = _norm(prefix)
    try:
        entries = sorted(os.scandir(base), key=lambda e: _norm(e.name))
    except OSError:
        return []
    out = []
    for entry in entries:
        if entry.name.startswith(".") and not prefix.startswith("."):
            continue
        try:
            if not entry.is_dir():
                continue
        except OSError:
            continue
        if _norm(entry.name).startswith(wanted):
            out.append(f"{head}{entry.name}/")
    return out


def complete(drive: Drive | None, partial: str, cache: FolderCache | None = None) -> list[str]:
    """Caminhos de pastas que começam com `partial`, cada um terminando em '/'."""
    partial = unicodedata.normalize("NFC", partial)
    if looks_local(partial):
        return complete_local(partial)
    parent, _, prefix = partial.rpartition("/")
    key = _norm(parent.strip("/"))
    names = cache.get(key) if cache else None
    if names is None:
        folder = drive.resolve_folder(parent) if parent.strip("/") else None
        folder_id = folder.id if folder else "root"
        names = [i.name for i in drive.iter_children(folder_id) if i.is_folder]
        if folder is None:
            names.insert(0, SHARED_NAME)
        if cache:
            cache.put(key, names)
    head = parent + "/" if parent else ""
    wanted = _norm(prefix)
    return [f"{head}{n}/" for n in names if _norm(n).startswith(wanted)]


ZSH_SCRIPT = r"""#compdef nimbus
# Autocomplete do nimbus para zsh. Ative com: eval "$(nimbus completion zsh)"
_nimbus() {
  local -a cmds items
  cmds=(
    'login:autoriza o acesso somente leitura ao Drive'
    'logout:apaga o token salvo'
    'ls:lista pastas e músicas'
    'play:toca as músicas de uma pasta'
    'completion:imprime o script de autocomplete'
  )
  if (( CURRENT == 2 )); then
    _describe 'comando' cmds
    return
  fi
  case $words[2] in
    ls|play)
      if [[ $PREFIX == -* ]]; then
        compadd -- -r --recursive -s --shuffle -a --all
      else
        items=("${(@f)$(nimbus _complete -- "${(Q)PREFIX}" 2>/dev/null)}")
        items=(${items:#})
        (( ${#items} )) && compadd -U -S '' -- "${items[@]}"
      fi
      ;;
    completion)
      compadd zsh bash
      ;;
  esac
}
if (( $+functions[compdef] )); then
  compdef _nimbus nimbus
else
  print -u2 "nimbus: rode 'autoload -Uz compinit && compinit' antes de ativar o autocomplete"
fi
"""

BASH_SCRIPT = r"""# Autocomplete do nimbus para bash. Ative com: eval "$(nimbus completion bash)"
_nimbus() {
  local cur=${COMP_WORDS[COMP_CWORD]}
  COMPREPLY=()
  if (( COMP_CWORD == 1 )); then
    COMPREPLY=($(compgen -W "login logout ls play completion" -- "$cur"))
    return
  fi
  case ${COMP_WORDS[1]} in
    ls|play)
      if [[ $cur == -* ]]; then
        COMPREPLY=($(compgen -W "-r --recursive -s --shuffle -a --all" -- "$cur"))
        return
      fi
      # Tira aspas e barras invertidas do que foi digitado.
      local typed=${cur#[\"\']}
      typed=${typed//\\/}
      local IFS=$'\n'
      COMPREPLY=($(nimbus _complete -- "$typed" 2>/dev/null))
      ;;
    completion)
      COMPREPLY=($(compgen -W "zsh bash" -- "$cur"))
      ;;
  esac
}
complete -o filenames -o nospace -F _nimbus nimbus
"""

SCRIPTS = {"zsh": ZSH_SCRIPT, "bash": BASH_SCRIPT}
