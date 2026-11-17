# -*- coding: utf-8 -*-
"""
========================================================================================
PASO 4: PANEL DE CONTROL E INVENTARIO EN STREAMLIT (v2 · Matriz Maslow + Multi-Tienda)
========================================================================================
"""

from __future__ import annotations

from contextlib import closing
from pathlib import Path
import re
import sqlite3
from typing import Any, Dict, List, Optional, Set, Tuple

import numpy as np
import pandas as pd
import streamlit as st

import exportar_mercadolibre as exp_ml
import inventario_tiendas as inv
import paso5_motor_financiero as finanzas

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

TABLAS_FINANCIERAS = (
    "matriz_financiera",
    "auditoria_financiera",
    "catalogo_financiero",
    "productos_auditados",
    "unit_economics",
)

ALIAS: Dict[str, List[str]] = {
    "id": ["ID Price Shoes", "id_ps", "id", "sku"],
    "sku": ["SKU Interno", "sku_interno", "sku_interno_ml"],
    "modelo": ["Modelo", "nombre_modelo", "nombre"],
    "enlace": ["Enlace Original", "url_real", "url"],
    "costo": ["Costo ($)", "costo_socio", "costo"],
    "stock": ["Stock Publicable", "stock_publicable", "stock_publicable_total", "stock_total_anterior", "stock"],
    "precio": ["Precio Sugerido", "precio_sugerido", "precio_ml", "precio_venta"],
    "ganancia": ["Ganancia Neta", "ganancia_neta", "utilidad_neta_ml", "utilidad_neta"],
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
    "Sobreprecio vs PS (%)",
    "Ganancia directa ($)",
    "ID Price Shoes",
    "Modelo",
]

FILTRO_KEYS = [
    "f_etapa", "f_cuadrante", "f_origen", "f_catalogos", "f_semaforo", "f_busqueda",
    "f_costo", "f_precio", "f_ganancia", "f_margen", "f_stock", "f_fotos", "f_orden",
    "f_asc", "f_tipos", "f_inventario",
]


# ----------------------------------------------------------------------------------
# UTILIDADES
# ----------------------------------------------------------------------------------
def formatear_nombre_catalogo(valor_origen: Any) -> str:
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
    s = str(valor).strip()
    limpio = limpiar_id(s)
    return {s, limpio, f"PS-{limpio}"}


def calcular_precio_y_ganancia_fallback(costo: float, modelo: str = "") -> Tuple[float, float]:
    if costo <= 0:
        return 0.0, 0.0
    _genero, categoria, _color = finanzas.clasificar_atributos_catalogo(str(modelo), [])
    economia = finanzas.calcular_unit_economics_por_articulo(float(costo), categoria)
    return economia["precio_ml"], economia["utilidad_neta_ml"]


def firma_fuentes() -> Tuple[float, ...]:
    rutas: List[Path] = [RUTA_BD, DIR_CATALOGO_LISTO]
    if DIR_DATA.exists():
        rutas += list(DIR_DATA.glob("*.csv"))
    if DIR_CATALOGO_LISTO.exists():
        rutas += [c for c in DIR_CATALOGO_LISTO.iterdir() if c.is_dir()]
    return tuple(p.stat().st_mtime for p in rutas if p.exists())


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


PALABRAS_CSV_FINANCIERO = ("financ", "matriz", "audit", "catalogo", "ml")


def _leer_mapa_origen(conn: sqlite3.Connection, tablas: Set[str]) -> Dict[str, str]:
    mapa_origen: Dict[str, str] = {}
    if "zapatos_candidatos" not in tablas:
        return mapa_origen
    cols = {c[1] for c in conn.execute("PRAGMA table_info(zapatos_candidatos)")}
    if "origen_catalogo" not in cols:
        return mapa_origen
    for id_p, orig in conn.execute("SELECT id_ps, COALESCE(origen_catalogo, '') FROM zapatos_candidatos"):
        nombre = formatear_nombre_catalogo(orig)
        for k in claves_id(id_p):
            mapa_origen[k] = nombre
    return mapa_origen


def _leer_tabla_no_vacia(conn: sqlite3.Connection, tabla: str) -> Optional[pd.DataFrame]:
    try:
        df_tmp = pd.read_sql_query(f"SELECT * FROM {tabla}", conn)
    except Exception:
        return None
    return None if df_tmp.empty else df_tmp


