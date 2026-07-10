"""
Monitor de Bonos - Republica del Paraguay

App Streamlit para mantener un universo editable de bonos soberanos PYG en
USD (bullet, cupon fijo, semestral, 30/360) e ingresar precio o yield para
obtener la valuacion completa (precio limpio/sucio, interes corrido, YTM,
duration, convexidad) y los cashflows futuros.

Uso:
    streamlit run bonos_pyg_app.py
"""

import os
from datetime import date

import pandas as pd
import streamlit as st

from bond_model import Bond

REGISTRY_PATH = os.path.join(os.path.dirname(__file__), "bonos_universo.csv")

st.set_page_config(page_title="Bonos Paraguay", layout="wide")

# ── Identidad visual propia: paleta bandera Paraguay (azul / blanco / rojo) ──
PY_BLUE = "#0038A8"
PY_RED = "#D52B1E"
PY_WHITE = "#F5F6F7"

st.markdown(
    f"""
    <style>
    .stApp {{ background-color: #0E1116; }}
    html, body, [class*="css"] {{
        font-family: -apple-system, "Segoe UI", Helvetica, Arial, sans-serif;
    }}
    h1, h2, h3 {{ color: {PY_WHITE} !important; font-weight: 600; letter-spacing: 0.2px; }}
    .py-flagbar {{
        display: flex; height: 5px; width: 100%; margin: 4px 0 20px 0; border-radius: 2px;
        overflow: hidden;
    }}
    .py-flagbar span {{ flex: 1; }}
    [data-testid="stMetricValue"] {{
        color: {PY_BLUE}; font-family: "Roboto Mono", Consolas, monospace; font-weight: 600;
    }}
    [data-testid="stMetricLabel"] {{ color: #8A8F98; }}
    .stTabs [data-baseweb="tab-list"] {{ gap: 4px; border-bottom: 1px solid #262B33; }}
    .stTabs [data-baseweb="tab"] {{
        background-color: #171B21; color: #B8BCC4; border-radius: 4px 4px 0 0;
        padding: 8px 20px;
    }}
    .stTabs [aria-selected="true"] {{
        background-color: #16233F; color: {PY_WHITE} !important;
        border-bottom: 2px solid {PY_BLUE};
    }}
    div[data-testid="stDataFrame"] {{ border: 1px solid #262B33; border-radius: 4px; }}
    .yas-label {{ color: #8A8F98; font-size: 0.8rem; letter-spacing: 0.4px; text-transform: uppercase; }}
    .yas-value {{
        color: {PY_BLUE}; font-size: 1.5rem; font-weight: 700;
        font-family: "Roboto Mono", Consolas, monospace; margin-bottom: 0.6rem;
    }}
    </style>
    """,
    unsafe_allow_html=True,
)

st.title("Bonos Paraguay")
st.markdown(
    f'<div class="py-flagbar">'
    f'<span style="background:{PY_BLUE}"></span>'
    f'<span style="background:{PY_WHITE}"></span>'
    f'<span style="background:{PY_RED}"></span>'
    f"</div>",
    unsafe_allow_html=True,
)
st.caption("Bonos bullet, cupón fijo semestral, convención de días 30/360")


# ── Universo de bonos (editable, persistido en CSV) ─────────────────────────
def load_registry() -> pd.DataFrame:
    df = pd.read_csv(REGISTRY_PATH)
    df["maturity"] = pd.to_datetime(df["maturity"]).dt.date
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


registry = load_registry()

if registry.empty:
    st.warning("El universo de bonos esta vacio. Anda a la tab 'Monitor de bonos' para cargar uno.")
    st.stop()

tab_cashflow, tab_yas, tab_monitor = st.tabs(["Cashflows", "YAS", "Monitor de bonos"])


