"""
========================================================================================
PASO 4: PANEL DE CONTROL E INVENTARIO EN STREAMLIT  (v4 · + existencias por talla, opcional)
========================================================================================

Propósito
---------
Panel para auditar el inventario de Zapatería Vallejo antes de publicarlo en
Mercado Libre (ML). Cruza costos/precios, velocidad de venta, catálogo de origen y
fotografías HD, asigna un semáforo (recomendado / no recomendado) y permite elegir,
guardar y descargar la selección de SKUs a subir.

Ejecución
---------
    streamlit run paso4_panel_control.py

Requisitos: streamlit, pandas (>= 1.0, por el dtype "boolean"), numpy.
Se ejecuta desde la raíz del proyecto (las rutas de CONFIGURACIÓN son relativas).

Flujo de datos
--------------
    SQLite (data/zapateria_vallejo.db)  ┐
    CSV    (data/*.csv)                 ├─► leer_fuentes() ─► cargar_datos_inventario()
    Carpetas data/catalogo_listo/<id>/  ┘                     (cacheado, 1 fila por ID)
                                                              │
                          evaluar_semaforo() ◄────────────────────────┘
                                 │
                          aplicar_filtros()  (barra lateral)
                                 │
                       renderizar_panel(): métricas · matriz editable · galería

Fuentes de datos (por orden de prioridad)
-----------------------------------------
1. Tabla financiera de SQLite: la primera que exista y tenga filas de
   `TABLAS_FINANCIERAS`; si no hay ninguna, `zapatos_candidatos`.
2. CSV en `data/` cuyo nombre contenga alguna de `PALABRAS_CSV`. Reemplaza a la
   tabla de SQLite si la tabla no existía o si el CSV trae columna de precio.
3. `zapatos_candidatos` aporta además el catálogo de origen y la velocidad de venta
   (mapas auxiliares indexados por ID).

Fórmulas (solo si el origen no trae Precio Sugerido / Ganancia Neta)
--------------------------------------------------------------------
    precio_bruto = costo * FACTOR_COSTO + CARGO_FIJO_PRECIO
    precio       = max(redondeo_a_decena(precio_bruto) - 1,  costo + MARGEN_MINIMO_SOBRE_COSTO)
                   → termina en 9, p. ej. 499, 549
    ganancia     = precio * FACTOR_NETO_VENTA - costo - CARGO_FIJO_GANANCIA
    margen (%)   = ganancia / precio * 100

Semáforo
--------
    ✅ RECOMENDADO     fotos == 4  y  stock >= mínimo  y  ganancia >= mínima
    ⚠️ NO RECOMENDADO  cualquier otro caso; el "Diagnóstico Técnico" dice por qué
                       (prioridad: fotos → stock → margen).
    Los umbrales se editan en la barra lateral ("Criterios del semáforo").

Estado y persistencia
---------------------
- `st.session_state.seleccion_ml`: conjunto de IDs marcados "Subir a ML". Sobrevive
  a los cambios de filtro durante la sesión.
- Botón "Guardar selección": persiste el conjunto en la tabla `seleccion_ml` de SQLite.
- Caché (`st.cache_data`): se invalida sola cuando cambian las "firmas" de los archivos
  (fecha de modificación de BD, CSV y carpetas) o con el botón "Recargar datos".

Existencias en tienda por talla (v4, OPCIONAL)
----------------------------------------------
Fuente (formato "largo": una fila por ID y talla; ver `TABLAS_TALLAS`):
    id_ps | talla | existencias
- Tabla de SQLite (`TABLAS_TALLAS`) o CSV en `data/` con "talla" o "existencia" en el
  nombre. También se acepta formato "ancho" (una columna por talla: 22, 22.5, 23…).
- Es una opción: la casilla "📏 Existencias por talla" de la barra lateral (apagada por
  defecto) activa tres cosas a la vez:
    1. Dos columnas compactas en la matriz: "Pares en Tienda" y "Tallas Disponibles"
       (p. ej. "22(1) 23(3) 25(2)", solo tallas con stock).
    2. Un filtro "Talla disponible" (cualquiera / todas las elegidas, con mínimo de pares).
    3. Una sección "Tabla de Existencias por Talla": un renglón por producto visible y
       una columna por talla, con descarga en CSV.
- Galería (siempre que haya datos de tallas, sin depender de la casilla): se separa en
  dos bloques para no mezclar lo que se publica con lo que ayuda a decidir:
    · "Vista del cliente": las 4 láminas HD y una línea con las tallas disponibles en
      stock (solo los nombres de talla; nunca cantidades, costos ni ganancias).
    · "Uso interno": costo, precio, ganancia, velocidad, stock y el número exacto de
      pares por talla (incluye tallas agotadas).
- Si no se encuentra la fuente de tallas, la barra lateral lo avisa y todo lo demás
  funciona igual que en v3.

Cambios principales respecto a v2
---------------------------------
- Semáforo, diagnóstico y fórmula de precio vectorizados (sin bucles fila por fila).
- Cruces por ID con `Series.map` en lugar de listas por comprensión.
- Texto de búsqueda precalculado una sola vez (dentro de la caché).
- Todos los filtros se combinan en una sola máscara booleana (una sola copia del DataFrame).
- Firma de archivos con `os.scandir` y TTL corto: no se escanea el disco en cada clic.
- Láminas por posición fija (01-04): antes, si faltaba la 02, la 03 se mostraba con la
  etiqueta equivocada.
- Lectura de CSV con `utf-8-sig` (el CSV que exporta este panel lleva BOM y rompía
  los alias de columnas), IDs tipo "123.0" normalizados, extensiones .jpg/.jpeg/.png
  sin importar mayúsculas.
- Compatible con Streamlit nuevo (`width="stretch"`) y antiguo (`use_container_width`).
- Sliders y multiselect con estado saneado al cambiar los datos (evita excepciones).
- Excepciones específicas en lugar de `except Exception`.
"""

from __future__ import annotations

import hashlib
import inspect
import os
import re
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import numpy as np
import pandas as pd
import streamlit as st

# NOTA: st.set_page_config debe ser el primer comando de Streamlit del script.
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

FOTOS_REQUERIDAS = 4          # un carrusel completo = 4 láminas (01..04)
STOCK_MIN_DEFAULT = 5         # pares mínimos para recomendar
GANANCIA_MIN_DEFAULT = 100.0  # pesos mínimos de ganancia neta para recomendar

# Fórmula de precio (antes eran números "mágicos" dentro de la función)
FACTOR_COSTO = 1.95
CARGO_FIJO_PRECIO = 60.0
FACTOR_NETO_VENTA = 0.7195      # fracción del precio que queda tras comisiones de ML
CARGO_FIJO_GANANCIA = 111.40    # cargo fijo por venta (envío/otros)
MARGEN_MINIMO_SOBRE_COSTO = 180  # el precio nunca baja de costo + 180

# Tablas de SQLite donde puede vivir la matriz financiera (se usa la primera con datos).
# Son constantes del código, por eso es seguro interpolarlas en el SQL.
TABLAS_FINANCIERAS = (
    "matriz_financiera",
    "auditoria_financiera",
    "catalogo_financiero",
    "productos_auditados",
    "unit_economics",
)

# Existencias en tienda por talla (formato "largo"). Se usa la primera tabla con datos;
# si no hay tabla, un CSV de `data/` cuyo nombre contenga alguna de PALABRAS_CSV_TALLAS.
TABLAS_TALLAS = ("historial_stock_21_dias", "existencias_tallas", "inventario_tallas", "stock_tallas", "existencias_tienda")
PALABRAS_CSV_TALLAS = ("talla", "existencia")

