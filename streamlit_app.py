import base64
import hashlib
import hmac
import secrets
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import pandas as pd
import plotly.express as px
import requests
import streamlit as st


AUTHORIZE_URL = "https://www.bling.com.br/Api/v3/oauth/authorize"
TOKEN_URL = "https://api.bling.com.br/Api/v3/oauth/token"
API_URL = "https://api.bling.com.br/Api/v3"
REQUEST_INTERVAL = 0.36

st.set_page_config(
    page_title="Ultra Loot | Estoque",
    page_icon="📦",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(
    """
    <style>
        :root { --ul-purple: #6d28d9; --ul-orange: #f97316; }
        .block-container { padding-top: 1.7rem; padding-bottom: 2rem; }
        [data-testid="stMetric"] {
            background: linear-gradient(145deg, rgba(109,40,217,.12), rgba(249,115,22,.06));
            border: 1px solid rgba(109,40,217,.25); border-radius: 14px; padding: 14px 16px;
        }
        [data-testid="stMetricValue"] { color: #7c3aed; }
        div.stButton > button[kind="primary"], div.stLinkButton > a {
            background: linear-gradient(90deg, var(--ul-purple), #8b5cf6);
            border: 0; font-weight: 700;
        }
        .ul-subtitle { color: #6b7280; margin-top: -12px; margin-bottom: 18px; }
    </style>
    """,
    unsafe_allow_html=True,
)


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
        timestamp, nonce, signature = state.split(".", 2)
        payload = f"{timestamp}.{nonce}"
        expected = hmac.new(
            client_secret.encode(), payload.encode(), hashlib.sha256
        ).hexdigest()
        age = int(time.time()) - int(timestamp)
        return 0 <= age <= max_age and hmac.compare_digest(signature, expected)
    except (AttributeError, TypeError, ValueError):
        return False


def basic_auth(client_id: str, client_secret: str) -> str:
    raw = f"{client_id}:{client_secret}".encode()
    return base64.b64encode(raw).decode()


def token_request(data: dict, client_id: str, client_secret: str) -> dict:
    response = requests.post(
        TOKEN_URL,
        headers={
            "Authorization": f"Basic {basic_auth(client_id, client_secret)}",
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "1.0",
            "enable-jwt": "1",
        },
        data=data,
        timeout=25,
    )
    response.raise_for_status()
    tokens = response.json()
    tokens["expires_at"] = time.time() + int(tokens.get("expires_in", 21600)) - 60
    return tokens


def exchange_code(code: str, client_id: str, client_secret: str) -> dict:
    return token_request(
        {"grant_type": "authorization_code", "code": code},
        client_id,
        client_secret,
    )


def refresh_access_token(client_id: str, client_secret: str) -> None:
    current = st.session_state.bling_tokens
    st.session_state.bling_tokens = token_request(
        {
            "grant_type": "refresh_token",
            "refresh_token": current["refresh_token"],
        },
        client_id,
        client_secret,
    )


def api_get(path: str, params: dict, client_id: str, client_secret: str) -> dict:
    tokens = st.session_state.bling_tokens
    if time.time() >= tokens.get("expires_at", 0):
        refresh_access_token(client_id, client_secret)
        tokens = st.session_state.bling_tokens

    elapsed = time.time() - st.session_state.get("last_api_request", 0.0)
    if elapsed < REQUEST_INTERVAL:
        time.sleep(REQUEST_INTERVAL - elapsed)

    response = requests.get(
        f"{API_URL}/{path.lstrip('/')}",
        headers={
            "Authorization": f"Bearer {tokens['access_token']}",
            "Accept": "application/json",
            "enable-jwt": "1",
        },
        params=params,
        timeout=30,
    )
    st.session_state.last_api_request = time.time()

    if response.status_code == 401 and tokens.get("refresh_token"):
        refresh_access_token(client_id, client_secret)
        return api_get(path, params, client_id, client_secret)

    response.raise_for_status()
    return response.json()


def fetch_all(path: str, client_id: str, client_secret: str) -> list[dict]:
    records: list[dict] = []
    for page in range(1, 501):
        payload = api_get(
            path,
            {"pagina": page, "limite": 100},
            client_id,
            client_secret,
        )
        batch = payload.get("data", [])
        if isinstance(batch, dict):
            batch = [batch]
        if not batch:
            break
        records.extend(batch)
        if len(batch) < 100:
            break
    return records


def nested_value(item: dict, *paths, default=None):
    for path in paths:
        value = item
        for key in path.split("."):
            if not isinstance(value, dict) or key not in value:
                value = None
                break
            value = value[key]
        if value is not None:
            return value
    return default


def number(value) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def stock_index(balances: list[dict]) -> dict[int, dict]:
    result: dict[int, dict] = {}
    for item in balances:
        product_id = nested_value(item, "produto.id", "idProduto", "produtoId")
        if product_id is None:
            continue

        deposits = item.get("depositos") or []
        physical = nested_value(
            item, "saldoFisicoTotal", "saldoFisico", "saldo.fisico"
        )
        virtual = nested_value(
            item, "saldoVirtualTotal", "saldoVirtual", "saldo.virtual"
        )
        if physical is None and deposits:
            physical = sum(number(d.get("saldoFisico")) for d in deposits)
        if virtual is None and deposits:
            virtual = sum(number(d.get("saldoVirtual")) for d in deposits)

        result[int(product_id)] = {
            "saldo_fisico": number(physical),
            "saldo_virtual": number(virtual if virtual is not None else physical),
        }
    return result


def normalize_inventory(products: list[dict], balances: list[dict]) -> pd.DataFrame:
    stocks = stock_index(balances)
    rows = []
    for product in products:
        product_id = int(product.get("id", 0) or 0)
        stock = stocks.get(product_id, {})
        physical = stock.get(
            "saldo_fisico",
            number(
                nested_value(
                    product,
                    "estoque.saldoFisicoTotal",
                    "estoque.saldoFisico",
                    "saldoFisicoTotal",
                )
            ),
        )
        virtual = stock.get(
            "saldo_virtual",
            number(
                nested_value(
                    product,
                    "estoque.saldoVirtualTotal",
                    "estoque.saldoVirtual",
                    "saldoVirtualTotal",
                    default=physical,
                )
            ),
        )
        rows.append(
            {
                "ID": product_id,
                "Código": str(product.get("codigo") or "").strip(),
                "Produto": str(product.get("nome") or "Sem nome").strip(),
                "Categoria": str(
                    nested_value(
                        product,
                        "categoria.descricao",
                        "categoria.nome",
                        "categoria.id",
                        default="Sem categoria",
                    )
                ),
                "Formato": str(product.get("formato") or ""),
                "Situação": "Ativo" if product.get("situacao", "A") == "A" else "Inativo",
                "Unidade": str(product.get("unidade") or "UN"),
                "Preço": number(product.get("preco")),
                "Saldo físico": physical,
                "Saldo virtual": virtual,
            }
        )

    df = pd.DataFrame(rows)
    if df.empty:
        return pd.DataFrame(
            columns=[
                "ID", "Código", "Produto", "Categoria", "Formato", "Situação",
                "Unidade", "Preço", "Saldo físico", "Saldo virtual",
            ]
        )
    df["Valor em estoque"] = df["Preço"] * df["Saldo físico"].clip(lower=0)
    return df


def load_inventory(client_id: str, client_secret: str) -> tuple[pd.DataFrame, str]:
    products = fetch_all("produtos", client_id, client_secret)
    stock_warning = ""
    try:
        balances = fetch_all("estoques/saldos", client_id, client_secret)
    except requests.HTTPError as exc:
        balances = []
        if exc.response is not None and exc.response.status_code == 403:
            stock_warning = (
                "O aplicativo não possui o escopo de leitura de estoques. "
                "Adicione esse escopo no Bling e autorize novamente."
            )
        else:
            stock_warning = f"Não foi possível consultar os saldos: {exc}"
    return normalize_inventory(products, balances), stock_warning


def br_number(value: float, decimals: int = 0) -> str:
    text = f"{value:,.{decimals}f}"
    return text.replace(",", "X").replace(".", ",").replace("X", ".")


def br_currency(value: float) -> str:
    return f"R$ {br_number(value, 2)}"


def handle_oauth(client_id: str, client_secret: str) -> None:
    error = st.query_params.get("error")
    code = st.query_params.get("code")
    state = st.query_params.get("state")

    if error:
        st.error(f"O Bling não autorizou a conexão: {error}")
    if code and state and "bling_tokens" not in st.session_state:
        if not valid_state(state, client_secret):
            st.error("O retorno de autorização é inválido ou expirou.")
        else:
            try:
                st.session_state.bling_tokens = exchange_code(
                    code, client_id, client_secret
                )
                st.query_params.clear()
                st.rerun()
            except requests.RequestException as exc:
                detail = exc.response.text if exc.response is not None else str(exc)
                st.error(f"Não foi possível concluir a autorização: {detail}")


def login_page(client_id: str, client_secret: str) -> None:
    st.title("📦 Ultra Loot")
    st.markdown('<p class="ul-subtitle">Dashboard de estoque integrado ao Bling</p>', unsafe_allow_html=True)
    st.write("Conecte a conta para carregar os produtos e os saldos atuais.")
    state = create_state(client_secret)
    url = f"{AUTHORIZE_URL}?{urlencode({'response_type': 'code', 'client_id': client_id, 'state': state})}"
    st.link_button("Conectar ao Bling", url, type="primary")


def dashboard(client_id: str, client_secret: str) -> None:
    with st.sidebar:
        st.title("Ultra Loot")
        st.caption("Gestão de estoque")
        low_limit = st.number_input(
            "Limite de estoque baixo", min_value=1, value=5, step=1
        )
        if st.button("Atualizar dados", type="primary", use_container_width=True):
            st.session_state.pop("inventory", None)
            st.session_state.pop("stock_warning", None)
        st.divider()
        if st.button("Desconectar", use_container_width=True):
            for key in ["bling_tokens", "inventory", "stock_warning"]:
                st.session_state.pop(key, None)
            st.rerun()

    if "inventory" not in st.session_state:
        try:
            with st.spinner("Sincronizando produtos e saldos com o Bling..."):
                inventory, warning = load_inventory(client_id, client_secret)
                st.session_state.inventory = inventory
                st.session_state.stock_warning = warning
                st.session_state.updated_at = datetime.now(
                    timezone(timedelta(hours=-3))
                )
        except requests.RequestException as exc:
            detail = exc.response.text if exc.response is not None else str(exc)
            st.error(f"Falha ao consultar a API do Bling: {detail}")
            return

    df = st.session_state.inventory.copy()
    warning = st.session_state.get("stock_warning", "")
    updated_at = st.session_state.get("updated_at")

    st.title("📦 Dashboard de Estoque")
    timestamp = updated_at.strftime("%d/%m/%Y às %H:%M") if updated_at else "agora"
    st.markdown(
        f'<p class="ul-subtitle">Posição sincronizada em {timestamp}</p>',
        unsafe_allow_html=True,
    )
    if warning:
        st.warning(warning)
    if df.empty:
        st.info("Nenhum produto foi retornado pela API.")
        return

    df["Status do estoque"] = "Normal"
    df.loc[df["Saldo físico"] <= low_limit, "Status do estoque"] = "Estoque baixo"
    df.loc[df["Saldo físico"] <= 0, "Status do estoque"] = "Sem estoque"

    with st.expander("Filtros", expanded=True):
        f1, f2, f3, f4 = st.columns([2.2, 1.3, 1.3, 1.3])
        search = f1.text_input("Buscar produto ou código", placeholder="Digite para pesquisar")
        situations = f2.multiselect(
            "Situação", sorted(df["Situação"].unique()), default=["Ativo"]
        )
        stock_status = f3.multiselect(
            "Status do estoque", ["Normal", "Estoque baixo", "Sem estoque"]
        )
        categories = f4.multiselect(
            "Categoria", sorted(df["Categoria"].astype(str).unique())
        )

    filtered = df.copy()
    if search:
        mask = (
            filtered["Produto"].str.contains(search, case=False, na=False)
            | filtered["Código"].str.contains(search, case=False, na=False)
        )
        filtered = filtered[mask]
    if situations:
        filtered = filtered[filtered["Situação"].isin(situations)]
    if stock_status:
        filtered = filtered[filtered["Status do estoque"].isin(stock_status)]
    if categories:
        filtered = filtered[filtered["Categoria"].isin(categories)]

    active = filtered[filtered["Situação"] == "Ativo"]
    k1, k2, k3, k4, k5 = st.columns(5)
    k1.metric("SKUs ativos", br_number(active["ID"].nunique()))
    k2.metric("Unidades físicas", br_number(active["Saldo físico"].sum(), 1))
    k3.metric("Sem estoque", br_number((active["Saldo físico"] <= 0).sum()))
    k4.metric(
        "Estoque baixo",
        br_number(((active["Saldo físico"] > 0) & (active["Saldo físico"] <= low_limit)).sum()),
    )
    k5.metric("Valor potencial", br_currency(active["Valor em estoque"].sum()))

    st.subheader("Visão geral")
    chart1, chart2 = st.columns([1, 1.8])
    status_summary = (
        active.groupby("Status do estoque", as_index=False)["ID"]
        .count()
        .rename(columns={"ID": "Produtos"})
    )
    fig_status = px.pie(
        status_summary,
        names="Status do estoque",
        values="Produtos",
        hole=0.62,
        color="Status do estoque",
        color_discrete_map={
            "Normal": "#6d28d9",
            "Estoque baixo": "#f59e0b",
            "Sem estoque": "#ef4444",
        },
    )
    fig_status.update_layout(margin=dict(l=10, r=10, t=25, b=10), legend_title="")
    chart1.plotly_chart(fig_status, use_container_width=True)

    top = active.nlargest(12, "Saldo físico").sort_values("Saldo físico")
    fig_top = px.bar(
        top,
        x="Saldo físico",
        y="Produto",
        orientation="h",
        color="Saldo físico",
        color_continuous_scale=["#ddd6fe", "#6d28d9"],
    )
    fig_top.update_layout(
        margin=dict(l=10, r=10, t=25, b=10),
        coloraxis_showscale=False,
        yaxis_title="",
    )
    chart2.plotly_chart(fig_top, use_container_width=True)

    st.subheader("Produtos")
    display_columns = [
        "Código", "Produto", "Categoria", "Situação", "Saldo físico",
        "Saldo virtual", "Status do estoque", "Preço", "Valor em estoque",
    ]
    st.dataframe(
        filtered[display_columns].sort_values(
            ["Saldo físico", "Produto"], ascending=[True, True]
        ),
        use_container_width=True,
        hide_index=True,
        column_config={
            "Preço": st.column_config.NumberColumn(format="R$ %.2f"),
            "Valor em estoque": st.column_config.NumberColumn(format="R$ %.2f"),
            "Saldo físico": st.column_config.NumberColumn(format="%.2f"),
            "Saldo virtual": st.column_config.NumberColumn(format="%.2f"),
        },
    )
    csv = filtered[display_columns].to_csv(index=False, sep=";", decimal=",").encode("utf-8-sig")
    st.download_button(
        "Baixar estoque filtrado (CSV)",
        csv,
        file_name=f"estoque_ultra_loot_{datetime.now():%Y%m%d_%H%M}.csv",
        mime="text/csv",
    )


client_id = setting("BLING_CLIENT_ID")
client_secret = setting("BLING_CLIENT_SECRET")
handle_oauth(client_id, client_secret)

if "bling_tokens" not in st.session_state:
    login_page(client_id, client_secret)
else:
    dashboard(client_id, client_secret)
