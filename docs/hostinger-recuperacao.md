# Recuperação do backend FluxHub — Hostinger

## Diagnóstico

A VPS registrou `SyntaxError` por marcadores de merge em `app/main.py`,
além de um marcador `=======` no `.env`. O Nginx encaminha
`/backend-api/` para `127.0.0.1:8000`; se o FastAPI não inicializar,
o proxy responde 502.

## Antes do deploy

- **Não** publique `.env`, senhas, tokens, bancos `.db`, nem o diretório `.venv`.
- A correção do repositório não repara automaticamente o `.env` da VPS.
- Faça backup privado do `.env` e de qualquer arquivo alterado.
- Pare o serviço enquanto resolve os conflitos locais:

```bash
sudo systemctl stop fluxhub-backend
cd /var/www/fluxhub/backend
sudo cp -p .env /root/fluxhub-env-backup-$(date +%Y%m%d-%H%M%S)
git status
git diff --name-only --diff-filter=U
```

Revise os dois lados dos conflitos no `.env` SEM divulgar credenciais.
Mantenha valores reais válidos em formato `KEY=VALUE` e remova
`<<<<<<<`, `=======` e `>>>>>>>`. Não execute `git reset --hard`
nem substitua o arquivo por `.env.example`.

A versão de segurança exige `BACKEND_TOKEN` aleatório com pelo menos
32 caracteres, configurado **igualmente e em segredo** no backend e no
servidor Next.js. Não use `NEXT_PUBLIC_BACKEND_TOKEN`.

Quando os conflitos locais estiverem resolvidos, atualize seu checkout
seguindo o fluxo Git adequado (sem sobrescrever arquivos locais),
valide os arquivos e reinicie:

```bash
cd /var/www/fluxhub/backend
.venv/bin/python -m compileall -q app main.py
sudo systemctl restart fluxhub-backend
sudo systemctl status fluxhub-backend --no-pager -l
sudo journalctl -u fluxhub-backend -n 50 --no-pager -l
curl -i http://127.0.0.1:8000/api/v1/health
```

Swagger pelo Nginx: `https://myfluxhub.tech/backend-api/docs`.
**Aviso:** o middleware de segurança protege também `/docs` e
`/openapi.json`: o navegador sem credenciais não poderá abrir
a interface diretamente. Não desative essa proteção sem outra forma de
controle de acesso.

Esta documentação não comprova que a VPS já esteja corrigida.
