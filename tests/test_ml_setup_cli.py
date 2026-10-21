"""Testes do ativador local: sem API externa ou credenciais reais."""
from pathlib import Path
from urllib.parse import urlencode

import pytest

from scripts import activate_mercado_livre as cli


def test_only_private_api_local_http_and_https():
    assert cli.validate_api_base("http://127.0.0.1:8000") == (
        "http://127.0.0.1:8000/api/v1")
    assert cli.validate_api_base("http://127.0.0.1:8000/api/v1") == (
        "http://127.0.0.1:8000/api/v1")
    for url in ("http://evil.example/api/v1",
                "http://example.com:8000",
                "http://user:pass@127.0.0.1:8000",
                "http://127.0.0.1:8000/internal",
                "file:///etc/passwd",
                "http://127.0.0.1:8000?token=oops"):
        with pytest.raises(cli.SetupError):
            cli.validate_api_base(url)


def test_load_backend_token_only_and_never_execute_dotenv(tmp_path):
    key = "s" * 48
    path = tmp_path / ".env"
    path.write_text(
        "MERCADO_LIVRE_CLIENT_SECRET=sensitive-do-not-expose\n"
        "BACKEND_TOKEN='" + key + "'\n"
        "OTHER=$(touch /tmp/not-a-command)\n",
        encoding="utf-8",
    )
    assert cli.service_key(environ={}, dotenv_path=path) == key
    assert cli.service_key(environ={"BACKEND_TOKEN": "a" * 48},
                           dotenv_path=path) == "a" * 48
    assert cli.service_key(environ={}, dotenv_path=tmp_path / "missing") == ""


def example_url(**kwargs):
    params = {
        "response_type": "code",
        "client_id": "1234567890",
        "redirect_uri": cli.PUBLIC_CALLBACK,
        "state": "test-state-with-more-than-sixteen-characters",
        **kwargs,
    }
    return "https://auth.mercadolivre.com.br/authorization?" + urlencode(params)


def test_only_official_ml_login_with_exact_public_callback():
    params = cli.validate_authorization_url(example_url(
        code_challenge="a" * 43, code_challenge_method="S256",
    ))
    assert params["client_id"] == ["1234567890"]
    for bad in (
        "https://evil.example/authorization?" + urlencode({
            "response_type": "code", "client_id": "1234",
            "state": "123456789012345678901234",
            "redirect_uri": cli.PUBLIC_CALLBACK,
        }),
        example_url(redirect_uri="https://myfluxhub.tech/backend-api/callback"),
        example_url(state="too-short"),
        example_url(client_id="abc"),
        example_url(response_type="token"),
        example_url(code_challenge="a" * 43, code_challenge_method="plain"),
        example_url().replace("/authorization?", "/redirect?"),
    ):
        with pytest.raises(cli.SetupError):
            cli.validate_authorization_url(bad)


def test_check_mode_uses_fluxhub_session_only_and_no_oauth(monkeypatch, capsys):
    called = []
    monkeypatch.setattr(cli, "service_key", lambda: "s" * 48)
    monkeypatch.setattr("builtins.input", lambda _: "admin@example.com")
    monkeypatch.setattr(cli.getpass, "getpass", lambda _: "test-only-password")

    def api(base, key, path, *, jwt=None, payload=None, timeout=20):
        called.append(path)
        assert key == "s" * 48
        if path == "/auth/login":
            assert payload["email"] == "admin@example.com"
            return {"access_token": "fake-jwt"}
        if path == "/auth/me":
            if jwt is None:
                raise cli.ApiAuthError("user_jwt_required", "JWT humano obrigatório.")
            assert jwt == "fake-jwt"
            return {"id": 7, "email": "admin@example.com"}
        if path == "/connectors/mercado-livre/diagnostics":
            return {
                "app_credentials_configured": True,
                "redirect_uri": cli.PUBLIC_CALLBACK,
                "pkce_enabled": False,
                "connection_count": 0,
                "accounts": [],
            }
        raise AssertionError("Rota não esperada: " + path)

    monkeypatch.setattr(cli, "api_request", api)
    assert cli.run(["--check"]) == 0
    assert "/connectors/mercado-livre/authorize" not in called
    assert "Contas OAuth cadastradas: 0" in capsys.readouterr().out


