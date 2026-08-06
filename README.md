# Validação da API do Bling

Aplicativo Streamlit mínimo para executar o OAuth 2.0 do Bling e validar uma
consulta de leitura ao endpoint de produtos.

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

6. No Bling, adicione ao menos o escopo de leitura de produtos e salve o app.
7. Abra o Streamlit, autorize a conta e teste a consulta.

## Segurança

O `client_secret`, o `access_token` e o `refresh_token` nunca devem ser
commitados no GitHub. Esta versão mantém os tokens somente na sessão e serve
apenas para validar a integração.
