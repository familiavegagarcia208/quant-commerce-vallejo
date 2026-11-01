"""Quant-Commerce Vallejo: Panel Ejecutivo Omnicanal (Simple, Veloz y Directo)."""
from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st

# ===========================================================================
# 1. CONFIGURACIÓN BÁSICA
# ===========================================================================
RUTA_BD = Path("data/zapateria_vallejo.db")
DIR_CATALOGO = Path("data/catalogo_listo")

UMBRAL_POISSON = 6  # Piso > 5 pares por talla + Poisson (Best-Sellers S/A)
CONFIANZA_PCT = 95
MERMA_PCT = 2
MAX_OPCIONES_SELECTOR = 2000

# Opciones del botón superior (Filtro Maestro)
VISTA_TODO = "📊 Todo el Catálogo"
VISTA_CON_COSTO = "🔍 Con Costo Socio"
VISTA_PARES_SEGUROS = "🛡️ Con Pares Seguros"
VISTA_APTOS = f"✅ 🔥 Best-Sellers Stock Blindado (>5/talla + Poisson)"

COLS_NUMERICAS = [
    "costo_socio", "stock_total_anterior", "velocidad_diaria",
    "stock_publicable_total", "precio_piso_ml", "precio_ml",
    "utilidad_neta_ml", "fondo_merma_ml", "iva_a_favor_ml",
    "precio_amazon", "utilidad_neta_amazon", "precio_tiktok",
    "utilidad_neta_tiktok", "apto_publicar", "estudio_foto_listo",
]

st.set_page_config(
    page_title="Quant-Commerce Vallejo",
    page_icon="👞",
    layout="wide",
)

st.markdown(
    """
    <style>
    .block-container {padding-top: 2rem; padding-bottom: 3rem;}
    [data-testid="stMetric"] {
        background: var(--secondary-background-color);
        border: 1px solid rgba(128,128,128,.25);
        border-radius: 8px;
        padding: 14px 16px;
    }
    [data-testid="stMetricLabel"] {font-size: .85rem; opacity: .8;}
    </style>
    """,
    unsafe_allow_html=True,
)


# ===========================================================================
# 2. FUNCIONES DE DATOS (CON CACHÉ AUTOMÁTICA)
# ===========================================================================
@st.cache_data(show_spinner="Cargando catálogo…", max_entries=2)
def cargar_catalogo(ruta_bd: str, mtime: float) -> pd.DataFrame:
    """Lee el catálogo de SQLite y agrega el resumen de tallas seguras de cada SKU."""
    with sqlite3.connect(ruta_bd) as conn:
        df = pd.read_sql("SELECT * FROM zapatos_candidatos", conn)

        # Consulta rápida para saber qué tallas tienen stock seguro en la última fecha
        sql_tallas = """
            SELECT CAST(h.id_ps AS TEXT) AS id_ps,
                   CAST(h.talla AS TEXT) AS talla,
                   CAST(h.stock_seguro_publicable AS INTEGER) AS pares
            FROM historial_stock_21_dias h
            INNER JOIN (
                SELECT id_ps, MAX(fecha) AS max_fecha
                FROM historial_stock_21_dias
                GROUP BY id_ps
            ) u ON h.id_ps = u.id_ps AND h.fecha = u.max_fecha
            WHERE COALESCE(h.stock_seguro_publicable, 0) > 0
            ORDER BY CAST(h.talla AS REAL)
        """
        try:
            df_tallas = pd.read_sql(sql_tallas, conn)
        except Exception:
            df_tallas = pd.DataFrame()

    if df.empty:
        return df

    # Asegurar que las columnas numéricas no tengan nulos ni texto
    for col in COLS_NUMERICAS:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)
        else:
            df[col] = 0.0

    df["id_ps"] = df["id_ps"].astype(str)
    df["tier"] = df["tier"].fillna("B").astype(str) if "tier" in df.columns else "B"
    df["nombre_modelo"] = df["nombre_modelo"].fillna("").astype(str)
    if "url_real" not in df.columns:
        df["url_real"] = None

    df["ultima_sincronizacion"] = (
        pd.to_datetime(df["ultima_sincronizacion"], errors="coerce")
        if "ultima_sincronizacion" in df.columns
        else pd.NaT
    )

    # Armar texto de tallas seguras por modelo: ej. "T23 (4p) · T24 (6p)"
    if not df_tallas.empty:
        df_tallas["texto"] = "T" + df_tallas["talla"] + " (" + df_tallas["pares"].astype(str) + "p)"
        mapa_tallas = df_tallas.groupby("id_ps", sort=False)["texto"].apply(" · ".join).to_dict()
        df["tallas_seguras"] = df["id_ps"].map(mapa_tallas).fillna("—")
    else:
        df["tallas_seguras"] = "—"

    # Columnas calculadas útiles para el negocio
    vel = df["velocidad_diaria"].where(df["velocidad_diaria"] > 0)
    df["dias_cobertura"] = (df["stock_total_anterior"] / vel).round(1)
    df["ref_competencia_x2"] = df["costo_socio"] * 2.0
    df["efectivo_total_ml"] = df["stock_publicable_total"] * df["utilidad_neta_ml"]

    # Columnas auxiliares para el buscador y los menús desplegables
    pares = df["stock_publicable_total"].astype(int).astype(str)
    df["_etiqueta_menu"] = df["id_ps"] + " · " + df["nombre_modelo"] + " (" + pares + " pares seguros)"
    df["_texto_busqueda"] = (
        df["id_ps"] + " " + df["nombre_modelo"] + " " + df["tallas_seguras"]
    ).str.lower()

    # Ordenar una sola vez: primero los que tienen más pares seguros y mayor rotación
    return df.sort_values(
        ["apto_publicar", "stock_publicable_total", "velocidad_diaria"],
        ascending=False,
        ignore_index=True,
    )


