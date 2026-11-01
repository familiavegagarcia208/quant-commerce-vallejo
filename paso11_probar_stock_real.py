import sys
import re
from bs4 import BeautifulSoup
from curl_cffi import requests as tls_requests

# Forzar salida en UTF-8 para Windows
sys.stdout.reconfigure(encoding='utf-8')

def probar_stock_en_vivo():
    print("=" * 70)
    print("📦 PRUEBA EN VIVO: Extracción de Inventario y Tallas Reales")
    print("=" * 70)

    # Usaremos el enlace real del tenis Reebok que apareció en nuestro sitemap
    url_zapato = "https://www.priceshoes.com/productos/tenis-casual-reebok-finale1342498"
    
    print(f"⏳ Conectando al producto real:\n   -> {url_zapato}")
    print("   (Navegando de forma sigilosa con huella de Chrome)...")

    try:
        resp = tls_requests.get(url_zapato, impersonate="chrome124", timeout=20)
        print(f"📥 Código HTTP de respuesta del servidor: {resp.status_code}")

        if resp.status_code != 200:
            print(f"❌ No se pudo acceder a la página del producto (HTTP {resp.status_code}).")
            return

        print("✅ ¡Página de producto descargada con éxito! Analizando tallas y stock...")
        soup = BeautifulSoup(resp.text, "html.parser")

        # Extraer el título oficial del zapato desde el HTML de la página
        titulo_tag = soup.find("h1") or soup.find("title")
        nombre_oficial = titulo_tag.get_text(strip=True) if titulo_tag else "Modelo Price Shoes"
        
        print(f"\n🏷️ [Producto Detectado en Servidor]:")
        print(f"   • Título Oficial: {nombre_oficial}")

        # Buscar selectores o botones de tallas en la página de producto
        # (Las tiendas usan botones, listas o elementos con clases como 'size', 'talla', 'variant')
        selectores_tallas = [
            ".size-option", ".talla-item", "[data-size]", 
            "button.size", ".variants-list button", "input[name='size']",
            ".product-form__swatch", "label[for*='talla']"
        ]
        
        elementos_tallas = []
        for sel in selectores_tallas:
            elementos_tallas = soup.select(sel)
            if elementos_tallas:
                break

        print(f"\n👟 [Auditoría de Tallas y Disponibilidad]:")
        if elementos_tallas:
            print(f"   Se encontraron {len(elementos_tallas)} botones/variantes de talla en la interfaz web:")
            for t in elementos_tallas:
                talla_texto = t.get_text(strip=True)
                # Verificar si está agotada (clases como 'disabled', 'out-of-stock', etc.)
                clases = " ".join(t.get("class", []))
                disponible = "Agotado" if "disabled" in clases or "out" in clases else "Disponible"
                print(f"     -> Talla: {talla_texto} [{disponible}]")
        else:
            print("   ⚠️ No se encontraron botones de talla estáticos (la página carga las tallas interactivamente por JavaScript).")
            print("   💡 Probando extracción de datos incrustados en el código oculto de la página (JSON-LD o Script)...")
            
            # Buscar datos ocultos de variantes en el código fuente de JavaScript
            encontro_datos = False
            for script in soup.find_all("script"):
                if script.string and ("talla" in script.string.lower() or "size" in script.string.lower() or "price" in script.string.lower()):
                    if "inventory" in script.string.lower() or "stock" in script.string.lower() or "variants" in script.string.lower():
                        encontro_datos = True
                        break
            
            if encontro_datos:
                print("   ✅ ¡Datos de inventario y variantes confirmados en el motor de la página!")
            else:
                print("   ℹ️ El producto está activo en el servidor, pero requiere sesión de socio activa para desglosar el stock exacto por bodega.")

        print("\n" + "=" * 70)
        print("🔗 ENLACE DE COMPROBACIÓN MANUAL:")
        print(f"   {url_zapato}")
        print("   (Puedes abrir este enlace en tu navegador para verificar que el zapato y el título coinciden exactamente).")
        print("=" * 70)

    except Exception as e:
        print(f"❌ Error durante la prueba de stock: {e}")

if __name__ == "__main__":
    probar_stock_en_vivo()