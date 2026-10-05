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

Teclas durante a reprodução: `espaço` pausa, `n` próxima, `p` anterior, `←`/`→` voltam ou avançam 10s, `+`/`-` volume, `q` sai.

## Como funciona

- `drive.py` lista pastas e arquivos pela Drive API v3 (com suporte a drives compartilhados, "Compartilhados comigo" e atalhos). Áudio é detectado pelo tipo MIME ou pela extensão.
- `player.py` inicia o mpv em modo ocioso e o controla pelo socket JSON IPC. Cada faixa é a URL `files/<id>?alt=media` da API, com o cabeçalho `Authorization: Bearer …` enviado pelo socket, nunca na linha de comando.
- `playback.py` cuida da fila e pede um token renovado antes de cada faixa.
- `cli.py` é a interface mínima de linha de comando.

Variáveis úteis: `NIMBUS_CONFIG_DIR` muda a pasta de configuração, `NIMBUS_MPV` aponta para outro executável do mpv.

## Testes

```sh
source .venv/bin/activate
pip install -e '.[dev]'
pytest
```

Os testes de reprodução usam um mpv real e um servidor HTTP local que imita o Drive (exige o token e responde a requisições Range).
