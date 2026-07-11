"""
MONITOR DE BONOS — Paraguay y Uruguay
======================================

App de Streamlit para pricear bonos soberanos (Paraguay Globales/Reg S,
Uruguay Globales y Unidad Indexada). Toda la matematica del bono (precio,
yield, duration, convexidad, paridad) vive en bond_model.py; este archivo
solo arma la interfaz y traduce lo que el usuario tipea en pantalla a
llamadas a esa libreria.

Como esta organizado el archivo (de arriba hacia abajo):
    1. Configuracion general: paises, colores, decimales, settlement.
    2. CSS: la identidad visual (paleta por pais, mayusculas, fuente).
    3. Funciones auxiliares: cargar/guardar el universo de bonos, armar un
       objeto Bond a partir de una fila de la tabla, filtrar por categoria.
    4. Tres tabs, cada uno una seccion independiente:
         - Cashflows: ver el calendario de pagos futuros de un bono.
         - YAS (estilo Bloomberg): pricear UN bono a la vez, precio<->yield.
         - Monitor de bonos: editar el universo completo y comparar todos
           los bonos juntos con precios/yields bid y offer.

Nota sobre Streamlit para quien no lo conozca: Streamlit no funciona como
una pagina web tradicional. Cada vez que el usuario toca un control (tipea
un numero, mueve un radio button, edita una celda), Streamlit vuelve a
correr TODO el archivo de arriba a abajo. Por eso usamos `st.session_state`
para que ciertos valores (como los precios bid/offer que el usuario va
editando) sobrevivan entre una corrida y la siguiente, en vez de perderse.

Uso:
    streamlit run bonos_pyg_app.py
"""

import json
import os
import sys
from datetime import date, timedelta

import pandas as pd
import streamlit as st

# Streamlit Cloud a veces ejecuta el script con un directorio de trabajo
# distinto al de este archivo, y ahi el "from bond_model import Bond" de
# abajo fallaria (no encontraria el modulo). Agregar a mano la carpeta de
# este archivo al sys.path lo soluciona sin importar desde donde se lance.
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from bond_model import Bond

# Aca se guarda, por bono, el ultimo yield que el usuario tipeo en la tab
# YAS - asi la proxima vez que abre la app aparece ese valor en vez de
# siempre 6.5. (Nota: en Streamlit Cloud esto vive en el filesystem del
# contenedor: sobrevive mientras la app siga "despierta", pero se resetea
# si la nube reinicia/redeploya la app, porque ese archivo no se sube a git).
LAST_YIELDS_PATH = os.path.join(BASE_DIR, "yas_ultimos_yields.json")

# Cronograma de calls (rescate anticipado) por bono, compartido entre
# Paraguay y Uruguay (un solo archivo, no por pais). Bonos que no
# aparecen aca se tratan como bullet puro (sin calls).
CALLS_PATH = os.path.join(BASE_DIR, "bonos_calls.csv")

# Cronograma de amortizacion (pago de capital en cuotas antes del
# vencimiento) por bono, tambien compartido entre paises. Bonos que no
# aparecen aca pagan el 100% del capital de golpe al vencimiento.
AMORTIZACION_PATH = os.path.join(BASE_DIR, "bonos_amortizacion.csv")


# =============================================================================
# 1) CONFIGURACION GENERAL
# =============================================================================

DEC = 3  # cantidad de decimales que se muestran en TODA la app (precios, yields, etc.)


def siguiente_dia_habil(d: date) -> date:
    """Si `d` cae sabado o domingo, la corre al lunes siguiente.

    Nota: solo evita fines de semana, no feriados de Paraguay/Uruguay
    (no tenemos cargado un calendario de feriados). El settlement de
    estos bonos tiene que ser un dia habil, asi que la app nunca deja
    calcular con un sabado/domingo - si se elige uno, se usa este
    "proximo dia habil" automaticamente.
    """
    while d.weekday() >= 5:  # 5 = sabado, 6 = domingo
        d += timedelta(days=1)
    return d


# T+1: la fecha de liquidacion por defecto es "mañana" (y si mañana cae
# fin de semana, el proximo dia habil). Estos bonos se operan asi
# habitualmente, asi que evita que el usuario tenga que cambiar la fecha
# cada vez que abre la app.
SETTLEMENT_DEFAULT = siguiente_dia_habil(date.today() + timedelta(days=1))


