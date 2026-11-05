"""
========================================================================================
PASO 4: PANEL DE CONTROL E INVENTARIO EN STREAMLIT (v2 · más filtros, caché y selección)
========================================================================================
"""

from __future__ import annotations

import re
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import numpy as np
import pandas as pd
import streamlit as st

st.set_page_config(
    page_title="Zapatería Vallejo · Control e Inventario",
    page_icon="👟",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ----------------------------------------------------------------------------------
# CONFIGURACIÓN
# ----------------------------------------------------------------------------------
RUTA_BD: Path = Path("data/zapateria_vallejo.db")
DIR_CATALOGO_LISTO: Path = Path("data/catalogo_listo")
DIR_DATA: Path = Path("data")

SIN_CATALOGO = "Sin Catálogo (Web)"
RECOMENDADO = "✅ RECOMENDADO"
NO_RECOMENDADO = "⚠️ NO RECOMENDADO"

FOTOS_REQUERIDAS = 4
STOCK_MIN_DEFAULT = 5
GANANCIA_MIN_DEFAULT = 100.0

# Fórmula de precio (antes eran números "mágicos" dentro de la función)
FACTOR_COSTO = 1.95
CARGO_FIJO_PRECIO = 60.0
FACTOR_NETO_VENTA = 0.7195  # lo que queda del precio tras comisiones
CARGO_FIJO_GANANCIA = 111.40
MARGEN_MINIMO_SOBRE_COSTO = 180

TABLAS_FINANCIERAS = (
    "matriz_financiera",
    "auditoria_financiera",
    "catalogo_financiero",
    "productos_auditados",
    "unit_economics",
)

# Alias de columnas aceptados (se resuelven una sola vez, sin importar mayúsculas)
ALIAS: Dict[str, List[str]] = {
    "id": ["ID Price Shoes", "id_ps", "id", "sku"],
    "sku": ["SKU Interno", "sku_interno"],
    "modelo": ["Modelo", "nombre_modelo", "nombre"],
    "enlace": ["Enlace Original", "url_real", "url"],
    "costo": ["Costo ($)", "costo_socio", "costo"],
    "stock": ["Stock Publicable", "stock_publicable", "stock_total_anterior", "stock"],
    "precio": ["Precio Sugerido", "precio_sugerido", "precio_venta"],
    "ganancia": ["Ganancia Neta", "ganancia_neta", "utilidad_neta"],
    "catalogo": ["Catálogo", "origen_catalogo"],
    "subir": ["Subir a ML", "subir_ml"],
}

NOMBRES_CATALOGOS: Dict[str, str] = {
    "1342200": "1342200 - Caballeros",
    "1345253": "1345253 - Botas",
    "1346256": "1346256 - Urbano",
    "1346284": "1346284 - Vestir Casual",
    "1348733": "1348733 - Abrigador",
    "1348737": "1348737 - Vestir",
    "1348749": "1348749 - NBA",
    "1348753": "1348753 - NFL",
    "1348757": "1348757 - Man",
    "1348779": "1348779 - Accesorios",
    "1348783": "1348783 - Kids",
    "1348800": "1348800 - Licencias",
    "1348804": "1348804 - Pijamas",
    "1352311": "1352311 - Seven Jeans",
    "1353556": "1353556 - Calzado y Accesorios",
    "1353557": "1353557 - Juvenil",
    "1353843": "1353843 - Confort",
    "1368789": "1368789 - Sandalias",
    "1368800": "1368800 - Prokennex Mini",
    "1368804": "1368804 - Pop Universe Mini",
    "1368805": "1368805 - Urban Lifestyle Mini",
    "1370231": "1370231 - Escolar",
    "1376435": "1376435 - Todo en Uno",
    "1377702": "1377702 - Exclusivos",
    "1378033": "1378033 - Kids Todo en Uno",
    "1380328": "1380328 - Botas Largas",
    "1380332": "1380332 - Goodyear Botas",
    "1380337": "1380337 - Coleccion Thalia",
    "1380341": "1380341 - Belinda Fashion & Chic",
    "1380346": "1380346 - Navidad",
    "1380350": "1380350 - Navidad Home",
    "1380354": "1380354 - Reebok Impulsados",
    "1380358": "1380358 - Importados Fall Ofertas",
    "1380359": "1380359 - Botas Kits",
    "1380363": "1380363 - Basicos Botines",
    "1380367": "1380367 - Botas Texano",
    "1380516": "1380516 - Oportunidades Urbano",
    "1380996": "1380996 - Love to Lounge",
    "1381316": "1381316 - Med Chef",
    "1381338": "1381338 - Jeans",
    "1381342": "1381342 - Ella",
    "1381346": "1381346 - Basicos OID6",
    "1381350": "1381350 - Mushu Esenciales Home",
    "1381556": "1381556 - Navidad Kids",
}

ETIQUETAS_LAMINAS = ["Portada Blanca", "Ángulo de Detalle", "Ficha de Beneficios", "Guía de Tallas (CM)"]

ORIGEN_PDF = "📘 Solo catálogos PDF"
ORIGEN_TODO = "🌐 Todo (incluye Web)"
ORIGEN_WEB = "🕸️ Solo Web / sin catálogo"

ORDEN_DEFAULT = "Recomendados primero + mayor ganancia"
OPCIONES_ORDEN = [
    ORDEN_DEFAULT,
    "Ganancia Neta",
    "Margen (%)",
    "Precio Sugerido",
    "Costo ($)",
    "Stock Publicable",
    "ID Price Shoes",
    "Modelo",
]

FILTRO_KEYS = [
    "f_origen", "f_catalogos", "f_semaforo", "f_busqueda", "f_costo", "f_precio",
    "f_ganancia", "f_margen", "f_stock", "f_fotos", "f_orden", "f_asc",
]


# ----------------------------------------------------------------------------------
# UTILIDADES
# ----------------------------------------------------------------------------------
def formatear_nombre_catalogo(valor_origen: Any) -> str:
    """Traduce '1342200.pdf' o '1342200_2.pdf' a '1342200 - Caballeros'."""
    if not valor_origen:
        return SIN_CATALOGO
    texto = str(valor_origen).strip()
    if not texto or texto.lower() in ("none", "nan", "null"):
        return SIN_CATALOGO
    m = re.search(r"(\d{7})", texto)
    if m and m.group(1) in NOMBRES_CATALOGOS:
        return NOMBRES_CATALOGOS[m.group(1)]
    limpio = texto.replace(".pdf", "").replace(".PDF", "").strip()
    return NOMBRES_CATALOGOS.get(limpio, texto)


def limpiar_id(valor: Any) -> str:
    s = str(valor).strip().replace("PS-", "")
    return s.lstrip("0") or s


def claves_id(valor: Any) -> Set[str]:
    """Todas las formas en que puede aparecer un mismo ID (con ceros, sin ceros, PS-xxx)."""
    s = str(valor).strip()
    limpio = limpiar_id(s)
    return {s, limpio, f"PS-{limpio}"}


def calcular_precio_y_ganancia_fallback(costo: float) -> Tuple[float, float]:
    """Calcula Precio Sugerido y Ganancia Neta si aún no están precalculados."""
    if costo <= 0:
        return 0.0, 0.0
    precio_bruto = costo * FACTOR_COSTO + CARGO_FIJO_PRECIO
    decena = round(precio_bruto / 10.0) * 10 - 1
    precio = float(max(decena, int(costo + MARGEN_MINIMO_SOBRE_COSTO)))
    ganancia = round(precio * FACTOR_NETO_VENTA - costo - CARGO_FIJO_GANANCIA, 2)
    return precio, ganancia


def firma_fuentes() -> Tuple[float, ...]:
    """Fechas de modificación de BD, CSV y carpetas de fotos: invalida la caché si algo cambia."""
    rutas: List[Path] = [RUTA_BD, DIR_CATALOGO_LISTO]
    if DIR_DATA.exists():
        rutas += list(DIR_DATA.glob("*.csv"))
    if DIR_CATALOGO_LISTO.exists():
        rutas += [c for c in DIR_CATALOGO_LISTO.iterdir() if c.is_dir()]
    return tuple(p.stat().st_mtime for p in rutas if p.exists())


# --- Lectura de columnas con alias (vectorizada) -------------------------------------
def _col(df: pd.DataFrame, campo: str) -> Optional[str]:
    minusculas = {c.lower(): c for c in df.columns}
    for alias in ALIAS[campo]:
        if alias.lower() in minusculas:
            return minusculas[alias.lower()]
    return None


def _texto(df: pd.DataFrame, campo: str, default: Any = "") -> pd.Series:
    c = _col(df, campo)
    if c is None:
        return pd.Series(default, index=df.index, dtype="object")
    return df[c].fillna("").astype(str).str.strip()


def _num(df: pd.DataFrame, campo: str, default: Any = 0.0) -> pd.Series:
    c = _col(df, campo)
    if c is None:
        return default if isinstance(default, pd.Series) else pd.Series(float(default), index=df.index)
    limpio = df[c].astype(str).str.replace(r"[$,\s]", "", regex=True)
    return pd.to_numeric(limpio, errors="coerce").fillna(default)


# ----------------------------------------------------------------------------------
# CARGA DE DATOS (con caché)
# ----------------------------------------------------------------------------------
@st.cache_data(show_spinner=False)
def indexar_carpetas_fotos(firma: Tuple[float, ...]) -> Dict[str, List[Path]]:
    """Devuelve {id: [lámina1..lámina4]} escaneando `data/catalogo_listo`."""
    mapa: Dict[str, List[Path]] = {}
    if not DIR_CATALOGO_LISTO.exists():
        return mapa

    for carpeta in DIR_CATALOGO_LISTO.iterdir():
        if not carpeta.is_dir():
            continue
        m = re.search(r"(\d{5,8})", carpeta.name)
        id_carpeta = m.group(1) if m else carpeta.name

        laminas: List[Path] = []
        for prefijo in ("01", "02", "03", "04"):
            candidatas = sorted(carpeta.glob(f"{prefijo}*.jpg")) + sorted(carpeta.glob(f"{prefijo}*.png"))
            if candidatas:
                laminas.append(candidatas[0])

        if laminas:
            for k in claves_id(id_carpeta):
                mapa[k] = laminas
    return mapa


def leer_fuentes() -> Tuple[Optional[pd.DataFrame], Dict[str, str]]:
    """Lee SQLite y CSV. Devuelve (df_base, mapa_origen_catalogo)."""
    mapa_origen: Dict[str, str] = {}
    df_base: Optional[pd.DataFrame] = None

    if RUTA_BD.exists():
        with closing(sqlite3.connect(RUTA_BD)) as conn:
            tablas = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}

            if "zapatos_candidatos" in tablas:
                cols = {c[1] for c in conn.execute("PRAGMA table_info(zapatos_candidatos)")}
                if "origen_catalogo" in cols:
                    for id_p, orig in conn.execute(
                        "SELECT id_ps, COALESCE(origen_catalogo, '') FROM zapatos_candidatos"
                    ):
                        nombre = formatear_nombre_catalogo(orig)
                        for k in claves_id(id_p):
                            mapa_origen[k] = nombre

            for tabla in TABLAS_FINANCIERAS:  # nombres fijos de la constante: seguro
                if tabla in tablas:
                    try:
                        df_tmp = pd.read_sql_query(f"SELECT * FROM {tabla}", conn)
                    except Exception:
                        continue
                    if not df_tmp.empty:
                        df_base = df_tmp
                        break

            if df_base is None and "zapatos_candidatos" in tablas:
                df_tmp = pd.read_sql_query("SELECT * FROM zapatos_candidatos", conn)
                if not df_tmp.empty:
                    df_base = df_tmp

    if DIR_DATA.exists():
        for csv_path in sorted(DIR_DATA.glob("*.csv")):
            if not any(k in csv_path.name.lower() for k in ("financ", "matriz", "audit", "catalogo", "ml")):
                continue
            try:
                df_csv = pd.read_csv(csv_path)
            except Exception:
                continue
            if df_csv.empty or len(df_csv.columns) < 5:
                continue
            if df_base is None or _col(df_csv, "precio") is not None:
                df_base = df_csv
                break

    return df_base, mapa_origen


