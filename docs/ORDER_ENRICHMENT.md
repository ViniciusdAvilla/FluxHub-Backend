# FluxHub — produtos dos pedidos, CRM de compradores e precificação

## Implantação na VPS Hostinger

**Migração aditiva** (não apaga pedidos, produtos, contas OAuth ou sync jobs).
A partir desta correção, o backend também verifica e cria automaticamente as
quatro tabelas ausentes na inicialização, antes do worker de sincronização.
Em instâncias antigas, pode executar manualmente sem reiniciar para corrigir
uma resposta 500 causada pela ausência das tabelas:

    cd /var/www/fluxhub/backend
    .venv/bin/python -m scripts.migrate_order_enrichment

Esta etapa cria:
- mercado_livre_order_lines: produtos, quantidades e comissão dos pedidos;
- mercado_livre_listing_photos: miniatura pública do anúncio por empresa/conta;
- mercado_livre_buyer_orders: apenas ID do comprador por pedido (sem CPF/e-mail/endereço).
- mercado_livre_buyer_access_audit: auditoria de consulta sem salvar dados pessoais.

A verificação é idempotente (segura se executada mais de uma vez) e, em
PostgreSQL, usa bloqueio transacional para evitar criação concorrente.
Se o usuário do banco não tiver permissão para criar tabelas, a inicialização
registra `order_enrichment_schema_bootstrap_failed` no journal; o serviço
permanece online, e o operador deve executar a migração com uma conta
autorizada. Não refaça a conexão Mercado Livre nem apague registros.

Se o banco de produção tiver outra URL, valide DATABASE_URL do serviço sem
publicar a senha.

Após realizar o deploy do backend e do frontend pelas rotinas já usadas na VPS:
1. Vá até Integrações e inicie a atualização de **pedidos**. O fluxo antigo de
   listagens já funciona por scan e deve ser preservado.
2. Os pedidos antigos serão enriquecidos progressivamente, sem exclusão.
3. Para obter miniaturas dos anúncios antigos é necessário atualizar as
   listagens ou executar uma rotina de backfill controlada.
4. Abra Vendas/Pedidos: essas telas **não** iniciam sincronização automática.
5. Clique no status (Pago/Cancelado/Pendente) para filtrar no PostgreSQL.
6. Clique no nome ou foto para abrir a Calculadora de Precificação.
7. Use "Dados do comprador" para uma consulta restrita e sob demanda.

## Privacidade

GET /api/v1/orders/{order_id}/buyer:
- exige autenticação FluxHub e vínculo comprovado de pedido e vendedor;
- consulta a API oficial Mercado Livre somente quando o usuário solicita;
- devolve apenas um conjunto controlado de campos pessoais e fiscais;
- não persiste nome, CPF, telefone ou endereço no PostgreSQL;
- não envia informações privadas para logs;
- usa headers privados/no-store.

A API externa pode omitir CPF, e-mail, telefone e destinatário por falta de
permissões/retorno. Sem disponibilidade, a UI mostra “não disponibilizado”.
Não usar estes dados para prospecção/marketing sem base legal e consentimentos
adequados. Configurar retenção, controle de acesso e auditoria conforme LGPD.

## Receita x lucro

O faturamento bruto pago soma pedidos com status paid; não equivale a
pagamentos liquidados. Lucro líquido é uma **simulação** até a empresa informar
custo unitário, tributos válidos e despesas de logística e conciliar taxas
efetivamente debitadas e devoluções.

A meta de 10% e os percentuais de ICMS, DIFAL e IRPJ/CSLL são ajustáveis.
A cotação de tarifa de anúncio vem de /sites/MLB/listing_prices, quando
disponível, e pode mudar com o preço. Ela não substitui a comissão efetiva
do pedido nem a conferência fiscal por profissional habilitado.

## Verificações sugeridas

- pytest no backend e typecheck/test/build no frontend via Actions;
- isolamento: usuário A não pode ler pedido/CPF/foto/conta do usuário B;
- pedido com diversos itens/variações; pedido cancelado; SKU ausente;
- pedido anterior à migração, sem foto; API 429/502 e buyer PII ausente;
- mobile: modal, filtros e tabela; foco/teclado e fechamento do diálogo.


## Corrigir erro `UndefinedColumn` na fila sem desconectar vendedores

Em instalações que já tinham `mercado_livre_sync_jobs`, atualizações antigas
podem deixar ausentes `pending_listing_ids` e
`pending_next_scroll_id`. A verificação automática no startup repara apenas
essas duas colunas antes de iniciar o worker. Não altera status, contadores,
cursores, anúncios, pedidos ou credenciais OAuth.

Correção manual imediata (idempotente):

    cd /var/www/fluxhub/backend
    .venv/bin/python -m scripts.migrate_sync_jobs
    sudo systemctl restart fluxhub-backend

Caso a conta do banco não possa executar o ALTER TABLE, a API mantém login e
conexões online, registra `sync_job_schema_bootstrap_failed` e pausa o worker
até a estrutura ser corrigida. Consulte `journalctl -u fluxhub-backend`.
