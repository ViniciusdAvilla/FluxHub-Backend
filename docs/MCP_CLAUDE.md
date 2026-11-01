# MCP do FluxHub: dashboard + Claude externo

## O que foi adicionado

- Assistente Claude em /agentes-claude: ferramentas reais de consulta (vendas, produtos vendidos, estoque, Product Ads e resumo). Consultas sempre isoladas por user_id.
- Servidor MCP externo (Streamable HTTP stateless) em https://myfluxhub.tech/mcp.
- Autorização OAuth 2.1 com PKCE S256, consentimento do vendedor no FluxHub, troca de código única, refresh token rotativo e revogação por usuário.
- A chave Anthropic pessoal permanece criptografada; o login e os tokens do Mercado Livre NÃO mudam.
- Chave da Claude API e tokens OAuth do MCP são credenciais diferentes.

## Ativação no servidor

1. Atualizar os repositórios FastAPI e Next.js **juntos**. Frontend e backend contêm rotas complementares.
2. Manter BACKEND_TOKEN igual em ambos; preservar JWT_SECRET_KEY, AUTH_SESSION_SECRET e, **sobretudo, TOKEN_ENCRYPTION_KEY**. Nunca gerar nova TOKEN_ENCRYPTION_KEY no deploy.
3. Confirmar que FRONTEND_URL no backend é https://myfluxhub.tech (mesma origem das URLs públicas do MCP).
4. Reiniciar FastAPI; o lifespan executa ensure_mcp_schema() e cria somente três tabelas novas. Se DDL automático estiver restrito, executar no diretório backend: .venv/bin/python -m scripts.migrate_mcp.
5. Build/start Next.js; verificar acesso HTTPS às rotas /mcp, /.well-known/oauth-protected-resource e /.well-known/oauth-authorization-server. A rota /mcp deve responder HTTP 401 com WWW-Authenticate quando não há token, não 404 ou HTML.
6. Validar /agentes-claude com uma chave de API com créditos no Anthropic Console. Salvar/testar utiliza uma mensagem curta real com a Messages API, que pode consumir tokens.
7. No Claude Web > Customize > Connectors > Add custom connector: nome "FluxHub", URL https://myfluxhub.tech/mcp, OAuth automático. Autorizar com login do vendedor FluxHub. A configuração e disponibilidade dos conectores variam conforme plano e políticas de workspace.
8. Revogar acessos em Agentes Claude > Conector MCP externo > Revogar conexões.

## Testes de aceitação

- GET /.well-known/oauth-protected-resource deve listar authorization_servers e resource https://myfluxhub.tech/mcp.
- GET /.well-known/oauth-authorization-server deve listar endpoints do OAuth, DCR e PKCE S256.
- POST /mcp sem Bearer válido retorna 401 com metadata; nunca permite usar cookie de login FluxHub como token MCP.
- POST /mcp com token válido suporta initialize, ping, tools/list, tools/call e retorna apenas dados da própria conta.
- Refresh token anterior não pode ser reutilizado; revogação invalida imediatamente os access tokens.
- Configuração Claude valida a chamada real /v1/messages, incluindo problemas de cobrança ou modelo.
- Claude responde consultas de dados usando ferramentas; sempre identifica valores brutos e cobertura parcial.

## Segurança e escopo

- MCP não executa SQL arbitrário nem comandos na VPS; ferramentas são listas fixas e somente leitura.
- Não exporta nome, endereço, documento, contato de compradores, tokens OAuth ou API keys.
- OAuth gera códigos curtos e descartáveis; refresh tokens ficam somente como hashes SHA-256 no banco.
- Nenhuma tabela ou conexão existente do Mercado Livre é apagada ou recriada.
- Não existe limite mensal de uso imposto pelo FluxHub; permanece intervalo de 30 s entre mensagens do assistente e a cobrança/limites do provedor.
- O MCP é servido no mesmo Next.js e backend. Não é necessário Kondado, Hunter HUB, Supabase nem serviço de terceiros.
- A configuração do Nginx deve permitir /mcp e /.well-known/* chegarem ao Next.js; se existir roteamento especial, revisar antes de publicar.
