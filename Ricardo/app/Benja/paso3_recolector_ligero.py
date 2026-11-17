"""
========================================================================================
PASO 3: RECOLECTOR LIGERO ANTI-BAN (EXCLUSIVO PARA LOS 44 CATÁLOGOS PDF OFICIALES)
========================================================================================
- Descarta y elimina de SQLite y de `data/catalogo_listo` cualquier SKU ajeno a los 44 PDFs
  (con respaldo previo de la BD y protecciones contra borrados masivos por error).
- Barre en orden de prioridad comercial todos los SKUs de los 44 catálogos oficiales.
- Extrae costo de socio, URL canónica real, nombre limpio del modelo y stock de Vallejo
  tanto para calzado (15.0 a 32.0) como para ropa, lencería, calcetería y jeans (CH, M, G, XG, UNI).
========================================================================================
"""

from __future__ import annotations

from datetime import datetime
import math
import os
from pathlib import Path
import random
import re
import shutil
import sqlite3
import time
from typing import Dict, List, Tuple

from playwright.sync_api import sync_playwright

RUTA_BD = "data/zapateria_vallejo.db"
RUTA_SESION = "data/sesion_vallejo.json"
DIR_CATALOGO_LISTO = Path("data/catalogo_listo")

# ===========================================================================
# PARÁMETROS DE OPERACIÓN
# ===========================================================================
LOTE_MICRO_SESION = 60
MODO_INVISIBLE = True
# Si tantos SKUs seguidos no devuelven datos, se detiene la sesión (sesión vencida o bloqueo)
MAX_SIN_DATOS_SEGUIDOS = 10
# Seguridad: si TODAS las filas de la BD parecieran ajenas a los 44 PDFs, no se borra nada
PERMITIR_BORRAR_TODO = False
# Validación del costo de socio contra el precio público (costo / público).
# En fichas reales el costo de socio queda entre ~0.69 y ~0.85 del precio público.
RATIO_COSTO_MIN_ESPERADO = 0.50
RATIO_COSTO_MAX_ESPERADO = 0.90

# Orden de prioridad comercial para barrer los 44 catálogos PDF oficiales
CATALOGOS_PRIORIDAD: List[Tuple[str, str]] = [
    ("1342200", "1342200 - Caballeros"),
    ("1370231", "1370231 - Escolar"),
    ("1346256", "1346256 - Urbano"),
    ("1345253", "1345253 - Botas"),
    ("1346284", "1346284 - Vestir Casual"),
    ("1348737", "1348737 - Vestir"),
    ("1348757", "1348757 - Man"),
    ("1348783", "1348783 - Kids"),
    ("1378033", "1378033 - Kids Todo en Uno"),
    ("1348800", "1348800 - Licencias"),
    ("1353843", "1353843 - Confort"),
    ("1380354", "1380354 - Reebok Impulsados"),
    ("1380332", "1380332 - Goodyear Botas"),
    ("1380328", "1380328 - Botas Largas"),
    ("1380359", "1380359 - Botas Kits"),
    ("1380363", "1380363 - Basicos Botines"),
    ("1380367", "1380367 - Botas Texano"),
    ("1368789", "1368789 - Sandalias"),
    ("1353557", "1353557 - Juvenil"),
    ("1381342", "1381342 - Ella"),
    ("1380337", "1380337 - Coleccion Thalia"),
    ("1380341", "1380341 - Belinda Fashion & Chic"),
    ("1377702", "1377702 - Exclusivos"),
    ("1380516", "1380516 - Oportunidades Urbano"),
    ("1380358", "1380358 - Importados Fall Ofertas"),
    ("1381316", "1381316 - Med Chef"),
    ("1353556", "1353556 - Calzado y Accesorios"),
    ("1376435", "1376435 - Todo en Uno"),
    ("1348749", "1348749 - NBA"),
    ("1348753", "1348753 - NFL"),
    ("1368800", "1368800 - Prokennex Mini"),
    ("1368804", "1368804 - Pop Universe Mini"),
    ("1368805", "1368805 - Urban Lifestyle Mini"),
    ("1352311", "1352311 - Seven Jeans"),
    ("1381338", "1381338 - Jeans"),
    ("1348733", "1348733 - Abrigador"),
    ("1348804", "1348804 - Pijamas"),
    ("1380996", "1380996 - Love to Lounge"),
    ("1381346", "1381346 - Basicos OID6"),
    ("1381350", "1381350 - Mushu Esenciales Home"),
    ("1348779", "1348779 - Accesorios"),
    ("1380346", "1380346 - Navidad"),
    ("1380350", "1380350 - Navidad Home"),
    ("1381556", "1381556 - Navidad Kids"),
]

MAPA_CATALOGOS: Dict[str, str] = dict(CATALOGOS_PRIORIDAD)
ORDEN_PRIORIDAD_CAT: Dict[str, int] = {
    nom: idx for idx, (_, nom) in enumerate(CATALOGOS_PRIORIDAD, start=1)
}

