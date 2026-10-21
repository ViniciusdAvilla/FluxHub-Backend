#!/usr/bin/env python3
"""Ativação única do vendedor Mercado Livre usando apenas a API privada FluxHub.

Executar na VPS: python3 scripts/activate_mercado_livre.py
Usa somente stdlib Python; não grava nem mostra segredos ou tokens.
"""
import argparse
import getpass
import json
import os
from pathlib import Path
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


PUBLIC_CALLBACK = "https://myfluxhub.tech/api/v1/connectors/mercado-livre/callback"
AUTH_HOST = "auth.mercadolivre.com.br"
PROJECT_ROOT = Path(__file__).resolve().parents[1]


class SetupError(Exception):
    pass


def service_key(environ=None, dotenv_path=None):
    """Lê apenas BACKEND_TOKEN. Não executa nem importa as outras variáveis."""
    environ = os.environ if environ is None else environ
    value = environ.get("BACKEND_TOKEN", "")
    if value:
        return value
    path = PROJECT_ROOT / ".env" if dotenv_path is None else Path(dotenv_path)
    try:
        rows = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        return ""
    for line in rows:
        if not line.strip().startswith("BACKEND_TOKEN="):
            continue
        raw = line.strip().partition("=")[2].strip()
        if len(raw) > 1 and raw[0] in ("'", '"') and raw[-1] == raw[0]:
            raw = raw[1:-1]
        return raw
    return ""


def validate_api_base(base):
    url = urlsplit(base)
    # Nunca envie credenciais por HTTP para fora do servidor.
    if url.scheme not in {"http", "https"} or url.username or url.password:
        raise SetupError("Endereço da API inválido.")
    if url.path.rstrip("/") not in ("", "/api/v1") or url.query or url.fragment:
        raise SetupError("Use apenas a raiz da API, sem query ou caminho adicional.")
    if url.scheme == "http" and url.hostname not in ("127.0.0.1", "localhost", "::1"):
        raise SetupError("HTTP só é permitido para a API local (127.0.0.1).")
    if not url.hostname:
        raise SetupError("Informe o endereço da API.")
    return base.rstrip("/").removesuffix("/api/v1") + "/api/v1"


class _NoRedirect(HTTPRedirectHandler):
    """Não reenviar X-Backend-Token/JWT se a API tentar redirecionar."""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def api_request(api_base, service_token, path, *, jwt=None, payload=None, timeout=20):
    if not path.startswith("/") or path.startswith("//") or "?" in path:
        raise SetupError("Caminho de consulta inválido.")
    headers = {"Accept": "application/json", "X-Backend-Token": service_token}
    if jwt:
        headers["Authorization"] = "Bearer " + jwt
    data = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(payload).encode("utf-8")
    request = Request(api_base + path, headers=headers, data=data,
                      method="POST" if payload is not None else "GET")
    try:
        with build_opener(_NoRedirect()).open(request, timeout=timeout) as response:
            result = json.load(response)
    except HTTPError as error:
        # Não reproduzimos response body: pode incluir detalhes não sanitizados.
        hints = {401: "BACKEND_TOKEN ou login FluxHub inválido.",
                 403: "Usuário sem permissão.",
                 404: "Rota não encontrada. Atualize e reinicie o FastAPI.",
                 422: "Parâmetros rejeitados.",
                 503: "Configuração incompleta no FastAPI."}
        raise SetupError(
            f"FastAPI retornou HTTP {error.code}. "
            + hints.get(error.code, "Consulte os logs privados do FastAPI.")
        ) from None
    except (URLError, TimeoutError, OSError):
        raise SetupError(
            "Não consegui acessar o FastAPI. Confira se ele está em execução "
            "em 127.0.0.1:8000 e use os logs privados do serviço."
        ) from None
    except (ValueError, UnicodeError):
        raise SetupError("O FastAPI não retornou JSON válido.") from None
    return result


def validate_authorization_url(url):
    """Bloqueia links arbitrários recebidos de um backend mal configurado."""
    parts = urlsplit(url)
    params = parse_qs(parts.query)
    if (parts.scheme != "https" or parts.hostname != AUTH_HOST or parts.port not in (None, 443)
            or parts.username or parts.password or parts.path != "/authorization"
            or parts.fragment):
        raise SetupError("O backend retornou um destino OAuth não esperado.")
    if (params.get("redirect_uri") != [PUBLIC_CALLBACK]
            or params.get("response_type") != ["code"]
            or len(params.get("state", [])) != 1
            or len(params["state"][0]) < 16
            or len(params.get("client_id", [])) != 1
            or not params["client_id"][0].isdigit()):
        raise SetupError(
            "O backend retornou OAuth incorreto: confira MERCADO_LIVRE_REDIRECT_URI "
            "e a configuração privada do aplicativo."
        )
    if "code_challenge" in params:
        if params.get("code_challenge_method") != ["S256"]:
            raise SetupError("PKCE deve usar S256.")
    return params