def test_refuses_bad_redirect_before_issuing_oauth_link(monkeypatch):
    called = []
    monkeypatch.setattr(cli, "service_key", lambda: "s" * 48)
    monkeypatch.setattr("builtins.input", lambda _: "admin@example.com")
    monkeypatch.setattr(cli.getpass, "getpass", lambda _: "test-password")
    def api(base, key, path, *, jwt=None, payload=None, timeout=20):
        called.append(path)
        if path == "/auth/login":
            return {"access_token": "jwt"}
        if path == "/auth/me":
            if jwt is None:
                raise cli.ApiAuthError("user_jwt_required", "JWT humano obrigatório.")
            return {"email": "admin@example.com", "id": 2}
        if path == "/connectors/mercado-livre/diagnostics":
            return {
                "app_credentials_configured": True,
                "redirect_uri": "https://example.com/wrong",
                "pkce_enabled": False,
                "connection_count": 0,
                "accounts": [],
            }
        raise AssertionError(path)
    monkeypatch.setattr(cli, "api_request", api)
    with pytest.raises(cli.SetupError, match="MERCADO_LIVRE_REDIRECT_URI"):
        cli.run(["--no-wait"])
    assert "/connectors/mercado-livre/authorize" not in called


def test_setup_never_follows_http_redirect_with_service_secrets():
    from urllib.request import Request
    handler = cli._NoRedirect()
    request = Request("http://127.0.0.1:8000/api/v1/auth/login",
                      headers={"X-Backend-Token": "secret"})
    assert handler.redirect_request(
        request, None, 302, "redirect", {},
        "https://external.example/steal"
    ) is None


def test_recognizes_service_vs_user_login_401_without_leaking_body():
    import io
    import json
    from urllib.error import HTTPError
    examples = (
        ("Não autorizado.", "service_token", "BACKEND_TOKEN recusado"),
        ("Credenciais inválidas.", "login", "neste PostgreSQL"),
        ("A valid FluxHub login is required.", "user_jwt_required", "JWT"),
    )
    for detail, kind, expected in examples:
        payload = json.dumps({"detail": detail, "sensitive": "NEVER_PRINT_ME"}).encode()
        response = HTTPError("http://127.0.0.1", 401, "Unauthorized",
                             {}, io.BytesIO(payload))
        diagnostic = cli._known_auth_error(response)
        assert isinstance(diagnostic, cli.ApiAuthError)
        assert diagnostic.kind == kind
        assert expected in str(diagnostic)
        assert "NEVER_PRINT_ME" not in str(diagnostic)
    unknown_response = HTTPError(
        "http://127.0.0.1", 401, "Unauthorized", {},
        io.BytesIO(b'{"detail":"a-secret-opaque-provider-error"}'))
    diagnostic = cli._known_auth_error(unknown_response)
    assert diagnostic.kind == "unknown_401"
    assert "a-secret-opaque-provider-error" not in str(diagnostic)


def test_wrong_service_token_rejected_before_asking_for_password(monkeypatch):
    monkeypatch.setattr(cli, "service_key", lambda: "wrong-token-" + "x" * 40)
    inputs = []
    def should_not_prompt(prompt):
        inputs.append(prompt)
        raise AssertionError("Não peça login FluxHub com token de serviço inválido")
    monkeypatch.setattr("builtins.input", should_not_prompt)
    monkeypatch.setattr(cli.getpass, "getpass", should_not_prompt)
    def fake_api(base, key, path, *, jwt=None, payload=None, timeout=20):
        assert path == "/auth/me"
        assert jwt is None
        raise cli.ApiAuthError("service_token", "BACKEND_TOKEN recusado pelo FastAPI.")
    monkeypatch.setattr(cli, "api_request", fake_api)
    with pytest.raises(cli.ApiAuthError, match="BACKEND_TOKEN recusado"):
        cli.run(["--check"])
    assert inputs == []


def test_valid_service_token_preflight_requires_human_jwt(monkeypatch, capsys):
    calls = []
    def fake_api(base, key, path, *, jwt=None, payload=None, timeout=20):
        calls.append((path, jwt))
        if path == "/auth/me" and jwt is None:
            raise cli.ApiAuthError("user_jwt_required", "JWT humano obrigatório.")
        raise AssertionError("não era esperado tentar outro login")
    monkeypatch.setattr(cli, "api_request", fake_api)
    cli.assert_service_token("http://127.0.0.1:8000/api/v1", "s" * 48)
    assert "BACKEND_TOKEN aceito" in capsys.readouterr().out
    assert calls == [("/auth/me", None)]
