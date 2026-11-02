# Importação de custos médios em Precificação

## Objetivo

Em **Precificação → Importar custos médios por CSV**, preencha exclusivamente os custos ainda ausentes, sem alterar preços publicados no Mercado Livre. O valor da coluna **Preço Médio** é tratado como **custo unitário de compra**, armazenado na tabela mercado_livre_product_costs, campo unit_cost. Um custo existente maior que zero é preservado, mesmo que o CSV traga valor diferente. Um registro sem custo ou com custo zero pode ser preenchido.

A importação não altera preços publicados, estoques, impostos, comissões, fretes, outras despesas, fabricantes nem tokens OAuth. Outras colunas do CSV são ignoradas.

## Formato

Aceita CSV UTF-8 ou Windows-1252, separador ponto e vírgula (ou vírgula quando não há ponto e vírgula no cabeçalho), até **8 MB** e **30.000 linhas**.

Obrigatório: **Preço Médio**, mais pelo menos uma coluna:
- **Referência** (preferencial, SKU do vendedor) ou **Cód.Item** (SKU alternativo);
- **MLB** ou **ID Mercado Livre**, coluna opcional de associação direta.

Exemplo:

~~~csv
Cód.Item;Descrição;Referência;Preço Médio;MLB
00428;Filtro de óleo;FILTRO-428;52,50;
;Outro produto;;73,90;MLB123456789
~~~

Saldo e outros campos da planilha são ignorados. Preços ausentes, inválidos, zero ou R$ 0,01 são sinalizados e ignorados. Identificadores preservam zeros iniciais, hífens e pontuação; nunca há correspondência por nome.

## Associação dos registros

- O usuário só pode importar para anúncios registrados em sua própria conta FluxHub.
- Um MLB explícito deve existir nos snapshots do vendedor autenticado.
- Sem coluna MLB, busca-se SKU exato (Referência ou Cód.Item) nos itens de pedidos sincronizados da mesma conta. Um valor MLB válido no campo Referência ou Cód.Item pode corresponder diretamente ao identificador do anúncio do próprio vendedor. Não há busca por semelhança ou associação entre vendedores.
- SKU apontando para vários anúncios: **ambíguo**, não atualizado automaticamente.
- Duas linhas apontando ao mesmo anúncio: **duplicado**, ambas ignoradas.
- Anúncios sem SKU nos itens de pedidos podem aparecer como **não encontrados**, mesmo existentes no catálogo. Inclua o MLB na planilha para associação direta.

## Fluxo seguro

1. Escolha o CSV e clique em **Conferir correspondências**.
2. A prévia exibe apenas os custos ausentes prontos para preenchimento, custos já cadastrados (sempre preservados), não encontrados, ambíguos, duplicados e valores suspeitos.
3. O servidor emite token assinado vinculado ao hash do arquivo, usuário, IDs, custos faltantes e validade de 15 minutos.
4. **Confirmar custos** reenvia o mesmo CSV. O servidor verifica a assinatura e refaz a análise sob bloqueio; quando custos ou associações mudam, exige nova prévia (HTTP 409).
5. Preenche apenas custos ausentes numa transação e preserva os demais campos e todos os custos positivos existentes. Se algum custo for preenchido entre a prévia e a confirmação, a operação exige uma nova prévia. Reimportações idênticas não alteram nada.

## Publicação

Backend e frontend precisam ser publicados juntos. O endpoint usa login FluxHub e o token privado de serviço existente, preservados pelo proxy Next.js.

Rotas autenticadas:
- POST /api/v1/pricing/cost-import/preview (multipart file)
- POST /api/v1/pricing/cost-import/apply (multipart file e preview_token)

**O CSV anexado ao chat não é enviado automaticamente ao banco de produção.** Depois do deploy do backend e frontend, selecione o CSV na tela de Precificação e confirme a prévia. Sem acesso autenticado ao banco de produção, não é possível verificar quantos custos faltam ou afirmar que foram gravados. Não publique o CSV de custos comerciais em repositórios.

## Exemplo de arquivo analisado (30/10/2026)

A planilha recebida tem 21.872 registros. Desses, 15.723 possuem preço médio acima de R$ 0,01 e 6.149 possuem zero ou R$ 0,01; esses últimos são ignorados automaticamente. O número realmente preenchido depende da correspondência exata do SKU/MLB e de quais custos já existiam no banco na hora da prévia. Não armazene a planilha no Git.

## Consulta da dashboard

A precificação busca os custos de compra em lotes paginados de até 10.000 registros, evitando o antigo corte silencioso de 1.000 custos na leitura financeira. A Visão Geral consulta os custos persistidos do usuário, exibe a cobertura de custos cadastrados e mantém os dados de vendas e anúncios separados. Os dados só aparecem após a gravação efetiva e a atualização da página.
