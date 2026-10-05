"""Login OAuth no Google com escopo somente leitura do Drive."""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials

from nimbus import config

SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]

# Renova o token se faltar menos que isso para expirar, para que uma faixa
# começada agora não perca o acesso no meio.
REFRESH_MARGIN = timedelta(minutes=5)


class AuthError(Exception):
    pass


class NotLoggedIn(AuthError):
    pass


def _save(creds: Credentials, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(creds.to_json())
    os.chmod(path, 0o600)


def login(client_secret: Path | None = None) -> Credentials:
    """Abre o navegador para autorizar o nimbus e salva o token."""
    from google_auth_oauthlib.flow import InstalledAppFlow

    secret = client_secret or config.client_secret_path()
    if not secret.exists():
        raise AuthError(
            f"Arquivo de credenciais OAuth não encontrado em {secret}.\n"
            "Crie um 'OAuth client ID' do tipo 'Desktop app' no Google Cloud Console, "
            "baixe o JSON e salve nesse caminho (veja o README)."
        )
    flow = InstalledAppFlow.from_client_secrets_file(str(secret), SCOPES)
    creds = flow.run_local_server(port=0, open_browser=True)
    _save(creds, config.token_path())
    return creds


def logout() -> bool:
    path = config.token_path()
    if path.exists():
        path.unlink()
        return True
    return False


def load_credentials() -> Credentials:
    """Carrega o token salvo, renovando se necessário."""
    path = config.token_path()
    if not path.exists():
        raise NotLoggedIn("Você ainda não fez login. Rode: nimbus login")
    creds = Credentials.from_authorized_user_file(str(path), SCOPES)
    ensure_fresh(creds)
    return creds


def _expiring_soon(creds: Credentials) -> bool:
    if not creds.valid:
        return True
    if creds.expiry is None:
        return False
    # google-auth guarda expiry como UTC sem fuso.
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    return creds.expiry - now < REFRESH_MARGIN


def ensure_fresh(creds: Credentials) -> None:
    if not _expiring_soon(creds):
        return
    if not creds.refresh_token:
        raise NotLoggedIn("O token expirou e não pode ser renovado. Rode: nimbus login")
    try:
        creds.refresh(Request())
    except Exception as e:  # RefreshError, erros de rede
        raise AuthError(f"Não foi possível renovar o acesso ao Google: {e}") from e
    _save(creds, config.token_path())


def access_token(creds: Credentials) -> str:
    """Token de acesso válido por pelo menos REFRESH_MARGIN."""
    ensure_fresh(creds)
    return creds.token
