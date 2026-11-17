"""
=========================================================================================
PASO 0: EXTRACTOR OCR LOCAL GRATUITO (RAPIDOCR ONNX - SIN IA DE PAGO) -> SQLITE
=========================================================================================
"""

from __future__ import annotations

import logging
import re
import sqlite3
import sys
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from curl_cffi import requests as tls_requests
from rapidocr_onnxruntime import RapidOCR

try:
    import pymupdf as fitz
except ImportError:
    import fitz

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("ExtractorRapidOCR")

RUTA_BD: Path = Path("data/zapateria_vallejo.db")
DIR_PDFS: Path = Path("data/catalogos_pdf")

# Captura "ID" seguido de 6 a 8 dígitos (con o sin espacio, e ignorando asteriscos al final)
PATRON_ID_ESTRICTO = re.compile(r"ID\s*[:\.\-]?\s*(\d{6,8})\*?", re.IGNORECASE)
# Captura también códigos de 6 a 7 dígitos sueltos que coincidan con el Sitemap (ej. arriba de los QR)
PATRON_DIGITOS = re.compile(r"(?<!\d)(\d{6,7})(?!\d)")


def asegurar_tablas(conn: sqlite3.Connection) -> None:
    """Asegura la estructura en SQLite y limpia páginas previas con 0 detecciones."""
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
            tier TEXT DEFAULT 'B'
        )
        """
    )
    cols = [c[1] for c in conn.execute("PRAGMA table_info(zapatos_candidatos)").fetchall()]
    if "origen_catalogo" not in cols:
        conn.execute("ALTER TABLE zapatos_candidatos ADD COLUMN origen_catalogo TEXT DEFAULT '';")

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS control_paginas_pdf (
            archivo_pdf TEXT,
            num_pagina INTEGER,
            metodo_usado TEXT,
            ids_detectados INTEGER DEFAULT 0,
            fecha_procesado TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (archivo_pdf, num_pagina)
        )
        """
    )
    # Limpia las páginas que se guardaron con 0 IDs en intentos anteriores para no saltarlas
    conn.execute("DELETE FROM control_paginas_pdf WHERE ids_detectados = 0;")
    conn.commit()


def cargar_sitemap_urls(conn: sqlite3.Connection) -> Dict[str, Tuple[str, str]]:
    """Carga el índice de URLs reales desde SQLite o desde el Sitemap XML de Price Shoes."""
    mapa_urls: Dict[str, Tuple[str, str]] = {}
    filas = conn.execute(
        "SELECT id_ps, COALESCE(nombre_modelo, ''), COALESCE(url_real, '') FROM zapatos_candidatos"
    ).fetchall()
    for id_s, nom, url_r in filas:
        if id_s and url_r:
            mapa_urls[str(id_s).strip()] = (str(nom).strip(), str(url_r).strip())

    if len(mapa_urls) > 500:
        return mapa_urls

    patron_url = re.compile(r"https://www\.priceshoes\.com/productos/([a-zA-Z0-9\-]+?)(\d{5,8})$")
    sitemaps = [
        "https://www.priceshoes.com/sitemap.xml",
        "https://www.priceshoes.com/sitemap_products_1.xml",
    ]

    for url_xml in sitemaps:
        try:
            resp = tls_requests.get(url_xml, impersonate="chrome120", timeout=20)
            if resp.status_code == 200:
                locs = re.findall(r"<loc>(.*?)</loc>", resp.text)
                for loc in locs:
                    m = patron_url.search(loc.strip())
                    if m:
                        slug, id_ps = m.group(1), m.group(2)
                        mapa_urls[id_ps] = (slug.replace("-", " ").strip().title(), loc.strip())
        except Exception:
            pass

    return mapa_urls


def extraer_ids_pagina_rapidocr(
    motor_ocr: RapidOCR, pagina: fitz.Page, ids_conocidos_sitemap: Set[str]
) -> Set[str]:
    """Renderiza la página y extrae los IDs usando RapidOCR localmente."""
    ids_encontrados: Set[str] = set()

    # Renderiza la página (120 DPI es ideal para lectura rápida y precisa en RapidOCR)
    pix = pagina.get_pixmap(dpi=120, alpha=False)
    img_bytes = pix.tobytes("jpeg")

    resultados, _ = motor_ocr(img_bytes)
    if not resultados:
        return ids_encontrados

    lineas_texto: List[str] = [str(item[1]).strip() for item in resultados if len(item) > 1]
    texto_completo = "\n".join(lineas_texto)

    # 1. Buscar patrón explícito "ID 1344114" o "ID1342658"
    for match in PATRON_ID_ESTRICTO.findall(texto_completo):
        if not match.startswith(("2024", "2025", "2026", "000")):
            ids_encontrados.add(match)

    # 2. Buscar códigos de 6-7 dígitos que existan en el Sitemap oficial (ej. códigos junto a QR)
    for candidato in PATRON_DIGITOS.findall(texto_completo):
        if candidato in ids_conocidos_sitemap:
            ids_encontrados.add(candidato)

    return ids_encontrados


