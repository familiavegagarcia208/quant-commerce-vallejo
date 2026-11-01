import re
import sqlite3
import xml.etree.ElementTree as ET
from curl_cffi import requests as tls_requests

RUTA_BD = "data/zapateria_vallejo.db"
URL_SITEMAP_ZAPATOS = "https://www.priceshoes.com/sitemap/sitemap_zapatos.xml"

# 1. LISTA NEGRA AUTOMÁTICA (Evita baneos por derechos de autor, licencias y nichos de bajo margen)
PALABRAS_PROHIBIDAS = [
    # Marcas comerciales restringidas en Marketplaces (VeRO / Propiedad Intelectual)
    "nike", "adidas", "puma", "reebok", "converse", "chuck-taylor", "vans", "fila",
    "skechers", "under-armour", "ua-", "caterpillar", "lacoste", "tommy", "boss",
    # Licencias infantiles (Riesgo de infracción y alta devolución)
    "stitch", "stich", "mickey", "minnie", "minie", "spiderman", "spider-man", "kuromi",
    "hello-kitty", "kitty", "sanrio", "cinamorroll", "bluey", "paw-patrol", "minecraft",
    "disney", "marvel", "avengers", "sonic", "toy-story", "buzz", "woody", "frozen",
    "elsa", "barbie", "dragon-ball", "goku", "snoopy", "peanuts", "bob-esponja", "cars",
    "mario", "free-fire", "stranger-things", "looney", "bugs", "lola", "pooh", "igor",
    "dalmatas", "lotso", "campanita", "mandalorian", "star-wars", "droids", "chewie",
    # Nichos de bajo margen, defectos frecuentes o tallas infantiles
    "escolar", "colegial", "luces", "iluminacion", "lluvia", "pantufla", "sandalia",
    "sueco", "zueco", "alpargata", "bebe", "-inf", "-ps", "-jr", "-kids", "ni-o", "kit-"
]

# 2. PALABRAS CLAVE DE ALTO MARGEN (Suman puntos al algoritmo)
PUNTAJE_KEYWORDS = {
    "piel-vacuno": 35, "piel-de-res": 35, "piel-de-borrego": 35, "piel-cabra": 30,
    "piel": 20, "nobuck": 20, "gamuza": 15,
    "industrial": 30, "dielectrico": 30, "casco": 25, "trabajo": 25, "tactico": 25,
    "hiker": 25, "todoterreno": 20, "todo-terreno": 20, "outdoor": 20,
    "chelsea": 25, "vaquera": 25, "vaquero": 25, "texana": 25, "militar": 20,
    "suela-track": 20, "chunky": 15, "plataforma": 15, "confort": 20, "mocasin": 15
}

def analizar_url_zapato(url: str) -> dict | None:
    """Extrae el ID real de Price Shoes, filtra riesgos y calcula el puntaje de rentabilidad."""
    slug = url.rstrip("/").split("/")[-1].lower()

    # Filtro 1: Descartar si contiene cualquier palabra prohibida
    for prohibida in PALABRAS_PROHIBIDAS:
        if prohibida in slug:
            return None

    # Extraer el ID real del final de la URL (6 a 7 dígitos reales de catálogo)
    match = re.search(r'^(.*?)(1\d{6}|\d{5,6})(?:-\d+)?$', slug)
    if not match:
        return None

    nombre_slug, id_ps_str = match.group(1).strip("-"), match.group(2)
    id_num = int(id_ps_str)

    # Filtro 2: Descartar modelos viejos (saldos con stock inestable en Vallejo)
    # Nos quedamos solo con IDs >= 1250000 (Temporada reciente / vigente)
    if id_num < 1250000:
        return None

    # Calcular Puntaje Quant Automático
    puntaje = 0
    if id_num >= 1340000:
        puntaje += 50  # Lanzamiento ultra reciente (Stock lleno en bodega)
    elif id_num >= 1310000:
        puntaje += 30  # Temporada actual

    for kw, pts in PUNTAJE_KEYWORDS.items():
        if kw in nombre_slug:
            puntaje += pts

    # Clasificación inteligente en nuestras 4 categorías rentables
    if any(k in nombre_slug for k in ["industrial", "casco", "dielectrico", "trabajo", "tactico", "bostoniano", "oxford"]):
        categoria = "CALZADO_HOMBRE"
        costo_base = 490.0 if "piel" in nombre_slug or "industrial" in nombre_slug else 420.0
    elif any(k in nombre_slug for k in ["bota", "botin", "chelsea", "vaquer", "texan", "militar", "hiker"]):
        categoria = "BOTAS_DAMA"
        costo_base = 460.0 if "piel" in nombre_slug else 395.0
    elif any(k in nombre_slug for k in ["mocasin", "confort", "loafer", "derby"]):
        categoria = "CASUAL_CONFORT_DAMA"
        costo_base = 385.0 if "piel" in nombre_slug else 330.0
    else:
        categoria = "TENIS_DAMA"
        costo_base = 410.0 if "piel" in nombre_slug else 355.0

    nombre_limpio = nombre_slug.replace("-", " ").title()

    return {
        "id_ps": id_ps_str,
        "nombre_modelo": f"{nombre_limpio} (URL: {url})",
        "nombre_corto": nombre_limpio,
        "url_real": url,
        "categoria": categoria,
        "costo_socio": costo_base,
        "puntaje": puntaje,
        "id_num": id_num
    }