def _leer_tabla_base(conn: sqlite3.Connection, tablas: Set[str]) -> Optional[pd.DataFrame]:
    for tabla in TABLAS_FINANCIERAS:
        df_tmp = _leer_tabla_no_vacia(conn, tabla) if tabla in tablas else None
        if df_tmp is not None:
            return df_tmp

    if "zapatos_candidatos" in tablas:
        df_tmp = pd.read_sql_query("SELECT * FROM zapatos_candidatos", conn)
        if not df_tmp.empty:
            return df_tmp
    return None


def _leer_csv_utilizable(csv_path: Path) -> Optional[pd.DataFrame]:
    if not any(k in csv_path.name.lower() for k in PALABRAS_CSV_FINANCIERO):
        return None
    try:
        df_csv = pd.read_csv(csv_path)
    except Exception:
        return None
    return None if (df_csv.empty or len(df_csv.columns) < 5) else df_csv


def _elegir_base_con_csv(df_base: Optional[pd.DataFrame]) -> Optional[pd.DataFrame]:
    if not DIR_DATA.exists():
        return df_base
    for csv_path in sorted(DIR_DATA.glob("*.csv")):
        df_csv = _leer_csv_utilizable(csv_path)
        if df_csv is not None and (df_base is None or _col(df_csv, "precio") is not None):
            return df_csv
    return df_base


def leer_fuentes() -> Tuple[Optional[pd.DataFrame], Dict[str, str]]:
    mapa_origen: Dict[str, str] = {}
    df_base: Optional[pd.DataFrame] = None

    if RUTA_BD.exists():
        with closing(sqlite3.connect(RUTA_BD)) as conn:
            tablas = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            mapa_origen = _leer_mapa_origen(conn, tablas)
            df_base = _leer_tabla_base(conn, tablas)

    return _elegir_base_con_csv(df_base), mapa_origen


VALORES_SUBIR_VERDADEROS = ["true", "1", "si", "sí", "x", "yes"]


def _serie_subir(df: pd.DataFrame) -> pd.Series:
    col_subir = _col(df, "subir")
    if not col_subir:
        return pd.Series(pd.NA, index=df.index, dtype="boolean")
    marcados = df[col_subir].astype(str).str.strip().str.lower().isin(VALORES_SUBIR_VERDADEROS)
    return marcados.astype("boolean")


def _precio_y_ganancia(df: pd.DataFrame, costo: pd.Series, modelo: pd.Series) -> Tuple[pd.Series, pd.Series]:
    pares = [calcular_precio_y_ganancia_fallback(c, m) for c, m in zip(costo, modelo)]
    precio_fb = pd.Series([p for p, _ in pares], index=df.index)
    ganancia_fb = pd.Series([g for _, g in pares], index=df.index)
    return _num(df, "precio", precio_fb), _num(df, "ganancia", ganancia_fb)


def _armar_inventario(
    df: pd.DataFrame, raw_id: pd.Series, id_limpio: pd.Series, num_fotos: pd.Series, costo: pd.Series,
    mapa_origen: Dict[str, str],
) -> pd.DataFrame:
    modelo = _texto(df, "modelo")
    modelo = modelo.where(modelo != "", "Modelo " + id_limpio)

    precio, ganancia = _precio_y_ganancia(df, costo, modelo)
    stock = _num(df, "stock", 0).astype(int).clip(lower=0)

    sku = _texto(df, "sku")
    sku = pd.Series(["PS-" + i if not s.startswith("PS-") else s for s, i in zip(sku, id_limpio)], index=df.index)

    origen_raw = _texto(df, "catalogo")
    catalogo = pd.Series(
        [mapa_origen.get(i) or mapa_origen.get(r) or formatear_nombre_catalogo(o)
         for i, r, o in zip(id_limpio, raw_id, origen_raw)],
        index=df.index,
    )

    return pd.DataFrame(
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
            "_subir_src": _serie_subir(df),
        }
    )


@st.cache_data(show_spinner="Cargando inventario…")
def cargar_datos_inventario(firma: Tuple[float, ...]) -> pd.DataFrame:
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
    stock_raw = _num(df, "stock", 0)

    valido = (id_limpio != "") & ~((costo <= 0) & (num_fotos == 0) & (stock_raw <= 0))
    df, raw_id, id_limpio, num_fotos, costo = (x[valido] for x in (df, raw_id, id_limpio, num_fotos, costo))
    if df.empty:
        return pd.DataFrame()

    out = _armar_inventario(df, raw_id, id_limpio, num_fotos, costo, mapa_origen)
    out = out.sort_values("Ganancia Neta", ascending=False).drop_duplicates("ID Price Shoes")
    return out.reset_index(drop=True)


