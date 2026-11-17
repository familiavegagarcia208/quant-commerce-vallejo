# -*- coding: utf-8 -*-
"""
========================================================================================
PASO 3: RECOLECTOR LIGERO ANTI-BAN (PRIORIZADO EN LOS CUADRANTES VERDES DE LA MATRIZ)
========================================================================================
- Descarta y elimina de SQLite y de `data/catalogo_listo` cualquier SKU ajeno a los 44 PDFs
  (con respaldo previo de la BD y protecciones contra borrados masivos por error).
- Prioriza estrictamente los 4 cuadrantes verdes de la Matriz de Maslow:
  1) Calzado escolar, 2) Tenis con licencia, 3) Sandalias y botas de moda, 4) Pantuflas.
- Sincroniza automáticamente `historial_stock_21_dias` y `stock_tienda` (Vallejo) para que
  el panel y `exportar_mercadolibre.py` dispongan del inventario al instante.
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
from typing import Dict, List, Set, Tuple

from playwright.sync_api import sync_playwright

import inventario_tiendas as inv

RUTA_BD = "data/zapateria_vallejo.db"
RUTA_SESION = "data/sesion_vallejo.json"
DIR_CATALOGO_LISTO = Path("data/catalogo_listo")

# ===========================================================================
# PARÁMETROS DE OPERACIÓN
# ===========================================================================
LOTE_MICRO_SESION = 60
MODO_INVISIBLE = True
MAX_SIN_DATOS_SEGUIDOS = 10
PERMITIR_BORRAR_TODO = False
RATIO_COSTO_MIN_ESPERADO = 0.50
RATIO_COSTO_MAX_ESPERADO = 0.90

# Orden de prioridad comercial empezando por los CUADRANTES VERDES de la Matriz de Maslow
CATALOGOS_PRIORIDAD: List[Tuple[str, str]] = [
    # --- 🟢 ETAPA 1: EMPEZAR AQUÍ (VERDES: Escolar, Licencias, Sandalias/Botas Moda, Pantuflas) ---
    ("1370231", "1370231 - Escolar"),
    ("1348800", "1348800 - Licencias"),
    ("1348783", "1348783 - Kids"),
    ("1378033", "1378033 - Kids Todo en Uno"),
    ("1368804", "1368804 - Pop Universe Mini"),
    ("1381556", "1381556 - Navidad Kids"),
    ("1368789", "1368789 - Sandalias"),
    ("1345253", "1345253 - Botas"),
    ("1380328", "1380328 - Botas Largas"),
    ("1380359", "1380359 - Botas Kits"),
    ("1380363", "1380363 - Basicos Botines"),
    ("1380367", "1380367 - Botas Texano"),
    ("1381342", "1381342 - Ella"),
    ("1380337", "1380337 - Coleccion Thalia"),
    ("1380341", "1380341 - Belinda Fashion & Chic"),
    ("1381350", "1381350 - Mushu Esenciales Home"),
    ("1380996", "1380996 - Love to Lounge"),
    ("1380350", "1380350 - Navidad Home"),
    # --- 🟠 ETAPA 2: SEGUNDA ETAPA (NARANJAS: Tenis Urbanos Dama y Línea Confort) ---
    ("1346256", "1346256 - Urbano"),
    ("1353557", "1353557 - Juvenil"),
    ("1380516", "1380516 - Oportunidades Urbano"),
    ("1368805", "1368805 - Urban Lifestyle Mini"),
    ("1353843", "1353843 - Confort"),
    ("1381316", "1381316 - Med Chef"),
    # --- ⚪ ETAPA 3: DEJAR PARA DESPUÉS (GRISES: Caballeros, Vestir, Marcas, Ropa) ---
    ("1342200", "1342200 - Caballeros"),
    ("1346284", "1346284 - Vestir Casual"),
    ("1348737", "1348737 - Vestir"),
    ("1348757", "1348757 - Man"),
    ("1380332", "1380332 - Goodyear Botas"),
    ("1380354", "1380354 - Reebok Impulsados"),
    ("1377702", "1377702 - Exclusivos"),
    ("1380358", "1380358 - Importados Fall Ofertas"),
    ("1353556", "1353556 - Calzado y Accesorios"),
    ("1376435", "1376435 - Todo en Uno"),
    ("1348749", "1348749 - NBA"),
    ("1348753", "1348753 - NFL"),
    ("1368800", "1368800 - Prokennex Mini"),
    ("1352311", "1352311 - Seven Jeans"),
    ("1381338", "1381338 - Jeans"),
    ("1348733", "1348733 - Abrigador"),
    ("1348804", "1348804 - Pijamas"),
    ("1381346", "1381346 - Basicos OID6"),
    ("1348779", "1348779 - Accesorios"),
    ("1380346", "1380346 - Navidad"),
]

MAPA_CATALOGOS: Dict[str, str] = dict(CATALOGOS_PRIORIDAD)
ORDEN_PRIORIDAD_CAT: Dict[str, int] = {
    nom: idx for idx, (_, nom) in enumerate(CATALOGOS_PRIORIDAD, start=1)
}

PATRON_TALLA_VALIDA = re.compile(
    r"^(?:\d{2}\.[05]"
    r"|X{0,2}S|X{0,2}L|X?CH|ECH|MED|M|G|XG|XXG|[2-4]X[GL]|EG|EEG"
    r"|UNI|UNITALLA|CH/M|M/G|G/XG"
    r"|[1-9]|[1-5]\d|60)$",
    re.IGNORECASE,
)


def es_sku_verde(nombre_modelo: str, origen_catalogo: str) -> bool:
    etapa, _ = inv.clasificar_matriz_maslow(nombre_modelo, origen_catalogo)
    return etapa == inv.ETAPA_VERDE


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
        "tipo_producto": "TEXT DEFAULT ''",
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
    inv.asegurar_esquema(conn)
    conn.commit()


def normalizar_id_sku(valor: object) -> str:
    return re.sub(r"^PS-", "", str(valor).strip(), flags=re.IGNORECASE)


def extraer_id_catalogo(texto_origen: str) -> str:
    texto = str(texto_origen).strip()
    m = re.search(r"(\d{7})", texto)
    return m.group(1) if m else re.sub(r"\.pdf$", "", texto, flags=re.IGNORECASE).strip()


def respaldar_bd(conn: sqlite3.Connection) -> bool:
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


def _clasificar_filas_por_catalogo(filas) -> Tuple[List[Tuple[str]], List[Tuple[str, str]], Set[str]]:
    ids_borrar: List[Tuple[str]] = []
    actualizaciones: List[Tuple[str, str]] = []
    ids_validos: Set[str] = set()

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


def _es_carpeta_ajena(nombre_carpeta: str, ids_validos: Set[str]) -> bool:
    m_id = re.search(r"(\d{5,8})", nombre_carpeta)
    cid = m_id.group(1) if m_id else ""
    return not cid or (cid not in ids_validos and (cid.lstrip("0") or cid) not in ids_validos)


def _borrar_carpetas_ajenas(ids_validos: Set[str]) -> int:
    if not ids_validos or not DIR_CATALOGO_LISTO.exists():
        return 0
    carpetas_borradas = 0
    for d in list(DIR_CATALOGO_LISTO.iterdir()):
        if d.is_dir() and _es_carpeta_ajena(d.name, ids_validos):
            shutil.rmtree(d, ignore_errors=True)
            carpetas_borradas += 1
    return carpetas_borradas


def depurar_solo_44_catalogos(conn: sqlite3.Connection) -> None:
    cur = conn.cursor()
    antes = cur.execute("SELECT COUNT(*) FROM zapatos_candidatos").fetchone()[0]
    filas = cur.execute(
        "SELECT id_ps, COALESCE(origen_catalogo, '') FROM zapatos_candidatos"
    ).fetchall()
    ids_borrar, actualizaciones, ids_validos = _clasificar_filas_por_catalogo(filas)

    if filas and len(ids_borrar) == len(filas) and not PERMITIR_BORRAR_TODO:
        print(
            "⚠️ Ninguna fila de `zapatos_candidatos` coincide con los 44 catálogos oficiales. "
            "Se omite la depuración para no borrar todo por error; revisa la columna origen_catalogo."
        )
        return

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
    id_ps, nombre, url, alpha, beta, stock_ant, ult_sinc, _costo, origen_cat = fila
    id_str = str(id_ps).strip()
    alpha = float(alpha) if alpha else 1.0
    beta = float(beta) if beta else 2.0
    stock_ant = int(stock_ant if stock_ant is not None else -1)

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


def _puntaje_ucb(alpha: float, beta: float, dias_sin_ver: float, verde: bool = False) -> float:
    explotacion = alpha / beta
    exploracion = 1.41 * (math.sqrt(alpha) / beta)
    factor_verde = 1.60 if verde else 1.0
    return (explotacion + exploracion) * math.log(1.0 + dias_sin_ver) * factor_verde


def seleccionar_lote_ucb(conn: sqlite3.Connection, limite: int = LOTE_MICRO_SESION):
    """
    Selecciona el lote de auditoría dando prioridad estricta a los CUADRANTES VERDES:
      1. Vírgenes de los 4 Cuadrantes Verdes (Escolar, Licencias, Sandalias/Botas Moda, Pantuflas).
      2. Resto de Vírgenes según CATALOGOS_PRIORIDAD.
      3. Re-Auditoría UCB Bayesiana ponderando con bono a los productos Verdes.
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
        verde = es_sku_verde(nombre_usar, origen_cat)

        if not ult_sinc:
            orden_cat = ORDEN_PRIORIDAD_CAT.get(str(origen_cat).strip(), 999)
            grupo_prio = 0 if verde else 1
            score_virgen = 1200.0 - (orden_cat * 5.0) + (200.0 if verde else 0.0)
            virgenes_por_catalogo.append(
                (grupo_prio, orden_cat, id_str, score_virgen, nombre_usar, url_usar, alpha, beta, stock_ant, 15.0, origen_cat)
            )
            continue

        dias_sin_ver = _dias_sin_ver(ult_sinc, ahora)
        auditados_ucb.append(
            (_puntaje_ucb(alpha, beta, dias_sin_ver, verde=verde), id_str, nombre_usar, url_usar, alpha, beta, stock_ant,
             dias_sin_ver, origen_cat)
        )

    virgenes_por_catalogo.sort(key=lambda x: (x[0], x[1], x[2]))
    auditados_ucb.sort(key=lambda x: x[0], reverse=True)

    lote_final = [
        (item[3], item[2], item[4], item[5], item[6], item[7], item[8], item[9], item[10])
        for item in virgenes_por_catalogo[:limite]
    ]

    if len(lote_final) < limite:
        lote_final.extend(auditados_ucb[: limite - len(lote_final)])

    return lote_final


