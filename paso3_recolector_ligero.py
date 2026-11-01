import os
import re
import math
import time
import random
import sqlite3
from pathlib import Path
from datetime import datetime
from curl_cffi import requests as tls_requests
from playwright.sync_api import sync_playwright

RUTA_BD = "data/zapateria_vallejo.db"
RUTA_SESION = "data/sesion_vallejo.json"

# ===========================================================================
# PARÁMETROS DE OPERACIÓN
# ===========================================================================
LOTE_MICRO_SESION = 60
MODO_INVISIBLE = True  # Cámbialo a True cuando verifiques que las tallas salen limpias

def asegurar_estructura_bd(conn):
    conn.execute("""
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
    """)

    columnas_existentes = [col[1] for col in conn.execute("PRAGMA table_info(zapatos_candidatos)").fetchall()]
    nuevas_columnas = {
        "alpha": "REAL DEFAULT 1.0",
        "beta": "REAL DEFAULT 2.0",
        "velocidad_diaria": "REAL DEFAULT 0.5",
        "stock_total_anterior": "INTEGER DEFAULT -1",
        "ultima_sincronizacion": "TIMESTAMP",
        "tier": "TEXT DEFAULT 'B'"
    }
    for col, tipo in nuevas_columnas.items():
        if col not in columnas_existentes:
            conn.execute(f"ALTER TABLE zapatos_candidatos ADD COLUMN {col} {tipo};")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS historial_stock_21_dias (
            id_ps TEXT,
            talla TEXT,
            stock_vallejo INTEGER,
            fecha TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Limpiar lecturas previas que hayan capturado números sin punto decimal
    skus_sucios = [
        r[0] for r in conn.execute(
            "SELECT DISTINCT id_ps FROM historial_stock_21_dias WHERE talla NOT LIKE '%.%'"
        ).fetchall()
    ]
    if skus_sucios:
        conn.execute("DELETE FROM historial_stock_21_dias WHERE talla NOT LIKE '%.%'")
        for s_id in skus_sucios:
            conn.execute("""
                UPDATE zapatos_candidatos
                SET ultima_sincronizacion = NULL, stock_total_anterior = -1
                WHERE id_ps = ?
            """, (s_id,))

    conn.commit()

def sembrar_catalogo_desde_sitemap(conn):
    total_actual = conn.execute("SELECT COUNT(*) FROM zapatos_candidatos").fetchone()[0]
    if total_actual > 50:
        print(f"📦 Catálogo activo en SQLite: {total_actual:,} artículos listos para monitoreo.")
        return

    print("🌐 Descargando catálogo completo desde el Sitemap XML oficial de Price Shoes...")
    urls_sitemap = [
        "https://www.priceshoes.com/sitemap.xml",
        "https://www.priceshoes.com/sitemap_products_1.xml"
    ]

    productos_encontrados = []
    patron_url = re.compile(r'https://www\.priceshoes\.com/productos/([a-zA-Z0-9\-]+?)(\d{6,8})$')

    for url_xml in urls_sitemap:
        try:
            resp = tls_requests.get(url_xml, impersonate="chrome120", timeout=20)
            if resp.status_code != 200:
                continue

            locs = re.findall(r'<loc>(.*?)</loc>', resp.text)
            sub_sitemaps = [u.strip() for u in locs if "product" in u.lower() or "sitemap" in u.lower()]
            for sub_url in sub_sitemaps[:5]:
                if sub_url.endswith(".xml") and sub_url not in urls_sitemap:
                    r_sub = tls_requests.get(sub_url, impersonate="chrome120", timeout=20)
                    for loc in re.findall(r'<loc>(.*?)</loc>', r_sub.text):
                        m = patron_url.search(loc.strip())
                        if m:
                            slug, id_ps = m.group(1), m.group(2)
                            nombre = slug.replace("-", " ").strip().title()
                            productos_encontrados.append((id_ps, nombre, loc.strip()))

            for loc in locs:
                m = patron_url.search(loc.strip())
                if m:
                    slug, id_ps = m.group(1), m.group(2)
                    nombre = slug.replace("-", " ").strip().title()
                    productos_encontrados.append((id_ps, nombre, loc.strip()))
        except Exception as e:
            print(f"   ⚠️ Aviso leyendo XML ({url_xml}): {e}")

    if productos_encontrados:
        conn.executemany("""
            INSERT OR IGNORE INTO zapatos_candidatos (id_ps, nombre_modelo, url_real, activo_monitoreo)
            VALUES (?, ?, ?, 1)
        """, productos_encontrados)
        conn.commit()
        nuevo_total = conn.execute("SELECT COUNT(*) FROM zapatos_candidatos").fetchone()[0]
        print(f"✅ ¡Sembrado exitoso! {nuevo_total:,} SKUs listos en la base de datos.")

def seleccionar_lote_ucb(conn, limite=LOTE_MICRO_SESION):
    filas = conn.execute("""
        SELECT id_ps, nombre_modelo, url_real, alpha, beta, stock_total_anterior, ultima_sincronizacion, costo_socio
        FROM zapatos_candidatos
        WHERE activo_monitoreo = 1 AND url_real IS NOT NULL
    """).fetchall()

    ahora = datetime.now()
    candidatos_puntuados = []

    for fila in filas:
        id_ps, nombre, url, alpha, beta, stock_ant, ult_sinc, costo = fila
        alpha = alpha if alpha else 1.0
        beta = beta if beta else 2.0

        # Prioridad extra a los SKUs que ya tienen precio o fueron limpiados para re-auditoría
        if not ult_sinc and costo and costo > 0:
            dias_sin_ver = 25.0
        elif not ult_sinc or not costo or costo <= 0 or stock_ant is None or stock_ant < 0:
            dias_sin_ver = 15.0
        else:
            try:
                fecha_ant = datetime.fromisoformat(str(ult_sinc))
                dias_sin_ver = max(0.05, (ahora - fecha_ant).total_seconds() / 86400.0)
            except:
                dias_sin_ver = 5.0

        explotacion = alpha / beta
        exploracion = 1.41 * (math.sqrt(alpha) / beta)
        score_ucb = (explotacion + exploracion) * math.log(1.0 + dias_sin_ver)

        candidatos_puntuados.append((score_ucb, id_ps, nombre, url, alpha, beta, stock_ant, dias_sin_ver))

    candidatos_puntuados.sort(key=lambda x: x[0], reverse=True)
    return candidatos_puntuados[:limite]

def extraer_datos_reales_dom(page):
    """Extrae el costo de socio real y únicamente las columnas de talla de la fila Vallejo."""
    costo_socio = 0.0
    tallas_vallejo = {}

    try:
        page.wait_for_function(
            "document.body && document.body.innerText.includes('Socio')",
            timeout=8000
        )
    except:
        pass

    texto_pagina = page.locator("body").inner_text()

    match_socio = re.search(r'Socio:\s*[\r\n\s]*\$\s*([\d,]+(?:\.\d{2})?)', texto_pagina, re.IGNORECASE)
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
                timeout=8000
            )
            page.wait_for_timeout(1200)
    except Exception as e:
        print(f"      ℹ️ Aviso al abrir modal de tiendas: {e}")

    texto_modal = page.locator("body").inner_text()

    # 1. Recortar estrictamente a partir de "TIENDAS CERCANAS" para ignorar el resto de la página
    if "TIENDAS CERCANAS" in texto_modal.upper():
        partes = re.split(r'TIENDAS CERCANAS[^\n]*', texto_modal, flags=re.IGNORECASE)
        zona_tabla = partes[-1]
    else:
        zona_tabla = texto_modal

    # 2. Aislar el encabezado entre "Tienda" y "Ecommerce" y la fila después de "Vallejo"
    match_bloque = re.search(
        r'Tienda\s+([\s\S]+?)\s+Ecommerce[\s\S]+?Vallejo\s*(?:Tu tienda)?\s*([\s\S]+)',
        zona_tabla,
        re.IGNORECASE
    )

    if match_bloque:
        encabezado_str = match_bloque.group(1)
        fila_vallejo_str = match_bloque.group(2)

        # Exigir punto decimal (.0 o .5) para capturar exclusivamente tallas reales (ej. 15.0, 25.5)
        tallas_detectadas = re.findall(r'\b\d{2}\.[05]\b', encabezado_str)

        if tallas_detectadas:
            # Cortar la fila de Vallejo antes de que empiece otra sucursal (Centro, Naucalpan, etc.)
            fila_vallejo_limpia = re.split(
                r'(?:Centro|Naucalpan|Olivar|Ecatepec|Tlalnepantla|Iztapalapa|Arcos|a\s+\d+\s*km)',
                fila_vallejo_str,
                flags=re.IGNORECASE
            )[0]

            tokens_vallejo = re.findall(r'(?:\b\d+\b|—|--|-)', fila_vallejo_limpia)
            for i, talla in enumerate(tallas_detectadas):
                if i < len(tokens_vallejo):
                    tok = tokens_vallejo[i]
                    stock_val = int(tok) if tok.isdigit() else 0
                    tallas_vallejo[talla] = stock_val
                else:
                    tallas_vallejo[talla] = 0

    return costo_socio, tallas_vallejo