def evaluar_semaforo(df: pd.DataFrame, min_stock: int, min_ganancia: float) -> pd.DataFrame:
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
        guardada = cargar_seleccion_guardada() or set()
        st.session_state.seleccion_ml = set(guardada)
        st.session_state["_seleccion_guardada"] = set(guardada)
    st.session_state.setdefault("ver_editor", 0)


def resetear_filtros() -> None:
    for k in FILTRO_KEYS:
        st.session_state.pop(k, None)


# ----------------------------------------------------------------------------------
# TIENDA, EXISTENCIAS POR TIENDA Y ALERTAS DE PAUSA
# ----------------------------------------------------------------------------------
def configurar_tienda() -> Tuple[int, str, int]:
    sb = st.sidebar
    sb.header("🏬 Tienda")
    with closing(inv.conectar(RUTA_BD)) as conn:
        inv.asegurar_esquema(conn)
        tiendas = inv.listar_tiendas(conn)
        activa_id, _ = inv.tienda_activa(conn)
        umbral_guardado = inv.umbral_pausa(conn)

        ids = [t[0] for t in tiendas]
        nombres = dict(tiendas)
        tienda_id = sb.selectbox(
            "Tienda a revisar",
            ids,
            index=ids.index(activa_id) if activa_id in ids else 0,
            format_func=lambda i: nombres.get(i, str(i)),
        )
        umbral = int(sb.number_input("Pausar si hay menos de (piezas)", 0, 500, umbral_guardado, 1))

        if tienda_id != activa_id or umbral != umbral_guardado:
            inv.fijar_tienda_activa(conn, tienda_id)
            inv.fijar_umbral_pausa(conn, umbral)
            inv.recalcular_alertas(conn, tienda_id)
    return tienda_id, nombres.get(tienda_id, str(tienda_id)), umbral


def aplicar_inventario_tienda(df: pd.DataFrame, tienda_id: int) -> pd.DataFrame:
    with closing(inv.conectar(RUTA_BD)) as conn:
        resumen = inv.resumen_por_producto(conn, tienda_id)

    df = df.copy()
    info = [resumen.get(str(i), {}) for i in df["ID Price Shoes"]]
    tipos_lista = [r.get("tipo") or inv.clasificar_tipo_producto(m) for r, m in zip(info, df["Modelo"])]
    df["Tipo"] = tipos_lista

    pares_maslow = [
        inv.clasificar_matriz_maslow(m, c, t)
        for m, c, t in zip(df["Modelo"], df["Catálogo"], df["Tipo"])
    ]
    df["Etapa Maslow"] = [p[0] for p in pares_maslow]
    df["Cuadrante"] = [p[1] for p in pares_maslow]

    df["_revisado"] = [r.get("stock") is not None for r in info]
    df["Stock Publicable"] = [
        int(r.get("stock")) if r.get("stock") is not None else int(stk_orig)
        for r, stk_orig in zip(info, df["Stock Publicable"])
    ]
    df["Tallas en tienda"] = [r.get("tallas", "") for r in info]
    df["Última revisión"] = [str(r.get("fecha", ""))[:16].replace("T", " ") for r in info]

    with closing(inv.conectar(RUTA_BD)) as conn:
        df = inv.agregar_comparacion_publico(df, conn)
    return df