@st.cache_data(show_spinner="Cargando inventario…")
def cargar_datos_inventario(firma: Tuple[float, ...]) -> pd.DataFrame:
    """Inventario auditado cruzado con catálogo de origen y fotografías HD."""
    df_base, mapa_origen = leer_fuentes()
    if df_base is None or df_base.empty:
        return pd.DataFrame()

    mapa_fotos = indexar_carpetas_fotos(firma)
    df = df_base.reset_index(drop=True)

    raw_id = _texto(df, "id")
    id_limpio = raw_id.str.replace("PS-", "", regex=False).str.lstrip("0")
    id_limpio = id_limpio.where(id_limpio != "", raw_id)

    num_fotos = pd.Series(
        [len(mapa_fotos.get(i) or mapa_fotos.get(r) or []) for i, r in zip(id_limpio, raw_id)],
        index=df.index,
    )
    costo = _num(df, "costo", 0.0)

    # Descarta filas sin ID o sin costo ni fotos
    valido = (id_limpio != "") & ~((costo <= 0) & (num_fotos == 0))
    df, raw_id, id_limpio, num_fotos, costo = (x[valido] for x in (df, raw_id, id_limpio, num_fotos, costo))
    if df.empty:
        return pd.DataFrame()

    pares = [calcular_precio_y_ganancia_fallback(c) for c in costo]
    precio_fb = pd.Series([p for p, _ in pares], index=df.index)
    ganancia_fb = pd.Series([g for _, g in pares], index=df.index)
    precio = _num(df, "precio", precio_fb)
    ganancia = _num(df, "ganancia", ganancia_fb)
    stock = _num(df, "stock", 0).astype(int).clip(lower=0)

    sku = _texto(df, "sku")
    sku = pd.Series(["PS-" + i if not s.startswith("PS-") else s for s, i in zip(sku, id_limpio)], index=df.index)

    modelo = _texto(df, "modelo")
    modelo = modelo.where(modelo != "", "Modelo " + id_limpio)

    origen_raw = _texto(df, "catalogo")
    catalogo = pd.Series(
        [mapa_origen.get(i) or mapa_origen.get(r) or formatear_nombre_catalogo(o)
         for i, r, o in zip(id_limpio, raw_id, origen_raw)],
        index=df.index,
    )

    col_subir = _col(df, "subir")
    if col_subir:
        subir_src = df[col_subir].astype(str).str.strip().str.lower().isin(["true", "1", "si", "sí", "x", "yes"])
        subir_src = subir_src.astype("boolean")
    else:
        subir_src = pd.Series(pd.NA, index=df.index, dtype="boolean")

    out = pd.DataFrame(
        {
            "ID Price Shoes": id_limpio,
            "SKU Interno": sku,
            "Catálogo": catalogo,
            "Modelo": modelo,
            "Enlace Original": _texto(df, "enlace"),
            "Costo ($)": costo.round(2),
            "Precio Sugerido": precio.round(2),
            "Ganancia Neta": ganancia.round(2),
            "Margen (%)": np.where(precio > 0, ganancia / precio * 100, 0.0).round(1),
            "Stock Publicable": stock,
            "_num_fotos": num_fotos,
            "_subir_src": subir_src,
        }
    )
    # Un mismo ID puede venir repetido: nos quedamos con la fila de mayor ganancia
    out = out.sort_values("Ganancia Neta", ascending=False).drop_duplicates("ID Price Shoes")
    return out.reset_index(drop=True)


