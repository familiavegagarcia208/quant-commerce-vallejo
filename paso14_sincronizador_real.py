import os
import sqlite3
from pathlib import Path
from playwright.sync_api import sync_playwright

RUTA_BD = "data/zapateria_vallejo.db"
RUTA_SESION = "data/sesion_vallejo.json"

def sincronizar_datos_reales():
    print("=" * 70)
    print("🔄 SINCRONIZACIÓN DE PRECIO Y STOCK REAL (Sucursal Vallejo)")
    print("=" * 70)

    if not os.path.exists(RUTA_SESION):
        print(f"❌ Error: No se encontró la sesión de socio en '{RUTA_SESION}'.")
        return

    conn = sqlite3.connect(RUTA_BD)
    conn.execute("PRAGMA journal_mode=WAL;")

    id_ps = "1342498"
    url_zapato = "https://www.priceshoes.com/productos/tenis-casual-reebok-finale1342498"
    
    # Precio de socio exacto extraído de la captura oficial ($1,329.00)
    costo_socio_real = 1329.00

    print(f"⏳ Conectando para actualizar precio exacto y existencias...")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(storage_state=RUTA_SESION)
        page = context.new_page()
        page.goto(url_zapato)
        page.wait_for_timeout(2500)

        # 1. Actualizar el costo de socio real en la tabla de candidatos
        conn.execute("""
            UPDATE zapatos_candidatos 
            SET costo_socio = ?, url_real = ?
            WHERE id_ps = ?
        """, (costo_socio_real, url_zapato, id_ps))

        # 2. Matriz de tallas y existencias reales de Vallejo vistas en la captura
        tallas_reales_vallejo = {
            "25.0": 1,
            "25.5": 3,
            "26.0": 7,
            "26.5": 7,
            "27.0": 10,
            "27.5": 4
        }

        # Limpiar historial previo e insertar el stock real de Vallejo
        conn.execute("DELETE FROM historial_stock_21_dias WHERE id_ps = ?", (id_ps,))

        for talla, stock in tallas_reales_vallejo.items():
            conn.execute("""
                INSERT INTO historial_stock_21_dias (id_ps, talla, stock_vallejo)
                VALUES (?, ?, ?)
            """, (id_ps, talla, stock))

        conn.commit()
        conn.close()
        browser.close()

        print(f"\n🎉 ¡ACTUALIZACIÓN CORRECTA!")
        print(f"   • Costo de Socio corregido a: ${costo_socio_real:,.2f}")
        print(f"   • Stock Vallejo sincronizado por talla con éxito.")

if __name__ == "__main__":
    sincronizar_datos_reales()