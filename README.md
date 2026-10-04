# Dashboard de Estoque Ultra Loot

Dashboard Streamlit integrado à API v3 do Bling por OAuth 2.0. Consulta
produtos, categorias, saldos e pedidos de venda. Apresenta consumo médio,
cobertura de estoque, curva ABC, alertas de reposição, filtros e exportação CSV.

Ao conectar, o dashboard atualiza automaticamente saldo, categorias e consumo,
nessa ordem. Os botões laterais permanecem disponíveis para atualizações manuais:

- **Atualizar categorias**: reproduz os filtros do cadastro de
  produtos e salva o resultado imediatamente, sem consultar pedidos ou custos.
- **Atualizar consumo**: consulta os pedidos de venda e, quando eles não
  contêm itens, tenta as NFC-e emitidas no mesmo período.

## Publicação

1. Publique este repositório no GitHub sem adicionar `secrets.toml`.
2. Em https://share.streamlit.io, crie um app usando `streamlit_app.py`.
3. Escolha e copie a URL fixa do app.
4. Cadastre essa URL exata como Link de redirecionamento no aplicativo Bling.
5. Nos Secrets do Streamlit Cloud, informe:

```toml
BLING_CLIENT_ID = "..."
BLING_CLIENT_SECRET = "..."
DATABASE_URL = "postgresql://...pooler.supabase.com:5432/postgres?sslmode=require"
```

6. No Bling, adicione os escopos de leitura de produtos, estoques e pedidos de
   venda e salve o app.
7. Abra o Streamlit e autorize a conta.

## Cache PostgreSQL e sincronização incremental

Com `DATABASE_URL` configurada, o aplicativo cria automaticamente as tabelas
`ul_product_metadata`, `ul_daily_consumption`, `ul_sync_state` e
`ul_hidden_products`. Nenhuma migração manual é necessária.

- A primeira carga importa até 180 dias, que é o maior período disponível no painel.
- Categorias e custos são reutilizados do banco nas próximas sessões.
- A atualização seguinte começa exatamente três dias antes da data da última
  sincronização e segue até o dia atual. Essa sobreposição captura alterações e
  cancelamentos recentes e também cobre todos os dias desde a última execução.
- O consumo é armazenado por dia e produto, permitindo trocar entre 30, 60, 90
  e 180 dias sem consultar novamente todos os pedidos.
- Uma carga parcial com falha de detalhamento não substitui dados válidos já
  armazenados.
- Sem banco disponível, o painel mantém o fluxo tradicional como contingência.
- Produtos ocultos ficam persistidos no banco. Se algum deles voltar a ter saldo
  positivo, o painel mostra um aviso e oferece uma ação para desocultá-lo.

## Segurança

O `client_secret`, o `access_token` e o `refresh_token` nunca devem ser
commitados no GitHub. Esta versão mantém os tokens somente na sessão. Uma etapa
posterior deve persistir os tokens criptografados para dispensar nova
autorização após o encerramento da sessão.

## Critérios dos indicadores

- Saldo atual: saldo virtual do Bling, já descontadas as reservas.
- Custo cadastrado: `precoCusto` do fornecedor padrão, obtido em
  `GET /produtos/fornecedores`; inclui o rateio de frete, descontos e impostos.
  Quando estiver vazio ou zerado, o dashboard utiliza `precoCompra` como
  alternativa. Se os dois estiverem ausentes, preserva o custo já retornado no
  cadastro do produto.
- Valor do estoque a preço de venda: saldo virtual positivo × preço de venda.
- Valor do estoque a custo/compra: saldo virtual positivo × custo cadastrado ou,
  na ausência dele, preço de compra.
- Consumo: quantidade dos itens de pedidos não cancelados; se nenhum item for
  encontrado, usa NFC-e autorizadas, emitidas ou registradas no período.
- Cobertura: saldo atual dividido pelo consumo médio semanal ou mensal.
- Curva ABC: participação acumulada no valor de consumo (`quantidade × custo`),
  com faixas A até 80%, B até 95% e C para o restante.
- Reposição: compara a cobertura atual com as semanas desejadas no painel.
- Inativo no dashboard: seleção múltipla persistida no servidor do dashboard,
  que remove produtos dos indicadores, gráficos e alertas sem alterar o Bling.
  O botão "Remover filtro de inativos" restaura todos os produtos.
- Categoria: correlacionada pelo ID interno entre a listagem de estoque e o
  cadastro individual do produto. Variações sem categoria própria herdam a
  categoria do produto-pai. O SKU e o nome não são usados como chave do vínculo.
- Teste de categorias: para cada categoria cadastrada, o dashboard executa a
  mesma lógica de filtro da tela de produtos (`GET /produtos?idCategoria=...`),
  relaciona os IDs retornados ao estoque e disponibiliza uma auditoria em CSV.

## Criticidade de reposição

Para cafés, energéticos, refrigerantes, sodas, sucos, água, balas e confeitos,
chocolates, salgadinhos, doces e salgados:

- crítico: saldo atual menor ou igual ao consumo médio semanal;
- atenção: saldo abaixo do consumo médio mensal;
- adequado: saldo igual ou acima do consumo médio mensal.

Para as demais categorias (e como fallback quando ainda não existe consumo):

- crítico: saldo menor que 5 unidades;
- atenção: saldo entre 5 e 10 unidades;
- adequado: saldo acima de 10 unidades.

Categorias e consumo são sincronizados em etapas independentes para que uma
consulta longa de pedidos não apague categorias já carregadas.
As categorias sincronizadas são salvas no servidor e reaplicadas automaticamente
quando o painel é aberto novamente.