# Máximo de productos en la "Tabla de Existencias por Talla" (mantiene la interfaz ágil).
MAX_FILAS_TABLA_TALLAS = 500

# Un CSV de `data/` solo se considera si su nombre contiene alguna de estas palabras.
# NOTA: "ml" es muy corto y también coincide con nombres como "html_export.csv";
# si te trae CSV no deseados, cámbialo por algo más específico (p. ej. "_ml").
PALABRAS_CSV = ("financ", "matriz", "audit", "catalogo", "ml")

# Segundos que se reutiliza el escaneo de archivos antes de volver a mirar el disco.
TTL_FIRMA_SEG = 5

# Alias de columnas aceptados (se resuelven sin importar mayúsculas/minúsculas ni
# espacios al borde). El primero que exista en el origen gana.
ALIAS: Dict[str, List[str]] = {
    "id": ["ID Price Shoes", "id_ps", "id", "sku"],
    "sku": ["SKU Interno", "sku_interno"],
    "modelo": ["Modelo", "nombre_modelo", "nombre"],
    "enlace": ["Enlace Original", "url_real", "url"],
    "costo": ["Costo ($)", "costo_socio", "costo"],
    "stock": ["Stock Publicable", "stock_publicable", "stock_total_anterior", "stock"],
    "velocidad": ["Velocidad de Venta", "velocidad_diaria", "velocidad", "ventas_diarias", "rotacion"],
    "precio": ["Precio Sugerido", "precio_sugerido", "precio_venta"],
    "ganancia": ["Ganancia Neta", "ganancia_neta", "utilidad_neta"],
    "catalogo": ["Catálogo", "origen_catalogo"],
    "subir": ["Subir a ML", "subir_ml"],
    # Tabla de tallas (el ID reutiliza el alias "id")
    "talla": ["Talla", "talla", "size"],
    "existencias": ["Existencias", "stock_vallejo", "existencias_tienda", "existencia", "cantidad", "pares"],
}

# Número de catálogo (7 dígitos, del nombre del PDF) → nombre legible.
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

# Láminas del carrusel HD: el prefijo del archivo define la posición.
PREFIJOS_LAMINAS = ("01", "02", "03", "04")
ETIQUETAS_LAMINAS = ["Portada Blanca", "Ángulo de Detalle", "Ficha de Beneficios", "Guía de Tallas (CM)"]
# Extensiones aceptadas y su preferencia (si hay 01.jpg y 01.png, gana el JPG).
EXT_LAMINAS: Dict[str, int] = {".jpg": 0, ".jpeg": 0, ".png": 1}

ORIGEN_PDF = "📘 Solo catálogos PDF"
ORIGEN_TODO = "🌐 Todo (incluye Web)"
ORIGEN_WEB = "🕸️ Solo Web / sin catálogo"

SEM_TODOS = "Todos"
SEM_SOLO_OK = "✅ Solo Recomendados"
SEM_PENDIENTES = "⚠️ Con Observaciones / Pendientes"

ORDEN_DEFAULT = "Recomendados primero + mayor ganancia"
OPCIONES_ORDEN = [
    ORDEN_DEFAULT,
    "Velocidad de Venta",
    "Ganancia Neta",
    "Margen (%)",
    "Precio Sugerido",
    "Costo ($)",
    "Stock Publicable",
    "ID Price Shoes",
    "Modelo",
]

# Claves de widgets de la barra lateral que "Limpiar filtros" debe reiniciar.
FILTRO_KEYS = [
    "f_origen", "f_catalogos", "f_semaforo", "f_busqueda", "f_costo", "f_precio",
    "f_ganancia", "f_margen", "f_velocidad", "f_stock", "f_fotos", "f_orden", "f_asc",
    "f_tallas", "f_tallas_min", "f_tallas_todas",
]

# Tipo de las "firmas": ((ruta, fecha_de_modificación), ...). Cambian si se agrega,
# quita o modifica cualquier archivo observado, lo que invalida la caché.
Firma = Tuple[Tuple[str, float], ...]


def _streamlit_usa_width() -> bool:
    """True si esta versión de Streamlit ya usa `width="stretch"`.

    `use_container_width` está deprecado y se eliminará; las versiones nuevas
    esperan `width="stretch"`. Se detecta mirando la firma de `st.image`.
    """
    try:
        return inspect.signature(st.image).parameters["width"].default == "content"
    except (KeyError, TypeError, ValueError):
        return False


# Se expande como **ANCHO_TOTAL en botones, editor e imágenes.
ANCHO_TOTAL: Dict[str, Any] = (
    {"width": "stretch"} if _streamlit_usa_width() else {"use_container_width": True}
)


# ----------------------------------------------------------------------------------
# UTILIDADES
# ----------------------------------------------------------------------------------
def formatear_nombre_catalogo(valor_origen: Any) -> str:
    """Traduce '1342200.pdf' o '1342200_2.pdf' a '1342200 - Caballeros'.

    Devuelve `SIN_CATALOGO` si el valor está vacío o es nulo; si el número no
    está en `NOMBRES_CATALOGOS`, devuelve el texto original.
    """
    if not valor_origen:
        return SIN_CATALOGO
    texto = str(valor_origen).strip()
    if not texto or texto.lower() in ("none", "nan", "null"):
        return SIN_CATALOGO
    m = re.search(r"(\d{7})", texto)
    if m and m.group(1) in NOMBRES_CATALOGOS:
        return NOMBRES_CATALOGOS[m.group(1)]
    limpio = re.sub(r"\.pdf$", "", texto, flags=re.IGNORECASE).strip()
    return NOMBRES_CATALOGOS.get(limpio, texto)


def limpiar_id(valor: Any) -> str:
    """Quita 'PS-' y ceros a la izquierda ('PS-00123' → '123'; '0' se conserva)."""
    s = str(valor).strip().replace("PS-", "")
    return s.lstrip("0") or s


def claves_id(valor: Any) -> Set[str]:
    """Todas las formas en que puede aparecer un mismo ID (con ceros, sin ceros, PS-xxx)."""
    s = str(valor).strip()
    limpio = limpiar_id(s)
    return {s, limpio, f"PS-{limpio}"}


def calcular_precio_y_ganancia_fallback(costo: pd.Series) -> Tuple[pd.Series, pd.Series]:
    """Calcula Precio Sugerido y Ganancia Neta (vectorizado) para un Series de costos.

    Se usa solo cuando el origen de datos no trae esas columnas o vienen vacías.
    Costos <= 0 devuelven (0, 0). Ver la sección "Fórmulas" del docstring del módulo.
    """
    c = costo.astype(float)
    precio_bruto = c * FACTOR_COSTO + CARGO_FIJO_PRECIO
    # np.round redondea .5 al par, como round() de Python. Única diferencia con v2: en
    # contados empates de decimales puede variar 1 centavo en la ganancia (float vs decimal).
    decena = np.round(precio_bruto / 10.0) * 10 - 1
    precio = np.maximum(decena, np.floor(c + MARGEN_MINIMO_SOBRE_COSTO))
    ganancia = np.round(precio * FACTOR_NETO_VENTA - c - CARGO_FIJO_GANANCIA, 2)
    valido = c > 0
    return precio.where(valido, 0.0), ganancia.where(valido, 0.0)


def _anotar_mtime(entradas: List[Tuple[str, float]], ruta: Path) -> None:
    """Agrega (ruta, mtime) a `entradas`; ignora rutas que no existen."""
    try:
        entradas.append((str(ruta), ruta.stat().st_mtime))
    except OSError:
        pass


