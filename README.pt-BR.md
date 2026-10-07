# nimbus

[English](README.md) · **Português**

Player de terminal (Linux e macOS) que toca em streaming as músicas das suas pastas do Google Drive. O acesso é somente leitura e nada é baixado antes: o mpv lê cada faixa por partes, uns dois minutos à frente do que está tocando.

Também toca as músicas do próprio computador: pasta pessoal, HD externo e pendrive. Essa parte funciona mesmo sem login no Google.

## Requisitos

- Python 3.9 ou mais novo
- [mpv](https://mpv.io): `brew install mpv` no macOS, `sudo apt install mpv` no Debian/Ubuntu

## Instalação

O jeito recomendado é o [pipx](https://pipx.pypa.io): ele cria um ambiente isolado para o nimbus e deixa o comando `nimbus` disponível em qualquer pasta e em qualquer terminal, o que também é necessário para o autocomplete.

```sh
brew install pipx          # macOS
sudo apt install pipx      # Debian/Ubuntu
pipx ensurepath            # coloca ~/.local/bin no PATH

git clone git@github.com:allanmedeiros71/nimbus.git
pipx install -e ./nimbus
```

Depois do `pipx ensurepath`, **abra um terminal novo** para o PATH valer. Com `-e`, um `git pull` dentro da pasta já atualiza o comando; só rode `pipx install -e --force ./nimbus` de novo se mudarem as dependências.

Para instalar direto do GitHub, sem clonar: `pipx install git+ssh://git@github.com/allanmedeiros71/nimbus.git` (atualize com `pipx upgrade nimbus`).

### Alternativa para desenvolvimento: ambiente virtual

```sh
cd nimbus
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
```

Nesse caso o comando `nimbus` só existe com o ambiente ativado: em cada terminal novo rode `source .venv/bin/activate` antes.

### Problemas comuns

- **`zsh: command not found: pip`**: no macOS com Homebrew e em Debian/Ubuntu recentes não existe `pip` solto e o Python do sistema não aceita pacotes. Use o pipx (acima) ou um ambiente virtual.
- **`zsh: permissão negada: nimbus`** ou **`command not found: nimbus`** fora da pasta do projeto: o comando só estava instalado no venv. Confira com `type -a nimbus`; se não aparecer um caminho como `~/.local/bin/nimbus`, instale com o pipx, rode `pipx ensurepath` e abra um terminal novo.
- **`mpv não encontrado`**: instale com `brew install mpv` ou `sudo apt install mpv`.

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
nimbus tui ~/Música         # ou com uma pasta do computador
nimbus tui --offline        # sem consultar MusicBrainz/Cover Art Archive
```

Em cima fica o painel **Playback**: capa do álbum, título • artista, álbum • gênero • ano, o ícone de tocando/pausado, repeat, shuffle, volume, a posição na fila e uma barra de progresso. Embaixo, **Directories**: a árvore de pastas (Meu Drive, Compartilhados comigo e Este computador) à esquerda e o conteúdo da pasta selecionada à direita. `Enter` numa música toca a pasta inteira a partir dela. Para economizar banda e chamadas à API, uma pasta do Drive só é listada à direita quando você aperta `Enter` ou `→`/`l` nela; pastas já abertas e as do computador aparecem na hora, durante a navegação.

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
| `h` / `l` | na árvore fecha a pasta ou sobe / abre a pasta (e, se já aberta, vai para a lista); na lista volta para a árvore / entra na pasta |
| `g` / `G` | primeiro / último item |
| `Tab` | alterna entre árvore e lista |
| `Enter` | toca a música ou entra na subpasta |
| `?` | ajuda |
| `q` | sair |

### Músicas do computador, HD externo e pendrive

A raiz **Este computador** da árvore mostra a pasta pessoal, os discos externos montados e o disco do sistema (`/`). Os discos externos são procurados onde cada sistema os monta:

- macOS: `/Volumes` (o disco do sistema, que lá aparece como atalho para `/`, fica de fora);
- Linux: `/media/<usuário>/…` (Ubuntu, Debian), `/run/media/<usuário>/…` (Fedora, Arch), `/media/…` e os pontos de montagem em `/mnt`.

Um pendrive conectado com a interface já aberta só aparece ao reabrir o nimbus. Pastas e arquivos ocultos (começando com `.`) não aparecem, e listas de reprodução (`.m3u`, `.pls`) não entram na fila. O mpv lê os arquivos direto do disco, e a capa vem do próprio arquivo ou de uma imagem da pasta, como no Drive.

Sem login no Google, `nimbus` abre só com **Este computador**.

### Capa e metadados

O nimbus usa, nesta ordem, o que estiver disponível:

1. Tags do arquivo lidas pelo mpv (título, artista, álbum, gênero, ano).
2. O nome do arquivo (`01 - Artista - Título.mp3`) e da pasta, quando faltam tags.
3. A capa embutida no MP3 ou FLAC. Só o começo do arquivo é lido, onde a capa fica.
4. Uma imagem na mesma pasta do Drive (`cover.jpg`, `folder.png`, `capa.jpg`…).
5. [MusicBrainz](https://musicbrainz.org) para completar álbum, ano e gênero, e o [Cover Art Archive](https://coverartarchive.org) para a capa. Se o Cover Art Archive não tiver a capa ou não for acessível na sua rede, a busca pública do iTunes e, depois, a do Deezer servem de reserva. Só artista, título e álbum são enviados. Use `--offline` ou `NIMBUS_OFFLINE=1` para desligar.

Em coletâneas (pastas com faixas de artistas diferentes, pelo nome dos arquivos) a capa vem primeiro do disco original de cada música no Cover Art Archive; a capa embutida e a imagem da pasta ficam como reserva, já que costumam ser iguais em todas as faixas.

Tags de propaganda (telefone, WhatsApp, links, como "DJ FULANO 62999999999 WHATSAPP") são ignoradas e o nome do arquivo assume; nesses arquivos a capa embutida também é ignorada.

Cada música resolvida fica salva em `~/.cache/nimbus/tracks.json`, pelo ID do arquivo no Drive: da próxima vez, título, artista, álbum e capa corrigidos aparecem na hora, sem consultar o Drive nem a internet ("salvo" na linha de dados). Nada é gravado no Drive, que continua somente leitura. Se a internet falhar, a faixa é consultada de novo na próxima vez. Apague `tracks.json` para refazer tudo. Gravar as correções nos próprios arquivos está no [backlog](ROADMAP.pt-BR.md).

Capas e respostas ficam em cache em `~/.cache/nimbus`. A linha "dados: … · capa: …" no painel mostra de onde veio cada coisa.

A capa é desenhada com blocos coloridos, o que funciona em qualquer terminal com cores. Em terminais com imagens de verdade dá para pedir mais resolução: `NIMBUS_COVER=kitty` (kitty, Ghostty, WezTerm), `NIMBUS_COVER=sixel` (foot, WezTerm, Konsole, iTerm2) ou `NIMBUS_COVER=auto` para detectar. Se aparecer lixo no lugar da capa, volte para o padrão (`NIMBUS_COVER=blocks`). `NIMBUS_COVER=off` esconde a capa.

## Linha de comando

```sh
nimbus ls                          # raiz de Meu Drive
nimbus ls "Música/Rock"            # caminho a partir de Meu Drive (maiúsculas não importam)
nimbus ls https://drive.google.com/drive/folders/<id>   # URL ou ID de pasta, inclusive compartilhadas
nimbus play "Música/Rock"          # toca a pasta em ordem
nimbus play "Música" -r -s         # inclui subpastas, ordem aleatória

nimbus ls ~/Música                 # pasta do computador
nimbus play /Volumes/PENDRIVE -r   # pendrive no macOS
nimbus play /media/$USER/HD/Discos -s   # HD externo no Linux
nimbus ls "Este computador"        # pasta pessoal e discos montados

nimbus ls "Compartilhados comigo"                 # o que outras pessoas compartilharam com você
nimbus ls "Compartilhados comigo/Discos do Amigo"
nimbus play "Compartilhados comigo/Discos do Amigo" -r
```

"Compartilhados comigo" aparece como uma pasta na raiz do `nimbus ls`. Também aceita `Shared with me`, `@compartilhados` ou `@shared`, útil se você tiver uma pasta própria com esse nome. Pastas compartilhadas também podem ser abertas pela URL ou pelo ID, como qualquer outra.

Um caminho é do computador quando começa com `/`, `~`, `./` ou `../` (também aceita `file://…`). Qualquer outro é procurado no Drive, então para uma pasta relativa do computador use `./Discos`, não só `Discos`.

### Autocomplete de pastas (zsh e bash)

Com o autocomplete ativo, `Tab` completa os nomes das pastas do Drive em `nimbus ls` e `nimbus play`, uma parte do caminho por vez, inclusive dentro de "Compartilhados comigo". Caminhos do computador (`~/Mú<Tab>`, `/Volumes/<Tab>`) também completam. Maiúsculas e acentos não precisam bater.

zsh (padrão no macOS): adicione ao fim do `~/.zshrc`

```sh
autoload -Uz compinit && compinit   # se o seu .zshrc ainda não tiver (oh-my-zsh já faz isso)
eval "$(nimbus completion zsh)"
```

bash: adicione ao `~/.bashrc` (no macOS, `~/.bash_profile`)

```sh
eval "$(nimbus completion bash)"
```

Abra um terminal novo e teste com `nimbus ls Mú<Tab>`. O `eval` precisa do comando `nimbus` no PATH, por isso use a instalação com pipx. As listagens ficam em cache por 5 minutos em `~/.cache/nimbus`, então uma pasta recém-criada pode levar esse tempo para aparecer no `Tab`.

Teclas durante o `nimbus play`: `espaço` pausa, `n` próxima, `p` anterior, `←`/`→` voltam ou avançam 10s, `+`/`-` volume, `q` sai.

## Como funciona

- `drive.py` lista pastas e arquivos pela Drive API v3 (com suporte a drives compartilhados, "Compartilhados comigo" e atalhos). Áudio é detectado pelo tipo MIME ou pela extensão.
- `player.py` inicia o mpv em modo ocioso e o controla pelo socket JSON IPC. Cada faixa é a URL `files/<id>?alt=media` da API, com o cabeçalho `Authorization: Bearer …` enviado pelo socket, nunca na linha de comando.
- `local.py` lista pastas do computador e encontra os discos montados; `library.py` junta Drive e computador e encaminha cada pedido para a origem certa. Itens locais têm ID `local:<caminho>`.
- `playback.py` cuida da fila e pede um token renovado antes de cada faixa do Drive. Arquivos locais vão para o mpv pelo caminho, sem token.
- `tui.py` é a interface visual (Textual). Os eventos do mpv chegam numa thread própria e a tela só lê o estado num timer, então rede e IPC não travam a interface.
- `metadata.py` junta tags, nome do arquivo, capa embutida, imagem da pasta e MusicBrainz/Cover Art Archive.
- `cli.py` tem os comandos de linha de comando e abre a interface quando não há comando.
- `completion.py` gera os scripts de autocomplete e responde ao `Tab` com as pastas do Drive.

Variáveis úteis: `NIMBUS_CONFIG_DIR` muda a pasta de configuração, `NIMBUS_MPV` aponta para outro executável do mpv, `NIMBUS_COVER` escolhe como desenhar a capa (`blocks`, `kitty`, `sixel`, `auto`, `off`) e `NIMBUS_OFFLINE=1` desliga as consultas à internet.

## Testes

```sh
source .venv/bin/activate   # ambiente virtual de desenvolvimento (acima)
pytest
```

Os testes de reprodução e da interface usam um mpv real e um servidor HTTP local que imita o Drive (exige o token e responde a requisições Range).
