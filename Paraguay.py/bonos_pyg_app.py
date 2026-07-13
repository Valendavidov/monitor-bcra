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
    4. Cuatro tabs, cada uno una seccion independiente:
         - Cashflows: ver el calendario de pagos futuros de un bono.
         - YAS (estilo Bloomberg): pricear UN bono a la vez, precio<->yield.
         - Monitor de bonos: editar el universo completo y comparar todos
           los bonos juntos con precios/yields bid y offer.
         - FRAs: a partir de los yields spot de toda la curva, calcular las
           tasas forward implicitas entre cada par de vencimientos.

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

import holidays
import pandas as pd
import requests
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
    """Formatea un numero con coma como separador de miles y punto como
    separador decimal (ej. 1,234,567.891) - el formato "de fabrica" de
    Python, asi que no hace falta ningun intercambio de simbolos.

    Se usa en todo texto que dibujamos nosotros mismos (metricas, la
    grilla de YAS, la conversion de moneda). Los inputs numericos
    (st.number_input) y las columnas EDITABLES de las tablas se dejan con
    punto decimal de fabrica sin separador de miles, porque cambiarles el
    formato ahi podria romper la edicion.
    """
    return f"{x:,.{decimales}f}"

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


def _password_ok() -> bool:
    """Muestra un cuadro de contraseña y no deja ver el resto de la app
    hasta que se tipee la correcta. La contraseña vive en `st.secrets`
    (archivo `.streamlit/secrets.toml` local, o la seccion "Secrets" del
    dashboard de Streamlit Cloud) - nunca en el codigo ni en git, para que
    no quede expuesta en el repo publico.

    `st.session_state["password_ok"]` guarda el resultado entre corridas:
    una vez tipeada bien, no hay que volver a tipearla en cada interaccion
    con la app (cada click/edicion hace correr el script de nuevo).
    """
    if st.session_state.get("password_ok"):
        return True

    st.title("Monitor de Bonos Soberanos")
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


# Codigo de pais que espera la libreria "holidays" para cada uno de
# nuestros dos paises (se usa en la tab NDF para el value date).
CODIGO_HOLIDAYS_PAIS = {"Paraguay": "PY", "Uruguay": "UY"}


def sumar_dias_habiles(fecha_inicio: date, n_habiles: int, codigos_paises: list) -> date:
    """Suma n_habiles dias habiles a fecha_inicio, saltando fines de
    semana y los feriados de todos los paises en codigos_paises (la tab
    NDF le pasa el pais elegido + "US", porque el NDF liquida en USD).
    Usa la libreria "holidays" (calendarios oficiales, no hardcodeados)."""
    anios = {fecha_inicio.year, fecha_inicio.year + 1}
    calendario = holidays.country_holidays(codigos_paises[0], years=list(anios))
    for cod in codigos_paises[1:]:
        calendario += holidays.country_holidays(cod, years=list(anios))
    d = fecha_inicio
    restantes = n_habiles
    while restantes > 0:
        d += timedelta(days=1)
        if d.weekday() < 5 and d not in calendario:
            restantes -= 1
    return d


@st.cache_data(ttl=3600)
def obtener_sofr():
    """Trae el SOFR mas reciente publicado por el NY Fed (API publica, no
    necesita API key). Se cachea 1 hora para no golpear la API en cada
    interaccion de la pantalla. Devuelve (tasa_pct, fecha_efectiva) o
    (None, None) si la consulta falla (sin internet, API caida, etc.) -
    en ese caso la tab NDF obliga a usar el override manual."""
    try:
        r = requests.get(
            "https://markets.newyorkfed.org/api/rates/secured/sofr/last/1.json",
            timeout=5,
        )
        r.raise_for_status()
        dato = r.json()["refRates"][0]
        return float(dato["percentRate"]), dato["effectiveDate"]
    except Exception:
        return None, None


