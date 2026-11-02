"""
=========================================================================================
PASO 4: CENTRO OPERATIVO FAMILIAR "PASO A PASO" - QUANT-COMMERCE VALLEJO
=========================================================================================
Arquitectura centrada en operación Par por Par (sin compras por lote ni jerga compleja):
1. Control de Productos a Subir: Checklist claro de 4 requisitos (Stock >5/talla,
   4/4 Fotos listas, Ganancia Limpia por par y URL + SKU oculto de rastreo).
2. Detector de Ventas (¿Qué se vendió?): Traduce al instante cualquier venta de
   Mercado Libre al ID de Price Shoes, foto, talla y botón directo al URL de tienda.
3. Inspector Unitario: Desglose de cuánto desembolsas por 1 par en Vallejo y cuánto
   te queda libre en la bolsa, junto con sus 4 fotos HD.
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
    page_title="Operación Vallejo · Paso a Paso",
    page_icon="👟",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(
    """
    <style>
    .block-container {
        padding-top: 0.9rem;
        padding-bottom: 1.5rem;
    }
    div[data-testid="stMetric"] {
        background: linear-gradient(145deg, #111827 0%, #1f2937 100%);
        border: 1px solid #374151;
        border-top: 4px solid #10b981;
        padding: 12px 16px;
        border-radius: 10px;
        box-shadow: 0 4px 10px rgba(0, 0, 0, 0.3);
    }
    div[data-testid="stMetricLabel"] > div {
        color: #cbd5e1 !important;
        font-size: 0.82rem !important;
        font-weight: 600 !important;
    }
    div[data-testid="stMetricValue"] > div {
        color: #ffffff !important;
        font-size: 1.45rem !important;
        font-weight: 700 !important;
    }
    .box-header {
        font-size: 0.95rem;
        font-weight: 700;
        color: #f8fafc;
        border-bottom: 1px solid #334155;
        padding-bottom: 6px;
        margin-bottom: 10px;
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
    if any(k in n for k in ["bota", "botin", "botín", "industrial", "tactica", "táctica", "hiking"]):
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
        ("Rosa / Lila", ["rosa", "pink", "fiusha", "fucsia", "lila", "morado", "violeta"]),
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


def contar_fotos_sku(id_str: str) -> tuple[int, bool]:
    carpeta = DIR_CATALOGO / str(id_str)
    esperadas = [
        carpeta / "01_portada_blanca.jpg",
        carpeta / "02_angulo_galeria.jpg",
        carpeta / "03_ficha_beneficios.jpg",
        carpeta / "04_guia_tallas_cm.jpg",
    ]
    existentes = sum(1 for r in esperadas if r.exists())
    return existentes, (existentes == 4)


def construir_url_ps(id_str: str, url_guardada: str | None) -> str:
    if url_guardada and str(url_guardada).strip().startswith("http"):
        return str(url_guardada).strip()
    return f"https://www.priceshoes.com/catalogo/buscar?q={id_str}"


def diagnosticar_producto(
    costo: float,
    utilidad: float,
    tallas_seguras: int,
    pares_seguros: int,
    num_fotos: int,
    stock_fisico: int,
) -> tuple[str, str]:
    """
    Devuelve (Estado Corto, Explicación clara de por qué SÍ o por qué NO se puede subir).
    """
    faltantes = []
    if costo <= 0:
        faltantes.append("Sin costo de socio registrado")
    if stock_fisico <= 0:
        faltantes.append("Agotado en Vallejo")
    elif tallas_seguras < 2 or pares_seguros < 6:
        faltantes.append(
            f"Stock bajo el blindaje (tiene {tallas_seguras} tallas >5 pares y {pares_seguros} pares libres; se piden mín. 2 tallas y 6 pares)"
        )
    if utilidad <= 0:
        faltantes.append("No deja ganancia libre tras comisiones")
    if num_fotos < 4:
        faltantes.append(f"Faltan fotos ({num_fotos}/4 generadas)")

    if not faltantes:
        return (
            "✅ LISTO PARA SUBIR",
            f"Cumple todo: {tallas_seguras} tallas blindadas ({pares_seguros} pares libres), 4/4 fotos HD y deja ${utilidad:,.0f} libres por par.",
        )
    return ("⚠️ NO SUBIR AÚN", " · ".join(faltantes))


@st.cache_data(ttl=20, show_spinner=False)
def cargar_datos_operativos() -> tuple[pd.DataFrame, pd.DataFrame, int]:
    if not RUTA_BD.exists():
        return pd.DataFrame(), pd.DataFrame(), 0

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

    total_crudos_bd = len(df_cand)
    if df_cand.empty:
        return pd.DataFrame(), pd.DataFrame(), 0

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
        "apto_publicar": 0,
    }
    for col, val_def in cols_num.items():
        if col not in df_cand.columns:
            df_cand[col] = val_def
        else:
            df_cand[col] = pd.to_numeric(df_cand[col], errors="coerce").fillna(val_def)

    # Corregir los "-1" de productos no escaneados para que NUNCA sumen stock negativo
    df_cand["stock_fisico"] = df_cand["stock_total_anterior"].clip(lower=0).astype(int)
    df_cand["tier"] = df_cand["tier"].fillna("B").astype(str) if "tier" in df_cand.columns else "B"

    # Procesar tallas desde el historial real
    if not df_hist.empty:
        df_hist["stock_vallejo"] = pd.to_numeric(df_hist["stock_vallejo"], errors="coerce").fillna(0).clip(lower=0).astype(int)
        if "stock_seguro_publicable" not in df_hist.columns:
            df_hist["stock_seguro_publicable"] = (df_hist["stock_vallejo"] - 6).clip(lower=0)
        else:
            df_hist["stock_seguro_publicable"] = (
                pd.to_numeric(df_hist["stock_seguro_publicable"], errors="coerce").fillna(0).clip(lower=0).astype(int)
            )
        df_hist["talla_norm"] = df_hist["talla"].apply(normalizar_talla_mx)
        df_tallas = (
            df_hist.sort_values("fecha")
            .drop_duplicates(subset=["id_ps", "talla_norm"], keep="last")
            .copy()
        )
        df_tallas["talla_num"] = pd.to_numeric(df_tallas["talla_norm"], errors="coerce").fillna(24.0)

        rangos = df_tallas.groupby("id_ps")["talla_num"].agg(["min", "max"]).to_dict("index")
        suma_fisica_hist = df_tallas.groupby("id_ps")["stock_vallejo"].sum().to_dict()
        suma_segura_hist = df_tallas.groupby("id_ps")["stock_seguro_publicable"].sum().to_dict()
        conteo_tallas_seg = (
            df_tallas[df_tallas["stock_seguro_publicable"] > 0]
            .groupby("id_ps")["talla_norm"]
            .count()
            .to_dict()
        )

        tallas_seg = df_tallas[df_tallas["stock_seguro_publicable"] > 0].copy()
        tallas_seg["etiqueta_t"] = (
            tallas_seg["talla_norm"] + " (" + tallas_seg["stock_seguro_publicable"].astype(str) + ")"
        )
        mapa_detalle_seg = tallas_seg.groupby("id_ps")["etiqueta_t"].apply(", ".join).to_dict()

        tallas_fis = df_tallas[df_tallas["stock_vallejo"] > 0].copy()
        tallas_fis["etiqueta_f"] = (
            tallas_fis["talla_norm"] + " (" + tallas_fis["stock_vallejo"].astype(str) + ")"
        )
        mapa_detalle_fis = tallas_fis.groupby("id_ps")["etiqueta_f"].apply(", ".join).to_dict()
    else:
        df_tallas = pd.DataFrame(
            columns=["id_ps", "talla", "talla_norm", "talla_num", "stock_vallejo", "stock_seguro_publicable"]
        )
        rangos, suma_fisica_hist, suma_segura_hist, conteo_tallas_seg = {}, {}, {}, {}
        mapa_detalle_seg, mapa_detalle_fis = {}, {}

    # Sincronizar stock real con las lecturas de tallas
    df_cand["stock_fisico"] = df_cand.apply(
        lambda r: int(suma_fisica_hist.get(str(r["id_ps"]), r["stock_fisico"])), axis=1
    )
    df_cand["stock_publicable_total"] = df_cand.apply(
        lambda r: int(suma_segura_hist.get(str(r["id_ps"]), r["stock_publicable_total"])), axis=1
    )
    df_cand["tallas_seguras_count"] = df_cand.apply(
        lambda r: int(conteo_tallas_seg.get(str(r["id_ps"]), r["tallas_seguras_count"])), axis=1
    )

    # Detectar columna de URL existente en la tabla
    col_url_db = None
    for c_cand in ("url_producto", "url", "link", "producto_url"):
        if c_cand in df_cand.columns:
            col_url_db = c_cand
            break

    generos, categorias, colores = [], [], []
    urls_producto, skus_ml, conteo_fotos, estados_cortos, motivos_detallados = [], [], [], [], []
    fotos_4_bool = []

    for _, row in df_cand.iterrows():
        sku_s = str(row["id_ps"])
        nom_s = str(row.get("nombre_modelo", ""))
        r_info = rangos.get(sku_s, {"min": 22.0, "max": 26.0})

        g_db = row.get("genero") if "genero" in df_cand.columns else None
        c_db = row.get("tipo_calzado") if "tipo_calzado" in df_cand.columns else None

        generos.append(
            g_db if pd.notna(g_db) and str(g_db).strip() else clasificar_genero(nom_s, r_info["max"], r_info["min"])
        )
        categorias.append(c_db if pd.notna(c_db) and str(c_db).strip() else clasificar_categoria(nom_s))
        colores.append(clasificar_color(nom_s))

        # URL directo a Price Shoes y SKU interno para Mercado Libre
        url_raw = row.get(col_url_db) if col_url_db else None
        urls_producto.append(construir_url_ps(sku_s, url_raw))
        skus_ml.append(f"PS-{sku_s}")

        # Revisión de las 4 fotos del estudio fotográfico
        n_f, ok_4 = contar_fotos_sku(sku_s)
        conteo_fotos.append(f"{n_f}/4 {'✅' if ok_4 else '📸'}")
        fotos_4_bool.append(ok_4)

        # Diagnóstico en español claro de por qué se puede subir o no
        est_c, mot_d = diagnosticar_producto(
            costo=float(row["costo_socio"]),
            utilidad=float(row["utilidad_neta_ml"]),
            tallas_seguras=int(row["tallas_seguras_count"]),
            pares_seguros=int(row["stock_publicable_total"]),
            num_fotos=n_f,
            stock_fisico=int(row["stock_fisico"]),
        )
        estados_cortos.append(est_c)
        motivos_detallados.append(mot_d)

    df_cand["genero"] = generos
    df_cand["categoria"] = categorias
    df_cand["color"] = colores
    df_cand["url_ps"] = urls_producto
    df_cand["sku_ml"] = skus_ml
    df_cand["fotos_status"] = conteo_fotos
    df_cand["fotos_completas"] = fotos_4_bool
    df_cand["diagnostico_estado"] = estados_cortos
    df_cand["por_que_subir"] = motivos_detallados
    df_cand["tallas_seguras_txt"] = df_cand["id_ps"].map(lambda x: mapa_detalle_seg.get(str(x), "Ninguna > 5 pares"))
    df_cand["tallas_fisicas_txt"] = df_cand["id_ps"].map(lambda x: mapa_detalle_fis.get(str(x), "Sin lectura"))

    df_cand["titulo_ml_limpio"] = (
        df_cand["titulo_seo_ml"].fillna(df_cand["nombre_modelo"]).astype(str)
        if "titulo_seo_ml" in df_cand.columns
        else df_cand["nombre_modelo"].astype(str)
    )
    df_cand["apto_bool"] = df_cand["apto_publicar"].astype(int) == 1

    # Filtrar por defecto la basura (SKUs crudos que nunca se han escaneado en Vallejo ni tienen fotos)
    skus_con_historial = set(df_tallas["id_ps"].unique())
    df_cand["es_auditado"] = (
        df_cand["id_ps"].isin(skus_con_historial)
        | (df_cand["stock_fisico"] > 0)
        | (df_cand["fotos_completas"])
        | (df_cand["apto_publicar"] == 1)
    )

    df_cand["blob_busqueda"] = (
        df_cand["id_ps"]
        + " "
        + df_cand["sku_ml"]
        + " "
        + df_cand["nombre_modelo"].astype(str)
        + " "
        + df_cand["titulo_ml_limpio"]
        + " "
        + df_cand["genero"]
        + " "
        + df_cand["categoria"]
        + " "
        + df_cand["color"]
        + " "
        + df_cand["tallas_seguras_txt"]
    ).str.lower()

    return df_cand, df_tallas, total_crudos_bd


df_all, df_tallas, total_crudos_bd = cargar_datos_operativos()

if df_all.empty:
    st.error("⚠️ No se encontraron datos en `data/zapateria_vallejo.db`.")
    st.stop()

# ===========================================================================
# BARRA LATERAL: FILTROS PENSADOS PARA OPERAR PASO A PASO CON TUS HERMANOS
# ===========================================================================
with st.sidebar:
    st.markdown("### 👟 Control Paso a Paso")
    st.caption("Filtra qué productos subir primero sin necesidad de invertir en lotes.")

    filtro_etapa = st.radio(
        "📌 1. ¿Qué productos quieres ver?",
        options=[
            "✅ Listos para Subir (Cumplen Todo)",
            "🟢 Marcados para Publicar (Apto = 1)",
            "📦 Todos los Auditados en Vallejo",
            "🗃️ Catálogo Crudo Completo (19k SKUs)",
        ],
        index=0,
    )

    solo_4_fotos = st.checkbox("📸 Exigir las 4 fotos completas (4/4)", value=True)

    st.markdown("---")
    st.markdown("#### 🔍 2. Filtros de Calzado y Talla")

    base_slicers = df_all[df_all["es_auditado"]] if filtro_etapa != "🗃️ Catálogo Crudo Completo (19k SKUs)" else df_all

    lista_gen = sorted(base_slicers["genero"].unique().tolist())
    sel_genero = st.multiselect("👥 Género / Departamento:", options=lista_gen, default=lista_gen)

    lista_cat = sorted(base_slicers["categoria"].unique().tolist())
    sel_categoria = st.multiselect("👟 Tipo de Calzado:", options=lista_cat, default=lista_cat)

    lista_col = sorted(base_slicers["color"].unique().tolist())
    sel_color = st.multiselect("🎨 Color:", options=lista_col, default=lista_col)

    # Filtro de Tallas Exactas (con conversión #9 -> 29.0 MX)
    tallas_ordenadas = sorted(
        df_tallas["talla_norm"].dropna().unique().tolist(),
        key=lambda x: float(x) if re.match(r"^\d+(\.\d+)?$", str(x)) else 99.0,
    )
    mapa_etiqueta_talla = {}
    opciones_talla = []
    for t_str in tallas_ordenadas:
        try:
            fv = float(t_str)
            alias = f" (Del {fv - 20:g})" if 22.0 <= fv <= 31.0 else ""
            lbl = f"{t_str} MX{alias}"
        except Exception:
            lbl = f"{t_str} MX"
        opciones_talla.append(lbl)
        mapa_etiqueta_talla[lbl] = t_str

    tallas_sel_ui = st.multiselect(
        "📏 Filtrar Talla(s) Específica(s):",
        options=opciones_talla,
        placeholder="Ej. 24.0 MX, 27.0 MX (Del 7)...",
    )

    st.markdown("---")
    st.markdown("#### 💵 3. Presupuesto por Par (Unitario)")

    max_costo_par = int(max(800.0, float(base_slicers["costo_socio"].max())))
    tope_costo_par = st.slider(
        "Máximo a desembolsar en Vallejo por 1 par ($):",
        min_value=100,
        max_value=max_costo_par,
        value=max_costo_par,
        step=25,
        help="Si quieren empezar con zapatos económicos al ir a surtir un pedido, baja este control.",
    )

    min_ganancia_par = st.slider(
        "Ganancia limpia mínima en tu bolsa por par ($):",
        min_value=0,
        max_value=400,
        value=120,
        step=20,
    )

# ===========================================================================
# APLICACIÓN DE FILTROS
# ===========================================================================
if filtro_etapa == "✅ Listos para Subir (Cumplen Todo)":
    df = df_all[df_all["diagnostico_estado"] == "✅ LISTO PARA SUBIR"].copy()
elif filtro_etapa == "🟢 Marcados para Publicar (Apto = 1)":
    df = df_all[df_all["apto_publicar"] == 1].copy()
elif filtro_etapa == "📦 Todos los Auditados en Vallejo":
    df = df_all[df_all["es_auditado"]].copy()
else:
    df = df_all.copy()

if solo_4_fotos:
    df = df[df["fotos_completas"]]

if sel_genero:
    df = df[df["genero"].isin(sel_genero)]
if sel_categoria:
    df = df[df["categoria"].isin(sel_categoria)]
if sel_color:
    df = df[df["color"].isin(sel_color)]

df = df[(df["costo_socio"] <= tope_costo_par) & (df["utilidad_neta_ml"] >= min_ganancia_par)]

# Filtro por tallas seleccionadas
tallas_codigos = [mapa_etiqueta_talla[k] for k in tallas_sel_ui]
if tallas_codigos:
    sub_t = df_tallas[
        (df_tallas["talla_norm"].isin(tallas_codigos)) & (df_tallas["stock_seguro_publicable"] > 0)
    ]
    df = df[df["id_ps"].isin(sub_t["id_ps"].unique())].copy()

# ===========================================================================
# ENCABEZADO Y TARJETAS UNITARIAS (SIN LOTES MILLONARIOS NI ROI CONFUSO)
# ===========================================================================
st.markdown("## 👟 Centro de Control Vallejo · Operación Paso a Paso")
st.caption(
    "Modelo de compra bajo demanda (Par por Par): Publicas primero, y solo vas a Vallejo a comprar 1 par cuando el cliente ya te pagó en Mercado Libre."
)

busqueda_rapida = st.text_input(
    "🔎 Buscar zapato por nombre, SKU de Price Shoes, color o talla (ej: escolar 1338196, tenis hombre negro, bota):",
    placeholder="Escribe aquí para filtrar al instante...",
)

if busqueda_rapida.strip():
    for tok in busqueda_rapida.lower().split():
        df = df[df["blob_busqueda"].str.contains(re.escape(tok), na=False)]

k1, k2, k3, k4, k5 = st.columns(5)

n_listos = len(df)
n_marcados_ml = int((df["apto_publicar"] == 1).sum())
costo_unit_prom = float(df[df["costo_socio"] > 0]["costo_socio"].mean()) if not df[df["costo_socio"] > 0].empty else 0.0
ganancia_unit_prom = float(df[df["utilidad_neta_ml"] > 0]["utilidad_neta_ml"].mean()) if not df[df["utilidad_neta_ml"] > 0].empty else 0.0
precio_ml_prom = float(df[df["precio_ml"] > 0]["precio_ml"].mean()) if not df[df["precio_ml"] > 0].empty else 0.0
con_4_fotos = int(df["fotos_completas"].sum())

k1.metric(
    "✅ Productos en Vista",
    f"{n_listos} modelos",
    f"{n_marcados_ml} encendidos para ML",
)
k2.metric(
    "💵 Costo en Vallejo (1 Par)",
    f"${costo_unit_prom:,.0f} MXN",
    "Lo que pagas en caja al surtir 1 venta",
)
k3.metric(
    "🏷️ Precio en Mercado Libre",
    f"${precio_ml_prom:,.0f} MXN",
    "Lo que paga el comprador por 1 par",
)
k4.metric(
    "💰 Tu Ganancia Limpia (1 Par)",
    f"${ganancia_unit_prom:,.0f} MXN",
    "Libre en tu bolsa tras ML, envío y SAT",
)
k5.metric(
    "📸 Con 4 Fotos Listas",
    f"{con_4_fotos} de {n_listos}",
    "Portada, ángulo, beneficios y tallas",
)

st.markdown("---")

# ===========================================================================
# 4 PESTAÑAS OPERATIVAS CLARAS PARA TI Y TUS HERMANOS
# ===========================================================================
tab_control, tab_ventas, tab_inspector, tab_resumen = st.tabs(
    [
        "✅ 1. Control de Productos a Subir (Por qué SÍ / URL / Fotos)",
        "🚨 2. ¡Cayó una Venta! ¿Qué Producto es en Vallejo?",
        "📸 3. Inspector de Producto (Fotos 4/4, Ganancia y Tallas)",
        "📊 4. Panorama de Tallas y Categorías",
    ]
)

# ===========================================================================
# PESTAÑA 1: CONTROL DE PRODUCTOS A SUBIR (EL CORAZÓN QUE PEDISTE)
# ===========================================================================
with tab_control:
    st.markdown(
        '<div class="box-header">✅ Lista de Control: ¿Qué productos subimos primero, por qué son seguros y cuál es su URL?</div>',
        unsafe_allow_html=True,
    )
    st.caption(
        "💡 **Cómo empezar poco a poco:** Palomea en la primera columna (`Subir a ML`) solo los 5, 10 o 20 productos con los que tú y tus hermanos quieran arrancar esta semana y presiona el botón verde de guardar. La columna **SKU en ML** (`PS-...`) se guarda oculta en Mercado Libre para que al venderse sepas exactamente qué código buscar en Price Shoes."
    )

    if df.empty:
        st.warning("No hay productos con los filtros actuales. Desmarca 'Exigir las 4 fotos' o cambia la opción 1 de la barra lateral a 'Todos los Auditados en Vallejo'.")
    else:
        c_btn1, c_btn2, c_btn3 = st.columns([3, 3, 6])
        with c_btn1:
            top_n_auto = st.number_input("Cantidad de productos para iniciar poco a poco:", min_value=1, max_value=100, value=10, step=5)
        with c_btn2:
            st.write("")
            if st.button(f"⚡ Seleccionar solo el Top {top_n_auto} más seguros", use_container_width=True):
                top_ids = (
                    df.sort_values(["fotos_completas", "stock_publicable_total", "utilidad_neta_ml"], ascending=[False, False, False])
                    .head(int(top_n_auto))["id_ps"]
                    .tolist()
                )
                conn = sqlite3.connect(RUTA_BD, timeout=20)
                conn.execute("UPDATE zapatos_candidatos SET apto_publicar = 0;")
                for tid in top_ids:
                    conn.execute("UPDATE zapatos_candidatos SET apto_publicar = 1 WHERE CAST(id_ps AS TEXT) = ?;", (str(tid),))
                conn.commit()
                conn.close()
                st.cache_data.clear()
                st.success(f"✅ Se dejaron encendidos únicamente los {len(top_ids)} mejores productos para iniciar paso a poco.")
                st.rerun()

        cols_tabla_ctrl = [
            "apto_bool",
            "id_ps",
            "sku_ml",
            "nombre_modelo",
            "url_ps",
            "fotos_status",
            "costo_socio",
            "precio_ml",
            "utilidad_neta_ml",
            "tallas_seguras_txt",
            "stock_publicable_total",
            "stock_fisico",
            "por_que_subir",
        ]

        df_editor = st.data_editor(
            df[cols_tabla_ctrl].sort_values(
                by=["apto_bool", "fotos_completas", "utilidad_neta_ml", "stock_publicable_total"],
                ascending=[False, False, False, False],
            ),
            column_config={
                "apto_bool": st.column_config.CheckboxColumn("🚀 Subir a ML"),
                "id_ps": st.column_config.TextColumn("ID Price Shoes", disabled=True),
                "sku_ml": st.column_config.TextColumn(
                    "SKU Oculto ML",
                    help="Este código se envía en el campo SELLER_SKU de Mercado Libre para identificar el producto cuando se venda.",
                    disabled=True,
                ),
                "nombre_modelo": st.column_config.TextColumn("Modelo", disabled=True),
                "url_ps": st.column_config.LinkColumn(
                    "URL Price Shoes",
                    display_text="🔗 Abrir en Price Shoes",
                    help="Haz clic para abrir la ficha oficial del zapato en Price Shoes.",
                    disabled=True,
                ),
                "fotos_status": st.column_config.TextColumn("Fotos (4/4)", disabled=True),
                "costo_socio": st.column_config.NumberColumn("Costo Vallejo (1 par)", format="$%.0f", disabled=True),
                "precio_ml": st.column_config.NumberColumn("Precio Venta ML", format="$%.0f", disabled=True),
                "utilidad_neta_ml": st.column_config.NumberColumn("💰 Tu Ganancia (1 par)", format="$%.2f", disabled=True),
                "tallas_seguras_txt": st.column_config.TextColumn("Tallas Seguras (>5 pares)", disabled=True),
                "stock_publicable_total": st.column_config.NumberColumn("Pares Libres", disabled=True),
                "stock_fisico": st.column_config.NumberColumn("Físico Tienda", disabled=True),
                "por_que_subir": st.column_config.TextColumn("📋 ¿Por qué se puede subir? (Diagnóstico)", disabled=True),
            },
            use_container_width=True,
            hide_index=True,
            height=470,
        )

        if st.button("💾 Guardar mi Selección de Productos a Subir en la Base de Datos", type="primary"):
            conn = sqlite3.connect(RUTA_BD, timeout=20)
            for _, fila in df_editor.iterrows():
                conn.execute(
                    "UPDATE zapatos_candidatos SET apto_publicar = ? WHERE CAST(id_ps AS TEXT) = ?",
                    (1 if fila["apto_bool"] else 0, str(fila["id_ps"])),
                )
            conn.commit()
            conn.close()
            st.cache_data.clear()
            st.success("✅ ¡Selección guardada! Solo los productos palomeados se sincronizarán con Mercado Libre.")
            st.rerun()

# ===========================================================================
# PESTAÑA 2: DETECTOR DE VENTAS (¿CÓMO SABEMOS QUÉ COMPRAR CUANDO SE VENDE?)
# ===========================================================================
with tab_ventas:
    st.markdown(
        '<div class="box-header">🚨 Traductor de Ventas de Mercado Libre ➔ Pasillo de Price Shoes Vallejo</div>',
        unsafe_allow_html=True,
    )
    st.info(
        "💡 **¿Cómo funciona cuando cae una venta?**\n"
        "1. Aunque el título público en Mercado Libre no muestre el ID para cuidar tu anuncio, el sincronizador guarda en el campo interno de vendedor (`SELLER_SKU`) el código **`PS-ID-TALLA`** (por ejemplo: **`PS-1338196-T24.0`**).\n"
        "2. Además, si cualquiera de tus hermanos pega aquí abajo el **SKU interno**, el **ID** o el **Título que apareció en la venta de Mercado Libre**, el sistema le muestra al instante qué zapato es, su foto y el botón directo al URL de Price Shoes para ir por él."
    )

    col_v_in1, col_v_in2 = st.columns([8, 4])
    with col_v_in1:
        consulta_venta = st.text_input(
            "🛒 Pega aquí el código SKU (`PS-1338196`), el ID (`1338196`) o el título que te notificó Mercado Libre:",
            value="1338196",
        )
    with col_v_in2:
        talla_vendida_input = st.text_input(
            "📏 Talla que pidió el cliente (opcional, ej: 24.0):",
            placeholder="Ej: 24.0",
        )

    q_limpia = consulta_venta.upper().replace("PS-", "").strip()
    m_id_venta = re.search(r"\b(\d{5,8})\b", q_limpia)

    df_auditados = df_all[df_all["es_auditado"]].copy()
    if m_id_venta:
        coincidencias = df_all[df_all["id_ps"] == m_id_venta.group(1)]
    elif q_limpia:
        coincidencias = df_auditados[
            df_auditados["blob_busqueda"].str.contains(re.escape(q_limpia.lower()), na=False)
        ]
    else:
        coincidencias = pd.DataFrame()

    if coincidencias.empty:
        st.warning("No se encontró ese código o título. Verifica el número o escribe una parte del nombre del modelo.")
    else:
        prod_v = coincidencias.iloc[0]
        id_v = str(prod_v["id_ps"])

        c_ficha1, c_ficha2 = st.columns([4, 8])
        with c_ficha1:
            ruta_portada = DIR_CATALOGO / id_v / "01_portada_blanca.jpg"
            if ruta_portada.exists():
                st.image(str(ruta_portada), caption=f"ID Price Shoes: {id_v}", use_container_width=True)
            else:
                st.info("Sin foto local en catálogo.")

        with c_ficha2:
            st.markdown(f"### 🎯 Producto Identificado: `{id_v}` · {prod_v['nombre_modelo']}")
            st.markdown(f"**🔗 Link Oficial para mostrar o buscar en tienda:** [**Abrir Producto {id_v} en PriceShoes.com**]({prod_v['url_ps']})")

            m_v1, m_v2, m_v3, m_v4 = st.columns(4)
            m_v1.metric("🆔 Código en Tienda Vallejo", id_v, f"SKU ML: {prod_v['sku_ml']}")
            m_v2.metric("💵 Pagarás en Caja Vallejo", f"${prod_v['costo_socio']:,.0f} MXN", "Costo de 1 par")
            m_v3.metric("🏷️ Cobrado en Mercado Libre", f"${prod_v['precio_ml']:,.0f} MXN", "Pagado por cliente")
            m_v4.metric("💰 Tu Ganancia Limpia", f"${prod_v['utilidad_neta_ml']:,.2f} MXN", "Libre en tu bolsa")

            st.markdown("#### 📏 Existencias actuales en Vallejo por Talla para surtir este pedido:")
            tallas_v = df_tallas[df_tallas["id_ps"] == id_v].sort_values("talla_num").copy()
            if not tallas_v.empty:
                if talla_vendida_input.strip():
                    t_buscada = normalizar_talla_mx(talla_vendida_input)
                    fila_t = tallas_v[tallas_v["talla_norm"] == t_buscada]
                    if not fila_t.empty:
                        stk_f = int(fila_t.iloc[0]["stock_vallejo"])
                        stk_s = int(fila_t.iloc[0]["stock_seguro_publicable"])
                        st.success(
                            f"✅ En la **Talla {t_buscada} MX** hay **{stk_f} pares físicos en Vallejo** ({stk_s} libres por encima del candado de 5 pares)."
                        )
                st.dataframe(
                    tallas_v[["talla_norm", "stock_vallejo", "stock_seguro_publicable", "fecha"]].rename(
                        columns={
                            "talla_norm": "Talla (MX)",
                            "stock_vallejo": "Pares Físicos en Vallejo",
                            "stock_seguro_publicable": "Pares Seguros Publicables (>5 + Poisson)",
                            "fecha": "Última Lectura del Robot",
                        }
                    ),
                    use_container_width=True,
                    hide_index=True,
                )

# ===========================================================================
# PESTAÑA 3: INSPECTOR DE PRODUCTO (FOTOS 4/4, URL, RAZÓN Y DESGLOSE POR PAR)
# ===========================================================================
with tab_inspector:
    lista_insp = df if not df.empty else df_all[df_all["es_auditado"]]
    opciones_insp = (
        lista_insp["id_ps"]
        + " · "
        + lista_insp["nombre_modelo"]
        + " | Ganancia: $"
        + lista_insp["utilidad_neta_ml"].round(0).astype(int).astype(str)
        + " | Fotos: "
        + lista_insp["fotos_status"]
    ).tolist()

    if opciones_insp:
        sel_insp = st.selectbox("🔍 Elige un producto para revisar sus 4 fotos, su URL y cuánto deja por par:", opciones_insp)
        sku_i = sel_insp.split(" · ")[0]
        fila_i = lista_insp[lista_insp["id_ps"] == sku_i].iloc[0]

        # Banner de diagnóstico claro
        if fila_i["diagnostico_estado"] == "✅ LISTO PARA SUBIR":
            st.success(
                f"**{fila_i['diagnostico_estado']}** — {fila_i['por_que_subir']}  |  "
                f"🔗 [**Abrir página del producto en PriceShoes.com**]({fila_i['url_ps']})  |  "
                f"🏷️ **SKU Oculto en ML:** `{fila_i['sku_ml']}`"
            )
        else:
            st.warning(
                f"**{fila_i['diagnostico_estado']}** — {fila_i['por_que_subir']}  |  "
                f"🔗 [**Abrir página del producto en PriceShoes.com**]({fila_i['url_ps']})"
            )

        col_wf, col_tallas = st.columns([6, 6])

        with col_wf:
            with st.container(border=True):
                st.markdown(f'<div class="box-header">💰 Cuentas Claras por 1 Par Vendido · SKU {sku_i}</div>', unsafe_allow_html=True)
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
                        x=[
                            "Paga el Cliente",
                            "Pagar en Vallejo",
                            "Comisión ML",
                            "Envío ML",
                            "Impuestos SAT",
                            "Seguro 2%",
                            "Tu Ganancia Limpia",
                        ],
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
                    margin=dict(l=4, r=4, t=15, b=4),
                )
                st.plotly_chart(fig_wf, use_container_width=True)

        with col_tallas:
            with st.container(border=True):
                st.markdown(
                    f'<div class="box-header">📏 ¿Por qué es seguro su stock? (Pares por Talla en Vallejo · SKU {sku_i})</div>',
                    unsafe_allow_html=True,
                )
                tallas_sku = df_tallas[df_tallas["id_ps"] == sku_i].sort_values("talla_num").copy()
                if not tallas_sku.empty:
                    tallas_sku["Candado Seguridad (5 + Poisson)"] = (
                        tallas_sku["stock_vallejo"] - tallas_sku["stock_seguro_publicable"]
                    ).clip(lower=0)

                    fig_ts = go.Figure()
                    fig_ts.add_trace(
                        go.Bar(
                            x=tallas_sku["talla_norm"],
                            y=tallas_sku["stock_seguro_publicable"],
                            name="Pares Seguros (Se publican en ML)",
                            marker_color="#10b981",
                        )
                    )
                    fig_ts.add_trace(
                        go.Bar(
                            x=tallas_sku["talla_norm"],
                            y=tallas_sku["Candado Seguridad (5 + Poisson)"],
                            name="Intocables por Seguridad (Primeros 5 + Poisson)",
                            marker_color="#475569",
                        )
                    )
                    fig_ts.add_hline(
                        y=PISO_FANTASMA_TALLA,
                        line_dash="dash",
                        line_color="#ef4444",
                        annotation_text="Mínimo 5 pares intocables (Evita devoluciones)",
                    )
                    fig_ts.update_layout(
                        barmode="stack",
                        height=320,
                        template="plotly_dark",
                        paper_bgcolor="rgba(0,0,0,0)",
                        plot_bgcolor="rgba(0,0,0,0)",
                        margin=dict(l=4, r=4, t=15, b=4),
                        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
                    )
                    st.plotly_chart(fig_ts, use_container_width=True)

        with st.container(border=True):
            st.markdown(
                f'<div class="box-header">📸 Verificación de las 4 Fotos Necesarias ({fila_i["fotos_status"]}) · SKU {sku_i}</div>',
                unsafe_allow_html=True,
            )
            carpeta_img = DIR_CATALOGO / sku_i
            archivos_img = [
                ("1. Portada Blanca (Obligatoria ML)", carpeta_img / "01_portada_blanca.jpg"),
                ("2. Ángulo Detalle", carpeta_img / "02_angulo_galeria.jpg"),
                ("3. Beneficios (Sin 'Caja' ni 'Envío Hoy')", carpeta_img / "03_ficha_beneficios.jpg"),
                ("4. Guía de Tallas en CM", carpeta_img / "04_guia_tallas_cm.jpg"),
            ]
            cols_img = st.columns(4)
            for idx, (tit_i, ruta_i) in enumerate(archivos_img):
                with cols_img[idx]:
                    if ruta_i.exists():
                        st.image(str(ruta_i), caption=f"✅ {tit_i}", use_container_width=True)
                    else:
                        st.error(f"❌ Falta: {ruta_i.name}")

