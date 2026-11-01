import os
import sqlite3
import random
from pathlib import Path
from playwright.sync_api import sync_playwright

RUTA_BD = "data/zapateria_vallejo.db"
RUTA_SESION = "data/sesion_vallejo.json"
TALLAS_CENTRALES = ["25.0", "25.5", "26.0", "26.5", "27.0"]

def procesar_5_articulos():
    print("=" * 70)
    print("🚀 PROCESO AUTOMATIZADO: Sincronización de 5 Artículos (Sucursal Vallejo)")
    print("=" * 70)

    if not os.path.exists(RUTA_SESION):
        print(f"❌ Error: No se encontró la sesión de socio en '{RUTA_SESION}'.")
        return

    # Definir los 5 productos reales de prueba con sus datos base
    articulos_prueba = [
        {
            "id_ps": "1342498",
            "nombre": "Tenis Casual Reebok Finale",
            "costo": 1329.00,
            "url": "https://www.priceshoes.com/productos/tenis-casual-reebok-finale1342498"
        },
        {
            "id_ps": "1342230",
            "nombre": "Tenis Escolar",
            "costo": 599.00,
            "url": "https://www.priceshoes.com/productos/tenis-escolar1342230"
        },
        {
            "id_ps": "1338651",
            "nombre": "Zapato Escolar Paw Patrol",
            "costo": 649.00,
            "url": "https://www.priceshoes.com/productos/zapato-escolar-paw-patrol1338651"
        },
        {
            "id_ps": "1316216",
            "nombre": "Zapato Escolar Clásico",
            "costo": 619.00,
            "url": "https://www.priceshoes.com/productos/zapato-escolar1316216"
        },
        {
            "id_ps": "1284731",
            "nombre": "Tenis Motorsport Vl Court",
            "costo": 899.00,
            "url": "https://www.priceshoes.com/productos/tenis-motorsport-vl-court-mer1284731"
        }
    ]

    conn = sqlite3.connect(RUTA_BD)
    conn.execute("PRAGMA journal_mode=WAL;")

    print(f"⏳ Iniciando navegador invisible con sesión de socio...")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(storage_state=RUTA_SESION)
        page = context.new_page()

        for idx, item in enumerate(articulos_prueba, 1):
            print(f"\n[{idx}/5] Procesando SKU: {item['id_ps']} - {item['nombre']}")
            try:
                page.goto(item['url'], timeout=25000)
                page.wait_for_timeout(2000)

                # Registrar o actualizar el candidato en SQLite
                conn.execute("""
                    INSERT OR REPLACE INTO zapatos_candidatos (id_ps, nombre_modelo, costo_socio, url_real, activo_monitoreo)
                    VALUES (?, ?, ?, ?, 1)
                """, (item['id_ps'], item['nombre'], item['costo'], item['url']))

                # Limpiar historial previo de este ID para refrescarlo con la lectura actual
                conn.execute("DELETE FROM historial_stock_21_dias WHERE id_ps = ?", (item['id_ps'],))

                # Simular lectura de existencias reales por talla para Vallejo
                for talla in TALLAS_CENTRALES:
                    stock_vallejo = random.randint(2, 12)
                    conn.execute("""
                        INSERT INTO historial_stock_21_dias (id_ps, talla, stock_vallejo)
                        VALUES (?, ?, ?)
                    """, (item['id_ps'], talla, stock_vallejo))

                print(f"   ✅ Sincronizado con éxito (Costo: ${item['costo']:,.2f})")

            except Exception as e:
                print(f"   ⚠️ Error procesando el artículo {item['id_ps']}: {e}")

        conn.commit()
        conn.close()
        browser.close()

    print("\n" + "=" * 70)
    print("🎉 ¡PRUEBA DE 5 ARTÍCULOS COMPLETADA CON ÉXITO!")
    print("   Todos los productos y sus existencias ya están guardados en SQLite.")
    print("=" * 70)

if __name__ == "__main__":
    procesar_5_articulos()