@st.cache_data(ttl=TTL_FIRMA_SEG, show_spinner=False)
def firma_datos() -> Firma:
    """Firma de la BD (y su WAL) y de los CSV de `data/`.

    Con TTL corto: los clics y escritos en filtros (que relanzan el script) no vuelven
    a tocar el disco; un cambio externo se detecta en como máximo `TTL_FIRMA_SEG` s.
    """
    entradas: List[Tuple[str, float]] = []
    _anotar_mtime(entradas, RUTA_BD)
    _anotar_mtime(entradas, Path(f"{RUTA_BD}-wal"))  # SQLite en modo WAL
    if DIR_DATA.is_dir():
        for csv_path in sorted(DIR_DATA.glob("*.csv")):
            _anotar_mtime(entradas, csv_path)
    return tuple(entradas)


@st.cache_data(ttl=TTL_FIRMA_SEG, show_spinner=False)
def firma_fotos() -> Firma:
    """Firma de `catalogo_listo` y de cada subcarpeta (cambia si se agrega/quita una foto).

    Está separada de `firma_datos` a propósito: así "Guardar selección" (que modifica
    la BD) no obliga a reescanear todas las carpetas de fotos.
    """
    entradas: List[Tuple[str, float]] = []
    _anotar_mtime(entradas, DIR_CATALOGO_LISTO)
    if DIR_CATALOGO_LISTO.is_dir():
        with os.scandir(DIR_CATALOGO_LISTO) as it:
            for e in it:
                if e.is_dir():
                    try:
                        entradas.append((e.path, e.stat().st_mtime))
                    except OSError:
                        pass
    return tuple(entradas)


# --- Lectura de columnas con alias (vectorizada) -------------------------------------
def _col(df: pd.DataFrame, campo: str) -> Optional[str]:
    """Nombre real de la columna que corresponde a `campo` según `ALIAS`, o None."""
    minusculas = {str(c).strip().lower(): c for c in df.columns}
    for alias in ALIAS[campo]:
        if alias.lower() in minusculas:
            return minusculas[alias.lower()]
    return None


def _texto(df: pd.DataFrame, campo: str, default: Any = "") -> pd.Series:
    """Columna como texto sin espacios al borde; vacío si no existe o es nula."""
    c = _col(df, campo)
    if c is None:
        return pd.Series(default, index=df.index, dtype="object")
    return df[c].fillna("").astype(str).str.strip()


def _num(df: pd.DataFrame, campo: str, default: Any = 0.0) -> pd.Series:
    """Columna como número. Acepta '$1,234.50'; lo no convertible se reemplaza por `default`.

    `default` puede ser un escalar o un Series (se alinea por índice), lo que permite
    usar valores calculados como respaldo fila por fila.
    """
    c = _col(df, campo)
    if c is None:
        return default if isinstance(default, pd.Series) else pd.Series(float(default), index=df.index)
    serie = df[c]
    if not pd.api.types.is_numeric_dtype(serie):  # solo limpia símbolos si hace falta
        serie = serie.astype(str).str.replace(r"[$,\s]", "", regex=True)
    return pd.to_numeric(serie, errors="coerce").fillna(default)


def _normalizar_ids(df: pd.DataFrame) -> Tuple[pd.Series, pd.Series]:
    """Devuelve (id_original, id_limpio) de un DataFrame, listos para cruzar por ID.

    Quita ".0" (enteros leídos como float), el prefijo "PS-" y los ceros a la izquierda.
    """
    raw_id = _texto(df, "id").str.replace(r"\.0+$", "", regex=True)
    id_limpio = raw_id.str.replace("PS-", "", regex=False).str.lstrip("0")
    return raw_id, id_limpio.where(id_limpio != "", raw_id)


def _cruzar(mapa: Dict[str, Any], id_limpio: pd.Series, raw_id: pd.Series) -> pd.Series:
    """Busca cada fila en `mapa` por ID limpio y, si no está, por ID original.

    Devuelve NaN donde no hay coincidencia (el llamador decide el valor por defecto).
    """
    return id_limpio.map(mapa).astype(object).fillna(raw_id.map(mapa).astype(object))


# ----------------------------------------------------------------------------------
# CARGA DE DATOS (con caché)
# ----------------------------------------------------------------------------------
def _laminas_de_carpeta(carpeta: Path) -> List[Optional[Path]]:
    """Devuelve 4 posiciones (láminas 01..04); `None` si esa lámina no existe.

    Se recorre la carpeta una sola vez. Si hay varios archivos con el mismo prefijo,
    se prefiere JPG sobre PNG y, a igualdad, el primero por orden alfabético.
    """
    mejores: Dict[str, Tuple[Tuple[int, str], Path]] = {}
    with os.scandir(carpeta) as it:
        for e in it:
            prefijo = e.name[:2]
            if prefijo not in PREFIJOS_LAMINAS or not e.is_file():
                continue
            rango_ext = EXT_LAMINAS.get(os.path.splitext(e.name)[1].lower())
            if rango_ext is None:
                continue
            clave = (rango_ext, e.name.lower())
            if prefijo not in mejores or clave < mejores[prefijo][0]:
                mejores[prefijo] = (clave, Path(e.path))
    return [mejores[p][1] if p in mejores else None for p in PREFIJOS_LAMINAS]


@st.cache_data(show_spinner=False)
def indexar_carpetas_fotos(firma: Firma) -> Dict[str, List[Optional[Path]]]:
    """Devuelve {id: [lámina1, lámina2, lámina3, lámina4]} escaneando `data/catalogo_listo`.

    - Cada subcarpeta se asocia a su ID (primer número de 5-8 dígitos del nombre).
    - La lista siempre tiene 4 posiciones; las láminas faltantes son `None`
      (en v2 se "compactaban" y las etiquetas quedaban desfasadas).
    - El mismo ID se registra con todas sus variantes (`claves_id`).
    - `firma` solo sirve como llave de caché: cambia cuando cambian las carpetas.
    """
    mapa: Dict[str, List[Optional[Path]]] = {}
    if not DIR_CATALOGO_LISTO.is_dir():
        return mapa

    for carpeta in sorted(DIR_CATALOGO_LISTO.iterdir()):  # sorted: resultado determinista
        if not carpeta.is_dir():
            continue
        m = re.search(r"(\d{5,8})", carpeta.name)
        id_carpeta = m.group(1) if m else carpeta.name
        laminas = _laminas_de_carpeta(carpeta)
        if any(laminas):
            for k in claves_id(id_carpeta):
                mapa[k] = laminas
    return mapa