def mostrar_alertas(tienda_id: int, tienda_nombre: str, umbral: int) -> None:
    with closing(inv.conectar(RUTA_BD)) as conn:
        alertas = inv.alertas_activas(conn, tienda_id)

    pendientes = [a for a in alertas if not a["atendida"]]
    if not alertas:
        st.success(f"Sin alertas: ningún producto seleccionado tiene menos de {umbral} piezas en {tienda_nombre}.")
        return

    if pendientes:
        st.error(f"⏸️ Pausar {len(pendientes)} productos: tienen menos de {umbral} piezas en {tienda_nombre}.")
    else:
        st.info(f"Las {len(alertas)} alertas de pausa ya están marcadas como atendidas.")

    df_alertas = pd.DataFrame(alertas).rename(columns={
        "id_ps": "ID Price Shoes", "nombre": "Modelo", "tipo": "Tipo", "stock_total": "Piezas en tienda",
        "fecha_creada": "Alerta desde", "fecha_actualizada": "Última revisión", "atendida": "Ya pausado",
    })
    df_alertas["Ya pausado"] = df_alertas["Ya pausado"].astype(bool)
    columnas = ["Ya pausado", "ID Price Shoes", "Modelo", "Tipo", "Piezas en tienda", "Alerta desde", "Última revisión"]
    editado = st.data_editor(
        df_alertas[columnas + ["id"]],
        hide_index=True,
        use_container_width=True,
        column_config={"id": None},
        disabled=[c for c in columnas if c != "Ya pausado"],
        key=f"alertas_{tienda_id}",
    )
    cambios = [
        (int(id_alerta), bool(nuevo))
        for id_alerta, nuevo, previo in zip(editado["id"], editado["Ya pausado"], df_alertas["Ya pausado"])
        if bool(nuevo) != bool(previo)
    ]
    if cambios:
        with closing(inv.conectar(RUTA_BD)) as conn:
            for id_alerta, atendida in cambios:
                inv.marcar_alerta_atendida(conn, id_alerta, atendida)
        st.rerun()


def guardar_seleccion_si_cambio(tienda_id: int) -> None:
    actual = set(st.session_state.seleccion_ml)
    if actual == st.session_state.get("_seleccion_guardada"):
        return
    try:
        guardar_seleccion(actual)
        with closing(inv.conectar(RUTA_BD)) as conn:
            inv.recalcular_alertas(conn, tienda_id)
        st.session_state["_seleccion_guardada"] = actual
    except sqlite3.Error as e:
        st.error(f"No se pudo guardar la selección: {e}")


# ----------------------------------------------------------------------------------
# FILTROS (barra lateral)
# ----------------------------------------------------------------------------------
def slider_rango(etiqueta: str, serie: pd.Series, key: str, formato: str = "%.0f") -> Optional[Tuple[float, float]]:
    lo, hi = float(serie.min()), float(serie.max())
    if not np.isfinite(lo) or lo >= hi:
        return None
    lo, hi = float(np.floor(lo)), float(np.ceil(hi))
    return st.sidebar.slider(etiqueta, lo, hi, (lo, hi), format=formato, key=key)


FILTRO_SEM_TODOS = "Todos"
FILTRO_SEM_RECOMENDADOS = "✅ Solo Recomendados"
FILTRO_SEM_PENDIENTES = "⚠️ Con Observaciones / Pendientes"

FILTROS_DE_RANGO = (
    ("Costo ($)", "Costo ($)", "f_costo", "%.0f"),
    ("Precio sugerido ($)", "Precio Sugerido", "f_precio", "%.0f"),
    ("Ganancia neta ($)", "Ganancia Neta", "f_ganancia", "%.0f"),
    ("Margen (%)", "Margen (%)", "f_margen", "%.0f%%"),
)


def _filtrar_matriz_y_tipo(sb, df: pd.DataFrame) -> pd.DataFrame:
    conteo_etapas = df["Etapa Maslow"].value_counts()
    opciones_etapa = [inv.ETAPA_VERDE, inv.ETAPA_NARANJA, inv.ETAPA_GRIS]
    etapas = sb.multiselect(
        "🎯 Etapa Matriz Maslow",
        opciones_etapa,
        default=[inv.ETAPA_VERDE],
        format_func=lambda e: f"{e} ({conteo_etapas.get(e, 0)})",
        key="f_etapa",
    )
    if etapas:
        df = df[df["Etapa Maslow"].isin(etapas)]

    conteo_cuad = df["Cuadrante"].value_counts()
    opciones_cuad = list(conteo_cuad.index)
    cuadrantes = sb.multiselect(
        "🟢 Cuadrante específico (vacío = todos)",
        opciones_cuad,
        format_func=lambda c: f"{c} ({conteo_cuad.get(c, 0)})",
        key="f_cuadrante",
    )
    if cuadrantes:
        df = df[df["Cuadrante"].isin(cuadrantes)]

    conteo_tipos = df["Tipo"].value_counts()
    opciones_tipo = [t for t in inv.TIPOS_PRODUCTO if t in conteo_tipos.index]
    tipos = sb.multiselect(
        "👟 Tipos de producto (vacío = todos)",
        opciones_tipo,
        format_func=lambda t: f"{t} ({conteo_tipos[t]})",
        key="f_tipos",
    )
    if tipos:
        df = df[df["Tipo"].isin(tipos)]

    tiene_stock_positivo = int((df["Stock Publicable"] > 0).sum()) > 0
    if sb.checkbox("Solo con inventario en la tienda", value=tiene_stock_positivo, key="f_inventario"):
        df = df[df["Stock Publicable"] > 0]
    return df


