"""
MONITOR DE BONOS — Argentina (USD)
===================================

App de Streamlit para pricear bonos soberanos argentinos en USD: Bonares
(ley argentina), Globales (ley NY) y BOPREAL (BCRA). Proyecto separado de
Paraguay.py/ (Paraguay/Uruguay) a pedido explicito: no comparten universo
de bonos ni motor de calculo, aunque la estructura de la app es hermana.

Particularidades de Argentina que no existian en Paraguay/Uruguay (ver
bond_model_ar.py para el detalle matematico):
    - CUPON ESCALONADO (step-up): AL30/AL35/AE38/AL41 (Bonares) y
      GD30/GD35/GD38/GD41/GD46 (Globales) no tienen una tasa fija
      unica - la tasa de cupon sube en fechas predeterminadas. AL29/GD29
      son la excepcion de esa misma familia (1,00% fijo, sin step-up).
      AO27/AO28/AN29 (bonos nuevos 2025-2026) y los BOPREAL tambien
      tienen tasa fija.
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
import re
import sys
from datetime import date, timedelta

import pandas as pd
import requests
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


# =============================================================================
# CONVERSIÓN ENTRE CONVENCIONES DE TASA (TEA / TNA Semianual)
# =============================================================================
# El motor de cálculo (bond_model_ar.py) siempre trabaja con un yield que es,
# por construcción, la TEA (tasa efectiva anual): dirty_price() la resuelve
# como un XIRR clásico (Actual/365, capitalización anual efectiva - ver
# docstring de bond_model_ar.py). A partir de esa TEA:
#   - TNA SEMIANUAL = ((1+TEA)^(180/360) - 1) * (360/180)  (nominal anual,
#     base semestral - la tasa que, compuesta 2 veces al año, da la TEA)
# Ninguna de las dos depende del plazo/vencimiento del bono. Se usan en
# YAS, Monitor de bonos y FRAs para que la usuaria pueda pricear
# indistintamente en cualquiera de las dos convenciones.
def tna_a_tea(tna_pct: float) -> float:
    return ((1 + tna_pct / 100 / 2) ** 2 - 1) * 100


def tea_a_tna(tea_pct: float) -> float:
    return (((1 + tea_pct / 100) ** 0.5) - 1) * 2 * 100


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
    # coupon_anchor: solo AL29/GD29/AE38/GD38 lo tienen cargado (ver
    # docstring de Bond.coupon_anchor) - el resto queda en None/NaT.
    df["coupon_anchor"] = pd.to_datetime(df["coupon_anchor"]).dt.date
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
    coupon_anchor = row.get("coupon_anchor")
    if pd.isna(coupon_anchor):
        coupon_anchor = None
    return Bond(
        coupon_schedule=CUPONES.get(row["nombre"], [(row["maturity"], 0.0)]),
        maturity=row["maturity"],
        face=float(row["face"]),
        freq=int(row["freq"]),
        coupon_anchor=coupon_anchor,
        amortization=AMORTIZACION.get(row["nombre"], []),
        puts=[PUTS[row["nombre"]]] if row["nombre"] in PUTS else [],
    )


# =============================================================================
# PRECIO CLEAN: "de mercado" (por 100 de capital vigente) vs "original"
# (por 100 del face original - la unidad nativa de clean_price/dirty_price)
# =============================================================================
# Verificado contra una tabla de referencia real: el precio DIRTY se cotiza
# por 100 de face ORIGINAL (exactamente lo que devuelve dirty_price(), sin
# reescalar), pero el precio CLEAN se cotiza reescalado sobre el capital
# VIGENTE - dos convenciones distintas para el mismo bono. Para un bono que
# todavia no amortizo nada (capital vigente = 100% del original) da exacto
# lo mismo; la diferencia solo importa para bonos ya parcialmente
# amortizados (Bonares/Globales del canje 2020 con vencimiento cercano,
# BOPREAL Serie 1).
def clean_original_a_mercado(b: Bond, clean_original: float, settlement: date) -> float:
    return clean_original / b.outstanding_pct(settlement) * 100


def clean_mercado_a_original(b: Bond, clean_mercado: float, settlement: date) -> float:
    return clean_mercado * b.outstanding_pct(settlement) / 100


# =============================================================================
# RECALCULO BIDIRECCIONAL: precio Clean / precio Dirty / TEA / TNA Semianual
# =============================================================================
# En Monitor de bonos y FRAs, las cuatro variables (precio Clean "de
# mercado", precio Dirty, TEA y TNA Semianual) son todas editables: la
# usuaria tipea CUALQUIERA de las cuatro y las otras tres se recalculan
# solas. Para eso alcanza con guardar UN solo estado canonico por bono
# (el precio clean "original", por 100 de face original - la unidad
# nativa del motor) y reconstruir las cuatro columnas a partir de eso.
def resolver_desde_clean(b: Bond, clean_original: float, settlement: date) -> dict:
    accrued = b.accrued_interest(settlement)
    tea = b.yield_from_clean_price(clean_original, settlement)
    return {
        "clean_original": clean_original,
        "tea": tea,
        "clean_mercado": clean_original_a_mercado(b, clean_original, settlement),
        "dirty": clean_original + accrued,
    }


def resolver_desde_tea(b: Bond, tea: float, settlement: date) -> dict:
    clean_original = b.clean_price(tea, settlement)
    return resolver_desde_clean(b, clean_original, settlement)


def filtrar_por_categoria(df: pd.DataFrame, key: str) -> pd.DataFrame:
    categorias = ["Todas"] + sorted(df["categoria"].unique().tolist())
    elegida = st.radio("Categoría", categorias, horizontal=True, key=key)
    if elegida != "Todas":
        return df[df["categoria"] == elegida]
    return df


def filtrar_por_categorias_multi(df: pd.DataFrame, key: str) -> pd.DataFrame:
    """Como filtrar_por_categoria, pero con multiselect en vez de radio -
    para poder combinar categorías (ej. Bonar + Global, dejando afuera
    BOPREAL) en vez de elegir solo una a la vez o todas juntas."""
    categorias = sorted(df["categoria"].unique().tolist())
    elegidas = st.multiselect("Categoría", categorias, default=categorias, key=key)
    if not elegidas:
        return df
    return df[df["categoria"].isin(elegidas)]


def cargar_ultimo_yield(nombre_bono: str, default: float = 10.0) -> float:
    if not os.path.exists(LAST_YIELDS_PATH):
        return default
    try:
        with open(LAST_YIELDS_PATH, "r") as f:
            data = json.load(f)
        return float(data.get(nombre_bono, default))
    except (json.JSONDecodeError, ValueError):
        return default


def guardar_ultimo_yield(nombre_bono: str, tea_pct: float) -> None:
    data = {}
    if os.path.exists(LAST_YIELDS_PATH):
        try:
            with open(LAST_YIELDS_PATH, "r") as f:
                data = json.load(f)
        except json.JSONDecodeError:
            data = {}
    data[nombre_bono] = tea_pct
    with open(LAST_YIELDS_PATH, "w") as f:
        json.dump(data, f, indent=2)


@st.cache_data(ttl=3600)
def obtener_a3500():
    """Trae el A3500 (tipo de cambio mayorista de referencia) más reciente
    publicado por el BCRA, vía la misma API que usa app.py (Monitor de
    Liquidez BCRA) - variable 5. Se usa para el put BCRA de BOPREAL (Valor
    Técnico × A3500). Devuelve (valor, fecha) o (None, None) si falla."""
    try:
        hoy = date.today().strftime("%Y-%m-%d")
        desde = (date.today() - timedelta(days=10)).strftime("%Y-%m-%d")
        url = f"https://api.bcra.gob.ar/estadisticas/v4.0/monetarias/5?desde={desde}&hasta={hoy}"
        r = requests.get(url, verify=False, timeout=10)
        r.raise_for_status()
        resultados = r.json().get("results", [])
        if resultados and resultados[0].get("detalle"):
            ultimo = resultados[0]["detalle"][-1]
            return float(ultimo["valor"]), ultimo["fecha"]
    except Exception:
        pass
    return None, None


@st.cache_data(ttl=3600)
def obtener_valor_afip_bopreal():
    """Trae el "Valor del BOPREAL" más reciente publicado por AFIP/ARCA
    (scraping de la tabla HTML estática que arma
    servicioscf.afip.gob.ar/publico/byma/bopreal.aspx - el iframe que
    incrusta valores-diarios.asp). No es una API documentada: si AFIP
    cambia el formato de esa página esto puede dejar de funcionar - en ese
    caso hay que cargar el valor a mano en la tab YAS. Devuelve
    (valor_pesos, fecha_texto) o (None, None) si falla.

    OJO: esta página publica UN solo valor por día, sin desglosar por
    serie/clase de BOPREAL (BPOA7/B7/C7/D7/A8/B8) - no está confirmado a
    cuál corresponde exactamente, así que se muestra como referencia para
    que la usuaria lo convierta a mano a % del capital vigente."""
    try:
        r = requests.get(
            "https://servicioscf.afip.gob.ar/publico/byma/bopreal.aspx?paginado=10",
            timeout=10,
        )
        r.raise_for_status()
        m = re.search(
            r'data-title="Fecha"[^>]*>([\d/]+)</td>\s*<td data-title="Valor del BOPREAL">\$\s*([\d.,]+)</td>',
            r.text,
        )
        if not m:
            return None, None
        fecha_txt, valor_txt = m.group(1), m.group(2)
        valor = float(valor_txt.replace(".", "").replace(",", "."))
        return valor, fecha_txt
    except Exception:
        return None, None


def selector_escenario(bond: Bond, key_prefix: str, settlement: date):
    """Si el bono tiene puts cargados (BOPREAL con opción de recompra
    anticipada), dibuja el selector manual "Vencimiento normal" / "Put
    anticipado" - a diferencia de Paraguay/Uruguay, ACA la usuaria elige a
    mano, no se calcula ningún escenario "to worst" solo (ver docstring
    del módulo). Devuelve (put_date, put_price_pct), ambos None si no
    aplica o si se eligió vencimiento normal.

    Por default se asume que el put se ejerce el mismo día del settlement
    (fecha de ejecución = fecha de valuación) - pedido explícito. Esa
    fecha es editable para simular un ejercicio más adelante; nunca puede
    ser ANTERIOR al settlement (el motor de cálculo asume que se cobra un
    flujo futuro-o-presente, no pasado)."""
    if not bond.puts:
        return None, None

    fecha_desde_default, precio_default = bond.puts[0]
    modo = st.radio(
        "Escenario", ["Vencimiento normal", "Put anticipado"], horizontal=True, key=f"{key_prefix}_escenario",
    )
    if modo == "Vencimiento normal":
        return None, None

    st.caption(f"Ejercicio del put habilitado desde el {fecha_desde_default}.")
    col_f, _ = st.columns(2)
    with col_f:
        put_date = st.date_input(
            "Fecha de ejecución del put", value=settlement, min_value=settlement,
            key=f"{key_prefix}_put_fecha",
        )
    if put_date < fecha_desde_default:
        st.warning(f"El put recién se puede ejercer desde el {fecha_desde_default}. Igual se calcula con la fecha elegida.")

    tipo_put = st.radio(
        "Precio del put", ["Manual (% del capital vigente)", "BCRA (Valor Técnico × A3500)", "AFIP/ARCA (valor publicado)"],
        key=f"{key_prefix}_put_tipo",
    )

    if tipo_put == "Manual (% del capital vigente)":
        put_price_pct = st.number_input(
            "Precio del put (% del capital vigente)", value=precio_default, step=0.5,
            format=f"%.{DEC}f", key=f"{key_prefix}_put_precio",
        )
    elif tipo_put == "BCRA (Valor Técnico × A3500)":
        # Valor Técnico × A3500: en USD-equivalente (dividiendo por el
        # mismo A3500) esto es exactamente el 100% del capital vigente +
        # interés corrido al momento del ejercicio - lo que este motor ya
        # calcula solo con put_price_pct=100. El A3500 acá es solo para
        # mostrar el monto en PESOS que efectivamente liquidaría el BCRA
        # (informativo), no cambia el precio en USD.
        a3500_api, fecha_a3500 = obtener_a3500()
        if a3500_api is None:
            st.warning("No se pudo obtener el A3500 del BCRA (revisá la conexión). Cargalo a mano.")
        col_a, _ = st.columns(2)
        with col_a:
            a3500 = st.number_input(
                "A3500 (ARS/USD)", value=a3500_api or 0.0, step=1.0, format="%.4f",
                key=f"{key_prefix}_a3500",
                help=f"Último publicado por el BCRA: {fecha_a3500}" if fecha_a3500 else "Sin dato de la API - cargalo a mano.",
            )
        put_price_pct = 100.0
        valor_tecnico = bond.outstanding_pct(settlement) + bond.accrued_interest(settlement)
        if a3500 > 0:
            st.caption(
                f"Valor Técnico ≈ USD {fmt_es(valor_tecnico)} (por 100 de face original) × A3500 {fmt_es(a3500, 4)} "
                f"≈ $ {fmt_es(valor_tecnico * a3500)} por cada 100 de face original."
            )
    else:
        valor_afip_api, fecha_afip = obtener_valor_afip_bopreal()
        if valor_afip_api is None:
            st.warning("No se pudo obtener el valor de AFIP/ARCA (revisá la conexión, o puede haber cambiado la página). Cargalo a mano.")
        col_v, col_e = st.columns(2)
        with col_v:
            st.number_input(
                "Valor AFIP/ARCA publicado ($)", value=valor_afip_api or 0.0, step=0.01, format="%.2f",
                key=f"{key_prefix}_afip_valor",
                help=f"Último publicado: {fecha_afip}" if fecha_afip else "Sin dato - cargalo a mano.",
            )
        st.caption(
            "AFIP/ARCA publica un solo valor por día, sin desglosar por serie/clase de BOPREAL - "
            "convertilo vos a % del capital vigente (no está confirmado a qué serie corresponde exactamente)."
        )
        with col_e:
            put_price_pct = st.number_input(
                "Equivalente en % del capital vigente", value=100.0, step=0.5,
                format=f"%.{DEC}f", key=f"{key_prefix}_afip_pct",
            )

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
            "Ingresar por", ["Yield %", "Precio Clean", "Precio Dirty"], key="yas_modo",
        )

        if modo == "Precio Clean":
            # El precio Clean se cotiza por 100 de capital VIGENTE (no del
            # face original) - ver clean_mercado_a_original(). Para un bono
            # que no amortizo nada todavia es exactamente lo mismo.
            clean_price_in = st.number_input("Precio Clean", value=100.0, step=0.25, format=f"%.{DEC}f", key="yas_price")
            clean_original_in = clean_mercado_a_original(bond, clean_price_in, settlement)
            summary = bond.summary(settlement, clean_price=clean_original_in, put_date=put_date, put_price_pct=put_precio)
        elif modo == "Precio Dirty":
            # Pedido explícito para los Globales: poder cargar directamente
            # el precio Dirty (lo que realmente se paga) en vez de tener
            # que restar a mano el interés corrido para llegar al Clean.
            dirty_price_in = st.number_input("Precio Dirty", value=100.0, step=0.25, format=f"%.{DEC}f", key="yas_dirty_price")
            accrued_preview = bond.accrued_interest(settlement)
            clean_price_calc = dirty_price_in - accrued_preview
            st.caption(f"Interés corrido: {fmt_es(accrued_preview)} → precio Clean implícito: {fmt_es(clean_price_calc)}")
            summary = bond.summary(settlement, clean_price=clean_price_calc, put_date=put_date, put_price_pct=put_precio)
        else:
            # La tasa se puede tipear en TEA (la convención nativa del motor
            # de cálculo, resuelta como XIRR) o TNA Semianual - se convierte
            # a TEA antes de pricear, y se vuelve a convertir para mostrar
            # el valor guardado la próxima vez que se abre este bono.
            convencion = st.radio(
                "Convención", ["TEA", "TNA Semianual"], horizontal=True, key="yas_convencion",
            )
            tea_guardada = cargar_ultimo_yield(nombre_sel)
            valor_default = tea_a_tna(tea_guardada) if convencion == "TNA Semianual" else tea_guardada
            tea_in_raw = st.number_input(
                f"Yield {convencion} %", value=valor_default, step=0.1, format=f"%.{DEC}f",
                key=f"yas_tea_{nombre_sel}_{convencion}",
            )
            tea_in = tna_a_tea(tea_in_raw) if convencion == "TNA Semianual" else tea_in_raw
            guardar_ultimo_yield(nombre_sel, tea_in)
            summary = bond.summary(settlement, tea_pct=tea_in, put_date=put_date, put_price_pct=put_precio)

    with col_grid:
        st.markdown("#### Resultado")
        st.markdown('<div class="yas-label">ISIN</div>', unsafe_allow_html=True)
        st.markdown(f'<div class="yas-value">{isin_txt}</div>', unsafe_allow_html=True)
        g1, g2 = st.columns(2)
        with g1:
            st.markdown('<div class="yas-label">YIELD TEA %</div>', unsafe_allow_html=True)
            st.markdown(f'<div class="yas-value">{fmt_es(summary["tea_pct"])}</div>', unsafe_allow_html=True)
            st.markdown('<div class="yas-label">YIELD TNA SEMIANUAL %</div>', unsafe_allow_html=True)
            st.markdown(f'<div class="yas-value">{fmt_es(tea_a_tna(summary["tea_pct"]))}</div>', unsafe_allow_html=True)
            precio_clean_mercado = clean_original_a_mercado(bond, summary["precio_clean"], settlement)
            st.markdown('<div class="yas-label">PRECIO CLEAN</div>', unsafe_allow_html=True)
            st.markdown(f'<div class="yas-value">{fmt_es(precio_clean_mercado)}</div>', unsafe_allow_html=True)
            st.markdown('<div class="yas-label">PRECIO DIRTY</div>', unsafe_allow_html=True)
            st.markdown(f'<div class="yas-value">{fmt_es(summary["precio_dirty"])}</div>', unsafe_allow_html=True)
            st.markdown('<div class="yas-label">INTERÉS CORRIDO</div>', unsafe_allow_html=True)
            st.markdown(f'<div class="yas-value">{fmt_es(summary["interes_corrido"])}</div>', unsafe_allow_html=True)
            paridad_val = bond.paridad(summary["precio_clean"], settlement)
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
        "Editá cualquiera de las cuatro columnas — PRECIO CLEAN, PRECIO DIRTY, TEA o TNA SEMIANUAL — "
        "las otras tres se recalculan solas. Siempre a vencimiento normal (sin considerar puts de "
        "BOPREAL) — para pricear un escenario de put puntual usá la tab YAS."
    )

    monitor_universe = filtrar_por_categorias_multi(registry, key="cat_monitor")

    clean_key = "mesa_clean_original_ar"
    st.session_state.setdefault(clean_key, {})

    mesa_settlement = ajustar_settlement(
        st.date_input("Settlement (comparación)", value=SETTLEMENT_DEFAULT, key="mesa_settlement")
    )

    for n in monitor_universe["nombre"]:
        if n not in st.session_state[clean_key]:
            bono_seed = monitor_universe[monitor_universe["nombre"] == n].iloc[0]
            b_seed = make_bond(bono_seed)
            # Semilla: TEA 10% de referencia, guardada como precio Clean
            # "original" (la unidad nativa del motor) - de ahí se derivan
            # las cuatro columnas editables (ver resolver_desde_clean).
            st.session_state[clean_key][n] = b_seed.clean_price(10.00, mesa_settlement)

    tabla_rows = []
    for _, row in monitor_universe.iterrows():
        n = row["nombre"]
        bono_row = registry[registry["nombre"] == n].iloc[0]
        b = make_bond(bono_row)
        dias_vto = (row["maturity"] - mesa_settlement).days

        clean_original = st.session_state[clean_key][n]
        estado = resolver_desde_clean(b, clean_original, mesa_settlement)

        tabla_rows.append({
            "nombre": n,
            "isin": row.get("isin", ""),
            "codigo": row.get("codigo", ""),
            "precio_clean": round(estado["clean_mercado"], DEC),
            "precio_dirty": round(estado["dirty"], DEC),
            "tea": round(estado["tea"], DEC),
            "tna_semianual": round(tea_a_tna(estado["tea"]), DEC),
            "paridad": fmt_es(b.paridad(clean_original, mesa_settlement)),
            "dias_vto": fmt_es(dias_vto, decimales=0),
            "maturity": row["maturity"],
            "cupon_vigente_pct": fmt_es(b.coupon_rate_at(date.today())),
            "duracion_modificada": fmt_es(
                b.duration_convexity(estado["tea"], mesa_settlement)["modified_duration"]
            ),
        })
    tabla_df = pd.DataFrame(tabla_rows)

    columnas_orden = ["nombre", "isin", "codigo", "precio_clean", "precio_dirty", "tea", "tna_semianual",
                      "paridad", "dias_vto", "maturity", "cupon_vigente_pct", "duracion_modificada"]
    campos_fijos = ["nombre", "isin", "codigo", "paridad", "dias_vto", "maturity",
                     "cupon_vigente_pct", "duracion_modificada"]

    nombres_orden_mesa = monitor_universe["nombre"].tolist()
    mesa_editor_key = "tabla_editor_ar"

    def _mesa_on_edit():
        estado_widget = st.session_state.get(mesa_editor_key, {})
        for idx, cambios in estado_widget.get("edited_rows", {}).items():
            n = nombres_orden_mesa[idx]
            bono_row = registry[registry["nombre"] == n].iloc[0]
            b = make_bond(bono_row)
            # Si en la misma edición cambió más de un campo (raro, pero
            # puede pasar con un paste multi-celda), se prioriza en este
            # orden: precio Clean > precio Dirty > TEA > TNA Semianual.
            if "precio_clean" in cambios:
                clean_original = clean_mercado_a_original(b, float(cambios["precio_clean"]), mesa_settlement)
            elif "precio_dirty" in cambios:
                clean_original = float(cambios["precio_dirty"]) - b.accrued_interest(mesa_settlement)
            elif "tea" in cambios:
                clean_original = b.clean_price(float(cambios["tea"]), mesa_settlement)
            elif "tna_semianual" in cambios:
                clean_original = b.clean_price(tna_a_tea(float(cambios["tna_semianual"])), mesa_settlement)
            else:
                continue
            st.session_state[clean_key][n] = clean_original

    st.data_editor(
        tabla_df[columnas_orden],
        use_container_width=True,
        hide_index=True,
        disabled=campos_fijos,
        column_config={
            "nombre": st.column_config.TextColumn("NOMBRE"),
            "isin": st.column_config.TextColumn("ISIN"),
            "codigo": st.column_config.TextColumn("CÓDIGO"),
            "precio_clean": st.column_config.NumberColumn("PRECIO CLEAN", format=f"%.{DEC}f"),
            "precio_dirty": st.column_config.NumberColumn("PRECIO DIRTY", format=f"%.{DEC}f"),
            "tea": st.column_config.NumberColumn("TEA %", format=f"%.{DEC}f"),
            "tna_semianual": st.column_config.NumberColumn("TNA SEMIANUAL %", format=f"%.{DEC}f"),
            "paridad": st.column_config.TextColumn("PARIDAD"),
            "dias_vto": st.column_config.TextColumn("DAYS"),
            "maturity": st.column_config.DateColumn("VENCIMIENTO"),
            "cupon_vigente_pct": st.column_config.TextColumn("CUPÓN VIGENTE %"),
            "duracion_modificada": st.column_config.TextColumn("MOD. DURATION"),
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

    fras_clean_key = f"fras_clean_{fra_key_suffix}"
    st.session_state.setdefault(fras_clean_key, {})
    bonos_por_nombre = {row["nombre"]: row for _, row in curva.iterrows()}
    for n, row in bonos_por_nombre.items():
        if n not in st.session_state[fras_clean_key]:
            b_seed = make_bond(row)
            tea_seed = cargar_ultimo_yield(n)
            st.session_state[fras_clean_key][n] = b_seed.clean_price(tea_seed, hoy)

    st.markdown("#### Precios/yields spot (a vencimiento)")
    st.caption("Editá cualquiera de las cuatro columnas - las otras tres se recalculan solas.")
    estados_fras = {}
    input_rows = []
    for _, row in curva.iterrows():
        n = row["nombre"]
        dias = int(row["dias_vto"])
        b = make_bond(row)
        clean_original = st.session_state[fras_clean_key][n]
        estado = resolver_desde_clean(b, clean_original, hoy)
        estados_fras[n] = estado
        input_rows.append({
            "bono": n,
            "dias_vto": fmt_es(dias, decimales=0),
            "precio_clean": round(estado["clean_mercado"], DEC),
            "precio_dirty": round(estado["dirty"], DEC),
            "tea": round(estado["tea"], DEC),
            "tna_semianual": round(tea_a_tna(estado["tea"]), DEC),
        })
    input_df = pd.DataFrame(input_rows)

    nombres_orden_fras = curva["nombre"].tolist()
    fras_editor_key = f"fras_editor_{fra_key_suffix}"

    def _fras_on_edit():
        estado_widget = st.session_state.get(fras_editor_key, {})
        for idx, cambios in estado_widget.get("edited_rows", {}).items():
            n = nombres_orden_fras[idx]
            b = make_bond(bonos_por_nombre[n])
            # Prioridad si se editó más de un campo a la vez: precio Clean
            # > precio Dirty > TEA > TNA Semianual (ver Monitor de bonos).
            if "precio_clean" in cambios:
                clean_original = clean_mercado_a_original(b, float(cambios["precio_clean"]), hoy)
            elif "precio_dirty" in cambios:
                clean_original = float(cambios["precio_dirty"]) - b.accrued_interest(hoy)
            elif "tea" in cambios:
                clean_original = b.clean_price(float(cambios["tea"]), hoy)
            elif "tna_semianual" in cambios:
                clean_original = b.clean_price(tna_a_tea(float(cambios["tna_semianual"])), hoy)
            else:
                continue
            st.session_state[fras_clean_key][n] = clean_original

    st.data_editor(
        input_df,
        use_container_width=True,
        hide_index=True,
        disabled=["bono", "dias_vto"],
        column_config={
            "bono": st.column_config.TextColumn("BONO"),
            "dias_vto": st.column_config.TextColumn("DÍAS AL VTO"),
            "precio_clean": st.column_config.NumberColumn("PRECIO CLEAN", format=f"%.{DEC}f"),
            "precio_dirty": st.column_config.NumberColumn("PRECIO DIRTY", format=f"%.{DEC}f"),
            "tea": st.column_config.NumberColumn("TEA %", format=f"%.{DEC}f"),
            "tna_semianual": st.column_config.NumberColumn("TNA SEMIANUAL %", format=f"%.{DEC}f"),
        },
        key=fras_editor_key,
        on_change=_fras_on_edit,
    )

    nombres = nombres_orden_fras
    codigos = dict(zip(curva["nombre"], curva["codigo"]))
    dias_por_bono = {n: int(curva[curva["nombre"] == n]["dias_vto"].iloc[0]) for n in nombres}
    anios_al_vto = {n: dias_por_bono[n] / 365 for n in nombres}

    yield_tea = {n: estados_fras[n]["tea"] for n in nombres}
    yield_tna = {n: tea_a_tna(yield_tea[n]) for n in nombres}

    etiquetas = [codigos[n] for n in nombres]
    t_por_nodo = anios_al_vto

    TASAS_BASE = {"TEA": yield_tea, "TNA Semianual": yield_tna}

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
            "Tasa de base", list(TASAS_BASE.keys()), horizontal=True, index=1, key=f"fras_base_b_{fra_key_suffix}",
        )
        _mostrar_matriz(*armar_matriz(TASAS_BASE[base_b], forward_simple))
