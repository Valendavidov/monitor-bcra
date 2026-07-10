"""
Monitor de Bonos - Paraguay y Uruguay

App Streamlit con un universo editable de bonos soberanos (bullet, cupon
fijo, pago semestral, convencion de dias 30/360) para Paraguay y Uruguay
(Globales y Unidad Indexada). Permite ingresar precio o yield para obtener
la valuacion completa (precio limpio/sucio, interes corrido, YTM, duration,
convexidad) y los cashflows futuros.

Uso:
    streamlit run bonos_pyg_app.py
"""

import os
from datetime import date

import pandas as pd
import streamlit as st

from bond_model import Bond

BASE_DIR = os.path.dirname(__file__)

PAISES = {
    "Paraguay": {
        "registry": os.path.join(BASE_DIR, "bonos_universo_py.csv"),
        "primary": "#0038A8",
        "accent": "#D52B1E",
        "flag": ["#0038A8", "#F5F6F7", "#D52B1E"],
    },
    "Uruguay": {
        "registry": os.path.join(BASE_DIR, "bonos_universo_uy.csv"),
        "primary": "#75AADB",
        "accent": "#FCD116",
        "flag": ["#75AADB", "#F5F6F7", "#75AADB"],
    },
}

st.set_page_config(page_title="Monitor de Bonos Soberanos", layout="wide")

pais = st.radio("País", list(PAISES.keys()), horizontal=True, key="pais_selector")
cfg = PAISES[pais]
PRIMARY = cfg["primary"]
ACCENT = cfg["accent"]

# ── Identidad visual: mayusculas en toda la interfaz, paleta por pais ────────
st.markdown(
    f"""
    <style>
    .stApp {{ background-color: #0E1116; }}
    html, body, [class*="css"] {{
        font-family: -apple-system, "Segoe UI", Helvetica, Arial, sans-serif;
    }}
    h1, h2, h3, h4, label, .stTabs button p, .stButton button p,
    .stDownloadButton button p, [data-testid="stCaptionContainer"],
    [data-testid="stMetricLabel"], .yas-label, .stRadio label p {{
        text-transform: uppercase !important;
    }}
    h1, h2, h3 {{ color: #F5F6F7 !important; font-weight: 600; letter-spacing: 0.2px; }}
    .flagbar {{
        display: flex; height: 5px; width: 100%; margin: 4px 0 20px 0; border-radius: 2px;
        overflow: hidden;
    }}
    .flagbar span {{ flex: 1; }}
    [data-testid="stMetricValue"] {{
        color: {PRIMARY}; font-family: "Roboto Mono", Consolas, monospace; font-weight: 600;
    }}
    [data-testid="stMetricLabel"] {{ color: #8A8F98; }}
    .stTabs [data-baseweb="tab-list"] {{ gap: 4px; border-bottom: 1px solid #262B33; }}
    .stTabs [data-baseweb="tab"] {{
        background-color: #171B21; color: #B8BCC4; border-radius: 4px 4px 0 0;
        padding: 8px 20px;
    }}
    .stTabs [aria-selected="true"] {{
        background-color: #1A2430; color: #F5F6F7 !important;
        border-bottom: 2px solid {PRIMARY};
    }}
    div[data-testid="stDataFrame"] {{ border: 1px solid #262B33; border-radius: 4px; }}
    .yas-label {{ color: #8A8F98; font-size: 0.8rem; letter-spacing: 0.4px; }}
    .yas-value {{
        color: {PRIMARY}; font-size: 1.5rem; font-weight: 700;
        font-family: "Roboto Mono", Consolas, monospace; margin-bottom: 0.6rem;
    }}
    </style>
    """,
    unsafe_allow_html=True,
)

st.title(f"Bonos {pais}")
st.markdown(
    '<div class="flagbar">' + "".join(f'<span style="background:{c}"></span>' for c in cfg["flag"]) + "</div>",
    unsafe_allow_html=True,
)
st.caption("Bonos bullet, cupón fijo semestral, convención de días 30/360")

REGISTRY_PATH = cfg["registry"]


# ── Universo de bonos (editable, persistido en CSV) ─────────────────────────
def load_registry() -> pd.DataFrame:
    df = pd.read_csv(REGISTRY_PATH)
    df["maturity"] = pd.to_datetime(df["maturity"]).dt.date
    df["isin"] = df["isin"].fillna("")
    return df


def save_registry(df: pd.DataFrame) -> None:
    out = df.copy()
    out["maturity"] = pd.to_datetime(out["maturity"]).dt.strftime("%Y-%m-%d")
    out.to_csv(REGISTRY_PATH, index=False)