def ndf_yield_pct(spot: float, px_futuro: float, sofr_pct: float, dias: int) -> float:
    """yield = (365/(spot*dias)) * [px_futuro*(1+sofr*dias/365) - spot],
    con sofr en decimal - el resultado ya sale en decimal, por eso se
    multiplica x100 antes de devolverlo (para mostrarlo como %)."""
    sofr = sofr_pct / 100
    return (365 / (spot * dias)) * (px_futuro * (1 + sofr * dias / 365) - spot) * 100


def ndf_px_futuro(spot: float, yield_pct: float, sofr_pct: float, dias: int) -> float:
    """px_futuro = [spot*(1+yield*dias/365)] / (1+sofr*dias/365), con
    yield y sofr en decimal."""
    sofr = sofr_pct / 100
    y = yield_pct / 100
    return (spot * (1 + y * dias / 365)) / (1 + sofr * dias / 365)


registry = load_registry()

if registry.empty:
    st.warning("El universo de bonos esta vacio. Anda a la tab 'Monitor de bonos' para cargar uno.")
    st.stop()

tab_cashflow, tab_yas, tab_monitor, tab_fras, tab_ndf = st.tabs(
    ["Cashflows", "YAS", "Monitor de bonos", "FRAs", "NDF"]
)


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
    # pre-formatear los numeros a mano (coma de miles, punto decimal).
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
        f"Ingresá una cantidad (valor nominal en USD) o un monto en {MONEDA}, más el tipo de "
        "cambio, y calcula lo que falta (incluido el equivalente en USD)."
    )

    # precio_sucio esta cotizado en USD por cada 100 de nominal (valor
    # nominal / face). "Cantidad" es cuanto valor nominal en USD tenes o
    # queres comprar (ej. 10.000 = USD 10.000 de nominal, no 10.000 bonos).
    modo_fx = st.radio(
        "Ingresar por", ["Cantidad", f"Monto en {MONEDA}"],
        horizontal=True, key="yas_fx_modo",
    )

    col_fx1, col_fx2 = st.columns(2)
    with col_fx2:
        tipo_cambio = st.number_input(
            f"Tipo de cambio (USD/{MONEDA})", min_value=0.0, value=0.0, step=1.0,
            format="%.4f", key="yas_fx",
        )

    usd_consideracion = None
    cantidad = None
    monto_local = None

    if modo_fx == "Cantidad":
        with col_fx1:
            cantidad = st.number_input(
                "Cantidad (valor nominal, USD)", min_value=0.0, value=100.0, step=100.0,
                format=f"%.{DEC}f", key="yas_nominales",
            )
        # precio_sucio/100 = cuanto USD cuesta cada USD 1 de nominal - el
        # monto en moneda local sale de multiplicar esa consideracion en
        # USD por el tipo de cambio.
        usd_consideracion = summary["precio_sucio"] / 100 * cantidad
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
            cantidad = usd_consideracion / (summary["precio_sucio"] / 100)

    def _valor_o_guion(v):
        return fmt_es(v) if v is not None else "—"

    g_fx1, g_fx2, g_fx3 = st.columns(3)
    with g_fx1:
        st.markdown('<div class="yas-label">CANTIDAD</div>', unsafe_allow_html=True)
        st.markdown(f'<div class="yas-value">{_valor_o_guion(cantidad)}</div>', unsafe_allow_html=True)
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
        # pre-formatean a texto (coma miles, punto decimal), porque esos
        # si podemos controlarlos por completo.
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

    # OJO: NO envolver tabla_df en un pandas.Styler para "apagar" columnas
    # de solo lectura (se probo antes) - Streamlit solo mantiene el estado
    # del editor si el Styler que se le pasa es el MISMO objeto entre
    # corridas; como este se reconstruye en cada corrida, el editor entero
    # se "reiniciaba" de cero, lo que ademas rompe el copy-paste desde
    # Excel. Se prioriza que el copy-paste funcione bien por sobre el
    # resaltado visual de la columna editable.

    # Orden estable de bonos (coincide con el de "monitor_universe"/tabla_df)
    # para mapear el indice de fila que devuelve el editor a un nombre de
    # bono dentro del callback de abajo.
    nombres_orden_mesa = monitor_universe["nombre"].tolist()
    mesa_editor_key = f"tabla_editor_{pais}_{modo_mesa}"

    def _mesa_on_edit():
        """Se dispara ANTES de que el script vuelva a correr, asi que si
        edito precio, el yield correspondiente (y viceversa) ya esta en
        session_state cuando se reconstruye la tabla - sin este callback,
        el otro lado del par (y duration/paridad, que dependen del precio
        medio) quedaban un paso atras: se actualizaban recien en la
        SIGUIENTE edicion, como si hubiera que tipear el valor dos veces."""
        estado = st.session_state.get(mesa_editor_key, {})
        for idx, cambios in estado.get("edited_rows", {}).items():
            n = nombres_orden_mesa[idx]
            bono_row = registry[registry["nombre"] == n].iloc[0]
            b = make_bond(bono_row)
            if modo_mesa == "Precio":
                if "px_bid" in cambios:
                    px_bid = float(cambios["px_bid"])
                    st.session_state[px_bid_key][n] = px_bid
                    st.session_state[yld_bid_key][n] = b.yield_to_worst(px_bid, mesa_settlement)
                if "px_offer" in cambios:
                    px_offer = float(cambios["px_offer"])
                    st.session_state[px_offer_key][n] = px_offer
                    st.session_state[yld_offer_key][n] = b.yield_to_worst(px_offer, mesa_settlement)
            else:
                if "yield_bid" in cambios:
                    yield_bid = float(cambios["yield_bid"])
                    st.session_state[yld_bid_key][n] = yield_bid
                    st.session_state[px_bid_key][n] = b.price_to_worst(yield_bid, mesa_settlement)
                if "yield_offer" in cambios:
                    yield_offer = float(cambios["yield_offer"])
                    st.session_state[yld_offer_key][n] = yield_offer
                    st.session_state[px_offer_key][n] = b.price_to_worst(yield_offer, mesa_settlement)

    st.data_editor(
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
            # numeros de verdad. OJO: NO usar format="localized" aca - es
            # un bug conocido de Streamlit (regresion desde la 1.43): con
            # "localized" el copy-paste de un bloque de celdas de Excel
            # se rompe (solo pega la primera celda, o corrompe los
            # numeros). Con un formato fijo tipo "%.3f" el paste anda bien.
            "yield_bid": st.column_config.NumberColumn("YIELD BID %", format=f"%.{DEC}f"),
            "yield_offer": st.column_config.NumberColumn("YIELD OFFER %", format=f"%.{DEC}f"),
            "px_bid": st.column_config.NumberColumn("PX BID", format=f"%.{DEC}f"),
            "px_offer": st.column_config.NumberColumn("PX OFFER", format=f"%.{DEC}f"),
            # Estas cuatro son siempre de solo lectura: ya llegan pre-formateadas
            # como texto (fmt_es), asi que van como TextColumn.
            "spread_bid_offer_bps": st.column_config.TextColumn("SPREAD B/O (BPS)"),
            "maturity": st.column_config.DateColumn("VENCIMIENTO"),
            "cupon_pct": st.column_config.TextColumn("CUPÓN %"),
            "duracion_modificada": st.column_config.TextColumn("MOD. DURATION"),
            "paridad": st.column_config.TextColumn("PARIDAD"),
        },
        key=mesa_editor_key,
        on_change=_mesa_on_edit,
    )


