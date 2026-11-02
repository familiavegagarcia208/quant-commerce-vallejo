"""
=========================================================================================
PASO 4: POWER BI ENTERPRISE CANVAS - QUANT-COMMERCE VALLEJO
=========================================================================================
Diseño de Inteligencia de Negocios (BI) en cuadrícula de mosaicos con:
- 10 Segmentadores Generales (Género, Categoría, Color, Rango de Tallas, Multi-Talla,
  Rango de Costo, Rango de Precio, Utilidad/ROI, Tier y Nivel de Stock).
- Lienzo visual con Treemap, Heatmap de Tallas, Curva de Bodega, Cuadrante UCB y Cascada.
- Matriz con formato condicional (Data Bars) y editor rápido de publicación.
=========================================================================================
"""
from __future__ import annotations

import re
import sqlite3
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

RUTA_BD = Path("data/zapateria_vallejo.db")
DIR_CATALOGO = Path("data/catalogo_listo")
PISO_FANTASMA_TALLA = 5

st.set_page_config(
    page_title="Quant-Commerce Vallejo · Power BI Canvas",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ---------------------------------------------------------------------------
# ESTILOS CSS: TEMA POWER BI DARK CANVAS & TILES
# ---------------------------------------------------------------------------
st.markdown(
    """
    <style>
    .block-container {
        padding-top: 0.8rem;
        padding-bottom: 1.5rem;
        padding-left: 1.5rem;
        padding-right: 1.5rem;
    }
    /* Tarjetas KPI estilo Power BI */
    div[data-testid="stMetric"] {
        background: linear-gradient(145deg, #111827 0%, #1f2937 100%);
        border: 1px solid #374151;
        border-top: 4px solid #3b82f6;
        padding: 10px 14px;
        border-radius: 8px;
        box-shadow: 0 4px 10px rgba(0, 0, 0, 0.35);
    }
    div[data-testid="stMetricLabel"] > div {
        color: #9ca3af !important;
        font-size: 0.78rem !important;
        text-transform: uppercase;
        letter-spacing: 0.04em;
        font-weight: 600 !important;
    }
    div[data-testid="stMetricValue"] > div {
        color: #f9fafb !important;
        font-size: 1.45rem !important;
        font-weight: 700 !important;
    }
    /* Encabezados de mosaicos */
    .pbi-tile-header {
        font-size: 0.92rem;
        font-weight: 700;
        color: #e5e7eb;
        text-transform: uppercase;
        letter-spacing: 0.03em;
        border-bottom: 1px solid #374151;
        padding-bottom: 6px;
        margin-bottom: 8px;
    }
    </style>
    """,
    unsafe_allow_html=True,
)


def normalizar_talla_mx(valor_str: str | float) -> str:
    m = re.findall(r"\d+(?:\.\d+)?", str(valor_str))
    if not m:
        return str(valor_str).strip()
    num = float(m[0])
    if 2.0 <= num <= 11.5:
        num += 20.0
    return f"{num:.1f}"


def clasificar_categoria(nombre: str) -> str:
    n = str(nombre).lower()
    if any(k in n for k in ["tenis", "sneaker", "runner", "running", "deportivo", "skate", "gym"]):
        return "Tenis / Deportivo"
    if any(k in n for k in ["bota", "botin", "botín", "industrial", "tactica", "táctica", "hiking", "montaña"]):
        return "Bota / Botín"
    if any(k in n for k in ["sandalia", "huarache", "chancla", "slide", "mule", "pantufla", "descanso"]):
        return "Sandalia / Confort"
    if any(k in n for k in ["escolar", "vestir", "oxford", "derby", "mocasin", "mocasín", "tacon", "tacón", "formal"]):
        return "Formal / Escolar"
    return "Casual / Urbano"


def clasificar_color(nombre: str) -> str:
    n = str(nombre).lower()
    mapa_colores = [
        ("Negro", ["negro", "black", "onix", "ónix", "charcoal"]),
        ("Blanco", ["blanco", "white", "hueso", "ivory", "perla", "crema"]),
        ("Café / Miel", ["cafe", "café", "miel", "camel", "tan", "tabaco", "chocolate", "brown", "arena", "beige", "nude"]),
        ("Azul / Marino", ["azul", "marino", "navy", "blue", "mezclilla", "denim", "celeste"]),
        ("Rojo / Vino", ["rojo", "vino", "tinto", "red", "burgundy", "coral", "guinda"]),
        ("Gris / Plata", ["gris", "plata", "silver", "grey", "gray", "plomo", "oxford"]),
        ("Rosa / Lila", ["rosa", "pink", "fiusha", "fucsia", "lila", "morado", "violeta", "oro rosa"]),
        ("Verde / Olivo", ["verde", "olivo", "militar", "green", "menta", "kaki"]),
    ]
    for etiqueta, palabras in mapa_colores:
        if any(p in n for p in palabras):
            return etiqueta
    return "Multicolor / Clásico"


def clasificar_genero(nombre: str, max_talla: float, min_talla: float) -> str:
    n = str(nombre).lower()
    if any(k in n for k in ["niño", "niña", "infantil", "kids", "junior", "escolar", "bebé", "bebe"]) or (0 < max_talla <= 21.5):
        return "Infantil / Juvenil"
    if any(k in n for k in ["caballero", "hombre", "varon", "varón", "men"]):
        return "Hombre"
    if any(k in n for k in ["dama", "mujer", "women", "tacon", "tacón", "plataforma", "ballet", "zapatilla"]):
        return "Mujer"
    if max_talla >= 27.0 or min_talla >= 25.0:
        return "Hombre"
    if 21.5 <= max_talla <= 26.5:
        return "Mujer"
    return "Unisex"


@st.cache_data(ttl=30, show_spinner=False)
def cargar_modelo_bi() -> tuple[pd.DataFrame, pd.DataFrame]:
    if not RUTA_BD.exists():
        return pd.DataFrame(), pd.DataFrame()

    conn = sqlite3.connect(RUTA_BD, timeout=15)
    df_cand = pd.read_sql_query("SELECT * FROM zapatos_candidatos", conn)
    df_hist = pd.read_sql_query(
        """
        SELECT CAST(id_ps AS TEXT) AS id_ps,
               CAST(talla AS TEXT) AS talla,
               stock_vallejo,
               stock_seguro_publicable,
               fecha
        FROM historial_stock_21_dias
        """,
        conn,
    )
    conn.close()

    if df_cand.empty:
        return pd.DataFrame(), pd.DataFrame()

    df_cand["id_ps"] = df_cand["id_ps"].astype(str)

    cols_num = {
        "costo_socio": 0.0,
        "stock_total_anterior": 0,
        "velocidad_diaria": 0.0,
        "score_ucb": 0.0,
        "tallas_seguras_count": 0,
        "stock_publicable_total": 0,
        "precio_piso_ml": 0.0,
        "precio_ml": 0.0,
        "utilidad_neta_ml": 0.0,
        "fondo_merma_ml": 0.0,
        "iva_a_favor_ml": 0.0,
        "precio_amazon": 0.0,
        "utilidad_neta_amazon": 0.0,
        "precio_tiktok": 0.0,
        "utilidad_neta_tiktok": 0.0,
        "apto_publicar": 0,
    }
    for col, val_def in cols_num.items():
        if col not in df_cand.columns:
            df_cand[col] = val_def
        else:
            df_cand[col] = pd.to_numeric(df_cand[col], errors="coerce").fillna(val_def)

    df_cand["stock_fisico"] = df_cand["stock_total_anterior"].astype(int)
    df_cand["tier"] = df_cand["tier"].fillna("B").astype(str) if "tier" in df_cand.columns else "B"

    # Procesar última lectura por talla
    if not df_hist.empty:
        df_hist["stock_vallejo"] = pd.to_numeric(df_hist["stock_vallejo"], errors="coerce").fillna(0).astype(int)
        if "stock_seguro_publicable" not in df_hist.columns:
            df_hist["stock_seguro_publicable"] = (df_hist["stock_vallejo"] - 6).clip(lower=0)
        else:
            df_hist["stock_seguro_publicable"] = (
                pd.to_numeric(df_hist["stock_seguro_publicable"], errors="coerce").fillna(0).astype(int)
            )
        df_hist["talla_norm"] = df_hist["talla"].apply(normalizar_talla_mx)
        df_tallas = (
            df_hist.sort_values("fecha")
            .drop_duplicates(subset=["id_ps", "talla_norm"], keep="last")
            .copy()
        )
        df_tallas["talla_num"] = pd.to_numeric(df_tallas["talla_norm"], errors="coerce").fillna(24.0)
        rangos = df_tallas.groupby("id_ps")["talla_num"].agg(["min", "max"]).to_dict("index")

        tallas_seg = df_tallas[df_tallas["stock_seguro_publicable"] > 0].copy()
        tallas_seg["etiqueta_t"] = (
            tallas_seg["talla_norm"] + " (" + tallas_seg["stock_seguro_publicable"].astype(str) + ")"
        )
        mapa_detalle_seg = tallas_seg.groupby("id_ps")["etiqueta_t"].apply(", ".join).to_dict()

        tallas_fis = df_tallas[df_tallas["stock_vallejo"] > 0].copy()
        mapa_rango_txt = (
            tallas_fis.groupby("id_ps")["talla_num"]
            .agg(lambda s: f"{s.min():.1f} - {s.max():.1f} MX" if len(s) > 1 else f"{s.min():.1f} MX")
            .to_dict()
        )
    else:
        df_tallas = pd.DataFrame(
            columns=["id_ps", "talla", "talla_norm", "talla_num", "stock_vallejo", "stock_seguro_publicable"]
        )
        rangos = {}
        mapa_detalle_seg = {}
        mapa_rango_txt = {}

    # Clasificación general (Categoría, Género, Color, Rango Tallas)
    generos, categorias, colores, min_tallas, max_tallas = [], [], [], [], []
    for _, row in df_cand.iterrows():
        sku_s = str(row["id_ps"])
        nom_s = str(row.get("nombre_modelo", ""))
        r_info = rangos.get(sku_s, {"min": 22.0, "max": 26.0})
        min_tallas.append(float(r_info["min"]))
        max_tallas.append(float(r_info["max"]))

        g_db = row.get("genero") if "genero" in df_cand.columns else None
        c_db = row.get("tipo_calzado") if "tipo_calzado" in df_cand.columns else None

        generos.append(
            g_db if pd.notna(g_db) and str(g_db).strip() else clasificar_genero(nom_s, r_info["max"], r_info["min"])
        )
        categorias.append(c_db if pd.notna(c_db) and str(c_db).strip() else clasificar_categoria(nom_s))
        colores.append(clasificar_color(nom_s))

    df_cand["genero"] = generos
    df_cand["categoria"] = categorias
    df_cand["color"] = colores
    df_cand["talla_min"] = min_tallas
    df_cand["talla_max"] = max_tallas
    df_cand["curva_tallas_txt"] = df_cand["id_ps"].map(lambda x: mapa_rango_txt.get(str(x), "S/D"))
    df_cand["tallas_seguras_txt"] = df_cand["id_ps"].map(lambda x: mapa_detalle_seg.get(str(x), "Ninguna"))

    # Métricas Financieras de BI
    df_cand["utilidad_potencial_lote"] = df_cand["utilidad_neta_ml"] * df_cand["stock_publicable_total"]
    df_cand["capital_requerido_lote"] = df_cand["costo_socio"] * df_cand["stock_publicable_total"]
    df_cand["ventas_brutas_lote"] = df_cand["precio_ml"] * df_cand["stock_publicable_total"]
    df_cand["margen_neto_pct"] = (
        (df_cand["utilidad_neta_ml"] / df_cand["precio_ml"].replace(0, pd.NA)) * 100
    ).fillna(0.0).round(1)
    df_cand["roi_costo_pct"] = (
        (df_cand["utilidad_neta_ml"] / df_cand["costo_socio"].replace(0, pd.NA)) * 100
    ).fillna(0.0).round(1)
    df_cand["fotos_listas"] = df_cand["id_ps"].apply(
        lambda x: (DIR_CATALOGO / str(x) / "03_ficha_beneficios.jpg").exists()
    )
    df_cand["apto_bool"] = df_cand["apto_publicar"].astype(int) == 1

    # Campo unificado de búsqueda para el filtro multitérmino
    df_cand["blob_busqueda"] = (
        df_cand["id_ps"]
        + " "
        + df_cand["nombre_modelo"].astype(str)
        + " "
        + df_cand["genero"]
        + " "
        + df_cand["categoria"]
        + " "
        + df_cand["color"]
        + " "
        + df_cand["tier"]
        + " "
        + df_cand["tallas_seguras_txt"]
    ).str.lower()

    return df_cand, df_tallas


df_raw, df_tallas = cargar_modelo_bi()

if df_raw.empty:
    st.error("⚠️ No se encontraron datos en `data/zapateria_vallejo.db`.")
    st.stop()

# ===========================================================================
# BARRA LATERAL: PANEL DE SEGMENTADORES MULTIDIMENSIONALES (POWER BI SLICERS)
# ===========================================================================
with st.sidebar:
    st.markdown("### ⚡ Segmentadores Power BI")

    if st.button("🔄 Restablecer Todos los Filtros", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

    # 1. Nivel de Disponibilidad
    slicer_stock_modo = st.selectbox(
        "📦 1. Nivel de Stock:",
        options=[
            "Todos los Modelos Escaneados",
            "Con Stock Físico en Vallejo (> 0)",
            "Con Stock Blindado (>5/talla + Poisson)",
            "Solo Aprobados para ML (Apto = 1)",
        ],
        index=0,
    )

    # 2. Género / Departamento
    lista_gen = sorted(df_raw["genero"].unique().tolist())
    slicer_genero = st.multiselect("👥 2. Género / Departamento:", options=lista_gen, default=lista_gen)

    # 3. Categoría de Calzado
    lista_cat = sorted(df_raw["categoria"].unique().tolist())
    slicer_categoria = st.multiselect("👟 3. Categoría / Estilo:", options=lista_cat, default=lista_cat)

    # 4. Color Detectado
    lista_col = sorted(df_raw["color"].unique().tolist())
    slicer_color = st.multiselect("🎨 4. Color / Tono:", options=lista_col, default=lista_col)

    st.markdown("---")
    st.markdown("#### 📏 5. Segmentador de Tallas (MX)")

    # 5A. Rango de Tallas en CM
    min_t_global = float(df_tallas["talla_num"].min()) if not df_tallas.empty else 15.0
    max_t_global = float(df_tallas["talla_num"].max()) if not df_tallas.empty else 30.0
    if min_t_global >= max_t_global:
        min_t_global, max_t_global = 15.0, 30.0

    rango_tallas_sel = st.slider(
        "Rango de Numeración (CM / MX):",
        min_value=float(min_t_global),
        max_value=float(max_t_global),
        value=(float(min_t_global), float(max_t_global)),
        step=0.5,
    )

    # 5B. Selección de Tallas Exactas (permite elegir una o varias tallas específicas)
    tallas_ordenadas = sorted(
        df_tallas["talla_norm"].dropna().unique().tolist(),
        key=lambda x: float(x) if re.match(r"^\d+(\.\d+)?$", str(x)) else 99.0,
    )
    mapa_etiqueta_talla = {}
    opciones_multitalla = []
    for t_str in tallas_ordenadas:
        try:
            fv = float(t_str)
            alias = f" (#{fv - 20:g})" if 22.0 <= fv <= 31.0 else ""
            lbl = f"{t_str} MX{alias}"
        except Exception:
            lbl = f"{t_str} MX"
        opciones_multitalla.append(lbl)
        mapa_etiqueta_talla[lbl] = t_str

    tallas_exactas_ui = st.multiselect(
        "Filtrar Talla(s) Específica(s):",
        options=opciones_multitalla,
        placeholder="Ej. 27.0 MX (#7), 29.0 MX (#9)...",
    )
    tipo_stock_talla = st.radio(
        "Criterio en la talla seleccionada:",
        options=["Existencia Física en Vallejo (> 0)", "Stock Blindado Seguro (> 5 + Poisson)"],
        index=0,
        horizontal=False,
    )

    st.markdown("---")
    st.markdown("#### 💰 6. Filtros Financieros y Tiers")

    # 6. Tiers
    lista_tiers = sorted(df_raw["tier"].unique().tolist())
    slicer_tiers = st.multiselect("🏆 Tier de Rotación (UCB):", options=lista_tiers, default=lista_tiers)

    # 7. Rango de Costo Socio
    max_costo = float(max(1000.0, df_raw["costo_socio"].max()))
    rango_costo = st.slider(
        "💵 Costo Socio Vallejo ($ MXN):",
        min_value=0.0,
        max_value=max_costo,
        value=(0.0, max_costo),
        step=25.0,
    )

    # 8. Rango de Precio ML
    max_precio = float(max(1500.0, df_raw["precio_ml"].max()))
    rango_precio = st.slider(
        "🏷️ Precio Venta ML ($ MXN):",
        min_value=0.0,
        max_value=max_precio,
        value=(0.0, max_precio),
        step=50.0,
    )

    # 9. Utilidad Neta Mínima y ROI
    max_util = int(max(500.0, df_raw["utilidad_neta_ml"].max()))
    min_utilidad = st.slider("📈 Utilidad Neta Mínima ($/par):", 0, max_util, 0, step=20)

    # 10. Estado de Fotos HD
    slicer_fotos = st.selectbox(
        "📸 Estado Estudio Fotográfico:",
        options=["Todos", "Con Carrusel HD Listo", "Sin Fotos Generadas"],
        index=0,
    )

# ===========================================================================
# CINTA SUPERIOR DE COMANDOS Y BUSCADOR GLOBAL MULTITÉRMNO
# ===========================================================================
col_tit, col_search, col_vista = st.columns([4, 5, 3])

with col_tit:
    st.markdown("## 📊 Quant-Commerce Vallejo · BI")
    st.caption("Power BI Executive Canvas · Datos en tiempo real desde SQLite")

with col_search:
    busqueda_global = st.text_input(
        "🔎 Buscador Multitérmino (combina palabras, SKU, color, categoría o talla):",
        placeholder="Ej: tenis hombre negro 27   |   bota mujer   |   1250365",
    )

with col_vista:
    modo_canvas = st.radio(
        "🖥️ Vista Activa:",
        options=["📊 Lienzo Power BI", "📋 Matriz & Editor SQLite", "📸 Inspector de Producto"],
        horizontal=True,
    )

# ===========================================================================
# MOTOR DE FILTRADO CRUZADO (CROSS-FILTERING ENGINE)
# ===========================================================================
df = df_raw.copy()

# 1. Modo de stock
if slicer_stock_modo == "Con Stock Físico en Vallejo (> 0)":
    df = df[df["stock_fisico"] > 0]
elif slicer_stock_modo == "Con Stock Blindado (>5/talla + Poisson)":
    df = df[df["stock_publicable_total"] > 0]
elif slicer_stock_modo == "Solo Aprobados para ML (Apto = 1)":
    df = df[df["apto_publicar"] == 1]

# 2. Dimensiones categóricas
if slicer_genero:
    df = df[df["genero"].isin(slicer_genero)]
if slicer_categoria:
    df = df[df["categoria"].isin(slicer_categoria)]
if slicer_color:
    df = df[df["color"].isin(slicer_color)]
if slicer_tiers:
    df = df[df["tier"].isin(slicer_tiers)]

# 3. Rangos financieros
df = df[(df["costo_socio"] >= rango_costo[0]) & (df["costo_socio"] <= rango_costo[1])]
df = df[(df["precio_ml"] >= rango_precio[0]) & (df["precio_ml"] <= rango_precio[1])]
df = df[df["utilidad_neta_ml"] >= min_utilidad]

# 4. Estado de Fotos
if slicer_fotos == "Con Carrusel HD Listo":
    df = df[df["fotos_listas"]]
elif slicer_fotos == "Sin Fotos Generadas":
    df = df[~df["fotos_listas"]]

# 5. Filtro por Rango de Tallas y Tallas Exactas
tallas_filtradas = df_tallas[
    (df_tallas["talla_num"] >= rango_tallas_sel[0]) & (df_tallas["talla_num"] <= rango_tallas_sel[1])
].copy()

tallas_exactas_codigos = [mapa_etiqueta_talla[k] for k in tallas_exactas_ui]

# También detectar si escribieron una talla en la barra de búsqueda (ej. "talla 9" o "27.5")
if busqueda_global.strip():
    m_talla_q = re.search(r"(?:talla|del|num|#)\s*(\d{1,2}(?:\.5)?)", busqueda_global.lower())
    if m_talla_q:
        t_conv = normalizar_talla_mx(m_talla_q.group(1))
        if t_conv not in tallas_exactas_codigos:
            tallas_exactas_codigos.append(t_conv)

if tallas_exactas_codigos:
    tallas_filtradas = tallas_filtradas[tallas_filtradas["talla_norm"].isin(tallas_exactas_codigos)]
    if tipo_stock_talla == "Stock Blindado Seguro (> 5 + Poisson)":
        tallas_filtradas = tallas_filtradas[tallas_filtradas["stock_seguro_publicable"] > 0]
    else:
        tallas_filtradas = tallas_filtradas[tallas_filtradas["stock_vallejo"] > 0]

    stk_fis_map = tallas_filtradas.groupby("id_ps")["stock_vallejo"].sum().to_dict()
    stk_seg_map = tallas_filtradas.groupby("id_ps")["stock_seguro_publicable"].sum().to_dict()

    df = df[df["id_ps"].isin(tallas_filtradas["id_ps"].unique())].copy()
    df["stock_fisico_vista"] = df["id_ps"].map(lambda x: int(stk_fis_map.get(str(x), 0)))
    df["stock_seguro_vista"] = df["id_ps"].map(lambda x: int(stk_seg_map.get(str(x), 0)))
else:
    if rango_tallas_sel != (float(min_t_global), float(max_t_global)):
        skus_en_rango = tallas_filtradas[tallas_filtradas["stock_vallejo"] > 0]["id_ps"].unique()
        df = df[df["id_ps"].isin(skus_en_rango)].copy()
    df["stock_fisico_vista"] = df["stock_fisico"]
    df["stock_seguro_vista"] = df["stock_publicable_total"]

# 6. Búsqueda Multitérmino Inteligente (filtra cada palabra escrita contra el blob del producto)
if busqueda_global.strip():
    limpio_q = re.sub(r"(?:talla|del|num|#)\s*\d{1,2}(?:\.5)?", "", busqueda_global.lower()).strip()
    tokens = [t for t in limpio_q.split() if len(t) > 1]
    for tok in tokens:
        # Sinónimos rápidos
        if tok in ["hombre", "caballero", "varon"]:
            df = df[df["genero"] == "Hombre"]
        elif tok in ["mujer", "dama"]:
            df = df[df["genero"] == "Mujer"]
        elif tok in ["niño", "niña", "infantil"]:
            df = df[df["genero"] == "Infantil / Juvenil"]
        else:
            df = df[df["blob_busqueda"].str.contains(re.escape(tok), na=False)]

# ===========================================================================
# FILA 1: TARJETAS KPI EJECUTIVAS (POWER BI HEADER CARDS)
# ===========================================================================
k1, k2, k3, k4, k5, k6 = st.columns(6)

n_modelos = len(df)
n_aptos = int((df["apto_publicar"] == 1).sum())
pares_fis = int(df["stock_fisico_vista"].sum())
pares_seg = int(df["stock_seguro_vista"].sum())
util_lote = float((df["utilidad_neta_ml"] * df["stock_seguro_vista"]).sum())
cap_lote = float((df["costo_socio"] * df["stock_seguro_vista"]).sum())
util_unit_prom = float(df["utilidad_neta_ml"].mean()) if not df.empty else 0.0
roi_prom = float(df["roi_costo_pct"].mean()) if not df.empty else 0.0
margen_prom = float(df["margen_neto_pct"].mean()) if not df.empty else 0.0

k1.metric("📦 Modelos Activos", f"{n_modelos} SKUs", f"{n_aptos} aprobados ML")
k2.metric("🛡️ Pares Blindados", f"{pares_seg:,}", f"De {pares_fis:,} físicos en tienda")
k3.metric("💰 Utilidad Neta Lote", f"${util_lote:,.0f}", f"Prom: ${util_unit_prom:,.0f} / par")
k4.metric("🏦 Capital de Compra", f"${cap_lote:,.0f}", "Si se vende todo el lote")
k5.metric("📈 ROI sobre Costo", f"{roi_prom:.1f}%", f"Margen s/venta: {margen_prom:.1f}%")
k6.metric("📸 Estudio HD Listo", f"{int(df['fotos_listas'].sum())} SKUs", f"{len(tallas_exactas_codigos) or 'Todas'} tallas")

if df.empty:
    st.warning("⚠️ Ningún producto coincide con los filtros actuales. Ajusta los segmentadores de la barra lateral.")
    st.stop()

# ===========================================================================
# VISTA 1: LIENZO EJECUTIVO POWER BI (MOSAICOS INTERACTIVOS)
# ===========================================================================
if modo_canvas == "📊 Lienzo Power BI":
    # --- FILA VISUAL 1: 3 MOSAICOS ---
    c_v1, c_v2, c_v3 = st.columns([4, 4, 4])

    with c_v1:
        with st.container(border=True):
            st.markdown('<div class="pbi-tile-header">🗺️ Mapa de Árbol: Stock y Ganancia por Categoría</div>', unsafe_allow_html=True)
            df_tree = df[df["stock_fisico_vista"] > 0].copy()
            if not df_tree.empty:
                fig_tree = px.treemap(
                    df_tree,
                    path=[px.Constant("Vallejo"), "genero", "categoria", "color"],
                    values="stock_fisico_vista",
                    color="utilidad_neta_ml",
                    color_continuous_scale="Tealgrn",
                    height=320,
                    template="plotly_dark",
                )
                fig_tree.update_layout(
                    margin=dict(l=4, r=4, t=4, b=4),
                    paper_bgcolor="rgba(0,0,0,0)",
                    coloraxis_colorbar=dict(title="Util. $", thickness=10),
                )
                st.plotly_chart(fig_tree, use_container_width=True)
            else:
                st.info("Sin stock físico para mostrar el Treemap.")

    with c_v2:
        with st.container(border=True):
            st.markdown('<div class="pbi-tile-header">📏 Curva de Bodega por Talla (Físico vs Blindado)</div>', unsafe_allow_html=True)
            df_t_sub = df_tallas[df_tallas["id_ps"].isin(df["id_ps"])].copy()
            if tallas_exactas_codigos:
                df_t_sub = df_t_sub[df_t_sub["talla_norm"].isin(tallas_exactas_codigos)]
            if not df_t_sub.empty:
                res_talla = (
                    df_t_sub.groupby("talla_norm", as_index=False)[["stock_vallejo", "stock_seguro_publicable"]]
                    .sum()
                )
                res_talla["t_orden"] = pd.to_numeric(res_talla["talla_norm"], errors="coerce").fillna(99.0)
                res_talla = res_talla.sort_values("t_orden")
                res_talla["Retenido Blindaje"] = (
                    res_talla["stock_vallejo"] - res_talla["stock_seguro_publicable"]
                ).clip(lower=0)

                fig_curva = go.Figure()
                fig_curva.add_trace(
                    go.Bar(
                        x=res_talla["talla_norm"],
                        y=res_talla["stock_seguro_publicable"],
                        name="Seguro Publicable",
                        marker_color="#10b981",
                    )
                )
                fig_curva.add_trace(
                    go.Bar(
                        x=res_talla["talla_norm"],
                        y=res_talla["Retenido Blindaje"],
                        name="Candado (Piso 5 + Poisson)",
                        marker_color="#374151",
                    )
                )
                fig_curva.update_layout(
                    barmode="stack",
                    height=320,
                    template="plotly_dark",
                    paper_bgcolor="rgba(0,0,0,0)",
                    plot_bgcolor="rgba(0,0,0,0)",
                    margin=dict(l=4, r=4, t=10, b=4),
                    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
                    xaxis_title="Talla (MX)",
                    yaxis_title="Total Pares",
                )
                st.plotly_chart(fig_curva, use_container_width=True)
            else:
                st.info("Sin lecturas de tallas para los filtros actuales.")

    with c_v3:
        with st.container(border=True):
            st.markdown('<div class="pbi-tile-header">🎯 Cuadrante Rentabilidad vs Rotación (UCB)</div>', unsafe_allow_html=True)
            fig_sc = px.scatter(
                df,
                x="score_ucb",
                y="utilidad_neta_ml",
                size=df["stock_seguro_vista"].clip(lower=3),
                color="tier",
                hover_name="nombre_modelo",
                hover_data=["id_ps", "categoria", "color", "precio_ml", "stock_seguro_vista"],
                color_discrete_map={"S": "#10b981", "A": "#3b82f6", "B": "#f59e0b", "C": "#6b7280"},
                height=320,
                template="plotly_dark",
            )
            fig_sc.add_hline(y=df["utilidad_neta_ml"].median(), line_dash="dot", line_color="#6b7280")
            fig_sc.add_vline(x=df["score_ucb"].median(), line_dash="dot", line_color="#6b7280")
            fig_sc.update_layout(
                margin=dict(l=4, r=4, t=10, b=4),
                paper_bgcolor="rgba(0,0,0,0)",
                plot_bgcolor="rgba(0,0,0,0)",
                xaxis_title="Velocidad Bayesiana (Score UCB)",
                yaxis_title="Utilidad Neta ($ MXN)",
                legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
            )
            st.plotly_chart(fig_sc, use_container_width=True)

    # --- FILA VISUAL 2: MAPA DE CALOR DE TALLAS (HEATMAP) + TOP RANKING ---
    c_v4, c_v5 = st.columns([7, 5])

    with c_v4:
        with st.container(border=True):
            st.markdown(
                '<div class="pbi-tile-header">🔥 Matriz Mapa de Calor: Disponibilidad por Modelo vs. Talla (Top 15 SKUs)</div>',
                unsafe_allow_html=True,
            )
            top_skus_hm = (
                df.sort_values(["stock_seguro_vista", "stock_fisico_vista"], ascending=False)
                .head(15)["id_ps"]
                .tolist()
            )
            df_hm_src = df_tallas[df_tallas["id_ps"].isin(top_skus_hm)].copy()
            if not df_hm_src.empty:
                col_metrica_hm = (
                    "stock_seguro_publicable"
                    if tipo_stock_talla == "Stock Blindado Seguro (> 5 + Poisson)"
                    else "stock_vallejo"
                )
                mapa_nom_corto = (
                    df.set_index("id_ps")["nombre_modelo"]
                    .apply(lambda s: str(s)[:22])
                    .to_dict()
                )
                df_hm_src["modelo_etiqueta"] = (
                    df_hm_src["id_ps"] + " · " + df_hm_src["id_ps"].map(mapa_nom_corto)
                )
                piv_hm = df_hm_src.pivot_table(
                    index="modelo_etiqueta",
                    columns="talla_norm",
                    values=col_metrica_hm,
                    aggfunc="sum",
                    fill_value=0,
                )
                cols_ord = sorted(piv_hm.columns, key=lambda x: float(x) if re.match(r"^\d+(\.\d+)?$", str(x)) else 99.0)
                piv_hm = piv_hm[cols_ord]

                fig_hm = px.imshow(
                    piv_hm,
                    text_auto=True,
                    aspect="auto",
                    color_continuous_scale="Viridis",
                    height=360,
                    template="plotly_dark",
                )
                fig_hm.update_layout(
                    margin=dict(l=4, r=4, t=6, b=4),
                    paper_bgcolor="rgba(0,0,0,0)",
                    xaxis_title="Talla Nacional (MX)",
                    yaxis_title="SKU · Modelo",
                    coloraxis_showscale=False,
                )
                st.plotly_chart(fig_hm, use_container_width=True)
            else:
                st.info("No hay suficientes lecturas de tallas para construir el mapa de calor.")

    with c_v5:
        with st.container(border=True):
            st.markdown(
                '<div class="pbi-tile-header">💰 Estructura de Precios: Costo Vallejo vs. Utilidad Libre (Top 12)</div>',
                unsafe_allow_html=True,
            )
            df_top_f = df.sort_values("utilidad_potencial_lote", ascending=True).tail(12).copy()
            df_top_f["etiq"] = df_top_f["id_ps"] + " · " + df_top_f["nombre_modelo"].str.slice(0, 20)

            fig_combo = go.Figure()
            fig_combo.add_trace(
                go.Bar(
                    y=df_top_f["etiq"],
                    x=df_top_f["costo_socio"],
                    name="Costo Vallejo",
                    orientation="h",
                    marker_color="#64748b",
                )
            )
            fig_combo.add_trace(
                go.Bar(
                    y=df_top_f["etiq"],
                    x=df_top_f["utilidad_neta_ml"],
                    name="Utilidad Neta Libre",
                    orientation="h",
                    marker_color="#10b981",
                )
            )
            fig_combo.update_layout(
                barmode="group",
                height=360,
                template="plotly_dark",
                paper_bgcolor="rgba(0,0,0,0)",
                plot_bgcolor="rgba(0,0,0,0)",
                margin=dict(l=4, r=4, t=10, b=4),
                legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
                xaxis_title="$ MXN por Par",
            )
            st.plotly_chart(fig_combo, use_container_width=True)

    # --- FILA VISUAL 3: MATRIZ POWER BI CON FORMATO CONDICIONAL (DATA BARS) ---
    with st.container(border=True):
        st.markdown(
            '<div class="pbi-tile-header">📋 Matriz Detallada de Catálogo (Con Barras de Datos Condicionales)</div>',
            unsafe_allow_html=True,
        )
        cols_matriz = [
            "id_ps",
            "tier",
            "nombre_modelo",
            "genero",
            "categoria",
            "color",
            "curva_tallas_txt",
            "tallas_seguras_txt",
            "stock_seguro_vista",
            "stock_fisico_vista",
            "costo_socio",
            "precio_ml",
            "utilidad_neta_ml",
            "margen_neto_pct",
            "score_ucb",
        ]
        max_seg_bar = int(max(10, df["stock_seguro_vista"].max()))
        max_fis_bar = int(max(10, df["stock_fisico_vista"].max()))

        st.dataframe(
            df[cols_matriz].sort_values(
                by=["stock_seguro_vista", "utilidad_neta_ml", "score_ucb"],
                ascending=[False, False, False],
            ),
            column_config={
                "id_ps": "SKU",
                "tier": "Tier",
                "nombre_modelo": "Modelo",
                "genero": "Género",
                "categoria": "Categoría",
                "color": "Color",
                "curva_tallas_txt": "Rango Tallas",
                "tallas_seguras_txt": "Tallas Blindadas (Pares)",
                "stock_seguro_vista": st.column_config.ProgressColumn(
                    "Stock Seguro",
                    format="%d pares",
                    min_value=0,
                    max_value=max_seg_bar,
                ),
                "stock_fisico_vista": st.column_config.ProgressColumn(
                    "Físico Tienda",
                    format="%d pares",
                    min_value=0,
                    max_value=max_fis_bar,
                ),
                "costo_socio": st.column_config.NumberColumn("Costo", format="$%.0f"),
                "precio_ml": st.column_config.NumberColumn("Precio ML", format="$%.0f"),
                "utilidad_neta_ml": st.column_config.NumberColumn("Utilidad Libre", format="$%.2f"),
                "margen_neto_pct": st.column_config.ProgressColumn(
                    "Margen %",
                    format="%.1f%%",
                    min_value=0,
                    max_value=40,
                ),
                "score_ucb": st.column_config.NumberColumn("UCB", format="%.2f"),
            },
            use_container_width=True,
            hide_index=True,
            height=380,
        )

# ===========================================================================
# VISTA 2: MATRIZ INTERACTIVA Y EDITOR DIRECTO EN SQLITE
# ===========================================================================
elif modo_canvas == "📋 Matriz & Editor SQLite":
    with st.container(border=True):
        st.markdown(
            '<div class="pbi-tile-header">🎛️ Editor de Publicación y Clasificación General (Guarda directo en SQLite)</div>',
            unsafe_allow_html=True,
        )
        st.caption("Activa o pausa productos (`Publicar ML`) o ajusta el Género y Categoría de cualquier SKU.")

        df_edit = st.data_editor(
            df[
                [
                    "apto_bool",
                    "id_ps",
                    "tier",
                    "nombre_modelo",
                    "genero",
                    "categoria",
                    "color",
                    "costo_socio",
                    "precio_ml",
                    "utilidad_neta_ml",
                    "stock_seguro_vista",
                    "stock_fisico_vista",
                    "tallas_seguras_txt",
                ]
            ].sort_values(["apto_bool", "stock_seguro_vista"], ascending=[False, False]),
            column_config={
                "apto_bool": st.column_config.CheckboxColumn("✅ Publicar ML"),
                "id_ps": st.column_config.TextColumn("SKU", disabled=True),
                "tier": st.column_config.TextColumn("Tier", disabled=True),
                "nombre_modelo": st.column_config.TextColumn("Modelo", disabled=True),
                "genero": st.column_config.SelectboxColumn(
                    "Género",
                    options=["Hombre", "Mujer", "Infantil / Juvenil", "Unisex"],
                    required=True,
                ),
                "categoria": st.column_config.SelectboxColumn(
                    "Categoría",
                    options=[
                        "Tenis / Deportivo",
                        "Casual / Urbano",
                        "Bota / Botín",
                        "Sandalia / Confort",
                        "Formal / Escolar",
                    ],
                    required=True,
                ),
                "color": st.column_config.TextColumn("Color", disabled=True),
                "costo_socio": st.column_config.NumberColumn("Costo", format="$%.0f", disabled=True),
                "precio_ml": st.column_config.NumberColumn("Precio ML", format="$%.0f", disabled=True),
                "utilidad_neta_ml": st.column_config.NumberColumn("Utilidad Neta", format="$%.2f", disabled=True),
                "stock_seguro_vista": st.column_config.NumberColumn("Pares Seguros", disabled=True),
                "stock_fisico_vista": st.column_config.NumberColumn("Físico Vallejo", disabled=True),
                "tallas_seguras_txt": st.column_config.TextColumn("Tallas Blindadas", disabled=True),
            },
            use_container_width=True,
            hide_index=True,
            height=520,
        )

        if st.button("💾 Guardar Cambios en Base de Datos", type="primary"):
            conn = sqlite3.connect(RUTA_BD, timeout=20)
            cols_actuales = [c[1] for c in conn.execute("PRAGMA table_info(zapatos_candidatos)").fetchall()]
            for c_nueva in ("genero", "tipo_calzado"):
                if c_nueva not in cols_actuales:
                    conn.execute(f"ALTER TABLE zapatos_candidatos ADD COLUMN {c_nueva} TEXT;")
            for _, r in df_edit.iterrows():
                conn.execute(
                    """
                    UPDATE zapatos_candidatos
                    SET apto_publicar = ?, genero = ?, tipo_calzado = ?
                    WHERE CAST(id_ps AS TEXT) = ?
                    """,
                    (
                        1 if r["apto_bool"] else 0,
                        str(r["genero"]),
                        str(r["categoria"]),
                        str(r["id_ps"]),
                    ),
                )
            conn.commit()
            conn.close()
            st.cache_data.clear()
            st.success("✅ ¡Cambios guardados en `data/zapateria_vallejo.db`!")
            st.rerun()

# ===========================================================================
# VISTA 3: INSPECTOR DE PRODUCTO (CASCADA FINANCIERA + TALLAS + ESTUDIO HD)
# ===========================================================================
else:
    opciones_insp = (
        df["id_ps"]
        + " · "
        + df["nombre_modelo"]
        + " ("
        + df["stock_seguro_vista"].astype(str)
        + " seguros / "
        + df["stock_fisico_vista"].astype(str)
        + " físicos)"
    ).tolist()

    sel_insp = st.selectbox("🔍 Selecciona un SKU filtrado para auditar sus finanzas, tallas y fotos HD:", opciones_insp)
    sku_i = sel_insp.split(" · ")[0]
    fila_i = df[df["id_ps"] == sku_i].iloc[0]

    col_i1, col_i2 = st.columns([6, 6])

    with col_i1:
        with st.container(border=True):
            st.markdown(f'<div class="pbi-tile-header">💰 Cascada Financiera · SKU {sku_i}</div>', unsafe_allow_html=True)
            precio_v = float(fila_i["precio_ml"])
            costo_s = float(fila_i["costo_socio"])
            comision_ml = round(precio_v * 0.175, 2)
            envio_ml = 98.0 if costo_s < 350 else (112.0 if costo_s < 650 else 128.0)
            ret_sat = round((precio_v / 1.16) * (0.08 + 0.025), 2)
            merma_2 = float(fila_i["fondo_merma_ml"])
            util_neta = float(fila_i["utilidad_neta_ml"])

            fig_wf = go.Figure(
                go.Waterfall(
                    orientation="v",
                    measure=["absolute", "relative", "relative", "relative", "relative", "relative", "total"],
                    x=["Precio ML", "Costo Socio", "Comisión 17.5%", "Envío ML", "SAT", "Fondo 2%", "Utilidad Libre"],
                    text=[
                        f"${precio_v:,.0f}",
                        f"-${costo_s:,.0f}",
                        f"-${comision_ml:,.0f}",
                        f"-${envio_ml:,.0f}",
                        f"-${ret_sat:,.0f}",
                        f"-${merma_2:,.0f}",
                        f"${util_neta:,.2f}",
                    ],
                    y=[precio_v, -costo_s, -comision_ml, -envio_ml, -ret_sat, -merma_2, 0],
                )
            )
            fig_wf.update_layout(
                height=320,
                template="plotly_dark",
                paper_bgcolor="rgba(0,0,0,0)",
                plot_bgcolor="rgba(0,0,0,0)",
                margin=dict(l=4, r=4, t=10, b=4),
            )
            st.plotly_chart(fig_wf, use_container_width=True)

    with col_i2:
        with st.container(border=True):
            st.markdown(f'<div class="pbi-tile-header">📏 Desglose Talla por Talla en Vallejo · SKU {sku_i}</div>', unsafe_allow_html=True)
            tallas_sku = df_tallas[df_tallas["id_ps"] == sku_i].copy()
            if not tallas_sku.empty:
                tallas_sku = tallas_sku.sort_values("talla_num")
                tallas_sku["Retenido (Piso 5 + Poisson)"] = (
                    tallas_sku["stock_vallejo"] - tallas_sku["stock_seguro_publicable"]
                ).clip(lower=0)

                fig_ts = go.Figure()
                fig_ts.add_trace(
                    go.Bar(
                        x=tallas_sku["talla_norm"],
                        y=tallas_sku["stock_seguro_publicable"],
                        name="Pares Publicables ML",
                        marker_color="#10b981",
                    )
                )
                fig_ts.add_trace(
                    go.Bar(
                        x=tallas_sku["talla_norm"],
                        y=tallas_sku["Retenido (Piso 5 + Poisson)"],
                        name="Retenido por Blindaje",
                        marker_color="#4b5563",
                    )
                )
                fig_ts.add_hline(y=PISO_FANTASMA_TALLA, line_dash="dash", line_color="#ef4444", annotation_text="Piso 5 pares")
                fig_ts.update_layout(
                    barmode="stack",
                    height=320,
                    template="plotly_dark",
                    paper_bgcolor="rgba(0,0,0,0)",
                    plot_bgcolor="rgba(0,0,0,0)",
                    margin=dict(l=4, r=4, t=10, b=4),
                    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
                )
                st.plotly_chart(fig_ts, use_container_width=True)

    with st.container(border=True):
        st.markdown(f'<div class="pbi-tile-header">📸 Carrusel Estudio Fotográfico HD (1200x1200) · SKU {sku_i}</div>', unsafe_allow_html=True)
        carpeta_img = DIR_CATALOGO / sku_i
        archivos_img = [
            ("1. Portada Blanca", carpeta_img / "01_portada_blanca.jpg"),
            ("2. Ángulo Detalle", carpeta_img / "02_angulo_galeria.jpg"),
            ("3. Ficha Beneficios", carpeta_img / "03_ficha_beneficios.jpg"),
            ("4. Guía de Tallas (CM)", carpeta_img / "04_guia_tallas_cm.jpg"),
        ]
        cols_img = st.columns(4)
        for idx, (tit_i, ruta_i) in enumerate(archivos_img):
            with cols_img[idx]:
                if ruta_i.exists():
                    st.image(str(ruta_i), caption=tit_i, use_container_width=True)
                else:
                    st.info(f"Sin imagen: {ruta_i.name}")