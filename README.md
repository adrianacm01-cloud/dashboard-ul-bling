# Dashboard de Estoque Ultra Loot

Dashboard Streamlit integrado à API v3 do Bling por OAuth 2.0. Consulta
produtos, categorias, saldos e pedidos de venda. Apresenta consumo médio,
cobertura de estoque, curva ABC, alertas de reposição, filtros e exportação CSV.

O primeiro carregamento usa somente a listagem paginada de produtos para abrir
rapidamente com saldo virtual. A sincronização foi separada em etapas:

- **Sincronizar somente categorias**: reproduz os filtros do cadastro de
  produtos e salva o resultado imediatamente, sem consultar pedidos ou custos.
- **Sincronizar consumo e custos**: etapa analítica mais demorada, pois o Bling
  exige uma consulta individual para cada produto e pedido.

## Publicação

1. Publique este repositório no GitHub sem adicionar `secrets.toml`.
2. Em https://share.streamlit.io, crie um app usando `streamlit_app.py`.
3. Escolha e copie a URL fixa do app.
4. Cadastre essa URL exata como Link de redirecionamento no aplicativo Bling.
5. Nos Secrets do Streamlit Cloud, informe:

```toml
BLING_CLIENT_ID = "..."
BLING_CLIENT_SECRET = "..."
```

6. No Bling, adicione os escopos de leitura de produtos, estoques e pedidos de
   venda e salve o app.
7. Abra o Streamlit e autorize a conta.

## Segurança

O `client_secret`, o `access_token` e o `refresh_token` nunca devem ser
commitados no GitHub. Esta versão mantém os tokens somente na sessão. Uma etapa
posterior deve persistir os tokens criptografados para dispensar nova
autorização após o encerramento da sessão.

## Critérios dos indicadores

- Saldo atual: saldo virtual do Bling, já descontadas as reservas.
- Custo cadastrado: `precoCusto` do fornecedor padrão; a API não fornece um
  campo separado de custo médio.
- Consumo: quantidade dos itens de pedidos não cancelados dentro do período.
- Cobertura: saldo atual dividido pelo consumo médio semanal ou mensal.
- Curva ABC: participação acumulada no valor de consumo (`quantidade × custo`),
  com faixas A até 80%, B até 95% e C para o restante.
- Reposição: compara a cobertura atual com as semanas desejadas no painel.
- Inativo no dashboard: marca local de visualização que remove o produto dos
  indicadores, gráficos e alertas sem alterar o cadastro no Bling. Enquanto não
  houver banco de dados, a marca permanece somente durante a sessão.
- Categoria: correlacionada pelo ID interno entre a listagem de estoque e o
  cadastro individual do produto. Variações sem categoria própria herdam a
  categoria do produto-pai. O SKU e o nome não são usados como chave do vínculo.
- Teste de categorias: para cada categoria cadastrada, o dashboard executa a
  mesma lógica de filtro da tela de produtos (`GET /produtos?idCategoria=...`),
  relaciona os IDs retornados ao estoque e disponibiliza uma auditoria em CSV.
