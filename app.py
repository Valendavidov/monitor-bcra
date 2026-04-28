import streamlit as st
import requests
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import xlrd
import openpyxl
import io
from datetime import date, timedelta

TARGET_BM = 44_000_000  # MM ARS

st.set_page_config(page_title="Monitor de Liquidez - BCRA", layout="wide")
st.title("Monitor de Liquidez - BCRA")

VARIABLES_CUADRO = {
    15:  {"titulo": "Base Monetaria",        "unidad": "MM ARS"},
    17:  {"titulo": "Circulante",            "unidad": "MM ARS"},
    117: {"titulo": "Crédito en ARS",        "unidad": "MM ARS"},
    125: {"titulo": "Crédito en USD",        "unidad": "MM USD"},
    100: {"titulo": "Depósitos privados ARS","unidad": "MM ARS"},
    108: {"titulo": "Depósitos en USD",      "unidad": "MM USD"},
    197: {"titulo": "M2 Transaccional",      "unidad": "MM ARS"},
    5:   {"titulo": "A3500",                 "unidad": "ARS/USD"},
}

# ── Funciones de API del BCRA ────────────────────────────────────────────────
@st.cache_data(ttl=3600)
def fetch_serie(id_variable, desde, hasta):
    url = f"https://api.bcra.gob.ar/estadisticas/v4.0/monetarias/{id_variable}?desde={desde}&hasta={hasta}"
    resp = requests.get(url, verify=False)
    if resp.status_code == 200:
        resultados = resp.json().get("results", [])
        if resultados and resultados[0].get("detalle"):
            df = pd.DataFrame(resultados[0]["detalle"])
            df["fecha"] = pd.to_datetime(df["fecha"])
            return df
    return pd.DataFrame()

def fetch_ultimo_valor(id_variable, hasta):
    desde = (pd.Timestamp(hasta) - pd.Timedelta(days=30)).strftime("%Y-%m-%d")
    df = fetch_serie(id_variable, desde, hasta)
    if not df.empty:
        ultima = df.iloc[-1]
        return ultima["valor"], ultima["fecha"].strftime("%Y-%m-%d")
    return None, None

# ── Funciones de Excel del BCRA ──────────────────────────────────────────────
@st.cache_data(ttl=86400)
def load_depositos_tesoro():
    """Descarga diar_bas.xls y extrae depósitos del gobierno en moneda nacional (col AF)."""
    url = "https://www.bcra.gob.ar/archivos/Pdfs/PublicacionesEstadisticas/diar_bas.xls"
    resp = requests.get(url, verify=False)
    wb = xlrd.open_workbook(file_contents=resp.content)
    ws = wb.sheet_by_name('Serie_diaria')
    data = []
    for i in range(6, ws.nrows):
        row = ws.row_values(i)
        if row[0] and isinstance(row[0], (int, float)) and row[0] > 10000:
            try:
                fecha = xlrd.xldate_as_datetime(float(row[0]), wb.datemode)
                valor = row[31]  # columna AF
                if valor and isinstance(valor, (int, float)) and valor > 0:
                    data.append({"fecha": pd.Timestamp(fecha), "valor": valor})
            except Exception:
                pass
    return pd.DataFrame(data)

@st.cache_data(ttl=86400)
def load_otros_instrumentos():
    """Descarga series.xlsm y extrae Otros (13) de INSTRUMENTOS DEL BCRA (col D)."""
    url = "https://www.bcra.gob.ar/archivos/Pdfs/PublicacionesEstadisticas/series.xlsm"
    resp = requests.get(url, verify=False)
    wb = openpyxl.load_workbook(io.BytesIO(resp.content), read_only=True, data_only=True)
    ws = wb['INSTRUMENTOS DEL BCRA']
    data = []
    for row in ws.iter_rows(min_row=10, values_only=True):
        fecha = row[0]
        valor = row[3]
        if fecha and valor and isinstance(valor, (int, float)):
            data.append({"fecha": pd.Timestamp(fecha), "valor": valor})
    return pd.DataFrame(data)

# ── Sección 1: cuadros de último valor ──────────────────────────────────────
st.header("Último valor disponible")
fecha_ref = st.date_input("Fecha de referencia", value=date.today())
fecha_ref_str = fecha_ref.strftime("%Y-%m-%d")

cols = st.columns(4)
for i, (id_var, meta) in enumerate(VARIABLES_CUADRO.items()):
    valor, fecha_dato = fetch_ultimo_valor(id_var, fecha_ref_str)
    with cols[i % 4]:
        if valor is not None:
            texto = f"${valor:,.2f}" if meta["unidad"] == "ARS/USD" else f"{valor:,.0f}"
            st.metric(
                label=f"{meta['titulo']} ({meta['unidad']})",
                value=texto,
                help=f"Dato al {fecha_dato}"
            )
        else:
            st.metric(label=f"{meta['titulo']} ({meta['unidad']})", value="Sin datos")

st.divider()

# ── Sección 2: BM + Simultáneas vs Target ───────────────────────────────────
st.header("Base Monetaria + Simultáneas vs Target")

col_desde2, col_hasta2 = st.columns(2)
with col_desde2:
    bm_desde = st.date_input("Desde", value=date.today() - timedelta(days=120), key="bmd")
with col_hasta2:
    bm_hasta = st.date_input("Hasta", value=date.today(), key="bmh")

bmd = bm_desde.strftime("%Y-%m-%d")
bmh = bm_hasta.strftime("%Y-%m-%d")