def evaluar_semaforo(df: pd.DataFrame, min_stock: int, min_ganancia: float) -> pd.DataFrame:
    """Calcula Semáforo y Diagnóstico con umbrales configurables."""
    df = df.copy()
    semaforo: List[str] = []
    diagnostico: List[str] = []
    for fotos, stock, gan in zip(df["_num_fotos"], df["Stock Publicable"], df["Ganancia Neta"]):
        if fotos >= FOTOS_REQUERIDAS and stock >= min_stock and gan >= min_ganancia:
            semaforo.append(RECOMENDADO)
            diagnostico.append(f"✅ Listo para publicar ({FOTOS_REQUERIDAS}/{FOTOS_REQUERIDAS} HD)")
        elif fotos < FOTOS_REQUERIDAS:
            semaforo.append(NO_RECOMENDADO)
            diagnostico.append(f"Faltan fotografías HD ({FOTOS_REQUERIDAS - fotos}/{FOTOS_REQUERIDAS})")
        elif stock < min_stock:
            semaforo.append(NO_RECOMENDADO)
            diagnostico.append(f"Stock bajo ({stock} pares) · Fotos ({fotos}/{FOTOS_REQUERIDAS})")
        else:
            semaforo.append(NO_RECOMENDADO)
            diagnostico.append(f"Margen ajustado · Fotos ({fotos}/{FOTOS_REQUERIDAS})")
    df["Semáforo"] = semaforo
    df["Diagnóstico Técnico"] = diagnostico
    return df


