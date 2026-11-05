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

    ids_borrar: List[Tuple[str]] = []
    actualizaciones: List[Tuple[str, str]] = []
    ids_validos: set[str] = set()

    for id_ps, orig in filas:
        id_str = str(id_ps).strip()
        texto_orig = str(orig).strip()
        id_cat = extraer_id_catalogo(texto_orig)

        if id_cat in MAPA_CATALOGOS:
            nom_oficial = MAPA_CATALOGOS[id_cat]
            id_norm = normalizar_id_sku(id_str)
            ids_validos.update({id_str, id_norm, id_norm.lstrip("0") or id_norm})
            if texto_orig != nom_oficial:
                actualizaciones.append((nom_oficial, id_str))
        else:
            ids_borrar.append((id_str,))

    # Protección: si TODO parece ajeno, casi seguro es un problema de formato en origen_catalogo
    if filas and len(ids_borrar) == len(filas) and not PERMITIR_BORRAR_TODO:
        print(
            "⚠️ Ninguna fila de `zapatos_candidatos` coincide con los 44 catálogos oficiales. "
            "Se omite la depuración para no borrar todo por error; revisa la columna origen_catalogo."
        )
        return

    puede_borrar = True
    if ids_borrar and not respaldar_bd(conn):
        puede_borrar = False
        ids_borrar = []

    if actualizaciones:
        cur.executemany(
            "UPDATE zapatos_candidatos SET origen_catalogo = ? WHERE id_ps = ?",
            actualizaciones,
        )

    if ids_borrar:
        cur.executemany("DELETE FROM zapatos_candidatos WHERE id_ps = ?", ids_borrar)
        cur.executemany("DELETE FROM historial_stock_21_dias WHERE id_ps = ?", ids_borrar)

    # Limpiar únicamente tallas basura que no sean ni decimales ni tallas válidas de ropa
    filas_tallas = cur.execute("SELECT rowid, id_ps, talla FROM historial_stock_21_dias").fetchall()
    rowids_basura: List[Tuple[int]] = []
    skus_reset: set[str] = set()
    for rowid, s_id, t_val in filas_tallas:
        if not PATRON_TALLA_VALIDA.match(str(t_val).strip()):
            rowids_basura.append((int(rowid),))
            skus_reset.add(str(s_id))

    if rowids_basura:
        cur.executemany("DELETE FROM historial_stock_21_dias WHERE rowid = ?", rowids_basura)
        cur.executemany(
            """
            UPDATE zapatos_candidatos
            SET ultima_sincronizacion = NULL, stock_total_anterior = -1
            WHERE id_ps = ?
            """,
            [(s,) for s in skus_reset],
        )

    conn.commit()
    despues = cur.execute("SELECT COUNT(*) FROM zapatos_candidatos").fetchone()[0]

    # Eliminar carpetas físicas en data/catalogo_listo que no estén en ids_validos
    carpetas_borradas = 0
    if puede_borrar and ids_validos and DIR_CATALOGO_LISTO.exists():
        for d in list(DIR_CATALOGO_LISTO.iterdir()):
            if not d.is_dir():
                continue
            m_id = re.search(r"(\d{5,8})", d.name)
            cid = m_id.group(1) if m_id else ""
            if not cid or (cid not in ids_validos and (cid.lstrip("0") or cid) not in ids_validos):
                shutil.rmtree(d, ignore_errors=True)
                carpetas_borradas += 1

    if ids_borrar or carpetas_borradas > 0:
        print(
            f"🧹 Depuración completada: {len(ids_borrar)} SKUs web descartados de SQLite "
            f"({antes:,} -> {despues:,} oficiales) y {carpetas_borradas} carpetas externas eliminadas."
        )
    else:
        print(f"📦 Catálogo oficial limpio en SQLite: {despues:,} SKUs exclusivos de los 44 PDFs.")


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
        id_ps, nombre, url, alpha, beta, stock_ant, ult_sinc, costo, origen_cat = fila
        id_str = str(id_ps).strip()
        alpha = float(alpha) if alpha else 1.0
        beta = float(beta) if beta else 2.0
        stock_ant = int(stock_ant if stock_ant is not None else -1)

        # Si el SKU del PDF aún no tenía URL directa, usar el buscador oficial por ID
        url_usar = (
            str(url).strip()
            if (url and str(url).strip().startswith("http"))
            else f"https://www.priceshoes.com/buscar?q={id_str}"
        )
        nombre_usar = str(nombre).strip() if nombre else f"Modelo {id_str}"
        orden_cat = ORDEN_PRIORIDAD_CAT.get(str(origen_cat).strip(), 999)

        # Nivel 1: SKUs vírgenes del catálogo PDF (nunca sincronizados)
        if not ult_sinc:
            score_virgen = 1000.0 - (orden_cat * 5.0)
            virgenes_por_catalogo.append(
                (orden_cat, id_str, score_virgen, nombre_usar, url_usar, alpha, beta, stock_ant, 15.0, origen_cat)
            )
            continue

        # Nivel 2: SKUs ya auditados puntuados por UCB Bayesiano
        try:
            fecha_ant = datetime.fromisoformat(str(ult_sinc))
            dias_sin_ver = max(0.05, (ahora - fecha_ant).total_seconds() / 86400.0)
        except Exception:
            dias_sin_ver = 5.0

        explotacion = alpha / beta
        exploracion = 1.41 * (math.sqrt(alpha) / beta)
        score_ucb = (explotacion + exploracion) * math.log(1.0 + dias_sin_ver)
        auditados_ucb.append(
            (score_ucb, id_str, nombre_usar, url_usar, alpha, beta, stock_ant, dias_sin_ver, origen_cat)
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
def extraer_datos_reales_dom(page, id_ps: str) -> Tuple[float, Dict[str, int], str]:
    """
    Extrae el costo de socio real, el nombre oficial del producto y las tallas de la fila Vallejo
    (soportando tanto numeración decimal de calzado como tallas CH, M, G, XG, UNI y Jeans).
    """
    costo_socio = 0.0
    tallas_vallejo: Dict[str, int] = {}
    nombre_detectado = ""

    # Si entramos por URL de búsqueda (/buscar?q=...), abrir la ficha del producto si aparece en resultados
    try:
        if "/buscar" in page.url:
            enlace_prod = page.locator(f"a[href*='{id_ps}']").first
            if enlace_prod.count() > 0:
                href_p = enlace_prod.get_attribute("href")
                if href_p:
                    url_destino = href_p if href_p.startswith("http") else f"https://www.priceshoes.com{href_p}"
                    page.goto(url_destino, timeout=30000, wait_until="domcontentloaded")
    except Exception:
        pass

    try:
        page.wait_for_function(
            "document.body && document.body.innerText.includes('Socio')",
            timeout=8000,
        )
    except Exception:
        pass

    # Capturar título H1 real si está disponible
    try:
        loc_h1 = page.locator("h1").first
        if loc_h1.count() > 0:
            txt_h1 = loc_h1.inner_text().strip()
            if txt_h1 and len(txt_h1) > 3:
                nombre_detectado = txt_h1
    except Exception:
        pass

    texto_pagina = page.locator("body").inner_text()

    match_socio = re.search(
        r"Socio:\s*[\r\n\s]*\$\s*([\d,]+(?:\.\d{2})?)", texto_pagina, re.IGNORECASE
    )
    if match_socio:
        costo_socio = float(match_socio.group(1).replace(",", ""))

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

    texto_modal = page.locator("body").inner_text()

    if "TIENDAS CERCANAS" in texto_modal.upper():
        partes = re.split(r"TIENDAS CERCANAS[^\n]*", texto_modal, flags=re.IGNORECASE)
        zona_tabla = partes[-1]
    else:
        zona_tabla = texto_modal

    match_bloque = re.search(
        r"Tienda\s+([\s\S]+?)\s+Ecommerce[\s\S]+?Vallejo\s*(?:Tu tienda)?\s*([\s\S]+)",
        zona_tabla,
        re.IGNORECASE,
    )

    if match_bloque:
        encabezado_str = match_bloque.group(1)
        fila_vallejo_str = match_bloque.group(2)

        # 1. Intentar primero tallas de calzado con punto decimal (.0 o .5)
        tallas_detectadas = re.findall(r"\b\d{2}\.[05]\b", encabezado_str)

        # 2. Si es ropa, jeans, calcetería o accesorio (sin decimales), extraer tokens válidos del encabezado
        if not tallas_detectadas:
            tokens_enc = [t.strip().upper() for t in re.split(r"[\s\t\r\n]+", encabezado_str) if t.strip()]
            tallas_detectadas = [t for t in tokens_enc if PATRON_TALLA_VALIDA.match(t)]

        if tallas_detectadas:
            fila_vallejo_limpia = re.split(
                r"(?:Centro|Naucalpan|Olivar|Ecatepec|Tlalnepantla|Iztapalapa|Arcos|Puebla|Le[oó]n|Guadalajara|a\s+\d+\s*km)",
                fila_vallejo_str,
                flags=re.IGNORECASE,
            )[0]

            tokens_vallejo = re.findall(r"(?:\b\d+\b|—|--|-)", fila_vallejo_limpia)
            for i, talla in enumerate(tallas_detectadas):
                if i < len(tokens_vallejo):
                    tok = tokens_vallejo[i]
                    stock_val = int(tok) if tok.isdigit() else 0
                    tallas_vallejo[talla] = stock_val
                else:
                    tallas_vallejo[talla] = 0

    return costo_socio, tallas_vallejo, nombre_detectado


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
def auditar_sku(conn: sqlite3.Connection, page, item) -> str:
    """
    Audita un SKU y guarda el resultado. Devuelve:
      'ok'      -> datos leídos y guardados
      'agotado' -> ficha cargada pero sin Socio ni tallas
      'fallo'   -> la página no es una ficha de producto (bloqueo, sesión vencida, SKU inexistente)
    """
    _score, id_ps, nombre, url, alpha_ant, beta_ant, stock_ant, dias_dt, _origen = item

    page.goto(url, timeout=35000, wait_until="domcontentloaded")
    costo_real, tallas_reales, nombre_h1 = extraer_datos_reales_dom(page, id_ps)

    # Si no terminamos en una ficha de producto no se toca la BD: evita marcar como agotado
    # un SKU solo porque la página falló, pidió login o bloqueó la sesión.
    if "/productos/" not in page.url:
        print("   ⚠️ No se llegó a una ficha de producto (¿sesión vencida, bloqueo o SKU inexistente?).")
        return "fallo"

    url_canonica = obtener_url_canonica(page)
    es_placeholder = (not nombre.strip()) or bool(re.match(r"^Modelo\s+\S+$", nombre.strip(), re.IGNORECASE))
    nombre_guardar = nombre_h1 if (nombre_h1 and es_placeholder) else nombre
    ahora_iso = datetime.now().isoformat()

    # --- Sin costo ni tallas: producto agotado ---
    if costo_real == 0.0 and not tallas_reales:
        print("   ⚠️ Producto agotado o sin bloque de Socio activo en la página.")
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
        return "agotado"

    # --- Hay costo pero no se leyeron tallas (modal no abrió): no tocar stock ni modelo bayesiano ---
    if not tallas_reales:
        print(f"   ⚠️ Costo ${costo_real:,.2f} leído, pero sin tabla de tallas: se conserva el stock anterior.")
        conn.execute(
            """
            UPDATE zapatos_candidatos
            SET nombre_modelo = COALESCE(NULLIF(?, ''), nombre_modelo),
                costo_socio = ?,
                url_real = COALESCE(NULLIF(?, ''), url_real),
                ultima_sincronizacion = ?
            WHERE id_ps = ?
            """,
            (nombre_guardar, costo_real, url_canonica, ahora_iso, id_ps),
        )
        conn.commit()
        return "ok"

    # --- Caso normal: costo y/o tallas leídos ---
    stock_total_actual = sum(tallas_reales.values())
    gamma = 0.90
    if stock_ant is None or stock_ant < 0:
        nuevo_alpha, nuevo_beta = alpha_ant, beta_ant
    else:
        ventas_detectadas = max(0, stock_ant - stock_total_actual)
        nuevo_alpha = (gamma * alpha_ant) + ventas_detectadas
        nuevo_beta = (gamma * beta_ant) + max(0.1, dias_dt)

    velocidad = round(nuevo_alpha / nuevo_beta, 2)

    activo = 1
    if stock_total_actual == 0 and stock_ant == 0 and nuevo_beta >= 4.0:
        tier = "Z"
        activo = 0
    elif velocidad >= 2.5 and stock_total_actual > 0:
        tier = "S"
    elif velocidad >= 0.8 and stock_total_actual > 0:
        tier = "A"
    else:
        tier = "B"

    conn.execute(
        """
        UPDATE zapatos_candidatos
        SET nombre_modelo = COALESCE(NULLIF(?, ''), nombre_modelo),
            costo_socio = CASE WHEN ? > 0 THEN ? ELSE costo_socio END,
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
        (
            nombre_guardar,
            costo_real,
            costo_real,
            url_canonica,
            round(nuevo_alpha, 3),
            round(nuevo_beta, 3),
            velocidad,
            stock_total_actual,
            ahora_iso,
            tier,
            activo,
            id_ps,
        ),
    )

    conn.execute("DELETE FROM historial_stock_21_dias WHERE id_ps = ?", (id_ps,))
    conn.executemany(
        "INSERT INTO historial_stock_21_dias (id_ps, talla, stock_vallejo) VALUES (?, ?, ?)",
        [(id_ps, talla, cant) for talla, cant in tallas_reales.items()],
    )
    conn.commit()

    print(f"   💰 Costo Socio: ${costo_real:,.2f} | 📦 Stock Vallejo: {stock_total_actual} uds")
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