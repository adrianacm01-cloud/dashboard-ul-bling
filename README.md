# Dashboard de Estoque Ultra Loot

Dashboard Streamlit integrado à API v3 do Bling por OAuth 2.0. Consulta
produtos, categorias, saldos e pedidos de venda. Apresenta consumo médio,
cobertura de estoque, curva ABC, alertas de reposição, filtros e exportação CSV.

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
