#!/usr/bin/env python3
"""Gera a fórmula do Homebrew (Formula/nimbus.rb) para uma versão publicada no PyPI.

Uso: python scripts/homebrew_formula.py 0.3.0 > nimbus.rb

Resolve as dependências com o pip (sem instalar nada) e escreve um bloco
`resource` por pacote, apontando para o sdist no PyPI. Pacotes que o Homebrew
já oferece como fórmula (com partes compiladas) viram `depends_on`.

O Homebrew compila cada resource a partir do código-fonte, inclusive o backend
de build. Backends escritos em Rust (uv_build, maturin) não compilam ali, então
esses pacotes, quando são Python puro, entram pelo wheel `py3-none-any`, que o
Homebrew instala direto.
"""

from __future__ import annotations

import io
import json
import re
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
from pathlib import Path

PACKAGE = "nimbus-player"
PYTHON = "python@3.14"

# Backends de build que precisam de Rust para serem compilados.
RUST_BACKENDS = {"uv_build", "maturin"}

# Pacotes do PyPI que vêm de fórmulas do Homebrew em vez de resources.
FROM_HOMEBREW = {
    "certifi": "certifi",
    "cffi": "cryptography",
    "cryptography": "cryptography",
    "pillow": "pillow",
    "pycparser": "cryptography",
}

TEMPLATE = """\
class Nimbus < Formula
  include Language::Python::Virtualenv

  desc "Terminal music player that streams your Google Drive folders"
  homepage "https://github.com/allanmedeiros71/nimbus"
  url "{url}"
  sha256 "{sha256}"
  license "MIT"

{depends}
{resources}
  def install
    virtualenv_install_with_resources
  end

  test do
    assert_match version.to_s, shell_output("#{{bin}}/nimbus --version")
  end
end
"""


def normalize(name: str) -> str:
    return name.lower().replace("_", "-").replace(".", "-")


def files(name: str, version: str) -> list[dict]:
    with urllib.request.urlopen(f"https://pypi.org/pypi/{name}/{version}/json") as r:
        return json.load(r)["urls"]


def sdist(name: str, version: str) -> tuple[str, str]:
    """URL e sha256 do sdist de name==version no PyPI."""
    for f in files(name, version):
        if f["packagetype"] == "sdist":
            return f["url"], f["digests"]["sha256"]
    raise SystemExit(f"{name} {version} não tem sdist no PyPI")


def build_backend(url: str) -> str:
    """O build-backend declarado no pyproject.toml do sdist ("" se não houver)."""
    with urllib.request.urlopen(url) as r:
        data = r.read()
    with tarfile.open(fileobj=io.BytesIO(data)) as tar:
        for member in tar.getmembers():
            if member.name.count("/") == 1 and member.name.endswith("/pyproject.toml"):
                text = tar.extractfile(member).read().decode()
                m = re.search(r'^build-backend\s*=\s*"([^"]+)"', text, re.M)
                return m.group(1) if m else ""
    return ""


def source(name: str, version: str) -> tuple[str, str]:
    """URL e sha256 que o Homebrew consegue instalar: o sdist ou, se ele precisar
    de Rust para compilar, o wheel Python puro."""
    url, sha = sdist(name, version)
    if build_backend(url).split(".")[0] in RUST_BACKENDS:
        for f in files(name, version):
            if f["filename"].endswith("-py3-none-any.whl"):
                return f["url"], f["digests"]["sha256"]
    return url, sha


def resolve(requirement: str) -> dict[str, str]:
    """Nome normalizado -> versão de tudo que o pip instalaria."""
    with tempfile.TemporaryDirectory() as tmp:
        report = Path(tmp) / "report.json"
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "--quiet", "--dry-run",
             "--ignore-installed", "--report", str(report), requirement],
            check=True,
        )
        data = json.loads(report.read_text())
    return {normalize(i["metadata"]["name"]): i["metadata"]["version"] for i in data["install"]}


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    version = sys.argv[1].lstrip("v")
    url, sha256 = sdist(PACKAGE, version)

    deps = resolve(f"{PACKAGE}=={version}")
    deps.pop(PACKAGE, None)

    formulas = {"mpv", PYTHON} | {FROM_HOMEBREW[n] for n in deps if n in FROM_HOMEBREW}
    depends = "\n".join(f'  depends_on "{f}"' for f in sorted(formulas)) + "\n"

    blocks = []
    for name in sorted(deps):
        if name in FROM_HOMEBREW:
            continue
        r_url, r_sha = source(name, deps[name])
        blocks.append(f'  resource "{name}" do\n    url "{r_url}"\n    sha256 "{r_sha}"\n  end\n')

    print(TEMPLATE.format(url=url, sha256=sha256, depends=depends,
                          resources="\n".join(blocks)), end="")


if __name__ == "__main__":
    main()
