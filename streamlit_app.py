import base64
import hashlib
import hmac
import secrets
import time
from urllib.parse import urlencode

import requests
import streamlit as st


AUTHORIZE_URL = "https://www.bling.com.br/Api/v3/oauth/authorize"
TOKEN_URL = "https://api.bling.com.br/Api/v3/oauth/token"
API_URL = "https://api.bling.com.br/Api/v3"

st.set_page_config(page_title="Validação da API Bling", page_icon="🔌")


def setting(name: str) -> str:
    value = st.secrets.get(name, "")
    if not value:
        st.error(f"Configure o segredo `{name}` no Streamlit Cloud.")
        st.stop()
    return str(value)


def create_state(client_secret: str) -> str:
    payload = f"{int(time.time())}.{secrets.token_urlsafe(16)}"
    signature = hmac.new(
        client_secret.encode(), payload.encode(), hashlib.sha256
    ).hexdigest()
    return f"{payload}.{signature}"


def valid_state(state: str, client_secret: str, max_age: int = 600) -> bool:
    try:
        timestamp, nonce, received_signature = state.split(".", 2)
        payload = f"{timestamp}.{nonce}"
        expected_signature = hmac.new(
            client_secret.encode(), payload.encode(), hashlib.sha256
        ).hexdigest()
        age = int(time.time()) - int(timestamp)
        return 0 <= age <= max_age and hmac.compare_digest(
            received_signature, expected_signature
        )
    except (AttributeError, TypeError, ValueError):
        return False


def basic_auth(client_id: str, client_secret: str) -> str:
    raw = f"{client_id}:{client_secret}".encode()
    return base64.b64encode(raw).decode()


def exchange_code(code: str, client_id: str, client_secret: str) -> dict:
    response = requests.post(
        TOKEN_URL,
        headers={
            "Authorization": f"Basic {basic_auth(client_id, client_secret)}",
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "1.0",
            "enable-jwt": "1",
        },
        data={"grant_type": "authorization_code", "code": code},
        timeout=20,
    )
    response.raise_for_status()
    return response.json()


def get_products(access_token: str) -> dict:
    response = requests.get(
        f"{API_URL}/produtos",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
            "enable-jwt": "1",
        },
        params={"pagina": 1, "limite": 10},
        timeout=20,
    )
    response.raise_for_status()
    return response.json()


client_id = setting("BLING_CLIENT_ID")
client_secret = setting("BLING_CLIENT_SECRET")

st.title("Validação da API do Bling")
st.caption("Autorize a conta e faça uma consulta de leitura aos produtos.")

query_code = st.query_params.get("code")
query_state = st.query_params.get("state")
query_error = st.query_params.get("error")

if query_error:
    st.error(f"O Bling não autorizou a conexão: {query_error}")

if query_code and query_state and "bling_tokens" not in st.session_state:
    if not valid_state(query_state, client_secret):
        st.error("O retorno de autorização é inválido ou expirou. Tente novamente.")
    else:
        try:
            st.session_state.bling_tokens = exchange_code(
                query_code, client_id, client_secret
            )
            st.query_params.clear()
            st.success("Conta Bling autorizada com sucesso.")
        except requests.RequestException as exc:
            detail = getattr(exc.response, "text", "") if exc.response else ""
            st.error(f"Não foi possível obter os tokens. {detail or exc}")

if "bling_tokens" not in st.session_state:
    state = create_state(client_secret)
    authorization_link = f"{AUTHORIZE_URL}?{urlencode({'response_type': 'code', 'client_id': client_id, 'state': state})}"
    st.link_button("Autorizar conta no Bling", authorization_link, type="primary")
else:
    tokens = st.session_state.bling_tokens
    st.success("Token recebido. As credenciais não serão exibidas.")

    if st.button("Testar consulta de produtos", type="primary"):
        try:
            result = get_products(tokens["access_token"])
            products = result.get("data", [])
            st.success(f"API funcionando: {len(products)} produto(s) recebido(s).")
            st.dataframe(products, use_container_width=True)
        except requests.RequestException as exc:
            detail = getattr(exc.response, "text", "") if exc.response else ""
            st.error(f"Falha na consulta. {detail or exc}")

    if st.button("Encerrar teste"):
        del st.session_state.bling_tokens
        st.rerun()

st.info(
    "Este aplicativo é apenas para validação. Os tokens ficam somente na sessão "
    "e serão perdidos quando ela terminar."
)