def _leer_sqlite() -> Tuple[Optional[pd.DataFrame], Dict[str, str], Dict[str, float]]:
    """Lee la BD. Devuelve (df_base, mapa_origen_catalogo, mapa_velocidad).

    `zapatos_candidatos` aporta los mapas {id: catálogo} y {id: velocidad}.
    `df_base` es la primera tabla financiera con datos (o `zapatos_candidatos`).
    """
    mapa_origen: Dict[str, str] = {}
    mapa_velocidad: Dict[str, float] = {}
    df_base: Optional[pd.DataFrame] = None

    if not RUTA_BD.exists():
        return df_base, mapa_origen, mapa_velocidad

    with closing(sqlite3.connect(RUTA_BD)) as conn:
        tablas = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}

        if "zapatos_candidatos" in tablas:
            cols = {c[1] for c in conn.execute("PRAGMA table_info(zapatos_candidatos)")}
            tiene_origen = "origen_catalogo" in cols
            tiene_vel = "velocidad_diaria" in cols
            if tiene_origen or tiene_vel:
                col_orig = "COALESCE(origen_catalogo, '')" if tiene_origen else "''"
                col_vel = "COALESCE(velocidad_diaria, 0.0)" if tiene_vel else "0.0"
                try:
                    filas = conn.execute(
                        f"SELECT id_ps, {col_orig}, {col_vel} FROM zapatos_candidatos"
                    ).fetchall()
                except sqlite3.Error:
                    filas = []
                for id_p, orig, vel in filas:
                    claves = claves_id(id_p)
                    if tiene_origen:
                        mapa_origen.update(dict.fromkeys(claves, formatear_nombre_catalogo(orig)))
                    if tiene_vel:
                        mapa_velocidad.update(dict.fromkeys(claves, float(vel or 0.0)))

        for tabla in TABLAS_FINANCIERAS:  # nombres fijos de la constante: seguro
            if tabla not in tablas:
                continue
            try:
                df_tmp = pd.read_sql_query(f"SELECT * FROM {tabla}", conn)
            except (sqlite3.Error, pd.errors.DatabaseError):
                continue
            if not df_tmp.empty:
                df_base = df_tmp
                break

        if df_base is None and "zapatos_candidatos" in tablas:
            try:
                df_tmp = pd.read_sql_query("SELECT * FROM zapatos_candidatos", conn)
            except (sqlite3.Error, pd.errors.DatabaseError):
                df_tmp = pd.DataFrame()
            if not df_tmp.empty:
                df_base = df_tmp

    return df_base, mapa_origen, mapa_velocidad


def _leer_csv(df_base: Optional[pd.DataFrame]) -> Optional[pd.DataFrame]:
    """Busca un CSV financiero en `data/`; si lo encuentra, reemplaza a `df_base`.

    Un CSV aplica si su nombre contiene alguna de `PALABRAS_CSV`, tiene >= 5 columnas
    y (no había `df_base` o el CSV trae columna de precio).
    """
    if not DIR_DATA.is_dir():
        return df_base
    for csv_path in sorted(DIR_DATA.glob("*.csv")):
        nombre = csv_path.name.lower()
        if any(k in nombre for k in PALABRAS_CSV_TALLAS) or not any(k in nombre for k in PALABRAS_CSV):
            continue  # los CSV de tallas se leen aparte (`cargar_tallas`)
        try:
            # utf-8-sig: tolera el BOM de los CSV exportados desde este panel.
            df_csv = pd.read_csv(csv_path, encoding="utf-8-sig")
        except (OSError, ValueError):  # ValueError incluye ParserError/EmptyDataError/Unicode
            continue
        if df_csv.empty or len(df_csv.columns) < 5:
            continue
        if df_base is None or _col(df_csv, "precio") is not None:
            return df_csv
    return df_base


def leer_fuentes() -> Tuple[Optional[pd.DataFrame], Dict[str, str], Dict[str, float]]:
    """Lee SQLite y CSV. Devuelve (df_base, mapa_origen_catalogo, mapa_velocidad)."""
    df_base, mapa_origen, mapa_velocidad = _leer_sqlite()
    return _leer_csv(df_base), mapa_origen, mapa_velocidad


# --- Existencias por talla -----------------------------------------------------------
_RE_TALLA_ANCHA = re.compile(r"^\d{1,2}([.,]\d)?$")  # nombres de columna tipo 22, 22.5, 23,5


def _clave_talla(talla: str) -> Tuple[int, float, str]:
    """Orden natural de tallas: numéricas de menor a mayor y luego las de texto (CH, M, G)."""
    try:
        return (0, float(talla), talla)
    except ValueError:
        return (1, 0.0, talla)


def _a_formato_largo(df: pd.DataFrame) -> Optional[pd.DataFrame]:
    """Acepta formato largo (id, talla, existencias) o ancho (una columna por talla)."""
    if _col(df, "talla") is not None:
        return df
    id_col = _col(df, "id")
    cols_talla = [c for c in df.columns if _RE_TALLA_ANCHA.match(str(c).strip())]
    if id_col is None or not cols_talla:
        return None
    return df.melt(id_vars=[id_col], value_vars=cols_talla, var_name="Talla", value_name="Existencias")


def _leer_tallas_crudo() -> Optional[pd.DataFrame]:
    """Primera fuente de tallas con datos: tablas de SQLite y, si no, CSV de `data/`."""
    if RUTA_BD.exists():
        with closing(sqlite3.connect(RUTA_BD)) as conn:
            tablas = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            for tabla in TABLAS_TALLAS:  # constante del código: seguro interpolarla
                if tabla not in tablas:
                    continue
                try:
                    df = pd.read_sql_query(f"SELECT * FROM {tabla}", conn)
                except (sqlite3.Error, pd.errors.DatabaseError):
                    continue
                largo = _a_formato_largo(df)
                if largo is not None and not largo.empty:
                    return largo
    if DIR_DATA.is_dir():
        for csv_path in sorted(DIR_DATA.glob("*.csv")):
            if not any(k in csv_path.name.lower() for k in PALABRAS_CSV_TALLAS):
                continue
            try:
                df = pd.read_csv(csv_path, encoding="utf-8-sig")
            except (OSError, ValueError):
                continue
            largo = _a_formato_largo(df)
            if largo is not None and not largo.empty:
                return largo
    return None


@st.cache_data(show_spinner=False)
def cargar_tallas(f_datos: Firma) -> pd.DataFrame:
    """Existencias por talla normalizadas: columnas `id`, `talla` (ordenada), `existencias`.

    Una fila por (id, talla); incluye tallas agotadas (existencias = 0). Devuelve un
    DataFrame vacío si no hay fuente de tallas. `f_datos` solo es llave de caché.
    """
    crudo = _leer_tallas_crudo()
    if crudo is None:
        return pd.DataFrame(columns=["id", "talla", "existencias"])

    _, id_limpio = _normalizar_ids(crudo)
    talla = (
        _texto(crudo, "talla").str.upper().str.replace(",", ".", regex=False)
        .str.replace(r"\.0+$", "", regex=True)  # "22.0" → "22"
    )
    existencias = _num(crudo, "existencias", 0).clip(lower=0).round().astype(int)

    out = pd.DataFrame({"id": id_limpio, "talla": talla, "existencias": existencias})
    out = out[(out["id"] != "") & (out["talla"] != "")]
    out = out.groupby(["id", "talla"], as_index=False)["existencias"].sum()  # une duplicados
    orden = sorted(out["talla"].unique(), key=_clave_talla)
    out["talla"] = pd.Categorical(out["talla"], categories=orden, ordered=True)
    return out.sort_values(["id", "talla"]).reset_index(drop=True)


def resumir_tallas(tallas: pd.DataFrame) -> pd.DataFrame:
    """Por ID: `Pares en Tienda` (suma) y `Tallas Disponibles` (texto "22(1) 23(3)").

    Solo cuentan las tallas con stock; el texto sigue el orden natural de tallas.
    """
    con_stock = tallas[tallas["existencias"] > 0]
    if con_stock.empty:
        return pd.DataFrame(columns=["Pares en Tienda", "Tallas Disponibles"])
    etiqueta = con_stock["talla"].astype(str) + "(" + con_stock["existencias"].astype(str) + ")"
    return pd.DataFrame(
        {
            "Pares en Tienda": con_stock.groupby("id", observed=True)["existencias"].sum(),
            "Tallas Disponibles": etiqueta.groupby(con_stock["id"]).agg(" ".join),
        }
    )