# ===========================================================================
# EXTRACCIÓN DESDE LA PÁGINA
# ===========================================================================
def extraer_precio_publico(html: str, texto_pagina: str, id_ps: str) -> float:
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
PATRON_FIN_FILA_VALLEJO = re.compile(
    r"(?:Centro|Naucalpan|Olivar|Ecatepec|Tlalnepantla|Iztapalapa|Arcos|Puebla|Le[oó]n|Guadalajara|a\s+\d+\s*km)",
    re.IGNORECASE,
)


def _abrir_ficha_desde_busqueda(page, id_ps: str) -> None:
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
    tallas_detectadas = re.findall(r"\b\d{2}\.[05]\b", encabezado_str)
    if tallas_detectadas:
        return tallas_detectadas

    tokens_enc = [t.strip().upper() for t in re.split(r"[\s\t\r\n]+", encabezado_str) if t.strip()]
    return [t for t in tokens_enc if PATRON_TALLA_VALIDA.match(t)]


def extraer_tallas_vallejo(texto_modal: str) -> Dict[str, int]:
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
    _abrir_ficha_desde_busqueda(page, id_ps)
    _esperar_bloque_socio(page)
    nombre_detectado = _leer_titulo_h1(page)

    texto_pagina = page.locator("body").inner_text()
    costo_socio = extraer_costo_socio(texto_pagina)
    precio_publico = _leer_precio_publico(page, texto_pagina, id_ps)

    _abrir_modal_tiendas(page)
    tallas_vallejo = extraer_tallas_vallejo(page.locator("body").inner_text())

    return costo_socio, tallas_vallejo, nombre_detectado, precio_publico