with st.spinner("Cargando datos..."):
    df_bm2    = fetch_serie(15,  bmd, bmh).set_index("fecha")["valor"].rename("BM")
    df_sim2   = fetch_serie(198, bmd, bmh).set_index("fecha")["valor"].rename("Sim")
    df_base   = pd.concat([df_bm2, df_sim2], axis=1).sort_index().dropna()
    df_base["BM_Sim"]    = df_base["BM"] + df_base["Sim"]
    df_base["BM_Sim_7d"] = df_base["BM_Sim"].rolling(7).mean()
    df_base["cambio_sim"]= df_base["Sim"].diff()

def grafico_bm_sim(titulo, usar_7d):
    fig = make_subplots(specs=[[{"secondary_y": True}]])

    serie_linea = "BM_Sim_7d" if usar_7d else "BM_Sim"
    label_linea = "BM + Simultáneas (7d avg)" if usar_7d else "BM + Simultáneas"

    # Barras de cambio diario simultáneas (eje derecho)
    colores_barra = ["#5cb85c" if v >= 0 else "#d9534f" for v in df_base["cambio_sim"]]
    fig.add_trace(
        go.Bar(x=df_base.index, y=df_base["cambio_sim"],
               name="Cambio stock simultáneas", marker_color=colores_barra,
               opacity=0.7),
        secondary_y=True
    )

    # Línea BM + Simultáneas (eje izquierdo)
    fig.add_trace(
        go.Scatter(x=df_base.index, y=df_base[serie_linea],
                   name=label_linea, line=dict(color="#1f77b4", width=2)),
        secondary_y=False
    )

    # Línea target
    fig.add_hline(y=TARGET_BM, line_color="red", line_width=2,
                  annotation_text="Target 44MM", annotation_position="top left",
                  secondary_y=False)

    fig.update_layout(
        title=dict(text=titulo, x=0, xanchor="left", font=dict(size=14)),
        hovermode="x unified",
        height=450,
        margin=dict(l=10, r=60, t=80, b=60),
        legend=dict(orientation="h", yanchor="top", y=-0.12, xanchor="left", x=0),
        barmode="relative",
    )
    fig.update_yaxes(
        tickformat=",.0f", title_text="MM ARS", secondary_y=False
    )
    fig.update_yaxes(
        tickformat=",.0f", title_text="Cambio MM ARS", secondary_y=True
    )
    st.plotly_chart(fig, use_container_width=True)

col_g1, col_g2 = st.columns(2)
with col_g1:
    grafico_bm_sim("BM + Simultáneas (7-day avg) vs Target", usar_7d=True)
with col_g2:
    grafico_bm_sim("BM + Simultáneas (diario) vs Target", usar_7d=False)

st.divider()

# ── Sección 3: gráficos históricos ──────────────────────────────────────────
st.header("Evolución histórica")

col_desde, col_hasta = st.columns(2)
with col_desde:
    graf_desde = st.date_input("Desde", value=date.today() - timedelta(days=365), key="gd")
with col_hasta:
    graf_hasta = st.date_input("Hasta", value=date.today(),  key="gh")

gd = graf_desde.strftime("%Y-%m-%d")
gh = graf_hasta.strftime("%Y-%m-%d")
COLORES = ["#00C9FF", "#F0A500", "#00E676", "#FF4C4C", "#B388FF", "#FF80AB", "#69F0AE"]

def grafico_api(titulo, series):
    fig = go.Figure()
    for id_var, nombre, color in series:
        df = fetch_serie(id_var, gd, gh)
        if not df.empty:
            fig.add_trace(go.Scatter(
                x=df["fecha"], y=df["valor"],
                name=nombre, line=dict(color=color, width=2)
            ))
    fig.update_layout(
        title=titulo, hovermode="x unified", height=350,
        margin=dict(l=0, r=0, t=40, b=0),
        legend=dict(orientation="h", yanchor="bottom", y=1.02)
    )
    st.plotly_chart(fig, use_container_width=True)

col1, col2 = st.columns(2)
with col1:
    grafico_api("Base Monetaria (MM ARS)", [(15, "Base Monetaria", COLORES[0])])
with col2:
    grafico_api("M2 Transaccional Privado (MM ARS)", [(197, "M2 Transaccional", COLORES[1])])

# CER compartido para los tres gráficos reales
df_cer = fetch_serie(30, gd, gh).set_index("fecha")["valor"]

def grafico_real(titulo, df_nominal, color, y_label="MM ARS reales", ma30=False):
    df_real = (df_nominal / df_cer).dropna()
    serie   = df_real.rolling(30).mean() if ma30 else df_real
    nombre  = f"{titulo} (30d avg)" if ma30 else titulo
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=serie.index, y=serie.values,
                             name=nombre, line=dict(color=color, width=2)))
    fig.update_layout(title=titulo, hovermode="x unified", height=350,
                      margin=dict(l=0, r=0, t=40, b=0))
    fig.update_yaxes(tickformat=",.0f", title_text=y_label)
    st.plotly_chart(fig, use_container_width=True)

col3, col4 = st.columns(2)
with col3:
    grafico_real("Base Monetaria real (÷CER)",
                 fetch_serie(15, gd, gh).set_index("fecha")["valor"],
                 COLORES[0])
with col4:
    grafico_real("M2 Transaccional real (÷CER)",
                 fetch_serie(197, gd, gh).set_index("fecha")["valor"],
                 COLORES[1])

col5, _ = st.columns(2)
with col5:
    grafico_real("Crédito ARS real (÷CER) — promedio móvil 30 días",
                 fetch_serie(117, gd, gh).set_index("fecha")["valor"],
                 COLORES[2], ma30=True)

