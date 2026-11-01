import os
import sys
from playwright.sync_api import sync_playwright

# Forzar codificacion UTF-8 para Windows
sys.stdout.reconfigure(encoding='utf-8')

RUTA_SESION = "data/sesion_vallejo.json"

def probar_extraccion_con_sesion():
    print("=" * 70)
    print("📦 PRUEBA DE INVENTARIO CON SESIÓN DE SOCIO ACTIVA")
    print("=" * 70)

    if not os.path.exists(RUTA_SESION):
        print(f"❌ No se encontró el archivo de sesión en '{RUTA_SESION}'.")
        print("   Ejecuta primero: python paso12_login_socio.py")
        return

    # URL del tenis Reebok de prueba
    url_zapato = "https://www.priceshoes.com/productos/tenis-casual-reebok-finale1342498"

    with sync_playwright() as p:
        # Iniciamos el navegador cargando el estado de la sesión guardada (cookies de socio)
        browser = p.chromium.launch(headless=False) # Ponlo en True para que corra de forma invisible
        context = browser.new_context(storage_state=RUTA_SESION)
        page = context.new_page()

        print(f"⏳ Navegando al producto con sesión de socio activa:\n   -> {url_zapato}")
        page.goto(url_zapato)

        try:
            # Esperar a que la página cargue elementos propios de socio (como existencias o sucursal)
            print("⏳ Esperando a que el servidor responda con el inventario...")
            page.wait_for_timeout(4000)

            # Intentar hacer clic en el botón de disponibilidad en tiendas si existe
            btn_disponibilidad = page.locator("text=Ver disponibilidad en tiendas")
            if btn_disponibilidad.count() > 0:
                btn_disponibilidad.click()
                print("🖱️ [Acción]: Clic en 'Ver disponibilidad en tiendas' ejecutado con éxito.")
                page.wait_for_timeout(2500) # Esperar que despliegue la tabla de tallas

            # Validar si el texto de la sucursal Vallejo aparece en el DOM de la página
            contenido_html = page.content()
            if "Vallejo" in contenido_html:
                print("\n✅ ¡ÉXITO TOTAL! El servidor reconoció la sesión de socio y cargó los datos de la sucursal Vallejo.")
                print("   El sistema ya puede leer las existencias exactas por talla automáticamente.")
            else:
                print("\n⚠️ La página cargó, pero no se visualiza el texto 'Vallejo'. Verifica si la sesión expiró.")

        except Exception as e:
            print(f"⚠️ Ocurrió un detalle durante la navegación automática: {e}")

        print("=" * 70)
        browser.close()

if __name__ == "__main__":
    probar_extraccion_con_sesion()