def _filtrar_origen_y_catalogo(sb, df: pd.DataFrame) -> pd.DataFrame:
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
    return df[df["Catálogo"].isin(elegidos)] if elegidos else df


def _filtrar_semaforo(sb, df: pd.DataFrame) -> pd.DataFrame:
    filtro_sem = sb.radio(
        "🚦 Semáforo",
        [FILTRO_SEM_TODOS, FILTRO_SEM_RECOMENDADOS, FILTRO_SEM_PENDIENTES],
        key="f_semaforo",
    )
    if filtro_sem == FILTRO_SEM_RECOMENDADOS:
        return df[df["Semáforo"] == RECOMENDADO]
    if filtro_sem != FILTRO_SEM_TODOS:
        return df[df["Semáforo"] != RECOMENDADO]
    return df


def _filtrar_busqueda(sb, df: pd.DataFrame) -> pd.DataFrame:
    busqueda = sb.text_input("🔍 Buscar (ID, SKU, modelo o catálogo)", key="f_busqueda").strip().lower()
    if not busqueda:
        return df
    pajar = (
        df["ID Price Shoes"].astype(str) + " " + df["SKU Interno"].astype(str) + " "
        + df["Modelo"].astype(str) + " " + df["Catálogo"].astype(str)
    ).str.lower()
    mask = pd.Series(True, index=df.index)
    for termino in busqueda.split():
        mask &= pajar.str.contains(termino, regex=False)
    return df[mask]


def _filtrar_rangos(sb, df: pd.DataFrame, df_total: pd.DataFrame) -> pd.DataFrame:
    sb.markdown("**💲 Costos, precios y márgenes**")
    for etiqueta, columna, key, formato in FILTROS_DE_RANGO:
        r = slider_rango(etiqueta, df_total[columna], key, formato)
        if r:
            df = df[df[columna].between(*r)]

    max_stock = int(df_total["Stock Publicable"].max()) if not df_total.empty else 0
    if max_stock > 0:
        min_stock_f = sb.slider("Stock mínimo (pares)", 0, max_stock, 0, key="f_stock")
        df = df[df["Stock Publicable"] >= min_stock_f]

    rango_fotos = sb.select_slider(
        "📸 Fotos HD disponibles", options=list(range(FOTOS_REQUERIDAS + 1)),
        value=(0, FOTOS_REQUERIDAS), key="f_fotos",
    )
    return df[df["_num_fotos"].between(*rango_fotos)]


def _ordenar(sb, df: pd.DataFrame) -> pd.DataFrame:
    orden = sb.selectbox("↕️ Ordenar por", OPCIONES_ORDEN, key="f_orden")
    ascendente = sb.checkbox("Orden ascendente", value=False, key="f_asc")
    if orden != ORDEN_DEFAULT:
        return df.sort_values(orden, ascending=ascendente)
    return (
        df.assign(_r=df["Semáforo"] != RECOMENDADO)
        .sort_values(["_r", "Ganancia Neta", "Stock Publicable"], ascending=[True, False, False])
        .drop(columns="_r")
    )


def aplicar_filtros(df_total: pd.DataFrame) -> pd.DataFrame:
    sb = st.sidebar
    sb.header("🔎 Filtros de Auditoría")
    sb.button("🧹 Limpiar filtros", on_click=resetear_filtros, use_container_width=True)

    df = _filtrar_matriz_y_tipo(sb, df_total)
    df = _filtrar_origen_y_catalogo(sb, df)
    df = _filtrar_semaforo(sb, df)
    df = _filtrar_busqueda(sb, df)
    df = _filtrar_rangos(sb, df, df_total)
    df = _ordenar(sb, df)
    return df.reset_index(drop=True)