def fmt_es(x: float, decimales: int = DEC) -> str:
    """Formatea un numero al estilo español/latinoamericano: punto como
    separador de miles, coma como separador decimal (ej. 1.234.567,891).

    Python solo sabe formatear al reves (coma miles, punto decimal), asi
    que se arma con el formato "de siempre" y despues se intercambian los
    dos simbolos. Se usa en todo texto que dibujamos nosotros mismos
    (metricas, la grilla de YAS, la conversion de moneda). Los inputs
    numericos (st.number_input) y las columnas EDITABLES de las tablas se
    dejan con punto decimal de fabrica, porque cambiarles el formato ahi
    podria romper la edicion (el navegador espera tipear con punto).
    """
    texto = f"{x:,.{decimales}f}"
    return texto.replace(",", "\x00").replace(".", ",").replace("\x00", ".")

# Un diccionario por pais con todo lo que cambia entre uno y otro: de que
# archivo CSV sale el universo de bonos, y la paleta de colores (basada en
# la bandera de cada pais) que se usa en el CSS de mas abajo.
PAISES = {
    "Paraguay": {
        "registry": os.path.join(BASE_DIR, "bonos_universo_py.csv"),
        "primary": "#0038A8",   # azul de la bandera paraguaya
        "accent": "#D52B1E",    # rojo de la bandera paraguaya
        "flag": ["#0038A8", "#F5F6F7", "#D52B1E"],
        "moneda": "PYG",        # guarani
    },
    "Uruguay": {
        "registry": os.path.join(BASE_DIR, "bonos_universo_uy.csv"),
        "primary": "#75AADB",   # celeste de la bandera uruguaya
        "accent": "#FCD116",    # dorado del sol de mayo
        "flag": ["#75AADB", "#F5F6F7", "#75AADB"],
        "moneda": "UYU",        # peso uruguayo
    },
}

st.set_page_config(page_title="Monitor de Bonos Soberanos", layout="wide")

# Selector de pais: es lo primero que se dibuja en la pagina. Determina que
# archivo CSV se carga y que paleta de colores se usa mas abajo.
pais = st.radio("País", list(PAISES.keys()), horizontal=True, key="pais_selector")
cfg = PAISES[pais]
PRIMARY = cfg["primary"]
ACCENT = cfg["accent"]
MONEDA = cfg["moneda"]


