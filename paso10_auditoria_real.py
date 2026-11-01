import sys
import re
import xml.etree.ElementTree as ET
from bs4 import BeautifulSoup
from curl_cffi import requests as tls_requests

# Forzar codificacion UTF-8 para Windows
sys.stdout.reconfigure(encoding='utf-8')

def auditar_extraccion_real():
    print("=" * 70)
    print("🔬 AUDITORÍA FORENSE DE EXTRACCIÓN REAL (Price Shoes)")
    print("=" * 70)

    # 1. Probar conectarse a la pagina de inicio oficial
    url_inicio = "https://www.priceshoes.com/"
    print(f"⏳ Conectando en vivo a la portada: {url_inicio}")

    try:
        resp = tls_requests.get(url_inicio, impersonate="chrome124", timeout=20)
        print(f"📥 Código HTTP de respuesta de la portada: {resp.status_code}")
        
        if resp.status_code == 200:
            soup = BeautifulSoup(resp.text, "html.parser")
            titulo = soup.title.string.strip() if soup.title else "Sin título"
            print(f"✅ ¡CONEXIÓN EXITOSA! Título de la web: {titulo}")
            
            # Extraer enlaces de categorias o productos reales desde la portada
            enlaces = soup.find_all("a", href=True)
            links_productos = [a["href"] for a in enlaces if "/productos/" in a["href"] or "/catalogo/" in a["href"]]
            print(f"🔍 Se encontraron {len(links_productos)} enlaces internos de productos/catálogos en la portada.")
        else:
            print(f"⚠️ La portada respondió con código {resp.status_code}")

    except Exception as e:
        print(f"❌ Error conectando a la portada: {e}")

    print("\n" + "=" * 70)
    print("🗺️ PROBANDO EXTRACCIÓN DIRECTA DESDE EL SITEMAP OFICIAL (Datos 100% Reales)")
    print("=" * 70)
    
    url_sitemap = "https://www.priceshoes.com/sitemap/sitemap_zapatos.xml"
    print(f"⏳ Descargando el catálogo completo desde: {url_sitemap}")

    try:
        resp_xml = tls_requests.get(url_sitemap, impersonate="chrome124", timeout=25)
        print(f"📥 Código HTTP del Sitemap: {resp_xml.status_code}")

        if resp_xml.status_code == 200:
            root = ET.fromstring(resp_xml.content)
            ns = {'ns': 'http://www.sitemaps.org/schemas/sitemap/0.9'}
            urls = [loc.text.strip() for loc in root.findall('.//ns:loc', ns) if loc.text]
            
            print(f"🎉 ¡ÉXITO TOTAL! Se extrajeron {len(urls):,} enlaces de calzado real de la base de datos de Price Shoes.")
            print("\n📋 Muestra de los primeros 5 productos reales vigentes en la tienda:")
            print("-" * 70)
            for idx, u in enumerate(urls[:5], 1):
                # Limpiar el nombre del producto de la URL
                slug = u.split("/")[-1].replace("-", " ").title()
                print(f" {idx}. [Modelo Real Extraído]")
                print(f"    • Nombre: {slug}")
                print(f"    • Enlace Directo: {u}")
                print("-" * 70)
                
            print("\n💡 PRUEBA FINAL:")
            print("   Copia cualquiera de los 'Enlaces Directos' de arriba, pégalo en tu navegador")
            print("   y verás que te abre exactamente el producto real en la tienda oficial.")
        else:
            print(f"❌ No se pudo descargar el sitemap (HTTP {resp_xml.status_code})")

    except Exception as e:
        print(f"❌ Error procesando el sitemap: {e}")

if __name__ == "__main__":
    auditar_extraccion_real()