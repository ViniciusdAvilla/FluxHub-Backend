# Recuperação da integração Mercado Livre no FluxHub

O navegador não pode acessar dados privados do vendedor com Client ID/Client
Secret isolados. Uma **única autorização OAuth do titular da conta** precisa
salvar `mercado_livre_connections` no PostgreSQL. Depois, o FastAPI renova
o token sem pedir login repetidamente.

## Implantação

Este repositório não tem uma rotina de implantação automática na VPS. Faça
backup da configuração privada e do banco, atualize a pasta do backend com
`git pull --ff-only origin main` e reinicie o serviço FastAPI existente
(systemd/Docker/PM2 conforme seu ambiente). **Não execute os testes que
usam `Base.metadata.drop_all` no banco de produção.** Eles são projetados
para um PostgreSQL descartável na CI.

O backend precisa ler no seu ambiente privado:
- `DATABASE_URL`: PostgreSQL efetivamente usado pela API;
- `JWT_SECRET_KEY`, `TOKEN_ENCRYPTION_KEY`, `BACKEND_TOKEN`: segredos
  existentes (não gire `TOKEN_ENCRYPTION_KEY` sem migrar tokens);
- `MERCADO_LIVRE_CLIENT_ID` e `MERCADO_LIVRE_CLIENT_SECRET` do app;
- `MERCADO_LIVRE_REDIRECT_URI=https://myfluxhub.tech/api/v1/connectors/mercado-livre/callback`;
- `MERCADO_LIVRE_PKCE_ENABLED=true` **se PKCE estiver habilitado no
  painel do aplicativo Mercado Livre**; caso contrário `false`.
- `FRONTEND_URL=https://myfluxhub.tech`.

O FastAPI não precisa de mudança de esquema PostgreSQL para PKCE: o
`code_verifier` é calculado no servidor com HMAC de segredo interno e
`state` válido, sem ser enviado ao frontend.

## Verifique a configuração ativa

1. Confirme que `GET http://127.0.0.1:8000/api/v1/health` retorna
   `{"status":"online"}` dentro da VPS.
2. Faça login no **FluxHub** com a mesma conta usada no painel.
   As rotas privadas exigem simultaneamente
   `X-Backend-Token: <BACKEND_TOKEN>` e
   `Authorization: Bearer <JWT do FluxHub>`.
3. A rota `GET /api/v1/connectors/mercado-livre/diagnostics` agora
   retorna um documento **sem tokens**:
   `app_credentials_configured`, `redirect_uri`, `pkce_enabled`,
   `connection_count`, e para o usuário autenticado, seller ID,
   fonte ativa e última sincronização.
4. Se `connection_count=0`, o banco não tem uma conexão OAuth desse
   usuário. Verifique também se as credenciais do app e o callback
   estão corretos e se PKCE corresponde ao painel Mercado Livre.

## Autorize uma única vez (servidor)

1. A partir do FastAPI autenticado com a conta FluxHub correta, execute
   `GET /api/v1/connectors/mercado-livre/authorize`. Essa rota gera um
   `state` exclusivo, com expiração de 10 minutos, e devolve a URL
   oficial `authorization_url`. Trate-a como privada.
2. Abra a URL **uma vez** no navegador com o usuário principal vendedor
   Mercado Livre (conta de colaborador não é aceita para esse grant).
3. O provedor devolve `code` e `state` ao callback público Next.js,
   que passa somente esses parâmetros ao FastAPI. Ele troca o código
   uma única vez e confere que o seller de `/users/me` corresponde ao
   `user_id` do token. Tokens são cifrados na tabela de conexões e
   a fonte `ml:<seller_id>` é ativada.
4. Se a autorização falhar, gere **um novo** `state` e novo link.
   Códigos OAuth e `state` são de uso único; não os registre em logs.
5. Confirme com `GET /api/v1/connectors/mercado-livre/diagnostics` que
   `connection_count=1` e `source_connected=true`.
6. Execute `POST /api/v1/analytics/sync?account_id=<seller_id>&limit=20&offset=0`
   com os mesmos cabeçalhos privados, ou use o botão de atualizar
   dados no frontend já conectado. Confira `listings` e `orders` no
   resultado; depois acesse Dashboard, Produtos e Pedidos.

## Diagnóstico privado em caso de erro

Acompanhe logs do processo FastAPI; o serviço registra apenas:

```text
[mercado-livre-oauth] phase=token_exchange status=400 provider_error=invalid_grant
[mercado-livre-oauth] phase=token_exchange status=401 provider_error=invalid_client
[mercado-livre-oauth] phase=profile status=403 provider_error=unknown
```

- `invalid_grant`: code usado/expirado, PKCE incorreto, callback divergente;
- `invalid_client`: credenciais rejeitadas — não as publique;
- `invalid_operator_user_id`: usuário Mercado Livre sem permissões;
- `phase=token_refresh`: refresh token revogado/expirado, pode exigir nova
  autorização inicial;