def make_bond(row: pd.Series) -> Bond:
    maturity = row["maturity"]
    if not isinstance(maturity, date):
        maturity = pd.to_datetime(maturity).date()
    return Bond(
        coupon_pct=float(row["coupon_pct"]),
        maturity=maturity,
        face=float(row["face"]),
        freq=int(row["freq"]),
    )


def filtrar_por_categoria(df: pd.DataFrame, key: str) -> pd.DataFrame:
    """Si hay mas de una categoria (ej. Uruguay: Global / UI), permite filtrar."""
    if "categoria" in df.columns and df["categoria"].nunique() > 1:
        categorias = ["Todas"] + sorted(df["categoria"].unique().tolist())
        elegida = st.radio("Categoría", categorias, horizontal=True, key=key)
        if elegida != "Todas":
            return df[df["categoria"] == elegida]
    return df


registry = load_registry()

if registry.empty:
    st.warning("El universo de bonos esta vacio. Anda a la tab 'Monitor de bonos' para cargar uno.")
    st.stop()

tab_cashflow, tab_yas, tab_monitor = st.tabs(["Cashflows", "YAS", "Monitor de bonos"])


# ── Tab 1: Cashflows ─────────────────────────────────────────────────────────
with tab_cashflow:
    registry_cf = filtrar_por_categoria(registry, key="cat_cf")

    col_sel, col_settle = st.columns([2, 1])
    with col_sel:
        nombre_cf = st.selectbox("Bono", registry_cf["nombre"].tolist(), key="cf_bono")
    with col_settle:
        settlement_cf = st.date_input("Settlement", value=date.today(), key="cf_settlement")

    row_cf = registry_cf[registry_cf["nombre"] == nombre_cf].iloc[0]
    bond_cf = make_bond(row_cf)

    prev_coupon, next_coupon, _, period_days, accrued_days, _ = bond_cf.schedule(settlement_cf)
    accrued = bond_cf.accrued_interest(settlement_cf)

    c1, c2, c3 = st.columns(3)
    c1.metric("Cupón anterior", prev_coupon.strftime("%Y-%m-%d"))
    c2.metric("Próximo cupón", next_coupon.strftime("%Y-%m-%d"))
    c3.metric("Interés corrido", f"{accrued:.4f}")

    st.subheader("Cashflows futuros")
    cf = bond_cf.cashflows(settlement_cf)
    st.dataframe(cf.rename(columns=str.upper), use_container_width=True, hide_index=True)
    st.download_button(
        "Descargar cashflows (CSV)",
        cf.to_csv(index=False).encode("utf-8"),
        file_name=f"cashflows_{nombre_cf.replace(' ', '_')}.csv",
        mime="text/csv",
    )


# ── Tab 2: Valoracion estilo YAS (Bloomberg) ────────────────────────────────
with tab_yas:
    registry_yas = filtrar_por_categoria(registry, key="cat_yas")

    col_inputs, col_grid = st.columns([1, 2])

    with col_inputs:
        nombre_sel = st.selectbox("Bono", registry_yas["nombre"].tolist(), key="yas_bono")
        row_sel = registry_yas[registry_yas["nombre"] == nombre_sel].iloc[0]
        isin_txt = row_sel.get("isin") or "-"
        st.caption(f"ISIN: {isin_txt}  |  Cupón: {row_sel['coupon_pct']}%  |  Vto: {row_sel['maturity']}")

        settlement = st.date_input("Settlement", value=date.today(), key="yas_settlement")
        modo = st.radio("Ingresar por", ["Precio limpio", "Yield (YTM %)"], key="yas_modo")

        if modo == "Precio limpio":
            clean_price_in = st.number_input("Precio limpio", value=100.0, step=0.25, format="%.4f", key="yas_price")
            bond = make_bond(row_sel)
            summary = bond.summary(settlement, clean_price=clean_price_in)
        else:
            ytm_in = st.number_input("Yield (YTM %)", value=6.5, step=0.1, format="%.4f", key="yas_ytm")
            bond = make_bond(row_sel)
            summary = bond.summary(settlement, ytm_pct=ytm_in)

    with col_grid:
        st.markdown("#### Resultado")
        g1, g2 = st.columns(2)
        with g1:
            st.markdown('<div class="yas-label">YIELD (YTM %)</div>', unsafe_allow_html=True)
            st.markdown(f'<div class="yas-value">{summary["ytm_pct"]:.4f}</div>', unsafe_allow_html=True)
            st.markdown('<div class="yas-label">PRECIO LIMPIO</div>', unsafe_allow_html=True)
            st.markdown(f'<div class="yas-value">{summary["precio_limpio"]:.4f}</div>', unsafe_allow_html=True)
            st.markdown('<div class="yas-label">PRECIO SUCIO</div>', unsafe_allow_html=True)
            st.markdown(f'<div class="yas-value">{summary["precio_sucio"]:.4f}</div>', unsafe_allow_html=True)
            st.markdown('<div class="yas-label">INTERÉS CORRIDO</div>', unsafe_allow_html=True)
            st.markdown(f'<div class="yas-value">{summary["interes_corrido"]:.4f}</div>', unsafe_allow_html=True)
        with g2:
            st.markdown('<div class="yas-label">DURACIÓN MACAULAY (AÑOS)</div>', unsafe_allow_html=True)
            st.markdown(f'<div class="yas-value">{summary["duracion_macaulay_anios"]:.4f}</div>', unsafe_allow_html=True)
            st.markdown('<div class="yas-label">DURACIÓN MODIFICADA</div>', unsafe_allow_html=True)
            st.markdown(f'<div class="yas-value">{summary["duracion_modificada"]:.4f}</div>', unsafe_allow_html=True)
            st.markdown('<div class="yas-label">CONVEXIDAD</div>', unsafe_allow_html=True)
            st.markdown(f'<div class="yas-value">{summary["convexidad"]:.4f}</div>', unsafe_allow_html=True)
            st.markdown('<div class="yas-label">SETTLEMENT</div>', unsafe_allow_html=True)
            st.markdown(f'<div class="yas-value">{summary["settlement"]}</div>', unsafe_allow_html=True)