# Tallas válidas: calzado 10.0-99.5, letras (XS a 4XG, ECH, EG, UNI...), combos (CH/M) y
# numeración de ropa/jeans (1-60, incluye pares e impares: 28, 30, 31, 33, 5, 7...)
PATRON_TALLA_VALIDA = re.compile(
    r"^(?:\d{2}\.[05]"
    r"|X{0,2}S|X{0,2}L|X?CH|ECH|MED|M|G|XG|XXG|[2-4]X[GL]|EG|EEG"
    r"|UNI|UNITALLA|CH/M|M/G|G/XG"
    r"|[1-9]|[1-5]\d|60)$",
    re.IGNORECASE,
)


# ===========================================================================
# BASE DE DATOS
# ===========================================================================
def asegurar_estructura_bd(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS zapatos_candidatos (
            id_ps TEXT PRIMARY KEY,
            nombre_modelo TEXT,
            costo_socio REAL DEFAULT 0.0,
            url_real TEXT,
            activo_monitoreo INTEGER DEFAULT 1,
            alpha REAL DEFAULT 1.0,
            beta REAL DEFAULT 2.0,
            velocidad_diaria REAL DEFAULT 0.5,
            stock_total_anterior INTEGER DEFAULT -1,
            ultima_sincronizacion TIMESTAMP,
            tier TEXT DEFAULT 'B',
            origen_catalogo TEXT DEFAULT ''
        )
        """
    )

    columnas_existentes = {
        col[1] for col in conn.execute("PRAGMA table_info(zapatos_candidatos)").fetchall()
    }
    nuevas_columnas = {
        "nombre_modelo": "TEXT",
        "costo_socio": "REAL DEFAULT 0.0",
        "url_real": "TEXT",
        "activo_monitoreo": "INTEGER DEFAULT 1",
        "alpha": "REAL DEFAULT 1.0",
        "beta": "REAL DEFAULT 2.0",
        "velocidad_diaria": "REAL DEFAULT 0.5",
        "stock_total_anterior": "INTEGER DEFAULT -1",
        "ultima_sincronizacion": "TIMESTAMP",
        "tier": "TEXT DEFAULT 'B'",
        "origen_catalogo": "TEXT DEFAULT ''",
        "precio_publico": "REAL DEFAULT 0.0",
        "costo_validacion": "TEXT DEFAULT ''",
    }
    for col, tipo in nuevas_columnas.items():
        if col not in columnas_existentes:
            conn.execute(f"ALTER TABLE zapatos_candidatos ADD COLUMN {col} {tipo};")

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS historial_stock_21_dias (
            id_ps TEXT,
            talla TEXT,
            stock_vallejo INTEGER,
            fecha TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_hist_stock_id_ps ON historial_stock_21_dias(id_ps)")
    conn.commit()


def normalizar_id_sku(valor: object) -> str:
    """Quita el prefijo 'PS-' (si existe) para comparar SKUs contra nombres de carpeta."""
    return re.sub(r"^PS-", "", str(valor).strip(), flags=re.IGNORECASE)


def extraer_id_catalogo(texto_origen: str) -> str:
    texto = str(texto_origen).strip()
    m = re.search(r"(\d{7})", texto)
    return m.group(1) if m else re.sub(r"\.pdf$", "", texto, flags=re.IGNORECASE).strip()


def respaldar_bd(conn: sqlite3.Connection) -> bool:
    """Respaldo diario (una vez por día) antes de borrar filas. Devuelve True si hay respaldo."""
    destino = Path("data") / f"respaldo_antes_de_depurar_{datetime.now():%Y%m%d}.db"
    if destino.exists():
        return True
    try:
        respaldo = sqlite3.connect(destino)
        try:
            conn.backup(respaldo)
        finally:
            respaldo.close()
        print(f"💾 Respaldo de seguridad creado: {destino}")
        return True
    except Exception as e:
        print(f"   ⚠️ No se pudo crear el respaldo ({e}). Se omite el borrado de datos.")
        return False


def _clasificar_filas_por_catalogo(filas) -> Tuple[List[Tuple[str]], List[Tuple[str, str]], set]:
    """
    Separa los SKUs oficiales de los ajenos a los 44 catálogos.
    Devuelve (ids_borrar, actualizaciones de nombre de catálogo, ids_validos).
    """
    ids_borrar: List[Tuple[str]] = []
    actualizaciones: List[Tuple[str, str]] = []
    ids_validos: set[str] = set()

    for id_ps, orig in filas:
        id_str = str(id_ps).strip()
        texto_orig = str(orig).strip()
        nom_oficial = MAPA_CATALOGOS.get(extraer_id_catalogo(texto_orig))
        if nom_oficial is None:
            ids_borrar.append((id_str,))
            continue
        id_norm = normalizar_id_sku(id_str)
        ids_validos.update({id_str, id_norm, id_norm.lstrip("0") or id_norm})
        if texto_orig != nom_oficial:
            actualizaciones.append((nom_oficial, id_str))

    return ids_borrar, actualizaciones, ids_validos


def _limpiar_tallas_invalidas(cur: sqlite3.Cursor) -> None:
    """Borra tallas basura (ni decimales ni tallas válidas de ropa) y reinicia esos SKUs."""
    filas_tallas = cur.execute("SELECT rowid, id_ps, talla FROM historial_stock_21_dias").fetchall()
    basura = [
        (int(rowid), str(s_id))
        for rowid, s_id, t_val in filas_tallas
        if not PATRON_TALLA_VALIDA.match(str(t_val).strip())
    ]
    if not basura:
        return
    cur.executemany("DELETE FROM historial_stock_21_dias WHERE rowid = ?", [(rowid,) for rowid, _ in basura])
    cur.executemany(
        """
        UPDATE zapatos_candidatos
        SET ultima_sincronizacion = NULL, stock_total_anterior = -1
        WHERE id_ps = ?
        """,
        [(s,) for s in {s_id for _, s_id in basura}],
    )


def _es_carpeta_ajena(nombre_carpeta: str, ids_validos: set) -> bool:
    m_id = re.search(r"(\d{5,8})", nombre_carpeta)
    cid = m_id.group(1) if m_id else ""
    return not cid or (cid not in ids_validos and (cid.lstrip("0") or cid) not in ids_validos)


def _borrar_carpetas_ajenas(ids_validos: set) -> int:
    """Elimina de `data/catalogo_listo` las carpetas que no estén en ids_validos. Devuelve cuántas."""
    if not ids_validos or not DIR_CATALOGO_LISTO.exists():
        return 0
    carpetas_borradas = 0
    for d in list(DIR_CATALOGO_LISTO.iterdir()):
        if d.is_dir() and _es_carpeta_ajena(d.name, ids_validos):
            shutil.rmtree(d, ignore_errors=True)
            carpetas_borradas += 1
    return carpetas_borradas


def depurar_solo_44_catalogos(conn: sqlite3.Connection) -> None:
    """
    Elimina de SQLite y de `data/catalogo_listo` cualquier producto que NO provenga
    de los 44 catálogos PDF oficiales, y estandariza el nombre del catálogo.
    """
    cur = conn.cursor()
    antes = cur.execute("SELECT COUNT(*) FROM zapatos_candidatos").fetchone()[0]
    filas = cur.execute(
        "SELECT id_ps, COALESCE(origen_catalogo, '') FROM zapatos_candidatos"
    ).fetchall()
    ids_borrar, actualizaciones, ids_validos = _clasificar_filas_por_catalogo(filas)

    # Protección: si TODO parece ajeno, casi seguro es un problema de formato en origen_catalogo
    if filas and len(ids_borrar) == len(filas) and not PERMITIR_BORRAR_TODO:
        print(
            "⚠️ Ninguna fila de `zapatos_candidatos` coincide con los 44 catálogos oficiales. "
            "Se omite la depuración para no borrar todo por error; revisa la columna origen_catalogo."
        )
        return

    # Sin respaldo de la BD no se borra nada (ni filas ni carpetas)
    puede_borrar = not ids_borrar or respaldar_bd(conn)
    if not puede_borrar:
        ids_borrar = []

    cur.executemany("UPDATE zapatos_candidatos SET origen_catalogo = ? WHERE id_ps = ?", actualizaciones)
    cur.executemany("DELETE FROM zapatos_candidatos WHERE id_ps = ?", ids_borrar)
    cur.executemany("DELETE FROM historial_stock_21_dias WHERE id_ps = ?", ids_borrar)
    _limpiar_tallas_invalidas(cur)

    conn.commit()
    despues = cur.execute("SELECT COUNT(*) FROM zapatos_candidatos").fetchone()[0]
    carpetas_borradas = _borrar_carpetas_ajenas(ids_validos) if puede_borrar else 0

    if ids_borrar or carpetas_borradas > 0:
        print(
            f"🧹 Depuración completada: {len(ids_borrar)} SKUs web descartados de SQLite "
            f"({antes:,} -> {despues:,} oficiales) y {carpetas_borradas} carpetas externas eliminadas."
        )
    else:
        print(f"📦 Catálogo oficial limpio en SQLite: {despues:,} SKUs exclusivos de los 44 PDFs.")


def _datos_base_lote(fila) -> Tuple[str, str, str, float, float, int, object, str]:
    """Normaliza una fila de `zapatos_candidatos` para armar el lote de auditoría."""
    id_ps, nombre, url, alpha, beta, stock_ant, ult_sinc, _costo, origen_cat = fila
    id_str = str(id_ps).strip()
    alpha = float(alpha) if alpha else 1.0
    beta = float(beta) if beta else 2.0
    stock_ant = int(stock_ant if stock_ant is not None else -1)

    # Si el SKU del PDF aún no tenía URL directa, usar el buscador oficial por ID
    url_txt = str(url).strip() if url else ""
    url_usar = url_txt if url_txt.startswith("http") else f"https://www.priceshoes.com/buscar?q={id_str}"
    nombre_usar = str(nombre).strip() if nombre else f"Modelo {id_str}"
    return id_str, nombre_usar, url_usar, alpha, beta, stock_ant, ult_sinc, origen_cat


def _dias_sin_ver(ult_sinc: object, ahora: datetime) -> float:
    try:
        fecha_ant = datetime.fromisoformat(str(ult_sinc))
        return max(0.05, (ahora - fecha_ant).total_seconds() / 86400.0)
    except Exception:
        return 5.0


def _puntaje_ucb(alpha: float, beta: float, dias_sin_ver: float) -> float:
    explotacion = alpha / beta
    exploracion = 1.41 * (math.sqrt(alpha) / beta)
    return (explotacion + exploracion) * math.log(1.0 + dias_sin_ver)


def seleccionar_lote_ucb(conn: sqlite3.Connection, limite: int = LOTE_MICRO_SESION):
    """
    Selecciona el lote de auditoría exclusivamente entre los 44 catálogos PDF oficiales,
    ordenando por:
      1. Nivel Prioritario: SKUs nunca sincronizados, siguiendo CATALOGOS_PRIORIDAD.
      2. Nivel Re-Auditoría UCB Bayesiana para SKUs ya sincronizados (incluye agotados,
         para que no acaparen el lote en cada corrida).
    """
    filas = conn.execute(
        """
        SELECT id_ps,
               COALESCE(nombre_modelo, ''),
               COALESCE(url_real, ''),
               COALESCE(alpha, 1.0),
               COALESCE(beta, 2.0),
               COALESCE(stock_total_anterior, -1),
               ultima_sincronizacion,
               COALESCE(costo_socio, 0.0),
               COALESCE(origen_catalogo, '')
        FROM zapatos_candidatos
        WHERE activo_monitoreo = 1
          AND origen_catalogo IS NOT NULL
          AND TRIM(origen_catalogo) != ''
        """
    ).fetchall()

    ahora = datetime.now()
    virgenes_por_catalogo = []
    auditados_ucb = []

    for fila in filas:
        id_str, nombre_usar, url_usar, alpha, beta, stock_ant, ult_sinc, origen_cat = _datos_base_lote(fila)

        # Nivel 1: SKUs vírgenes del catálogo PDF (nunca sincronizados)
        if not ult_sinc:
            orden_cat = ORDEN_PRIORIDAD_CAT.get(str(origen_cat).strip(), 999)
            score_virgen = 1000.0 - (orden_cat * 5.0)
            virgenes_por_catalogo.append(
                (orden_cat, id_str, score_virgen, nombre_usar, url_usar, alpha, beta, stock_ant, 15.0, origen_cat)
            )
            continue

        # Nivel 2: SKUs ya auditados puntuados por UCB Bayesiano
        dias_sin_ver = _dias_sin_ver(ult_sinc, ahora)
        auditados_ucb.append(
            (_puntaje_ucb(alpha, beta, dias_sin_ver), id_str, nombre_usar, url_usar, alpha, beta, stock_ant,
             dias_sin_ver, origen_cat)
        )

    # Primero los vírgenes, respetando estrictamente la prioridad de los 44 catálogos
    virgenes_por_catalogo.sort(key=lambda x: (x[0], x[1]))
    auditados_ucb.sort(key=lambda x: x[0], reverse=True)

    lote_final = [
        (item[2], item[1], item[3], item[4], item[5], item[6], item[7], item[8], item[9])
        for item in virgenes_por_catalogo[:limite]
    ]

    if len(lote_final) < limite:
        lote_final.extend(auditados_ucb[: limite - len(lote_final)])

    return lote_final


# ===========================================================================
# EXTRACCIÓN DESDE LA PÁGINA
# ===========================================================================
def extraer_precio_publico(html: str, texto_pagina: str, id_ps: str) -> float:
    """
    Precio público de la ficha. Primero lo busca en los datos estructurados del HTML
    ("price": 799) y, si no aparece, en el texto visible justo después de "ID <sku>".
    Devuelve 0.0 si no se pudo leer.
    """
    for m in re.finditer(r'"price"\s*:\s*"?(\d+(?:\.\d+)?)"?', html or ""):
        valor = float(m.group(1))
        if valor > 0:
            return valor

    m_txt = re.search(
        rf"ID\s*:?\s*{re.escape(str(id_ps))}\s*\$\s*([\d,]+(?:\.\d{{2}})?)",
        texto_pagina or "",
        re.IGNORECASE,
    )
    if m_txt:
        return float(m_txt.group(1).replace(",", ""))
    return 0.0


def validar_costo_socio(costo_socio: float, precio_publico: float) -> Tuple[str, str]:
    """
    Contrasta el costo de socio con el precio público. Devuelve (estado, mensaje):
      'ok'          -> el costo está en el rango esperado respecto al público
      'revisar'     -> descuento de socio inusualmente chico o grande
      'sospechoso'  -> el costo es mayor o igual al precio público
      'sin_publico' -> no se pudo leer el precio público para comparar
      'sin_lectura' -> no se leyó costo de socio en esta visita (se conserva el anterior)
    """
    if costo_socio <= 0:
        return "sin_lectura", "No se leyó costo de socio; se conserva el anterior."
    if precio_publico <= 0:
        return "sin_publico", "No se pudo leer el precio público para validar el costo."

    ratio = costo_socio / precio_publico
    if ratio >= 1.0:
        return "sospechoso", (
            f"Costo ${costo_socio:,.0f} >= público ${precio_publico:,.0f}: "
            "posible lectura de otro producto o precio desactualizado."
        )
    if ratio > RATIO_COSTO_MAX_ESPERADO:
        return "revisar", f"Descuento de socio de solo {(1 - ratio) * 100:.0f}% sobre ${precio_publico:,.0f}."
    if ratio < RATIO_COSTO_MIN_ESPERADO:
        return "revisar", f"Descuento de socio de {(1 - ratio) * 100:.0f}% sobre ${precio_publico:,.0f}: inusualmente alto."
    return "ok", f"Costo validado: {(1 - ratio) * 100:.0f}% bajo el público ${precio_publico:,.0f}."


PATRON_COSTO_SOCIO = re.compile(r"Socio:\s*[\r\n\s]*\$\s*([\d,]+(?:\.\d{2})?)", re.IGNORECASE)
PATRON_BLOQUE_VALLEJO = re.compile(
    r"Tienda\s+([\s\S]+?)\s+Ecommerce[\s\S]+?Vallejo\s*(?:Tu tienda)?\s*([\s\S]+)",
    re.IGNORECASE,
)
# La fila de Vallejo termina donde empieza la siguiente tienda del modal
PATRON_FIN_FILA_VALLEJO = re.compile(
    r"(?:Centro|Naucalpan|Olivar|Ecatepec|Tlalnepantla|Iztapalapa|Arcos|Puebla|Le[oó]n|Guadalajara|a\s+\d+\s*km)",
    re.IGNORECASE,
)


def _abrir_ficha_desde_busqueda(page, id_ps: str) -> None:
    """Si entramos por URL de búsqueda (/buscar?q=...), abre la ficha del producto si aparece."""
    try:
        if "/buscar" not in page.url:
            return
        enlace_prod = page.locator(f"a[href*='{id_ps}']").first
        if enlace_prod.count() == 0:
            return
        href_p = enlace_prod.get_attribute("href")
        if href_p:
            url_destino = href_p if href_p.startswith("http") else f"https://www.priceshoes.com{href_p}"
            page.goto(url_destino, timeout=30000, wait_until="domcontentloaded")
    except Exception:
        pass


def _esperar_bloque_socio(page) -> None:
    try:
        page.wait_for_function(
            "document.body && document.body.innerText.includes('Socio')",
            timeout=8000,
        )
    except Exception:
        pass


def _leer_titulo_h1(page) -> str:
    """Título H1 real de la ficha, o '' si no está disponible."""
    try:
        loc_h1 = page.locator("h1").first
        if loc_h1.count() > 0:
            txt_h1 = loc_h1.inner_text().strip()
            if txt_h1 and len(txt_h1) > 3:
                return txt_h1
    except Exception:
        pass
    return ""


def extraer_costo_socio(texto_pagina: str) -> float:
    match_socio = PATRON_COSTO_SOCIO.search(texto_pagina)
    return float(match_socio.group(1).replace(",", "")) if match_socio else 0.0


def _leer_precio_publico(page, texto_pagina: str, id_ps: str) -> float:
    try:
        return extraer_precio_publico(page.content(), texto_pagina, id_ps)
    except Exception:
        return extraer_precio_publico("", texto_pagina, id_ps)


def _abrir_modal_tiendas(page) -> None:
    try:
        btn_disp = page.locator("text=Ver disponibilidad en tiendas").first
        if btn_disp.count() > 0:
            btn_disp.scroll_into_view_if_needed()
            page.wait_for_timeout(500)
            btn_disp.click(force=True)

            page.wait_for_function(
                "document.body.innerText.includes('Ecommerce') || document.body.innerText.includes('TIENDAS CERCANAS')",
                timeout=8000,
            )
            page.wait_for_timeout(1200)
    except Exception as e:
        print(f"      ℹ️ Aviso al abrir modal de tiendas: {e}")


def _tallas_del_encabezado(encabezado_str: str) -> List[str]:
    """Tallas de la cabecera de la tabla: calzado con decimal o, si no hay, tallas de ropa."""
    # 1. Intentar primero tallas de calzado con punto decimal (.0 o .5)
    tallas_detectadas = re.findall(r"\b\d{2}\.[05]\b", encabezado_str)
    if tallas_detectadas:
        return tallas_detectadas

    # 2. Si es ropa, jeans, calcetería o accesorio (sin decimales), extraer tokens válidos del encabezado
    tokens_enc = [t.strip().upper() for t in re.split(r"[\s\t\r\n]+", encabezado_str) if t.strip()]
    return [t for t in tokens_enc if PATRON_TALLA_VALIDA.match(t)]


def extraer_tallas_vallejo(texto_modal: str) -> Dict[str, int]:
    """Existencias por talla de la fila Vallejo a partir del texto del modal de tiendas."""
    if "TIENDAS CERCANAS" in texto_modal.upper():
        zona_tabla = re.split(r"TIENDAS CERCANAS[^\n]*", texto_modal, flags=re.IGNORECASE)[-1]
    else:
        zona_tabla = texto_modal

    match_bloque = PATRON_BLOQUE_VALLEJO.search(zona_tabla)
    if not match_bloque:
        return {}

    tallas_detectadas = _tallas_del_encabezado(match_bloque.group(1))
    fila_vallejo_limpia = PATRON_FIN_FILA_VALLEJO.split(match_bloque.group(2))[0]
    tokens_vallejo = re.findall(r"(?:\b\d+\b|—|--|-)", fila_vallejo_limpia)

    tallas_vallejo: Dict[str, int] = {}
    for i, talla in enumerate(tallas_detectadas):
        tok = tokens_vallejo[i] if i < len(tokens_vallejo) else ""
        tallas_vallejo[talla] = int(tok) if tok.isdigit() else 0
    return tallas_vallejo


def extraer_datos_reales_dom(page, id_ps: str) -> Tuple[float, Dict[str, int], str, float]:
    """
    Extrae el costo de socio real, el nombre oficial del producto, las tallas de la fila Vallejo
    (soportando tanto numeración decimal de calzado como tallas CH, M, G, XG, UNI y Jeans)
    y el precio público de la ficha.
    """
    _abrir_ficha_desde_busqueda(page, id_ps)
    _esperar_bloque_socio(page)
    nombre_detectado = _leer_titulo_h1(page)

    texto_pagina = page.locator("body").inner_text()
    costo_socio = extraer_costo_socio(texto_pagina)
    # Precio público (se lee antes de abrir el modal de tiendas)
    precio_publico = _leer_precio_publico(page, texto_pagina, id_ps)

    _abrir_modal_tiendas(page)
    tallas_vallejo = extraer_tallas_vallejo(page.locator("body").inner_text())

    return costo_socio, tallas_vallejo, nombre_detectado, precio_publico


def obtener_url_canonica(page) -> str:
    """Devuelve la URL canónica de la ficha (/productos/...) o '' si la página no es una ficha."""
    try:
        loc_canon = page.locator("link[rel='canonical']")
        if loc_canon.count() > 0:
            href_c = (loc_canon.first.get_attribute("href") or "").strip()
            if href_c.startswith("http") and "/productos/" in href_c:
                return href_c
    except Exception:
        pass
    if "priceshoes.com/productos/" in page.url:
        return page.url.split("#")[0].strip()
    return ""


def pausa_antiban_lognormal() -> None:
    pausa = min(22.0, max(3.5, random.lognormvariate(mu=1.7, sigma=0.45)))
    print(f"   🛡️ Pausa humana Anti-Ban: {pausa:.1f}s...")
    time.sleep(pausa)


# ===========================================================================
# AUDITORÍA DE UN SKU
# ===========================================================================
GAMMA_MEMORIA = 0.90  # decaimiento de memoria del modelo bayesiano de ventas


def _nombre_a_guardar(nombre: str, nombre_h1: str) -> str:
    """El H1 de la ficha solo sustituye a un nombre vacío o provisional ("Modelo 123")."""
    es_placeholder = (not nombre.strip()) or bool(re.match(r"^Modelo\s+\S+$", nombre.strip(), re.IGNORECASE))
    return nombre_h1 if (nombre_h1 and es_placeholder) else nombre


def _guardar_agotado(conn: sqlite3.Connection, id_ps: str, url_canonica: str, ahora_iso: str) -> None:
    conn.execute(
        """
        UPDATE zapatos_candidatos
        SET url_real = COALESCE(NULLIF(?, ''), url_real),
            ultima_sincronizacion = ?,
            stock_total_anterior = 0,
            tier = 'Z'
        WHERE id_ps = ?
        """,
        (url_canonica, ahora_iso, id_ps),
    )
    conn.commit()


def _guardar_costo_sin_tallas(
    conn: sqlite3.Connection,
    id_ps: str,
    nombre_guardar: str,
    costo_real: float,
    precio_publico: float,
    estado_val: str,
    url_canonica: str,
    ahora_iso: str,
) -> None:
    """Hay costo pero no se leyeron tallas (modal no abrió): no toca stock ni modelo bayesiano."""
    conn.execute(
        """
        UPDATE zapatos_candidatos
        SET nombre_modelo = COALESCE(NULLIF(?, ''), nombre_modelo),
            costo_socio = ?,
            precio_publico = CASE WHEN ? > 0 THEN ? ELSE precio_publico END,
            costo_validacion = ?,
            url_real = COALESCE(NULLIF(?, ''), url_real),
            ultima_sincronizacion = ?
        WHERE id_ps = ?
        """,
        (nombre_guardar, costo_real, precio_publico, precio_publico, estado_val, url_canonica, ahora_iso, id_ps),
    )
    conn.commit()


def _actualizar_modelo_ventas(
    alpha_ant: float, beta_ant: float, stock_ant, stock_total_actual: int, dias_dt: float
) -> Tuple[float, float]:
    """Nuevos (alpha, beta): ventas detectadas por baja de stock y días observados, con decaimiento."""
    if stock_ant is None or stock_ant < 0:
        return alpha_ant, beta_ant
    ventas_detectadas = max(0, stock_ant - stock_total_actual)
    nuevo_alpha = (GAMMA_MEMORIA * alpha_ant) + ventas_detectadas
    nuevo_beta = (GAMMA_MEMORIA * beta_ant) + max(0.1, dias_dt)
    return nuevo_alpha, nuevo_beta


def _clasificar_tier(stock_total_actual: int, stock_ant, nuevo_beta: float, velocidad: float) -> Tuple[str, int]:
    """Devuelve (tier, activo_monitoreo). El Tier Z (agotado crónico) pausa el monitoreo."""
    if stock_total_actual == 0 and stock_ant == 0 and nuevo_beta >= 4.0:
        return "Z", 0
    if velocidad >= 2.5 and stock_total_actual > 0:
        return "S", 1
    if velocidad >= 0.8 and stock_total_actual > 0:
        return "A", 1
    return "B", 1


def _guardar_auditoria_completa(conn: sqlite3.Connection, id_ps: str, valores: tuple, tallas_reales: Dict[str, int]) -> None:
    conn.execute(
        """
        UPDATE zapatos_candidatos
        SET nombre_modelo = COALESCE(NULLIF(?, ''), nombre_modelo),
            costo_socio = CASE WHEN ? > 0 THEN ? ELSE costo_socio END,
            precio_publico = CASE WHEN ? > 0 THEN ? ELSE precio_publico END,
            costo_validacion = ?,
            url_real = COALESCE(NULLIF(?, ''), url_real),
            alpha = ?,
            beta = ?,
            velocidad_diaria = ?,
            stock_total_anterior = ?,
            ultima_sincronizacion = ?,
            tier = ?,
            activo_monitoreo = ?
        WHERE id_ps = ?
        """,
        valores + (id_ps,),
    )

    conn.execute("DELETE FROM historial_stock_21_dias WHERE id_ps = ?", (id_ps,))
    conn.executemany(
        "INSERT INTO historial_stock_21_dias (id_ps, talla, stock_vallejo) VALUES (?, ?, ?)",
        [(id_ps, talla, cant) for talla, cant in tallas_reales.items()],
    )
    conn.commit()


def auditar_sku(conn: sqlite3.Connection, page, item) -> str:
    """
    Audita un SKU y guarda el resultado. Devuelve:
      'ok'      -> datos leídos y guardados
      'agotado' -> ficha cargada pero sin Socio ni tallas
      'fallo'   -> la página no es una ficha de producto (bloqueo, sesión vencida, SKU inexistente)
    """
    _score, id_ps, nombre, url, alpha_ant, beta_ant, stock_ant, dias_dt, _origen = item

    page.goto(url, timeout=35000, wait_until="domcontentloaded")
    costo_real, tallas_reales, nombre_h1, precio_publico = extraer_datos_reales_dom(page, id_ps)

    # Si no terminamos en una ficha de producto no se toca la BD: evita marcar como agotado
    # un SKU solo porque la página falló, pidió login o bloqueó la sesión.
    if "/productos/" not in page.url:
        print("   ⚠️ No se llegó a una ficha de producto (¿sesión vencida, bloqueo o SKU inexistente?).")
        return "fallo"

    url_canonica = obtener_url_canonica(page)
    nombre_guardar = _nombre_a_guardar(nombre, nombre_h1)
    ahora_iso = datetime.now().isoformat()

    # --- Sin costo ni tallas: producto agotado ---
    if costo_real == 0.0 and not tallas_reales:
        print("   ⚠️ Producto agotado o sin bloque de Socio activo en la página.")
        _guardar_agotado(conn, id_ps, url_canonica, ahora_iso)
        return "agotado"

    # --- Validación del costo de socio contra el precio público ---
    estado_val, mensaje_val = validar_costo_socio(costo_real, precio_publico)
    if estado_val != "ok":
        print(f"   🔎 Validación de costo [{estado_val}]: {mensaje_val}")

    if not tallas_reales:
        print(f"   ⚠️ Costo ${costo_real:,.2f} leído, pero sin tabla de tallas: se conserva el stock anterior.")
        _guardar_costo_sin_tallas(
            conn, id_ps, nombre_guardar, costo_real, precio_publico, estado_val, url_canonica, ahora_iso
        )
        return "ok"

    # --- Caso normal: costo y/o tallas leídos ---
    stock_total_actual = sum(tallas_reales.values())
    nuevo_alpha, nuevo_beta = _actualizar_modelo_ventas(alpha_ant, beta_ant, stock_ant, stock_total_actual, dias_dt)
    velocidad = round(nuevo_alpha / nuevo_beta, 2)
    tier, activo = _clasificar_tier(stock_total_actual, stock_ant, nuevo_beta, velocidad)

    _guardar_auditoria_completa(
        conn,
        id_ps,
        (
            nombre_guardar,
            costo_real,
            costo_real,
            precio_publico,
            precio_publico,
            estado_val,
            url_canonica,
            round(nuevo_alpha, 3),
            round(nuevo_beta, 3),
            velocidad,
            stock_total_actual,
            ahora_iso,
            tier,
            activo,
        ),
        tallas_reales,
    )

    print(
        f"   💰 Costo Socio: ${costo_real:,.2f} | 🏷️ Público: ${precio_publico:,.2f} [{estado_val}] "
        f"| 📦 Stock Vallejo: {stock_total_actual} uds"
    )
    print(f"   📈 Velocidad: {velocidad}/día | 🏆 Tier: {tier} | Tallas: {tallas_reales}")
    return "ok"


# ===========================================================================
# FLUJO PRINCIPAL
# ===========================================================================
def ejecutar_recolector_inteligente() -> None:
    print("=" * 80)
    print("🧠 MOTOR BAYESIANO QUANT-COMMERCE: Barrido Exclusivo 44 Catálogos PDF (Vallejo)")
    print("=" * 80)

    if not os.path.exists(RUTA_SESION):
        print(f"❌ No se encontró '{RUTA_SESION}'. Ejecuta primero el inicio de sesión.")
        return

    Path("data").mkdir(exist_ok=True)
    conn = sqlite3.connect(RUTA_BD, timeout=30)
    try:
        conn.execute("PRAGMA journal_mode=WAL;")

        asegurar_estructura_bd(conn)
        depurar_solo_44_catalogos(conn)

        lote = seleccionar_lote_ucb(conn, limite=LOTE_MICRO_SESION)
        if not lote:
            print("⚠️ No hay SKUs de catálogos PDF en `zapatos_candidatos`. Ejecuta primero el extractor de PDFs.")
            return

        resumen_cats: Dict[str, int] = {}
        for item in lote:
            resumen_cats[item[8]] = resumen_cats.get(item[8], 0) + 1
        print(f"\n🎯 Lote seleccionado: {len(lote)} SKUs oficiales de Catálogos PDF:")
        for c_nom, cant in resumen_cats.items():
            print(f"   📘 {c_nom}: {cant} SKUs en este lote")
        print()

        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=MODO_INVISIBLE,
                args=["--disable-blink-features=AutomationControlled"],
            )
            try:
                context = browser.new_context(
                    storage_state=RUTA_SESION,
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
                    viewport={"width": 1366, "height": 768},
                )
                page = context.new_page()

                sin_datos_seguidos = 0
                for idx, item in enumerate(lote, 1):
                    score, id_ps, nombre, _url, _a, _b, _s, _d, origen_cat = item
                    print(f"[{idx}/{len(lote)}] [{origen_cat}] SKU {id_ps} ({nombre}) | Prioridad: {score:.1f}")

                    try:
                        estado = auditar_sku(conn, page, item)
                    except Exception as e:
                        conn.rollback()
                        print(f"   ⚠️ Error en SKU {id_ps}: {e}")
                        estado = "fallo"

                    sin_datos_seguidos = 0 if estado == "ok" else sin_datos_seguidos + 1
                    if sin_datos_seguidos >= MAX_SIN_DATOS_SEGUIDOS:
                        print(
                            f"\n🛑 {sin_datos_seguidos} SKUs seguidos sin datos: posible sesión vencida o bloqueo. "
                            "Se detiene la micro-sesión; renueva la sesión e inténtalo de nuevo."
                        )
                        break

                    if idx < len(lote):
                        pausa_antiban_lognormal()
            finally:
                browser.close()
    finally:
        conn.close()
    print("\n✅ ¡Micro-sesión de Catálogos PDF finalizada y guardada en SQLite!")


if __name__ == "__main__":
    ejecutar_recolector_inteligente()