def pausa_antiban_lognormal():
    pausa = min(22.0, max(3.5, random.lognormvariate(mu=1.7, sigma=0.45)))
    print(f"   🛡️ Pausa humana Anti-Ban: {pausa:.1f}s...")
    time.sleep(pausa)

def ejecutar_recolector_inteligente():
    print("=" * 75)
    print("🧠 MOTOR BAYESIANO QUANT-COMMERCE: Escaneo Real Anti-Ban (Vallejo)")
    print("=" * 75)

    if not os.path.exists(RUTA_SESION):
        print(f"❌ No se encontró '{RUTA_SESION}'. Ejecuta primero el inicio de sesión.")
        return

    Path("data").mkdir(exist_ok=True)
    conn = sqlite3.connect(RUTA_BD)
    conn.execute("PRAGMA journal_mode=WAL;")

    asegurar_estructura_bd(conn)
    sembrar_catalogo_desde_sitemap(conn)

    lote = seleccionar_lote_ucb(conn, limite=LOTE_MICRO_SESION)
    print(f"\n🎯 Lote UCB seleccionado: {len(lote)} artículos prioritarios para auditar en esta corrida.\n")

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=MODO_INVISIBLE,
            args=["--disable-blink-features=AutomationControlled"]
        )
        context = browser.new_context(
            storage_state=RUTA_SESION,
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
            viewport={"width": 1366, "height": 768}
        )
        page = context.new_page()

        for idx, (score, id_ps, nombre, url, alpha_ant, beta_ant, stock_ant, dias_dt) in enumerate(lote, 1):
            print(f"[{idx}/{len(lote)}] SKU {id_ps} ({nombre}) | Score UCB: {score:.2f}")
            try:
                page.goto(url, timeout=35000, wait_until="domcontentloaded")

                costo_real, tallas_reales = extraer_datos_reales_dom(page)
                stock_total_actual = sum(tallas_reales.values())

                if costo_real == 0.0 and not tallas_reales:
                    print("   ⚠️ No se detectó bloque de Socio en la página.")
                else:
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
                        tier = 'Z'
                        activo = 0
                    elif velocidad >= 2.5 and stock_total_actual > 0:
                        tier = 'S'
                    elif velocidad >= 0.8 and stock_total_actual > 0:
                        tier = 'A'
                    else:
                        tier = 'B'

                    ahora_iso = datetime.now().isoformat()

                    conn.execute("""
                        UPDATE zapatos_candidatos
                        SET costo_socio = CASE WHEN ? > 0 THEN ? ELSE costo_socio END,
                            alpha = ?,
                            beta = ?,
                            velocidad_diaria = ?,
                            stock_total_anterior = ?,
                            ultima_sincronizacion = ?,
                            tier = ?,
                            activo_monitoreo = ?
                        WHERE id_ps = ?
                    """, (costo_real, costo_real, round(nuevo_alpha, 3), round(nuevo_beta, 3),
                          velocidad, stock_total_actual, ahora_iso, tier, activo, id_ps))

                    if tallas_reales:
                        conn.execute("DELETE FROM historial_stock_21_dias WHERE id_ps = ?", (id_ps,))
                        for talla, cant in tallas_reales.items():
                            conn.execute("""
                                INSERT INTO historial_stock_21_dias (id_ps, talla, stock_vallejo)
                                VALUES (?, ?, ?)
                            """, (id_ps, talla, cant))

                    conn.commit()
                    print(f"   💰 Costo Socio: ${costo_real:,.2f} | 📦 Stock Total Vallejo: {stock_total_actual} pares")
                    print(f"   📈 Velocidad: {velocidad} pares/día | 🏆 Tier: {tier} | Tallas leídas: {tallas_reales}")

            except Exception as e:
                print(f"   ⚠️ Error en SKU {id_ps}: {e}")

            if idx < len(lote):
                pausa_antiban_lognormal()

        browser.close()
    conn.close()
    print("\n✅ ¡Micro-sesión finalizada y guardada en SQLite!")

if __name__ == "__main__":
    ejecutar_recolector_inteligente()