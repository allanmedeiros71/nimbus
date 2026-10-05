# nimbus

Player de terminal (Linux e macOS) que toca em streaming as músicas das suas pastas do Google Drive. O acesso é somente leitura e nada é baixado antes: o mpv lê cada faixa por partes, uns dois minutos à frente do que está tocando.

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

## Uso

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

Abra um terminal novo e teste com `nimbus ls Mú<Tab>`. O `eval` precisa do comando `nimbus` no PATH, por isso use a instalação com pipx. As listagens ficam em cache por 5 minutos em `~/.cache/nimbus`, então uma pasta recém-criada pode levar esse tempo para aparecer no `Tab`.

Teclas durante a reprodução: `espaço` pausa, `n` próxima, `p` anterior, `←`/`→` voltam ou avançam 10s, `+`/`-` volume, `q` sai.

## Como funciona

- `drive.py` lista pastas e arquivos pela Drive API v3 (com suporte a drives compartilhados, "Compartilhados comigo" e atalhos). Áudio é detectado pelo tipo MIME ou pela extensão.
- `player.py` inicia o mpv em modo ocioso e o controla pelo socket JSON IPC. Cada faixa é a URL `files/<id>?alt=media` da API, com o cabeçalho `Authorization: Bearer …` enviado pelo socket, nunca na linha de comando.
- `playback.py` cuida da fila e pede um token renovado antes de cada faixa.
- `cli.py` é a interface mínima de linha de comando.
- `completion.py` gera os scripts de autocomplete e responde ao `Tab` com as pastas do Drive.

Variáveis úteis: `NIMBUS_CONFIG_DIR` muda a pasta de configuração, `NIMBUS_MPV` aponta para outro executável do mpv.

## Testes

```sh
source .venv/bin/activate   # ambiente virtual de desenvolvimento (acima)
pytest
```

Os testes de reprodução usam um mpv real e um servidor HTTP local que imita o Drive (exige o token e responde a requisições Range).