def ejecutar_extraccion_local() -> None:
    print("=" * 80)
    print("🚀 PASO 0: EXTRACTOR LOCAL GRATUITO (RAPIDOCR ONNX -> SQLITE)")
    print("=" * 80)

    DIR_PDFS.mkdir(parents=True, exist_ok=True)
    archivos_pdf = sorted(DIR_PDFS.glob("*.pdf"))
    if not archivos_pdf:
        print(f"⚠️ No se encontraron archivos PDF en: '{DIR_PDFS.resolve()}'.")
        return

    motor_ocr = RapidOCR()

    conn = sqlite3.connect(RUTA_BD, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL;")
    asegurar_tablas(conn)
    mapa_sitemap = cargar_sitemap_urls(conn)
    ids_sitemap_set = set(mapa_sitemap.keys())

    total_nuevos = 0
    total_priorizados = 0
    todos_los_ids: Set[str] = set()

    try:
        for idx, ruta_pdf in enumerate(archivos_pdf, 1):
            nombre_pdf = ruta_pdf.name
            doc = fitz.open(ruta_pdf)
            total_pags = len(doc)

            paginas_procesadas = {
                r[0]
                for r in conn.execute(
                    "SELECT num_pagina FROM control_paginas_pdf WHERE archivo_pdf = ? AND ids_detectados > 0",
                    (nombre_pdf,),
                ).fetchall()
            }

            print(
                f"\n📘 [{idx:02d}/{len(archivos_pdf):02d}] {nombre_pdf} "
                f"({total_pags} págs | {len(paginas_procesadas)} ya procesadas con éxito)"
            )

            ids_este_pdf: Set[str] = set()

            for num_p in range(total_pags):
                num_pag = num_p + 1
                if num_pag in paginas_procesadas:
                    continue

                ids_pag = extraer_ids_pagina_rapidocr(motor_ocr, doc[num_p], ids_sitemap_set)
                ids_este_pdf.update(ids_pag)
                todos_los_ids.update(ids_pag)

                for id_ps in ids_pag:
                    nombre_sitemap, url_sitemap = mapa_sitemap.get(
                        id_ps, (f"Catálogo {ruta_pdf.stem}", "")
                    )
                    url_real: Optional[str] = url_sitemap if url_sitemap else None

                    fila = conn.execute(
                        "SELECT id_ps FROM zapatos_candidatos WHERE id_ps = ?", (id_ps,)
                    ).fetchone()

                    if fila is None:
                        conn.execute(
                            """
                            INSERT INTO zapatos_candidatos (
                                id_ps, nombre_modelo, url_real, activo_monitoreo,
                                alpha, beta, origen_catalogo
                            ) VALUES (?, ?, ?, 1, 8.0, 1.0, ?)
                            """,
                            (id_ps, nombre_sitemap, url_real, nombre_pdf),
                        )
                        total_nuevos += 1
                    else:
                        conn.execute(
                            """
                            UPDATE zapatos_candidatos
                            SET activo_monitoreo = 1,
                                alpha = MAX(COALESCE(alpha, 1.0), 8.0),
                                beta = MIN(COALESCE(beta, 2.0), 1.0),
                                url_real = COALESCE(url_real, ?),
                                origen_catalogo = ?
                            WHERE id_ps = ?
                            """,
                            (url_real, nombre_pdf, id_ps),
                        )
                        total_priorizados += 1

                conn.execute(
                    """
                    INSERT OR REPLACE INTO control_paginas_pdf
                    (archivo_pdf, num_pagina, metodo_usado, ids_detectados)
                    VALUES (?, ?, 'RAPIDOCR_ONNX', ?)
                    """,
                    (nombre_pdf, num_pag, len(ids_pag)),
                )
                conn.commit()

                if ids_pag:
                    print(
                        f"   📄 Pág {num_pag}/{total_pags} -> ✅ {len(ids_pag)} IDs: "
                        f"{', '.join(sorted(ids_pag))}"
                    )

            doc.close()

    except KeyboardInterrupt:
        print("\n⚠️ Proceso pausado por el usuario (Ctrl+C). El avance quedó guardado en SQLite.")
        conn.close()
        sys.exit(0)

    conn.close()
    print("\n" + "=" * 80)
    print(f"✅ EXTRACCIÓN FINALIZADA: {len(todos_los_ids):,} IDs únicos detectados")
    print(f"   🆕 Nuevos insertados en SQLite : {total_nuevos:,}")
    print(f"   🚀 Existentes priorizados      : {total_priorizados:,}")
    print("=" * 80)


if __name__ == "__main__":
    ejecutar_extraccion_local()