# ----------------------------------------------------------------------------------
# SELECCIÓN "SUBIR A ML" (persistente)
# ----------------------------------------------------------------------------------
def cargar_seleccion_guardada() -> Optional[Set[str]]:
    if not RUTA_BD.exists():
        return None
    with closing(sqlite3.connect(RUTA_BD)) as conn:
        try:
            return {str(r[0]) for r in conn.execute("SELECT id_ps FROM seleccion_ml")}
        except sqlite3.Error:
            return None


def guardar_seleccion(ids: Set[str]) -> None:
    with closing(sqlite3.connect(RUTA_BD)) as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS seleccion_ml ("
            "id_ps TEXT PRIMARY KEY, actualizado TEXT DEFAULT CURRENT_TIMESTAMP)"
        )
        conn.execute("DELETE FROM seleccion_ml")
        conn.executemany("INSERT INTO seleccion_ml (id_ps) VALUES (?)", [(i,) for i in sorted(ids)])
        conn.commit()


def inicializar_estado(df_total: pd.DataFrame) -> None:
    if "seleccion_ml" not in st.session_state:
        guardada = cargar_seleccion_guardada()
        if guardada is not None:
            st.session_state.seleccion_ml = guardada
        else:
            marcados = df_total["_subir_src"].fillna(
                df_total["_num_fotos"].ge(FOTOS_REQUERIDAS)
                & df_total["Stock Publicable"].ge(STOCK_MIN_DEFAULT)
                & df_total["Ganancia Neta"].ge(GANANCIA_MIN_DEFAULT)
            )
            st.session_state.seleccion_ml = set(df_total.loc[marcados.astype(bool), "ID Price Shoes"])
    st.session_state.setdefault("ver_editor", 0)


