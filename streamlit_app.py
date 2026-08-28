import base64
import hashlib
import hmac
import secrets
import time
import unicodedata
from datetime import date, datetime, timedelta, timezone
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


def api_get(path: str, params, client_id: str, client_secret: str) -> dict:
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


def fetch_all(
    path: str,
    client_id: str,
    client_secret: str,
    extra_params: dict | None = None,
) -> list[dict]:
    records: list[dict] = []
    for page in range(1, 501):
        params = {"pagina": page, "limite": 100}
        params.update(extra_params or {})
        payload = api_get(
            path,
            params,
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


def chunks(values: list, size: int):
    for index in range(0, len(values), size):
        yield values[index : index + size]


def fetch_stock_balances(
    product_ids: list[int], client_id: str, client_secret: str
) -> list[dict]:
    """The Bling endpoint requires product IDs and is not paginated."""
    balances: list[dict] = []
    for group in chunks(product_ids, 100):
        params = [("idsProdutos[]", product_id) for product_id in group]
        payload = api_get("estoques/saldos", params, client_id, client_secret)
        data = payload.get("data", [])
        balances.extend(data if isinstance(data, list) else [data])
    return balances


def fetch_categories_by_product_filter(
    client_id: str, client_secret: str
) -> tuple[dict[int, str], list[dict], list[str]]:
    """Replicate the category filter from Bling's product registration screen."""
    warnings: list[str] = []
    categories = fetch_all("categorias/produtos", client_id, client_secret)
    category_by_id = {
        int(item["id"]): item for item in categories if item.get("id") is not None
    }

    def category_depth(category_id: int, visited: set[int] | None = None) -> int:
        visited = visited or set()
        if category_id in visited:
            return 0
        parent_id = nested_value(category_by_id.get(category_id, {}), "categoriaPai.id")
        if parent_id is None:
            return 0
        return 1 + category_depth(int(parent_id), visited | {category_id})

    product_categories: dict[int, str] = {}
    audit: list[dict] = []
    ordered = sorted(
        categories, key=lambda item: category_depth(int(item.get("id", 0)))
    )
    progress = st.progress(0, text="Consultando produtos por categoria...")
    total = max(len(ordered), 1)
    for index, category in enumerate(ordered, start=1):
        category_id = int(category["id"])
        category_name = str(
            category.get("descricao") or f"Categoria {category_id}"
        )
        try:
            matched = fetch_all(
                "produtos",
                client_id,
                client_secret,
                {"criterio": 5, "idCategoria": category_id},
            )
            matched_ids = [int(item["id"]) for item in matched if item.get("id")]
            for product_id in matched_ids:
                # Parent categories are processed first; the most specific wins.
                product_categories[product_id] = category_name
            status = "OK"
        except requests.RequestException as exc:
            matched_ids = []
            status = f"Erro: {exc}"
        audit.append(
            {
                "ID da categoria": category_id,
                "Categoria": category_name,
                "Produtos retornados pelo filtro": len(matched_ids),
                "Status": status,
            }
        )
        progress.progress(index / total, text="Consultando produtos por categoria...")
    progress.empty()
    if not categories:
        warnings.append("A API do Bling não retornou categorias cadastradas.")
    elif not product_categories:
        warnings.append(
            "As categorias foram listadas, mas todos os filtros retornaram zero produtos."
        )
    return product_categories, audit, warnings


def fetch_product_metadata(
    products: list[dict], client_id: str, client_secret: str
) -> tuple[dict[int, str], dict[int, float], list[str], list[dict]]:
    (
        product_categories,
        category_audit,
        warnings,
    ) = fetch_categories_by_product_filter(client_id, client_secret)
    category_names = {
        int(item["ID da categoria"]): str(item["Categoria"])
        for item in category_audit
    }

    product_details: dict[int, dict] = {}
    product_rows = {int(item["id"]): item for item in products if item.get("id")}
    failed = 0
    progress = st.progress(0, text="Carregando categorias dos produtos...")
    total = max(len(products), 1)
    for index, product in enumerate(products, start=1):
        product_id = int(product.get("id", 0) or 0)
        try:
            detail = api_get(
                f"produtos/{product_id}", {}, client_id, client_secret
            ).get("data", {})
        except requests.RequestException:
            failed += 1
            progress.progress(index / total, text="Carregando categorias e custos...")
            continue
        product_details[product_id] = detail
        progress.progress(index / total, text="Carregando categorias e custos...")

    parent_ids = {
        int(detail.get("idProdutoPai") or product_rows[product_id].get("idProdutoPai"))
        for product_id, detail in product_details.items()
        if detail.get("idProdutoPai") or product_rows[product_id].get("idProdutoPai")
    }
    for parent_id in parent_ids - product_details.keys():
        try:
            product_details[parent_id] = api_get(
                f"produtos/{parent_id}", {}, client_id, client_secret
            ).get("data", {})
        except requests.RequestException:
            pass

    product_costs: dict[int, float] = {}
    inherited_categories = 0
    category_ids_not_listed: set[int] = set()
    category_detail_cache: dict[int, str] = {}
    for product_id, product in product_rows.items():
        detail = product_details.get(product_id, {})
        category_id = nested_value(detail, "categoria.id")
        category_description = nested_value(
            detail, "categoria.descricao", "categoria.nome"
        )

        # Variations commonly inherit the category from the parent product.
        if category_id is None:
            parent_id = detail.get("idProdutoPai") or product.get("idProdutoPai")
            parent_detail = product_details.get(int(parent_id or 0), {})
            category_id = nested_value(parent_detail, "categoria.id")
            category_description = nested_value(
                parent_detail, "categoria.descricao", "categoria.nome"
            )
            if category_id is not None:
                inherited_categories += 1

        if category_id is not None:
            category_id = int(category_id)
            category = category_names.get(category_id) or category_description
            if not category and category_id not in category_detail_cache:
                try:
                    category_data = api_get(
                        f"categorias/produtos/{category_id}",
                        {},
                        client_id,
                        client_secret,
                    ).get("data", {})
                    category_detail_cache[category_id] = str(
                        category_data.get("descricao") or ""
                    )
                except requests.RequestException:
                    category_detail_cache[category_id] = ""
            category = category or category_detail_cache.get(category_id)
            if not category:
                category = f"Categoria {category_id}"
                category_ids_not_listed.add(category_id)
        else:
            category = "Sem categoria"
        # The category filter is the primary source. Product detail is fallback.
        product_categories.setdefault(product_id, str(category))

        product_costs[product_id] = number(
            nested_value(
                detail,
                "precoCusto",
                "fornecedor.precoCusto",
                "fornecedor.precoCompra",
                default=product.get("precoCusto"),
            )
        )
    progress.empty()
    if failed:
        warnings.append(
            f"{failed} produto(s) não puderam ter os detalhes consultados; "
            "eles permaneceram sem categoria/custo detalhado."
        )
    if inherited_categories:
        warnings.append(
            f"{inherited_categories} variação(ões) herdaram a categoria do produto-pai."
        )
    if category_ids_not_listed:
        warnings.append(
            "Alguns IDs de categoria vieram no cadastro dos produtos, mas não "
            "vieram na listagem de categorias; o painel exibirá o próprio ID."
        )
    if not any(row["Produtos retornados pelo filtro"] for row in category_audit):
        warnings.append(
            "O teste de todos os filtros de categoria retornou zero produtos. "
            "Consulte a auditoria de categorias para confirmar a resposta da API."
        )
    return product_categories, product_costs, warnings, category_audit


def fetch_sales_consumption(
    start_date: date,
    end_date: date,
    client_id: str,
    client_secret: str,
) -> tuple[dict[int, float], int, int, list[str]]:
    orders = fetch_all(
        "pedidos/vendas",
        client_id,
        client_secret,
        {"dataInicial": start_date.isoformat(), "dataFinal": end_date.isoformat()},
    )
    warnings: list[str] = []
    situation_names: dict[int, str] = {}
    for situation_id in {
        nested_value(order, "situacao.id") for order in orders
    } - {None}:
        try:
            data = api_get(
                f"situacoes/{int(situation_id)}", {}, client_id, client_secret
            ).get("data", {})
            situation_names[int(situation_id)] = str(data.get("nome") or "")
        except requests.RequestException:
            situation_names[int(situation_id)] = ""

    valid_orders = []
    for order in orders:
        situation_id = nested_value(order, "situacao.id")
        situation_name = situation_names.get(int(situation_id or 0), "").lower()
        if "cancel" not in situation_name:
            valid_orders.append(order)

    consumption: dict[int, float] = {}
    item_count = 0
    failed_orders = 0
    progress = st.progress(0, text="Calculando consumo pelos pedidos de venda...")
    total = max(len(valid_orders), 1)
    for index, order in enumerate(valid_orders, start=1):
        try:
            detail = api_get(
                f"pedidos/vendas/{int(order['id'])}", {}, client_id, client_secret
            ).get("data", {})
        except requests.RequestException:
            failed_orders += 1
            progress.progress(index / total, text="Calculando consumo pelos pedidos de venda...")
            continue
        for item in detail.get("itens", []):
            product_id = nested_value(item, "produto.id")
            if product_id is not None:
                product_id = int(product_id)
                consumption[product_id] = consumption.get(product_id, 0.0) + number(
                    item.get("quantidade")
                )
                item_count += 1
        progress.progress(index / total, text="Calculando consumo pelos pedidos de venda...")
    progress.empty()
    if orders and not any(situation_names.values()):
        warnings.append(
            "Não foi possível identificar os nomes das situações; os pedidos "
            "cancelados podem estar incluídos no consumo."
        )
    if failed_orders:
        warnings.append(
            f"{failed_orders} pedido(s) não puderam ser detalhados e foram "
            "ignorados no cálculo de consumo."
        )
    return consumption, len(valid_orders), item_count, warnings


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


def normalize_text(value) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    return " ".join(
        "".join(char for char in text if not unicodedata.combining(char))
        .lower()
        .split()
    )


FAST_MOVING_CATEGORIES = {
    "cafes",
    "energetico",
    "energeticos",
    "refrigerante",
    "refrigerantes",
    "soda",
    "sodas",
    "suco",
    "sucos",
    "agua",
    "balas e confeitos",
    "chocolate",
    "chocolates",
    "salgadinho",
    "salgadinhos",
    "doce",
    "doces",
    "salgado",
    "salgados",
}


def is_fast_moving_category(category: str) -> bool:
    normalized = normalize_text(category)
    parts = {part.strip() for part in normalized.replace(">", "/").split("/")}
    return bool(parts & FAST_MOVING_CATEGORIES) or normalized in FAST_MOVING_CATEGORIES


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


def normalize_inventory(
    products: list[dict],
    balances: list[dict],
    product_categories: dict[int, str],
    product_costs: dict[int, float],
    consumption: dict[int, float],
    analysis_days: int,
) -> pd.DataFrame:
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
                "Categoria": product_categories.get(product_id, "Sem categoria"),
                "Formato": str(product.get("formato") or ""),
                "Situação": "Ativo" if product.get("situacao", "A") == "A" else "Inativo",
                "Unidade": str(product.get("unidade") or "UN"),
                "Preço": number(product.get("preco")),
                "Custo cadastrado": number(
                    product_costs.get(product_id, product.get("precoCusto"))
                ),
                "Saldo físico": physical,
                "Saldo virtual": virtual,
                "Consumo no período": number(consumption.get(product_id)),
            }
        )

    df = pd.DataFrame(rows)
    if df.empty:
        return pd.DataFrame(
            columns=[
                "ID", "Código", "Produto", "Categoria", "Formato", "Situação",
                "Unidade", "Preço", "Custo cadastrado", "Saldo físico",
                "Saldo virtual", "Consumo no período",
            ]
        )
    weeks = max(analysis_days / 7, 1 / 7)
    months = max(analysis_days / 30.4375, 1 / 30.4375)
    df["Saldo atual"] = df["Saldo virtual"]
    df["Consumo médio semanal"] = df["Consumo no período"] / weeks
    df["Consumo médio mensal"] = df["Consumo no período"] / months
    df["Cobertura (semanas)"] = df.apply(
        lambda row: (
            row["Saldo atual"] / row["Consumo médio semanal"]
            if row["Consumo médio semanal"] > 0
            else float("inf")
        ),
        axis=1,
    )
    df["Cobertura (meses)"] = df["Cobertura (semanas)"] / (30.4375 / 7)
    df["Valor em estoque"] = (
        df["Custo cadastrado"] * df["Saldo atual"].clip(lower=0)
    )
    df["Valor de consumo"] = df["Consumo no período"] * df["Custo cadastrado"]
    return df


