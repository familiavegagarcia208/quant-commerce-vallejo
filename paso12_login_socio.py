import os
import json
from pathlib import Path
from playwright.sync_api import sync_playwright

RUTA_SESION = "data/sesion_vallejo.json"

def iniciar_sesion_socio():
    print("=" * 70)
    print("🔐 ASISTENTE DE INICIO DE SESIÓN DE SOCIO (Price Shoes)")
    print("=" * 70)
    print("Se abrirá una ventana de navegador controlada.")
    print("Por favor, inicia sesión manualmente con tu cuenta de socio.")
    print("Una vez que estés dentro de tu panel de socio, presiona ENTER en esta terminal.\n")

    Path("data").mkdir(exist_ok=True)

    with sync_playwright() as p:
        # Lanzar el navegador visible para que puedas ingresar tus datos
        browser = p.chromium.launch(headless=False, slow_mo=50)
        context = browser.new_context()
        page = context.new_page()

        # Ir a la página de inicio de sesión de Price Shoes
        page.goto("https://www.priceshoes.com/login")

        # Pausa para que inicies sesión a mano
        input("👉 [ACCIÓN REQUERIDA]: Inicia sesión en la ventana del navegador y cuando veas tu cuenta activa, presiona ENTER aquí en la terminal...")

        # Guardar las cookies y el estado de la sesión actual
        storage = context.storage_state()
        with open(RUTA_SESION, "w", encoding="utf-8") as f:
            json.dump(storage, f, ensure_ascii=False, indent=4)

        print(f"\n✅ ¡Sesión guardada con éxito en '{RUTA_SESION}'!")
        print("   A partir de ahora, los scripts usarán esta sesión para consultar inventarios reales sin pedir contraseña.")
        
        browser.close()

if __name__ == "__main__":
    iniciar_sesion_socio()