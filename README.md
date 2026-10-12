# FluxHub Backend — segurança da API

Este FastAPI requer um **token de serviço** para todas as rotas, incluindo `/api/v1/products/`, `/docs` e `/openapi.json`. Apenas `GET /api/v1/health` permanece público para monitoramento (responde apenas `{"status":"online"}`).

## Configurar o token nos DOIS serviços

Gere uma chave aleatória (não use sua senha de login):

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Configure **o mesmo valor** em ambiente privado, com o nome `BACKEND_TOKEN`:

- **FastAPI:** `BACKEND_TOKEN` usado pelo middleware de autenticação.
- **Next.js:** `BACKEND_TOKEN` usado em `Authorization: Bearer ...` nas chamadas privadas ao FastAPI.

Não faça commit do segredo, não envie ao navegador e **não use `NEXT_PUBLIC_BACKEND_TOKEN`**. O token deve ter no mínimo 32 caracteres, sem espaços nas extremidades. Sem token válido, a API retorna `503` (configuração ausente) ou `401` (cabeçalho inválido).

Configure a variável antes de implantar/reiniciar o backend; o FastAPI lê as variáveis na inicialização.

## Compatibilidade de aplicação

Os dois comandos usam a mesma instância protegida:

```bash
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
# ou
python -m uvicorn main:app --host 127.0.0.1 --port 8000
```

O frontend já consegue adicionar esse cabeçalho somente pelo Next.js. Para testar diretamente sem revelar o segredo a outras pessoas:

```bash
# Defina BACKEND_TOKEN no terminal de forma privada.
curl -i http://127.0.0.1:8000/api/v1/products/
curl -i -H "Authorization: Bearer $BACKEND_TOKEN" http://127.0.0.1:8000/api/v1/products/
curl -i -H "Authorization: Bearer $BACKEND_TOKEN" http://127.0.0.1:8000/openapi.json
```

A primeira deve retornar 401, as outras 200 quando o banco e a API estiverem disponíveis. O endereço público com o prefixo `/backend-api` pode ser usado se o proxy remove esse prefixo ao encaminhar ao FastAPI.

**Swagger protegido:** a URL `/docs` não abre diretamente no navegador sem `Authorization`. Isso evita a exposição pública da documentação. Para testar endpoints, use o OpenAPI por curl/Postman com token, ou consulte os endpoints GET na Central de Dados do FluxHub.

## Implantação segura

1. Configure `BACKEND_TOKEN` primeiro no ambiente do Next.js e no ambiente FastAPI, com o mesmo valor, e atualize a versão do frontend que exige token.
2. Publique o backend protegido. Faça redeploy/reinicie os dois serviços.
3. Verifique que o navegador não consegue acessar diretamente `/backend-api/api/v1/products/`, `/backend-api/docs` e `/backend-api/openapi.json` sem token, mas que a Central de Dados consegue consultar.
4. Restrinja o FastAPI com firewall/Nginx às redes/servidores necessários. Se usar o endereço público `http://187.77.230.250`, configure **HTTPS/TLS** antes de transportar tokens reais. HTTP transmite o bearer token sem criptografia.

**Importante:** o token de serviço não substitui a autenticação do usuário no Next.js. A senha humana fica fora do backend, e a conta do frontend é configurada separadamente por hash/sessão.

## Testes

```bash
pip install -r requirements.txt
pytest tests/test_api_auth.py -q
```
