# -*- coding: utf-8 -*-
"""
========================================================================================
PASO 3: RECOLECTOR MULTI-TIENDA (EXTRACCION DE TODAS LAS SUCURSALES DE PRICE SHOES)
========================================================================================
- Espera la hidratación completa del bloque 'Socio' y abre el modal 'Ver disponibilidad
  en tiendas' para extraer el stock por talla de TODAS las sucursales (Vallejo, Centro,
  Naucalpan, Olivar Del Conde, Ecatepec, Tlalnepantla, Iztapalapa, Arco Norte, León,
  Puebla, Guadalajara, Ecommerce, etc.).
- Si el modal no abre en una visita, conserva intacto el stock anterior sin pisarlo con 0.
========================================================================================
"""

from __future__ import annotations

from datetime import datetime
import math
import os
from pathlib import Path
import random
import re
import sqlite3
import sys
import time
from typing import Any, Dict, List, Optional, Tuple
import unicodedata

from playwright.sync_api import Page, Response, sync_playwright

import inventario_tiendas as inv

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

RUTA_BD = "data/zapateria_vallejo.db"
RUTA_SESION = "data/sesion_vallejo.json"
DIR_CATALOGO_LISTO = Path("data/catalogo_listo")

# ===========================================================================
# PARAMETROS DE OPERACION
# ===========================================================================
LOTE_MICRO_SESION = int(os.environ.get("LOTE_MICRO_SESION", "140"))
MODO_INVISIBLE = True
MAX_SIN_DATOS_SEGUIDOS = 15
PERMITIR_BORRAR_TODO = False
RATIO_COSTO_MIN_ESPERADO = 0.50
RATIO_COSTO_MAX_ESPERADO = 0.90
GAMMA_MEMORIA = 0.90

TIENDAS_EXTRA: Dict[int, str] = {
    102: "Tlalnepantla",
    103: "Guadalajara",
    104: "Toluca",
    105: "Queretaro",
    106: "Monterrey",
    107: "Morelia",
    108: "Veracruz",
    109: "Cuernavaca",
    110: "Pachuca",
}

TODAS_LAS_TIENDAS: Dict[int, str] = {**inv.TIENDAS_SEMILLA, **TIENDAS_EXTRA}

ALIAS_TIENDAS: Dict[str, Tuple[int, str]] = {
    "vallejo": (1, "Vallejo"),
    "leon": (3, "León"),
    "iztapalapa": (4, "Iztapalapa"),
    "ecatepec": (5, "Ecatepec"),
    "chalco": (8, "Chalco"),
    "cordoba": (17, "Cordoba"),
    "naucalpan": (22, "Naucalpan"),
    "olivar": (25, "Olivar Del Conde"),
    "olivar del conde": (25, "Olivar Del Conde"),
    "ecommerce": (29, "Ecommerce"),
    "e-commerce": (29, "Ecommerce"),
    "tienda en linea": (29, "Ecommerce"),
    "centro": (30, "Centro"),
    "ixtapaluca": (33, "Ixtapaluca"),
    "chihuahua": (35, "Chihuahua"),
    "nicolas romero": (36, "Nicolas Romero"),
    "mexicali": (39, "Mexicali"),
    "atemajac": (42, "Atemajac"),
    "obregon": (54, "Obregón"),
    "cd obregon": (54, "Obregón"),
    "nogales": (56, "Nogales"),
    "cardenas": (59, "Cardenas"),
    "hermosillo": (61, "Hermosillo"),
    "coatzacoalcos": (63, "Coatzacoalcos"),
    "puebla": (68, "Puebla"),
    "aguascalientes": (76, "Aguascalientes"),
    "celaya": (77, "Celaya"),
    "puebla centro": (87, "Puebla Centro"),
    "medrano": (96, "Medrano"),
    "miravalle": (97, "Miravalle"),
    "arco norte": (98, "Arco Norte"),
    "arcos": (98, "Arco Norte"),
    "merida": (101, "Merida"),
    "tlalnepantla": (102, "Tlalnepantla"),
    "guadalajara": (103, "Guadalajara"),
    "toluca": (104, "Toluca"),
    "queretaro": (105, "Queretaro"),
    "monterrey": (106, "Monterrey"),
    "morelia": (107, "Morelia"),
    "veracruz": (108, "Veracruz"),
    "cuernavaca": (109, "Cuernavaca"),
    "pachuca": (110, "Pachuca"),
}