# ----------------------------------------------------------------------------------
# PANEL
# ----------------------------------------------------------------------------------
COLUMNAS_MATRIZ = [
    "Subir a ML", "ID Price Shoes", "SKU Interno", "Cuadrante", "Tipo", "Catálogo", "Modelo", "Enlace Original",
    "Semáforo", "Costo ($)", "Precio Sugerido", "Ganancia Neta", "Margen (%)",
    *inv.COLUMNAS_COMPARACION,
    "Stock Publicable", "Tallas en tienda", "Última revisión", "Diagnóstico Técnico",
]


def _mostrar_gestos_verdes(df_total: pd.DataFrame) -> None:
    """Resumen ejecutivo de los 4 cuadrantes verdes de la Matriz de Maslow."""
    st.markdown("#### 🟢 Gestos del Sistema en los 4 Cuadrantes Verdes (*Empezar aquí*)")
    cols_v = st.columns(4)
    for col_ui, cuad in zip(cols_v, inv.CUADRANTES_VERDES):
        sub = df_total[df_total["Cuadrante"] == cuad]
        con_stk = sub[sub["Stock Publicable"] > 0]
        rec = int((sub["Semáforo"] == RECOMENDADO).sum())
        gan_prom = float(con_stk["Ganancia Neta"].mean()) if not con_stk.empty else 0.0
        col_ui.metric(
            label=cuad,
            value=f"{len(con_stk):,} con stock",
            delta=f"{rec} recomendados · Gan. prom: ${gan_prom:,.0f}",
            help=f"Total en catálogo para este cuadrante: {len(sub):,} SKUs",
        )


def _mostrar_metricas(df: pd.DataFrame, df_total: pd.DataFrame) -> None:
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("📦 SKUs en vista", f"{len(df):,}", help=f"de {len(df_total):,} en total")
    c2.metric("✅ Recomendados", f"{int((df['Semáforo'] == RECOMENDADO).sum()):,}")
    c3.metric("📸 Con carrusel HD (4/4)", f"{int((df['_num_fotos'] >= FOTOS_REQUERIDAS).sum()):,}")
    c4.metric("💰 Ganancia neta promedio", f"${df['Ganancia Neta'].mean() if not df.empty else 0:,.2f}")


def _mostrar_matriz(df: pd.DataFrame, tienda_id: int, tienda_nombre: str) -> None:
    b1, b2, _ = st.columns([1, 1, 4])
    ids_vista = set(df["ID Price Shoes"])
    if b1.button("☑️ Marcar visibles", use_container_width=True):
        st.session_state.seleccion_ml |= ids_vista
        st.session_state.ver_editor += 1
    if b2.button("⬜ Desmarcar visibles", use_container_width=True):
        st.session_state.seleccion_ml -= ids_vista
        st.session_state.ver_editor += 1
    st.caption(
        "Marca **Seleccionado** en los productos que vas a vender: se guardan solos y son los que se "
        "exportan a Mercado Libre y generan alertas de pausa."
    )

    columnas = COLUMNAS_MATRIZ
    df_editor = df.assign(**{"Subir a ML": df["ID Price Shoes"].isin(st.session_state.seleccion_ml)})[columnas]

    editado = st.data_editor(
        df_editor,
        use_container_width=True,
        hide_index=True,
        height=400,
        column_config={
            "Subir a ML": st.column_config.CheckboxColumn("Seleccionado", default=False),
            "Stock Publicable": st.column_config.NumberColumn(f"Piezas en {tienda_nombre}"),
            "Enlace Original": st.column_config.LinkColumn("Enlace Original", display_text="Ver en PS"),
            "Costo ($)": st.column_config.NumberColumn("Costo ($)", format="$%.0f"),
            "Precio Sugerido": st.column_config.NumberColumn("Precio Sugerido", format="$%.0f"),
            "Ganancia Neta": st.column_config.NumberColumn("Ganancia Neta", format="$%.2f"),
            "Margen (%)": st.column_config.NumberColumn("Margen (%)", format="%.1f%%"),
            "Precio público PS": st.column_config.NumberColumn("Precio público PS", format="$%.0f"),
            "Descuento socio (%)": st.column_config.NumberColumn("Descuento socio (%)", format="%.1f%%"),
            "Sobreprecio vs PS (%)": st.column_config.NumberColumn("Sobreprecio vs PS (%)", format="%.1f%%"),
            "Ganancia directa ($)": st.column_config.NumberColumn("Ganancia directa ($)", format="$%.0f"),
        },
        disabled=[c for c in columnas if c != "Subir a ML"],
        key=f"editor_{st.session_state.ver_editor}_{hash(tuple(df['ID Price Shoes']))}",
    )

    for id_, marcado in zip(editado["ID Price Shoes"], editado["Subir a ML"]):
        if marcado:
            st.session_state.seleccion_ml.add(id_)
        else:
            st.session_state.seleccion_ml.discard(id_)
    guardar_seleccion_si_cambio(tienda_id)


