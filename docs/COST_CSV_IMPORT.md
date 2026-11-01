# Importação de custos médios em Precificação

## Objetivo

Em **Precificação → Importar custos médios por CSV**, faça upload de um CSV sem alterar preços publicados no Mercado Livre. O valor da coluna **Preço Médio** é tratado exclusivamente como **custo unitário de compra**, armazenado na tabela mercado_livre_product_costs, campo unit_cost.

A importação não altera preços publicados, estoques, impostos, comissões, fretes, outras despesas, fabricantes nem tokens OAuth.

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
- Sem MLB, busca-se SKU exato (Referência ou Cód.Item) nos itens de pedidos sincronizados da mesma conta. Não há busca por semelhança ou associação entre vendedores.
- SKU apontando para vários anúncios: **ambíguo**, não atualizado automaticamente.
- Duas linhas apontando ao mesmo anúncio: **duplicado**, ambas ignoradas.
- Anúncios sem SKU nos itens de pedidos podem aparecer como **não encontrados**, mesmo existentes no catálogo. Inclua o MLB na planilha para associação direta.

## Fluxo seguro

1. Escolha o CSV e clique em **Conferir correspondências**.
2. A prévia exibe prontos, sem alteração, não encontrados, ambíguos, duplicados e valores suspeitos.
3. O servidor emite token assinado vinculado ao hash do arquivo, usuário, IDs, custos antigos e validade de 15 minutos.
4. **Confirmar custos** reenvia o mesmo CSV. O servidor verifica a assinatura e refaz a análise sob bloqueio; quando custos ou associações mudam, exige nova prévia (HTTP 409).
5. Atualiza apenas custos elegíveis numa transação e preserva os demais campos. Reimportações idênticas são idempotentes.

## Publicação

Backend e frontend precisam ser publicados juntos. O endpoint usa login FluxHub e o token privado de serviço existente, preservados pelo proxy Next.js.

Rotas autenticadas:
- POST /api/v1/pricing/cost-import/preview (multipart file)
- POST /api/v1/pricing/cost-import/apply (multipart file e preview_token)

**A planilha anexada ao chat não é importada automaticamente no banco de produção.** Depois do deploy, selecione o CSV na tela de Precificação e confirme a prévia. Não publique o CSV de custos comerciais em repositórios públicos.