def obtener_url_canonica(page) -> str:
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
GAMMA_MEMORIA = 0.90


def _nombre_a_guardar(nombre: str, nombre_h1: str) -> str:
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
    conn.execute("DELETE FROM stock_tienda WHERE id_ps = ? AND tienda_id = 1", (id_ps,))
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
    tipo_calc = inv.clasificar_tipo_producto(nombre_guardar)
    conn.execute(
        """
        UPDATE zapatos_candidatos
        SET nombre_modelo = COALESCE(NULLIF(?, ''), nombre_modelo),
            costo_socio = ?,
            precio_publico = CASE WHEN ? > 0 THEN ? ELSE precio_publico END,
            costo_validacion = ?,
            url_real = COALESCE(NULLIF(?, ''), url_real),
            ultima_sincronizacion = ?,
            tipo_producto = CASE WHEN ? != '' THEN ? ELSE tipo_producto END
        WHERE id_ps = ?
        """,
        (nombre_guardar, costo_real, precio_publico, precio_publico, estado_val, url_canonica, ahora_iso,
         tipo_calc, tipo_calc, id_ps),
    )
    conn.commit()


def _actualizar_modelo_ventas(
    alpha_ant: float, beta_ant: float, stock_ant, stock_total_actual: int, dias_dt: float
) -> Tuple[float, float]:
    if stock_ant is None or stock_ant < 0:
        return alpha_ant, beta_ant
    ventas_detectadas = max(0, stock_ant - stock_total_actual)
    nuevo_alpha = (GAMMA_MEMORIA * alpha_ant) + ventas_detectadas
    nuevo_beta = (GAMMA_MEMORIA * beta_ant) + max(0.1, dias_dt)
    return nuevo_alpha, nuevo_beta