CATALOGOS_PRIORIDAD: List[Tuple[str, str]] = [
    # --- 🟢 ETAPA 1: EMPEZAR AQUI (VERDES) ---
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
    # --- 🟠 ETAPA 2: SEGUNDA ETAPA (NARANJAS) ---
    ("1346256", "1346256 - Urbano"),
    ("1353557", "1353557 - Juvenil"),
    ("1380516", "1380516 - Oportunidades Urbano"),
    ("1368805", "1368805 - Urban Lifestyle Mini"),
    ("1353843", "1353843 - Confort"),
    ("1381316", "1381316 - Med Chef"),
    # --- ⚪ ETAPA 3: DEJAR PARA DESPUES (GRISES) ---
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


def _sin_acentos(texto: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFKD", texto) if not unicodedata.combining(c)
    )


def resolver_tienda_por_nombre(nombre_sucursal: str) -> Optional[Tuple[int, str]]:
    if not nombre_sucursal:
        return None
    limpio = re.sub(
        r"\b(?:tu\s+tienda|a\s+\d+(?:[\.,]\d+)?\s*km|sucursal|tienda)\b",
        "",
        nombre_sucursal,
        flags=re.I,
    )
    limpio = re.sub(r"[^a-zA-ZáéíóúÁÉÍÓÚñÑ\s\-]", " ", limpio)
    limpio = re.sub(r"\s{2,}", " ", limpio).strip(" -")
    if not limpio or len(limpio) < 3:
        return None

    clave = _sin_acentos(limpio).lower().strip()
    if clave in ALIAS_TIENDAS:
        return ALIAS_TIENDAS[clave]

    for alias_k in sorted(ALIAS_TIENDAS.keys(), key=len, reverse=True):
        if re.search(rf"\b{re.escape(alias_k)}\b", clave):
            return ALIAS_TIENDAS[alias_k]

    if len(clave) >= 4 and not clave.isdigit():
        id_estable = 200 + (sum(ord(c) for c in clave) % 700)
        return id_estable, limpio.title()
    return None


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
        "categoria_ps": "TEXT DEFAULT ''",
        "marca_extraida": "TEXT DEFAULT ''",
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
    conn.executemany(
        "INSERT OR IGNORE INTO tiendas (id, nombre) VALUES (?, ?)",
        list(TODAS_LAS_TIENDAS.items()),
    )

    # Limpiar los 5 SKUs de la prueba anterior que quedaron con 0 por no abrir el modal
    conn.execute(
        """
        UPDATE zapatos_candidatos
        SET ultima_sincronizacion = NULL
        WHERE id_ps IN ('1172396', '1315635', '1315643', '1316216')
        """
    )
    conn.commit()


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
        return True
    except Exception:
        return False


def depurar_solo_44_catalogos(conn: sqlite3.Connection) -> None:
    cur = conn.cursor()
    filas = cur.execute(
        "SELECT id_ps, COALESCE(origen_catalogo, '') FROM zapatos_candidatos"
    ).fetchall()

    ids_borrar: List[Tuple[str]] = []
    actualizaciones: List[Tuple[str, str]] = []

    for id_ps, orig in filas:
        id_str = str(id_ps).strip()
        texto_orig = str(orig).strip()
        nom_oficial = MAPA_CATALOGOS.get(extraer_id_catalogo(texto_orig))
        if nom_oficial is None:
            ids_borrar.append((id_str,))
            continue
        if texto_orig != nom_oficial:
            actualizaciones.append((nom_oficial, id_str))

    if filas and len(ids_borrar) == len(filas) and not PERMITIR_BORRAR_TODO:
        return

    puede_borrar = not ids_borrar or respaldar_bd(conn)
    if not puede_borrar:
        ids_borrar = []

    if actualizaciones:
        cur.executemany("UPDATE zapatos_candidatos SET origen_catalogo = ? WHERE id_ps = ?", actualizaciones)
    if ids_borrar:
        cur.executemany("DELETE FROM zapatos_candidatos WHERE id_ps = ?", ids_borrar)
        cur.executemany("DELETE FROM historial_stock_21_dias WHERE id_ps = ?", ids_borrar)
    conn.commit()


# ===========================================================================
# SELECCION DE LOTE (PRIORIZA MULTITIENDA Y CUADRANTES VERDES)
# ===========================================================================
def _dias_sin_ver(ult_sinc: object, ahora: datetime) -> float:
    try:
        fecha_ant = datetime.fromisoformat(str(ult_sinc))
        return max(0.05, (ahora - fecha_ant).total_seconds() / 86400.0)
    except Exception:
        return 10.0


def _puntaje_ucb(alpha: float, beta: float, dias_sin_ver: float, factor_etapa: float) -> float:
    explotacion = alpha / max(0.5, beta)
    exploracion = 1.41 * (math.sqrt(max(0.5, alpha)) / max(0.5, beta))
    return (explotacion + exploracion) * math.log(1.0 + dias_sin_ver) * factor_etapa


def seleccionar_lote_estrategico(conn: sqlite3.Connection, limite: int = LOTE_MICRO_SESION) -> List[tuple]:
    seleccionados_set = {
        str(r[0]).strip().lstrip("0") or str(r[0]).strip()
        for r in conn.execute("SELECT id_ps FROM seleccion_ml").fetchall()
    }

    skus_multitienda = {
        str(r[0]).strip()
        for r in conn.execute(
            "SELECT id_ps FROM stock_tienda GROUP BY id_ps HAVING COUNT(DISTINCT tienda_id) > 1"
        ).fetchall()
    }

    skus_con_stock_vallejo = {
        str(r[0]).strip()
        for r in conn.execute(
            "SELECT id_ps FROM stock_tienda WHERE tienda_id = 1 GROUP BY id_ps HAVING SUM(cantidad) > 0"
        ).fetchall()
    }

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
               COALESCE(origen_catalogo, ''),
               COALESCE(tipo_producto, '')
        FROM zapatos_candidatos
        WHERE activo_monitoreo = 1
          AND origen_catalogo IS NOT NULL
          AND TRIM(origen_catalogo) != ''
        """
    ).fetchall()

    ahora = datetime.now()
    carril_seleccionados = []
    carril_virgenes = []
    carril_ucb = []

    for fila in filas:
        id_ps, nombre, url, alpha, beta, stock_ant, ult_sinc, _costo, origen_cat, tipo_p = fila
        id_str = str(id_ps).strip()
        id_clean = id_str.lstrip("0") or id_str
        alpha_f = float(alpha) if alpha else 1.0
        beta_f = float(beta) if beta else 2.0
        stock_ant_i = int(stock_ant if stock_ant is not None else -1)

        url_txt = str(url).strip() if url else ""
        tiene_url_directa = 0 if "/productos/" in url_txt else 1
        url_usar = url_txt if url_txt.startswith("http") else f"https://www.priceshoes.com/buscar?q={id_str}"
        nombre_usar = str(nombre).strip() if nombre else f"Modelo {id_str}"

        etapa, _cuad = inv.clasificar_matriz_maslow(nombre_usar, origen_cat, tipo_p)
        orden_etapa = 0 if etapa == inv.ETAPA_VERDE else (1 if etapa == inv.ETAPA_NARANJA else 2)
        factor_etapa = 1.8 if orden_etapa == 0 else (1.3 if orden_etapa == 1 else 1.0)
        orden_cat = ORDEN_PRIORIDAD_CAT.get(str(origen_cat).strip(), 999)

        dias_dt = _dias_sin_ver(ult_sinc, ahora) if ult_sinc else 15.0

        if id_clean in seleccionados_set and (not ult_sinc or dias_dt >= 0.20 or id_str not in skus_multitienda):
            carril_seleccionados.append(
                (2000.0, id_str, nombre_usar, url_usar, alpha_f, beta_f, stock_ant_i, dias_dt, origen_cat)
            )
            continue

        tiene_stk_conocido = id_str in skus_con_stock_vallejo or stock_ant_i > 0
        if not ult_sinc or (tiene_stk_conocido and id_str not in skus_multitienda):
            prioridad_stock = 0 if (tiene_stk_conocido and id_str not in skus_multitienda) else 1
            score_v = 1500.0 - (prioridad_stock * 200.0) - (tiene_url_directa * 300.0) - (orden_etapa * 100.0) - (orden_cat * 1.5)
            carril_virgenes.append(
                (prioridad_stock, tiene_url_directa, orden_etapa, orden_cat, id_str, score_v,
                 nombre_usar, url_usar, alpha_f, beta_f, stock_ant_i, 15.0, origen_cat)
            )
            continue

        score_u = _puntaje_ucb(alpha_f, beta_f, dias_dt, factor_etapa)
        carril_ucb.append(
            (score_u, id_str, nombre_usar, url_usar, alpha_f, beta_f, stock_ant_i, dias_dt, origen_cat)
        )

    carril_virgenes.sort(key=lambda x: (x[0], x[1], x[2], x[3], x[4]))
    carril_ucb.sort(key=lambda x: x[0], reverse=True)

    lote_final = list(carril_seleccionados[:limite])
    restante = limite - len(lote_final)
    if restante > 0:
        lote_final.extend(
            (v[5], v[4], v[6], v[7], v[8], v[9], v[10], v[11], v[12])
            for v in carril_virgenes[:restante]
        )
    restante = limite - len(lote_final)
    if restante > 0:
        lote_final.extend(carril_ucb[:restante])

    return lote_final


# ===========================================================================
# EXTRACCION DEL DOM Y MODAL DE TIENDAS (TODAS LAS SUCURSALES)
# ===========================================================================
PATRON_COSTO_SOCIO = re.compile(r"Socio:\s*[\r\n\s]*\$\s*([\d,]+(?:\.\d{2})?)", re.IGNORECASE)


def validar_costo_socio(costo_socio: float, precio_publico: float) -> Tuple[str, str]:
    if costo_socio <= 0:
        return "sin_lectura", "No se leyó costo de socio; se conserva el anterior."
    if precio_publico <= 0:
        return "sin_publico", "No se pudo leer el precio público para validar el costo."

    ratio = costo_socio / precio_publico
    if ratio >= 1.0:
        return "sospechoso", f"Costo ${costo_socio:,.0f} >= público ${precio_publico:,.0f}."
    if ratio > RATIO_COSTO_MAX_ESPERADO:
        return "revisar", f"Descuento de socio de solo {(1 - ratio) * 100:.0f}%."
    if ratio < RATIO_COSTO_MIN_ESPERADO:
        return "revisar", f"Descuento de socio de {(1 - ratio) * 100:.0f}% (alto)."
    return "ok", f"Costo validado: {(1 - ratio) * 100:.0f}% bajo el público."


def _esperar_hidratacion_ficha(page: Page) -> None:
    """Espera a que React monte el bloque de Socio o el botón de disponibilidad en tiendas."""
    try:
        page.wait_for_function(
            """
            () => document.body && (
                document.body.innerText.includes('Socio') ||
                document.body.innerText.includes('Ver disponibilidad en tiendas')
            )
            """,
            timeout=9000,
        )
    except Exception:
        pass


def _abrir_modal_tiendas(page: Page) -> bool:
    """Hace scroll al botón 'Ver disponibilidad en tiendas', lo abre y espera la tabla de sucursales."""
    try:
        btn_disp = page.locator("text=Ver disponibilidad en tiendas").first
        btn_disp.wait_for(state="attached", timeout=5000)
        btn_disp.scroll_into_view_if_needed()
        page.wait_for_timeout(500)
        btn_disp.click(force=True)

        page.wait_for_function(
            """
            () => document.body && (
                document.body.innerText.includes('Ecommerce') ||
                document.body.innerText.includes('TIENDAS CERCANAS')
            )
            """,
            timeout=8000,
        )
        page.wait_for_timeout(1200)
        return True
    except Exception:
        return False


def _tallas_del_encabezado(encabezado_str: str) -> List[str]:
    tallas_detectadas = re.findall(r"\b\d{2}\.[05]\b", encabezado_str)
    if tallas_detectadas:
        return tallas_detectadas
    tokens_enc = [t.strip().upper() for t in re.split(r"[\s\t\r\n]+", encabezado_str) if t.strip()]
    return [t for t in tokens_enc if PATRON_TALLA_VALIDA.match(t)]


def extraer_todas_tiendas_tabla_dom(page: Page) -> List[Dict[str, Any]]:
    filas_inventario: List[Dict[str, Any]] = []
    try:
        tablas_raw = page.evaluate(
            """
            () => {
                const resultado = [];
                const tablas = Array.from(document.querySelectorAll('table'));
                for (const tabla of tablas) {
                    const filas = Array.from(tabla.querySelectorAll('tr')).map(tr =>
                        Array.from(tr.querySelectorAll('th, td')).map(c => (c.innerText || '').trim())
                    );
                    if (filas.length >= 2) {
                        resultado.push(filas);
                    }
                }
                return resultado;
            }
            """
        )
        for filas_tabla in tablas_raw or []:
            encabezado = filas_tabla[0]
            if not encabezado or "tienda" not in encabezado[0].lower():
                continue
            tallas_cols: List[Tuple[int, str]] = []
            for idx_col, texto_col in enumerate(encabezado[1:], start=1):
                t_limpia = texto_col.strip().upper()
                if re.match(r"^\d{2}\.[05]$", t_limpia) or PATRON_TALLA_VALIDA.match(t_limpia):
                    tallas_cols.append((idx_col, t_limpia))
            if not tallas_cols:
                continue

            for fila in filas_tabla[1:]:
                if not fila:
                    continue
                par_tienda = resolver_tienda_por_nombre(fila[0])
                if not par_tienda:
                    continue
                id_t, nom_t = par_tienda
                for idx_col, talla_str in tallas_cols:
                    val_celda = fila[idx_col].strip() if idx_col < len(fila) else ""
                    m_num = re.search(r"\b(\d+)\b", val_celda)
                    cant = int(m_num.group(1)) if m_num else 0
                    filas_inventario.append(
                        {
                            "tienda_id": id_t,
                            "tienda_nombre": nom_t,
                            "talla": talla_str,
                            "cantidad": cant,
                        }
                    )
    except Exception:
        pass
    return filas_inventario


def extraer_todas_tiendas_texto_modal(texto_modal: str) -> List[Dict[str, Any]]:
    if "TIENDAS CERCANAS" in texto_modal.upper():
        zona_tabla = re.split(r"TIENDAS CERCANAS[^\n]*", texto_modal, flags=re.IGNORECASE)[-1]
    else:
        zona_tabla = texto_modal

    m_enc = re.search(r"Tienda\s+([\s\S]+?)(?=\bEcommerce\b|\bVallejo\b)", zona_tabla, re.IGNORECASE)
    if not m_enc:
        return []

    tallas_detectadas = _tallas_del_encabezado(m_enc.group(1))
    if not tallas_detectadas:
        return []

    cuerpo_tiendas = zona_tabla[m_enc.end():]
    cuerpo_norm = _sin_acentos(cuerpo_tiendas)
    cuerpo_norm = re.sub(r"\ba\s+\d+(?:[\.,]\d+)?\s*km\b", " ", cuerpo_norm, flags=re.I)
    cuerpo_norm = re.sub(r"\bTu\s+tienda\b", " ", cuerpo_norm, flags=re.I)

    nombres_buscar = sorted(ALIAS_TIENDAS.keys(), key=len, reverse=True)
    patron_nombres = "|".join(re.escape(n) for n in nombres_buscar)
    patron_tienda = re.compile(rf"\b({patron_nombres})\b", re.IGNORECASE)

    coincidencias = list(patron_tienda.finditer(cuerpo_norm))
    if not coincidencias:
        return []

    filas_inventario: List[Dict[str, Any]] = []
    n_tallas = len(tallas_detectadas)

    for idx_m, match_t in enumerate(coincidencias):
        alias_encontrado = match_t.group(1).lower().strip()
        par_tienda = ALIAS_TIENDAS.get(alias_encontrado)
        if not par_tienda:
            continue
        id_t, nom_t = par_tienda

        inicio_valores = match_t.end()
        fin_valores = coincidencias[idx_m + 1].start() if (idx_m + 1) < len(coincidencias) else len(cuerpo_norm)
        segmento_fila = cuerpo_norm[inicio_valores:fin_valores]

        tokens_stock = re.findall(r"(?:\b\d+\b|—|--|-)", segmento_fila)
        if not tokens_stock:
            continue

        for i_t, talla_str in enumerate(tallas_detectadas):
            if i_t >= n_tallas:
                break
            tok = tokens_stock[i_t] if i_t < len(tokens_stock) else ""
            cant = int(tok) if tok.isdigit() else 0
            filas_inventario.append(
                {
                    "tienda_id": id_t,
                    "tienda_nombre": nom_t,
                    "talla": talla_str,
                    "cantidad": cant,
                }
            )

    return filas_inventario


def consolidar_inventario_multitienda(
    filas_json: List[Dict[str, Any]],
    filas_dom_tabla: List[Dict[str, Any]],
    filas_texto_modal: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    consolidado: Dict[Tuple[int, str], Dict[str, Any]] = {}

    for fuente in (filas_texto_modal, filas_dom_tabla, filas_json):
        for f in fuente:
            try:
                id_t = int(f["tienda_id"])
                talla = str(f["talla"]).strip()
                cant = max(0, int(f["cantidad"]))
                nom_t = str(f.get("tienda_nombre") or TODAS_LAS_TIENDAS.get(id_t) or f"Tienda {id_t}").strip()
                if id_t > 0 and talla:
                    consolidado[(id_t, talla)] = {
                        "tienda_id": id_t,
                        "tienda_nombre": nom_t,
                        "talla": talla,
                        "cantidad": cant,
                    }
            except Exception:
                continue

    return list(consolidado.values())


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


def obtener_url_canonica(page: Page) -> str:
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


def auditar_sku(conn: sqlite3.Connection, page: Page, item: tuple) -> str:
    _score, id_ps, nombre_ant, url, alpha_ant, beta_ant, stock_ant, dias_dt, origen_cat = item
    filas_json_capturadas: List[Dict[str, Any]] = []
    tiendas_json_capturadas: List[Dict[str, Any]] = []

    def _escuchar_respuesta(resp: Response) -> None:
        try:
            ctype = resp.headers.get("content-type", "").lower()
            if "json" not in ctype:
                return
            datos_j = resp.json()
            t_extraidas = inv.extraer_tiendas_json(datos_j)
            if t_extraidas:
                tiendas_json_capturadas.extend(t_extraidas)
            inv_extraido = inv.extraer_inventario_json(datos_j)
            if inv_extraido:
                filas_json_capturadas.extend(inv_extraido)
        except Exception:
            pass

    page.on("response", _escuchar_respuesta)
    try:
        era_busqueda = "/buscar" in url
        page.goto(url, timeout=32000, wait_until="domcontentloaded")

        if "/buscar" in page.url:
            page.wait_for_timeout(1200)
            enlace_prod = page.locator(f"a[href*='{id_ps}']").first
            if enlace_prod.count() > 0:
                href_p = enlace_prod.get_attribute("href")
                if href_p:
                    url_dest = href_p if href_p.startswith("http") else f"https://www.priceshoes.com{href_p}"
                    page.goto(url_dest, timeout=30000, wait_until="domcontentloaded")
            else:
                ahora_iso = datetime.now().isoformat(timespec="seconds")
                conn.execute(
                    """
                    UPDATE zapatos_candidatos
                    SET ultima_sincronizacion = ?, stock_total_anterior = 0, tier = 'Z', activo_monitoreo = 0
                    WHERE id_ps = ?
                    """,
                    (ahora_iso, id_ps),
                )
                conn.commit()
                print(f"   ⚪ SKU {id_ps} no listado en web actual de PS -> marcado Tier Z.")
                return "no_web"

        if "/productos/" not in page.url:
            if era_busqueda:
                ahora_iso = datetime.now().isoformat(timespec="seconds")
                conn.execute(
                    "UPDATE zapatos_candidatos SET ultima_sincronizacion = ?, stock_total_anterior = 0, tier = 'Z' WHERE id_ps = ?",
                    (ahora_iso, id_ps),
                )
                conn.commit()
                return "no_web"
            return "fallo"

        # 1. Esperar hidratación del bloque de Socio y abrir modal de tiendas
        _esperar_hidratacion_ficha(page)
        html = page.content()
        ficha_next = inv.extraer_ficha_next_data(html)
        prod_next = inv._producto_next_data(html)
        if prod_next:
            filas_json_capturadas.extend(inv.extraer_inventario_json(prod_next))

        _abrir_modal_tiendas(page)

        if tiendas_json_capturadas:
            inv.registrar_tiendas(conn, tiendas_json_capturadas)

        texto_pagina = page.locator("body").inner_text()

        # 2. Consolidar las 3 capas de inventario multi-tienda
        filas_dom_tabla = extraer_todas_tiendas_tabla_dom(page)
        filas_texto_modal = extraer_todas_tiendas_texto_modal(texto_pagina)
        filas_todas_tiendas = consolidar_inventario_multitienda(
            filas_json_capturadas, filas_dom_tabla, filas_texto_modal
        )

        # 3. Extraer Costo Socio y Precio Público
        costo_next = float(ficha_next.get("costo_socio") or 0.0)
        match_socio = PATRON_COSTO_SOCIO.search(texto_pagina)
        costo_dom = float(match_socio.group(1).replace(",", "")) if match_socio else 0.0
        costo_real = costo_dom if costo_dom > 0 else costo_next

        publico_next = float(ficha_next.get("precio_publico") or 0.0)
        publico_dom = extraer_precio_publico(html, texto_pagina, id_ps)
        precio_publico = publico_next if publico_next > 0 else publico_dom

        nombre_h1 = str(ficha_next.get("nombre") or "").strip()
        if not nombre_h1:
            try:
                loc_h1 = page.locator("h1").first
                if loc_h1.count() > 0:
                    nombre_h1 = loc_h1.inner_text().strip()
            except Exception:
                pass

        es_placeholder = (not nombre_ant.strip()) or bool(
            re.match(r"^(?:Modelo|Cat[aá]logo)\s+\S+$", nombre_ant.strip(), re.I)
        )
        nombre_guardar = nombre_h1 if (nombre_h1 and es_placeholder) else (nombre_h1 or nombre_ant)
        categoria_ps = str(ficha_next.get("categoria_ps") or "")
        marca_ps = str(ficha_next.get("marca") or "")
        tipo_prod = inv.clasificar_tipo_producto(nombre_guardar, categoria_ps)
        url_canonica = obtener_url_canonica(page)
        ahora_iso = datetime.now().isoformat(timespec="seconds")
        estado_val, _msg_val = validar_costo_socio(costo_real, precio_publico)

        if costo_real <= 0 and not filas_todas_tiendas:
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

        # Regla de seguridad: si se leyó el costo pero no abrió la tabla de tiendas, NO pisar el stock con 0
        if not filas_todas_tiendas:
            print(
                f"   ⚠️ Costo ${costo_real:,.0f} leído, pero el modal de tiendas no devolvió filas: "
                "se conserva el stock anterior."
            )
            conn.execute(
                """
                UPDATE zapatos_candidatos
                SET nombre_modelo = COALESCE(NULLIF(?, ''), nombre_modelo),
                    costo_socio = CASE WHEN ? > 0 THEN ? ELSE costo_socio END,
                    precio_publico = CASE WHEN ? > 0 THEN ? ELSE precio_publico END,
                    costo_validacion = ?,
                    url_real = COALESCE(NULLIF(?, ''), url_real),
                    ultima_sincronizacion = ?,
                    tipo_producto = ?,
                    categoria_ps = CASE WHEN ? != '' THEN ? ELSE categoria_ps END,
                    marca_extraida = CASE WHEN ? != '' THEN ? ELSE marca_extraida END
                WHERE id_ps = ?
                """,
                (
                    nombre_guardar, costo_real, costo_real, precio_publico, precio_publico,
                    estado_val, url_canonica, ahora_iso, tipo_prod,
                    categoria_ps, categoria_ps, marca_ps, marca_ps, id_ps,
                ),
            )
            conn.commit()
            return "ok"

        tallas_vallejo: Dict[str, int] = {
            str(f["talla"]): int(f["cantidad"])
            for f in filas_todas_tiendas
            if int(f["tienda_id"]) == 1
        }
        stock_vallejo_actual = sum(tallas_vallejo.values())

        stock_por_tienda: Dict[str, int] = {}
        for f in filas_todas_tiendas:
            if int(f["tienda_id"]) != inv.ID_TIENDA_ECOMMERCE:
                nom_s = str(f["tienda_nombre"])
                stock_por_tienda[nom_s] = stock_por_tienda.get(nom_s, 0) + int(f["cantidad"])
        stock_nacional_fisico = sum(stock_por_tienda.values())
        num_tiendas_capturadas = len({int(f["tienda_id"]) for f in filas_todas_tiendas})

        if stock_ant is None or stock_ant < 0:
            nuevo_alpha, nuevo_beta = alpha_ant, beta_ant
        else:
            ventas_det = max(0, stock_ant - stock_vallejo_actual)
            nuevo_alpha = (GAMMA_MEMORIA * alpha_ant) + ventas_det
            nuevo_beta = (GAMMA_MEMORIA * beta_ant) + max(0.1, dias_dt)

        velocidad = round(nuevo_alpha / max(0.5, nuevo_beta), 2)
        if stock_vallejo_actual == 0 and stock_nacional_fisico == 0 and nuevo_beta >= 4.0:
            tier, activo = "Z", 0
        elif velocidad >= 2.5 and stock_vallejo_actual > 0:
            tier, activo = "S", 1
        elif velocidad >= 0.8 and stock_vallejo_actual > 0:
            tier, activo = "A", 1
        else:
            tier, activo = "B", 1

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
                tipo_producto = ?,
                categoria_ps = CASE WHEN ? != '' THEN ? ELSE categoria_ps END,
                marca_extraida = CASE WHEN ? != '' THEN ? ELSE marca_extraida END
            WHERE id_ps = ?
            """,
            (
                nombre_guardar, costo_real, costo_real, precio_publico, precio_publico,
                estado_val, url_canonica, round(nuevo_alpha, 3), round(nuevo_beta, 3),
                velocidad, stock_vallejo_actual, ahora_iso, tier, activo, tipo_prod,
                categoria_ps, categoria_ps, marca_ps, marca_ps, id_ps,
            ),
        )

        if tallas_vallejo:
            conn.execute("DELETE FROM historial_stock_21_dias WHERE id_ps = ?", (id_ps,))
            conn.executemany(
                "INSERT INTO historial_stock_21_dias (id_ps, talla, stock_vallejo, fecha) VALUES (?, ?, ?, ?)",
                [(id_ps, talla, cant, ahora_iso) for talla, cant in tallas_vallejo.items()],
            )

        tienda_act_id, _ = inv.tienda_activa(conn)
        inv.guardar_revision(conn, id_ps, tienda_act_id, filas_todas_tiendas, ficha=ficha_next)
        conn.commit()

        _, cuad = inv.clasificar_matriz_maslow(nombre_guardar, origen_cat, tipo_prod)
        top_sucursales = ", ".join(
            f"{k}: {v}" for k, v in sorted(stock_por_tienda.items(), key=lambda x: x[1], reverse=True)[:5] if v > 0
        ) or "0 en sucursales"
        print(
            f"   ✅ [{cuad}] SKU {id_ps} | Costo: ${costo_real:,.0f} | "
            f"🏬 {num_tiendas_capturadas} tiendas ({stock_nacional_fisico} pzas | {top_sucursales})"
        )
        return "ok"
    finally:
        try:
            page.remove_listener("response", _escuchar_respuesta)
        except Exception:
            pass


