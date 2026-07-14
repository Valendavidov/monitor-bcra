"""
MONITOR DE BONOS — Argentina (USD)
===================================

App de Streamlit para pricear bonos soberanos argentinos en USD: Bonares
(ley argentina), Globales (ley NY) y BOPREAL (BCRA). Proyecto separado de
Paraguay.py/ (Paraguay/Uruguay) a pedido explicito: no comparten universo
de bonos ni motor de calculo, aunque la estructura de la app es hermana.

Particularidades de Argentina que no existian en Paraguay/Uruguay (ver
bond_model_ar.py para el detalle matematico):
    - CUPON ESCALONADO (step-up): AL29/AL30/AL35/AE38/AL41 (Bonares) y
      GD29/GD30/GD35/GD38/GD41/GD46 (Globales) no tienen una tasa fija
      unica - la tasa de cupon sube en fechas predeterminadas. AO27/AO28/
      AN29 (bonos nuevos 2025-2026) y los BOPREAL si tienen tasa fija.
    - AMORTIZACION en cuotas: los Bonares/Globales del canje 2020 y los
      BOPREAL Serie 1 amortizan en varias cuotas antes del vencimiento
      (los BOPREAL Serie 4 y AO27/AO28/AN29 son bullet).
    - PUT de BOPREAL: opcion del TENEDOR (no del emisor) de pedirle al
      BCRA la recompra anticipada desde determinada fecha. A diferencia
      de los calls de Paraguay/Uruguay, ACA NO se calcula un escenario
      "to worst" automatico - la usuaria elige a mano si pricear a
      vencimiento o al put (ver seccion "Escenario" en Cashflows/YAS).

Cuatro tabs (por ahora - Ops Historicas/NDF quedan para mas adelante si
hace falta): Cashflows, YAS, Monitor de bonos, FRAs.

Uso:
    streamlit run bonos_ar_app.py
"""

import json
import os
import sys
from datetime import date, timedelta

import pandas as pd
import streamlit as st

# Ver el mismo comentario en Paraguay.py/bonos_pyg_app.py: Streamlit Cloud
# a veces corre el script con un directorio de trabajo distinto al de este
# archivo, y ahi el import de bond_model_ar fallaria si no se agrega esta
# carpeta a mano al sys.path.
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from bond_model_ar import Bond

LAST_YIELDS_PATH = os.path.join(BASE_DIR, "yas_ultimos_yields_ar.json")
REGISTRY_PATH = os.path.join(BASE_DIR, "bonos_universo_ar.csv")
CUPONES_PATH = os.path.join(BASE_DIR, "bonos_cupones_ar.csv")
AMORTIZACION_PATH = os.path.join(BASE_DIR, "bonos_amortizacion_ar.csv")
PUTS_PATH = os.path.join(BASE_DIR, "bonos_puts_ar.csv")


# =============================================================================
# 1) CONFIGURACION GENERAL
# =============================================================================

DEC = 3  # cantidad de decimales que se muestran en TODA la app

PRIMARY = "#74ACDF"   # celeste de la bandera argentina
FLAG = ["#74ACDF", "#F6F6F6", "#74ACDF"]


def siguiente_dia_habil(d: date) -> date:
    """Si `d` cae sabado o domingo, la corre al lunes siguiente. No evita
    feriados de Argentina (no tenemos calendario cargado), solo fin de
    semana - igual que en Paraguay/Uruguay."""
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


SETTLEMENT_DEFAULT = siguiente_dia_habil(date.today() + timedelta(days=1))


def ajustar_settlement(fecha: date) -> date:
    if fecha.weekday() < 5:
        return fecha
    habil = siguiente_dia_habil(fecha)
    st.warning(f"{fecha} es fin de semana. Se usa el próximo día hábil: {habil}.")
    return habil


def fmt_es(x: float, decimales: int = DEC) -> str:
    """Coma de miles, punto decimal - ver el mismo comentario en
    Paraguay.py/bonos_pyg_app.py."""
    return f"{x:,.{decimales}f}"


st.set_page_config(page_title="Monitor de Bonos Argentina (USD)", layout="wide")


