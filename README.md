# nimbus

**English** · [Português](README.pt-BR.md)

A terminal player (Linux and macOS) that streams the music in your Google Drive folders. Access is read-only and nothing is downloaded up front: mpv reads each track in chunks, about two minutes ahead of what is playing.

It also plays music from your own computer: home folder, external drive and USB stick. That part works even without signing in to Google.

The interface and command-line messages are currently in Portuguese.

## Requirements

- Python 3.9 or newer
- [mpv](https://mpv.io): `brew install mpv` on macOS, `sudo apt install mpv` on Debian/Ubuntu

The Homebrew installation brings both along.

## Installation

### Homebrew (macOS and Linux)

```sh
brew install allanmedeiros71/tap/nimbus
```

Homebrew installs mpv and Python along with it. To update: `brew upgrade nimbus`.

### pipx

[pipx](https://pipx.pypa.io) creates an isolated environment for nimbus and makes the `nimbus` command available from any folder and any terminal, which shell completion also needs. In this case mpv is installed separately (see Requirements). On PyPI the package is called `nimbus-player`; the command is still `nimbus`.

```sh
brew install pipx          # macOS
sudo apt install pipx      # Debian/Ubuntu
pipx ensurepath            # adds ~/.local/bin to PATH

pipx install nimbus-player
```

After `pipx ensurepath`, **open a new terminal** so the PATH change takes effect. To update: `pipx upgrade nimbus-player`.

If you installed an older version from GitHub, switch to the PyPI one: `pipx uninstall nimbus && pipx install nimbus-player`.

To run the code from the repository (development version): `git clone git@github.com:allanmedeiros71/nimbus.git` and `pipx install -e ./nimbus`. With `-e`, a `git pull` inside the folder already updates the command; only run `pipx install -e --force ./nimbus` again if the dependencies change.

### Alternative for development: virtual environment

```sh
cd nimbus
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
```

In this case the `nimbus` command only exists while the environment is active: run `source .venv/bin/activate` in every new terminal first.

### Common problems

- **`zsh: command not found: pip`**: on macOS with Homebrew and on recent Debian/Ubuntu there is no standalone `pip`, and the system Python does not accept packages. Use pipx (above) or a virtual environment.
- **`zsh: permission denied: nimbus`** or **`command not found: nimbus`** outside the project folder: the command was only installed in the venv. Check with `type -a nimbus`; if no path like `~/.local/bin/nimbus` shows up, install with pipx, run `pipx ensurepath` and open a new terminal.
- **`mpv não encontrado`** (mpv not found): install it with `brew install mpv` or `sudo apt install mpv`.

## Google credentials (one time only)

nimbus uses your own OAuth client, so access stays between you and Google.

1. Open the [Google Cloud Console](https://console.cloud.google.com/) and create a project (or use an existing one).
2. In **APIs & Services > Library**, enable the **Google Drive API**.
3. In **APIs & Services > OAuth consent screen**, choose **External**, and add your email under **Test users**.
4. In **APIs & Services > Credentials**, click **Create credentials > OAuth client ID**, type **Desktop app**.
5. Download the JSON and save it as `~/.config/nimbus/client_secret.json`.

Then:

```sh
nimbus login
```

The browser opens asking for read permission on Drive (`drive.readonly`). The token is stored in `~/.config/nimbus/token.json`, readable only by your user. `nimbus logout` deletes the token.

## Visual interface

```sh
nimbus                      # opens the interface
nimbus tui "Música/Rock"    # opens with this folder already selected
nimbus tui ~/Música         # or with a folder on your computer
nimbus tui --offline        # without querying MusicBrainz/Cover Art Archive
```

At the top is the **Playback** panel: album cover, title • artist, album • genre • year, the playing/paused icon, repeat, shuffle, volume, the position in the queue and a progress bar. Below it, **Directories**: the folder tree (Meu Drive, Compartilhados comigo and Este computador, i.e. My Drive, Shared with me and This computer) on the left and the contents of the selected folder on the right. `Enter` on a song plays the whole folder starting from it. To save bandwidth and API calls, a Drive folder is only listed on the right when you press `Enter` or `→`/`l` on it; folders already opened and folders on your computer show up instantly, as you navigate.

| Key | Action |
|---|---|
| `space` | play / pause |
| `n` / `p` | next / previous |
| `>` `<` (or `.` `,`) | forward / back 10s |
| `+` / `-` | volume |
| `m` | mute |
| `r` | repeat: off → all → one |
| `s` | shuffle (the current track keeps playing) |
| `c` | bigger / smaller cover (28×14 instead of 14×7 characters) |
| `j` / `k` | down / up |
| `h` / `l` | in the tree, collapse the folder or go up / expand the folder (and, if already expanded, move to the list); in the list, go back to the tree / enter the folder |
| `g` / `G` | first / last item |
| `Tab` | switch between tree and list |
| `Enter` | play the song or enter the subfolder |
| `?` | help |
| `q` | quit |

### Music on your computer, external drive and USB stick

The **Este computador** (This computer) root in the tree shows your home folder, mounted external drives and the system disk (`/`). External drives are looked up where each system mounts them:

- macOS: `/Volumes` (the system disk, which shows up there as a link to `/`, is left out);
- Linux: `/media/<user>/…` (Ubuntu, Debian), `/run/media/<user>/…` (Fedora, Arch), `/media/…` and the mount points in `/mnt`.

A USB stick plugged in while the interface is open only shows up after reopening nimbus. Hidden folders and files (starting with `.`) are not shown, and playlists (`.m3u`, `.pls`) are not added to the queue. mpv reads the files straight from disk, and the cover comes from the file itself or from an image in the folder, as with Drive.

Without a Google login, `nimbus` opens with only **Este computador**.

### Cover art and metadata

nimbus uses whatever is available, in this order:

1. File tags read by mpv (title, artist, album, genre, year).
2. The file name (`01 - Artist - Title.mp3`) and the folder name, when tags are missing.
3. The cover embedded in the MP3 or FLAC. Only the beginning of the file, where the cover lives, is read.
4. An image in the same folder (`cover.jpg`, `folder.png`, `capa.jpg`…). For music on this computer, art subfolders such as `Covers/`, `Artwork/` or `Scans/` are searched too, and images that fail to open are skipped. For music on this computer, an image named like a cover (`Folder.jpg`, `cover.jpg`, `AlbumArt_…_Large.jpg`) wins over the embedded cover, and thumbnails such as `AlbumArtSmall.jpg` come last.
5. [MusicBrainz](https://musicbrainz.org) to fill in album, year and genre, and the [Cover Art Archive](https://coverartarchive.org) for the cover. If the Cover Art Archive does not have the cover or cannot be reached from your network, the public iTunes search and then Deezer's serve as fallbacks. Only artist, title and album are sent. Use `--offline` or `NIMBUS_OFFLINE=1` to turn this off.

In compilations (folders with tracks by different artists, judging by the file names) the cover comes first from each song's original release on the Cover Art Archive; the embedded cover and the folder image are fallbacks, since they tend to be the same on every track.

Advertising tags (phone numbers, WhatsApp, links, such as "DJ FULANO 62999999999 WHATSAPP") are ignored and the file name takes over; in those files the embedded cover is ignored too.

Each resolved song is saved in `~/.cache/nimbus/tracks.json`, keyed by its Drive file ID: next time, the corrected title, artist, album and cover show up instantly, without querying Drive or the internet ("salvo", saved, on the data line). Nothing is written to Drive, which stays read-only. If the internet fails, the track is looked up again next time. Delete `tracks.json` to redo everything. Writing the corrections into the files themselves is on the [backlog](ROADMAP.md).

Covers and responses are cached in `~/.cache/nimbus`. The "dados: … · capa: …" (data: … · cover: …) line in the panel shows where each piece came from.

The cover is drawn with colored blocks, which works in any terminal with colors. In terminals with real image support you can ask for higher resolution: `NIMBUS_COVER=kitty` (kitty, Ghostty, WezTerm), `NIMBUS_COVER=sixel` (foot, WezTerm, Konsole, iTerm2) or `NIMBUS_COVER=auto` to detect. If garbage shows up instead of the cover, go back to the default (`NIMBUS_COVER=blocks`). `NIMBUS_COVER=off` hides the cover.

## Command line

```sh
nimbus ls                          # root of My Drive
nimbus ls "Música/Rock"            # path from My Drive (case doesn't matter)
nimbus ls https://drive.google.com/drive/folders/<id>   # folder URL or ID, shared ones included
nimbus play "Música/Rock"          # plays the folder in order
nimbus play "Música" -r -s         # includes subfolders, random order

nimbus ls ~/Música                 # folder on your computer
nimbus play /Volumes/PENDRIVE -r   # USB stick on macOS
nimbus play /media/$USER/HD/Discos -s   # external drive on Linux
nimbus ls "Este computador"        # home folder and mounted drives

nimbus ls "Shared with me"                 # what other people shared with you
nimbus ls "Shared with me/Friend's Records"
nimbus play "Shared with me/Friend's Records" -r
```

"Compartilhados comigo" (Shared with me) shows up as a folder at the root of `nimbus ls`. `Shared with me`, `@compartilhados` and `@shared` also work, which is useful if you have a folder of your own with that name. Shared folders can also be opened by URL or ID, like any other.

A path is on your computer when it starts with `/`, `~`, `./` or `../` (`file://…` also works). Anything else is looked up on Drive, so for a relative folder on your computer use `./Discos`, not just `Discos`.

### Folder completion (zsh and bash)

With completion enabled, `Tab` completes Drive folder names in `nimbus ls` and `nimbus play`, one path segment at a time, including inside "Shared with me". Paths on your computer (`~/Mú<Tab>`, `/Volumes/<Tab>`) complete too. Case and accents don't need to match.

zsh (default on macOS): add to the end of `~/.zshrc`

```sh
autoload -Uz compinit && compinit   # if your .zshrc doesn't have it yet (oh-my-zsh already does this)
eval "$(nimbus completion zsh)"
```

bash: add to `~/.bashrc` (on macOS, `~/.bash_profile`)

```sh
eval "$(nimbus completion bash)"
```

Open a new terminal and test with `nimbus ls Mú<Tab>`. The `eval` needs the `nimbus` command on the PATH, which is why the Homebrew or pipx installation is recommended. Listings are cached for 5 minutes in `~/.cache/nimbus`, so a newly created folder may take that long to show up on `Tab`.

Keys during `nimbus play`: `space` pauses, `n` next, `p` previous, `←`/`→` back or forward 10s, `+`/`-` volume, `q` quits.

## How it works

- `drive.py` lists folders and files through the Drive API v3 (with support for shared drives, "Shared with me" and shortcuts). Audio is detected by MIME type or by extension.
- `player.py` starts mpv in idle mode and controls it through the JSON IPC socket. Each track is the API URL `files/<id>?alt=media`, with the `Authorization: Bearer …` header sent through the socket, never on the command line.
- `local.py` lists folders on your computer and finds mounted drives; `library.py` combines Drive and the computer and routes each request to the right source. Local items have the ID `local:<path>`.
- `playback.py` manages the queue and asks for a refreshed token before each Drive track. Local files go to mpv by path, with no token.
- `tui.py` is the visual interface (Textual). mpv events arrive on their own thread and the screen only reads the state on a timer, so network and IPC never freeze the interface.
- `metadata.py` combines tags, file name, embedded cover, folder image and MusicBrainz/Cover Art Archive.
- `cli.py` has the command-line commands and opens the interface when no command is given.
- `completion.py` generates the completion scripts and answers `Tab` with the Drive folders.

Useful variables: `NIMBUS_CONFIG_DIR` changes the configuration folder, `NIMBUS_MPV` points to another mpv executable, `NIMBUS_COVER` chooses how to draw the cover (`blocks`, `kitty`, `sixel`, `auto`, `off`) and `NIMBUS_OFFLINE=1` turns off internet lookups.

## Tests

```sh
source .venv/bin/activate   # development virtual environment (above)
pytest
```

The playback and interface tests use a real mpv and a local HTTP server that mimics Drive (it requires the token and answers Range requests).

## Releasing a version

Just create and push a tag on `main`: `git tag v0.3.2 && git push origin v0.3.2`. The version number comes from the tag itself (via [setuptools-scm](https://setuptools-scm.readthedocs.io)), so there is no number to edit in the code.

The `release` workflow runs the tests, publishes `nimbus-player` to PyPI, creates the GitHub Release with the packages and updates `Formula/nimbus.rb` in the [homebrew-tap](https://github.com/allanmedeiros71/homebrew-tap) repository. The formula is generated by `scripts/homebrew_formula.py`.

One-time setup:
- PyPI: on pypi.org, *Your projects → Publishing → Add a new pending publisher* with project `nimbus-player`, owner `allanmedeiros71`, repository `nimbus`, workflow `release.yml` and environment `pypi`.
- Homebrew: create the public repository `allanmedeiros71/homebrew-tap` and a fine-grained token with *Contents: read and write* on it only; save it as the `HOMEBREW_TAP_TOKEN` secret in this repository. Without the secret, the workflow publishes to PyPI and only warns that it skipped Homebrew.
