# Backlog

[English](ROADMAP.md) · **Português**

Ideias combinadas para versões futuras do nimbus.

## Gravar as correções de metadados nos arquivos

Hoje as correções (título, artista, álbum, gênero, ano e capa) ficam só em `~/.cache/nimbus/tracks.json`. A ideia é poder gravá-las nas tags dos próprios MP3/FLAC no Drive, trocando também a capa de propaganda pela capa certa.

- Exige um escopo de escrita no Drive (`drive.file` não basta para arquivos existentes; seria `drive`), pedido só quando o usuário ativar o recurso. O padrão continua `drive.readonly`.
- Gravar só depois de confirmação, faixa a faixa ou por pasta, mostrando antes o que muda.
- O Drive guarda versões anteriores do arquivo, o que permite desfazer.
- Reaproveitar `tracks.json` como fonte das correções.
