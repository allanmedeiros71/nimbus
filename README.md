# nimbus

Player de terminal (Linux e macOS) que toca em streaming as músicas das suas pastas do Google Drive. O acesso é somente leitura e nada é baixado antes: o mpv lê cada faixa por partes, uns dois minutos à frente do que está tocando.

## Requisitos

- Python 3.9 ou mais novo
- [mpv](https://mpv.io): `brew install mpv` no macOS, `sudo apt install mpv` no Debian/Ubuntu

## Instalação

```sh
pipx install git+https://github.com/<seu-usuario>/nimbus
# ou, a partir do código:
pip install -e .
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
```

Teclas durante a reprodução: `espaço` pausa, `n` próxima, `p` anterior, `←`/`→` voltam ou avançam 10s, `+`/`-` volume, `q` sai.

## Como funciona

- `drive.py` lista pastas e arquivos pela Drive API v3 (com suporte a drives compartilhados e atalhos). Áudio é detectado pelo tipo MIME ou pela extensão.
- `player.py` inicia o mpv em modo ocioso e o controla pelo socket JSON IPC. Cada faixa é a URL `files/<id>?alt=media` da API, com o cabeçalho `Authorization: Bearer …` enviado pelo socket, nunca na linha de comando.
- `playback.py` cuida da fila e pede um token renovado antes de cada faixa.
- `cli.py` é a interface mínima de linha de comando.

Variáveis úteis: `NIMBUS_CONFIG_DIR` muda a pasta de configuração, `NIMBUS_MPV` aponta para outro executável do mpv.

## Testes

```sh
pip install -e '.[dev]'
pytest
```

Os testes de reprodução usam um mpv real e um servidor HTTP local que imita o Drive (exige o token e responde a requisições Range).