# =============================================================================
# TAB 4: FRAs (tasas forward implicitas)
# =============================================================================
# A partir de un yield spot semianual por bono (que el usuario tipea), esta
# tab arma dos matrices triangulares de tasas forward: cuanto rendiria, hoy,
# un compromiso a futuro que arranca en el vencimiento del bono "i" y
# termina en el vencimiento del bono "j" (con j posterior a i). Es la misma
# logica que un FRA de tasas: la tasa forward implicita se despeja de la
# curva spot, no se inventa ni se tipea a mano.
#
# Nodo "HOY": se agrega como un vencimiento mas, con 0 dias/0 años. Matematicamente
# la formula de forward con ti=0 da como resultado exactamente el yield spot del
# otro bono (cualquier numero elevado a la 0 es 1), asi que no hace falta un caso
# especial para la primera fila: "HOY" funciona como cualquier otro nodo de la curva.
with tab_fras:
    st.subheader("FRAs — tasas forward implícitas")

    # Para Uruguay hay que elegir UNA curva completa (UI o Globales) - a
    # diferencia de filtrar_por_categoria() de mas arriba, aca no existe
    # una opcion "Todas" porque no tiene sentido mezclar dos curvas
    # distintas en una misma matriz de forwards. Para Paraguay, que solo
    # tiene una categoria, no se muestra ningun selector.
    if "categoria" in registry.columns and registry["categoria"].nunique() > 1:
        categorias_fra = sorted(registry["categoria"].unique().tolist())
        cat_fra = st.radio("Curva", categorias_fra, horizontal=True, key="fras_categoria")
        curva = registry[registry["categoria"] == cat_fra].copy()
        fra_key_suffix = f"{pais}_{cat_fra}"
    else:
        curva = registry.copy()
        fra_key_suffix = pais

    hoy = date.today()
    curva["dias_vto"] = curva["maturity"].apply(lambda m: (m - hoy).days)
    curva = curva.sort_values("dias_vto").reset_index(drop=True)

    # st.session_state guarda el yield semianual tipeado por bono, para que
    # sobreviva entre corridas del script (cada edicion de celda hace
    # correr todo el archivo de nuevo). La primera vez que aparece un bono
    # en esta tab, se arranca con el ultimo yield que se tipeo para el en
    # la tab YAS (mejor punto de partida que un numero fijo arbitrario).
    fras_yield_key = f"fras_yield_{fra_key_suffix}"
    st.session_state.setdefault(fras_yield_key, {})
    for n in curva["nombre"]:
        if n not in st.session_state[fras_yield_key]:
            st.session_state[fras_yield_key][n] = cargar_ultimo_yield(n)

    st.markdown("#### Yields spot")
    input_rows = []
    for _, row in curva.iterrows():
        n = row["nombre"]
        dias = int(row["dias_vto"])
        yld_semi = st.session_state[fras_yield_key][n]
        # TEA (tasa efectiva anual): "yield_semianual" es la tasa NOMINAL
        # compuesta semestralmente, asi que la tasa POR PERIODO (6 meses)
        # es la mitad - de ahi el /2 antes de elevar al cuadrado.
        tea = ((1 + yld_semi / 100 / 2) ** 2 - 1) * 100
        # TNA (tasa nominal anual "simple"): anualiza LINEALMENTE el
        # crecimiento efectivo que da la TEA en los dias propios de ESTE
        # bono (no en 365 dias parejos) - por eso depende de "dias".
        tna = (365 / dias) * ((1 + tea / 100) ** (dias / 365) - 1) * 100
        input_rows.append({
            "bono": n,
            # dias_vto se pre-formatea a texto (coma de miles) porque es
            # de solo lectura: como NumberColumn de fabrica no separa
            # miles, un vencimiento largo (ej. 7305 dias) se veria sin la
            # coma que usa el resto de la app.
            "dias_vto": fmt_es(dias, decimales=0),
            "yield_semianual": round(yld_semi, DEC),
            "yield_anual": round(tea, DEC),
            "tna": round(tna, DEC),
        })
    input_df = pd.DataFrame(input_rows)

    # OJO: NO envolver input_df en un pandas.Styler para "apagar" columnas
    # de solo lectura (se probo antes) - rompe la statefulness del editor
    # (Streamlit lo recrea de cero en cada corrida si el Styler no es el
    # mismo objeto) y con eso el copy-paste desde Excel. Se prioriza que
    # el copy-paste funcione por sobre el resaltado visual.

    # Orden estable de bonos (coincide con el de "curva"/input_df) para
    # poder mapear el indice de fila que devuelve el editor a un nombre de
    # bono dentro del callback de abajo.
    nombres_orden_fras = curva["nombre"].tolist()
    fras_editor_key = f"fras_editor_{fra_key_suffix}"

    def _fras_on_edit():
        """Se dispara ANTES de que el script vuelva a correr (a
        diferencia de leer el valor editado despues de mostrar la tabla),
        asi que el yield recien tipeado ya esta en session_state cuando
        se recalculan TEA/TNA y las matrices - sin este callback, esos
        numeros quedaban un paso atras: aparecian recien en la SIGUIENTE
        edicion, como si hubiera que tipear el valor dos veces."""
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
            # OJO: NO usar format="localized" en yield_semianual (la unica
            # columna editable aca) - rompe el copy-paste de un bloque de
            # celdas de Excel (bug conocido de Streamlit, ver comentario
            # igual en la tabla de Monitor de bonos).
            "yield_semianual": st.column_config.NumberColumn("YIELD SEMIANUAL %", format=f"%.{DEC}f"),
            "yield_anual": st.column_config.NumberColumn("YIELD ANUAL (TEA) %", format=f"%.{DEC}f"),
            "tna": st.column_config.NumberColumn("TNA %", format=f"%.{DEC}f"),
        },
        key=fras_editor_key,
        on_change=_fras_on_edit,
    )

    # A partir de aca se arma la curva de nodos que alimenta las dos
    # matrices: un nodo por bono (bono vs bono, sin agregar "HOY"), en el
    # mismo orden (ascendente por vencimiento) que la tabla de arriba.
    nombres = nombres_orden_fras
    codigos = dict(zip(curva["nombre"], curva["codigo"]))
    dias_por_bono = {n: int(curva[curva["nombre"] == n]["dias_vto"].iloc[0]) for n in nombres}
    anios_al_vto = {n: dias_por_bono[n] / 365 for n in nombres}

    yield_semi = {n: st.session_state[fras_yield_key][n] for n in nombres}
    yield_tea = {n: ((1 + yield_semi[n] / 100 / 2) ** 2 - 1) * 100 for n in nombres}
    yield_tna = {
        n: (365 / dias_por_bono[n]) * ((1 + yield_tea[n] / 100) ** (dias_por_bono[n] / 365) - 1) * 100
        for n in nombres
    }

    nodos = nombres
    etiquetas = [codigos[n] for n in nombres]
    t_por_nodo = anios_al_vto

    # Las tres tasas base que se pueden elegir para alimentar cada matriz.
    TASAS_BASE = {
        "Semi Anual": yield_semi,
        "Anual (TEA)": yield_tea,
        "TNA": yield_tna,
    }

    def forward_compounding(ti: float, ri: float, tj: float, rj: float) -> float:
        """Anual compounding: forward compuesto, con exponente en años
        (dias/365). Tasas en decimal (no %); devuelve decimal."""
        return ((1 + rj) ** tj / (1 + ri) ** ti) ** (1 / (tj - ti)) - 1

    def forward_simple(ti: float, ri: float, tj: float, rj: float) -> float:
        """Simple rate: forward lineal (tipo Act/365 simple), en vez de
        compuesto. Tasas en decimal; devuelve decimal."""
        return ((1 + rj * tj) / (1 + ri * ti) - 1) / (tj - ti)

    def armar_matriz(tasas_pct: dict, formula):
        """tasas_pct: {nodo: tasa en %} - la tasa base elegida para esta
        matriz. formula: forward_compounding o forward_simple. Solo se
        completan las celdas donde el vencimiento de la columna es
        posterior al de la fila (triangular superior, sin diagonal); el
        resto queda vacio.

        Devuelve dos DataFrames del mismo tamaño/indice:
          - texto: lo que se ve en pantalla (numeros ya formateados con
            punto decimal, o "" en las celdas que no aplican). Nunca lleva
            NaN - si se deja un NaN en un DataFrame que despues se muestra
            con st.dataframe, el render se lo come `None` en pantalla en
            vez de dejarlo en blanco, que es justo lo que no queremos.
          - crudo: los mismos numeros SIN formatear (o None), solo para
            calcular el color de cada celda en _mostrar_matriz.
        """
        filas_texto, filas_crudo = [], []
        for i, ni in enumerate(nodos):
            fila_t, fila_c = [], []
            for j, nj in enumerate(nodos):
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

    # Semaforo verde-amarillo-rojo: la tasa mas baja de la matriz queda
    # verde, la mas alta queda roja, y todo lo del medio se interpola. Las
    # celdas que no aplican (j <= i) quedan pintadas de gris solido, no
    # solo con texto oscuro, para que se note de un vistazo que estan
    # deshabilitadas. Las tasas fwd van con punto decimal, igual que el
    # resto de la app (coma de miles / punto decimal).
    _VERDE, _AMARILLO, _ROJO = (46, 204, 113), (241, 196, 15), (231, 76, 60)
    _GRIS_VACIO = "background-color: #1A1E24; color: #3A3F47;"

    def _interp(c1, c2, f):
        return tuple(int(c1[k] + (c2[k] - c1[k]) * f) for k in range(3))

    def _mostrar_matriz(texto: pd.DataFrame, crudo: pd.DataFrame):
        # OJO: pandas convierte los None a NaN solo con construir el
        # DataFrame (columna numerica), asi que hay que chequear con
        # pd.isna() y no con "is None" mas abajo.
        validos = [v for fila in crudo.to_numpy().tolist() for v in fila if not pd.isna(v)]
        lo, hi = (min(validos), max(validos)) if validos else (0.0, 1.0)

        def _color(v):
            if pd.isna(v):
                return _GRIS_VACIO
            frac = 0.5 if hi == lo else min(max((v - lo) / (hi - lo), 0.0), 1.0)
            rgb = _interp(_VERDE, _AMARILLO, frac / 0.5) if frac <= 0.5 else _interp(_AMARILLO, _ROJO, (frac - 0.5) / 0.5)
            return f"background-color: rgb{rgb}; color: #14181F; font-weight: 600;"

        # crudo.map(_color) arma una grilla de estilos CSS del mismo
        # tamaño que "texto"; Styler.apply(axis=None) la aplica celda a
        # celda sobre el DataFrame de texto (que es el que efectivamente
        # se muestra), sin tener que mezclar numeros y strings en una
        # sola tabla.
        estilos = crudo.map(_color)
        st.dataframe(texto.style.apply(lambda _: estilos, axis=None), use_container_width=True)

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


