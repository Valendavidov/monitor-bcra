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


# =============================================================================
# 1) CONFIGURACION GENERAL
# =============================================================================

DEC = 3  # cantidad de decimales que se muestran en TODA la app (precios, yields, etc.)

# T+1: la fecha de liquidacion por defecto es "mañana". Estos bonos se
# operan asi habitualmente, asi que evita que el usuario tenga que
# cambiar la fecha cada vez que abre la app.
SETTLEMENT_DEFAULT = date.today() + timedelta(days=1)

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


def make_bond(row: pd.Series) -> Bond:
    """Convierte una fila del universo (del CSV o de una tabla editada) en
    un objeto Bond de bond_model.py, listo para pedirle precio/yield/etc."""
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

    row_cf = registry_cf[registry_cf["nombre"] == nombre_cf].iloc[0]
    bond_cf = make_bond(row_cf)

    # schedule() ubica la fecha de settlement dentro del calendario de
    # cupones: cual fue el ultimo pagado, cual es el proximo, etc.
    prev_coupon, next_coupon, _, period_days, accrued_days, _ = bond_cf.schedule(settlement_cf)
    accrued = bond_cf.accrued_interest(settlement_cf)

    c1, c2, c3 = st.columns(3)
    c1.metric("Cupón anterior", prev_coupon.strftime("%Y-%m-%d"))
    c2.metric("Próximo cupón", next_coupon.strftime("%Y-%m-%d"))
    c3.metric("Interés corrido", f"{accrued:.{DEC}f}")

    st.subheader("Cashflows futuros")
    cf = bond_cf.cashflows(settlement_cf)
    # .rename(columns=str.upper): a diferencia de las tablas EDITABLES de
    # mas abajo, esta es de solo lectura (st.dataframe), asi que alcanza
    # con renombrar las columnas del propio DataFrame a mayuscula.
    st.dataframe(cf.rename(columns=str.upper), use_container_width=True, hide_index=True)
    st.download_button(
        "Descargar cashflows (CSV)",
        cf.to_csv(index=False).encode("utf-8"),
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
        # "Yield" va primero en la lista (y por lo tanto es la opcion por
        # defecto) porque estos bonos se operan/cotizan en tasa, no en precio.
        modo = st.radio("Ingresar por", ["Yield (YTM %)", "Precio limpio"], key="yas_modo")

        if modo == "Precio limpio":
            clean_price_in = st.number_input("Precio limpio", value=100.0, step=0.25, format=f"%.{DEC}f", key="yas_price")
            bond = make_bond(row_sel)
            summary = bond.summary(settlement, clean_price=clean_price_in)
        else:
            ytm_in = st.number_input("Yield (YTM %)", value=6.5, step=0.1, format=f"%.{DEC}f", key="yas_ytm")
            bond = make_bond(row_sel)
            summary = bond.summary(settlement, ytm_pct=ytm_in)

    with col_grid:
        # summary es el diccionario que devuelve Bond.summary() en
        # bond_model.py: ya viene con todo calculado y redondeado.
        st.markdown("#### Resultado")
        g1, g2 = st.columns(2)
        with g1:
            st.markdown('<div class="yas-label">YIELD (YTM %)</div>', unsafe_allow_html=True)
            st.markdown(f'<div class="yas-value">{summary["ytm_pct"]:.{DEC}f}</div>', unsafe_allow_html=True)
            st.markdown('<div class="yas-label">PRECIO LIMPIO</div>', unsafe_allow_html=True)
            st.markdown(f'<div class="yas-value">{summary["precio_limpio"]:.{DEC}f}</div>', unsafe_allow_html=True)
            st.markdown('<div class="yas-label">PRECIO SUCIO</div>', unsafe_allow_html=True)
            st.markdown(f'<div class="yas-value">{summary["precio_sucio"]:.{DEC}f}</div>', unsafe_allow_html=True)
            st.markdown('<div class="yas-label">INTERÉS CORRIDO</div>', unsafe_allow_html=True)
            st.markdown(f'<div class="yas-value">{summary["interes_corrido"]:.{DEC}f}</div>', unsafe_allow_html=True)
        with g2:
            st.markdown('<div class="yas-label">DURACIÓN MACAULAY (AÑOS)</div>', unsafe_allow_html=True)
            st.markdown(f'<div class="yas-value">{summary["duracion_macaulay_anios"]:.{DEC}f}</div>', unsafe_allow_html=True)
            st.markdown('<div class="yas-label">DURACIÓN MODIFICADA</div>', unsafe_allow_html=True)
            st.markdown(f'<div class="yas-value">{summary["duracion_modificada"]:.{DEC}f}</div>', unsafe_allow_html=True)
            st.markdown('<div class="yas-label">CONVEXIDAD</div>', unsafe_allow_html=True)
            st.markdown(f'<div class="yas-value">{summary["convexidad"]:.{DEC}f}</div>', unsafe_allow_html=True)
            st.markdown('<div class="yas-label">SETTLEMENT</div>', unsafe_allow_html=True)
            st.markdown(f'<div class="yas-value">{summary["settlement"]}</div>', unsafe_allow_html=True)

    st.divider()
    st.markdown("#### Conversión de moneda")
    # Estos bonos cotizan en USD (precio_sucio esta en USD por 100 nominal).
    # Aca dejamos tipear un tipo de cambio USD/PYG o USD/UYU (segun el
    # pais elegido arriba) para ver a cuanto equivale en moneda local.
    col_fx_in, col_fx_out = st.columns(2)
    with col_fx_in:
        tipo_cambio = st.number_input(
            f"Tipo de cambio (USD/{MONEDA})", min_value=0.0, value=0.0, step=1.0,
            format="%.4f", key="yas_fx",
        )
    if tipo_cambio > 0:
        monto_local = summary["precio_sucio"] * tipo_cambio
        with col_fx_out:
            st.markdown(f'<div class="yas-label">EQUIVALENTE EN {MONEDA} (POR 100 NOMINAL)</div>', unsafe_allow_html=True)
            st.markdown(f'<div class="yas-value">{monto_local:,.{DEC}f}</div>', unsafe_allow_html=True)
        st.caption(
            f"Precio sucio (USD 100 nominal) × tipo de cambio ingresado = {MONEDA}. "
            + ("La UI tiene su propio factor de conversión oficial contra el UYU; esto es una aproximación con el tipo de cambio ingresado, no lo reemplaza." if row_sel.get("categoria") == "UI" else "")
        )
    else:
        with col_fx_out:
            st.caption(f"Ingresá el tipo de cambio USD/{MONEDA} para ver el equivalente en moneda local.")


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
            st.session_state[px_bid_key][n] = b_seed.clean_price(6.50, mesa_settlement)
            st.session_state[px_offer_key][n] = b_seed.clean_price(6.30, mesa_settlement)

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

        tabla_rows.append({
            "nombre": n,
            "isin": row.get("isin", ""),
            "codigo": row.get("codigo", ""),
            "yield_bid": round(yield_bid, DEC),
            "yield_offer": round(yield_offer, DEC),
            "px_bid": round(px_bid, DEC),
            "px_offer": round(px_offer, DEC),
            "spread_bid_offer_bps": round((yield_bid - yield_offer) * 100, DEC),  # *100: de % a bps
            "maturity": row["maturity"],
            "cupon_pct": row["coupon_pct"],
            "duracion_modificada": round(s_mid["duracion_modificada"], DEC),
            "paridad": round(b.paridad(px_mid, mesa_settlement), DEC),
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
            "yield_bid": st.column_config.NumberColumn("YIELD BID %", format=f"%.{DEC}f"),
            "yield_offer": st.column_config.NumberColumn("YIELD OFFER %", format=f"%.{DEC}f"),
            "px_bid": st.column_config.NumberColumn("PX BID", format=f"%.{DEC}f"),
            "px_offer": st.column_config.NumberColumn("PX OFFER", format=f"%.{DEC}f"),
            "spread_bid_offer_bps": st.column_config.NumberColumn("SPREAD B/O (BPS)", format=f"%.{DEC}f"),
            "maturity": st.column_config.DateColumn("VENCIMIENTO"),
            "cupon_pct": st.column_config.NumberColumn("CUPÓN %", format=f"%.{DEC}f"),
            "duracion_modificada": st.column_config.NumberColumn("MOD. DURATION", format=f"%.{DEC}f"),
            "paridad": st.column_config.NumberColumn("PARIDAD", format=f"%.{DEC}f"),
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
            yield_bid = b.yield_from_clean_price(px_bid, mesa_settlement)
            yield_offer = b.yield_from_clean_price(px_offer, mesa_settlement)
        else:
            yield_bid = float(row["yield_bid"])
            yield_offer = float(row["yield_offer"])
            px_bid = b.clean_price(yield_bid, mesa_settlement)
            px_offer = b.clean_price(yield_offer, mesa_settlement)

        st.session_state[px_bid_key][n] = px_bid
        st.session_state[px_offer_key][n] = px_offer
        st.session_state[yld_bid_key][n] = yield_bid
        st.session_state[yld_offer_key][n] = yield_offer