def tabla_tallas_ancha(df_vista: pd.DataFrame, tallas: pd.DataFrame) -> pd.DataFrame:
    """Un renglón por producto de `df_vista` y una columna por talla (0 = agotada o sin registro).

    Respeta el orden de `df_vista` y solo incluye las tallas que existen para esos productos.
    """
    ids = df_vista["ID Price Shoes"]
    ancho = (
        tallas[tallas["id"].isin(ids)]
        .pivot(index="id", columns="talla", values="existencias")
        .dropna(axis=1, how="all")
    )
    ancho.columns = ancho.columns.astype(str)  # evita columnas categóricas al unir
    base = df_vista[["ID Price Shoes", "Modelo"]]
    unido = base.merge(ancho, left_on="ID Price Shoes", right_index=True, how="left")
    cols_talla = list(ancho.columns)
    unido[cols_talla] = unido[cols_talla].fillna(0).astype(int)
    return unido.reset_index(drop=True)


@st.cache_data(show_spinner="Cargando inventario…")
def cargar_datos_inventario(f_datos: Firma, f_fotos: Firma) -> pd.DataFrame:
    """Inventario auditado cruzado con catálogo de origen, velocidad y fotografías HD.

    Devuelve un DataFrame con UNA fila por `ID Price Shoes` (si hay duplicados se queda
    la de mayor ganancia). Las columnas que empiezan con "_" son internas
    (`_num_fotos`, `_subir_src`, `_busqueda`) y no se muestran ni se exportan.

    `f_datos` y `f_fotos` solo son llaves de caché: cuando cambian, se vuelve a leer todo.
    """
    df_base, mapa_origen, mapa_velocidad = leer_fuentes()
    if df_base is None or df_base.empty:
        return pd.DataFrame()

    mapa_fotos = indexar_carpetas_fotos(f_fotos)
    conteo_fotos = {k: sum(p is not None for p in v) for k, v in mapa_fotos.items()}
    df = df_base.reset_index(drop=True)

    # ID original (sin ".0" que aparece cuando pandas lee enteros como float) y limpio
    raw_id, id_limpio = _normalizar_ids(df)

    num_fotos = _cruzar(conteo_fotos, id_limpio, raw_id).fillna(0).astype(int)
    costo = _num(df, "costo", 0.0)

    # Descarta filas sin ID, o sin costo y sin fotos (no hay nada que auditar)
    valido = (id_limpio != "") & ~((costo <= 0) & (num_fotos == 0))
    df, raw_id, id_limpio, num_fotos, costo = (x[valido] for x in (df, raw_id, id_limpio, num_fotos, costo))
    if df.empty:
        return pd.DataFrame()

    # Precio y ganancia: del origen si existen; si no, se calculan con la fórmula
    precio_fb, ganancia_fb = calcular_precio_y_ganancia_fallback(costo)
    precio = _num(df, "precio", precio_fb)
    ganancia = _num(df, "ganancia", ganancia_fb)
    stock = _num(df, "stock", 0).astype(int).clip(lower=0)

    velocidad_fb = _cruzar(mapa_velocidad, id_limpio, raw_id).fillna(0.0).astype(float)
    velocidad = _num(df, "velocidad", velocidad_fb).clip(lower=0.0)

    # NOTA: el SKU interno siempre es "PS-<id>": si el origen trae un SKU que no
    # empieza con "PS-", se reemplaza (comportamiento heredado de v2). Si ese SKU
    # debe conservarse, cambiar por: sku.where(sku != "", "PS-" + id_limpio).
    sku = _texto(df, "sku")
    sku = sku.where(sku.str.startswith("PS-"), "PS-" + id_limpio)

    modelo = _texto(df, "modelo")
    modelo = modelo.where(modelo != "", "Modelo " + id_limpio)

    # Catálogo: primero el mapa de SQLite; si no, el texto de la propia fila
    origen_raw = _texto(df, "catalogo")
    nombres = {o: formatear_nombre_catalogo(o) for o in origen_raw.unique()}
    catalogo = _cruzar(mapa_origen, id_limpio, raw_id).fillna(origen_raw.map(nombres))

    # "Subir a ML" inicial según el origen (True/False/NA si no viene la columna)
    col_subir = _col(df, "subir")
    if col_subir:
        subir_src = (
            df[col_subir].astype(str).str.strip().str.lower().isin(["true", "1", "si", "sí", "x", "yes"])
        ).astype("boolean")
    else:
        subir_src = pd.Series(pd.NA, index=df.index, dtype="boolean")

    margen = (ganancia / precio.where(precio > 0) * 100).fillna(0.0)

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
            "Margen (%)": margen.round(1),
            "Velocidad de Venta": velocidad.round(2),
            "Stock Publicable": stock,
            "_num_fotos": num_fotos,
            "_subir_src": subir_src,
        }
    )
    # Un mismo ID puede venir repetido: nos quedamos con la fila de mayor ganancia
    out = out.sort_values("Ganancia Neta", ascending=False, kind="stable").drop_duplicates("ID Price Shoes")

    # Existencias por talla: solo 2 columnas resumen (el detalle se ve en la galería)
    tallas = cargar_tallas(f_datos)
    if not tallas.empty:
        resumen = resumir_tallas(tallas)
        out["Pares en Tienda"] = out["ID Price Shoes"].map(resumen["Pares en Tienda"]).fillna(0).astype(int)
        out["Tallas Disponibles"] = out["ID Price Shoes"].map(resumen["Tallas Disponibles"]).fillna("")

    # Texto de búsqueda precalculado (ya en minúsculas): el filtro solo hace `contains`
    out["_busqueda"] = (
        out["ID Price Shoes"] + " " + out["SKU Interno"] + " " + out["Modelo"] + " " + out["Catálogo"]
    ).str.lower()
    return out.reset_index(drop=True)


def evaluar_semaforo(df: pd.DataFrame, min_stock: int, min_ganancia: float) -> pd.DataFrame:
    """Agrega `Semáforo` y `Diagnóstico Técnico` (vectorizado) según los umbrales dados.

    Prioridad del diagnóstico cuando no se recomienda: fotos → stock → margen.
    """
    fotos = df["_num_fotos"]
    stock = df["Stock Publicable"]
    fotos_ok = fotos >= FOTOS_REQUERIDAS
    stock_ok = stock >= min_stock
    listo = fotos_ok & stock_ok & (df["Ganancia Neta"] >= min_ganancia)

    req = FOTOS_REQUERIDAS
    txt_fotos = fotos.astype(str)
    # Se parte del caso de menor prioridad y cada `mask` lo sobrescribe si aplica.
    diagnostico = (
        ("Margen ajustado · Fotos (" + txt_fotos + f"/{req})")
        .mask(~stock_ok, "Stock bajo (" + stock.astype(str) + " pares) · Fotos (" + txt_fotos + f"/{req})")
        .mask(~fotos_ok, "Faltan fotografías HD (" + (req - fotos).astype(str) + f"/{req})")
        .mask(listo, f"✅ Listo para publicar ({req}/{req} HD)")
    )
    return df.assign(
        **{
            "Semáforo": np.where(listo, RECOMENDADO, NO_RECOMENDADO),
            "Diagnóstico Técnico": diagnostico,
        }
    )


@st.cache_data(show_spinner=False, max_entries=5)
def _a_csv(df: pd.DataFrame) -> bytes:
    """CSV (UTF-8 con BOM, abre bien en Excel). Cacheado para no regenerarlo en cada clic."""
    return df.to_csv(index=False).encode("utf-8-sig")