# =============================================================================
# TAB 5: NDF (Non-Deliverable Forwards)
# =============================================================================
# Calcula el yield implicito de un NDF a partir de su precio futuro (o al
# reves), usando el SOFR como tasa de referencia (reemplaza a la LIBOR,
# discontinuada, en la formula clasica de NDF).
with tab_ndf:
    st.subheader("NDF — Non-Deliverable Forwards")

    col_izq, col_der = st.columns([1, 1])

    with col_izq:
        st.markdown("#### Fechas")
        dia_operado = st.date_input("Día operado (trade date)", value=date.today(), key="ndf_trade_date")
        fecha_fixing = st.date_input("Fecha de fixing", value=date.today(), key="ndf_fixing")

        # Value date = fixing + 2 dias habiles, saltando fines de semana Y
        # feriados de EEUU (el NDF liquida en USD) mas los del pais que
        # este elegido arriba en el selector de "País" (Paraguay/Uruguay).
        codigos_feriados = [CODIGO_HOLIDAYS_PAIS[pais], "US"]
        fecha_valuta = sumar_dias_habiles(fecha_fixing, 2, codigos_feriados)
        st.markdown('<div class="yas-label">VALUE DATE</div>', unsafe_allow_html=True)
        st.markdown(f'<div class="yas-value">{fecha_valuta}</div>', unsafe_allow_html=True)
        st.caption(f"Fixing + 2 días hábiles (feriados de {pais} + EEUU).")

        hasta = st.radio("Días al vencimiento hasta", ["Fixing", "Value Date"], horizontal=True, key="ndf_dias_hasta")
        fecha_referencia = fecha_fixing if hasta == "Fixing" else fecha_valuta
        dias = (fecha_referencia - dia_operado).days
        st.markdown('<div class="yas-label">DÍAS AL VENCIMIENTO</div>', unsafe_allow_html=True)
        st.markdown(f'<div class="yas-value">{dias}</div>', unsafe_allow_html=True)

        st.markdown("#### Spot")
        spot = st.number_input("Spot", min_value=0.0, value=0.0, step=1.0, format="%.4f", key="ndf_spot")

    with col_der:
        st.markdown("#### SOFR")
        sofr_api_pct, sofr_fecha = obtener_sofr()

        modo_sofr = st.radio(
            "Fuente", ["Automático (API NY Fed)", "Override manual"], key="ndf_modo_libor",
        )

        if modo_sofr == "Override manual" or sofr_api_pct is None:
            if sofr_api_pct is None:
                st.warning(
                    "No se pudo obtener el SOFR desde la API del NY Fed (revisá la conexión). "
                    "Usá el override manual mientras tanto."
                )
            sofr_pct = st.number_input(
                "SOFR manual (%)", value=0.0, step=0.01, format=f"%.{DEC}f", key="ndf_libor_manual",
            )
        else:
            sofr_pct = sofr_api_pct
            st.markdown('<div class="yas-label">SOFR (' + str(sofr_fecha) + ')</div>', unsafe_allow_html=True)
            st.markdown(f'<div class="yas-value">{fmt_es(sofr_pct)}%</div>', unsafe_allow_html=True)

    st.divider()
    st.markdown("#### Cálculo")

    if spot <= 0 or dias <= 0:
        st.caption("Ingresá un spot y una fecha de fixing/value date válidos (posteriores al día operado) para calcular.")
    else:
        modo_calculo = st.radio(
            "Ingresar por", ["Yield", "Precio futuro"], horizontal=True, key="ndf_modo_calculo",
        )

        col_in, col_out = st.columns(2)
        if modo_calculo == "Yield":
            with col_in:
                yield_in = st.number_input(
                    "Yield (%)", value=0.0, step=0.1, format=f"%.{DEC}f", key="ndf_yield_in",
                )
            px_out = ndf_px_futuro(spot, yield_in, sofr_pct, dias)
            yield_out = yield_in
        else:
            with col_in:
                px_in = st.number_input(
                    "Precio futuro", value=spot, step=0.1, format="%.4f", key="ndf_px_in",
                )
            yield_out = ndf_yield_pct(spot, px_in, sofr_pct, dias)
            px_out = px_in

        g1, g2 = st.columns(2)
        with g1:
            st.markdown('<div class="yas-label">YIELD %</div>', unsafe_allow_html=True)
            st.markdown(f'<div class="yas-value">{fmt_es(yield_out)}%</div>', unsafe_allow_html=True)
        with g2:
            st.markdown('<div class="yas-label">PRECIO FUTURO</div>', unsafe_allow_html=True)
            st.markdown(f'<div class="yas-value">{fmt_es(px_out, decimales=4)}</div>', unsafe_allow_html=True)
