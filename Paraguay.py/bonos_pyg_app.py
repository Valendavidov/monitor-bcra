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
import plotly.graph_objects as go
import streamlit as st

from bond_model import Bond

REGISTRY_PATH = os.path.join(os.path.dirname(__file__), "bonos_universo.csv")

st.set_page_config(page_title="Monitor de Bonos - Paraguay", layout="wide")
st.title("Monitor de Bonos - Republica del Paraguay")
st.caption("Bonos bullet, cupon fijo semestral, convencion de dias 30/360")


# ── Universo de bonos (editable, persistido en CSV) ─────────────────────────
def load_registry() -> pd.DataFrame:
    df = pd.read_csv(REGISTRY_PATH)
    df["maturity"] = pd.to_datetime(df["maturity"]).dt.date
    return df


def save_registry(df: pd.DataFrame) -> None:
    out = df.copy()
    out["maturity"] = pd.to_datetime(out["maturity"]).dt.strftime("%Y-%m-%d")
    out.to_csv(REGISTRY_PATH, index=False)


st.header("Universo de bonos")
st.caption(
    "Agrega, edita o elimina bonos. Los datos (cupon, vencimiento, ISIN) hay "
    "que verificarlos contra el prospecto/emisor - no vienen precargados salvo "
    "el 2031 que ya estaba modelado."
)

registry = load_registry()
edited = st.data_editor(
    registry,
    num_rows="dynamic",
    use_container_width=True,
    column_config={
        "nombre": st.column_config.TextColumn("Nombre", required=True),
        "isin": st.column_config.TextColumn("ISIN"),
        "coupon_pct": st.column_config.NumberColumn("Cupon %", format="%.3f", required=True),
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

st.divider()

if edited.empty:
    st.warning("Agrega al menos un bono en la tabla de arriba para continuar.")
    st.stop()


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


# ── Valuacion individual ─────────────────────────────────────────────────────
st.header("Valuación")

col_sel, col_settle = st.columns([2, 1])
with col_sel:
    nombre_sel = st.selectbox("Bono", edited["nombre"].tolist())
with col_settle:
    settlement = st.date_input("Settlement", value=date.today())

row_sel = edited[edited["nombre"] == nombre_sel].iloc[0]
bond = make_bond(row_sel)

modo = st.radio("Ingresar por", ["Precio limpio", "Yield (YTM %)"], horizontal=True)
col_input, _ = st.columns([1, 3])
with col_input:
    if modo == "Precio limpio":
        clean_price = st.number_input("Precio limpio", value=100.0, step=0.25, format="%.4f")
        summary = bond.summary(settlement, clean_price=clean_price)
    else:
        ytm = st.number_input("Yield (YTM %)", value=6.5, step=0.1, format="%.4f")
        summary = bond.summary(settlement, ytm_pct=ytm)

m1, m2, m3, m4 = st.columns(4)
m1.metric("Precio limpio", f"{summary['precio_limpio']:.4f}")
m2.metric("Precio sucio", f"{summary['precio_sucio']:.4f}")
m3.metric("Interés corrido", f"{summary['interes_corrido']:.4f}")
m4.metric("YTM %", f"{summary['ytm_pct']:.4f}")

m5, m6, m7 = st.columns(3)
m5.metric("Duración Macaulay (años)", f"{summary['duracion_macaulay_anios']:.4f}")
m6.metric("Duración modificada", f"{summary['duracion_modificada']:.4f}")
m7.metric("Convexidad", f"{summary['convexidad']:.4f}")

st.subheader("Cashflows futuros")
cf = bond.cashflows(settlement)
st.dataframe(cf, use_container_width=True, hide_index=True)
st.download_button(
    "Descargar cashflows (CSV)",
    cf.to_csv(index=False).encode("utf-8"),
    file_name=f"cashflows_{nombre_sel.replace(' ', '_')}.csv",
    mime="text/csv",
)

st.subheader("Sensibilidad precio / yield")
base_ytm = summary["ytm_pct"]
yields = [base_ytm + delta for delta in range(-300, 301, 10)]  # +/- 3 puntos, paso 10bps
prices = [bond.clean_price(y, settlement) for y in [v / 100 for v in yields]]
fig = go.Figure()
fig.add_trace(go.Scatter(x=[v / 100 for v in yields], y=prices, mode="lines", name="Precio limpio"))
fig.add_trace(go.Scatter(
    x=[summary["ytm_pct"]], y=[summary["precio_limpio"]],
    mode="markers", marker=dict(size=10, color="red"), name="Punto actual",
))
fig.update_layout(
    xaxis_title="YTM %", yaxis_title="Precio limpio",
    hovermode="x unified", height=400, margin=dict(l=0, r=0, t=20, b=0),
)
st.plotly_chart(fig, use_container_width=True)

st.divider()

# ── Mesa rapida: precio -> yield para todos los bonos del universo ──────────
st.header("Mesa rápida")
st.caption("Cargá un precio limpio por bono y mirá el yield/duration de todo el universo junto.")

if "mesa_precios" not in st.session_state:
    st.session_state.mesa_precios = {n: 100.0 for n in edited["nombre"]}
for n in edited["nombre"]:
    st.session_state.mesa_precios.setdefault(n, 100.0)

mesa_settlement = st.date_input("Settlement (mesa)", value=date.today(), key="mesa_settlement")

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