# ----------------------------------------------------------------------------------
# SELECCIÓN "SUBIR A ML" (persistente)
# ----------------------------------------------------------------------------------
def cargar_seleccion_guardada() -> Optional[Set[str]]:
    """IDs guardados en la tabla `seleccion_ml`, o None si nunca se ha guardado nada.

    Se distingue `None` (no hay tabla: usar la regla inicial) de `set()` (el usuario
    guardó una selección vacía a propósito).
    """
    if not RUTA_BD.exists():
        return None
    with closing(sqlite3.connect(RUTA_BD)) as conn:
        try:
            return {str(r[0]) for r in conn.execute("SELECT id_ps FROM seleccion_ml")}
        except sqlite3.Error:
            return None


def guardar_seleccion(ids: Set[str]) -> None:
    """Reemplaza el contenido de `seleccion_ml` por `ids`, en una sola transacción.

    NOTA: escribir en la BD cambia su fecha de modificación y, por tanto, la caché
    del inventario se recarga en el siguiente ciclo. Si el inventario es grande,
    considera guardar la selección en un archivo SQLite aparte.
    """
    with closing(sqlite3.connect(RUTA_BD)) as conn, conn:  # `with conn` = commit/rollback
        conn.execute(
            "CREATE TABLE IF NOT EXISTS seleccion_ml ("
            "id_ps TEXT PRIMARY KEY, actualizado TEXT DEFAULT CURRENT_TIMESTAMP)"
        )
        conn.execute("DELETE FROM seleccion_ml")
        conn.executemany("INSERT INTO seleccion_ml (id_ps) VALUES (?)", [(i,) for i in sorted(ids)])


def inicializar_estado(df_total: pd.DataFrame) -> None:
    """Crea `seleccion_ml` en la sesión (una sola vez).

    Orden de prioridad: selección guardada en BD → columna "Subir a ML" del origen →
    regla por defecto (4 fotos, stock y ganancia mínimos).
    """
    if "seleccion_ml" not in st.session_state:
        guardada = cargar_seleccion_guardada()
        if guardada is not None:
            st.session_state.seleccion_ml = guardada
        else:
            regla = (
                df_total["_num_fotos"].ge(FOTOS_REQUERIDAS)
                & df_total["Stock Publicable"].ge(STOCK_MIN_DEFAULT)
                & df_total["Ganancia Neta"].ge(GANANCIA_MIN_DEFAULT)
            )
            marcados = df_total["_subir_src"].fillna(regla).astype(bool)
            st.session_state.seleccion_ml = set(df_total.loc[marcados, "ID Price Shoes"])
    st.session_state.setdefault("ver_editor", 0)  # se incrementa para forzar un editor nuevo


def resetear_filtros() -> None:
    """Borra el estado de todos los filtros (se usa como callback de botón)."""
    for k in FILTRO_KEYS:
        st.session_state.pop(k, None)


def recargar_datos() -> None:
    """Callback del botón 'Recargar': limpia cachés y filtros (se ejecuta antes del rerun)."""
    st.cache_data.clear()
    resetear_filtros()


# ----------------------------------------------------------------------------------
# FILTROS (barra lateral)
# ----------------------------------------------------------------------------------
def _ajustar_estado(key: str, lo: float, hi: float) -> None:
    """Acota el valor guardado de un slider a [lo, hi].

    Si los datos cambian (p. ej. tras regenerar la BD) y el valor guardado queda fuera
    del nuevo rango, Streamlit puede lanzar una excepción. Solo se escribe si cambia.
    """
    valor = st.session_state.get(key)
    if valor is None:
        return
    acotar = lambda x: min(max(x, lo), hi)  # noqa: E731
    nuevo = tuple(acotar(v) for v in valor) if isinstance(valor, (tuple, list)) else acotar(valor)
    if nuevo != valor:
        st.session_state[key] = nuevo


def slider_rango(
    etiqueta: str, serie: pd.Series, key: str, formato: str = "%.0f", paso: float = 1.0
) -> Optional[pd.Series]:
    """Dibuja un slider de rango y devuelve la máscara booleana a aplicar.

    Los límites se calculan sobre el TOTAL (no sobre lo ya filtrado) para que el slider
    no "salte" al mover otros filtros. Devuelve None si no hay rango útil o si el
    usuario no lo ha movido (no hace falta filtrar).
    """
    lo, hi = float(np.floor(serie.min())), float(np.ceil(serie.max()))
    if not (np.isfinite(lo) and np.isfinite(hi)) or lo >= hi:
        return None
    _ajustar_estado(key, lo, hi)
    sel = st.sidebar.slider(etiqueta, lo, hi, (lo, hi), step=paso, format=formato, key=key)
    return None if tuple(sel) == (lo, hi) else serie.between(*sel)