# =============================================================================
# 2) CSS: identidad visual (paleta por pais + todo en mayusculas)
# =============================================================================
# Streamlit no permite cambiar la tipografia/colores de los titulos, tabs,
# labels, etc. desde Python "normal" - hay que inyectar CSS a mano con
# st.markdown(..., unsafe_allow_html=True). Esto es lo unico "raro" de
# leer en el archivo; el resto es Python comun.
#
# OJO: las tablas editables (st.data_editor / st.dataframe mas abajo) se
# dibujan con un componente que renderiza en un <canvas>, no con HTML
# normal. Por eso el CSS de aca ABAJO no les cambia el texto de los
# encabezados de columna a mayuscula - eso se resuelve escribiendo el
# nombre de columna ya en mayuscula directamente en el column_config de
# cada tabla (lo vas a ver mas abajo, ej. "NOMBRE", "ISIN", etc.).
st.markdown(
    f"""
    <style>
    .stApp {{ background-color: #0E1116; }}
    html, body, [class*="css"] {{
        font-family: -apple-system, "Segoe UI", Helvetica, Arial, sans-serif;
    }}
    /* Todo el texto "de interfaz" (titulos, labels, tabs, botones, captions)
       se muestra en mayuscula, aunque en el codigo este escrito normal. */
    h1, h2, h3, h4, label, .stTabs button p, .stButton button p,
    .stDownloadButton button p, [data-testid="stCaptionContainer"],
    [data-testid="stMetricLabel"], .yas-label, .stRadio label p {{
        text-transform: uppercase !important;
    }}
    h1, h2, h3 {{ color: #F5F6F7 !important; font-weight: 600; letter-spacing: 0.2px; }}
    /* Franja de 3 colores (bandera del pais elegido) debajo del titulo */
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
    /* .yas-label / .yas-value: los "cuadraditos" de numeros grandes de la tab YAS */
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


# =============================================================================
# 3) FUNCIONES AUXILIARES
# =============================================================================
# Estas funciones las usan las tres tabs; por eso estan definidas antes,
# a nivel de modulo, en vez de repetir la logica adentro de cada tab.

def load_registry() -> pd.DataFrame:
    """Lee el universo de bonos del pais elegido desde su CSV.

    Cada fila del CSV es un bono con: nombre, isin, codigo (ticker corto),
    categoria (Global/UI), coupon_pct, maturity, face y freq. Este es el
    "maestro" de bonos disponibles en toda la app.
    """
    df = pd.read_csv(REGISTRY_PATH)
    df["maturity"] = pd.to_datetime(df["maturity"]).dt.date
    df["isin"] = df["isin"].fillna("")  # Uruguay todavia no tiene ISIN cargado
    return df


def cargar_ultimo_yield(nombre_bono: str, default: float = 6.5) -> float:
    """Devuelve el ultimo yield guardado para ese bono, o `default` si
    todavia nunca se tipeo nada para el."""
    if not os.path.exists(LAST_YIELDS_PATH):
        return default
    try:
        with open(LAST_YIELDS_PATH, "r") as f:
            data = json.load(f)
        return float(data.get(nombre_bono, default))
    except (json.JSONDecodeError, ValueError):
        return default


def guardar_ultimo_yield(nombre_bono: str, ytm_pct: float) -> None:
    """Actualiza el yield guardado para ese bono (y deja el resto igual)."""
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


def load_calls() -> dict:
    """Lee bonos_calls.csv y arma {nombre_del_bono: [(fecha, precio), ...]}.

    Si el archivo no existe (o un bono no aparece en el), ese bono se
    trata como bullet puro (sin calls) - ver Bond.calls en bond_model.py.
    """
    if not os.path.exists(CALLS_PATH):
        return {}
    df = pd.read_csv(CALLS_PATH)
    df["call_date"] = pd.to_datetime(df["call_date"]).dt.date
    calls: dict = {}
    for _, row in df.iterrows():
        calls.setdefault(row["nombre"], []).append((row["call_date"], float(row["call_price"])))
    return calls


CALLS = load_calls()  # se lee una sola vez al arrancar la app


def load_amortization() -> dict:
    """Lee bonos_amortizacion.csv y arma {nombre_del_bono: [(fecha, fraccion), ...]}.

    Si el archivo no existe (o un bono no aparece en el), ese bono se
    trata como bullet (paga el 100% del capital al vencimiento) - ver
    Bond.amortization en bond_model.py.
    """
    if not os.path.exists(AMORTIZACION_PATH):
        return {}
    df = pd.read_csv(AMORTIZACION_PATH)
    df["fecha"] = pd.to_datetime(df["fecha"]).dt.date
    amort: dict = {}
    for _, row in df.iterrows():
        amort.setdefault(row["nombre"], []).append((row["fecha"], float(row["fraccion"])))
    return amort


AMORTIZACION = load_amortization()  # se lee una sola vez al arrancar la app


def make_bond(row: pd.Series) -> Bond:
    """Convierte una fila del universo (del CSV o de una tabla editada) en
    un objeto Bond de bond_model.py, listo para pedirle precio/yield/etc.
    Si el bono tiene calls (bonos_calls.csv) o amortizacion
    (bonos_amortizacion.csv) cargados, se los pasa para que el motor
    calcule todo correctamente en vez de asumir un bullet puro."""
    maturity = row["maturity"]
    if not isinstance(maturity, date):
        maturity = pd.to_datetime(maturity).date()
    return Bond(
        coupon_pct=float(row["coupon_pct"]),
        maturity=maturity,
        face=float(row["face"]),
        freq=int(row["freq"]),
        calls=CALLS.get(row["nombre"], []),
        amortization=AMORTIZACION.get(row["nombre"], []),
    )


def filtrar_por_categoria(df: pd.DataFrame, key: str) -> pd.DataFrame:
    """Si el universo tiene mas de una 'categoria' (Uruguay separa sus
    bonos en Global y UI), muestra un filtro para elegir cual mirar. Para
    Paraguay, que solo tiene una categoria, esto no dibuja nada y devuelve
    el dataframe entero sin tocar.
    """
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


# =============================================================================
# TAB 1: CASHFLOWS
# =============================================================================
# La mas simple de las tres: elegis un bono y una fecha de liquidacion, y
# te muestra el calendario completo de pagos futuros (cupones + capital).
# No depende de ningun precio/yield, solo de la fecha.
with tab_cashflow:
    registry_cf = filtrar_por_categoria(registry, key="cat_cf")

    col_sel, col_settle = st.columns([2, 1])
    with col_sel:
        nombre_cf = st.selectbox("Bono", registry_cf["nombre"].tolist(), key="cf_bono")
    with col_settle:
        settlement_cf = st.date_input("Settlement", value=SETTLEMENT_DEFAULT, key="cf_settlement")
    if settlement_cf.weekday() >= 5:
        settlement_cf_habil = siguiente_dia_habil(settlement_cf)
        st.warning(f"{settlement_cf} es fin de semana. Se usa el próximo día hábil: {settlement_cf_habil}.")
        settlement_cf = settlement_cf_habil

    row_cf = registry_cf[registry_cf["nombre"] == nombre_cf].iloc[0]
    bond_cf = make_bond(row_cf)

    # schedule() ubica la fecha de settlement dentro del calendario de
    # cupones: cual fue el ultimo pagado, cual es el proximo, etc.
    prev_coupon, next_coupon, _, period_days, accrued_days, _ = bond_cf.schedule(settlement_cf)
    accrued = bond_cf.accrued_interest(settlement_cf)

    c1, c2, c3 = st.columns(3)
    c1.metric("Cupón anterior", prev_coupon.strftime("%Y-%m-%d"))
    c2.metric("Próximo cupón", next_coupon.strftime("%Y-%m-%d"))
    c3.metric("Interés corrido", fmt_es(accrued))

    st.subheader("Cashflows futuros")
    cf = bond_cf.cashflows(settlement_cf)
    # .rename(columns=str.upper): a diferencia de las tablas EDITABLES de
    # mas abajo, esta es de solo lectura (st.dataframe), asi que alcanza
    # con renombrar las columnas del propio DataFrame a mayuscula y
    # pre-formatear los numeros a mano (punto de miles, coma decimal).
    cf_display = cf.copy()
    for col in ["periodos_semestrales", "cupon", "principal", "flujo_total"]:
        cf_display[col] = cf_display[col].map(fmt_es)
    st.dataframe(cf_display.rename(columns=str.upper), use_container_width=True, hide_index=True)
    st.download_button(
        "Descargar cashflows (CSV)",
        cf.to_csv(index=False).encode("utf-8"),  # el CSV exportado queda con numeros "de siempre"
        file_name=f"cashflows_{nombre_cf.replace(' ', '_')}.csv",
        mime="text/csv",
    )


# =============================================================================
# TAB 2: VALORACION ESTILO YAS (como la pantalla YAS de Bloomberg)
# =============================================================================
# Pricea UN bono a la vez: le das precio limpio O yield, y calcula todo lo
# demas (el otro de los dos, precio sucio, interes corrido, duration,
# convexidad). Ademas, al final, convierte el precio a moneda local usando
# un tipo de cambio que el usuario tipea.
with tab_yas:
    registry_yas = filtrar_por_categoria(registry, key="cat_yas")

    col_inputs, col_grid = st.columns([1, 2])

    with col_inputs:
        nombre_sel = st.selectbox("Bono", registry_yas["nombre"].tolist(), key="yas_bono")
        row_sel = registry_yas[registry_yas["nombre"] == nombre_sel].iloc[0]
        isin_txt = row_sel.get("isin") or "-"
        st.caption(f"ISIN: {isin_txt}  |  Cupón: {row_sel['coupon_pct']}%  |  Vto: {row_sel['maturity']}")

        settlement = st.date_input("Settlement", value=SETTLEMENT_DEFAULT, key="yas_settlement")
        if settlement.weekday() >= 5:
            settlement_habil = siguiente_dia_habil(settlement)
            st.warning(f"{settlement} es fin de semana. Se usa el próximo día hábil: {settlement_habil}.")
            settlement = settlement_habil
        # "Yield" va primero en la lista (y por lo tanto es la opcion por
        # defecto) porque estos bonos se operan/cotizan en tasa, no en precio.
        modo = st.radio("Ingresar por", ["Yield to Worst %", "Precio limpio"], key="yas_modo")

        if modo == "Precio limpio":
            clean_price_in = st.number_input("Precio limpio", value=100.0, step=0.25, format=f"%.{DEC}f", key="yas_price")
            bond = make_bond(row_sel)
            summary = bond.summary(settlement, clean_price=clean_price_in)
        else:
            # El yield que se muestra por defecto es el ultimo que se
            # tipeo para ESTE bono (guardado en yas_ultimos_yields.json),
            # no un 6.5 fijo. Cada vez que se tipea uno nuevo, se
            # actualiza el archivo y queda como default de aca en mas.
            ytm_default = cargar_ultimo_yield(nombre_sel)
            ytm_in = st.number_input(
                "Yield to Worst %", value=ytm_default, step=0.1, format=f"%.{DEC}f",
                key=f"yas_ytm_{nombre_sel}",
            )
            guardar_ultimo_yield(nombre_sel, ytm_in)
            bond = make_bond(row_sel)
            summary = bond.summary(settlement, ytm_pct=ytm_in)

        # Si el bono tiene calls cargados (ver bonos_calls.csv), el
        # "escenario ganador" puede ser un call en vez del vencimiento
        # normal - se lo mostramos al usuario para que sepa a que se
        # esta refiriendo el yield/precio de arriba.
        if bond.calls:
            if summary["es_call"]:
                st.caption(f"Escenario worst: call del {summary['escenario_fecha']} @ {summary['escenario_precio']}")
            else:
                st.caption(f"Escenario worst: vencimiento normal ({summary['escenario_fecha']})")

    with col_grid:
        # summary es el diccionario que devuelve Bond.summary() en
        # bond_model.py: ya viene con todo calculado y redondeado.
        st.markdown("#### Resultado")
        st.markdown('<div class="yas-label">ISIN</div>', unsafe_allow_html=True)
        st.markdown(f'<div class="yas-value">{isin_txt}</div>', unsafe_allow_html=True)
        g1, g2 = st.columns(2)
        with g1:
            st.markdown('<div class="yas-label">YIELD TO WORST %</div>', unsafe_allow_html=True)
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

    st.divider()
    st.markdown("#### Conversión de moneda")
    st.caption(
        f"Ingresá nominales (valor nominal en USD) o un monto en {MONEDA}, más el tipo de "
        "cambio, y calcula lo que falta (incluido el equivalente en USD)."
    )

    # precio_sucio esta cotizado en USD por cada 100 de nominal (valor
    # nominal / face). "Nominales" es cuanto valor nominal en USD tenes o
    # queres comprar (ej. 10.000 = USD 10.000 de nominal, no 10.000 bonos).
    modo_fx = st.radio(
        "Ingresar por", ["Nominales (USD)", f"Monto en {MONEDA}"],
        horizontal=True, key="yas_fx_modo",
    )

    col_fx1, col_fx2 = st.columns(2)
    with col_fx2:
        tipo_cambio = st.number_input(
            f"Tipo de cambio (USD/{MONEDA})", min_value=0.0, value=0.0, step=1.0,
            format="%.4f", key="yas_fx",
        )

    usd_consideracion = None
    nominales = None
    monto_local = None

    if modo_fx == "Nominales (USD)":
        with col_fx1:
            nominales = st.number_input(
                "Nominales (valor nominal, USD)", min_value=0.0, value=100.0, step=100.0,
                format=f"%.{DEC}f", key="yas_nominales",
            )
        # precio_sucio/100 = cuanto USD cuesta cada USD 1 de nominal.
        usd_consideracion = summary["precio_sucio"] / 100 * nominales
        if tipo_cambio > 0:
            monto_local = usd_consideracion * tipo_cambio
    else:
        with col_fx1:
            monto_local = st.number_input(
                f"Monto en {MONEDA}", min_value=0.0, value=0.0, step=1000.0,
                format=f"%.{DEC}f", key="yas_monto_local",
            )
        if tipo_cambio > 0:
            usd_consideracion = monto_local / tipo_cambio
            nominales = usd_consideracion / (summary["precio_sucio"] / 100)

    def _valor_o_guion(v):
        return fmt_es(v) if v is not None else "—"

    g_fx1, g_fx2, g_fx3 = st.columns(3)
    with g_fx1:
        st.markdown('<div class="yas-label">NOMINALES (USD)</div>', unsafe_allow_html=True)
        st.markdown(f'<div class="yas-value">{_valor_o_guion(nominales)}</div>', unsafe_allow_html=True)
    with g_fx2:
        st.markdown(f'<div class="yas-label">MONTO EN {MONEDA}</div>', unsafe_allow_html=True)
        st.markdown(f'<div class="yas-value">{_valor_o_guion(monto_local)}</div>', unsafe_allow_html=True)
    with g_fx3:
        st.markdown('<div class="yas-label">USD (CONSIDERACIÓN)</div>', unsafe_allow_html=True)
        st.markdown(f'<div class="yas-value">{_valor_o_guion(usd_consideracion)}</div>', unsafe_allow_html=True)

    if tipo_cambio <= 0:
        st.caption(f"Ingresá el tipo de cambio USD/{MONEDA} para completar la conversión.")
    elif row_sel.get("categoria") == "UI":
        st.caption(
            "La UI tiene su propio factor de conversión oficial contra el UYU; esto es una "
            "aproximación con el tipo de cambio ingresado, no lo reemplaza."
        )


# =============================================================================
# TAB 3: MONITOR DE BONOS (universo editable + comparacion bid/offer)
# =============================================================================
with tab_monitor:
    # El universo de bonos (agregar/editar/borrar) ya no se edita desde la
    # interfaz: se administra directo en los CSV (bonos_universo_py.csv /
    # bonos_universo_uy.csv). Esta tab muestra la comparacion bid/offer de
    # todo ese universo.
    st.subheader("Monitor de bonos")
    st.caption("Editá precio o yield (bid/offer) directo en la tabla y mirá el resto de los campos calculados.")

    monitor_universe = filtrar_por_categoria(registry, key="cat_monitor")

    # st.session_state guarda estos diccionarios {nombre_del_bono: valor}
    # entre una corrida del script y la siguiente. Sin esto, cada vez que
    # el usuario editara una celda, Streamlit volveria a correr el
    # archivo entero y perderiamos lo que habia tipeado antes en las
    # demas filas/columnas.
    px_bid_key = f"mesa_px_bid_{pais}"
    px_offer_key = f"mesa_px_offer_{pais}"
    yld_bid_key = f"mesa_yield_bid_{pais}"
    yld_offer_key = f"mesa_yield_offer_{pais}"
    for k in (px_bid_key, px_offer_key, yld_bid_key, yld_offer_key):
        st.session_state.setdefault(k, {})

    col_modo, col_settle = st.columns([1, 1])
    with col_modo:
        # "Yield" primero: estos bonos se pricean en tasa por convencion de mercado.
        modo_mesa = st.radio("Ingresar por", ["Yield", "Precio"], horizontal=True, key="mesa_modo")
    with col_settle:
        mesa_settlement = st.date_input("Settlement (comparación)", value=SETTLEMENT_DEFAULT, key="mesa_settlement")
    if mesa_settlement.weekday() >= 5:
        mesa_settlement_habil = siguiente_dia_habil(mesa_settlement)
        st.warning(f"{mesa_settlement} es fin de semana. Se usa el próximo día hábil: {mesa_settlement_habil}.")
        mesa_settlement = mesa_settlement_habil

    # Semilla (solo la primera vez que aparece un bono nuevo en la sesion):
    # arrancamos con yield bid 6.50% / offer 6.30%, y calculamos el PRECIO
    # que le corresponde a CADA bono a partir de ese yield (en vez de
    # inventar un precio fijo como 99.5/100.5 que no tendria relacion real
    # con el yield semilla de ese bono en particular).
    for n in monitor_universe["nombre"]:
        if n not in st.session_state[yld_bid_key]:
            bono_seed = monitor_universe[monitor_universe["nombre"] == n].iloc[0]
            b_seed = make_bond(bono_seed)
            st.session_state[yld_bid_key][n] = 6.50
            st.session_state[yld_offer_key][n] = 6.30
            st.session_state[px_bid_key][n] = b_seed.price_to_worst(6.50, mesa_settlement)
            st.session_state[px_offer_key][n] = b_seed.price_to_worst(6.30, mesa_settlement)

    # Armamos la tabla a mostrar leyendo los valores actuales de
    # session_state (lo ultimo que el usuario edito, o la semilla si es
    # la primera vez) y calculando en el momento duration/paridad sobre
    # el precio medio (mid) entre bid y offer.
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

        # yield_bid/offer y px_bid/offer quedan como numeros (uno de los
        # dos pares es editable segun el modo, y las columnas editables
        # necesitan seguir siendo NumberColumn). Los campos que SIEMPRE
        # son de solo lectura (spread, cupon, duration, paridad) se
        # pre-formatean a texto en estilo español (punto miles, coma
        # decimal), porque esos si podemos controlarlos por completo.
        tabla_rows.append({
            "nombre": n,
            "isin": row.get("isin", ""),
            "codigo": row.get("codigo", ""),
            "yield_bid": round(yield_bid, DEC),
            "yield_offer": round(yield_offer, DEC),
            "px_bid": round(px_bid, DEC),
            "px_offer": round(px_offer, DEC),
            "spread_bid_offer_bps": fmt_es((yield_bid - yield_offer) * 100),  # *100: de % a bps
            "maturity": row["maturity"],
            "cupon_pct": fmt_es(row["coupon_pct"]),
            "duracion_modificada": fmt_es(s_mid["duracion_modificada"]),
            "paridad": fmt_es(b.paridad(px_mid, mesa_settlement)),
        })
    tabla_df = pd.DataFrame(tabla_rows)

    # Segun el modo elegido, solo dos columnas quedan editables (precio o
    # yield, bid y offer); el resto se deshabilita porque son "salida"
    # calculada, no algo que el usuario deba tipear.
    columnas_orden = ["nombre", "isin", "codigo", "yield_bid", "yield_offer", "px_bid", "px_offer",
                      "spread_bid_offer_bps", "maturity", "cupon_pct", "duracion_modificada", "paridad"]
    campos_fijos = ["nombre", "isin", "codigo", "spread_bid_offer_bps", "maturity", "cupon_pct",
                    "duracion_modificada", "paridad"]
    if modo_mesa == "Precio":
        disabled_cols = campos_fijos + ["yield_bid", "yield_offer"]
    else:
        disabled_cols = campos_fijos + ["px_bid", "px_offer"]

    tabla_edited = st.data_editor(
        tabla_df[columnas_orden],
        use_container_width=True,
        hide_index=True,
        disabled=disabled_cols,
        column_config={
            "nombre": st.column_config.TextColumn("NOMBRE"),
            "isin": st.column_config.TextColumn("ISIN"),
            "codigo": st.column_config.TextColumn("CÓDIGO"),
            # yield_bid/offer y px_bid/offer son las columnas EDITABLES (una
            # de las dos parejas segun el modo), asi que se quedan como
            # numeros de verdad. "localized" le pide al navegador que las
            # muestre con el formato numerico de su propio idioma.
            "yield_bid": st.column_config.NumberColumn("YIELD BID %", format="localized"),
            "yield_offer": st.column_config.NumberColumn("YIELD OFFER %", format="localized"),
            "px_bid": st.column_config.NumberColumn("PX BID", format="localized"),
            "px_offer": st.column_config.NumberColumn("PX OFFER", format="localized"),
            # Estas cuatro son siempre de solo lectura: ya llegan pre-formateadas
            # como texto (fmt_es), asi que van como TextColumn.
            "spread_bid_offer_bps": st.column_config.TextColumn("SPREAD B/O (BPS)"),
            "maturity": st.column_config.DateColumn("VENCIMIENTO"),
            "cupon_pct": st.column_config.TextColumn("CUPÓN %"),
            "duracion_modificada": st.column_config.TextColumn("MOD. DURATION"),
            "paridad": st.column_config.TextColumn("PARIDAD"),
        },
        key=f"tabla_editor_{pais}_{modo_mesa}",
    )

    # Despues de mostrar la tabla, procesamos lo que el usuario acaba de
    # editar: si edito precio, recalculamos el yield correspondiente (y
    # viceversa), y guardamos AMBOS lados en session_state para que la
    # proxima corrida del script ya muestre todo consistente entre si.
    for _, row in tabla_edited.iterrows():
        n = row["nombre"]
        bono_row = registry[registry["nombre"] == n].iloc[0]
        b = make_bond(bono_row)

        if modo_mesa == "Precio":
            px_bid = float(row["px_bid"])
            px_offer = float(row["px_offer"])
            yield_bid = b.yield_to_worst(px_bid, mesa_settlement)
            yield_offer = b.yield_to_worst(px_offer, mesa_settlement)
        else:
            yield_bid = float(row["yield_bid"])
            yield_offer = float(row["yield_offer"])
            px_bid = b.price_to_worst(yield_bid, mesa_settlement)
            px_offer = b.price_to_worst(yield_offer, mesa_settlement)

        st.session_state[px_bid_key][n] = px_bid
        st.session_state[px_offer_key][n] = px_offer
        st.session_state[yld_bid_key][n] = yield_bid
        st.session_state[yld_offer_key][n] = yield_offer