def apply_consumption(
    inventory: pd.DataFrame,
    consumption: dict[int, float],
    analysis_days: int,
) -> pd.DataFrame:
    df = inventory.copy()
    weeks = max(analysis_days / 7, 1 / 7)
    months = max(analysis_days / 30.4375, 1 / 30.4375)
    df["Consumo no período"] = df["ID"].map(consumption).fillna(0.0)
    df["Consumo médio semanal"] = df["Consumo no período"] / weeks
    df["Consumo médio mensal"] = df["Consumo no período"] / months
    df["Cobertura (semanas)"] = df.apply(
        lambda row: (
            row["Saldo atual"] / row["Consumo médio semanal"]
            if row["Consumo médio semanal"] > 0
            else float("inf")
        ),
        axis=1,
    )
    df["Cobertura (meses)"] = df["Cobertura (semanas)"] / (30.4375 / 7)
    df["Valor de consumo"] = df["Consumo no período"] * df["Custo cadastrado"]
    return df


def classify_replenishment(df: pd.DataFrame) -> pd.DataFrame:
    result = df.copy()
    result["Política de reposição"] = result["Categoria"].apply(
        lambda value: "Consumo" if is_fast_moving_category(value) else "Quantidade"
    )
    result["Criticidade"] = "Adequado"
    result["Motivo do alerta"] = "Estoque dentro do parâmetro"
    result["Sugestão de compra"] = 0.0

    fast = result["Política de reposição"] == "Consumo"
    has_consumption = result["Consumo médio mensal"] > 0
    critical_fast = (
        fast
        & has_consumption
        & (result["Saldo atual"] <= result["Consumo médio semanal"])
    )
    attention_fast = (
        fast
        & has_consumption
        & ~critical_fast
        & (result["Saldo atual"] < result["Consumo médio mensal"])
    )
    result.loc[attention_fast, "Criticidade"] = "Atenção"
    result.loc[attention_fast, "Motivo do alerta"] = "Abaixo do consumo médio mensal"
    result.loc[critical_fast, "Criticidade"] = "Crítico"
    result.loc[critical_fast, "Motivo do alerta"] = "Até um consumo médio semanal"
    result.loc[fast & has_consumption, "Sugestão de compra"] = (
        result.loc[fast & has_consumption, "Consumo médio mensal"]
        - result.loc[fast & has_consumption, "Saldo atual"]
    ).clip(lower=0)

    # If there is no sales history, keep a safe quantity fallback.
    quantity_policy = ~fast | ~has_consumption
    critical_quantity = quantity_policy & (result["Saldo atual"] < 5)
    attention_quantity = (
        quantity_policy
        & ~critical_quantity
        & (result["Saldo atual"] <= 10)
    )
    result.loc[attention_quantity, "Criticidade"] = "Atenção"
    result.loc[attention_quantity, "Motivo do alerta"] = "Saldo entre 5 e 10 unidades"
    result.loc[critical_quantity, "Criticidade"] = "Crítico"
    result.loc[critical_quantity, "Motivo do alerta"] = "Saldo menor que 5 unidades"
    result.loc[quantity_policy, "Sugestão de compra"] = (
        10 - result.loc[quantity_policy, "Saldo atual"]
    ).clip(lower=0)
    return result