def _password_ok() -> bool:
    if st.session_state.get("password_ok"):
        return True
    st.title("Monitor de Bonos Argentina (USD)")
    pw = st.text_input("Contraseña", type="password", key="password_input")
    if pw:
        if pw == st.secrets.get("app_password", ""):
            st.session_state["password_ok"] = True
            st.rerun()
        else:
            st.error("Contraseña incorrecta.")
    return False


if not _password_ok():
    st.stop()


# =============================================================================
# 2) CSS: identidad visual (misma estructura que Paraguay/Uruguay)
# =============================================================================
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
    .stTabs [data-baseweb="tab-list"] {{ gap: 8px; padding-bottom: 6px; }}
    .stTabs [data-baseweb="tab"] {{
        background-color: #171B21; color: #8A8F98; border-radius: 999px;
        padding: 8px 22px; transition: background-color 0.15s ease, color 0.15s ease;
    }}
    .stTabs [data-baseweb="tab"]:hover {{ background-color: #1F242C; color: #C9CDD4; }}
    .stTabs [aria-selected="true"] {{
        background-color: {PRIMARY} !important; color: #F5F6F7 !important;
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

st.title("Bonos Argentina (USD)")
st.markdown(
    '<div class="flagbar">' + "".join(f'<span style="background:{c}"></span>' for c in FLAG) + "</div>",
    unsafe_allow_html=True,
)
st.caption("Bonares, Globales y BOPREAL — cupón fijo o escalonado, convención de días 30/360")
st.caption(
    "⚠️ BOPREAL: el precio de ejercicio del put se carga por defecto en 100% del capital vigente, "
    "de referencia — en la práctica el BCRA liquida el put en pesos al tipo de cambio oficial del "
    "día del ejercicio, no en USD reales. Editalo en la tab YAS si querés pricear otro supuesto."
)


# =============================================================================
# 3) FUNCIONES AUXILIARES
# =============================================================================

def load_registry() -> pd.DataFrame:
    df = pd.read_csv(REGISTRY_PATH)
    df["maturity"] = pd.to_datetime(df["maturity"]).dt.date
    df["isin"] = df["isin"].fillna("")
    return df


def load_cupones() -> dict:
    """Lee bonos_cupones_ar.csv y arma {nombre: [(fecha_desde, cupon_pct), ...]},
    ordenado por fecha - un bono de cupón fijo simplemente tiene una lista
    de una sola entrada."""
    df = pd.read_csv(CUPONES_PATH)
    df["fecha_desde"] = pd.to_datetime(df["fecha_desde"]).dt.date
    cupones: dict = {}
    for _, row in df.iterrows():
        cupones.setdefault(row["nombre"], []).append((row["fecha_desde"], float(row["cupon_pct"])))
    for nombre in cupones:
        cupones[nombre].sort()
    return cupones


def load_amortizacion() -> dict:
    if not os.path.exists(AMORTIZACION_PATH):
        return {}
    df = pd.read_csv(AMORTIZACION_PATH)
    df["fecha"] = pd.to_datetime(df["fecha"]).dt.date
    amort: dict = {}
    for _, row in df.iterrows():
        amort.setdefault(row["nombre"], []).append((row["fecha"], float(row["fraccion"])))
    return amort


def load_puts() -> dict:
    """Lee bonos_puts_ar.csv y arma {nombre: (fecha_desde, precio_pct_default)}.
    Bonos que no aparecen (la mayoria - solo algunas clases de BOPREAL
    tienen put) no tienen opción de redención anticipada."""
    if not os.path.exists(PUTS_PATH):
        return {}
    df = pd.read_csv(PUTS_PATH)
    df["fecha_desde"] = pd.to_datetime(df["fecha_desde"]).dt.date
    return {row["nombre"]: (row["fecha_desde"], float(row["precio_pct"])) for _, row in df.iterrows()}


CUPONES = load_cupones()
AMORTIZACION = load_amortizacion()
PUTS = load_puts()


def make_bond(row: pd.Series) -> Bond:
    return Bond(
        coupon_schedule=CUPONES.get(row["nombre"], [(row["maturity"], 0.0)]),
        maturity=row["maturity"],
        face=float(row["face"]),
        freq=int(row["freq"]),
        amortization=AMORTIZACION.get(row["nombre"], []),
        puts=[PUTS[row["nombre"]]] if row["nombre"] in PUTS else [],
    )


def filtrar_por_categoria(df: pd.DataFrame, key: str) -> pd.DataFrame:
    categorias = ["Todas"] + sorted(df["categoria"].unique().tolist())
    elegida = st.radio("Categoría", categorias, horizontal=True, key=key)
    if elegida != "Todas":
        return df[df["categoria"] == elegida]
    return df


def cargar_ultimo_yield(nombre_bono: str, default: float = 10.0) -> float:
    if not os.path.exists(LAST_YIELDS_PATH):
        return default
    try:
        with open(LAST_YIELDS_PATH, "r") as f:
            data = json.load(f)
        return float(data.get(nombre_bono, default))
    except (json.JSONDecodeError, ValueError):
        return default


def guardar_ultimo_yield(nombre_bono: str, ytm_pct: float) -> None:
    data = {}
    if os.path.exists(LAST_YIELDS_PATH):
        try:
            with open(LAST_YIELDS_PATH, "r") as f:
                data = json.load(f)
        except json.JSONDecodeError:
            data = {}
    data[nombre_bono] = ytm_pct
    with open(LAST_YIELDS_PATH, "w") as f:
        json.dump(data, f, indent=2)


def selector_escenario(bond: Bond, key_prefix: str, settlement: date):
    """Si el bono tiene puts cargados (BOPREAL con opción de recompra
    anticipada), dibuja el selector manual "Vencimiento normal" / "Put
    anticipado" - a diferencia de Paraguay/Uruguay, ACA la usuaria elige a
    mano, no se calcula ningún escenario "to worst" solo (ver docstring
    del módulo). Devuelve (put_date, put_price_pct), ambos None si no
    aplica o si se eligió vencimiento normal.

    La fecha de ejercicio NUNCA puede ser anterior (ni igual) al
    settlement - el motor de cálculo asume que se cobra un flujo FUTURO,
    así que una fecha de put ya pasada respecto del settlement rompe la
    cuenta (duration/precio negativos, sin sentido). Por eso `min_value`
    del date_input está clavado en el día siguiente al settlement, aunque
    la fecha "oficial" desde la que se habilita el put (según el
    cronograma cargado) haya sido anterior."""
    if not bond.puts:
        return None, None

    fecha_desde_default, precio_default = bond.puts[0]
    modo = st.radio(
        "Escenario", ["Vencimiento normal", "Put anticipado"], horizontal=True, key=f"{key_prefix}_escenario",
    )
    if modo == "Vencimiento normal":
        return None, None

    st.caption(f"Ejercicio del put habilitado desde el {fecha_desde_default}.")
    minimo_ejercicio = settlement + timedelta(days=1)
    default_ejercicio = max(fecha_desde_default, minimo_ejercicio)
    col_f, col_p = st.columns(2)
    with col_f:
        put_date = st.date_input(
            "Fecha de ejercicio del put", value=default_ejercicio, min_value=minimo_ejercicio,
            key=f"{key_prefix}_put_fecha",
        )
    with col_p:
        put_price_pct = st.number_input(
            "Precio del put (% del capital vigente)", value=precio_default, step=0.5,
            format=f"%.{DEC}f", key=f"{key_prefix}_put_precio",
        )
    if put_date < fecha_desde_default:
        st.warning(f"El put recién se puede ejercer desde el {fecha_desde_default}. Igual se calcula con la fecha elegida.")
    return put_date, put_price_pct


registry = load_registry()

_nombres_tabs = ["Cashflows", "YAS", "Monitor de bonos", "FRAs"]
_tabs = st.tabs(_nombres_tabs)
tab_cashflow, tab_yas, tab_monitor, tab_fras = _tabs


# =============================================================================
# TAB 1: CASHFLOWS
# =============================================================================
with tab_cashflow:
    registry_cf = filtrar_por_categoria(registry, key="cat_cf")

    col_sel, col_settle = st.columns([2, 1])
    with col_sel:
        nombre_cf = st.selectbox("Bono", registry_cf["nombre"].tolist(), key="cf_bono")
    with col_settle:
        settlement_cf = ajustar_settlement(st.date_input("Settlement", value=SETTLEMENT_DEFAULT, key="cf_settlement"))

    row_cf = registry_cf[registry_cf["nombre"] == nombre_cf].iloc[0]
    bond_cf = make_bond(row_cf)

    put_date_cf, put_precio_cf = selector_escenario(bond_cf, "cf", settlement_cf)

    prev_coupon, next_coupon, _, period_days, accrued_days, _ = bond_cf.schedule(settlement_cf)
    accrued = bond_cf.accrued_interest(settlement_cf)

    c1, c2, c3 = st.columns(3)
    c1.metric("Cupón anterior", prev_coupon.strftime("%Y-%m-%d"))
    c2.metric("Próximo cupón", next_coupon.strftime("%Y-%m-%d"))
    c3.metric("Interés corrido", fmt_es(accrued))

    st.subheader("Cashflows futuros")
    cf = bond_cf.cashflows(settlement_cf, put_date_cf, put_precio_cf)
    cf_display = cf.copy()
    for col in ["periodos", "cupon", "principal", "flujo_total"]:
        cf_display[col] = cf_display[col].map(fmt_es)
    st.dataframe(cf_display.rename(columns=str.upper), use_container_width=True, hide_index=True)
    st.download_button(
        "Descargar cashflows (CSV)",
        cf.to_csv(index=False).encode("utf-8"),
        file_name=f"cashflows_{nombre_cf.replace(' ', '_')}.csv",
        mime="text/csv",
    )


# =============================================================================
# TAB 2: YAS (estilo Bloomberg)
# =============================================================================
with tab_yas:
    registry_yas = filtrar_por_categoria(registry, key="cat_yas")

    col_inputs, col_grid = st.columns([1, 2])

    with col_inputs:
        nombre_sel = st.selectbox("Bono", registry_yas["nombre"].tolist(), key="yas_bono")
        row_sel = registry_yas[registry_yas["nombre"] == nombre_sel].iloc[0]
        bond = make_bond(row_sel)
        isin_txt = row_sel.get("isin") or "-"
        cupon_vigente_hoy = bond.coupon_rate_at(date.today())
        st.caption(
            f"ISIN: {isin_txt}  |  Cupón vigente: {cupon_vigente_hoy}%"
            + ("  (step-up)" if len(bond.coupon_schedule) > 1 else "")
            + f"  |  Vto: {row_sel['maturity']}"
        )

        settlement = ajustar_settlement(st.date_input("Settlement", value=SETTLEMENT_DEFAULT, key="yas_settlement"))

        put_date, put_precio = selector_escenario(bond, "yas", settlement)

        modo = st.radio(
            "Ingresar por", ["Yield %", "Precio limpio", "Precio sucio"], key="yas_modo",
        )

        if modo == "Precio limpio":
            clean_price_in = st.number_input("Precio limpio", value=100.0, step=0.25, format=f"%.{DEC}f", key="yas_price")
            summary = bond.summary(settlement, clean_price=clean_price_in, put_date=put_date, put_price_pct=put_precio)
        elif modo == "Precio sucio":
            # Pedido explícito para los Globales: poder cargar directamente
            # el precio SUCIO (lo que realmente se paga) en vez de tener
            # que restar a mano el interés corrido para llegar al limpio.
            dirty_price_in = st.number_input("Precio sucio", value=100.0, step=0.25, format=f"%.{DEC}f", key="yas_dirty_price")
            accrued_preview = bond.accrued_interest(settlement)
            clean_price_calc = dirty_price_in - accrued_preview
            st.caption(f"Interés corrido: {fmt_es(accrued_preview)} → precio limpio implícito: {fmt_es(clean_price_calc)}")
            summary = bond.summary(settlement, clean_price=clean_price_calc, put_date=put_date, put_price_pct=put_precio)
        else:
            ytm_default = cargar_ultimo_yield(nombre_sel)
            ytm_in = st.number_input(
                "Yield %", value=ytm_default, step=0.1, format=f"%.{DEC}f", key=f"yas_ytm_{nombre_sel}",
            )
            guardar_ultimo_yield(nombre_sel, ytm_in)
            summary = bond.summary(settlement, ytm_pct=ytm_in, put_date=put_date, put_price_pct=put_precio)

    with col_grid:
        st.markdown("#### Resultado")
        st.markdown('<div class="yas-label">ISIN</div>', unsafe_allow_html=True)
        st.markdown(f'<div class="yas-value">{isin_txt}</div>', unsafe_allow_html=True)
        g1, g2 = st.columns(2)
        with g1:
            st.markdown('<div class="yas-label">YIELD %</div>', unsafe_allow_html=True)
            st.markdown(f'<div class="yas-value">{fmt_es(summary["ytm_pct"])}</div>', unsafe_allow_html=True)
            st.markdown('<div class="yas-label">PRECIO LIMPIO</div>', unsafe_allow_html=True)
            st.markdown(f'<div class="yas-value">{fmt_es(summary["precio_limpio"])}</div>', unsafe_allow_html=True)
            st.markdown('<div class="yas-label">PRECIO SUCIO</div>', unsafe_allow_html=True)
            st.markdown(f'<div class="yas-value">{fmt_es(summary["precio_sucio"])}</div>', unsafe_allow_html=True)
            st.markdown('<div class="yas-label">INTERÉS CORRIDO</div>', unsafe_allow_html=True)
            st.markdown(f'<div class="yas-value">{fmt_es(summary["interes_corrido"])}</div>', unsafe_allow_html=True)
            paridad_val = bond.paridad(summary["precio_limpio"], settlement)
            st.markdown('<div class="yas-label">PARIDAD</div>', unsafe_allow_html=True)
            st.markdown(f'<div class="yas-value">{fmt_es(paridad_val)}</div>', unsafe_allow_html=True)
        with g2:
            st.markdown('<div class="yas-label">DURACIÓN MACAULAY (AÑOS)</div>', unsafe_allow_html=True)
            st.markdown(f'<div class="yas-value">{fmt_es(summary["duracion_macaulay_anios"])}</div>', unsafe_allow_html=True)
            st.markdown('<div class="yas-label">DURACIÓN MODIFICADA</div>', unsafe_allow_html=True)
            st.markdown(f'<div class="yas-value">{fmt_es(summary["duracion_modificada"])}</div>', unsafe_allow_html=True)
            st.markdown('<div class="yas-label">CONVEXIDAD</div>', unsafe_allow_html=True)
            st.markdown(f'<div class="yas-value">{fmt_es(summary["convexidad"])}</div>', unsafe_allow_html=True)
            st.markdown('<div class="yas-label">SETTLEMENT</div>', unsafe_allow_html=True)
            st.markdown(f'<div class="yas-value">{summary["settlement"]}</div>', unsafe_allow_html=True)
            if put_date is not None:
                st.markdown('<div class="yas-label">ESCENARIO</div>', unsafe_allow_html=True)
                st.markdown(f'<div class="yas-value">Put {put_date}</div>', unsafe_allow_html=True)


# =============================================================================
# TAB 3: MONITOR DE BONOS (universo + comparación bid/offer, a vencimiento)
# =============================================================================
with tab_monitor:
    st.subheader("Monitor de bonos")
    st.caption(
        "Editá precio o yield (bid/offer) directo en la tabla. Siempre a vencimiento normal "
        "(sin considerar puts de BOPREAL) — para pricear un escenario de put puntual usá la tab YAS."
    )

    monitor_universe = filtrar_por_categoria(registry, key="cat_monitor")

    px_bid_key, px_offer_key = "mesa_px_bid_ar", "mesa_px_offer_ar"
    yld_bid_key, yld_offer_key = "mesa_yield_bid_ar", "mesa_yield_offer_ar"
    for k in (px_bid_key, px_offer_key, yld_bid_key, yld_offer_key):
        st.session_state.setdefault(k, {})

    col_modo, col_settle = st.columns([1, 1])
    with col_modo:
        modo_mesa = st.radio("Ingresar por", ["Yield", "Precio"], horizontal=True, key="mesa_modo")
    with col_settle:
        mesa_settlement = ajustar_settlement(
            st.date_input("Settlement (comparación)", value=SETTLEMENT_DEFAULT, key="mesa_settlement")
        )

    for n in monitor_universe["nombre"]:
        if n not in st.session_state[yld_bid_key]:
            bono_seed = monitor_universe[monitor_universe["nombre"] == n].iloc[0]
            b_seed = make_bond(bono_seed)
            st.session_state[yld_bid_key][n] = 10.00
            st.session_state[yld_offer_key][n] = 9.50
            st.session_state[px_bid_key][n] = b_seed.clean_price(10.00, mesa_settlement)
            st.session_state[px_offer_key][n] = b_seed.clean_price(9.50, mesa_settlement)

    tabla_rows = []
    for _, row in monitor_universe.iterrows():
        n = row["nombre"]
        bono_row = registry[registry["nombre"] == n].iloc[0]
        b = make_bond(bono_row)

        px_bid = st.session_state[px_bid_key][n]
        px_offer = st.session_state[px_offer_key][n]
        yield_bid = st.session_state[yld_bid_key][n]
        yield_offer = st.session_state[yld_offer_key][n]

        px_mid = (px_bid + px_offer) / 2
        s_mid = b.summary(mesa_settlement, clean_price=px_mid)

        tabla_rows.append({
            "nombre": n,
            "isin": row.get("isin", ""),
            "codigo": row.get("codigo", ""),
            "yield_bid": round(yield_bid, DEC),
            "yield_offer": round(yield_offer, DEC),
            "px_bid": round(px_bid, DEC),
            "px_offer": round(px_offer, DEC),
            "spread_bid_offer_bps": fmt_es((yield_bid - yield_offer) * 100),
            "maturity": row["maturity"],
            "cupon_vigente_pct": fmt_es(b.coupon_rate_at(date.today())),
            "duracion_modificada": fmt_es(s_mid["duracion_modificada"]),
            "paridad": fmt_es(b.paridad(px_mid, mesa_settlement)),
        })
    tabla_df = pd.DataFrame(tabla_rows)

    columnas_orden = ["nombre", "isin", "codigo", "yield_bid", "yield_offer", "px_bid", "px_offer",
                      "spread_bid_offer_bps", "maturity", "cupon_vigente_pct", "duracion_modificada", "paridad"]
    campos_fijos = ["nombre", "isin", "codigo", "spread_bid_offer_bps", "maturity", "cupon_vigente_pct",
                    "duracion_modificada", "paridad"]
    if modo_mesa == "Precio":
        disabled_cols = campos_fijos + ["yield_bid", "yield_offer"]
    else:
        disabled_cols = campos_fijos + ["px_bid", "px_offer"]

    nombres_orden_mesa = monitor_universe["nombre"].tolist()
    mesa_editor_key = f"tabla_editor_ar_{modo_mesa}"

    def _mesa_on_edit():
        estado = st.session_state.get(mesa_editor_key, {})
        for idx, cambios in estado.get("edited_rows", {}).items():
            n = nombres_orden_mesa[idx]
            bono_row = registry[registry["nombre"] == n].iloc[0]
            b = make_bond(bono_row)
            if modo_mesa == "Precio":
                if "px_bid" in cambios:
                    px_bid = float(cambios["px_bid"])
                    st.session_state[px_bid_key][n] = px_bid
                    st.session_state[yld_bid_key][n] = b.yield_from_clean_price(px_bid, mesa_settlement)
                if "px_offer" in cambios:
                    px_offer = float(cambios["px_offer"])
                    st.session_state[px_offer_key][n] = px_offer
                    st.session_state[yld_offer_key][n] = b.yield_from_clean_price(px_offer, mesa_settlement)
            else:
                if "yield_bid" in cambios:
                    yield_bid = float(cambios["yield_bid"])
                    st.session_state[yld_bid_key][n] = yield_bid
                    st.session_state[px_bid_key][n] = b.clean_price(yield_bid, mesa_settlement)
                if "yield_offer" in cambios:
                    yield_offer = float(cambios["yield_offer"])
                    st.session_state[yld_offer_key][n] = yield_offer
                    st.session_state[px_offer_key][n] = b.clean_price(yield_offer, mesa_settlement)

    st.data_editor(
        tabla_df[columnas_orden],
        use_container_width=True,
        hide_index=True,
        disabled=disabled_cols,
        column_config={
            "nombre": st.column_config.TextColumn("NOMBRE"),
            "isin": st.column_config.TextColumn("ISIN"),
            "codigo": st.column_config.TextColumn("CÓDIGO"),
            "yield_bid": st.column_config.NumberColumn("YIELD BID %", format=f"%.{DEC}f"),
            "yield_offer": st.column_config.NumberColumn("YIELD OFFER %", format=f"%.{DEC}f"),
            "px_bid": st.column_config.NumberColumn("PX BID", format=f"%.{DEC}f"),
            "px_offer": st.column_config.NumberColumn("PX OFFER", format=f"%.{DEC}f"),
            "spread_bid_offer_bps": st.column_config.TextColumn("SPREAD B/O (BPS)"),
            "maturity": st.column_config.DateColumn("VENCIMIENTO"),
            "cupon_vigente_pct": st.column_config.TextColumn("CUPÓN VIGENTE %"),
            "duracion_modificada": st.column_config.TextColumn("MOD. DURATION"),
            "paridad": st.column_config.TextColumn("PARIDAD"),
        },
        key=mesa_editor_key,
        on_change=_mesa_on_edit,
    )


# =============================================================================
# TAB 4: FRAs (tasas forward implícitas)
# =============================================================================
with tab_fras:
    st.subheader("FRAs — tasas forward implícitas")
    st.caption("Se compara dentro de UNA sola categoría (no tiene sentido mezclar leyes/emisores distintos en una misma curva).")

    categorias_fra = sorted(registry["categoria"].unique().tolist())
    cat_fra = st.radio("Curva", categorias_fra, horizontal=True, key="fras_categoria")
    curva = registry[registry["categoria"] == cat_fra].copy()
    fra_key_suffix = cat_fra

    hoy = date.today()
    curva["dias_vto"] = curva["maturity"].apply(lambda m: (m - hoy).days)
    curva = curva.sort_values("dias_vto").reset_index(drop=True)

    fras_yield_key = f"fras_yield_{fra_key_suffix}"
    st.session_state.setdefault(fras_yield_key, {})
    for n in curva["nombre"]:
        if n not in st.session_state[fras_yield_key]:
            st.session_state[fras_yield_key][n] = cargar_ultimo_yield(n)

    st.markdown("#### Yields spot (a vencimiento)")
    input_rows = []
    for _, row in curva.iterrows():
        n = row["nombre"]
        dias = int(row["dias_vto"])
        yld_semi = st.session_state[fras_yield_key][n]
        tea = ((1 + yld_semi / 100 / 2) ** 2 - 1) * 100
        tna = (365 / dias) * ((1 + tea / 100) ** (dias / 365) - 1) * 100 if dias > 0 else 0.0
        input_rows.append({
            "bono": n,
            "dias_vto": fmt_es(dias, decimales=0),
            "yield_semianual": round(yld_semi, DEC),
            "yield_anual": round(tea, DEC),
            "tna": round(tna, DEC),
        })
    input_df = pd.DataFrame(input_rows)

    nombres_orden_fras = curva["nombre"].tolist()
    fras_editor_key = f"fras_editor_{fra_key_suffix}"

    def _fras_on_edit():
        estado = st.session_state.get(fras_editor_key, {})
        for idx, cambios in estado.get("edited_rows", {}).items():
            if "yield_semianual" in cambios:
                n = nombres_orden_fras[idx]
                st.session_state[fras_yield_key][n] = float(cambios["yield_semianual"])

    st.data_editor(
        input_df,
        use_container_width=True,
        hide_index=True,
        disabled=["bono", "dias_vto", "yield_anual", "tna"],
        column_config={
            "bono": st.column_config.TextColumn("BONO"),
            "dias_vto": st.column_config.TextColumn("DÍAS AL VTO"),
            "yield_semianual": st.column_config.NumberColumn("YIELD SEMIANUAL %", format=f"%.{DEC}f"),
            "yield_anual": st.column_config.NumberColumn("YIELD ANUAL (TEA) %", format=f"%.{DEC}f"),
            "tna": st.column_config.NumberColumn("TNA %", format=f"%.{DEC}f"),
        },
        key=fras_editor_key,
        on_change=_fras_on_edit,
    )

    nombres = nombres_orden_fras
    codigos = dict(zip(curva["nombre"], curva["codigo"]))
    dias_por_bono = {n: int(curva[curva["nombre"] == n]["dias_vto"].iloc[0]) for n in nombres}
    anios_al_vto = {n: dias_por_bono[n] / 365 for n in nombres}

    yield_semi = {n: st.session_state[fras_yield_key][n] for n in nombres}
    yield_tea = {n: ((1 + yield_semi[n] / 100 / 2) ** 2 - 1) * 100 for n in nombres}
    yield_tna = {
        n: (365 / dias_por_bono[n]) * ((1 + yield_tea[n] / 100) ** (dias_por_bono[n] / 365) - 1) * 100
        if dias_por_bono[n] > 0 else 0.0
        for n in nombres
    }

    etiquetas = [codigos[n] for n in nombres]
    t_por_nodo = anios_al_vto

    TASAS_BASE = {"Semi Anual": yield_semi, "Anual (TEA)": yield_tea, "TNA": yield_tna}

    def forward_compounding(ti, ri, tj, rj):
        return ((1 + rj) ** tj / (1 + ri) ** ti) ** (1 / (tj - ti)) - 1

    def forward_simple(ti, ri, tj, rj):
        return ((1 + rj * tj) / (1 + ri * ti) - 1) / (tj - ti)

    def armar_matriz(tasas_pct: dict, formula):
        filas_texto, filas_crudo = [], []
        for i, ni in enumerate(nombres):
            fila_t, fila_c = [], []
            for j, nj in enumerate(nombres):
                if j <= i:
                    fila_t.append("")
                    fila_c.append(None)
                else:
                    ti, tj = t_por_nodo[ni], t_por_nodo[nj]
                    ri, rj = tasas_pct[ni] / 100, tasas_pct[nj] / 100
                    valor = formula(ti, ri, tj, rj) * 100
                    fila_t.append(f"{valor:.{DEC}f}")
                    fila_c.append(valor)
            filas_texto.append(fila_t)
            filas_crudo.append(fila_c)
        texto = pd.DataFrame(filas_texto, columns=etiquetas, index=etiquetas)
        crudo = pd.DataFrame(filas_crudo, columns=etiquetas, index=etiquetas)
        return texto, crudo

    _VERDE, _AMARILLO, _ROJO = (46, 204, 113), (241, 196, 15), (231, 76, 60)
    _GRIS_VACIO = "background-color: #1A1E24; color: #3A3F47;"

    def _interp(c1, c2, f):
        return tuple(int(c1[k] + (c2[k] - c1[k]) * f) for k in range(3))

    def _mostrar_matriz(texto: pd.DataFrame, crudo: pd.DataFrame):
        validos = [v for fila in crudo.to_numpy().tolist() for v in fila if not pd.isna(v)]
        lo, hi = (min(validos), max(validos)) if validos else (0.0, 1.0)

        def _color(v):
            if pd.isna(v):
                return _GRIS_VACIO
            frac = 0.5 if hi == lo else min(max((v - lo) / (hi - lo), 0.0), 1.0)
            rgb = _interp(_VERDE, _AMARILLO, frac / 0.5) if frac <= 0.5 else _interp(_AMARILLO, _ROJO, (frac - 0.5) / 0.5)
            return f"background-color: rgb{rgb}; color: #14181F; font-weight: 600;"

        estilos = crudo.map(_color)
        st.dataframe(texto.style.apply(lambda _: estilos, axis=None), use_container_width=True)

    if len(nombres) < 2:
        st.caption("Hace falta más de un bono en esta categoría para armar una matriz de forwards.")
    else:
        st.markdown("#### Anual Compounding")
        base_a = st.radio(
            "Tasa de base", list(TASAS_BASE.keys()), horizontal=True, index=0, key=f"fras_base_a_{fra_key_suffix}",
        )
        _mostrar_matriz(*armar_matriz(TASAS_BASE[base_a], forward_compounding))

        st.markdown("#### Simple Rate")
        base_b = st.radio(
            "Tasa de base", list(TASAS_BASE.keys()), horizontal=True, index=2, key=f"fras_base_b_{fra_key_suffix}",
        )
        _mostrar_matriz(*armar_matriz(TASAS_BASE[base_b], forward_simple))