def _clasificar_tier(stock_total_actual: int, stock_ant, nuevo_beta: float, velocidad: float) -> Tuple[str, int]:
    if stock_total_actual == 0 and stock_ant == 0 and nuevo_beta >= 4.0:
        return "Z", 0
    if velocidad >= 2.5 and stock_total_actual > 0:
        return "S", 1
    if velocidad >= 0.8 and stock_total_actual > 0:
        return "A", 1
    return "B", 1


def _guardar_auditoria_completa(
    conn: sqlite3.Connection, id_ps: str, valores: tuple, tallas_reales: Dict[str, int], ahora_iso: str
) -> None:
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
            activo_monitoreo = ?,
            tipo_producto = ?
        WHERE id_ps = ?
        """,
        valores + (id_ps,),
    )

    conn.execute("DELETE FROM historial_stock_21_dias WHERE id_ps = ?", (id_ps,))
    conn.executemany(
        "INSERT INTO historial_stock_21_dias (id_ps, talla, stock_vallejo, fecha) VALUES (?, ?, ?, ?)",
        [(id_ps, talla, cant, ahora_iso) for talla, cant in tallas_reales.items()],
    )

    # Sincronización directa hacia `stock_tienda` (Tienda 1: Vallejo) y alertas
    filas_inv = [
        {"tienda_id": 1, "tienda_nombre": "Vallejo", "talla": talla, "cantidad": cant}
        for talla, cant in tallas_reales.items()
    ]
    inv.guardar_revision(conn, id_ps, 1, filas_inv, ahora=datetime.now())
    conn.commit()


def auditar_sku(conn: sqlite3.Connection, page, item) -> str:
    _score, id_ps, nombre, url, alpha_ant, beta_ant, stock_ant, dias_dt, origen_cat = item

    page.goto(url, timeout=35000, wait_until="domcontentloaded")
    costo_real, tallas_reales, nombre_h1, precio_publico = extraer_datos_reales_dom(page, id_ps)

    if "/productos/" not in page.url:
        print("   ⚠️ No se llegó a una ficha de producto (¿sesión vencida, bloqueo o SKU inexistente?).")
        return "fallo"

    url_canonica = obtener_url_canonica(page)
    nombre_guardar = _nombre_a_guardar(nombre, nombre_h1)
    ahora_iso = datetime.now().isoformat()

    if costo_real == 0.0 and not tallas_reales:
        print("   ⚠️ Producto agotado o sin bloque de Socio activo en la página.")
        _guardar_agotado(conn, id_ps, url_canonica, ahora_iso)
        return "agotado"

    estado_val, mensaje_val = validar_costo_socio(costo_real, precio_publico)
    if estado_val != "ok":
        print(f"   🔎 Validación de costo [{estado_val}]: {mensaje_val}")

    if not tallas_reales:
        print(f"   ⚠️ Costo ${costo_real:,.2f} leído, pero sin tabla de tallas: se conserva el stock anterior.")
        _guardar_costo_sin_tallas(
            conn, id_ps, nombre_guardar, costo_real, precio_publico, estado_val, url_canonica, ahora_iso
        )
        return "ok"

    stock_total_actual = sum(tallas_reales.values())
    nuevo_alpha, nuevo_beta = _actualizar_modelo_ventas(alpha_ant, beta_ant, stock_ant, stock_total_actual, dias_dt)
    velocidad = round(nuevo_alpha / nuevo_beta, 2)
    tier, activo = _clasificar_tier(stock_total_actual, stock_ant, nuevo_beta, velocidad)
    tipo_prod = inv.clasificar_tipo_producto(nombre_guardar)

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
            tipo_prod,
        ),
        tallas_reales,
        ahora_iso,
    )

    _, cuadrante = inv.clasificar_matriz_maslow(nombre_guardar, origen_cat, tipo_prod)
    print(
        f"   [{cuadrante}] 💰 Costo Socio: ${costo_real:,.2f} | 🏷️ Público: ${precio_publico:,.2f} [{estado_val}] "
        f"| 📦 Stock Vallejo: {stock_total_actual} uds"
    )
    print(f"   📈 Velocidad: {velocidad}/día | 🏆 Tier: {tier} | Tallas: {tallas_reales}")
    return "ok"


# ===========================================================================
# FLUJO PRINCIPAL
# ===========================================================================
def ejecutar_recolector_inteligente() -> None:
    print("=" * 80)
    print("🧠 MOTOR BAYESIANO QUANT-COMMERCE: Barrido Priorizado en VERDES (Vallejo)")
    print("=" * 80)

    if not os.path.exists(RUTA_SESION):
        print(f"❌ No se encontró '{RUTA_SESION}'. Ejecuta primero: python paso12_login_socio.py")
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
        verdes_cnt = 0
        for item in lote:
            resumen_cats[item[8]] = resumen_cats.get(item[8], 0) + 1
            if es_sku_verde(item[2], item[8]):
                verdes_cnt += 1
        print(f"\n🎯 Lote seleccionado: {len(lote)} SKUs ({verdes_cnt} en Cuadrantes VERDES):")
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
    print("\n✅ ¡Micro-sesión de Catálogos PDF finalizada y sincronizada en `stock_tienda`!")


if __name__ == "__main__":
    ejecutar_recolector_inteligente()