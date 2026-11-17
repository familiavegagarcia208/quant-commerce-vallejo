# -*- coding: utf-8 -*-
"""
========================================================================================
AMPLIADOR Y UNIFICADOR MASIVO DE BASE DE DATOS (8,165 SKUs DEL CATÁLOGO)
========================================================================================
1. Rescata costos y existencias de todas las tablas/columnas históricas de SQLite.
2. Cruza los 8,165 SKUs contra TODOS los Sitemaps XML de Price Shoes (sin tope de 500)
   para asignarles nombre real y URL directa.
3. Extrae en paralelo (12 hilos vía curl_cffi) los datos de `__NEXT_DATA__`
   (costo_socio, precio_publico, marca, categoría y tallas) usando `inventario_tiendas.py`.
4. Ejecuta `paso5_motor_financiero.py` para dejar calculados los precios y ganancias.
========================================================================================
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
import re
import sqlite3
from typing import Any, Dict, List, Optional, Tuple

from curl_cffi import requests as tls_requests

import inventario_tiendas as inv
import paso5_motor_financiero as finanzas

RUTA_BD = Path("data/zapateria_vallejo.db")
MAX_HILOS = 12


def imprimir_radiografia_bd(conn: sqlite3.Connection, titulo: str) -> None:
    """Muestra exactamente cuántos registros hay en cada etapa para no estar a ciegas."""
    total = conn.execute("SELECT COUNT(*) FROM zapatos_candidatos").fetchone()[0]
    con_url = conn.execute(
        "SELECT COUNT(*) FROM zapatos_candidatos WHERE COALESCE(url_real, '') LIKE 'http%'"
    ).fetchone()[0]
    con_nombre_real = conn.execute(
        "SELECT COUNT(*) FROM zapatos_candidatos WHERE COALESCE(nombre_modelo, '') != '' "
        "AND LOWER(nombre_modelo) NOT LIKE 'catálogo%' AND LOWER(nombre_modelo) NOT LIKE 'catalogo%' "
        "AND LOWER(nombre_modelo) NOT LIKE 'modelo %'"
    ).fetchone()[0]
    con_costo = conn.execute(
        "SELECT COUNT(*) FROM zapatos_candidatos WHERE COALESCE(costo_socio, 0) > 0"
    ).fetchone()[0]
    con_pub = conn.execute(
        "SELECT COUNT(*) FROM zapatos_candidatos WHERE COALESCE(precio_publico, 0) > 0"
    ).fetchone()[0]
    en_stock_tienda = conn.execute(
        "SELECT COUNT(DISTINCT id_ps) FROM stock_tienda WHERE cantidad > 0"
    ).fetchone()[0]
    revisados_stock = conn.execute(
        "SELECT COUNT(DISTINCT id_ps) FROM stock_tienda"
    ).fetchone()[0]

    print("\n" + "=" * 80)
    print(f"📊 RADIOGRAFÍA DE LA BASE DE DATOS: {titulo}")
    print("=" * 80)
    print(f"   📘 Total SKUs en Catálogo (`zapatos_candidatos`) : {total:,}")
    print(f"   🔗 Con URL directa de producto (`url_real`)      : {con_url:,}")
    print(f"   🏷️  Con Nombre comercial real                    : {con_nombre_real:,}")
    print(f"   💰 Con Costo de Socio (> $0)                     : {con_costo:,}")
    print(f"   🏪 Con Precio Público PS (> $0)                  : {con_pub:,}")
    print(f"   🔍 Con Existencias revisadas en tienda           : {revisados_stock:,} ({en_stock_tienda:,} con stock > 0)")
    print("=" * 80)


def rescatar_datos_historicos_locales(conn: sqlite3.Connection) -> None:
    """Unifica datos de tablas y columnas creadas por versiones previas del motor ETL."""
    print("\n🔄 FASE 1: Rescatando datos históricos dentro de `zapateria_vallejo.db`...")
    inv.asegurar_esquema(conn)
    finanzas.asegurar_esquema_financiero(conn)

    tablas = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    cols_zc = {c[1] for c in conn.execute("PRAGMA table_info(zapatos_candidatos)").fetchall()}

    # 1. Recuperar de `zapatos_respaldo_no_catalogo` si había SKUs con datos útiles
    if "zapatos_respaldo_no_catalogo" in tablas:
        cols_resp = {c[1] for c in conn.execute("PRAGMA table_info(zapatos_respaldo_no_catalogo)").fetchall()}
        if "costo_socio" in cols_resp:
            conn.execute(
                """
                UPDATE zapatos_candidatos
                SET costo_socio = COALESCE(
                    (SELECT r.costo_socio FROM zapatos_respaldo_no_catalogo r
                     WHERE r.id_ps = zapatos_candidatos.id_ps AND r.costo_socio > 0),
                    costo_socio
                )
                WHERE COALESCE(costo_socio, 0) <= 0
                """
            )

    # 2. Copiar `costo_base` -> `costo_socio` si existía del motor anterior
    if "costo_base" in cols_zc:
        conn.execute(
            """
            UPDATE zapatos_candidatos
            SET costo_socio = costo_base
            WHERE COALESCE(costo_socio, 0) <= 0 AND COALESCE(costo_base, 0) > 0
            """
        )

    # 3. Copiar `catalogo_origen` -> `origen_catalogo` si estaba en otra columna
    if "catalogo_origen" in cols_zc:
        conn.execute(
            """
            UPDATE zapatos_candidatos
            SET origen_catalogo = catalogo_origen
            WHERE COALESCE(origen_catalogo, '') = '' AND COALESCE(catalogo_origen, '') != ''
            """
        )

    # 4. Rescatar existencias desde `variantes_sucursal` o `historial_inventario` si existen
    ahora_iso = datetime.now().isoformat(timespec="seconds")
    if "variantes_sucursal" in tablas:
        cols_vs = {c[1] for c in conn.execute("PRAGMA table_info(variantes_sucursal)").fetchall()}
        col_sku = "sku" if "sku" in cols_vs else ("id_ps" if "id_ps" in cols_vs else None)
        col_talla = "variante" if "variante" in cols_vs else ("talla" if "talla" in cols_vs else None)
        if col_sku and col_talla and "stock_vallejo" in cols_vs:
            conn.execute(
                f"""
                INSERT OR IGNORE INTO historial_stock_21_dias (id_ps, talla, stock_vallejo, fecha)
                SELECT TRIM({col_sku}), TRIM({col_talla}), MAX(COALESCE(stock_vallejo, 0)), ?
                FROM variantes_sucursal
                WHERE COALESCE(stock_vallejo, 0) >= 0
                GROUP BY TRIM({col_sku}), TRIM({col_talla})
                """,
                (ahora_iso,),
            )

    # 5. Rescatar stock general guardado en `zapatos_candidatos` (`stock_vallejo` o `stock_actual`)
    for col_stk in ("stock_vallejo", "stock_actual", "stock_total_anterior"):
        if col_stk in cols_zc:
            filas_stk = conn.execute(
                f"""
                SELECT id_ps, {col_stk}
                FROM zapatos_candidatos
                WHERE COALESCE({col_stk}, -1) > 0
                  AND id_ps NOT IN (SELECT DISTINCT id_ps FROM historial_stock_21_dias)
                """
            ).fetchall()
            if filas_stk:
                conn.executemany(
                    "INSERT INTO historial_stock_21_dias (id_ps, talla, stock_vallejo, fecha) VALUES (?, '25.0', ?, ?)",
                    [(str(i).strip(), int(s), ahora_iso) for i, s in filas_stk],
                )

    # 6. Registrar en `stock_tienda` los SKUs que ya fueron auditados y salieron agotados (0 pzas)
    filas_agotadas = conn.execute(
        """
        SELECT id_ps, COALESCE(ultima_sincronizacion, ?)
        FROM zapatos_candidatos
        WHERE ultima_sincronizacion IS NOT NULL
          AND COALESCE(stock_total_anterior, -1) = 0
          AND id_ps NOT IN (SELECT DISTINCT id_ps FROM stock_tienda WHERE tienda_id = 1)
        """,
        (ahora_iso,),
    ).fetchall()
    if filas_agotadas:
        conn.executemany(
            "INSERT OR IGNORE INTO stock_tienda (id_ps, tienda_id, talla, cantidad, fecha) VALUES (?, 1, 'UNICA', 0, ?)",
            [(str(i).strip(), str(f)) for i, f in filas_agotadas],
        )

    inv._migrar_stock_vallejo(conn)
    inv._clasificar_pendientes(conn)
    conn.commit()


def sincronizar_todos_los_sitemaps(conn: sqlite3.Connection) -> int:
    """Descarga TODOS los Sitemaps de productos de Price Shoes sin el límite de 500."""
    print("\n🌐 FASE 2: Descargando índice completo de Sitemaps de Price Shoes...")
    sitemaps_productos: List[str] = []
    try:
        r_idx = tls_requests.get("https://www.priceshoes.com/sitemap.xml", impersonate="chrome120", timeout=20)
        if r_idx.status_code == 200:
            locs = re.findall(r"<loc>(.*?)</loc>", r_idx.text)
            sitemaps_productos = [u.strip() for u in locs if "product" in u.lower() or u.endswith(".xml")]
    except Exception as e:
        print(f"   ⚠️ Aviso al leer sitemap principal: {e}")

    if not sitemaps_productos:
        sitemaps_productos = [
            f"https://www.priceshoes.com/sitemap_products_{i}.xml" for i in range(1, 10)
        ]

    patron_url = re.compile(r"https://www\.priceshoes\.com/productos/([a-zA-Z0-9\-]+?)[_\-]?(\d{5,8})$")
    mapa_urls: Dict[str, Tuple[str, str]] = {}

    for url_xml in sitemaps_productos:
        try:
            resp = tls_requests.get(url_xml, impersonate="chrome120", timeout=25)
            if resp.status_code != 200:
                continue
            locs = re.findall(r"<loc>(.*?)</loc>", resp.text)
            for loc in locs:
                u_clean = loc.strip()
                m = patron_url.search(u_clean)
                if m:
                    slug, id_ps = m.group(1), m.group(2)
                    nombre_limpio = slug.replace("-", " ").strip().title()
                    mapa_urls[id_ps] = (nombre_limpio, u_clean)
                    mapa_urls[id_ps.lstrip("0") or id_ps] = (nombre_limpio, u_clean)
            print(f"   ✅ Leído {url_xml}: {len(mapa_urls):,} productos indexados acumulados.")
        except Exception:
            continue

    if not mapa_urls:
        return 0

    filas_bd = conn.execute(
        "SELECT id_ps, COALESCE(nombre_modelo, ''), COALESCE(url_real, '') FROM zapatos_candidatos"
    ).fetchall()

    actualizaciones: List[Tuple[str, str, str, str]] = []
    for id_ps, nom_act, url_act in filas_bd:
        id_str = str(id_ps).strip()
        par = mapa_urls.get(id_str) or mapa_urls.get(id_str.lstrip("0") or id_str)
        if not par:
            continue
        nom_sm, url_sm = par
        es_prov = (not nom_act) or nom_act.lower().startswith(("catálogo", "catalogo", "modelo "))
        nuevo_nom = nom_sm if es_prov else nom_act
        nueva_url = url_sm if not str(url_act).startswith("http") else url_act
        if nuevo_nom != nom_act or nueva_url != url_act:
            tipo_p = inv.clasificar_tipo_producto(nuevo_nom)
            actualizaciones.append((nuevo_nom, nueva_url, tipo_p, id_str))

    if actualizaciones:
        conn.executemany(
            """
            UPDATE zapatos_candidatos
            SET nombre_modelo = ?, url_real = ?, tipo_producto = ?
            WHERE id_ps = ?
            """,
            actualizaciones,
        )
        conn.commit()
    print(f"   🎯 SKUs actualizados con Nombre Real y URL directa del Sitemap: {len(actualizaciones):,}")
    return len(actualizaciones)


def _extraer_ficha_rapida_sku(item: Tuple[str, str, str]) -> Optional[Tuple[str, Dict[str, Any], List[Dict[str, Any]], str]]:
    """Consulta la ficha de un SKU vía HTTP ligero y extrae `__NEXT_DATA__` sin abrir navegador."""
    id_ps, url_real, _origen = item
    url_intento = url_real if url_real.startswith("http") else f"https://www.priceshoes.com/buscar?q={id_ps}"

    try:
        resp = tls_requests.get(url_intento, impersonate="chrome120", timeout=15, allow_redirects=True)
        if resp.status_code != 200 or not resp.text:
            return None
        html = resp.text
        url_final = str(resp.url)

        # Si cayó en página de búsqueda, buscar el enlace a `/productos/...`
        if "/productos/" not in url_final:
            m_rel = re.search(
                rf'href=["\']((?:https://www\.priceshoes\.com)?/productos/[a-zA-Z0-9\-]*{re.escape(id_ps)}[a-zA-Z0-9\-]*)["\']',
                html,
                re.I,
            )
            if not m_rel:
                return None
            enlace = m_rel.group(1)
            url_prod = enlace if enlace.startswith("http") else f"https://www.priceshoes.com{enlace}"
            resp2 = tls_requests.get(url_prod, impersonate="chrome120", timeout=15, allow_redirects=True)
            if resp2.status_code != 200 or not resp2.text:
                return None
            html = resp2.text
            url_final = str(resp2.url)

        ficha = inv.extraer_ficha_next_data(html)
        if not ficha:
            return None

        # Intentar extraer inventario si viene embebido en __NEXT_DATA__
        prod_raw = inv._producto_next_data(html)
        filas_inv = inv.extraer_inventario_json(prod_raw) if prod_raw else []
        return id_ps, ficha, filas_inv, url_final
    except Exception:
        return None


def ampliar_costos_y_fichas_en_paralelo(conn: sqlite3.Connection, max_skus: int = 1500) -> int:
    """
    Recorre en paralelo los SKUs que aún no tienen `costo_socio` o `precio_publico`,
    dando prioridad a los que ya tienen `url_real` y a los Cuadrantes Verdes.
    """
    filas = conn.execute(
        """
        SELECT id_ps, COALESCE(url_real, ''), COALESCE(origen_catalogo, ''), COALESCE(nombre_modelo, '')
        FROM zapatos_candidatos
        WHERE COALESCE(costo_socio, 0) <= 0 OR COALESCE(precio_publico, 0) <= 0
        """
    ).fetchall()

    if not filas:
        print("\n✅ Todos los SKUs ya cuentan con costo de socio y precio público.")
        return 0

    # Priorizar: 1) Con URL directa `/productos/`, 2) Cuadrantes Verdes
    def _prioridad(f: tuple) -> Tuple[int, int, str]:
        id_s, u_r, orig_c, nom_m = f
        tiene_url = 0 if "/productos/" in str(u_r) else 1
        etapa, _ = inv.clasificar_matriz_maslow(nom_m, orig_c)
        es_verde = 0 if etapa == inv.ETAPA_VERDE else (1 if etapa == inv.ETAPA_NARANJA else 2)
        return (tiene_url, es_verde, str(id_s))

    filas_ordenadas = sorted(filas, key=_prioridad)[:max_skus]
    lote = [(str(f[0]).strip(), str(f[1]).strip(), str(f[2]).strip()) for f in filas_ordenadas]

    print(
        f"\n⚡ FASE 3: Extrayendo fichas y costos en paralelo ({MAX_HILOS} hilos) "
        f"para {len(lote):,} SKUs pendientes (de {len(filas):,} sin costo completo)..."
    )

    actualizados = 0
    procesados = 0
    ahora_iso = datetime.now().isoformat(timespec="seconds")

    try:
        with ThreadPoolExecutor(max_workers=MAX_HILOS) as pool:
            futuros = {pool.submit(_extraer_ficha_rapida_sku, item): item[0] for item in lote}
            for fut in as_completed(futuros):
                procesados += 1
                res = fut.result()
                if res is not None:
                    id_ps, ficha, filas_inv, url_final = res
                    nombre = str(ficha.get("nombre") or "")
                    categoria = str(ficha.get("categoria_ps") or "")
                    costo = float(ficha.get("costo_socio") or 0.0)
                    publico = float(ficha.get("precio_publico") or 0.0)
                    tipo_p = inv.clasificar_tipo_producto(nombre, categoria)
                    est_val = "ok" if (costo > 0 and publico > costo) else "sin_publico"

                    conn.execute(
                        """
                        UPDATE zapatos_candidatos
                        SET nombre_modelo = CASE WHEN ? != '' THEN ? ELSE nombre_modelo END,
                            costo_socio = CASE WHEN ? > 0 THEN ? ELSE costo_socio END,
                            precio_publico = CASE WHEN ? > 0 THEN ? ELSE precio_publico END,
                            categoria_ps = CASE WHEN ? != '' THEN ? ELSE categoria_ps END,
                            tipo_producto = ?,
                            costo_validacion = ?,
                            url_real = CASE WHEN ? LIKE 'http%' THEN ? ELSE url_real END
                        WHERE id_ps = ?
                        """,
                        (
                            nombre, nombre, costo, costo, publico, publico,
                            categoria, categoria, tipo_p, est_val, url_final, url_final, id_ps,
                        ),
                    )
                    if filas_inv:
                        inv.guardar_revision(conn, id_ps, 1, filas_inv, ficha=ficha)
                    actualizados += 1

                if procesados % 100 == 0 or procesados == len(lote):
                    conn.commit()
                    print(
                        f"   ⏳ Avance: {procesados:,}/{len(lote):,} revisados -> "
                        f"✅ {actualizados:,} fichas con costo/precio extraídas..."
                    )
    except KeyboardInterrupt:
        conn.commit()
        print("\n⚠️ Pausa manual detectada (Ctrl+C). Todos los avances quedaron guardados en SQLite.")

    conn.commit()
    return actualizados


def main() -> None:
    if not RUTA_BD.exists():
        print(f"❌ No existe {RUTA_BD}.")
        return

    conn = inv.conectar(RUTA_BD)
    try:
        imprimir_radiografia_bd(conn, "ANTES DE AMPLIAR")
        rescatar_datos_historicos_locales(conn)
        sincronizar_todos_los_sitemaps(conn)
        ampliar_costos_y_fichas_en_paralelo(conn, max_skus=2000)
        imprimir_radiografia_bd(conn, "DESPUÉS DE AMPLIAR")
    finally:
        conn.close()

    print("\n⚙️ FASE 4: Calculando precios sugeridos, márgenes y semáforos para TODA la base...")
    finanzas.ejecutar_motor_financiero_y_sanitizacion()


if __name__ == "__main__":
    main()