@st.cache_data(show_spinner=False, max_entries=256)
def cargar_tallas_sku(ruta_bd: str, mtime: float, sku: str) -> pd.DataFrame:
    """Devuelve la tabla de tallas de la última lectura de un SKU."""
    sql = """
        SELECT talla, stock_vallejo, stock_seguro_publicable
        FROM historial_stock_21_dias
        WHERE id_ps = ?
          AND fecha = (SELECT MAX(fecha) FROM historial_stock_21_dias WHERE id_ps = ?)
    """
    with sqlite3.connect(ruta_bd) as conn:
        df_h = pd.read_sql(sql, conn, params=(sku, sku))

    if df_h.empty:
        return df_h

    df_h["stock_vallejo"] = pd.to_numeric(df_h["stock_vallejo"], errors="coerce").fillna(0).astype(int)
    df_h["stock_seguro_publicable"] = (
        pd.to_numeric(df_h["stock_seguro_publicable"], errors="coerce").fillna(0).astype(int)
    )
    df_h["talla"] = df_h["talla"].astype(str)
    df_h["talla_num"] = pd.to_numeric(df_h["talla"], errors="coerce")
    return df_h.sort_values(["talla_num", "talla"], ignore_index=True)


def obtener_fotos_sku(sku: str) -> list[Path]:
    """Busca las imágenes .jpg o .png dentro de la carpeta del SKU."""
    carpeta = DIR_CATALOGO / str(sku)
    if not carpeta.is_dir():
        return []
    return sorted(p for p in carpeta.iterdir() if p.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"})


def filtrar_datos(
    df: pd.DataFrame, vista: str, busqueda: str, tiers: list[str], vel_min: float
) -> pd.DataFrame:
    """Aplica los filtros elegidos por el usuario."""
    mask = pd.Series(True, index=df.index)

    if vista == VISTA_CON_COSTO:
        mask &= df["costo_socio"] > 0
    elif vista == VISTA_PARES_SEGUROS:
        mask &= df["stock_publicable_total"] > 0
    elif vista == VISTA_APTOS:
        mask &= df["apto_publicar"] == 1

    if tiers:
        mask &= df["tier"].isin(tiers)

    if vel_min > 0:
        mask &= df["velocidad_diaria"] >= vel_min

    texto = busqueda.strip().lower()
    if texto:
        mask &= df["_texto_busqueda"].str.contains(texto, regex=False, na=False)

    return df.loc[mask]


# ===========================================================================
# 3. CARGA INICIAL Y ENCABEZADO
# ===========================================================================
if not RUTA_BD.exists():
    st.error(f"No se encontró la base de datos en `{RUTA_BD}`.")
    st.stop()

MTIME_BD = RUTA_BD.stat().st_mtime
df_catalogo = cargar_catalogo(str(RUTA_BD), MTIME_BD)

if df_catalogo.empty:
    st.warning("La base de datos está vacía.")
    st.stop()

st.title("👞 Quant-Commerce Vallejo · Suite Ejecutiva Omnicanal")

ultima_sync = df_catalogo["ultima_sincronizacion"].max()
subtitulo = f"Universo monitoreado: {len(df_catalogo):,} SKUs"
if pd.notna(ultima_sync):
    subtitulo += f" · Última sincronización: {ultima_sync:%d/%m/%Y %H:%M}"
st.caption(subtitulo)

# --- 5 Tarjetas de Resumen (KPIs) ---
total_skus = len(df_catalogo)
skus_auditados = int(df_catalogo["ultima_sincronizacion"].notna().sum())
skus_aptos = int((df_catalogo["apto_publicar"] == 1).sum())
skus_con_seguros = int((df_catalogo["stock_publicable_total"] > 0).sum())
total_pares_seguros = int(df_catalogo["stock_publicable_total"].sum())
total_efectivo_ml = float(df_catalogo["efectivo_total_ml"].sum())

k1, k2, k3, k4, k5 = st.columns(5)
k1.metric("Universo Catálogo", f"{total_skus:,}")
k2.metric("SKUs Auditados", f"{skus_auditados:,}", f"{skus_auditados / total_skus * 100:.1f}% cobertura", delta_color="off")
k3.metric("Aptos para Publicar", f"{skus_aptos:,}", "Piso >5/talla + Tier S/A", delta_color="off")
k4.metric("Pares Seguros", f"{total_pares_seguros:,}", f"Repartidos en {skus_con_seguros} SKUs", delta_color="off")
k5.metric("Efectivo Potencial (ML)", f"${total_efectivo_ml:,.0f}", "Si se vende todo", delta_color="off")

st.divider()

# ===========================================================================
# 4. BARRA LATERAL Y BOTÓN DE FILTRO MAESTRO
# ===========================================================================
tiers_unicos = sorted(df_catalogo["tier"].unique().tolist())
vel_maxima = float(max(10.0, df_catalogo["velocidad_diaria"].max()))


def limpiar_filtros() -> None:
    for clave in ("f_vista", "f_busqueda", "f_tiers", "f_vel"):
        st.session_state.pop(clave, None)


with st.sidebar:
    st.header("🎛️ Filtros")
    busqueda = st.text_input("Buscar SKU, modelo o talla (ej. T24)", key="f_busqueda", placeholder="Ej. 1338649 o T24")
    tiers_sel = st.multiselect("Nivel (Tier)", tiers_unicos, default=tiers_unicos, key="f_tiers")
    vel_min = st.slider("Velocidad mínima (pares/día)", 0.0, vel_maxima, 0.0, 0.1, key="f_vel")
    st.button("Restablecer filtros", on_click=limpiar_filtros, width="stretch")

modo_vista = st.segmented_control(
    "Filtro maestro de vista (haz clic en '🛡️ Con Pares Seguros' para ver cuáles son):",
    options=[VISTA_TODO, VISTA_CON_COSTO, VISTA_PARES_SEGUROS, VISTA_APTOS],
    default=VISTA_TODO,
    selection_mode="single",
    key="f_vista",
)

df_filtrado = filtrar_datos(df_catalogo, modo_vista or VISTA_TODO, busqueda, tiers_sel, vel_min)
st.divider()

# ===========================================================================
# 5. PESTAÑAS DEL PANEL
# ===========================================================================
tab_ranking, tab_finanzas, tab_tallas, tab_estudio, tab_export = st.tabs([
    "🔥 Ranking y Pares Seguros",
    "💵 Precios y Utilidades",
    "📦 Ver Tallas por Modelo",
    "📸 Estudio Fotográfico",
    "📥 Descargar Excel/CSV",
])

# --- PESTAÑA 1: RANKING Y DETALLE DE PARES SEGUROS ---
with tab_ranking:
    pares_en_tabla = int(df_filtrado["stock_publicable_total"].sum())
    st.subheader("Catálogo y Desglose de Pares Seguros")
    st.caption(
        f"Mostrando **{len(df_filtrado):,} modelos** que suman **{pares_en_tabla:,} Pares Seguros**."
    )

    if df_filtrado.empty:
        st.info("No hay modelos que coincidan con tu búsqueda.")
    else:
        columnas_ranking = [
            "id_ps", "nombre_modelo", "tier", "costo_socio",
            "stock_total_anterior", "stock_publicable_total", "tallas_seguras",
            "efectivo_total_ml", "velocidad_diaria", "dias_cobertura", "url_real",
        ]
        st.dataframe(
            df_filtrado[columnas_ranking],
            width="stretch",
            height=480,
            hide_index=True,
            column_config={
                "id_ps": st.column_config.TextColumn("SKU", width="small"),
                "nombre_modelo": st.column_config.TextColumn("Modelo", width="medium"),
                "tier": st.column_config.TextColumn("Tier", width="small"),
                "costo_socio": st.column_config.NumberColumn("Costo Socio", format="$%.2f"),
                "stock_total_anterior": st.column_config.NumberColumn("Físico Vallejo", format="%d"),
                "stock_publicable_total": st.column_config.NumberColumn("🛡️ Pares Seguros", format="%d"),
                "tallas_seguras": st.column_config.TextColumn("Tallas Seguras (Cuáles son)", width="large"),
                "efectivo_total_ml": st.column_config.NumberColumn("💰 Efectivo Potencial ML", format="$%.0f"),
                "velocidad_diaria": st.column_config.ProgressColumn(
                    "Rotación (pares/día)", format="%.2f", min_value=0.0, max_value=vel_maxima
                ),
                "dias_cobertura": st.column_config.NumberColumn("Días Cobertura", format="%.1f"),
                "url_real": st.column_config.LinkColumn("Catálogo", display_text="Abrir"),
            },
        )

# --- PESTAÑA 2: FINANZAS ---
with tab_finanzas:
    st.subheader("Simulador de Precios y Ganancia Libre por Canal")
    df_fin = df_filtrado[df_filtrado["costo_socio"] > 0]

    if df_fin.empty:
        st.info("No hay modelos con costo de socio en la vista actual.")
    else:
        columnas_finanzas = [
            "id_ps", "nombre_modelo", "stock_publicable_total", "costo_socio",
            "ref_competencia_x2", "precio_piso_ml", "precio_ml", "utilidad_neta_ml",
            "efectivo_total_ml", "fondo_merma_ml", "iva_a_favor_ml",
            "precio_amazon", "utilidad_neta_amazon", "precio_tiktok", "utilidad_neta_tiktok",
        ]
        st.dataframe(
            df_fin[columnas_finanzas],
            width="stretch",
            height=480,
            hide_index=True,
            column_config={
                "id_ps": "SKU",
                "nombre_modelo": "Modelo",
                "stock_publicable_total": st.column_config.NumberColumn("🛡️ Pares Seguros", format="%d"),
                "costo_socio": st.column_config.NumberColumn("Costo Socio", format="$%.2f"),
                "ref_competencia_x2": st.column_config.NumberColumn("Ref. Rival (x2)", format="$%.2f"),
                "precio_piso_ml": st.column_config.NumberColumn("Piso ML", format="$%.2f"),
                "precio_ml": st.column_config.NumberColumn("Precio ML", format="$%.2f"),
                "utilidad_neta_ml": st.column_config.NumberColumn("💵 Ganancia/Par ML", format="$%.2f"),
                "efectivo_total_ml": st.column_config.NumberColumn("💰 Ganancia Total ML", format="$%.0f"),
                "fondo_merma_ml": st.column_config.NumberColumn(f"🛡️ Merma ({MERMA_PCT}%)", format="$%.2f"),
                "iva_a_favor_ml": st.column_config.NumberColumn("🏛️ IVA a Favor", format="+$%.2f"),
                "precio_amazon": st.column_config.NumberColumn("Precio Amazon", format="$%.2f"),
                "utilidad_neta_amazon": st.column_config.NumberColumn("💵 Ganancia Amazon", format="$%.2f"),
                "precio_tiktok": st.column_config.NumberColumn("Precio TikTok", format="$%.2f"),
                "utilidad_neta_tiktok": st.column_config.NumberColumn("💵 Ganancia TikTok", format="$%.2f"),
            },
        )

# --- PESTAÑA 3: TALLAS POR MODELO ---
with tab_tallas:
    st.subheader("Existencias por Talla (Físico en Tienda vs. Seguro para Publicar)")

    # Mostrar primero los que tienen stock seguro para no buscar a ciegas
    base_tallas = df_filtrado if not df_filtrado.empty else df_catalogo
    opciones_df = base_tallas.head(MAX_OPCIONES_SELECTOR)
    mapa_nombres = dict(zip(opciones_df["id_ps"], opciones_df["_etiqueta_menu"]))

    sku_elegido = st.selectbox(
        "Elige un modelo para ver sus tallas:",
        options=list(mapa_nombres.keys()),
        format_func=mapa_nombres.get,
        key="sku_tallas",
    )

    if sku_elegido:
        info_sku = df_catalogo[df_catalogo["id_ps"] == sku_elegido].iloc[0]

        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Costo Socio", f"${info_sku['costo_socio']:,.2f}")
        m2.metric("Precio Mercado Libre", f"${info_sku['precio_ml']:,.2f}")
        m3.metric("Stock Físico Vallejo", f"{int(info_sku['stock_total_anterior']):,} pares")
        m4.metric("Pares Seguros", f"{int(info_sku['stock_publicable_total']):,} pares")

        df_tallas_sku = cargar_tallas_sku(str(RUTA_BD), MTIME_BD, sku_elegido)
        if df_tallas_sku.empty:
            st.info("Este modelo aún no tiene historial de tallas registrado.")
        else:
            col_grafica, col_tabla = st.columns(2)
            with col_grafica:
                st.markdown("**Comparativa Visual por Talla**")
                st.bar_chart(
                    df_tallas_sku,
                    x="talla",
                    y=["stock_vallejo", "stock_seguro_publicable"],
                    height=320,
                )
            with col_tabla:
                st.markdown("**Pares Exactos por Talla**")
                st.dataframe(
                    df_tallas_sku[["talla", "stock_vallejo", "stock_seguro_publicable"]],
                    hide_index=True,
                    width="stretch",
                    column_config={
                        "talla": "Talla MX",
                        "stock_vallejo": st.column_config.NumberColumn("Físico en Vallejo", format="%d pares"),
                        "stock_seguro_publicable": st.column_config.NumberColumn(
                            "🛡️ Publicar en Línea", format="%d pares"
                        ),
                    },
                )

# --- PESTAÑA 4: ESTUDIO FOTOGRÁFICO ---
with tab_estudio:
    st.subheader("Galería de Fotos Lista para Publicar")
    solo_con_fotos = st.checkbox("Mostrar solo modelos con fotos listas", value=True)

    df_fotos = df_catalogo[df_catalogo["costo_socio"] > 0]
    if solo_con_fotos and not df_fotos[df_fotos["estudio_foto_listo"] == 1].empty:
        df_fotos = df_fotos[df_fotos["estudio_foto_listo"] == 1]

    if df_fotos.empty:
        st.info("Aún no hay modelos con costo o fotos procesadas.")
    else:
        opciones_fotos = df_fotos.head(MAX_OPCIONES_SELECTOR)
        mapa_fotos = dict(zip(opciones_fotos["id_ps"], opciones_fotos["_etiqueta_menu"]))

        sku_foto = st.selectbox(
            "Elige un modelo para revisar sus fotos:",
            options=list(mapa_fotos.keys()),
            format_func=mapa_fotos.get,
            key="sku_fotos",
        )

        archivos_img = obtener_fotos_sku(sku_foto) if sku_foto else []
        if not archivos_img:
            st.warning(f"No hay imágenes en `{DIR_CATALOGO / str(sku_foto)}`.")
        else:
            columnas_img = st.columns(min(4, len(archivos_img)))
            for i, ruta_img in enumerate(archivos_img):
                with columnas_img[i % len(columnas_img)]:
                    st.image(str(ruta_img), caption=ruta_img.name, width="stretch")

# --- PESTAÑA 5: EXPORTAR A EXCEL / CSV ---
with tab_export:
    st.subheader("Descargar Tabla Filtrada")
    if df_filtrado.empty:
        st.info("No hay datos filtrados para exportar.")
    else:
        st.caption(f"Se descargarán los **{len(df_filtrado):,} modelos** que tienes filtrados actualmente.")
        columnas_ocultas = ["_etiqueta_menu", "_texto_busqueda"]
        csv_bytes = df_filtrado.drop(columns=columnas_ocultas).to_csv(index=False).encode("utf-8-sig")

        st.download_button(
            label="📥 Descargar Reporte en CSV (Para Excel)",
            data=csv_bytes,
            file_name=f"reporte_vallejo_{datetime.now():%Y%m%d_%H%M}.csv",
            mime="text/csv",
        )