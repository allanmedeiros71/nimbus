"""Metadados e capa da faixa que está tocando.

A ordem de busca vai do que custa menos ao que depende da internet:

1. Tags lidas pelo mpv (título, artista, álbum, gênero, ano).
2. Nome do arquivo ("01 - Artista - Título.mp3") e da pasta, quando faltam tags.
3. Capa embutida no arquivo (ID3 do MP3 e bloco PICTURE do FLAC), lendo do
   Drive (ou do disco) só o começo do arquivo, onde esses dados ficam.
4. Imagem na mesma pasta (cover.jpg, folder.png, capa.jpg…).
5. MusicBrainz para completar álbum, ano e gênero, e Cover Art Archive para a
   capa. Só artista, título e álbum saem do computador.

Capas e respostas da internet ficam em cache em ~/.cache/nimbus.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Callable, Optional, Sequence

from nimbus import __version__
from nimbus.drive import DriveItem, media_url
from nimbus.local import is_local_id, local_fetcher, local_path

USER_AGENT = f"nimbus/{__version__} ( https://github.com/allanmedeiros71/nimbus )"
MB_API = "https://musicbrainz.org/ws/2"
CAA = "https://coverartarchive.org"

# Limite para ler tags do começo do arquivo; capas maiores que isso são raras.
MAX_TAG_BYTES = 16 * 1024 * 1024
HEAD_BYTES = 256 * 1024

COVER_NAMES = ("cover", "folder", "front", "capa", "album", "albumart", "albumartsmall")
IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp")


def cache_dir() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache"
    return Path(base).expanduser() / "nimbus"


@dataclass
class TrackInfo:
    title: str = ""
    artist: str = ""
    album: str = ""
    genre: str = ""
    year: str = ""
    track: str = ""
    sources: list = field(default_factory=list)  # de onde vieram os dados: tags, arquivo, musicbrainz

    def missing(self) -> bool:
        return not (self.album and self.year and self.genre)


# --- tags e nome do arquivo -------------------------------------------------

_TAG_KEYS = {
    "title": ("title",),
    "artist": ("artist", "album_artist", "albumartist", "performer"),
    "album": ("album",),
    "genre": ("genre",),
    "year": ("date", "year", "originaldate", "tdrc", "tyer"),
    "track": ("track", "tracknumber"),
}


def from_tags(tags: dict | None) -> TrackInfo:
    """Converte o dicionário 'metadata' do mpv (chaves em qualquer caixa)."""
    if not tags:
        return TrackInfo()
    lower = {str(k).lower(): str(v).strip() for k, v in tags.items() if v not in (None, "")}
    values = {}
    for attr, keys in _TAG_KEYS.items():
        for key in keys:
            if lower.get(key):
                values[attr] = lower[key]
                break
    if "year" in values:
        m = re.search(r"\d{4}", values["year"])
        values["year"] = m.group(0) if m else ""
    if "track" in values:
        values["track"] = values["track"].split("/")[0].strip()
    info = TrackInfo(**values)
    if values:
        info.sources.append("tags")
    return info


_NUM_PREFIX = re.compile(r"^\s*(?:\d{1,2}[-.]\s*)?(\d{1,3})\s*(?:[-._)]\s*|\s+)")


def from_filename(name: str) -> TrackInfo:
    """'01 - Artista - Título.mp3', 'Artista - Título.flac', '03. Título.ogg'."""
    stem = name.rsplit(".", 1)[0] if "." in name else name
    stem = stem.replace("_", " ").strip()
    track = ""
    m = _NUM_PREFIX.match(stem)
    if m and m.end() < len(stem):
        track = str(int(m.group(1)))
        stem = stem[m.end():].strip()
    parts = [p.strip() for p in re.split(r"\s+[-–—]\s+", stem) if p.strip()]
    artist = ""
    if len(parts) >= 2:
        artist, title = parts[0], " - ".join(parts[1:])
    else:
        title = stem
    return TrackInfo(title=title, artist=artist, track=track, sources=["arquivo"])


# Tags gravadas por quem distribuiu o arquivo ("DJ FULANO 62999999999 WHATSAPP",
# "baixe em www.site.com") em vez dos dados da música.
_JUNK_TAG = re.compile(
    r"\d{9,}|\b\d{5}-\d{4}\b|\(\d{2}\)\s*\d"         # telefones
    r"|whats\s*app|\bwpp\b|\bzap\b|telegram|instagram|facebook|tiktok|youtube"
    r"|https?://|\bwww\.|\.com(?:\.br)?\b|\.net\b|@\w"  # links e perfis
    r"|\bbaix(?:e|ar|ou)\b|\bdownload|\bcontato\b|\bgravad[oa]s?\b|\bpromo(?:cional)?\b",
    re.IGNORECASE,
)


def is_junk_tag(value: str) -> bool:
    return bool(value) and bool(_JUNK_TAG.search(value))


def clean_tags(tags: TrackInfo, from_file: TrackInfo) -> tuple:
    """Descarta tags que são propaganda. Devolve (tags limpas, se havia lixo)."""
    out = replace(tags, sources=list(tags.sources))
    junk = False
    for attr in ("title", "artist", "album", "genre"):
        if is_junk_tag(getattr(out, attr)):
            setattr(out, attr, "")
            junk = True
    # Título igual ao artista é sinal de tag preenchida no automático; se o nome
    # do arquivo traz "Artista - Título", ele é mais confiável.
    if out.title and out.title.casefold() == out.artist.casefold() and from_file.artist:
        out.title = out.artist = ""
        junk = True
    if not any(getattr(out, a) for a in ("title", "artist", "album", "genre", "year")):
        out.sources = [x for x in out.sources if x != "tags"]
    return out, junk


def merge(primary: TrackInfo, fallback: TrackInfo) -> TrackInfo:
    """Preenche os campos vazios de primary com os de fallback."""
    out = replace(primary, sources=list(primary.sources))
    used = False
    for attr in ("title", "artist", "album", "genre", "year", "track"):
        if not getattr(out, attr) and getattr(fallback, attr):
            setattr(out, attr, getattr(fallback, attr))
            used = True
    if used:
        out.sources.extend(s for s in fallback.sources if s not in out.sources)
    return out


# --- capa embutida -----------------------------------------------------------

def _syncsafe(b: bytes) -> int:
    return (b[0] << 21) | (b[1] << 14) | (b[2] << 7) | b[3]


class RangeReader:
    """Lê um arquivo remoto aos pedaços, só até onde for preciso."""

    def __init__(self, fetch: Callable[[int, int], bytes], chunk: int = HEAD_BYTES):
        self._fetch = fetch
        self._chunk = chunk
        self.data = b""
        self.eof = False

    def ensure(self, n: int) -> bool:
        while len(self.data) < n and not self.eof:
            if n > MAX_TAG_BYTES:
                return False
            want = max(n - len(self.data), self._chunk)
            got = self._fetch(len(self.data), len(self.data) + want - 1)
            if len(got) < want:
                self.eof = True
            self.data += got
        return len(self.data) >= n


def _id3_cover(reader: RangeReader) -> bytes | None:
    if not reader.ensure(10) or reader.data[:3] != b"ID3":
        return None
    size = _syncsafe(reader.data[6:10]) + 10 + (10 if reader.data[5] & 0x10 else 0)
    if not reader.ensure(size):
        return None
    from mutagen.id3 import ID3

    try:
        tags = ID3(io.BytesIO(reader.data[:size]))
    except Exception:
        return None
    pics = tags.getall("APIC") or tags.getall("PIC")
    if not pics:
        return None
    front = [p for p in pics if getattr(p, "type", None) == 3]
    return (front or pics)[0].data or None


def _flac_cover(reader: RangeReader) -> bytes | None:
    if not reader.ensure(4) or reader.data[:4] != b"fLaC":
        return None
    from mutagen.flac import Picture

    pos = 4
    pictures = []
    while reader.ensure(pos + 4):
        header = reader.data[pos:pos + 4]
        last, kind = header[0] & 0x80, header[0] & 0x7F
        length = int.from_bytes(header[1:4], "big")
        body_end = pos + 4 + length
        if kind == 6:
            if not reader.ensure(body_end):
                break
            try:
                pictures.append(Picture(reader.data[pos + 4:body_end]))
            except Exception:
                pass
        if last:
            break
        pos = body_end
    if not pictures:
        return None
    front = [p for p in pictures if p.type == 3]
    return (front or pictures)[0].data or None


def embedded_cover(fetch: Callable[[int, int], bytes]) -> bytes | None:
    """Capa embutida em MP3 (ID3) ou FLAC, lendo só o começo do arquivo."""
    reader = RangeReader(fetch)
    return _id3_cover(reader) or _flac_cover(reader)


def folder_cover_candidates(siblings: Sequence[DriveItem]) -> list[DriveItem]:
    """Imagens da pasta, com cover/folder/capa… primeiro."""
    images = [
        i for i in siblings
        if i.mime_type.startswith("image/") or i.name.lower().endswith(IMAGE_EXTENSIONS)
    ]

    def rank(item: DriveItem) -> tuple:
        stem = item.name.rsplit(".", 1)[0].lower().strip()
        return (0 if stem in COVER_NAMES else 1 if any(n in stem for n in COVER_NAMES) else 2, item.name.lower())

    return sorted(images, key=rank)


def is_compilation(siblings: Sequence[DriveItem]) -> bool:
    """Pasta com faixas de artistas diferentes, pelo nome dos arquivos ("NN Artista - Título")."""
    artists = [from_filename(i.name).artist.casefold() for i in siblings if i.is_audio]
    artists = [a for a in artists if a]
    if len(artists) < 2:
        return False
    top = max(artists.count(a) for a in set(artists))
    return len(set(artists)) >= 2 and top / len(artists) < 0.8


# --- internet ----------------------------------------------------------------

class Http:
    """GET simples com User-Agent e um intervalo mínimo entre chamadas ao MusicBrainz."""

    def __init__(self, timeout: float = 10.0):
        self.timeout = timeout
        self._lock = threading.Lock()
        self._last_mb = 0.0

    def get(self, url: str, headers: dict | None = None) -> bytes:
        if url.startswith(MB_API):
            with self._lock:  # MusicBrainz pede no máximo 1 requisição por segundo
                wait = self._last_mb + 1.1 - time.monotonic()
                if wait > 0:
                    time.sleep(wait)
                self._last_mb = time.monotonic()
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **(headers or {})})
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            return resp.read()

    def json(self, url: str) -> dict:
        return json.loads(self.get(url, {"Accept": "application/json"}))


def _lucene(value: str) -> str:
    return '"' + re.sub(r'(["\\])', r"\\\1", value) + '"'


@dataclass
class OnlineResult:
    album: str = ""
    year: str = ""
    genre: str = ""
    artist: str = ""
    release_group: str = ""
    release: str = ""


def _best_release(recording: dict) -> dict | None:
    releases = recording.get("releases") or []

    def rank(r: dict) -> tuple:
        rg = r.get("release-group") or {}
        return (
            rg.get("primary-type") != "Album",
            bool(rg.get("secondary-types")),  # coletâneas, ao vivo…
            r.get("status") != "Official",
            r.get("date") or "9999",
        )

    return min(releases, key=rank) if releases else None


def parse_recording_search(data: dict) -> OnlineResult | None:
    recs = [r for r in data.get("recordings", []) if int(r.get("score", 0)) >= 80]
    if not recs:
        return None
    rec = recs[0]
    rel = _best_release(rec)
    out = OnlineResult(year=(rec.get("first-release-date") or "")[:4])
    credit = rec.get("artist-credit") or []
    out.artist = "".join(c.get("name", "") + c.get("joinphrase", "") for c in credit)
    if rel:
        out.album = rel.get("title", "")
        out.release = rel.get("id", "")
        out.release_group = (rel.get("release-group") or {}).get("id", "")
        out.year = out.year or (rel.get("date") or "")[:4]
    tags = sorted(rec.get("tags") or [], key=lambda t: -int(t.get("count", 0)))
    if tags:
        out.genre = tags[0].get("name", "")
    return out


def parse_release_group_search(data: dict) -> OnlineResult | None:
    groups = [g for g in data.get("release-groups", []) if int(g.get("score", 0)) >= 80]
    if not groups:
        return None
    g = groups[0]
    releases = g.get("releases") or []
    return OnlineResult(
        album=g.get("title", ""),
        year=(g.get("first-release-date") or "")[:4],
        release_group=g.get("id", ""),
        release=releases[0]["id"] if releases else "",
    )


def pick_genre(data: dict) -> str:
    genres = sorted(data.get("genres") or data.get("tags") or [], key=lambda t: -int(t.get("count", 0)))
    return genres[0]["name"] if genres else ""


class OnlineLookup:
    """MusicBrainz + Cover Art Archive, com cache em disco."""

    def __init__(self, http: Http | None = None, cache: Path | None = None):
        self.http = http or Http()
        self.cache_file = (cache or cache_dir()) / "lookups.json"
        self._lock = threading.Lock()
        try:
            self._cache = json.loads(self.cache_file.read_text())
        except (OSError, ValueError):
            self._cache = {}

    def _save(self) -> None:
        try:
            self.cache_file.parent.mkdir(parents=True, exist_ok=True)
            self.cache_file.write_text(json.dumps(self._cache))
        except OSError:
            pass

    def lookup(self, info: TrackInfo, errors: list | None = None) -> OnlineResult | None:
        if not info.title and not info.album:
            return None
        # O prefixo muda quando a busca muda, para não reaproveitar um "não achei" antigo.
        key = "v2|" + "|".join(x.casefold() for x in (info.artist, info.title, info.album))
        with self._lock:
            if key in self._cache:
                hit = self._cache[key]
                return OnlineResult(**hit) if hit else None
        try:
            result = self._lookup(info)
        except (OSError, ValueError, urllib.error.URLError) as e:
            if errors is not None:
                errors.append(e)
            return None  # sem internet ou resposta estranha: tenta de novo na próxima vez
        with self._lock:
            self._cache[key] = result.__dict__ if result else None
            self._save()
        return result

    def _lookup(self, info: TrackInfo) -> OnlineResult | None:
        result = None
        if info.album and info.artist:
            q = f"releasegroup:{_lucene(info.album)} AND artist:{_lucene(info.artist)}"
            result = parse_release_group_search(
                self.http.json(f"{MB_API}/release-group?fmt=json&limit=3&query={urllib.parse.quote(q)}"))
        if result is None and info.title:
            for q in self._recording_queries(info):
                result = parse_recording_search(
                    self.http.json(f"{MB_API}/recording?fmt=json&limit=5&query={urllib.parse.quote(q)}"))
                if result is not None:
                    break
        if result and result.release_group and not result.genre:
            try:
                data = self.http.json(f"{MB_API}/release-group/{result.release_group}?fmt=json&inc=genres+tags")
                result.genre = pick_genre(data)
            except (OSError, ValueError, urllib.error.URLError):
                pass
        return result

    @staticmethod
    def _recording_queries(info: TrackInfo) -> list:
        """Da busca mais exata à mais solta: o álbum pode ser uma coletânea e o
        nome do arquivo costuma vir sem acentos ou em maiúsculas."""
        def words(value: str) -> str:
            return "(" + re.sub(r'[^\w\s]', " ", value).strip() + ")"

        queries = []
        artist = f" AND artist:{_lucene(info.artist)}" if info.artist else ""
        if info.album:
            queries.append(f"recording:{_lucene(info.title)}{artist} AND release:{_lucene(info.album)}")
        queries.append(f"recording:{_lucene(info.title)}{artist}")
        if info.artist:
            queries.append(f"recording:{words(info.title)} AND artist:{words(info.artist)}")
        return queries

    def cover(self, result: OnlineResult) -> bytes | None:
        urls = []
        if result.release_group:
            urls.append(f"{CAA}/release-group/{result.release_group}/front-250")
        if result.release:
            urls.append(f"{CAA}/release/{result.release}/front-250")
        for url in urls:
            try:
                return self.http.get(url)
            except urllib.error.HTTPError as e:
                if e.code != 404:  # 404 = sem capa; outros erros tentam de novo depois
                    raise
        return None

    def store_cover(self, info: TrackInfo) -> tuple:
        """Capa pelas buscas públicas do iTunes e do Deezer (sem chave de API).

        Reserva para quando o Cover Art Archive não tem a capa ou não é acessível
        (as imagens dele ficam no archive.org, bloqueado em algumas redes).
        Devolve (bytes, nome da fonte) ou (None, "").
        """
        if not (info.title and info.artist):
            return None, ""
        last_error = None
        for name, find in (("itunes", self._itunes_art), ("deezer", self._deezer_art)):
            try:
                url = find(info)
                if url:
                    return self.http.get(url), name
            except (OSError, ValueError, urllib.error.URLError) as e:
                last_error = e
        if last_error is not None:
            raise last_error
        return None, ""

    def _itunes_art(self, info: TrackInfo) -> str:
        term = urllib.parse.quote(f"{info.artist} {info.title}")
        data = self.http.json(f"https://itunes.apple.com/search?term={term}&entity=song&limit=10")
        for r in data.get("results", []):
            if _same_name(r.get("artistName", ""), info.artist) and r.get("artworkUrl100"):
                return r["artworkUrl100"].replace("100x100bb", "300x300bb")
        return ""

    def _deezer_art(self, info: TrackInfo) -> str:
        q = urllib.parse.quote(f'artist:"{info.artist}" track:"{info.title}"')
        data = self.http.json(f"https://api.deezer.com/search?q={q}&limit=10")
        for r in data.get("data", []):
            album = r.get("album") or {}
            if _same_name((r.get("artist") or {}).get("name", ""), info.artist) and album.get("cover_medium"):
                return album["cover_medium"]
        return ""


def _fold(value: str) -> str:
    import unicodedata

    value = unicodedata.normalize("NFKD", value)
    return re.sub(r"[^a-z0-9]", "", "".join(c for c in value if not unicodedata.combining(c)).lower())


def _same_name(a: str, b: str) -> bool:
    """'Legião Urbana' == 'LEGIAO URBANA'; tolera 'Titãs' vs 'Titãs & Convidados'."""
    fa, fb = _fold(a), _fold(b)
    return bool(fa and fb) and (fa == fb or fa.startswith(fb) or fb.startswith(fa))


# --- juntando tudo -----------------------------------------------------------

class CoverCache:
    def __init__(self, path: Path | None = None):
        self.path = path or cache_dir() / "covers"

    def _file(self, key: str) -> Path:
        return self.path / hashlib.sha1(key.encode()).hexdigest()

    def get(self, key: str) -> bytes | None:
        try:
            data = self._file(key).read_bytes()
        except OSError:
            return None
        return data or None

    def has(self, key: str) -> bool:
        return self._file(key).exists()

    def put(self, key: str, data: bytes | None) -> None:
        try:
            self.path.mkdir(parents=True, exist_ok=True)
            self._file(key).write_bytes(data or b"")  # vazio = já procurei e não tem
        except OSError:
            pass


def drive_fetcher(file_id: str, token: Callable[[], str], timeout: float = 15.0) -> Callable[[int, int], bytes]:
    def fetch(start: int, end: int) -> bytes:
        req = urllib.request.Request(media_url(file_id), headers={
            "Authorization": f"Bearer {token()}",
            "Range": f"bytes={start}-{end}",
            "User-Agent": USER_AGENT,
        })
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = resp.read(end - start + 1)
                if resp.status == 200 and start > 0:  # servidor ignorou o Range
                    return b""
                return data
        except urllib.error.HTTPError as e:
            if e.code == 416:  # pediu além do fim do arquivo
                return b""
            raise

    return fetch


@dataclass
class Resolved:
    info: TrackInfo
    cover: Optional[bytes] = None
    cover_source: str = ""
    note: str = ""  # o que aconteceu na internet, para mostrar no painel


class TrackStore:
    """Metadados já corrigidos de cada faixa, por ID do arquivo no Drive.

    Fica em ~/.cache/nimbus/tracks.json. Nada é gravado no Drive: o acesso
    continua somente leitura. Apagar o arquivo faz tudo ser resolvido de novo.
    """

    VERSION = 3  # muda quando a lógica de correção mudar, para refazer as faixas

    def __init__(self, path: Path | None = None):
        self.path = path or cache_dir() / "tracks.json"
        self._lock = threading.Lock()
        try:
            data = json.loads(self.path.read_text())
            self._data = data.get("tracks", {}) if data.get("version") == self.VERSION else {}
        except (OSError, ValueError, AttributeError):
            self._data = {}

    def get(self, file_id: str) -> dict | None:
        with self._lock:
            entry = self._data.get(file_id)
            return dict(entry) if entry else None

    def put(self, file_id: str, entry: dict) -> None:
        with self._lock:
            self._data[file_id] = entry
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                tmp = self.path.with_suffix(".tmp")
                tmp.write_text(json.dumps({"version": self.VERSION, "tracks": self._data}, ensure_ascii=False))
                tmp.replace(self.path)
            except OSError:
                pass

    def __len__(self) -> int:
        return len(self._data)


_INFO_FIELDS = ("title", "artist", "album", "genre", "year", "track")


class MetadataResolver:
    def __init__(
        self,
        token: Callable[[], str],
        online: OnlineLookup | None = None,
        covers: CoverCache | None = None,
        fetcher: Callable[[str], Callable[[int, int], bytes]] | None = None,
        store: TrackStore | None = None,
    ):
        self._token = token
        self.online = online
        self.covers = covers or CoverCache()
        self.store = store if store is not None else TrackStore()
        drive = fetcher or (lambda file_id: drive_fetcher(file_id, self._token))
        # Arquivos do disco são lidos direto, sem token.
        self._fetcher = lambda file_id: (
            local_fetcher(local_path(file_id)) if is_local_id(file_id) else drive(file_id)
        )

    def basic(self, item: DriveItem, tags: dict | None, folder_name: str = "") -> TrackInfo:
        """O que dá para mostrar já: a correção salva, se a faixa já tocou, ou tags + nome do arquivo."""
        known = self.known(item)
        return known if known is not None else self._basic(item, tags, folder_name)[0]

    def known(self, item: DriveItem) -> TrackInfo | None:
        entry = self.store.get(item.id)
        if not entry:
            return None
        info = TrackInfo(**{k: entry.get(k, "") for k in _INFO_FIELDS})
        info.sources = list(entry.get("sources", [])) + ["salvo"]
        return info

    def _basic(self, item: DriveItem, tags: dict | None, folder_name: str = "") -> tuple:
        from_file = from_filename(item.name)
        tag_info, junk = clean_tags(from_tags(tags), from_file)
        info = merge(tag_info, from_file)
        if not info.album and folder_name:
            info.album = folder_name
            info.sources.append("pasta")
        return info, junk

    def resolve(self, item: DriveItem, tags: dict | None, siblings: Sequence[DriveItem] = (),
                folder_name: str = "") -> Resolved:
        """Metadados e capa corrigidos; a primeira vez consulta tudo e salva, as outras leem do disco."""
        entry = self.store.get(item.id)
        if entry and entry.get("complete"):
            cover = self.covers.get(entry["cover_key"]) if entry.get("cover_key") else None
            if cover or not entry.get("cover_key"):  # capa sumiu do cache: refaz
                return Resolved(info=self.known(item), cover=cover, cover_source=entry.get("cover_source", ""),
                                note=entry.get("note", ""))

        errors: list = []
        out, cover_key = self._resolve(item, tags, siblings, folder_name, errors)
        entry = {k: getattr(out.info, k) for k in _INFO_FIELDS}
        entry.update(sources=out.info.sources, cover_key=cover_key, cover_source=out.cover_source, note=out.note,
                     # erro de rede sem capa: mostra o que salvou, mas tenta de novo depois
                     complete=not errors or (out.cover is not None and out.cover_source != "pasta"))
        self.store.put(item.id, entry)
        return out

    def _resolve(self, item: DriveItem, tags: dict | None, siblings: Sequence[DriveItem],
                 folder_name: str, errors: list) -> tuple:
        info, junk_tags = self._basic(item, tags, folder_name)
        album_from_folder = "pasta" in info.sources
        out = Resolved(info=info)

        # Numa coletânea a capa embutida e a imagem da pasta costumam ser a mesma
        # para todas as faixas: primeiro tenta a capa do disco original de cada
        # música na internet, e essas ficam por último.
        compilation = is_compilation(siblings)
        cover_key = ""
        if not compilation:
            cover_key = self._local_cover(item, siblings, junk_tags, out, errors)

        if self.online is not None and (info.missing() or out.cover is None):
            query = replace(info, album="" if album_from_folder or compilation else info.album)
            n_errors = len(errors)
            result = self.online.lookup(query, errors)
            if len(errors) > n_errors:
                out.note = f"internet: erro ({str(errors[-1])[:60]})"
            elif result is None:
                out.note = "musicbrainz: não encontrado"
            if result:
                filled = TrackInfo(album=result.album, year=result.year, genre=result.genre,
                                   artist=result.artist, sources=["musicbrainz"])
                if album_from_folder and result.album:
                    info.album = ""  # o nome da pasta era só um palpite
                    info.sources.remove("pasta")
                out.info = merge(info, filled)
                if out.cover is None:
                    key = f"caa:{result.release_group or result.release}"
                    out.cover = self._cached(key, lambda: self.online.cover(result), errors)
                    if out.cover:
                        out.cover_source, cover_key = "cover art archive", key
                    elif len(errors) > n_errors:
                        out.note = f"internet: erro ({str(errors[-1])[:60]})"
                    else:
                        out.note = "cover art archive: sem capa"
            if out.cover is None:
                query = out.info if result else info
                key = "store:" + "|".join(_fold(x) for x in (query.artist, query.title))
                found: dict = {}

                def from_store() -> bytes | None:
                    data, found["source"] = self.online.store_cover(query)
                    return data

                n_errors = len(errors)
                out.cover = self._cached(key, from_store, errors)
                if out.cover:
                    out.cover_source, cover_key = found.get("source") or "itunes/deezer", key
                    out.note = ""
                elif len(errors) > n_errors and not out.note.startswith("internet"):
                    out.note = f"internet: erro ({str(errors[-1])[:60]})"
        if out.cover is None and compilation:
            cover_key = self._local_cover(item, siblings, junk_tags, out, errors)
        return out, cover_key

    def _local_cover(self, item: DriveItem, siblings: Sequence[DriveItem], junk_tags: bool,
                     out: Resolved, errors: list) -> str:
        """Capa embutida (se as tags não são propaganda) ou, sem ela, imagem da pasta."""
        key = f"embedded:{item.id}"
        # Quem grava propaganda nas tags costuma pôr a própria arte como capa.
        cover = None if junk_tags else self._cached(key, lambda: embedded_cover(self._fetcher(item.id)), errors)
        if cover:
            out.cover, out.cover_source = cover, "arquivo"
            return key
        for img in folder_cover_candidates(siblings)[:2]:
            key = f"drive:{img.id}"
            cover = self._cached(key, lambda img=img: self._fetcher(img.id)(0, 8 * 1024 * 1024 - 1), errors)
            if cover:
                out.cover, out.cover_source = cover, "pasta"
                return key
        return ""

    def _cached(self, key: str, compute: Callable[[], bytes | None], errors: list | None = None) -> bytes | None:
        if self.covers.has(key):
            return self.covers.get(key)
        try:
            data = compute()
        except (OSError, urllib.error.URLError) as e:
            if errors is not None:
                errors.append(e)
            return None  # erro de rede: não grava, tenta de novo depois
        self.covers.put(key, data)
        return data