- `transport`: conectividade para `api.mercadolibre.com`.

Esses são **exemplos de categorias**, não erros observados no servidor do usuário.
O sistema nunca registra token, URL completa com code/state, segredo nem corpo
da resposta do Mercado Livre.

Após concluir a autorização, as credenciais do aplicativo permanecem no
backend; o frontend apenas consome dados do FastAPI. Se seu Client Secret
foi exposto em mensagens ou outros locais, substitua-o no painel e na VPS
em uma janela controlada, verificando o impacto nos tokens já existentes.


## Ativação inicial pela VPS — sem novo botão no frontend

O frontend FluxHub passou a mostrar somente o vendedor já autorizado.
Por isso, a autorização inicial deve ser feita pelo **próprio backend**.

Na pasta do `FluxHub-Backend`, depois de atualizar a branch `main` e
reiniciar o FastAPI, execute:

```bash
python3 scripts/activate_mercado_livre.py --check
python3 scripts/activate_mercado_livre.py
```

O ativador usa **somente a biblioteca padrão do Python 3**, sem SQLAlchemy ou
ambiente virtual para o cliente HTTP. O FastAPI continua rodando no ambiente
Python original e usa PostgreSQL.

- Lê o `BACKEND_TOKEN` do ambiente do terminal ou do arquivo `.env` privado
  da raiz do repositório; se não houver, solicita a chave **sem exibi-la**.
- Solicita e-mail/senha **da conta FluxHub**, também sem mostrar a senha,
  porque a API exige autenticação de serviço + JWT de usuário.
- Usa `/auth/me` para conferir o usuário e `/diagnostics` para verificar
  aplicativo, callback exato e PKCE.
- Obtém link oficial com `GET /connectors/mercado-livre/authorize` para o
  usuário autenticado (o `state` único fica no PostgreSQL).
- Mostra o link no terminal: abra no navegador em até 10 minutos, com a
  **conta principal vendedora**, autorize uma vez e aguarde a mensagem
  "Autorização registrada no PostgreSQL!". Não compartilhe esse link.
- Se a VPS não tiver shell interativo por tempo suficiente, use
  `python3 scripts/activate_mercado_livre.py --no-wait` e confira depois com
  `--check`.

É obrigatório que o callback Next.js
`https://myfluxhub.tech/api/v1/connectors/mercado-livre/callback`
esteja **publicado na VPS e encaminhando a autorização para o FastAPI**.
O `git pull` somente do backend não instala automaticamente a versão
atualizada do Next.js.

Se `--check` apontar zero contas, ainda falta uma autorização válida.
Client ID e Client Secret não substituem access_token/refresh_token do seller.
Se `--check` mostrar conta conectada mas os dados não importarem, use o
botão de sincronizar no frontend e procure nos logs privados por
`[mercado-livre-data] resource=item_search upstream_status=...`,
`resource=item_details`, ou `resource=orders`.
Os logs não contêm token, dados confidenciais nem URL de OAuth.


## Quando o ativador retorna HTTP 401

Na versão atual, antes de perguntar e-mail/senha, o script valida o
`X-Backend-Token` contra o **processo FastAPI ativo** chamando
`GET /api/v1/auth/me` sem JWT humano. A API deve responder
`A valid FluxHub login is required.`, o que comprova que a chave
de serviço foi aceita (o 401 sem JWT humano é intencional).

- **`BACKEND_TOKEN recusado pelo FastAPI`**: a chave lida pelo ativador
  de seu `BACKEND_TOKEN` do shell ou do `.env` local **não coincide** com
  a configuração real do processo FastAPI. Verifique se o systemd utiliza
  outro `EnvironmentFile`, se Docker fornece a variável separadamente,
  e se o servidor foi reiniciado depois de editar o ambiente. **Não**
  publique token em terminal compartilhado nem aqui.
- **`FastAPI recebeu a requisição, mas recusou o login FluxHub neste PostgreSQL`**:
  o serviço aceitou o BACKEND_TOKEN, mas `POST /auth/login` não
  encontrou usuário ativo com e-mail/senha válida **nesse banco**.
  Verifique a instalação efetivamente usada pelo painel, o banco alvo do
  FastAPI e a existência da conta sem exibir `password_hash`.
- **`BACKEND_TOKEN aceito pelo FastAPI`**: pode prosseguir com o e-mail e
  senha do painel. O script não imprime senha, JWT ou token de serviço.

Para identificar o serviço sem mostrar segredos:

```bash
systemctl list-units --type=service | grep -Ei 'fluxhub|fastapi|uvicorn'
```

A inspeção de `EnvironmentFile`, Docker, `/proc/*/environ` ou
`systemctl show` pode conter segredos. Não copie a saída bruta desses
comandos para chats ou GitHub. Se alterar `BACKEND_TOKEN`, ele deverá
continuar **idêntico** no FastAPI e Next.js; preferencialmente alinhe
o script à configuração existente, sem girar outras chaves.