def aplicar_filtros(df_total: pd.DataFrame, tallas: pd.DataFrame) -> pd.DataFrame:
    """Dibuja los filtros de la barra lateral y devuelve el DataFrame filtrado y ordenado.

    Todos los filtros se acumulan en UNA máscara booleana (`mask`) que se aplica al final.
    """
    sb = st.sidebar
    sb.header("🔎 Filtros de Auditoría")
    sb.button("🧹 Limpiar filtros", on_click=resetear_filtros, **ANCHO_TOTAL)

    mask = pd.Series(True, index=df_total.index)

    # --- Origen y catálogos --------------------------------------------------------
    es_pdf = df_total["Catálogo"] != SIN_CATALOGO
    conteo = df_total.loc[es_pdf, "Catálogo"].value_counts()
    tiene_pdfs = not conteo.empty
    origen = sb.radio(
        "Origen del producto",
        [ORIGEN_PDF, ORIGEN_TODO, ORIGEN_WEB],
        index=0 if tiene_pdfs else 1,
        key="f_origen",
    )
    if origen == ORIGEN_PDF and tiene_pdfs:
        mask &= es_pdf
    elif origen == ORIGEN_WEB:
        mask &= ~es_pdf

    oficiales = list(NOMBRES_CATALOGOS.values())
    opciones_cat = [c for c in oficiales if c in conteo.index] + [c for c in conteo.index if c not in oficiales]
    previos = st.session_state.get("f_catalogos")
    if previos:  # descarta catálogos que ya no existen en los datos
        vigentes = [c for c in previos if c in opciones_cat]
        if vigentes != previos:
            st.session_state["f_catalogos"] = vigentes
    elegidos = sb.multiselect(
        "📘 Catálogos (vacío = todos)",
        opciones_cat,
        format_func=lambda c: f"{c} ({conteo[c]} SKUs)",
        key="f_catalogos",
    )
    if elegidos:
        mask &= df_total["Catálogo"].isin(elegidos)

    # --- Semáforo y búsqueda -------------------------------------------------------
    filtro_sem = sb.radio("🚦 Semáforo", [SEM_TODOS, SEM_SOLO_OK, SEM_PENDIENTES], key="f_semaforo")
    if filtro_sem == SEM_SOLO_OK:
        mask &= df_total["Semáforo"] == RECOMENDADO
    elif filtro_sem == SEM_PENDIENTES:
        mask &= df_total["Semáforo"] != RECOMENDADO

    busqueda = sb.text_input("🔍 Buscar (ID, SKU, modelo o catálogo)", key="f_busqueda").strip().lower()
    for termino in busqueda.split():  # todas las palabras deben aparecer (en cualquier orden)
        mask &= df_total["_busqueda"].str.contains(termino, regex=False)

    # --- Talla disponible (solo si hay datos de tallas) ------------------------------
    if not tallas.empty:
        sb.markdown("**👟 Existencias por talla**")
        opciones_t = list(tallas["talla"].cat.categories)
        previas = st.session_state.get("f_tallas")
        if previas and [t for t in previas if t in opciones_t] != previas:
            st.session_state["f_tallas"] = [t for t in previas if t in opciones_t]
        sel_t = sb.multiselect("Talla disponible (vacío = cualquiera)", opciones_t, key="f_tallas")
        if sel_t:
            min_t = sb.number_input("Pares mínimos por talla", 1, 100, 1, key="f_tallas_min")
            todas = sb.checkbox("Debe tener todas las tallas elegidas", key="f_tallas_todas")
            ok = tallas[tallas["talla"].isin(sel_t) & (tallas["existencias"] >= min_t)]
            cuenta = ok["id"].value_counts()  # nº de tallas elegidas con stock, por ID
            ids_ok = cuenta.index[cuenta >= len(sel_t)] if todas else cuenta.index
            mask &= df_total["ID Price Shoes"].isin(ids_ok)

    # --- Rangos numéricos ----------------------------------------------------------
    sb.markdown("**💲 Costos, precios y márgenes**")
    for etiqueta, columna, key, formato in (
        ("Costo ($)", "Costo ($)", "f_costo", "%.0f"),
        ("Precio sugerido ($)", "Precio Sugerido", "f_precio", "%.0f"),
        ("Ganancia neta ($)", "Ganancia Neta", "f_ganancia", "%.0f"),
        ("Margen (%)", "Margen (%)", "f_margen", "%.0f%%"),
    ):
        m = slider_rango(etiqueta, df_total[columna], key, formato)
        if m is not None:
            mask &= m

    # --- Rotación, stock y fotos ---------------------------------------------------
    sb.markdown("**📈 Rotación e inventario**")
    # La velocidad nunca es negativa (se acota en la carga): el mínimo es 0.
    hi_vel = float(np.ceil(max(1.0, float(df_total["Velocidad de Venta"].max())) * 20.0) / 20.0)
    _ajustar_estado("f_velocidad", 0.0, hi_vel)
    r_vel = sb.slider(
        "🔥 Velocidad de venta (pares/día)",
        min_value=0.0,
        max_value=hi_vel,
        value=(0.0, hi_vel),
        step=0.05,
        format="%.2f",
        key="f_velocidad",
    )
    if tuple(r_vel) != (0.0, hi_vel):
        mask &= df_total["Velocidad de Venta"].between(*r_vel)

    max_stock = int(df_total["Stock Publicable"].max())
    if max_stock > 0:
        _ajustar_estado("f_stock", 0, max_stock)
        min_stock_f = sb.slider("Stock mínimo (pares)", 0, max_stock, 0, key="f_stock")
        if min_stock_f > 0:
            mask &= df_total["Stock Publicable"] >= min_stock_f

    rango_fotos = sb.select_slider(
        "📸 Fotos HD disponibles",
        options=list(range(FOTOS_REQUERIDAS + 1)),
        value=(0, FOTOS_REQUERIDAS),
        key="f_fotos",
    )
    if tuple(rango_fotos) != (0, FOTOS_REQUERIDAS):
        mask &= df_total["_num_fotos"].between(*rango_fotos)

    # --- Orden ---------------------------------------------------------------------
    orden = sb.selectbox("↕️ Ordenar por", OPCIONES_ORDEN, key="f_orden")
    ascendente = sb.checkbox("Orden ascendente", value=False, key="f_asc")

    df = df_total[mask]  # única copia del DataFrame
    if orden == ORDEN_DEFAULT:
        df = (
            df.assign(_r=df["Semáforo"] != RECOMENDADO)
            .sort_values(
                ["_r", "Ganancia Neta", "Velocidad de Venta", "Stock Publicable"],
                ascending=[True, False, False, False],
            )
            .drop(columns="_r")
        )
    else:
        df = df.sort_values(orden, ascending=ascendente)

    return df.reset_index(drop=True)


