# nimbus

Player de terminal (Linux e macOS) que toca em streaming as músicas das suas pastas do Google Drive. O acesso é somente leitura e nada é baixado antes: o mpv lê cada faixa por partes, uns dois minutos à frente do que está tocando.

## Requisitos

- Python 3.9 ou mais novo
- [mpv](https://mpv.io): `brew install mpv` no macOS, `sudo apt install mpv` no Debian/Ubuntu

## Instalação

Em muitos sistemas (macOS com Homebrew, Debian/Ubuntu recentes) não existe o comando `pip` solto, só `python3 -m pip`, e instalar pacotes no Python do sistema é bloqueado. Por isso use um ambiente virtual ou o pipx.

### Opção 1: ambiente virtual dentro da pasta do projeto

```sh
git clone git@github.com:allanmedeiros71/nimbus.git
cd nimbus
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
nimbus login
```

Com o ambiente ativado, `pip` e `nimbus` funcionam normalmente. Em um terminal novo, rode `source .venv/bin/activate` de novo antes de usar o `nimbus`.

### Opção 2: pipx, para ter o comando `nimbus` em qualquer terminal

```sh
brew install pipx          # macOS
sudo apt install pipx      # Debian/Ubuntu
pipx ensurepath            # depois abra um terminal novo

pipx install -e .          # dentro da pasta clonada
# ou direto do GitHub:
pipx install git+ssh://git@github.com/allanmedeiros71/nimbus.git
```

## Credenciais do Google (uma vez só)

O nimbus usa o seu próprio cliente OAuth, então o acesso fica só entre você e o Google.

1. Abra o [Google Cloud Console](https://console.cloud.google.com/) e crie um projeto (ou use um existente).
2. Em **APIs e serviços > Biblioteca**, ative a **Google Drive API**.
3. Em **APIs e serviços > Tela de consentimento OAuth**, configure como **Externo**, e em **Usuários de teste** adicione o seu e-mail.
4. Em **APIs e serviços > Credenciais**, clique em **Criar credenciais > ID do cliente OAuth**, tipo **App para computador**.
5. Baixe o JSON e salve como `~/.config/nimbus/client_secret.json`.

Depois:

```sh
nimbus login
```

O navegador abre pedindo permissão de leitura do Drive (`drive.readonly`). O token fica em `~/.config/nimbus/token.json`, legível só pelo seu usuário. `nimbus logout` apaga o token.

## Interface visual

```sh
nimbus                      # abre a interface
nimbus tui "Música/Rock"    # abre já com essa pasta selecionada
nimbus tui --offline        # sem consultar MusicBrainz/Cover Art Archive
```

Em cima fica o painel **Playback**: capa do álbum, título • artista, álbum • gênero • ano, o ícone de tocando/pausado, repeat, shuffle, volume, a posição na fila e uma barra de progresso. Embaixo, **Directories**: a árvore de pastas (Meu Drive e Compartilhados comigo) à esquerda e o conteúdo da pasta selecionada à direita. `Enter` numa música toca a pasta inteira a partir dela.

| Tecla | Ação |
|---|---|
| `espaço` | tocar / pausar |
| `n` / `p` | próxima / anterior |
| `>` `<` (ou `.` `,`) | avança / volta 10s |
| `+` / `-` | volume |
| `m` | mudo |
| `r` | repeat: off → all → one |
| `s` | shuffle (a faixa atual continua tocando) |
| `j` / `k` | desce / sobe |
| `h` / `l` | na árvore fecha/abre a pasta; na lista volta para a árvore/entra na pasta |
| `g` / `G` | primeiro / último item |
| `Tab` | alterna entre árvore e lista |
| `Enter` | toca a música ou entra na subpasta |
| `?` | ajuda |
| `q` | sair |

### Capa e metadados

O nimbus usa, nesta ordem, o que estiver disponível:

1. Tags do arquivo lidas pelo mpv (título, artista, álbum, gênero, ano).
2. O nome do arquivo (`01 - Artista - Título.mp3`) e da pasta, quando faltam tags.
3. A capa embutida no MP3 ou FLAC. Só o começo do arquivo é lido, onde a capa fica.
4. Uma imagem na mesma pasta do Drive (`cover.jpg`, `folder.png`, `capa.jpg`…).
5. [MusicBrainz](https://musicbrainz.org) para completar álbum, ano e gênero, e o [Cover Art Archive](https://coverartarchive.org) para a capa. Só artista, título e álbum são enviados. Use `--offline` ou `NIMBUS_OFFLINE=1` para desligar.

Tags de propaganda (telefone, WhatsApp, links, como "DJ FULANO 62999999999 WHATSAPP") são ignoradas e o nome do arquivo assume; nesses arquivos a capa embutida também é ignorada.

Cada música resolvida fica salva em `~/.cache/nimbus/tracks.json`, pelo ID do arquivo no Drive: da próxima vez, título, artista, álbum e capa corrigidos aparecem na hora, sem consultar o Drive nem a internet ("salvo" na linha de dados). Nada é gravado no Drive, que continua somente leitura. Se a internet falhar, a faixa é consultada de novo na próxima vez. Apague `tracks.json` para refazer tudo. Gravar as correções nos próprios arquivos está no [backlog](ROADMAP.md).

Capas e respostas ficam em cache em `~/.cache/nimbus`. A linha "dados: … · capa: …" no painel mostra de onde veio cada coisa.

A capa é desenhada com blocos coloridos, o que funciona em qualquer terminal com cores. Em terminais com imagens de verdade dá para pedir mais resolução: `NIMBUS_COVER=kitty` (kitty, Ghostty, WezTerm), `NIMBUS_COVER=sixel` (foot, WezTerm, Konsole, iTerm2) ou `NIMBUS_COVER=auto` para detectar. Se aparecer lixo no lugar da capa, volte para o padrão (`NIMBUS_COVER=blocks`). `NIMBUS_COVER=off` esconde a capa.

## Linha de comando

```sh
nimbus ls                          # raiz de Meu Drive
nimbus ls "Música/Rock"            # caminho a partir de Meu Drive (maiúsculas não importam)
nimbus ls https://drive.google.com/drive/folders/<id>   # URL ou ID de pasta, inclusive compartilhadas
nimbus play "Música/Rock"          # toca a pasta em ordem
nimbus play "Música" -r -s         # inclui subpastas, ordem aleatória

nimbus ls "Compartilhados comigo"                 # o que outras pessoas compartilharam com você
nimbus ls "Compartilhados comigo/Discos do Amigo"
nimbus play "Compartilhados comigo/Discos do Amigo" -r
```

"Compartilhados comigo" aparece como uma pasta na raiz do `nimbus ls`. Também aceita `Shared with me`, `@compartilhados` ou `@shared`, útil se você tiver uma pasta própria com esse nome. Pastas compartilhadas também podem ser abertas pela URL ou pelo ID, como qualquer outra.

### Autocomplete de pastas (zsh e bash)

Com o autocomplete ativo, `Tab` completa os nomes das pastas do Drive em `nimbus ls` e `nimbus play`, uma parte do caminho por vez, inclusive dentro de "Compartilhados comigo". Maiúsculas e acentos não precisam bater.

zsh (padrão no macOS): adicione ao fim do `~/.zshrc`

```sh
autoload -Uz compinit && compinit   # se o seu .zshrc ainda não tiver (oh-my-zsh já faz isso)
eval "$(nimbus completion zsh)"
```

bash: adicione ao `~/.bashrc` (no macOS, `~/.bash_profile`)

```sh
eval "$(nimbus completion bash)"
```

Abra um terminal novo e teste com `nimbus ls Mú<Tab>`. Se você usa o venv, o `eval` só funciona quando o comando `nimbus` está no PATH; com o pipx isso vale para qualquer terminal. As listagens ficam em cache por 5 minutos em `~/.cache/nimbus`, então uma pasta recém-criada pode levar esse tempo para aparecer no `Tab`.

Teclas durante o `nimbus play`: `espaço` pausa, `n` próxima, `p` anterior, `←`/`→` voltam ou avançam 10s, `+`/`-` volume, `q` sai.

## Como funciona

- `drive.py` lista pastas e arquivos pela Drive API v3 (com suporte a drives compartilhados, "Compartilhados comigo" e atalhos). Áudio é detectado pelo tipo MIME ou pela extensão.
- `player.py` inicia o mpv em modo ocioso e o controla pelo socket JSON IPC. Cada faixa é a URL `files/<id>?alt=media` da API, com o cabeçalho `Authorization: Bearer …` enviado pelo socket, nunca na linha de comando.
- `playback.py` cuida da fila e pede um token renovado antes de cada faixa.
- `tui.py` é a interface visual (Textual). Os eventos do mpv chegam numa thread própria e a tela só lê o estado num timer, então rede e IPC não travam a interface.
- `metadata.py` junta tags, nome do arquivo, capa embutida, imagem da pasta e MusicBrainz/Cover Art Archive.
- `cli.py` tem os comandos de linha de comando e abre a interface quando não há comando.
- `completion.py` gera os scripts de autocomplete e responde ao `Tab` com as pastas do Drive.

Variáveis úteis: `NIMBUS_CONFIG_DIR` muda a pasta de configuração, `NIMBUS_MPV` aponta para outro executável do mpv, `NIMBUS_COVER` escolhe como desenhar a capa (`blocks`, `kitty`, `sixel`, `auto`, `off`) e `NIMBUS_OFFLINE=1` desliga as consultas à internet.

## Testes

```sh
source .venv/bin/activate
pip install -e '.[dev]'
pytest
```

Os testes de reprodução e da interface usam um mpv real e um servidor HTTP local que imita o Drive (exige o token e responde a requisições Range).