def ejecutar_curaduria_inteligente():
    print("🧠 Iniciando Curaduría Quant Automática sobre el Sitemap de Price Shoes...")
    resp = tls_requests.get(URL_SITEMAP_ZAPATOS, impersonate="chrome124", timeout=25)
    
    if resp.status_code != 200:
        print(f"❌ Error descargando sitemap: HTTP {resp.status_code}")
        return

    root = ET.fromstring(resp.content)
    ns = {'ns': 'http://www.sitemaps.org/schemas/sitemap/0.9'}
    todas_urls = [loc.text.strip() for loc in root.findall('.//ns:loc', ns) if loc.text]
    print(f"📥 URLs totales leídas del XML: {len(todas_urls):,}")

    # Analizar y filtrar automáticamente miles de modelos
    aprobados = []
    for u in todas_urls:
        item = analizar_url_zapato(u)
        if item:
            aprobados.append(item)

    print(f"🛡️ Modelos que superaron el Filtro Anti-Baneo y de Temporada Reciente: {len(aprobados):,}")

    # Ordenar de mayor a menor Puntaje Quant y por ID más reciente
    aprobados.sort(key=lambda x: (x["puntaje"], x["id_num"]), reverse=True)

    # Seleccionar el Top 30 de cada una de las 4 categorías (120 Golden Candidates equilibrados)
    seleccion_120 = []
    conteo_cat = {"BOTAS_DAMA": 0, "TENIS_DAMA": 0, "CASUAL_CONFORT_DAMA": 0, "CALZADO_HOMBRE": 0}

    for prod in aprobados:
        cat = prod["categoria"]
        if conteo_cat[cat] < 30:
            seleccion_120.append(prod)
            conteo_cat[cat] += 1
        if len(seleccion_120) == 120:
            break

    # Limpiar la base de datos de datos sintéticos viejos e insertar los 120 reales
    conn = sqlite3.connect(RUTA_BD)
    conn.execute("PRAGMA journal_mode=WAL;")
    
    # Agregar columna url_real si no existía
    try:
        conn.execute("ALTER TABLE zapatos_candidatos ADD COLUMN url_real TEXT;")
    except sqlite3.OperationalError:
        pass

    conn.execute("BEGIN IMMEDIATE;")
    conn.execute("DELETE FROM historial_stock_21_dias;")
    conn.execute("DELETE FROM zapatos_candidatos;")

    for p in seleccion_120:
        conn.execute("""
            INSERT INTO zapatos_candidatos (id_ps, nombre_modelo, categoria, costo_socio, activo_monitoreo, url_real)
            VALUES (?, ?, ?, ?, 1, ?)
        """, (p["id_ps"], p["nombre_corto"], p["categoria"], p["costo_socio"], p["url_real"]))
    conn.commit()
    conn.close()

    print("\n✅ ¡CURADURÍA INTELIGENTE COMPLETADA! Base de datos actualizada con 100% productos reales:")
    for cat, cant in conteo_cat.items():
        print(f"   • {cat}: {cant} modelos seleccionados")

    print("\n🏆 Top 5 Mejores Candidatos Detectados Automáticamente:")
    for i, top in enumerate(seleccion_120[:5], 1):
        print(f"   {i}. [ID: {top['id_ps']}] {top['nombre_corto']} | {top['categoria']} | Score: {top['puntaje']} pts")
        print(f"      🔗 {top['url_real']}")

if __name__ == "__main__":
    ejecutar_curaduria_inteligente()