def describe(diag):
    if not isinstance(diag, dict):
        raise SetupError("Resposta inesperada da rota /diagnostics.")
    print("\nDiagnóstico privado do backend (sem tokens):")
    print("  Credenciais do aplicativo:", "configuradas" if diag.get("app_credentials_configured") else "ausentes")
    print("  Callback:", diag.get("redirect_uri", "indisponível"))
    print("  PKCE:", "ativado" if diag.get("pkce_enabled") else "desativado")
    print("  Contas OAuth cadastradas:", diag.get("connection_count", "indisponível"))
    for account in diag.get("accounts", []):
        print("  Vendedor:", account.get("seller_id"), "|", account.get("nickname") or "sem nome")
        print("    Fonte ativa:", bool(account.get("source_connected") and account.get("source_enabled")))
        print("    Última sincronização:", account.get("last_synced_at") or "nenhuma")
    return diag.get("connection_count", 0)


def run(argv=None):
    parser = argparse.ArgumentParser(description="Ativar vendedor Mercado Livre no FastAPI/FluxHub")
    parser.add_argument("--check", action="store_true",
                        help="Somente consultar a situação atual, sem gerar link OAuth")
    parser.add_argument("--no-wait", action="store_true",
                        help="Gerar link sem esperar a conclusão no terminal")
    parser.add_argument("--api", default="http://127.0.0.1:8000",
                        help="Endereço privado do FastAPI, padrão: http://127.0.0.1:8000")
    args = parser.parse_args(argv)

    api_base = validate_api_base(args.api)
    key = service_key()
    if not key:
        key = getpass.getpass("BACKEND_TOKEN do serviço (entrada oculta): ")
    if len(key) < 32 or len(key) > 2048 or key != key.strip():
        raise SetupError("BACKEND_TOKEN ausente ou inválido. Confira o .env do backend.")

    email = input("E-mail da conta FluxHub usada no painel: ").strip().lower()
    if not email or "@" not in email:
        raise SetupError("E-mail FluxHub inválido.")
    password = getpass.getpass("Senha FluxHub (entrada oculta): ")
    login = api_request(api_base, key, "/auth/login",
                        payload={"email": email, "password": password})
    password = None
    jwt = login.get("access_token") if isinstance(login, dict) else None
    if not isinstance(jwt, str) or not jwt:
        raise SetupError("Login FluxHub não retornou token de sessão.")
    identity = api_request(api_base, key, "/auth/me", jwt=jwt)
    if not isinstance(identity, dict) or identity.get("email", "").lower() != email:
        raise SetupError("Sessão FluxHub incompatível com a conta informada.")
    print("\nUsuário FluxHub confirmado. ID:", identity.get("id"))

    diag = api_request(api_base, key, "/connectors/mercado-livre/diagnostics", jwt=jwt)
    previous = describe(diag)
    if args.check:
        return 0

    if not diag.get("app_credentials_configured"):
        raise SetupError("Configure MERCADO_LIVRE_CLIENT_ID e CLIENT_SECRET no FastAPI.")
    if diag.get("redirect_uri") != PUBLIC_CALLBACK:
        raise SetupError(
            "Corrija MERCADO_LIVRE_REDIRECT_URI no processo FastAPI e reinicie o serviço."
        )

    result = api_request(api_base, key, "/connectors/mercado-livre/authorize", jwt=jwt)
    url = result.get("authorization_url") if isinstance(result, dict) else None
    if not isinstance(url, str):
        raise SetupError("O backend não retornou URL de autorização.")
    params = validate_authorization_url(url)
    if bool(params.get("code_challenge")) != bool(diag.get("pkce_enabled")):
        raise SetupError("PKCE inconsistente. Reinicie o backend com o .env correto.")

    print("\nABRA ESTE LINK UMA ÚNICA VEZ no navegador, usando a conta principal do vendedor:")
    print(url)
    print("\nLink privado, válido por 10 minutos. Não publique nem cole em conversas.")
    print("Se o Mercado Livre mostrar erro, gere um NOVO link; código e state são de uso único.")
    if args.no_wait:
        return 0
    print("\nAguardando o retorno do Mercado Livre por até 10 minutos... (Ctrl+C encerra)")
    start = time.monotonic()
    initial_expiry = {
        str(row.get("seller_id")): str(row.get("token_expires_at"))
        for row in diag.get("accounts", [])
    }
    while time.monotonic() - start < 600:
        time.sleep(5)
        try:
            latest = api_request(
                api_base, key, "/connectors/mercado-livre/diagnostics",
                jwt=jwt, timeout=10,
            )
        except SetupError:
            continue
        for account in latest.get("accounts", []):
            seller_id = str(account.get("seller_id"))
            expiry = str(account.get("token_expires_at"))
            if seller_id not in initial_expiry or initial_expiry[seller_id] != expiry:
                print("\nAutorização registrada no PostgreSQL!")
                describe(latest)
                print("Abra o FluxHub e clique em Atualizar dados do Mercado Livre.")
                return 0

    print("\nNenhuma nova autorização foi detectada no PostgreSQL.")
    print("Se você autorizou no navegador, veja os logs privados FastAPI por")
    print("phase=token_exchange, provider_error=invalid_grant/invalid_client ou phase=profile.")
    return 1


if __name__ == "__main__":
    try:
        sys.exit(run())
    except SetupError as error:
        print("\nERRO:", error, file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print("\nOperação interrompida, sem alterar tokens manualmente.")
        sys.exit(130)