def resetear_filtros() -> None:
    for k in FILTRO_KEYS:
        st.session_state.pop(k, None)


# ----------------------------------------------------------------------------------
# FILTROS (barra lateral)
# ----------------------------------------------------------------------------------
def slider_rango(etiqueta: str, serie: pd.Series, key: str, formato: str = "%.0f") -> Optional[Tuple[float, float]]:
    lo, hi = float(serie.min()), float(serie.max())
    if not np.isfinite(lo) or lo >= hi:
        return None
    lo, hi = float(np.floor(lo)), float(np.ceil(hi))
    return st.sidebar.slider(etiqueta, lo, hi, (lo, hi), format=formato, key=key)


def aplicar_filtros(df_total: pd.DataFrame) -> pd.DataFrame:
    sb = st.sidebar
    sb.header("🔎 Filtros de Auditoría")
    sb.button("🧹 Limpiar filtros", on_click=resetear_filtros, use_container_width=True)

    df = df_total

    # --- Origen y catálogos --------------------------------------------------------
    conteo = df[df["Catálogo"] != SIN_CATALOGO]["Catálogo"].value_counts()
    tiene_pdfs = not conteo.empty
    origen = sb.radio(
        "Origen del producto",
        [ORIGEN_PDF, ORIGEN_TODO, ORIGEN_WEB],
        index=0 if tiene_pdfs else 1,
        key="f_origen",
    )
    if origen == ORIGEN_PDF and tiene_pdfs:
        df = df[df["Catálogo"] != SIN_CATALOGO]
    elif origen == ORIGEN_WEB:
        df = df[df["Catálogo"] == SIN_CATALOGO]

    oficiales = list(NOMBRES_CATALOGOS.values())
    opciones_cat = [c for c in oficiales if c in conteo.index] + [c for c in conteo.index if c not in oficiales]
    elegidos = sb.multiselect(
        "📘 Catálogos (vacío = todos)",
        opciones_cat,
        format_func=lambda c: f"{c} ({conteo[c]} SKUs)",
        key="f_catalogos",
    )
    if elegidos:
        df = df[df["Catálogo"].isin(elegidos)]

    # --- Semáforo y búsqueda -------------------------------------------------------
    filtro_sem = sb.radio(
        "🚦 Semáforo",
        ["Todos", "✅ Solo Recomendados", "⚠️ Con Observaciones / Pendientes"],
        key="f_semaforo",
    )
    if filtro_sem == "✅ Solo Recomendados":
        df = df[df["Semáforo"] == RECOMENDADO]
    elif filtro_sem != "Todos":
        df = df[df["Semáforo"] != RECOMENDADO]

    busqueda = sb.text_input("🔍 Buscar (ID, SKU, modelo o catálogo)", key="f_busqueda").strip().lower()
    if busqueda:
        pajar = (
            df["ID Price Shoes"].astype(str) + " " + df["SKU Interno"].astype(str) + " "
            + df["Modelo"].astype(str) + " " + df["Catálogo"].astype(str)
        ).str.lower()
        mask = pd.Series(True, index=df.index)
        for termino in busqueda.split():  # todas las palabras deben aparecer
            mask &= pajar.str.contains(termino, regex=False)
        df = df[mask]

    # --- Rangos numéricos (se calculan sobre el total para que no "salten") ---------
    sb.markdown("**💲 Costos, precios y márgenes**")
    r = slider_rango("Costo ($)", df_total["Costo ($)"], "f_costo")
    if r:
        df = df[df["Costo ($)"].between(*r)]
    r = slider_rango("Precio sugerido ($)", df_total["Precio Sugerido"], "f_precio")
    if r:
        df = df[df["Precio Sugerido"].between(*r)]
    r = slider_rango("Ganancia neta ($)", df_total["Ganancia Neta"], "f_ganancia", "%.0f")
    if r:
        df = df[df["Ganancia Neta"].between(*r)]
    r = slider_rango("Margen (%)", df_total["Margen (%)"], "f_margen", "%.0f%%")
    if r:
        df = df[df["Margen (%)"].between(*r)]

    max_stock = int(df_total["Stock Publicable"].max())
    if max_stock > 0:
        min_stock_f = sb.slider("Stock mínimo (pares)", 0, max_stock, 0, key="f_stock")
        df = df[df["Stock Publicable"] >= min_stock_f]

    rango_fotos = sb.select_slider(
        "📸 Fotos HD disponibles", options=list(range(FOTOS_REQUERIDAS + 1)),
        value=(0, FOTOS_REQUERIDAS), key="f_fotos",
    )
    df = df[df["_num_fotos"].between(*rango_fotos)]

    # --- Orden ---------------------------------------------------------------------
    orden = sb.selectbox("↕️ Ordenar por", OPCIONES_ORDEN, key="f_orden")
    ascendente = sb.checkbox("Orden ascendente", value=False, key="f_asc")
    if orden == ORDEN_DEFAULT:
        df = (
            df.assign(_r=df["Semáforo"] != RECOMENDADO)
            .sort_values(["_r", "Ganancia Neta", "Stock Publicable"], ascending=[True, False, False])
            .drop(columns="_r")
        )
    else:
        df = df.sort_values(orden, ascending=ascendente)

    return df.reset_index(drop=True)