# ===========================================================================
# FLUJO PRINCIPAL
# ===========================================================================
def ejecutar_recolector_inteligente() -> None:
    print("=" * 85)
    print(f"🚀 MOTOR MULTI-TIENDA (TODAS LAS SUCURSALES): Lote de {LOTE_MICRO_SESION} SKUs")
    print("=" * 85)

    Path("data").mkdir(exist_ok=True)
    conn = sqlite3.connect(RUTA_BD, timeout=30)
    try:
        conn.execute("PRAGMA journal_mode=WAL;")
        asegurar_estructura_bd(conn)
        depurar_solo_44_catalogos(conn)

        lote = seleccionar_lote_estrategico(conn, limite=LOTE_MICRO_SESION)
        if not lote:
            print("⚠️ No hay SKUs pendientes por auditar.")
            return

        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=MODO_INVISIBLE,
                args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
            )
            try:
                kwargs_ctx: Dict[str, Any] = {
                    "user_agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
                    ),
                    "viewport": {"width": 1366, "height": 768},
                }
                if os.path.exists(RUTA_SESION):
                    print(f"🔐 Usando sesión de socio activa en '{RUTA_SESION}'.")
                    kwargs_ctx["storage_state"] = RUTA_SESION
                else:
                    print("🌐 Sin archivo de sesión local (modo Cloud).")

                context = browser.new_context(**kwargs_ctx)
                page = context.new_page()

                sin_datos_seguidos = 0
                exitos = 0
                for idx, item in enumerate(lote, 1):
                    score, id_ps, nombre, _url, _a, _b, _s, _d, origen_cat = item
                    print(f"[{idx}/{len(lote)}] [{origen_cat}] SKU {id_ps} ({nombre[:40]}) | Prio: {score:.1f}")

                    try:
                        estado = auditar_sku(conn, page, item)
                    except Exception as e:
                        conn.rollback()
                        print(f"   ⚠️ Error en SKU {id_ps}: {e}")
                        estado = "fallo"

                    if estado == "fallo":
                        sin_datos_seguidos += 1
                    else:
                        sin_datos_seguidos = 0
                        if estado == "ok":
                            exitos += 1

                    if sin_datos_seguidos >= MAX_SIN_DATOS_SEGUIDOS:
                        print(f"\n🛑 {sin_datos_seguidos} fallos seguidos. Deteniendo corrida por seguridad.")
                        break

                    if idx < len(lote):
                        time.sleep(random.uniform(2.2, 4.2))
            finally:
                browser.close()
    finally:
        conn.close()

    conn_res = sqlite3.connect(RUTA_BD)
    try:
        filas_t = conn_res.execute(
            """
            SELECT COALESCE(t.nombre, 'Tienda ' || s.tienda_id), COUNT(DISTINCT s.id_ps), COALESCE(SUM(s.cantidad), 0)
            FROM stock_tienda s
            LEFT JOIN tiendas t ON t.id = s.tienda_id
            GROUP BY s.tienda_id
            ORDER BY SUM(s.cantidad) DESC
            """
        ).fetchall()
        print("\n📊 RESUMEN DE INVENTARIO MULTI-TIENDA EN `stock_tienda`:")
        for nom_t, n_skus, tot_pzas in filas_t:
            print(f"   🏬 {nom_t}: {n_skus:,} SKUs revisados ({tot_pzas:,} piezas totales)")
    finally:
        conn_res.close()
    print(f"\n✅ ¡Corrida finalizada! {exitos} SKUs procesados.")


if __name__ == "__main__":
    ejecutar_recolector_inteligente()