def load_inventory(
    client_id: str,
    client_secret: str,
    start_date: date,
    end_date: date,
) -> tuple[pd.DataFrame, list[str], int, dict, list[dict]]:
    products = fetch_all(
        "produtos", client_id, client_secret, {"criterio": 5}
    )
    warnings: list[str] = []
    product_ids = [int(product["id"]) for product in products if product.get("id")]
    try:
        balances = fetch_stock_balances(product_ids, client_id, client_secret)
    except requests.HTTPError as exc:
        balances = []
        if exc.response is not None and exc.response.status_code == 403:
            warnings.append(
                "O aplicativo não possui o escopo de leitura de estoques. "
                "Adicione esse escopo no Bling e autorize novamente."
            )
        else:
            detail = exc.response.text if exc.response is not None else str(exc)
            warnings.append(f"Não foi possível consultar os saldos: {detail}")

    active_products = [
        product for product in products if product.get("situacao", "A") == "A"
    ]
    (
        product_categories,
        product_costs,
        metadata_warnings,
        category_audit,
    ) = fetch_product_metadata(active_products, client_id, client_secret)
    warnings.extend(metadata_warnings)

    try:
        consumption, order_count, item_count, sales_warnings = fetch_sales_consumption(
            start_date, end_date, client_id, client_secret
        )
        warnings.extend(sales_warnings)
    except requests.HTTPError as exc:
        consumption, order_count, item_count = {}, 0, 0
        if exc.response is not None and exc.response.status_code == 403:
            warnings.append(
                "O aplicativo não possui leitura de pedidos de venda. Adicione "
                "esse escopo e autorize novamente para calcular consumo, giro e ABC."
            )
        else:
            detail = exc.response.text if exc.response is not None else str(exc)
            warnings.append(f"Não foi possível calcular o consumo: {detail}")

    analysis_days = max((end_date - start_date).days + 1, 1)
    return (
        normalize_inventory(
            products,
            balances,
            product_categories,
            product_costs,
            consumption,
            analysis_days,
        ),
        warnings,
        order_count,
        {
            "Produtos": len(products),
            "Produtos ativos detalhados": len(active_products),
            "Cadastros correlacionados por ID": len(product_categories),
            "Categorias identificadas": sum(
                value != "Sem categoria" for value in product_categories.values()
            ),
            "Produtos sem categoria no cadastro": sum(
                value == "Sem categoria" for value in product_categories.values()
            ),
            "Custos maiores que zero": sum(value > 0 for value in product_costs.values()),
            "Pedidos considerados": order_count,
            "Itens de pedidos considerados": item_count,
            "Produtos com consumo": sum(value > 0 for value in consumption.values()),
        },
        category_audit,
    )