# ── Tab 1: Cashflows ─────────────────────────────────────────────────────────
with tab_cashflow:
    col_sel, col_settle = st.columns([2, 1])
    with col_sel:
        nombre_cf = st.selectbox("Bono", registry["nombre"].tolist(), key="cf_bono")
    with col_settle:
        settlement_cf = st.date_input("Settlement", value=date.today(), key="cf_settlement")

    row_cf = registry[registry["nombre"] == nombre_cf].iloc[0]
    bond_cf = make_bond(row_cf)

    prev_coupon, next_coupon, _, period_days, accrued_days, _ = bond_cf.schedule(settlement_cf)
    accrued = bond_cf.accrued_interest(settlement_cf)

    c1, c2, c3 = st.columns(3)
    c1.metric("Cupón anterior", prev_coupon.strftime("%Y-%m-%d"))
    c2.metric("Próximo cupón", next_coupon.strftime("%Y-%m-%d"))
    c3.metric("Interés corrido", f"{accrued:.4f}")

    st.subheader("Cashflows futuros")
    cf = bond_cf.cashflows(settlement_cf)
    st.dataframe(cf, use_container_width=True, hide_index=True)
    st.download_button(
        "Descargar cashflows (CSV)",
        cf.to_csv(index=False).encode("utf-8"),
        file_name=f"cashflows_{nombre_cf.replace(' ', '_')}.csv",
        mime="text/csv",
    )


# ── Tab 2: Valoracion estilo YAS (Bloomberg) ────────────────────────────────
with tab_yas:
    col_inputs, col_grid = st.columns([1, 2])

    with col_inputs:
        nombre_sel = st.selectbox("Bono", registry["nombre"].tolist(), key="yas_bono")
        row_sel = registry[registry["nombre"] == nombre_sel].iloc[0]
        st.caption(f"ISIN: {row_sel.get('isin', '-')}  |  Cupón: {row_sel['coupon_pct']}%  |  Vto: {row_sel['maturity']}")

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
        "Agrega, edita o elimina bonos. Verificá cupón/vencimiento/ISIN contra el "
        "prospecto o el ticker del emisor antes de operar con estos numeros."
    )

    edited = st.data_editor(
        registry,
        num_rows="dynamic",
        use_container_width=True,
        column_config={
            "nombre": st.column_config.TextColumn("Nombre", required=True),
            "isin": st.column_config.TextColumn("ISIN"),
            "coupon_pct": st.column_config.NumberColumn("Cupón %", format="%.3f", required=True),
            "maturity": st.column_config.DateColumn("Vencimiento", required=True),
            "face": st.column_config.NumberColumn("Face", default=100.0, required=True),
            "freq": st.column_config.NumberColumn("Pagos/año", default=2, required=True),
        },
        key="registry_editor",
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

    if "mesa_precios" not in st.session_state:
        st.session_state.mesa_precios = {n: 100.0 for n in edited["nombre"]}
    for n in edited["nombre"]:
        st.session_state.mesa_precios.setdefault(n, 100.0)

    mesa_settlement = st.date_input("Settlement (comparación)", value=date.today(), key="mesa_settlement")

    mesa_rows = []
    for _, row in edited.iterrows():
        n = row["nombre"]
        default_price = st.session_state.mesa_precios[n]
        mesa_rows.append({
            "nombre": n,
            "isin": row.get("isin", ""),
            "coupon_pct": row["coupon_pct"],
            "maturity": row["maturity"],
            "precio_limpio": default_price,
        })
    mesa_df = pd.DataFrame(mesa_rows)

    mesa_edited = st.data_editor(
        mesa_df,
        use_container_width=True,
        hide_index=True,
        disabled=["nombre", "isin", "coupon_pct", "maturity"],
        column_config={
            "precio_limpio": st.column_config.NumberColumn("Precio limpio", format="%.4f"),
        },
        key="mesa_editor",
    )

    resultados = []
    for _, row in mesa_edited.iterrows():
        bono_row = edited[edited["nombre"] == row["nombre"]].iloc[0]
        b = make_bond(bono_row)
        s = b.summary(mesa_settlement, clean_price=float(row["precio_limpio"]))
        resultados.append({
            "nombre": row["nombre"],
            "precio_limpio": s["precio_limpio"],
            "ytm_pct": s["ytm_pct"],
            "duracion_modificada": s["duracion_modificada"],
            "convexidad": s["convexidad"],
        })
        st.session_state.mesa_precios[row["nombre"]] = float(row["precio_limpio"])

    st.dataframe(pd.DataFrame(resultados), use_container_width=True, hide_index=True)