def _mostrar_acciones_seleccion(df_total: pd.DataFrame) -> None:
    seleccion = df_total[df_total["ID Price Shoes"].isin(st.session_state.seleccion_ml)]
    a1, a2, a3, a4 = st.columns([1.8, 1.1, 1.1, 1.2])
    a1.markdown(
        f"**🛒 Seleccionados para ML:** {len(seleccion):,} SKUs · "
        f"{int((seleccion['Semáforo'] != RECOMENDADO).sum())} con observaciones"
    )
    a2.download_button(
        "⬇️ Descargar selección (CSV)",
        data=seleccion.drop(columns=["_num_fotos", "_subir_src", "_revisado"]).to_csv(index=False).encode("utf-8-sig"),
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

    if a4.button("📦 Exportar CSVs por Tipo (ML)", type="primary", use_container_width=True, disabled=seleccion.empty):
        with closing(inv.conectar(RUTA_BD)) as conn:
            inv.asegurar_esquema(conn)
            exp_ml.asegurar_tabla_publicaciones(conn)
            resumen = exp_ml.exportar(
                conn,
                exp_ml.DIR_EXPORTES,
                exp_ml.RESERVA_POR_TALLA_DEFAULT,
                exp_ml.ACCION_PAUSAR,
            )
        st.success(
            f"✅ Exportación generada en `{resumen['carpeta']}` · "
            f"Nuevas: {len(resumen['ids_nuevas'])} productos · Omitidos: {resumen['omitidos']} · "
            f"Archivos: {', '.join(resumen['archivos'].keys()) or 'ninguno'}"
        )


def _mostrar_galeria(df: pd.DataFrame, firma: Tuple[float, ...]) -> None:
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
        format_func=lambda i: f"{i} · {filas.loc[i, 'Modelo']} ({filas.loc[i, 'Cuadrante']})",
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


def _preparar_datos_de_tienda(df_cargado: pd.DataFrame) -> Tuple[pd.DataFrame, int, str]:
    tienda_id, tienda_nombre, umbral_pausa = configurar_tienda()
    df_cargado = aplicar_inventario_tienda(df_cargado, tienda_id)
    st.caption(
        f"Tienda: **{tienda_nombre}** · {int(df_cargado['_revisado'].sum()):,} productos con existencias revisadas."
    )
    mostrar_alertas(tienda_id, tienda_nombre, umbral_pausa)

    with st.sidebar.expander("⚙️ Criterios del semáforo", expanded=False):
        min_stock = st.number_input("Stock mínimo para recomendar", 0, 500, STOCK_MIN_DEFAULT, 1)
        min_ganancia = st.number_input("Ganancia neta mínima ($)", 0.0, 5000.0, GANANCIA_MIN_DEFAULT, 10.0)
    return evaluar_semaforo(df_cargado, int(min_stock), float(min_ganancia)), tienda_id, tienda_nombre


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
    df_total, tienda_id, tienda_nombre = _preparar_datos_de_tienda(df_cargado)

    _mostrar_gestos_verdes(df_total)
    st.markdown("---")

    df = aplicar_filtros(df_total)

    if st.sidebar.button("🔄 Recargar datos y fotos HD", use_container_width=True):
        st.cache_data.clear()
        resetear_filtros()
        st.rerun()

    _mostrar_metricas(df, df_total)
    st.markdown("---")

    st.subheader("📋 Matriz de Control e Inventario")
    if df.empty:
        st.warning("No hay artículos que coincidan con los filtros seleccionados.")
        return

    _mostrar_matriz(df, tienda_id, tienda_nombre)
    _mostrar_acciones_seleccion(df_total)
    st.markdown("---")
    _mostrar_galeria(df, firma)


if __name__ == "__main__":
    renderizar_panel()