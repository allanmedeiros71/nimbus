# Backlog

**English** · [Português](ROADMAP.pt-BR.md)

Ideas agreed on for future versions of nimbus.

## Write metadata corrections into the files

Today the corrections (title, artist, album, genre, year and cover) live only in `~/.cache/nimbus/tracks.json`. The idea is to be able to write them into the tags of the MP3/FLAC files on Drive themselves, also replacing advertising covers with the right one.

- Requires a Drive write scope (`drive.file` is not enough for existing files; it would have to be `drive`), requested only when the user turns the feature on. The default stays `drive.readonly`.
- Write only after confirmation, track by track or per folder, showing what changes first.
- Drive keeps previous versions of the file, which allows undoing.
- Reuse `tracks.json` as the source of the corrections.