# ----------------------------------------------------------------------------------
# PANEL
# ----------------------------------------------------------------------------------
def renderizar_panel() -> None:
    """Punto de entrada: carga datos, dibuja filtros, métricas, matriz y galería."""
    st.title("👟 Zapatería Vallejo · Centro de Control e Inventario")

    df_cargado = cargar_datos_inventario(firma_datos(), firma_fotos())

    if df_cargado.empty:
        st.warning(
            "⚠️ Aún no se encontraron artículos con costo auditado en `data/zapateria_vallejo.db` "
            "o carpetas en `data/catalogo_listo`. Ejecuta primero `python pipeline_catalogo.py`."
        )
        return

    inicializar_estado(df_cargado)

    # Umbrales del semáforo configurables (se recalcula solo el semáforo, no la carga)
    with st.sidebar.expander("⚙️ Criterios del semáforo", expanded=False):
        min_stock = st.number_input("Stock mínimo para recomendar", 0, 500, STOCK_MIN_DEFAULT, 1, key="c_stock_min")
        min_ganancia = st.number_input(
            "Ganancia neta mínima ($)", 0.0, 5000.0, GANANCIA_MIN_DEFAULT, 10.0, key="c_ganancia_min"
        )
    df_total = evaluar_semaforo(df_cargado, int(min_stock), float(min_ganancia))

    # Existencias por talla: opcional (casilla apagada por defecto)
    tallas = cargar_tallas(firma_datos())
    if tallas.empty:
        ver_tallas = False
        st.sidebar.caption("📏 Sin datos de tallas (ver `TABLAS_TALLAS` en el código).")
    else:
        ver_tallas = st.sidebar.checkbox(
            "📏 Existencias por talla",
            value=False,
            key="ver_tallas",
            help="Agrega columnas, un filtro por talla y una tabla de existencias por talla.",
        )

    df = aplicar_filtros(df_total, tallas if ver_tallas else tallas.iloc[0:0])

    st.sidebar.button("🔄 Recargar datos y fotos HD", on_click=recargar_datos, **ANCHO_TOTAL)

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
    if b1.button("☑️ Marcar visibles", **ANCHO_TOTAL):
        st.session_state.seleccion_ml |= ids_vista
        st.session_state.ver_editor += 1
    if b2.button("⬜ Desmarcar visibles", **ANCHO_TOTAL):
        st.session_state.seleccion_ml -= ids_vista
        st.session_state.ver_editor += 1
    st.caption(
        "La selección se conserva al cambiar de filtros. Usa **Guardar selección** para no perderla al recargar la página."
    )

    columnas = [
        "Subir a ML", "ID Price Shoes", "SKU Interno", "Catálogo", "Modelo", "Enlace Original",
        "Semáforo", "Costo ($)", "Precio Sugerido", "Ganancia Neta", "Margen (%)",
        "Velocidad de Venta", "Stock Publicable", "Diagnóstico Técnico",
    ]
    if ver_tallas and "Pares en Tienda" in df.columns:
        i_stock = columnas.index("Stock Publicable") + 1
        columnas[i_stock:i_stock] = ["Pares en Tienda", "Tallas Disponibles"]
    df_editor = df.assign(**{"Subir a ML": df["ID Price Shoes"].isin(st.session_state.seleccion_ml)})[columnas]

    # La llave cambia cuando cambia el conjunto de filas visibles (o al marcar/desmarcar
    # visibles): así el editor se reinicia y refleja el estado real de la selección.
    # md5 y no `hash()`: es estable entre procesos y no depende de PYTHONHASHSEED.
    huella = hashlib.md5("|".join(df["ID Price Shoes"]).encode("utf-8")).hexdigest()[:12]

    editado = st.data_editor(
        df_editor,
        hide_index=True,
        height=400,
        column_config={
            "Subir a ML": st.column_config.CheckboxColumn("Subir a ML", default=False),
            "Enlace Original": st.column_config.LinkColumn("Enlace Original", display_text="Ver en PS"),
            "Costo ($)": st.column_config.NumberColumn("Costo ($)", format="$%.0f"),
            "Precio Sugerido": st.column_config.NumberColumn("Precio Sugerido", format="$%.0f"),
            "Ganancia Neta": st.column_config.NumberColumn("Ganancia Neta", format="$%.2f"),
            "Margen (%)": st.column_config.NumberColumn("Margen (%)", format="%.1f%%"),
            "Velocidad de Venta": st.column_config.NumberColumn(
                "Velocidad de Venta",
                format="%.2f /día",
                help="Pares vendidos por día estimados por el motor Bayesiano UCB",
            ),
        },
        disabled=[c for c in columnas if c != "Subir a ML"],
        key=f"editor_{st.session_state.ver_editor}_{huella}",
        **ANCHO_TOTAL,
    )

    # Sincroniza las casillas con el estado persistente (operaciones de conjuntos,
    # sin bucle): se quitan los IDs visibles y se vuelven a agregar los marcados.
    visibles = set(editado["ID Price Shoes"])
    marcados = set(editado.loc[editado["Subir a ML"], "ID Price Shoes"])
    st.session_state.seleccion_ml = (st.session_state.seleccion_ml - visibles) | marcados

    # --- ACCIONES SOBRE LA SELECCIÓN -----------------------------------------------
    # Se toma de df_total (no de df) para incluir también lo marcado pero ahora oculto por filtros.
    seleccion = df_total[df_total["ID Price Shoes"].isin(st.session_state.seleccion_ml)]
    a1, a2, a3 = st.columns([2, 1, 1])
    a1.markdown(
        f"**🛒 Seleccionados para ML:** {len(seleccion):,} SKUs · "
        f"{int((seleccion['Semáforo'] != RECOMENDADO).sum())} con observaciones"
    )
    a2.download_button(
        "⬇️ Descargar selección (CSV)",
        data=_a_csv(
            seleccion.drop(
                columns=[
                    c for c in seleccion.columns
                    if c.startswith("_") or (not ver_tallas and c in ("Pares en Tienda", "Tallas Disponibles"))
                ]
            )
        ),
        file_name="seleccion_mercadolibre.csv",
        mime="text/csv",
        disabled=seleccion.empty,
        **ANCHO_TOTAL,
    )
    if a3.button("💾 Guardar selección", **ANCHO_TOTAL):
        try:
            guardar_seleccion(st.session_state.seleccion_ml)
            st.toast("Selección guardada en la base de datos ✅")
        except sqlite3.Error as e:
            st.error(f"No se pudo guardar la selección: {e}")

    st.markdown("---")

    # --- TABLA DE EXISTENCIAS POR TALLA (opcional) -----------------------------------
    if ver_tallas:
        st.subheader("📏 Tabla de Existencias por Talla")
        vista = df.head(MAX_FILAS_TABLA_TALLAS)
        tabla = tabla_tallas_ancha(vista, tallas)
        if len(df) > len(vista):
            st.caption(f"Se muestran los primeros {len(vista):,} de {len(df):,} productos; afina los filtros para ver el resto.")
        st.dataframe(tabla, hide_index=True, height=300, **ANCHO_TOTAL)
        st.download_button(
            "⬇️ Descargar tabla de tallas (CSV)",
            data=_a_csv(tabla),
            file_name="existencias_por_talla.csv",
            mime="text/csv",
        )
        st.markdown("---")

    # --- GALERÍA -------------------------------------------------------------------
    st.subheader("📸 Galería de Fotografías HD")

    df_fotos = df[df["_num_fotos"] > 0]
    if df_fotos.empty:
        st.info(
            "Aún no hay carruseles generados para los SKUs de este filtro. "
            "Ejecuta `python paso6_estudio_fotografico.py` para generar sus 4 láminas HD."
        )
        return

    filas = df_fotos.set_index("ID Price Shoes")
    # Etiquetas precalculadas: `format_func` se llama por cada opción en cada ciclo, y un
    # `.loc` por opción sería lento con miles de SKUs.
    # NOTA: con miles de opciones el selectbox pesa; si el inventario crece, filtra antes
    # o limita las opciones (p. ej. `df_fotos.head(500)`).
    etiquetas = dict(zip(filas.index, filas.index + " · " + filas["Modelo"] + " (" + filas["Catálogo"] + ")"))
    id_sel = st.selectbox(
        "Selecciona un producto para inspeccionar sus 4 láminas HD:",
        options=list(filas.index),
        format_func=etiquetas.get,
    )
    fila = filas.loc[id_sel]

    mapa_fotos = indexar_carpetas_fotos(firma_fotos())
    laminas = mapa_fotos.get(id_sel) or mapa_fotos.get(f"PS-{id_sel}") or [None] * len(PREFIJOS_LAMINAS)

    # Existencias por talla del producto seleccionado (incluye tallas agotadas)
    det = tallas[tallas["id"] == id_sel] if not tallas.empty else tallas
    tallas_en_stock = det.loc[det["existencias"] > 0, "talla"].astype(str).tolist()

    # ── Bloque 1 · VISTA DEL CLIENTE ─────────────────────────────────────────────
    # Solo lo que se publica: láminas y nombres de las tallas con stock.
    # Nunca cantidades, costos ni ganancias (eso va en el bloque de uso interno).
    with st.container(border=True):
        st.markdown("**🛍️ Vista del cliente** · lo que se publica")
        for i, col in enumerate(st.columns(len(PREFIJOS_LAMINAS))):
            with col:
                ruta = laminas[i]
                if ruta is not None and ruta.exists():
                    st.image(str(ruta), caption=ETIQUETAS_LAMINAS[i], **ANCHO_TOTAL)
                else:
                    st.warning(f"Falta lámina {i + 1}: {ETIQUETAS_LAMINAS[i]}")
        if not tallas.empty:
            if det.empty:
                st.caption("👟 Tallas en stock: sin información para este producto.")
            elif tallas_en_stock:
                st.markdown("✅ **Tallas disponibles en stock:** " + " · ".join(tallas_en_stock))
            else:
                st.markdown("⚠️ **Sin tallas disponibles en stock**")

    # ── Bloque 2 · USO INTERNO ───────────────────────────────────────────────────
    # Datos para decidir si se publica; nada de esto se comparte con el cliente.
    with st.container(border=True):
        st.markdown("**🔒 Uso interno** · apoyo para decidir, no se publica")
        m1, m2, m3, m4, m5 = st.columns(5)
        m1.metric("Costo", f"${fila['Costo ($)']:,.0f}")
        m2.metric("Precio sugerido", f"${fila['Precio Sugerido']:,.0f}")
        m3.metric("Ganancia neta", f"${fila['Ganancia Neta']:,.2f}", f"{fila['Margen (%)']:.1f}%")
        m4.metric("Velocidad de venta", f"{fila['Velocidad de Venta']:.2f} /día")
        m5.metric("Stock", f"{int(fila['Stock Publicable'])} pares")

        if not tallas.empty:
            st.markdown("**🏬 Existencias en tienda por talla (pares)**")
            if det.empty:
                st.caption("Sin existencias registradas por talla para este producto.")
            else:
                st.dataframe(
                    pd.DataFrame([det["existencias"].to_numpy()], columns=det["talla"].astype(str), index=["Pares"]),
                    **ANCHO_TOTAL,
                )
                agotadas = det.loc[det["existencias"] == 0, "talla"].astype(str).tolist()
                st.caption(
                    f"{int(det['existencias'].sum())} pares en {len(tallas_en_stock)} de {len(det)} tallas"
                    + (f" · agotadas: {', '.join(agotadas)}" if agotadas else "")
                )


if __name__ == "__main__":
    renderizar_panel()