# ----------------------------------------------------------------------------------
# PANEL
# ----------------------------------------------------------------------------------
def renderizar_panel() -> None:
    st.title("👟 Zapatería Vallejo · Centro de Control e Inventario")

    firma = firma_fuentes()
    df_cargado = cargar_datos_inventario(firma)

    if df_cargado.empty:
        st.warning(
            "⚠️ Aún no se encontraron artículos con costo auditado en `data/zapateria_vallejo.db` "
            "o carpetas en `data/catalogo_listo`. Ejecuta primero `python pipeline_catalogo.py`."
        )
        return

    inicializar_estado(df_cargado)

    # Umbrales del semáforo configurables
    with st.sidebar.expander("⚙️ Criterios del semáforo", expanded=False):
        min_stock = st.number_input("Stock mínimo para recomendar", 0, 500, STOCK_MIN_DEFAULT, 1)
        min_ganancia = st.number_input("Ganancia neta mínima ($)", 0.0, 5000.0, GANANCIA_MIN_DEFAULT, 10.0)
    df_total = evaluar_semaforo(df_cargado, int(min_stock), float(min_ganancia))

    df = aplicar_filtros(df_total)

    if st.sidebar.button("🔄 Recargar datos y fotos HD", use_container_width=True):
        st.cache_data.clear()
        resetear_filtros()
        st.rerun()

    # --- MÉTRICAS ------------------------------------------------------------------
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("📦 SKUs en vista", f"{len(df):,}", help=f"de {len(df_total):,} en total")
    c2.metric("✅ Recomendados", f"{int((df['Semáforo'] == RECOMENDADO).sum()):,}")
    c3.metric("📸 Con carrusel HD (4/4)", f"{int((df['_num_fotos'] >= FOTOS_REQUERIDAS).sum()):,}")
    c4.metric("💰 Ganancia neta promedio", f"${df['Ganancia Neta'].mean() if not df.empty else 0:,.2f}")

    st.markdown("---")

    # --- MATRIZ --------------------------------------------------------------------
    st.subheader("📋 Matriz de Control e Inventario")

    if df.empty:
        st.warning("No hay artículos que coincidan con los filtros seleccionados.")
        return

    b1, b2, _ = st.columns([1, 1, 4])
    ids_vista = set(df["ID Price Shoes"])
    if b1.button("☑️ Marcar visibles", use_container_width=True):
        st.session_state.seleccion_ml |= ids_vista
        st.session_state.ver_editor += 1
    if b2.button("⬜ Desmarcar visibles", use_container_width=True):
        st.session_state.seleccion_ml -= ids_vista
        st.session_state.ver_editor += 1
    st.caption(
        "La selección se conserva al cambiar de filtros. Usa **Guardar selección** para no perderla al recargar la página."
    )

    columnas = [
        "Subir a ML", "ID Price Shoes", "SKU Interno", "Catálogo", "Modelo", "Enlace Original",
        "Semáforo", "Costo ($)", "Precio Sugerido", "Ganancia Neta", "Margen (%)",
        "Stock Publicable", "Diagnóstico Técnico",
    ]
    df_editor = df.assign(**{"Subir a ML": df["ID Price Shoes"].isin(st.session_state.seleccion_ml)})[columnas]

    editado = st.data_editor(
        df_editor,
        use_container_width=True,
        hide_index=True,
        height=400,
        column_config={
            "Subir a ML": st.column_config.CheckboxColumn("Subir a ML", default=False),
            "Enlace Original": st.column_config.LinkColumn("Enlace Original", display_text="Ver en PS"),
            "Costo ($)": st.column_config.NumberColumn("Costo ($)", format="$%.0f"),
            "Precio Sugerido": st.column_config.NumberColumn("Precio Sugerido", format="$%.0f"),
            "Ganancia Neta": st.column_config.NumberColumn("Ganancia Neta", format="$%.2f"),
            "Margen (%)": st.column_config.NumberColumn("Margen (%)", format="%.1f%%"),
        },
        disabled=[c for c in columnas if c != "Subir a ML"],
        key=f"editor_{st.session_state.ver_editor}_{hash(tuple(df['ID Price Shoes']))}",
    )

    # Sincroniza las casillas con el estado persistente
    for id_, marcado in zip(editado["ID Price Shoes"], editado["Subir a ML"]):
        if marcado:
            st.session_state.seleccion_ml.add(id_)
        else:
            st.session_state.seleccion_ml.discard(id_)

    # --- ACCIONES SOBRE LA SELECCIÓN -----------------------------------------------
    seleccion = df_total[df_total["ID Price Shoes"].isin(st.session_state.seleccion_ml)]
    a1, a2, a3 = st.columns([2, 1, 1])
    a1.markdown(
        f"**🛒 Seleccionados para ML:** {len(seleccion):,} SKUs · "
        f"{int((seleccion['Semáforo'] != RECOMENDADO).sum())} con observaciones"
    )
    a2.download_button(
        "⬇️ Descargar selección (CSV)",
        data=seleccion.drop(columns=["_num_fotos", "_subir_src"]).to_csv(index=False).encode("utf-8-sig"),
        file_name="seleccion_mercadolibre.csv",
        mime="text/csv",
        disabled=seleccion.empty,
        use_container_width=True,
    )
    if a3.button("💾 Guardar selección", use_container_width=True):
        try:
            guardar_seleccion(st.session_state.seleccion_ml)
            st.toast("Selección guardada en la base de datos ✅")
        except sqlite3.Error as e:
            st.error(f"No se pudo guardar la selección: {e}")

    st.markdown("---")

    # --- GALERÍA -------------------------------------------------------------------
    st.subheader("📸 Galería de Fotografías HD")

    mapa_fotos = indexar_carpetas_fotos(firma)
    df_fotos = df[df["_num_fotos"] > 0]

    if df_fotos.empty:
        st.info(
            "Aún no hay carruseles generados para los SKUs de este filtro. "
            "Ejecuta `python paso6_estudio_fotografico.py` para generar sus 4 láminas HD."
        )
        return

    filas = df_fotos.set_index("ID Price Shoes")
    id_sel = st.selectbox(
        "Selecciona un producto para inspeccionar sus 4 láminas HD:",
        options=list(filas.index),
        format_func=lambda i: f"{i} · {filas.loc[i, 'Modelo']} ({filas.loc[i, 'Catálogo']})",
    )
    fila = filas.loc[id_sel]
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Costo", f"${fila['Costo ($)']:,.0f}")
    m2.metric("Precio sugerido", f"${fila['Precio Sugerido']:,.0f}")
    m3.metric("Ganancia neta", f"${fila['Ganancia Neta']:,.2f}", f"{fila['Margen (%)']:.1f}%")
    m4.metric("Stock", f"{int(fila['Stock Publicable'])} pares")

    laminas = mapa_fotos.get(id_sel) or mapa_fotos.get(f"PS-{id_sel}") or []
    columnas_img = st.columns(4)
    for i, col in enumerate(columnas_img):
        with col:
            if i < len(laminas) and laminas[i].exists():
                st.image(str(laminas[i]), caption=ETIQUETAS_LAMINAS[i], use_container_width=True)
            else:
                st.warning(f"Falta lámina {i + 1}: {ETIQUETAS_LAMINAS[i]}")


if __name__ == "__main__":
    renderizar_panel()