# ===========================================================================
# PESTAÑA 4: PANORAMA VISUAL DE TALLAS Y GANANCIA UNITARIA
# ===========================================================================
with tab_resumen:
    if not df.empty:
        c_p1, c_p2 = st.columns([6, 6])
        with c_p1:
            with st.container(border=True):
                st.markdown(
                    '<div class="box-header">💵 Comparativa Unitaria: Lo que pagas en Vallejo vs. Tu Ganancia Limpia (Por 1 Par)</div>',
                    unsafe_allow_html=True,
                )
                df_top_u = df.sort_values("utilidad_neta_ml", ascending=True).tail(15).copy()
                df_top_u["etiq"] = df_top_u["id_ps"] + " · " + df_top_u["nombre_modelo"].str.slice(0, 22)

                fig_u = go.Figure()
                fig_u.add_trace(
                    go.Bar(
                        y=df_top_u["etiq"],
                        x=df_top_u["costo_socio"],
                        name="Desembolso Vallejo (1 par)",
                        orientation="h",
                        marker_color="#64748b",
                    )
                )
                fig_u.add_trace(
                    go.Bar(
                        y=df_top_u["etiq"],
                        x=df_top_u["utilidad_neta_ml"],
                        name="Tu Ganancia Limpia (1 par)",
                        orientation="h",
                        marker_color="#10b981",
                    )
                )
                fig_u.update_layout(
                    barmode="group",
                    height=420,
                    template="plotly_dark",
                    paper_bgcolor="rgba(0,0,0,0)",
                    plot_bgcolor="rgba(0,0,0,0)",
                    margin=dict(l=4, r=4, t=10, b=4),
                    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
                    xaxis_title="$ MXN por 1 Par",
                )
                st.plotly_chart(fig_u, use_container_width=True)

        with c_p2:
            with st.container(border=True):
                st.markdown(
                    '<div class="box-header">🔥 Mapa de Calor: ¿Cuántos pares seguros hay en cada talla? (Top 15 Modelos)</div>',
                    unsafe_allow_html=True,
                )
                top_skus = df.sort_values("stock_publicable_total", ascending=False).head(15)["id_ps"].tolist()
                df_hm = df_tallas[df_tallas["id_ps"].isin(top_skus)].copy()
                if not df_hm.empty:
                    mapa_n = df.set_index("id_ps")["nombre_modelo"].apply(lambda s: str(s)[:20]).to_dict()
                    df_hm["modelo"] = df_hm["id_ps"] + " · " + df_hm["id_ps"].map(mapa_n)
                    piv = df_hm.pivot_table(
                        index="modelo",
                        columns="talla_norm",
                        values="stock_seguro_publicable",
                        aggfunc="sum",
                        fill_value=0,
                    )
                    cols_o = sorted(piv.columns, key=lambda x: float(x) if re.match(r"^\d+(\.\d+)?$", str(x)) else 99.0)
                    piv = piv[cols_o]
                    fig_hm = px.imshow(
                        piv,
                        text_auto=True,
                        aspect="auto",
                        color_continuous_scale="Tealgrn",
                        height=420,
                        template="plotly_dark",
                    )
                    fig_hm.update_layout(
                        margin=dict(l=4, r=4, t=10, b=4),
                        paper_bgcolor="rgba(0,0,0,0)",
                        coloraxis_showscale=False,
                        xaxis_title="Talla México (CM)",
                        yaxis_title="SKU · Modelo",
                    )
                    st.plotly_chart(fig_hm, use_container_width=True)