def load_basic_inventory(
    client_id: str,
    client_secret: str,
    analysis_days: int,
) -> pd.DataFrame:
    """Fast first paint: the product list already includes virtual stock and cost."""
    products = fetch_all(
        "produtos",
        client_id,
        client_secret,
        {"criterio": 5},
    )
    return normalize_inventory(products, [], {}, {}, {}, analysis_days)


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
        st.caption("Estoque, consumo e reposição")
        analysis_days = st.selectbox(
            "Período para consumo", [30, 60, 90, 180], index=0,
            format_func=lambda value: f"Últimos {value} dias",
        )
        st.caption(
            "Alto giro: alerta abaixo do consumo mensal e crítico até o consumo semanal."
        )
        if st.button("Atualizar saldo", type="primary", use_container_width=True):
            st.session_state.pop("inventory", None)
            st.session_state.pop("category_audit", None)
            st.session_state.categories_loaded = False
            st.session_state.analytics_loaded = False
        run_categories = st.button(
            "Sincronizar somente categorias",
            use_container_width=True,
            help="Consulta as categorias e seus produtos sem carregar pedidos ou custos.",
        )
        run_analytics = st.button(
            "Sincronizar consumo",
            use_container_width=True,
            help=(
                "Consulta os pedidos do período para calcular médias e criticidade."
            ),
        )
        if st.session_state.get("analytics_loaded"):
            st.success("Consumo sincronizado")
        elif st.session_state.get("categories_loaded"):
            st.success("Categorias sincronizadas")
        else:
            st.caption("Saldo rápido ativo · categorias ainda não sincronizadas")
        st.divider()
        if st.button("Desconectar", use_container_width=True):
            for key in [
                "bling_tokens",
                "inventory",
                "inventory_warnings",
                "category_audit",
                "categories_loaded",
                "analytics_loaded",
            ]:
                st.session_state.pop(key, None)
            st.rerun()

    end_date = date.today()
    start_date = end_date - timedelta(days=analysis_days - 1)
    if (
        st.session_state.get("analytics_loaded")
        and st.session_state.get("inventory_period") != analysis_days
        and "inventory" in st.session_state
    ):
        st.session_state.inventory = apply_consumption(
            st.session_state.inventory, {}, analysis_days
        )
        st.session_state.analytics_loaded = False
        st.session_state.order_count = 0
    if "inventory" not in st.session_state:
        try:
            with st.spinner("Carregando produtos e saldo atual..."):
                inventory = load_basic_inventory(
                    client_id, client_secret, analysis_days
                )
                st.session_state.inventory = inventory
                st.session_state.inventory_warnings = []
                st.session_state.order_count = 0
                st.session_state.sync_diagnostics = {
                    "Produtos": len(inventory),
                    "Categorias identificadas": 0,
                    "Custos maiores que zero": int(
                        (inventory["Custo cadastrado"] > 0).sum()
                    ),
                    "Pedidos considerados": 0,
                    "Itens de pedidos considerados": 0,
                    "Produtos com consumo": 0,
                }
                st.session_state.category_audit = []
                st.session_state.categories_loaded = False
                st.session_state.inventory_period = analysis_days
                st.session_state.analytics_loaded = False
                st.session_state.updated_at = datetime.now(
                    timezone(timedelta(hours=-3))
                )
        except requests.RequestException as exc:
            detail = exc.response.text if exc.response is not None else str(exc)
            st.error(f"Falha ao consultar a API do Bling: {detail}")
            return

    if run_categories:
        try:
            with st.spinner("Sincronizando somente categorias..."):
                category_map, category_audit, category_warnings = (
                    fetch_categories_by_product_filter(client_id, client_secret)
                )
                inventory = st.session_state.inventory.copy()
                mapped = inventory["ID"].map(category_map)
                inventory["Categoria"] = mapped.fillna("Sem categoria")
                st.session_state.inventory = inventory
                st.session_state.category_audit = category_audit
                st.session_state.inventory_warnings = category_warnings
                st.session_state.categories_loaded = True
                st.session_state.updated_at = datetime.now(
                    timezone(timedelta(hours=-3))
                )
                diagnostics = dict(st.session_state.get("sync_diagnostics", {}))
                diagnostics["Categorias cadastradas"] = len(category_audit)
                diagnostics["Produtos vinculados por filtro"] = len(category_map)
                diagnostics["Categorias com produtos"] = sum(
                    int(item["Produtos retornados pelo filtro"] > 0)
                    for item in category_audit
                )
                st.session_state.sync_diagnostics = diagnostics
            st.rerun()
        except requests.RequestException as exc:
            detail = exc.response.text if exc.response is not None else str(exc)
            st.error(f"Falha ao sincronizar categorias: {detail}")

    if run_analytics:
        try:
            with st.spinner("Consultando pedidos e calculando o consumo..."):
                consumption, order_count, item_count, sales_warnings = (
                    fetch_sales_consumption(
                        start_date, end_date, client_id, client_secret
                    )
                )
                inventory = apply_consumption(
                    st.session_state.inventory,
                    consumption,
                    analysis_days,
                )
                st.session_state.inventory = inventory
                existing_warnings = st.session_state.get("inventory_warnings", [])
                st.session_state.inventory_warnings = (
                    existing_warnings + sales_warnings
                )
                st.session_state.order_count = order_count
                st.session_state.inventory_period = analysis_days
                st.session_state.analytics_loaded = True
                diagnostics = dict(st.session_state.get("sync_diagnostics", {}))
                diagnostics["Pedidos considerados"] = order_count
                diagnostics["Itens de pedidos considerados"] = item_count
                diagnostics["Produtos com consumo"] = sum(
                    value > 0 for value in consumption.values()
                )
                st.session_state.sync_diagnostics = diagnostics
                st.session_state.updated_at = datetime.now(
                    timezone(timedelta(hours=-3))
                )
            st.rerun()
        except requests.RequestException as exc:
            detail = exc.response.text if exc.response is not None else str(exc)
            st.error(f"Falha ao sincronizar o consumo: {detail}")

    df = st.session_state.inventory.copy()
    warnings = st.session_state.get("inventory_warnings", [])
    updated_at = st.session_state.get("updated_at")
    order_count = st.session_state.get("order_count", 0)

    st.title("📦 Dashboard de Estoque")
    timestamp = updated_at.strftime("%d/%m/%Y às %H:%M") if updated_at else "agora"
    if st.session_state.get("analytics_loaded"):
        subtitle = (
            f"Posição em {timestamp} · Consumo de {start_date:%d/%m/%Y} "
            f"a {end_date:%d/%m/%Y} · {order_count} pedidos"
        )
    elif st.session_state.get("categories_loaded"):
        subtitle = (
            f"Saldo e categorias atualizados em {timestamp} · Consumo ainda não sincronizado"
        )
    else:
        subtitle = (
            f"Saldo atualizado em {timestamp} · Clique em “Sincronizar somente "
            "categorias” para carregar as classificações dos produtos"
        )
    st.markdown(
        f'<p class="ul-subtitle">{subtitle}</p>',
        unsafe_allow_html=True,
    )
    for warning in warnings:
        st.warning(warning)
    with st.expander("Diagnóstico da sincronização", expanded=False):
        diagnostics = st.session_state.get("sync_diagnostics", {})
        if diagnostics:
            diagnostic_df = pd.DataFrame(
                diagnostics.items(), columns=["Dado", "Quantidade"]
            )
            st.dataframe(diagnostic_df, hide_index=True, use_container_width=True)
        st.caption(
            "Se categorias, custos ou itens aparecerem zerados após a sincronização, "
            "a origem não foi retornada pela API ou falta permissão para o recurso."
        )
    category_audit = st.session_state.get("category_audit", [])
    if category_audit:
        with st.expander("Auditoria dos filtros de categoria", expanded=False):
            audit_df = pd.DataFrame(category_audit)
            st.dataframe(
                audit_df.sort_values(
                    ["Produtos retornados pelo filtro", "Categoria"],
                    ascending=[False, True],
                ),
                hide_index=True,
                use_container_width=True,
            )
            st.download_button(
                "Baixar auditoria de categorias (CSV)",
                audit_df.to_csv(index=False, sep=";").encode("utf-8-sig"),
                file_name="auditoria_categorias_bling.csv",
                mime="text/csv",
            )
    if df.empty:
        st.info("Nenhum produto foi retornado pela API.")
        return

    df = classify_replenishment(df)

    abc_base = df["Valor de consumo"].clip(lower=0)
    if abc_base.sum() <= 0:
        abc_base = df["Consumo no período"].clip(lower=0)
    total_consumption_value = abc_base.sum()
    if total_consumption_value > 0:
        cumulative = abc_base.sort_values(ascending=False).cumsum() / total_consumption_value
        previous = cumulative.shift(fill_value=0)
        abc = pd.Series("C", index=df.index)
        abc.loc[previous[previous < 0.80].index] = "A"
        abc.loc[previous[(previous >= 0.80) & (previous < 0.95)].index] = "B"
        df["Curva ABC"] = abc
    else:
        df["Curva ABC"] = "Sem classificação"

    inactive_ids = set(st.session_state.get("dashboard_inactive_products", []))
    with st.expander(
        f"Gerenciar produtos ocultos ({len(inactive_ids)})",
        expanded=False,
    ):
        st.caption(
            "Marque produtos que não devem participar dos indicadores, gráficos "
            "e alertas. Esta ação não altera o cadastro no Bling."
        )
        tag_table = df[["ID", "Código", "Produto", "Categoria"]].copy()
        tag_table.insert(
            0,
            "Inativo no dashboard",
            tag_table["ID"].isin(inactive_ids),
        )
        edited_tags = st.data_editor(
            tag_table,
            use_container_width=True,
            hide_index=True,
            disabled=["ID", "Código", "Produto", "Categoria"],
            column_config={
                "Inativo no dashboard": st.column_config.CheckboxColumn(
                    "Inativo no dashboard",
                    help="Oculta apenas neste dashboard; não altera o Bling.",
                ),
                "ID": None,
            },
            key="product_visibility_editor",
        )
        new_inactive_ids = set(
            edited_tags.loc[
                edited_tags["Inativo no dashboard"], "ID"
            ].astype(int)
        )
        if new_inactive_ids != inactive_ids:
            st.session_state.dashboard_inactive_products = sorted(new_inactive_ids)
            inactive_ids = new_inactive_ids
            st.rerun()

    if inactive_ids:
        df = df[~df["ID"].isin(inactive_ids)].copy()
    st.caption(
        f"{len(inactive_ids)} produto(s) marcado(s) como inativo(s) somente no dashboard."
    )

    with st.expander("Filtros", expanded=True):
        f1, f2, f3, f4, f5 = st.columns([2.2, 1.1, 1.4, 1.3, 1.0])
        search = f1.text_input("Buscar produto ou código", placeholder="Digite para pesquisar")
        situations = f2.multiselect(
            "Situação", sorted(df["Situação"].unique()), default=["Ativo"]
        )
        replenishment_status = f3.multiselect(
            "Criticidade",
            ["Crítico", "Atenção", "Adequado"],
        )
        categories = f4.multiselect(
            "Categoria", sorted(df["Categoria"].astype(str).unique())
        )
        abc_filter = f5.multiselect("Curva ABC", ["A", "B", "C"])

    filtered = df.copy()
    if search:
        mask = (
            filtered["Produto"].str.contains(search, case=False, na=False)
            | filtered["Código"].str.contains(search, case=False, na=False)
        )
        filtered = filtered[mask]
    if situations:
        filtered = filtered[filtered["Situação"].isin(situations)]
    if replenishment_status:
        filtered = filtered[
            filtered["Criticidade"].isin(replenishment_status)
        ]
    if categories:
        filtered = filtered[filtered["Categoria"].isin(categories)]
    if abc_filter:
        filtered = filtered[filtered["Curva ABC"].isin(abc_filter)]

    active = filtered[filtered["Situação"] == "Ativo"]
    k1, k2, k3, k4, k5 = st.columns(5)
    k1.metric("SKUs ativos", br_number(active["ID"].nunique()))
    k2.metric("Saldo atual", br_number(active["Saldo atual"].sum(), 1))
    k3.metric(
        "Produtos críticos",
        br_number((active["Criticidade"] == "Crítico").sum()),
    )
    k4.metric(
        "Produtos em atenção",
        br_number((active["Criticidade"] == "Atenção").sum()),
    )
    k5.metric("Sugestão de compra", br_number(active["Sugestão de compra"].sum(), 1))

    st.subheader("Fila de reposição")
    critical_tab, attention_tab = st.tabs(["🔴 Críticos", "🟠 Atenção"])
    alert_columns = [
        "Código",
        "Produto",
        "Categoria",
        "Saldo atual",
        "Consumo médio semanal",
        "Consumo médio mensal",
        "Sugestão de compra",
        "Motivo do alerta",
    ]
    with critical_tab:
        critical_products = active[active["Criticidade"] == "Crítico"]
        if critical_products.empty:
            st.success("Nenhum produto em nível crítico.")
        else:
            st.dataframe(
                critical_products[alert_columns].sort_values(
                    ["Sugestão de compra", "Saldo atual"], ascending=[False, True]
                ),
                hide_index=True,
                use_container_width=True,
            )
    with attention_tab:
        attention_products = active[active["Criticidade"] == "Atenção"]
        if attention_products.empty:
            st.success("Nenhum produto em nível de atenção.")
        else:
            st.dataframe(
                attention_products[alert_columns].sort_values(
                    ["Sugestão de compra", "Saldo atual"], ascending=[False, True]
                ),
                hide_index=True,
                use_container_width=True,
            )

    st.subheader("Visão geral")
    chart1, chart2, chart3 = st.columns([1, 1, 1.6])
    status_summary = (
        active.groupby("Criticidade", as_index=False)["ID"]
        .count()
        .rename(columns={"ID": "Produtos"})
    )
    fig_status = px.pie(
        status_summary,
        names="Criticidade",
        values="Produtos",
        hole=0.62,
        color="Criticidade",
        color_discrete_map={
            "Adequado": "#22c55e",
            "Atenção": "#f59e0b",
            "Crítico": "#ef4444",
        },
    )
    fig_status.update_layout(margin=dict(l=10, r=10, t=25, b=10), legend_title="")
    chart1.plotly_chart(fig_status, use_container_width=True)

    abc_summary = (
        active[active["Curva ABC"].isin(["A", "B", "C"])]
        .groupby("Curva ABC", as_index=False)["Valor de consumo"]
        .sum()
    )
    fig_abc = px.pie(
        abc_summary,
        names="Curva ABC",
        values="Valor de consumo",
        hole=0.62,
        category_orders={"Curva ABC": ["A", "B", "C"]},
        color="Curva ABC",
        color_discrete_map={"A": "#6d28d9", "B": "#f97316", "C": "#94a3b8"},
    )
    fig_abc.update_layout(margin=dict(l=10, r=10, t=25, b=10), legend_title="Curva")
    chart2.plotly_chart(fig_abc, use_container_width=True)

    top = active.nlargest(12, "Consumo médio mensal").sort_values("Consumo médio mensal")
    fig_top = px.bar(
        top,
        x="Consumo médio mensal",
        y="Produto",
        orientation="h",
        color="Curva ABC",
        color_discrete_map={"A": "#6d28d9", "B": "#f97316", "C": "#94a3b8"},
    )
    fig_top.update_layout(
        margin=dict(l=10, r=10, t=25, b=10),
        yaxis_title="",
        legend_title="Curva",
    )
    chart3.plotly_chart(fig_top, use_container_width=True)

    st.subheader("Produtos")
    display_columns = [
        "Código", "Produto", "Categoria", "Curva ABC", "Saldo atual",
        "Consumo médio semanal", "Consumo médio mensal", "Cobertura (semanas)",
        "Cobertura (meses)", "Política de reposição", "Criticidade",
        "Motivo do alerta", "Sugestão de compra",
    ]
    display_df = filtered[display_columns].copy()
    display_df["Cobertura (semanas)"] = display_df["Cobertura (semanas)"].replace(
        [float("inf")], None
    )
    display_df["Cobertura (meses)"] = display_df["Cobertura (meses)"].replace(
        [float("inf")], None
    )
    st.dataframe(
        display_df.sort_values(
            ["Criticidade", "Cobertura (semanas)", "Produto"],
            ascending=[True, True, True],
        ),
        use_container_width=True,
        hide_index=True,
        column_config={
            "Saldo atual": st.column_config.NumberColumn(format="%.2f"),
            "Consumo médio semanal": st.column_config.NumberColumn(format="%.2f"),
            "Consumo médio mensal": st.column_config.NumberColumn(format="%.2f"),
            "Cobertura (semanas)": st.column_config.NumberColumn(format="%.1f"),
            "Cobertura (meses)": st.column_config.NumberColumn(format="%.1f"),
            "Sugestão de compra": st.column_config.NumberColumn(format="%.1f"),
        },
    )
    csv = display_df.to_csv(index=False, sep=";", decimal=",").encode("utf-8-sig")
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