# ── Tab 3: Monitor de bonos (universo + comparacion) ────────────────────────
with tab_monitor:
    st.subheader("Universo de bonos")
    st.caption(
        "Agrega, edita o elimina bonos. Verificá cupón/vencimiento/ISIN/categoría contra el "
        "prospecto o el ticker del emisor antes de operar con estos numeros."
    )

    edited = st.data_editor(
        registry,
        num_rows="dynamic",
        use_container_width=True,
        column_config={
            "nombre": st.column_config.TextColumn("Nombre", required=True),
            "isin": st.column_config.TextColumn("ISIN"),
            "categoria": st.column_config.TextColumn("Categoría"),
            "coupon_pct": st.column_config.NumberColumn("Cupón %", format="%.3f", required=True),
            "maturity": st.column_config.DateColumn("Vencimiento", required=True),
            "face": st.column_config.NumberColumn("Face", default=100.0, required=True),
            "freq": st.column_config.NumberColumn("Pagos/año", default=2, required=True),
        },
        key=f"registry_editor_{pais}",
    )

    if st.button("Guardar universo"):
        save_registry(edited)
        st.success("Universo guardado.")
        st.rerun()

    if edited.empty:
        st.warning("Agrega al menos un bono para ver la comparación.")
        st.stop()

    st.divider()
    st.subheader("Comparación rápida")
    st.caption("Cargá un precio limpio por bono y mirá yield/duration de todo el universo junto.")

    session_key = f"mesa_precios_{pais}"
    if session_key not in st.session_state:
        st.session_state[session_key] = {n: 100.0 for n in edited["nombre"]}
    for n in edited["nombre"]:
        st.session_state[session_key].setdefault(n, 100.0)

    mesa_settlement = st.date_input("Settlement (comparación)", value=date.today(), key="mesa_settlement")

    mesa_rows = []
    for _, row in edited.iterrows():
        n = row["nombre"]
        default_price = st.session_state[session_key][n]
        mesa_rows.append({
            "nombre": n,
            "isin": row.get("isin", ""),
            "categoria": row.get("categoria", ""),
            "coupon_pct": row["coupon_pct"],
            "maturity": row["maturity"],
            "precio_limpio": default_price,
        })
    mesa_df = pd.DataFrame(mesa_rows)

    mesa_edited = st.data_editor(
        mesa_df,
        use_container_width=True,
        hide_index=True,
        disabled=["nombre", "isin", "categoria", "coupon_pct", "maturity"],
        column_config={
            "precio_limpio": st.column_config.NumberColumn("Precio limpio", format="%.4f"),
        },
        key=f"mesa_editor_{pais}",
    )

    resultados = []
    for _, row in mesa_edited.iterrows():
        bono_row = edited[edited["nombre"] == row["nombre"]].iloc[0]
        b = make_bond(bono_row)
        s = b.summary(mesa_settlement, clean_price=float(row["precio_limpio"]))
        resultados.append({
            "nombre": row["nombre"],
            "categoria": row.get("categoria", ""),
            "precio_limpio": s["precio_limpio"],
            "ytm_pct": s["ytm_pct"],
            "duracion_modificada": s["duracion_modificada"],
            "convexidad": s["convexidad"],
        })
        st.session_state[session_key][row["nombre"]] = float(row["precio_limpio"])

    st.dataframe(pd.DataFrame(resultados).rename(columns=str.upper), use_container_width=True, hide_index=True)
