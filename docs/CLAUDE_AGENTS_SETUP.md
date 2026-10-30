# Agentes Claude no FluxHub

A tela /agentes-claude integra a API Anthropic ao FastAPI e aparece na
dashboard de todo vendedor autenticado. O assistente apenas analisa
indicadores consolidados da operação do usuário; não executa código na VPS
nem ações no Mercado Livre.

## Ativação segura

1. Atualize backend e frontend. Ao iniciar, o FastAPI cria apenas a nova tabela
   claude_provider_config, sem alterar os dados OAuth do Mercado Livre.
   Se o banco bloquear DDL, na pasta /var/www/fluxhub/backend execute:
   .venv/bin/python -m scripts.migrate_claude_config
2. Mantenha TOKEN_ENCRYPTION_KEY já configurada. Nunca troque a chave: ela
   protege também os tokens existentes do Mercado Livre.
3. Cada usuário pode entrar em Agentes Claude no seu painel, inserir sua
   chave Anthropic, escolher o modelo, testar e salvar a conexão. O backend
   valida a chave com a Anthropic e criptografa antes de armazenar.
4. Na área direita, o usuário faz perguntas ao assistente. Os agentes
   existentes (vendas, preços, estoque e estratégia) determinam o contexto.

## Segurança e compatibilidade

- A API nunca devolve a chave; ela fica criptografada com Fernet no PostgreSQL.
- As chaves são separadas por user_id, sem exposição de uma conta a outra.
- A API exige login FluxHub, e as rotas Next.js verificam a origem.
- Os agentes recebem somente estatísticas agregadas do usuário.
- O histórico de chat fica no navegador, sem salvar mensagens no banco.
- O intervalo de 30 segundos entre análises por usuário permanece como
  proteção contra abuso e custos inesperados. Não existe campo de limite mensal.
- Se nenhuma chave pessoal estiver cadastrada, ANTHROPIC_API_KEY do backend
  continua funcionando como padrão opcional para manter compatibilidade.
- A integração Claude NÃO executa o Claude Code CLI nem scripts arbitrários.
