"""
========================================================================================
PASO 6: ESTUDIO FOTOGRÁFICO HD (EXCLUSIVO PARA LOS 44 CATÁLOGOS PDF OFICIALES)  · v2 corregida
========================================================================================
Genera el carrusel comercial en 1200x1200 px (1:1 HD) únicamente para productos que
provienen de los catálogos PDF oficiales, descartando cualquier residuo externo:
  - 01_portada_blanca.jpg   : 1.ª foto oficial maximizada en fondo blanco puro
  - 02_angulo_galeria.jpg   : 2.ª foto oficial extraída de Price Shoes (0° rotación)
  - 03_ficha_beneficios.jpg : Diseño comercial de 2 columnas + chips azules de stock real
  - 04_guia_tallas_cm.jpg   : Tabla doble columna con tallas intermedias + 3 pasos de medición
  (cada lámina se guarda también como 01.jpg … 04.jpg, que es lo que lee el panel Streamlit)

Correcciones de esta versión (respecto a la original)
-----------------------------------------------------
 1. ¡La carga del catálogo ya NO borra datos! Antes ejecutaba un DELETE sobre
    `zapatos_candidatos` y eliminaba para siempre los productos "Sin Catálogo (Web)"
    (que el panel Streamlit sí muestra). Ahora solo los ignora al leer.
 2. Ya no se inventa disponibilidad: sin datos reales de tallas, las láminas 03 y 04
    dejaban "DISPONIBLE" con tallas por defecto (p. ej. 23.5, 24.5, 25.0) aunque no
    hubiera stock de ellas. Ahora solo se muestran tallas reales; las presentaciones fijas
    (perfumería, hogar, accesorios) conservan su valor por defecto.
 3. IDs robustos: "PS-00123", "123.0" y "00123" se reconocen como el mismo producto.
 4. Costos/stock con formato "$1,250.50" o "5.0" ya no rompen la carga (ValueError).
 5. Tallas con coma decimal ("22,5") se normalizan igual que las de punto.
 6. Clasificador: "con cordones" / "con agujetas" / "horma" en un zapato ya no lo
    convierte en "plantilla/cuidado"; un zapato con solo la talla 22 en stock ya no se
    clasifica como infantil.
 7. Las depuraciones ya no hacen `rmtree` directo: las carpetas fuera de catálogo se
    mueven a `data/catalogo_descartado` (cambia ELIMINAR_DESCARTADAS para borrarlas).
 8. Los productos sin 2.ª foto ya no vuelven a consumir descargas web en cada corrida
    (marca `.intento_web`, reintento a los REINTENTO_DIAS días).
 9. Descargas: un 404/410 ya no se reintenta con 3 navegadores + urllib por cada URL
    adivinada; las expresiones regulares escapan el ID.
10. Más de 8 tallas: ahora caben hasta 10 chips y se avisa "+N tallas más" (antes se
    cortaban en silencio). Títulos demasiado largos se acortan con "..." en vez de
    chocar con la insignia.
11. Salida UTF-8 forzada (los emojis de los mensajes fallaban al redirigir en Windows),
    JPEG sin submuestreo de color (texto azul más nítido) y errores de lectura de
    tallas reportados en lugar de silenciados.
"""

from __future__ import annotations

import ast
from functools import lru_cache
from io import BytesIO
import json
import random
import re
import shutil
import sqlite3
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    from curl_cffi import requests as tls_requests
except ImportError:
    tls_requests = None

from PIL import Image, ImageChops, ImageDraw, ImageEnhance, ImageFont, ImageOps, ImageStat

RUTA_BD: Path = Path("data/zapateria_vallejo.db")
DIR_CATALOGO_LISTO: Path = Path("data/catalogo_listo")
DIR_DESCARTADOS: Path = Path("data/catalogo_descartado")
MAX_DESCARGAS_WEB_POR_CORRIDA: int = 40

# True = borrar carpetas fuera de catálogo; False (recomendado) = moverlas a DIR_DESCARTADOS.
ELIMINAR_DESCARTADAS: bool = False
# Días que se espera antes de volver a buscar en la web la foto que faltó.
REINTENTO_DIAS: int = 7
MARCA_INTENTO_WEB: str = ".intento_web"
# Máximo de chips de talla en la lámina 03 (el resto se resume como "+N tallas más").
MAX_CHIPS_03: int = 10
# Perfiles cuya "talla" por defecto es una presentación fija (no una talla real de stock).
TIPOS_CON_PRESENTACION_FIJA = {"perfumeria", "hogar_cocina", "accesorios"}
# Opciones de guardado JPEG: sin submuestreo de color para que el texto no se vea borroso.
OPCIONES_JPEG: Dict[str, Any] = {"quality": 95, "subsampling": 0, "optimize": True}

# Valores de `origen_catalogo` que significan "no viene de un PDF oficial".
ORIGENES_NO_PDF = {"", "none", "null", "nan", "sin catálogo (web)"}


# ======================================================================================
# UTILIDADES GENERALES
# ======================================================================================
def configurar_salida() -> None:
    """Fuerza UTF-8 en consola/tubería: los emojis fallan con cp1252 al redirigir en Windows."""
    for flujo in (sys.stdout, sys.stderr):
        try:
            flujo.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def normalizar_id(valor: Any) -> str:
    """'PS-00123' / '00123' / '123.0' → '123' (si todo son ceros, se conserva)."""
    s = re.sub(r"\.0+$", "", str(valor).strip())
    s = re.sub(r"^PS-", "", s, flags=re.IGNORECASE)
    return s.lstrip("0") or s


def a_float(valor: Any, default: float = 0.0) -> float:
    """Convierte '$1,250.50', '5', 5.0 o None a float sin lanzar excepciones."""
    if valor is None:
        return default
    try:
        return float(re.sub(r"[$,\s]", "", str(valor)))
    except ValueError:
        return default


# ======================================================================================
# FUENTES Y HELPERS DE TEXTO
# ======================================================================================
@lru_cache(maxsize=64)
def cargar_fuente(tamano: int, negrita: bool = False):
    rutas = (
        [
            "C:/Windows/Fonts/segoeuib.ttf",
            "C:/Windows/Fonts/arialbd.ttf",
            "C:/Windows/Fonts/calibrib.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
            "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        ]
        if negrita
        else [
            "C:/Windows/Fonts/segoeui.ttf",
            "C:/Windows/Fonts/arial.ttf",
            "C:/Windows/Fonts/calibri.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
            "/System/Library/Fonts/Supplemental/Arial.ttf",
        ]
    )
    for r in rutas:
        if Path(r).exists():
            try:
                return ImageFont.truetype(r, tamano)
            except OSError:
                pass
    return ImageFont.load_default()


def ajustar_fuente(draw, texto, ancho_max, tam_max, tam_min=11, negrita=False):
    """Devuelve la fuente más grande (entre tam_max y tam_min) con la que el texto cabe en ancho_max."""
    for tam in range(tam_max, tam_min - 1, -1):
        f = cargar_fuente(tam, negrita)
        if draw.textlength(texto, font=f) <= ancho_max:
            return f
    return cargar_fuente(tam_min, negrita)


def recortar_con_puntos(draw, texto: str, fuente, ancho_max: float) -> str:
    """Acorta `texto` con '...' si aun con la fuente más pequeña no cabe en `ancho_max`."""
    if draw.textlength(texto, font=fuente) <= ancho_max:
        return texto
    while texto and draw.textlength(texto + "...", font=fuente) > ancho_max:
        texto = texto[:-1]
    return texto.rstrip() + "..."


def envolver_texto(draw, texto, fuente, ancho_max):
    lineas, actual = [], ""
    for pal in texto.split():
        prueba = f"{actual} {pal}".strip()
        if not actual or draw.textlength(prueba, font=fuente) <= ancho_max:
            actual = prueba
        else:
            lineas.append(actual)
            actual = pal
    if actual:
        lineas.append(actual)
    return lineas


def envolver_ajustado(draw, texto, ancho_max, max_lineas, tam_max, tam_min=13, negrita=False):
    f, lineas, tam = cargar_fuente(tam_min, negrita), [texto], tam_min
    for tam in range(tam_max, tam_min - 1, -1):
        f = cargar_fuente(tam, negrita)
        lineas = envolver_texto(draw, texto, f, ancho_max)
        if len(lineas) <= max_lineas:
            return f, lineas, tam
    return f, lineas[:max_lineas], tam_min


def texto_centrado(draw, x0, x1, y, texto, fuente, fill):
    w = draw.textlength(texto, font=fuente)
    draw.text((x0 + (x1 - x0 - w) / 2, y), texto, fill=fill, font=fuente)


# ======================================================================================
# LIMPIEZA DE TÍTULOS E IMÁGENES
# ======================================================================================
def limpiar_titulo(nombre: str) -> str:
    limpio = re.sub(r"\b(caja|env[ií]o\s*hoy|sku|id|ps)\b", "", str(nombre), flags=re.IGNORECASE)
    limpio = re.sub(r"\b\d{5,8}\b", "", limpio)
    limpio = re.sub(r"\bcu\s+a\b", "Cuña", limpio, flags=re.IGNORECASE)
    limpio = re.sub(r"\bni\s+a\b", "Niña", limpio, flags=re.IGNORECASE)
    limpio = re.sub(r"\bni\s+o\b", "Niño", limpio, flags=re.IGNORECASE)
    limpio = re.sub(r"\s{2,}", " ", limpio).strip(" -·•|_")
    return limpio if limpio else "Calzado Selección Oficial"


def recortar_espacio_blanco(img: Image.Image) -> Image.Image:
    rgb = img.convert("RGB")
    w, h = rgb.size
    rgb_inner = rgb.crop((4, 4, w - 4, h - 4)) if (w > 30 and h > 30) else rgb
    iw, ih = rgb_inner.size

    esquinas = [
        rgb_inner.getpixel((0, 0)),
        rgb_inner.getpixel((iw - 1, 0)),
        rgb_inner.getpixel((0, ih - 1)),
        rgb_inner.getpixel((iw - 1, ih - 1)),
    ]
    bg_r = int(sorted(c[0] for c in esquinas)[2])
    bg_g = int(sorted(c[1] for c in esquinas)[2])
    bg_b = int(sorted(c[2] for c in esquinas)[2])

    color_fondo = (bg_r, bg_g, bg_b) if min(bg_r, bg_g, bg_b) >= 210 else (255, 255, 255)
    fondo_ref = Image.new("RGB", (iw, ih), color_fondo)
    diff = ImageChops.difference(rgb_inner, fondo_ref).convert("L")
    mascara = diff.point(lambda p: 255 if p > 18 else 0)

    bbox = mascara.getbbox()
    if bbox and (bbox[2] - bbox[0]) > 45 and (bbox[3] - bbox[1]) > 45:
        if color_fondo != (255, 255, 255):
            lienzo_limpio = Image.new("RGB", (iw, ih), (255, 255, 255))
            mascara_suave = diff.point(lambda p: 255 if p > 12 else 0)
            lienzo_limpio.paste(rgb_inner, (0, 0), mascara_suave)
            return lienzo_limpio.crop(bbox)
        return rgb_inner.crop(bbox)
    return rgb


def son_imagenes_iguales(img1: Image.Image, img2: Image.Image, revisar_espejo: bool = True) -> bool:
    try:
        c1 = ImageOps.fit(recortar_espacio_blanco(img1), (64, 64)).convert("L")
        c2 = ImageOps.fit(recortar_espacio_blanco(img2), (64, 64)).convert("L")
        if ImageStat.Stat(ImageChops.difference(c1, c2)).mean[0] < 11.0:
            return True
        if revisar_espejo:
            c2_flip = c2.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
            if ImageStat.Stat(ImageChops.difference(c1, c2_flip)).mean[0] < 11.0:
                return True
        return False
    except (ValueError, OSError):
        return False


def tiene_lupa_o_rotacion_vieja(img: Image.Image) -> bool:
    try:
        if img.size != (1200, 1200):
            return False
        rgb = img.convert("RGB")
        p1, p2 = rgb.getpixel((970, 385)), rgb.getpixel((970, 53))
        return (p1[0] < 45 and p1[1] < 50 and p1[2] < 70) and (p2[0] < 45 and p2[1] < 50 and p2[2] < 70)
    except (ValueError, OSError, IndexError):
        return False


# ======================================================================================
# TALLAS Y BASE DE DATOS
# ======================================================================================
_ORDEN_LETRAS = ["XCH", "XS", "CH", "S", "CH-M", "M", "M-G", "G", "L", "XG", "XL", "2XG", "XXL", "UNI", "UNITALLA"]


def clave_talla(t: str):
    """Orden natural: tallas numéricas de menor a mayor y luego las de letras (CH, M, G…)."""
    try:
        valor = float(t)
        if valor == valor and abs(valor) != float("inf"):  # descarta "nan"/"inf"
            return (0, valor, "")
    except ValueError:
        pass
    return (1, _ORDEN_LETRAS.index(t) if t in _ORDEN_LETRAS else 99, t)


def parsear_tallas_valor(val: Any) -> List[str]:
    if not val:
        return []
    s = str(val).strip()
    if not s or s in ("{}", "[]", "None", "null", "0"):
        return []
    if s.startswith("{") and s.endswith("}"):
        for loader in (json.loads, ast.literal_eval):
            try:
                d = loader(s)
                if isinstance(d, dict):
                    return [str(k).strip() for k, v in d.items() if float(v or 0) > 0]
            except (ValueError, TypeError, SyntaxError):
                pass
    if s.startswith("[") and s.endswith("]"):
        for loader in (json.loads, ast.literal_eval):
            try:
                lst = loader(s)
                if isinstance(lst, list):
                    return [str(x).strip() for x in lst if str(x).strip()]
            except (ValueError, TypeError, SyntaxError):
                pass
    patron_csv = r"^(?:1\d|2\d|3[0-2]|X?CH|M|G|X+G|S|L|XL|UNI)(?:\.[05])?(?:\s*,\s*(?:1\d|2\d|3[0-2]|X?CH|M|G|X+G|S|L|XL|UNI)(?:\.[05])?)+$"
    if re.search(patron_csv, s, re.IGNORECASE):
        return [x.strip() for x in s.split(",") if x.strip()]
    return []


def normalizar_etiqueta_talla(t: str) -> str:
    """'22' → '22.0', '22,5' → '22.5', 'ch' → 'CH' (quita 'MX' y 'CM')."""
    t_str = str(t).upper().replace("MX", "").replace("CM", "").replace(",", ".").strip()
    m = re.match(r"^(\d+)(?:\.(\d+))?$", t_str)
    if m:
        return f"{int(m.group(1))}.5" if m.group(2) in ("5", "50") else f"{int(m.group(1))}.0"
    return t_str


def obtener_unidad_chip(talla_limpia: str, unidad_perfil: str) -> str:
    """
    Si la talla contiene letras (CH, M, G, XG, UNI, UNITALLA), muestra 'TALLA MX'
    para nunca imprimir 'CM MX' debajo de una letra.
    """
    t = str(talla_limpia).strip().upper()
    if unidad_perfil in ("PRESENTACIÓN", "MEDIDA CM"):
        return unidad_perfil
    if re.match(r"^\d+(?:\.\d+)?$", t):
        return unidad_perfil
    return "TALLA MX"


def cargar_catalogo_sqlite() -> Dict[str, Dict[str, Any]]:
    """
    Carga SOLO LECTURA de los productos con `origen_catalogo` asignado (catálogos PDF
    oficiales). Los demás registros se ignoran, pero NO se borran de la base de datos
    (el panel Streamlit los muestra como "Sin Catálogo (Web)").

    Devuelve {id_normalizado: registro}; el registro también queda accesible con el id
    tal como viene en la BD (p. ej. "PS-00123").
    """
    productos: Dict[str, Dict[str, Any]] = {}
    if not RUTA_BD.exists():
        return productos

    conn = sqlite3.connect(RUTA_BD)
    try:
        tablas = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        tallas_por_sku: Dict[str, List[str]] = {}

        for t_nom in tablas:
            try:
                cols = [c[1] for c in conn.execute(f'PRAGMA table_info("{t_nom}")').fetchall()]
                col_id = next((c for c in cols if c.lower() in ("id_ps", "sku", "id_producto")), None)
                if not col_id:
                    continue
                if "talla" in cols:
                    if "stock_vallejo" in cols:
                        cond = " WHERE stock_vallejo > 0 "
                    elif "stock" in cols:
                        cond = " WHERE stock > 0 "
                    elif "existencias" in cols:
                        cond = " WHERE existencias > 0 "
                    else:
                        cond = ""
                    for r_id, r_t in conn.execute(f'SELECT "{col_id}", talla FROM "{t_nom}"{cond}').fetchall():
                        lista = tallas_por_sku.setdefault(normalizar_id(r_id), [])
                        t_norm = normalizar_etiqueta_talla(str(r_t))
                        if t_norm and t_norm not in lista:
                            lista.append(t_norm)
                cols_j = [
                    c for c in cols
                    if any(p in c.lower() for p in ("talla", "corrida", "stock_json", "inventario")) and c.lower() != "talla"
                ]
                if cols_j:
                    seleccion = ", ".join(f'"{c}"' for c in cols_j)
                    for fila_j in conn.execute(f'SELECT "{col_id}", {seleccion} FROM "{t_nom}"').fetchall():
                        k = normalizar_id(fila_j[0])
                        for val_col in fila_j[1:]:
                            ext = [normalizar_etiqueta_talla(x) for x in parsear_tallas_valor(val_col)]
                            if ext:
                                tallas_por_sku[k] = ext
            except sqlite3.Error as e:
                print(f"⚠️ No se pudieron leer tallas de la tabla '{t_nom}': {e}")

        if "zapatos_candidatos" not in tablas:
            return productos
        cols_zc = [c[1] for c in conn.execute("PRAGMA table_info(zapatos_candidatos)").fetchall()]
        if "origen_catalogo" not in cols_zc:
            print("⚠️ `zapatos_candidatos` no tiene la columna `origen_catalogo`: no hay productos de catálogo PDF.")
            return productos

        c_marca = "marca_extraida" if "marca_extraida" in cols_zc else "''"
        c_corte = "material_corte" if "material_corte" in cols_zc else "''"
        c_stock = "stock_total_anterior" if "stock_total_anterior" in cols_zc else "0"
        filas = conn.execute(
            f"SELECT id_ps, COALESCE(nombre_modelo,''), COALESCE(costo_socio,0.0), COALESCE(url_real,''), "
            f"COALESCE({c_marca},''), COALESCE({c_corte},''), COALESCE(origen_catalogo,''), COALESCE({c_stock},0) "
            f"FROM zapatos_candidatos"
        ).fetchall()
    finally:
        conn.close()

    for id_ps, nombre, costo, url_r, marca, corte, origen, stock in filas:
        origen = str(origen).strip()
        if origen.lower() in ORIGENES_NO_PDF:  # se filtra en Python: SQLite LOWER() no maneja acentos
            continue
        id_raw = str(id_ps).strip()
        id_l = normalizar_id(id_raw)
        reg = {
            "id_ps": id_raw,
            "id_limpio": id_l,
            "nombre": limpiar_titulo(nombre or "Calzado Selección Oficial"),
            "costo": a_float(costo),
            "url_real": str(url_r).strip(),
            "marca": str(marca).strip() or "CATÁLOGO OFICIAL",
            "corte": str(corte).strip(),
            "origen_catalogo": origen,
            "stock": max(0, int(a_float(stock))),
            "tallas_lista": sorted(tallas_por_sku.get(id_l, []), key=clave_talla),
        }
        productos[id_l] = reg
        productos[id_raw] = reg
    return productos


# ======================================================================================
# PERFILES POR CATEGORÍA
# ======================================================================================
PERFILES_CATALOGO: Dict[str, Dict[str, Any]] = {
    "plantilla_cuidado": {
        "tipo": "plantilla_cuidado",
        "unidad_chip": "TALLA MX",
        "subtitulo_03": "PRODUCTO 100% NUEVO Y ORIGINAL  ·  GARANTÍA DIRECTA",
        "badge_izq_inf": "DISEÑO ANATÓMICO, SUAVE Y RESISTENTE",
        "beneficios": [
            ("PRODUCTO 100% NUEVO Y ORIGINAL", "Artículo original en su empaque de fábrica, revisado cuidadosamente antes de enviarse."),
            ("EMPAQUE DE ALTA PROTECCIÓN", "Protegido cuidadosamente con material de alta resistencia para un traslado impecable."),
            ("CONFORT Y AMORTIGUACIÓN DIARIA", "Diseño anatómico que absorbe el impacto al caminar, brindando descanso y frescura en cada paso."),
            ("COMPRA 100% PROTEGIDA", "Aprobada la transacción, tu pedido se procesa de inmediato, seguro y respaldado por la plataforma."),
        ],
        "titulo_04": "GUÍA DE TALLAS Y AJUSTE · PLANTILLAS Y ACCESORIOS",
        "subtitulo_04": "TABLA DE EQUIVALENCIAS EN CENTÍMETROS (MX)  ·  FORMATO AJUSTABLE",
        "columnas_04": ("TALLA / FORMATO", "RANGO CALZADO MX", "LARGO EN (CM)"),
        "filas_izq": [
            ("INFANTIL / JR", "15.0 al 21.5 MX", "15.0 a 21.5 cm"),
            ("TALLA CH (S)", "22.0 al 24.5 MX", "22.0 a 24.5 cm"),
            ("TALLA M (MED)", "25.0 al 27.0 MX", "25.0 a 27.0 cm"),
            ("TALLA G (L)", "27.5 al 30.0 MX", "27.5 a 30.0 cm"),
        ],
        "filas_der": [
            ("DAMA ESTÁNDAR", "22.0 al 26.0 MX", "Recortable 22-26 cm"),
            ("CABALLERO EST.", "25.0 al 29.5 MX", "Recortable 25-29.5 cm"),
            ("TALONERA / GEL", "Ajuste Universal", "Anatómico Estándar"),
            ("UNITALLA (UNI)", "22.0 al 29.0 MX", "Guía de Corte Impresa"),
        ],
        "tallas_default": ["CH", "M", "G"],
        "pasos_04": [
            ("REVISA TU TALLA MX", "Verifica el número mexicano (CM) del calzado donde colocarás tu plantilla o accesorio."),
            ("AJUSTE RECORTABLE", "Si tu plantilla incluye líneas guía, recorta la punta con tijeras siguiendo tu talla exacta."),
            ("COLOCACIÓN LIMPIA", "Retira la plantilla anterior o limpia el interior del calzado antes de insertar la nueva."),
        ],
    },
    "perfumeria": {
        "tipo": "perfumeria",
        "unidad_chip": "PRESENTACIÓN",
        "subtitulo_03": "PERFUMERÍA 100% NUEVA Y ORIGINAL  ·  SELLADA DE FÁBRICA",
        "badge_izq_inf": "AROMA DE ALTA FIJACIÓN Y ORIGEN OFICIAL",
        "beneficios": [
            ("FRAGANCIA 100% NUEVA Y ORIGINAL", "Producto estrictamente original en su caja sellada con celofán de fábrica."),
            ("EMPAQUE ANTIGOLPES REFORZADO", "Protección especial con burbuja amortiguante para un traslado 100% seguro."),
            ("EXCELENTE FIJACIÓN Y ESTELA", "Fórmula original verificada por lote para garantizar su duración en piel."),
            ("COMPRA 100% PROTEGIDA", "Aprobada la transacción, tu pedido se procesa con respaldo total."),
        ],
        "titulo_04": "GUÍA DE PRESENTACIONES Y FIJACIÓN · PERFUMERÍA",
        "subtitulo_04": "TABLA DE EQUIVALENCIAS EN ML / OZ  ·  CONCENTRACIÓN OFICIAL",
        "columnas_04": ("PRESENTACIÓN", "EQUIVALENCIA", "TIPO / DURACIÓN"),
        "filas_izq": [
            ("30 ML", "1.0 FL. OZ.", "Bolsillo / Viaje"),
            ("50 ML", "1.7 FL. OZ.", "Estándar Compacto"),
            ("75 ML", "2.5 FL. OZ.", "Clásico Mediano"),
            ("100 ML", "3.4 FL. OZ.", "Tamaño Estándar"),
        ],
        "filas_der": [
            ("125 ML", "4.2 FL. OZ.", "Formato Grande"),
            ("200 ML", "6.7 FL. OZ.", "Edición Familiar"),
            ("EDT", "Eau de Toilette", "Fijación 5 a 7 hrs"),
            ("EDP", "Eau de Parfum", "Fijación 8 a 12 hrs"),
        ],
        "tallas_default": ["100 ML", "ORIGINAL", "SELLADO"],
        "pasos_04": [
            ("PIEL HIDRATADA", "Aplica la fragancia sobre la piel limpia e hidratada para prolongar su fijación."),
            ("PUNTOS DE PULSO", "Atomiza a 15 cm en muñecas, cuello y detrás de las orejas sin frotar."),
            ("CONSERVACIÓN", "Guarda el frasco en un lugar fresco y alejado de la luz solar directa."),
        ],
    },
    "hogar_cocina": {
        "tipo": "hogar_cocina",
        "unidad_chip": "MEDIDA CM",
        "subtitulo_03": "ARTÍCULO PARA EL HOGAR 100% NUEVO  ·  GARANTÍA DIRECTA",
        "badge_izq_inf": "MATERIALES DE ALTA DURABILIDAD Y RESISTENCIA",
        "beneficios": [
            ("ARTÍCULO 100% NUEVO EN CAJA", "Producto revisado pieza por pieza en su empaque original de fábrica."),
            ("DISTRIBUCIÓN UNIFORME Y CALIDAD", "Acabados resistentes diseñados para uso doméstico diario y fácil limpieza."),
            ("EMPAQUE PROTEGIDO DE ENVÍO", "Embalaje reforzado para que recibas tu producto intacto en casa."),
            ("COMPRA 100% PROTEGIDA", "Aprobada la transacción, tu pedido se procesa con respaldo total."),
        ],
        "titulo_04": "GUÍA DE MEDIDAS Y CAPACIDADES · HOGAR Y COCINA",
        "subtitulo_04": "TABLA DE DIMENSIONES EN CENTÍMETROS (CM)  ·  USO DOMÉSTICO",
        "columnas_04": ("DIÁMETRO / TAM.", "CAPACIDAD APROX.", "PORCIONES / USO"),
        "filas_izq": [
            ("20 a 22 CM", "1.5 a 2.0 Litros", "1 a 2 Personas"),
            ("24 a 26 CM", "2.5 a 3.5 Litros", "3 a 4 Personas"),
            ("28 a 30 CM", "4.0 a 5.5 Litros", "4 a 6 Personas"),
            ("32 a 34 CM", "6.0 a 8.0 Litros", "Familiar (6 a 8)"),
        ],
        "filas_der": [
            ("36 a 40 CM", "9.0 a 12 Litros", "Especial / Eventos"),
            ("INDIVIDUAL", "150 x 220 cm", "Cama Individual"),
            ("MATRIMONIAL", "180 x 220 cm", "Cama Matrimonial"),
            ("QUEEN / KING", "220 x 240 cm", "Cama Queen / King"),
        ],
        "tallas_default": ["30 CM", "34 CM", "UNITALLA"],
        "pasos_04": [
            ("VERIFICA EL DIÁMETRO", "En sartenes, comales y arroceras la medida en CM corresponde al diámetro superior."),
            ("CUIDADO INICIAL", "Lava con esponja suave y agua tibia antes del primer uso para proteger el recubrimiento."),
            ("UTENSILIOS ADECUADOS", "Utiliza palas de madera, silicón o nylon para conservar el antiadherente intacto."),
        ],
    },
    "accesorios": {
        "tipo": "accesorios",
        "unidad_chip": "TALLA MX",
        "subtitulo_03": "ACCESORIO 100% NUEVO Y ORIGINAL  ·  GARANTÍA DIRECTA",
        "badge_izq_inf": "DISEÑO CÓMODO, AJUSTABLE Y DE ALTA DURABILIDAD",
        "beneficios": [
            ("ACCESORIO 100% NUEVO Y ORIGINAL", "Artículo con etiquetas y bordados oficiales verificado antes de empacar."),
            ("MATERIALES DE ALTA RESISTENCIA", "Costuras reforzadas y herrajes durables para uso diario continuo."),
            ("EMPAQUE CUIDADOSO", "Protegido para conservar su horma, visera y estructura durante el envío."),
            ("COMPRA 100% PROTEGIDA", "Aprobada la transacción, tu pedido se procesa con respaldo total."),
        ],
        "titulo_04": "GUÍA DE MEDIDAS · GORRAS, BOLSAS Y ACCESORIOS (CM)",
        "subtitulo_04": "TABLA DE EQUIVALENCIAS  ·  AJUSTE ESTÁNDAR NACIONAL",
        "columnas_04": ("TALLA / FORMATO", "MEDIDA EN (CM)", "TIPO DE AJUSTE"),
        "filas_izq": [
            ("GORRA INFANTIL", "50.0 a 54.0 cm", "Broche Ajustable"),
            ("GORRA UNITALLA", "55.0 a 60.0 cm", "Snapback / Strap"),
            ("GORRA M / G", "57.0 a 59.5 cm", "Cerrada Elástica"),
            ("GORRA G / XG", "60.0 a 62.5 cm", "Talla Extendida"),
        ],
        "filas_der": [
            ("CINTURÓN CH/M", "75.0 a 90.0 cm", "Pantalón 28 a 32"),
            ("CINTURÓN G/XG", "92.0 a 108 cm", "Pantalón 34 a 40"),
            ("BOLSA / MOCHILA", "Formato Oficial", "Compartimento Amplio"),
            ("UNITALLA (UNI)", "Medida Estándar", "Ajuste Universal"),
        ],
        "tallas_default": ["UNITALLA", "AJUSTABLE"],
        "pasos_04": [
            ("CONTORNO DE CABEZA", "Para gorras, rodea tu cabeza con cinta métrica 1 cm por encima de cejas y orejas."),
            ("BROCHE SNAPBACK", "Los modelos 9Fifty y Unitalla cuentan con broche trasero regulable a tu medida."),
            ("REVISA MEDIDAS", "Compara tus centímetros con la tabla superior para confirmar un ajuste cómodo."),
        ],
    },
    "calcetin": {
        "tipo": "calcetin",
        "unidad_chip": "TALLA MX",
        "subtitulo_03": "CALCETERÍA 100% NUEVA Y ORIGINAL  ·  GARANTÍA DIRECTA",
        "badge_izq_inf": "TEJIDO TRANSPIRABLE, SUAVE Y ELÁSTICO",
        "beneficios": [
            ("PRENDA 100% NUEVA Y ORIGINAL", "Calcetería en empaque original sellado, verificada antes de ser enviada."),
            ("AJUSTE ELÁSTICO ERGONÓMICO", "Tejido con spandex de memoria que se adapta al pie sin apretar ni deslizarse."),
            ("FRESCURA Y TRANSPIRACIÓN", "Fibras absorbentes con costuras planas en puntera para evitar roces."),
            ("COMPRA 100% PROTEGIDA", "Aprobada la transacción, tu pedido se procesa con respaldo total."),
        ],
        "titulo_04": "GUÍA DE TALLAS · CALCETERÍA Y TINES (MÉXICO)",
        "subtitulo_04": "TABLA COMPLETA DE EQUIVALENCIAS  ·  AJUSTE ELÁSTICO NACIONAL",
        "columnas_04": ("TALLA", "PIE EN (CM)", "CALZADO MX"),
        "filas_izq": [
            ("INFANTIL CH", "13.0 a 15.5 cm", "13 al 15.5 MX"),
            ("INFANTIL M", "16.0 a 18.5 cm", "16 al 18.5 MX"),
            ("ESCOLAR G", "19.0 a 21.5 cm", "19 al 21.5 MX"),
            ("JUVENIL", "22.0 a 23.5 cm", "22 al 23.5 MX"),
        ],
        "filas_der": [
            ("UNITALLA DAMA", "23.0 a 26.0 cm", "23 al 26.0 MX"),
            ("CABALLERO M", "25.0 a 27.0 cm", "25 al 27.0 MX"),
            ("CABALLERO G", "27.5 a 29.5 cm", "27.5 al 29.5 MX"),
            ("CABALLERO XG", "30.0 a 31.5 cm", "30 al 31.5 MX"),
        ],
        "tallas_default": ["UNI DAMA", "23-26", "25-28"],
        "pasos_04": [
            ("VERIFICA TU CALZADO", "Revisa el número mexicano (MX) que usas habitualmente en tus tenis o zapatos."),
            ("UBICA EL RANGO", "Localiza tu número dentro de la columna CALZADO MX de la tabla superior."),
            ("ELIGE CON CONFIANZA", "El tejido elástico se amolda cómodamente a todo el rango indicado."),
        ],
    },
    "calzones": {
        "tipo": "calzones",
        "unidad_chip": "TALLA MX",
        "subtitulo_03": "ROPA INTERIOR 100% NUEVA Y ORIGINAL  ·  HIGIENE GARANTIZADA",
        "badge_izq_inf": "AJUSTE ANATÓMICO, SUAVE Y TRANSPIRABLE",
        "beneficios": [
            ("PRENDA 100% NUEVA Y SELLADA", "Ropa interior estrictamente nueva en su empaque de higiene original."),
            ("ELÁSTICO SUAVE DE ALTA FIRMEZA", "Pretina y tejido de control que sujeta con suavidad sin marcar ni enrollarse."),
            ("FRESCURA TODO EL DÍA", "Puente y fibras transpirables hipoalergénicas para máxima comodidad diaria."),
            ("COMPRA 100% PROTEGIDA", "Envío discreto, seguro y respaldado directamente por la plataforma."),
        ],
        "titulo_04": "GUÍA DE TALLAS · ROPA INTERIOR, PANTIS Y FAJAS (CM)",
        "subtitulo_04": "TABLA COMPLETA DE EQUIVALENCIAS  ·  CINTURA Y CADERA EN CM",
        "columnas_04": ("TALLA MX", "CINTURA (CM)", "CADERA / PANT."),
        "filas_izq": [
            ("XCH (XS)", "64 a 69 cm", "86-90 cm (26-28)"),
            ("CH (S)", "70 a 75 cm", "91-95 cm (28-30)"),
            ("CH / M", "76 a 78 cm", "96-98 cm (31)"),
            ("M (MED)", "79 a 84 cm", "99-103 cm (32-33)"),
        ],
        "filas_der": [
            ("M / G", "85 a 87 cm", "104-106 cm (34)"),
            ("G (L)", "88 a 94 cm", "107-112 cm (36)"),
            ("XG (XL)", "95 a 102 cm", "113-119 cm (38-40)"),
            ("2XG (XXL)", "103 a 110 cm", "120-127 cm (42)"),
        ],
        "tallas_default": ["CH", "M", "G", "XG"],
        "pasos_04": [
            ("MIDE TU CINTURA", "Rodea tu cintura natural con una cinta métrica sin apretar la piel."),
            ("MIDE TU CADERA", "Pasa la cinta por la parte más prominente de los glúteos y cadera."),
            ("COMPARA EN TABLA", "Si estás entre dos medidas, elige la talla superior para mayor confort."),
        ],
    },
    "chamarra_abrigador": {
        "tipo": "chamarra_abrigador",
        "unidad_chip": "TALLA MX",
        "subtitulo_03": "PRENDA ABRIGADORA 100% NUEVA Y ORIGINAL  ·  GARANTÍA DIRECTA",
        "badge_izq_inf": "DISEÑO TÉRMICO, CÓMODO Y DE ALTA DURABILIDAD",
        "beneficios": [
            ("PRENDA 100% NUEVA Y ORIGINAL", "Chamarra, chaleco o sudadera revisada en cierres y costuras antes de empacar."),
            ("PROTECCIÓN Y CONFORT TÉRMICO", "Interior suave y materiales seleccionados para abrigar con total ligereza."),
            ("CORTE MODERNO Y VERSÁTIL", "Horma pensada para llevar cómodamente sobre playeras o camisas."),
            ("COMPRA 100% PROTEGIDA", "Aprobada la transacción, tu pedido se procesa con respaldo total."),
        ],
        "titulo_04": "GUÍA DE TALLAS · CHAMARRAS, SUDADERAS Y ABRIGO (CM)",
        "subtitulo_04": "TABLA COMPLETA DE EQUIVALENCIAS  ·  PECHO, HOMBRO Y LARGO",
        "columnas_04": ("TALLA MX", "PECHO (CM)", "HOMBRO / LARGO"),
        "filas_izq": [
            ("XCH (XS)", "86 a 90 cm", "41 cm / 62 cm"),
            ("CH (S)", "91 a 96 cm", "43 cm / 64 cm"),
            ("CH-M", "97 a 99 cm", "44.5 cm / 65 cm"),
            ("M (MED)", "100 a 105 cm", "46 cm / 67 cm"),
        ],
        "filas_der": [
            ("M-G", "106 a 108 cm", "47.5 cm / 68 cm"),
            ("G (L)", "109 a 114 cm", "49 cm / 70 cm"),
            ("XG (XL)", "115 a 122 cm", "51 cm / 72 cm"),
            ("2XG (XXL)", "123 a 130 cm", "53 cm / 74 cm"),
        ],
        "tallas_default": ["CH", "M", "G", "XG"],
        "pasos_04": [
            ("CONTORNO DE PECHO", "Mide alrededor del pecho con una playera puesta manteniendo la cinta horizontal."),
            ("ESPACIO DE CAPAS", "Si planeas usar suéter grueso debajo de la chamarra, considera elegir media talla más."),
            ("LARGO DE PRENDA", "Verifica el largo desde la base del cuello hasta la cintura o cadera según el corte."),
        ],
    },
    "camisa": {
        "tipo": "camisa",
        "unidad_chip": "TALLA MX",
        "subtitulo_03": "PRENDA 100% NUEVA Y ORIGINAL  ·  CORTE Y CONFECCIÓN OFICIAL",
        "badge_izq_inf": "CORTE ELEGANTE, FRESCO Y DE BUENA CAÍDA",
        "beneficios": [
            ("PRENDA 100% NUEVA Y ORIGINAL", "Camisa con etiquetas originales, revisada en costuras y botones antes de enviar."),
            ("ACABADO DE ALTA PRESENTACIÓN", "Cuello y puños con estructura firme que conservan su porte tras el lavado."),
            ("COMODIDAD Y MOVIMIENTO", "Horma pensada para lucir impecable sin limitar la movilidad de brazos."),
            ("COMPRA 100% PROTEGIDA", "Aprobada la transacción, tu pedido se procesa con respaldo total."),
        ],
        "titulo_04": "GUÍA DE TALLAS · CAMISAS EN CENTÍMETROS (MX)",
        "subtitulo_04": "TABLA COMPLETA DE EQUIVALENCIAS  ·  PECHO, HOMBRO Y LARGO",
        "columnas_04": ("TALLA MX", "PECHO (CM)", "HOMBRO / LARGO"),
        "filas_izq": [
            ("XCH (34)", "88 a 92 cm", "42 cm / 70 cm"),
            ("CH (36)", "93 a 97 cm", "44 cm / 72 cm"),
            ("CH-M (37)", "98 a 100 cm", "45 cm / 73 cm"),
            ("M (38)", "101 a 105 cm", "46 cm / 74 cm"),
        ],
        "filas_der": [
            ("M-G (39)", "106 a 108 cm", "47.5 cm / 75 cm"),
            ("G (40)", "109 a 113 cm", "49 cm / 76 cm"),
            ("XG (42)", "114 a 119 cm", "51 cm / 78 cm"),
            ("2XG (44)", "120 a 126 cm", "53 cm / 80 cm"),
        ],
        "tallas_default": ["CH", "M", "G", "XG"],
        "pasos_04": [
            ("CONTORNO DE PECHO", "Pasa la cinta métrica por debajo de las axilas en la parte más ancha del pecho."),
            ("ANCHO DE HOMBROS", "Mide de costura a costura en la espalda sobre una camisa que te quede bien."),
            ("ELIGE TU TALLA", "Compara tus centímetros en la tabla; si prefieres ajuste holgado, sube media talla."),
        ],
    },
    "blusa": {
        "tipo": "blusa",
        "unidad_chip": "TALLA MX",
        "subtitulo_03": "PRENDA 100% NUEVA Y ORIGINAL  ·  MODA OFICIAL DE CATÁLOGO",
        "badge_izq_inf": "TEJIDO SUAVE, ESTILIZADO Y FAVORECEDOR",
        "beneficios": [
            ("PRENDA 100% NUEVA Y ORIGINAL", "Blusa o top verificado pieza por pieza con acabados y etiquetas de fábrica."),
            ("CAÍDA SUAVE Y FAVORECEDORA", "Confección diseñada para estilizar la silueta con total comodidad."),
            ("COLORES Y TEXTURA DURADERA", "Telas seleccionadas que mantienen su suavidad y tono lavado tras lavado."),
            ("COMPRA 100% PROTEGIDA", "Respaldo directo en tu compra y empaque cuidadoso de alta protección."),
        ],
        "titulo_04": "GUÍA DE TALLAS · BLUSAS, TOPS Y VESTIDOS DAMA (CM)",
        "subtitulo_04": "TABLA COMPLETA DE EQUIVALENCIAS  ·  BUSTO Y CINTURA EN CM",
        "columnas_04": ("TALLA MX", "BUSTO (CM)", "CINTURA / LARGO"),
        "filas_izq": [
            ("XCH (26)", "82 a 85 cm", "64-67 cm / 59 cm"),
            ("CH (28-30)", "86 a 90 cm", "68-72 cm / 61 cm"),
            ("CH-M (31)", "91 a 93 cm", "73-75 cm / 62 cm"),
            ("M (32-34)", "94 a 98 cm", "76-80 cm / 63 cm"),
        ],
        "filas_der": [
            ("M-G (35)", "99 a 101 cm", "81-83 cm / 64 cm"),
            ("G (36-38)", "102 a 107 cm", "84-89 cm / 65 cm"),
            ("XG (40)", "108 a 114 cm", "90-96 cm / 67 cm"),
            ("2XG (42)", "115 a 121 cm", "97-103 cm / 69 cm"),
        ],
        "tallas_default": ["CH", "M", "G", "XG"],
        "pasos_04": [
            ("MIDE TU BUSTO", "Rodea la parte más amplia del busto con una cinta métrica manteniendo la espalda recta."),
            ("MIDE TU CINTURA", "Toma la medida alrededor de la parte más estrecha del torso sin apretar."),
            ("UBICA TU TALLA", "Verifica la equivalencia en número (28 a 42) o letra (CH a 2XG) en la tabla."),
        ],
    },
    "playera": {
        "tipo": "playera",
        "unidad_chip": "TALLA MX",
        "subtitulo_03": "PRENDA 100% NUEVA Y ORIGINAL  ·  GARANTÍA DIRECTA",
        "badge_izq_inf": "TEJIDO TRANSPIRABLE, CÓMODO Y RESISTENTE",
        "beneficios": [
            ("PRENDA 100% NUEVA Y ORIGINAL", "Artículo estrictamente nuevo con etiquetas oficiales, revisado antes de empacar."),
            ("FRESCURA Y SUAVIDAD AL TACTO", "Tejido cómodo y ligero pensado para uso diario sin irritaciones."),
            ("COSTURAS Y COLOR DE ALTA FIJACIÓN", "Mantiene su ajuste y color original tras múltiples ciclos de lavado."),
            ("COMPRA 100% PROTEGIDA", "Aprobada la transacción, tu pedido se procesa con respaldo total."),
        ],
        "titulo_04": "GUÍA DE TALLAS · PLAYERAS, POLOS Y PIJAMAS (CM)",
        "subtitulo_04": "TABLA COMPLETA DE EQUIVALENCIAS  ·  CONTORNO DE PECHO Y LARGO",
        "columnas_04": ("TALLA MX", "PECHO (CM)", "ANCHO / LARGO"),
        "filas_izq": [
            ("XCH (XS)", "84 a 88 cm", "45 cm / 66 cm"),
            ("CH (S)", "89 a 94 cm", "48 cm / 68 cm"),
            ("CH-M", "95 a 97 cm", "50 cm / 69.5 cm"),
            ("M (MED)", "98 a 103 cm", "52 cm / 71 cm"),
        ],
        "filas_der": [
            ("M-G", "104 a 106 cm", "54 cm / 72.5 cm"),
            ("G (L)", "107 a 112 cm", "56 cm / 74 cm"),
            ("XG (XL)", "113 a 119 cm", "60 cm / 76 cm"),
            ("2XG (XXL)", "120 a 127 cm", "64 cm / 78 cm"),
        ],
        "tallas_default": ["CH", "M", "G", "XG"],
        "pasos_04": [
            ("CONTORNO DE PECHO", "Mide alrededor del pecho a la altura de las axilas con la cinta horizontal."),
            ("COMPARA UNA PRENDA", "O extiende tu playera favorita en una mesa y mide de axila a axila (ANCHO)."),
            ("ELIGE TU AJUSTE", "Usa la tabla superior; para un estilo más relajado (oversize) elige una talla más."),
        ],
    },
    "pantalon": {
        "tipo": "pantalon",
        "unidad_chip": "TALLA MX",
        "subtitulo_03": "PRENDA 100% NUEVA Y ORIGINAL  ·  GARANTÍA DIRECTA",
        "badge_izq_inf": "CORTE ERGONÓMICO, CÓMODO Y RESISTENTE",
        "beneficios": [
            ("PRENDA 100% NUEVA Y ORIGINAL", "Revisada en cierres, botones y costuras antes de ser empaquetada."),
            ("AJUSTE FAVORECEDOR Y CÓMODO", "Horma nacional que brinda libertad de movimiento durante todo el día."),
            ("TELA DE ALTA RESISTENCIA", "Tejido duradero que conserva su forma sin aflojarse con el uso."),
            ("COMPRA 100% PROTEGIDA", "Aprobada la transacción, tu pedido se procesa con respaldo total."),
        ],
        "titulo_04": "GUÍA DE TALLAS · PANTALONES Y JEANS EN CM (MX)",
        "subtitulo_04": "TABLA COMPLETA DE EQUIVALENCIAS  ·  CINTURA Y CADERA EN CM",
        "columnas_04": ("TALLA MX", "CINTURA (CM)", "CADERA (CM)"),
        "filas_izq": [
            ("28 (5 Dama)", "68 a 71 cm", "90 a 94 cm"),
            ("30 (7 Dama)", "72 a 75 cm", "95 a 98 cm"),
            ("31 (9 Dama)", "76 a 78 cm", "99 a 101 cm"),
            ("32 (11 Dama)", "79 a 82 cm", "102 a 105 cm"),
        ],
        "filas_der": [
            ("33 (12 Dama)", "83 a 85 cm", "106 a 108 cm"),
            ("34 (13 Dama)", "86 a 89 cm", "109 a 112 cm"),
            ("36 (15 Dama)", "90 a 94 cm", "113 a 117 cm"),
            ("38 (17 Dama)", "95 a 100 cm", "118 a 123 cm"),
        ],
        "tallas_default": ["28", "30", "32", "34"],
        "pasos_04": [
            ("MIDE TU CINTURA", "Rodea tu cintura a la altura donde sueles usar el pantalón con cinta métrica."),
            ("MIDE TU CADERA", "Toma el contorno de la parte más amplia de tu cadera con los pies juntos."),
            ("REVISA EQUIVALENCIA", "Ubica tu medida en centímetros para elegir tu talla exacta en México."),
        ],
    },
    "calzado_infantil": {
        "tipo": "calzado_infantil",
        "unidad_chip": "CM MX",
        "subtitulo_03": "CALZADO 100% NUEVO Y ORIGINAL  ·  GARANTÍA DIRECTA",
        "badge_izq_inf": "DISEÑO CÓMODO, LIGERO Y RESISTENTE",
        "beneficios": [
            ("CALZADO 100% NUEVO Y ORIGINAL", "Calzado de calidad estrictamente nueva, verificado par por par antes de ser empaquetado."),
            ("EMPAQUE DE ALTA PROTECCIÓN", "Protegido cuidadosamente con material de alta resistencia para un traslado impecable."),
            ("MÁXIMA COMODIDAD DIARIA", "Plantilla suave y suela antiderrapante diseñada para brindar confort en el colegio y recreo."),
            ("COMPRA 100% PROTEGIDA", "Aprobada la transacción, tu pedido se procesa de inmediato, seguro y respaldado por la plataforma."),
        ],
        "titulo_04": "GUÍA DE TALLAS EN CENTÍMETROS · INFANTIL / ESCOLAR",
        "subtitulo_04": "TABLA COMPLETA CON MEDIOS NÚMEROS  ·  HORMA NACIONAL EXACTA",
        "columnas_04": ("TALLA MX", "TU PIE (CM)", "PLANTILLA"),
        "filas_izq": [
            ("15.0 MX", "14.7 a 15.0 cm", "15.3 cm"),
            ("15.5 MX", "15.1 a 15.5 cm", "15.8 cm"),
            ("16.0 MX", "15.7 a 16.0 cm", "16.3 cm"),
            ("16.5 MX", "16.1 a 16.5 cm", "16.8 cm"),
            ("17.0 MX", "16.7 a 17.0 cm", "17.3 cm"),
            ("17.5 MX", "17.1 a 17.5 cm", "17.8 cm"),
            ("18.0 MX", "17.7 a 18.0 cm", "18.3 cm"),
            ("18.5 MX", "18.1 a 18.5 cm", "18.8 cm"),
        ],
        "filas_der": [
            ("19.0 MX", "18.7 a 19.0 cm", "19.3 cm"),
            ("19.5 MX", "19.1 a 19.5 cm", "19.8 cm"),
            ("20.0 MX", "19.7 a 20.0 cm", "20.3 cm"),
            ("20.5 MX", "20.1 a 20.5 cm", "20.8 cm"),
            ("21.0 MX", "20.7 a 21.0 cm", "21.3 cm"),
            ("21.5 MX", "21.1 a 21.5 cm", "21.8 cm"),
            ("22.0 MX", "21.7 a 22.0 cm", "22.3 cm"),
            ("22.5 MX", "22.1 a 22.5 cm", "22.8 cm"),
        ],
        "tallas_default": ["18.0", "19.0", "20.0", "21.0"],
        "pasos_04": [
            ("APOYA EL TALÓN", "Coloca una hoja de papel en el piso pegada a la pared y pon el pie con el talón tocando la pared."),
            ("HAZ UNA MARCA", "Con un lápiz vertical, traza una pequeña línea justo donde termina el dedo más largo."),
            ("MIDE CON REGLA", "Mide la distancia del borde de la hoja a la marca: esos centímetros son tu talla MX."),
        ],
    },
    "calzado_adulto": {
        "tipo": "calzado_adulto",
        "unidad_chip": "CM MX",
        "subtitulo_03": "CALZADO 100% NUEVO Y ORIGINAL  ·  GARANTÍA DIRECTA",
        "badge_izq_inf": "DISEÑO CÓMODO, LIGERO Y RESISTENTE",
        "beneficios": [
            ("CALZADO 100% NUEVO Y ORIGINAL", "Calzado de calidad estrictamente nueva, verificado par por par antes de ser empaquetado."),
            ("EMPAQUE DE ALTA PROTECCIÓN", "Protegido cuidadosamente con material de alta resistencia para un traslado impecable."),
            ("MÁXIMA COMODIDAD DIARIA", "Plantilla suave y horma ergonómica diseñada para brindar confort por largas jornadas."),
            ("COMPRA 100% PROTEGIDA", "Aprobada la transacción, tu pedido se procesa de inmediato, seguro y respaldado por la plataforma."),
        ],
        "titulo_04": "GUÍA DE TALLAS EN CENTÍMETROS (MÉXICO)",
        "subtitulo_04": "TABLA COMPLETA DE EQUIVALENCIAS  ·  HORMA NACIONAL EXACTA",
        "columnas_04": ("TALLA MX", "TU PIE (CM)", "PLANTILLA"),
        "filas_izq": [
            ("21.5 MX", "21.2 a 21.5 cm", "21.8 cm"),
            ("22.0 MX", "21.7 a 22.0 cm", "22.3 cm"),
            ("22.5 MX", "22.1 a 22.5 cm", "22.8 cm"),
            ("23.0 MX", "22.6 a 23.0 cm", "23.3 cm"),
            ("23.5 MX", "23.1 a 23.5 cm", "23.8 cm"),
            ("24.0 MX", "23.6 a 24.0 cm", "24.3 cm"),
            ("24.5 MX", "24.1 a 24.5 cm", "24.8 cm"),
            ("25.0 MX", "24.6 a 25.0 cm", "25.3 cm"),
        ],
        "filas_der": [
            ("25.5 MX", "25.1 a 25.5 cm", "25.8 cm"),
            ("26.0 MX", "25.6 a 26.0 cm", "26.3 cm"),
            ("26.5 MX", "26.1 a 26.5 cm", "26.8 cm"),
            ("27.0 MX", "26.6 a 27.0 cm", "27.3 cm"),
            ("27.5 MX", "27.1 a 27.5 cm", "27.8 cm"),
            ("28.0 MX", "27.6 a 28.0 cm", "28.3 cm"),
            ("28.5 MX", "28.1 a 28.5 cm", "28.8 cm"),
            ("29.0 MX", "28.6 a 29.0 cm", "29.3 cm"),
        ],
        "tallas_default": ["23.5", "24.5", "25.0"],
        "pasos_04": [
            ("APOYA EL TALÓN", "Coloca una hoja de papel en el piso pegada a la pared y pon tu pie con el talón tocando la pared."),
            ("HAZ UNA MARCA", "Con un lápiz vertical, traza una pequeña línea justo donde termina tu dedo más largo."),
            ("MIDE CON REGLA", "Mide la distancia del borde de la hoja a la marca: esos centímetros son tu talla MX."),
        ],
    },
}


# ======================================================================================
# CLASIFICADOR DE CATEGORÍAS
# ======================================================================================
# Artículos de cuidado que SIEMPRE son plantilla/accesorio de calzado.
RE_PLANTILLA_CUIDADO = re.compile(
    r"\b(plantilla|plantillas|talonera|taloneras|calzador|calzadores|boleador|"
    r"cera\s+para|esponja\s+lustradora|protector\s+de\s+tal[oó]n|gel\s+ortop[eé]dico|"
    r"horm(?:a|as)\s+(?:para|de)\s+(?:zapato|zapatos|calzado|bota|botas|tenis))\b",
    re.I,
)
# Palabras ambiguas ("tenis con cordones", "sudadera con cordón", "bota con agujetas"):
# solo indican cuidado de calzado si el título NO menciona calzado ni prendas.
RE_CUIDADO_AMBIGUO = re.compile(
    r"\b(agujeta|agujetas|cord[oó]n|cordones|almohadilla|almohadillas)\b", re.I
)
RE_CALZADO = re.compile(
    r"\b(zapato|zapatos|tenis|mocas[ií]n|mocasines|choclo|choclos|top\s*sider|bot[ií]n|botines|bota|botas|"
    r"sandalia|sandalias|zapatilla|zapatillas|loafer|hiker|calzado|chancla|chanclas|huarache|huaraches|"
    r"pantufla|pantuflas|balerina|balerinas|cu[ñn]a|cu\s+a)\b",
    re.I,
)
RE_NO_CALZADO = re.compile(
    r"\b(pantal[oó]n|pantalones|jeans|mezclilla|calcet[ií]n|calcetines|calceta|calcetas|tines?|medias|"
    r"mochila|bolsa|gorra|playera|camisa|chamarra|sudadera|short|shorts|legging|leggings|perfume|"
    r"plantilla|plantillas|talonera|agujeta|agujetas|calzador)\b",
    re.I,
)
RE_INFANTIL_CALZADO = re.compile(
    r"\b(escolar|ni[ñn][oa]s?|infantil|beb[eé]|junior|jr|kids|bluey|spiderman|paw\s*patrol|kuromi|sonic|mickey|minnie|"
    r"stitch|avengers|disney|luces|colegial|k\s*pop|woody|frozen|barbie|batman|mario|pokemon|peppa|princesas|cars|"
    r"toy\s*story|hello\s*kitty|minions)\b",
    re.I,
)
# Un calzado es infantil por TALLAS solo si TODAS las tallas en stock son menores a esto
# (la 22.0 ya es talla de dama: un zapato adulto con solo la 22 en stock no es infantil).
LIMITE_TALLA_INFANTIL = 22.0


def detectar_perfil_prenda(
    nombre: str, marca: str, corte: str, tallas_bd: List[str], origen_catalogo: str = ""
) -> Dict[str, Any]:
    texto_nombre = (nombre or "").lower().strip()
    texto = f"{nombre} {marca} {corte} {origen_catalogo}".lower()
    tallas_num = [
        float(m.group(1))
        for t in tallas_bd
        if (m := re.match(r"^(\d+(?:\.\d+)?)$", str(t).strip())) and 10.0 <= float(m.group(1)) <= 33.0
    ]

    # 0. Plantillas, taloneras y cuidado del calzado (prioridad sobre catálogos de calzado)
    if RE_PLANTILLA_CUIDADO.search(texto_nombre):
        return PERFILES_CATALOGO["plantilla_cuidado"]
    if (
        RE_CUIDADO_AMBIGUO.search(texto_nombre)
        and not RE_CALZADO.search(texto_nombre)
        and not RE_NO_CALZADO.search(texto_nombre)
    ):
        return PERFILES_CATALOGO["plantilla_cuidado"]

    es_infantil = bool(RE_INFANTIL_CALZADO.search(texto)) or bool(tallas_num and max(tallas_num) < LIMITE_TALLA_INFANTIL)

    # 1. Calzado explícito en el título (y que no sea prenda/accesorio/plantilla)
    if RE_CALZADO.search(texto_nombre) and not RE_NO_CALZADO.search(texto_nombre):
        return PERFILES_CATALOGO["calzado_infantil"] if es_infantil else PERFILES_CATALOGO["calzado_adulto"]

    # 2. Perfumería
    if re.search(
        r"\b(perfume|perfumes|fragancia|fragancias|locion|loci[oó]n|eau\s*de|colonia|body\s*mist|desodorante|cosmetico|cosm[eé]tico|maquillaje|labial|crema\s*corporal|shampoo|limpiador)\b",
        texto,
    ):
        return PERFILES_CATALOGO["perfumeria"]

    # 3. Hogar y cocina
    if re.search(
        r"\b(arrocera|comal|sart[eé]n|sartenes|olla|ollas|cacerola|bater[ií]a\s*de\s*cocina|licuadora|cafetera|termo|vajilla|utensilio|cobertor|edred[oó]n|s[aá]bana|sabanas|frazada|almohada|coj[ií]n|toalla|mantel|cortina|tapete|hogar)\b",
        texto,
    ):
        return PERFILES_CATALOGO["hogar_cocina"]

    # 4. Gorras, bolsas y accesorios
    if re.search(
        r"\b(gorra|gorras|gorro|sombrero|visera|snapback|mochila|mochilas|bolsa|bolsas|bolso|cartera|cangurera|mariconera|maleta|lonchera|lapicera|cintur[oó]n|lentes|gafas|reloj|paraguas|bufanda|guantes)\b",
        texto,
    ):
        return PERFILES_CATALOGO["accesorios"]

    # 5. Calcetería
    if re.search(
        r"\b(calcet[ií]n|calcetines|calceta|calcetas|tin|tines|medias|tobimedias?|media(?!\s+(?:manga|ca[ñn]a|suela|tac[oó]n)))\b",
        texto,
    ):
        return PERFILES_CATALOGO["calcetin"]

    # 6. Ropa interior
    if re.search(
        r"\b(calz[oó]n|calzones|boxer|b[oó]xer|boxers|b[oó]xers|trusa|trusas|bikini|bikinis|tanga|tangas|cachetero|cacheteros|hipkini|panty|panti|pantis|pantys|faja|fajas|control\s*abdomen|body\s*control|ropa\s*interior|lencer[ií]a|brasier|bra|bralette|top\s*interior|corpi[ñn]o|camiseta\s*interior)\b",
        texto,
    ):
        return PERFILES_CATALOGO["calzones"]

    # 7. Chamarras y abrigadores
    if re.search(
        r"\b(chamarra|chamarras|chaleco|chalecos|abrigo|abrigos|capa|capas|poncho|gabardina|rompevientos|parka|bomber|torera|saco|blazer|cardigan|c[aá]rdigan|su[eé]ter|sueteres|sudadera|sudaderas|hoodie|polar|abrigador)\b",
        texto,
    ):
        return PERFILES_CATALOGO["chamarra_abrigador"]

    # 8. Camisas
    if re.search(r"\b(camisa|camisas|guayabera|guayaberas|sobrecamisa)\b", texto):
        return PERFILES_CATALOGO["camisa"]

    # 9. Blusas y vestidos
    if re.search(
        r"\b(blusa|blusas|blus[oó]n|camisera\s*dama|crop\s*top|top\s*seamless|top\s*deportivo|top\s*dama|vestido|vestidos|enterizo|jumpsuit|palazzo)\b",
        texto,
    ):
        return PERFILES_CATALOGO["blusa"]

    # 10. Playeras, polos y pijamas
    if re.search(
        r"\b(playera|playeras|polo|t-shirt|jersey|pijama|pijamas|lounge|camis[oó]n|camiseta|camisetas|tank\s*top|esqueleto|bata|conjunto)\b",
        texto,
    ):
        return PERFILES_CATALOGO["playera"]

    # 11. Pantalones y similares
    if re.search(
        r"\b(pantal[oó]n|pantalones|jeans|mezclilla|skinny|superskinny|short|shorts|bermuda|bermudas|falda|faldas|legging|leggings|mall[oó]n|jogger|joggers|pants|capri)\b",
        texto,
    ):
        return PERFILES_CATALOGO["pantalon"]

    # 12. Calzado infantil o adulto por defecto
    return PERFILES_CATALOGO["calzado_infantil"] if es_infantil else PERFILES_CATALOGO["calzado_adulto"]


# ======================================================================================
# DESCARGA DE FOTOS
# ======================================================================================
ERRORES_IMAGEN = (OSError, ValueError, Image.DecompressionBombError)


def http_get_bytes(url: str, timeout: int = 14, con_texto: bool = True) -> Tuple[int, bytes, str]:
    """GET con huella TLS de navegador (si hay curl_cffi) y respaldo con urllib.

    Un 404/410 es definitivo: no se reintenta con otros navegadores ni con urllib.
    """
    if tls_requests is not None:
        for nav in ("chrome120", "chrome110", "safari15_5"):
            try:
                r = tls_requests.get(url, impersonate=nav, timeout=timeout)
            except Exception:  # curl_cffi lanza varios tipos de error de red
                continue
            if r.status_code == 200:
                return r.status_code, r.content, (r.text if con_texto else "")
            if r.status_code in (404, 410):
                return r.status_code, b"", ""
    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/122.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,image/avif,image/webp,*/*;q=0.8",
            },
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            return getattr(resp, "status", 200), raw, (raw.decode("utf-8", errors="ignore") if con_texto else "")
    except Exception:  # urllib: HTTPError, URLError, timeout, SSL…
        return 0, b"", ""


def generar_variantes_cdn_segunda_foto(url_primera: str, id_limpio: str) -> List[str]:
    variantes: List[str] = []
    if not url_primera:
        return variantes
    base, sep, query = url_primera.partition("?")
    suf_q = f"{sep}{query}" if sep else ""
    id_re = re.escape(id_limpio)
    reemplazos = [
        (rf"({id_re})([_\-])1(\.[a-zA-Z]+)$", r"\1\g<2>2\3"),
        (rf"({id_re})([_\-])1(\.[a-zA-Z]+)$", r"\1\g<2>3\3"),
        (rf"({id_re})([_\-])01(\.[a-zA-Z]+)$", r"\1\g<2>02\3"),
        (rf"({id_re})([_\-]?)[aA](\.[a-zA-Z]+)$", r"\1\g<2>b\3"),
        (rf"({id_re})(\.[a-zA-Z]+)$", r"\1_2\2"),
        (rf"({id_re})(\.[a-zA-Z]+)$", r"\1-2\2"),
        (r"([_\-])1(\.[a-zA-Z]+)$", r"\g<1>2\2"),
        (r"([_\-])01(\.[a-zA-Z]+)$", r"\g<1>02\2"),
    ]
    for pat, sust in reemplazos:
        nueva = re.sub(pat, sust, base)
        if nueva != base and f"{nueva}{suf_q}" not in variantes:
            variantes.append(f"{nueva}{suf_q}")
    return variantes


def extraer_urls_galeria_html(html: str, id_limpio: str) -> List[str]:
    candidatas: List[str] = []
    id_re = re.escape(id_limpio)

    def agregar(u: str) -> None:
        ul = u.strip().replace("\\/", "/").replace("\\u002F", "/").replace("&amp;", "&")
        if not ul.startswith("http"):
            return
        low = ul.lower()
        if any(b in low for b in ("logo", "icon", "avatar", "banner", "placeholder", ".svg", "favicon", "spinner", "thumb_")):
            return
        if re.search(r"(?<!\d)(?:50|100)x(?:\d|\b)", low):  # miniaturas reales, no 750x750 / 1100x1100
            return
        if ul not in candidatas:
            candidatas.append(ul)

    for b_ld in re.findall(r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html, re.I | re.S):
        for m in re.findall(r'https?:\\?/\\?/[^"\'\s<>]+?\.(?:jpg|jpeg|png|webp)(?:\?[^"\'\s<>]*)?', b_ld, re.I):
            agregar(m)

    m_next = re.search(r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>', html, re.I | re.S)
    if m_next:
        for m in re.findall(r'https?:\\?/\\?/[^"\'\s<>]+?\.(?:jpg|jpeg|png|webp)(?:\?[^"\'\s<>]*)?', m_next.group(1), re.I):
            agregar(m)

    for m_og in re.findall(r"<meta[^>]+og:image[^>]+>", html, re.I):
        if m_c := re.search(r'content=["\'](https?://[^"\']+)["\']', m_og, re.I):
            agregar(m_c.group(1))

    for m_id in re.findall(
        rf'https?:\\?/\\?/[^"\'\s<>]*?{id_re}[^"\'\s<>]*?\.(?:jpg|jpeg|png|webp)(?:\?[^"\'\s<>]*)?', html, re.I
    ):
        agregar(m_id)

    if candidatas:
        dir_cdn = candidatas[0].rsplit("/", 1)[0]
        if len(dir_cdn) > 25:
            for m_dir in re.findall(
                re.escape(dir_cdn) + r'/[^"\'\s<>]+?\.(?:jpg|jpeg|png|webp)(?:\?[^"\'\s<>]*)?', html, re.I
            ):
                agregar(m_dir)

    for u_base in list(candidatas[:3]):
        for u_var in generar_variantes_cdn_segunda_foto(u_base, id_limpio):
            agregar(u_var)

    return candidatas


def _bajar_imagen(url: str) -> Optional[Image.Image]:
    st, raw, _ = http_get_bytes(url, timeout=12, con_texto=False)
    if st != 200 or len(raw) <= 4000:
        return None
    try:
        return Image.open(BytesIO(raw)).convert("RGB")
    except ERRORES_IMAGEN:
        return None


def descargar_fotos_oficiales_ps(
    id_limpio: str, url_real: str, img_01_existente: Optional[Image.Image] = None
) -> List[Image.Image]:
    """Devuelve hasta 2 fotos (la 1.ª es `img_01_existente` si se dio). Pausa aleatoria anti-bloqueo."""
    imagenes: List[Image.Image] = [img_01_existente] if img_01_existente is not None else []
    urls_vistas: set[str] = set()
    url_ficha = url_real.strip()
    id_re = re.escape(id_limpio)

    def procesar_html(html: str) -> bool:
        for u_img in extraer_urls_galeria_html(html, id_limpio):
            if u_img in urls_vistas:
                continue
            urls_vistas.add(u_img)
            im = _bajar_imagen(u_img)
            if im is not None and not any(son_imagenes_iguales(im, ex, revisar_espejo=False) for ex in imagenes):
                imagenes.append(im)
            if len(imagenes) >= 2:
                return True
        return False

    try:
        time.sleep(random.uniform(1.8, 3.2))
        if not url_ficha or "/productos/" not in url_ficha:
            st_b, _, html_b = http_get_bytes(f"https://www.priceshoes.com/buscar?q={id_limpio}", timeout=15)
            if st_b == 200 and html_b:
                m_prod = re.search(rf'https://www\.priceshoes\.com/productos/[a-zA-Z0-9\-]*{id_re}[a-zA-Z0-9\-]*', html_b)
                m_rel = re.search(rf'href=["\'](/productos/[a-zA-Z0-9\-]*{id_re}[a-zA-Z0-9\-]*)["\']', html_b)
                if m_prod:
                    url_ficha = m_prod.group(0)
                elif m_rel:
                    url_ficha = f"https://www.priceshoes.com{m_rel.group(1)}"
                elif procesar_html(html_b):
                    return imagenes

        if url_ficha:
            st_p, _, html_p = http_get_bytes(url_ficha, timeout=15)
            if st_p == 200 and html_p:
                procesar_html(html_p)
    except Exception as e:  # una ficha rota no debe detener todo el lote
        print(f"   ⚠️ Descarga fallida para {id_limpio}: {e}")
    return imagenes


# ======================================================================================
# LÁMINAS
# ======================================================================================
def tallas_para_mostrar(perfil: Dict[str, Any], tallas_disponibles: List[str]) -> List[str]:
    """Tallas a imprimir en la lámina 03.

    - Con datos reales de stock: esas tallas.
    - Sin datos: solo las presentaciones fijas (perfumería, hogar, accesorios).
      Para calzado/ropa NO se inventan tallas: se devuelve [] (la lámina muestra un aviso).
    """
    if tallas_disponibles:
        return list(tallas_disponibles)
    if perfil["tipo"] in TIPOS_CON_PRESENTACION_FIJA:
        return list(perfil["tallas_default"])
    return []


def crear_lamina_01_portada_blanca(img_recortada: Image.Image) -> Image.Image:
    lienzo = Image.new("RGB", (1200, 1200), (255, 255, 255))
    mejorada = ImageEnhance.Sharpness(img_recortada).enhance(1.18)
    prod = ImageOps.contain(mejorada, (1080, 1020), Image.Resampling.LANCZOS)
    lienzo.paste(prod, ((1200 - prod.width) // 2, (1200 - prod.height) // 2))
    return lienzo


def crear_lamina_02_angulo_galeria(
    img_01_recortada: Image.Image, img_02_recortada: Optional[Image.Image]
) -> Image.Image:
    lienzo = Image.new("RGB", (1200, 1200), (255, 255, 255))
    if img_02_recortada is not None:
        mejorada = ImageEnhance.Sharpness(img_02_recortada).enhance(1.18)
        prod = ImageOps.contain(mejorada, (1080, 1020), Image.Resampling.LANCZOS)
    else:
        mejorada = ImageEnhance.Sharpness(img_01_recortada).enhance(1.22)
        prod = ImageOps.contain(mejorada, (1140, 1080), Image.Resampling.LANCZOS)
    lienzo.paste(prod, ((1200 - prod.width) // 2, (1200 - prod.height) // 2))
    return lienzo


def renderizar_lamina_03_ficha_beneficios(
    img_01_recortada: Image.Image, nombre: str, perfil: Dict[str, Any], tallas_disponibles: List[str]
) -> Image.Image:
    lienzo = Image.new("RGB", (1200, 1200), (244, 247, 252))
    draw = ImageDraw.Draw(lienzo)
    f_pill = cargar_fuente(18, True)
    f_chip_s = cargar_fuente(14, True)
    f_info_ancho = 1000

    # Encabezado (los textos largos se acortan con "..." para no invadir la insignia)
    draw.rounded_rectangle([30, 25, 1170, 155], radius=20, fill=(15, 23, 42))
    draw.rounded_rectangle([35, 147, 1165, 155], radius=4, fill=(245, 158, 11))
    titulo = limpiar_titulo(nombre).upper()
    f_tit = ajustar_fuente(draw, titulo, 770, 34, 20, True)
    draw.text((60, 44), recortar_con_puntos(draw, titulo, f_tit, 770), fill=(255, 255, 255), font=f_tit)
    sub = perfil["subtitulo_03"]
    f_sub = ajustar_fuente(draw, sub, 770, 19, 12, True)
    draw.text((60, 96), recortar_con_puntos(draw, sub, f_sub, 770), fill=(148, 163, 184), font=f_sub)
    draw.rounded_rectangle([855, 55, 1140, 115], radius=28, fill=(245, 158, 11))
    texto_centrado(draw, 855, 1140, 74, "CALIDAD GARANTIZADA", f_pill, (15, 23, 42))

    # Panel izquierdo
    draw.rounded_rectangle([30, 180, 540, 765], radius=22, fill=(255, 255, 255), outline=(219, 228, 240), width=2)
    draw.rounded_rectangle([75, 205, 495, 255], radius=24, fill=(220, 252, 231), outline=(134, 239, 172), width=2)
    texto_centrado(draw, 75, 495, 219, "ENVÍO SEGURO DESDE CDMX", f_pill, (21, 128, 61))

    mejorada = ImageEnhance.Sharpness(img_01_recortada).enhance(1.18)
    prod_izq = ImageOps.contain(mejorada, (460, 380), Image.Resampling.LANCZOS)
    lienzo.paste(prod_izq, (30 + (510 - prod_izq.width) // 2, 275 + (400 - prod_izq.height) // 2))

    badge = perfil["badge_izq_inf"]
    draw.rounded_rectangle([55, 690, 515, 742], radius=24, fill=(219, 234, 254), outline=(147, 197, 253), width=2)
    texto_centrado(draw, 55, 515, 705, badge, ajustar_fuente(draw, badge, 420, 18, 12, True), (29, 78, 216))

    # Beneficios
    y_card = 180
    for tit_b, desc_b in perfil["beneficios"]:
        draw.rounded_rectangle([565, y_card, 1170, y_card + 132], radius=18, fill=(255, 255, 255), outline=(219, 228, 240), width=2)
        draw.rounded_rectangle([565, y_card + 10, 577, y_card + 122], radius=6, fill=(37, 99, 235))
        draw.ellipse([596, y_card + 38, 650, y_card + 92], fill=(22, 163, 74))
        draw.line([(611, y_card + 66), (620, y_card + 76), (636, y_card + 55)], fill=(255, 255, 255), width=5)
        draw.text((670, y_card + 16), tit_b, fill=(15, 23, 42), font=ajustar_fuente(draw, tit_b, 480, 22, 14, True))
        f_d, lineas, tam = envolver_ajustado(draw, desc_b, 480, 3, 18, 13)
        for i, lin in enumerate(lineas):
            draw.text((670, y_card + 52 + i * (tam + 6)), lin, fill=(71, 85, 105), font=f_d)
        y_card += 148

    # Tallas: SOLO reales (o presentación fija); nunca se inventa disponibilidad
    tallas_mostrar = tallas_para_mostrar(perfil, tallas_disponibles)
    draw.rounded_rectangle([30, 790, 1170, 1175], radius=22, fill=(255, 255, 255), outline=(219, 228, 240), width=2)
    draw.rounded_rectangle([30, 790, 1170, 852], radius=18, fill=(15, 23, 42))
    draw.rectangle([30, 830, 1170, 852], fill=(15, 23, 42))
    titulo_panel = "TALLAS NACIONALES (MX) DISPONIBLES EN STOCK" if tallas_mostrar else "TALLAS Y DISPONIBILIDAD"
    texto_centrado(draw, 30, 1170, 808, titulo_panel, cargar_fuente(22, True), (255, 255, 255))

    if tallas_mostrar:
        extra = max(0, len(tallas_mostrar) - MAX_CHIPS_03)
        tallas_mostrar = tallas_mostrar[:MAX_CHIPS_03]
        n = len(tallas_mostrar)
        ancho_chip = min(145, max(84, (1040 - (n - 1) * 16) // max(n, 1)))
        x_chip = (1200 - (n * ancho_chip + (n - 1) * 16)) // 2
        y_chip = 882
        for t_val in tallas_mostrar:
            t_clean = normalizar_etiqueta_talla(t_val)
            unidad_chip = obtener_unidad_chip(t_clean, perfil["unidad_chip"])
            draw.rounded_rectangle([x_chip + 2, y_chip + 4, x_chip + ancho_chip + 2, y_chip + 184], radius=16, fill=(191, 219, 254))
            draw.rounded_rectangle([x_chip, y_chip, x_chip + ancho_chip, y_chip + 180], radius=16, fill=(37, 99, 235), outline=(29, 78, 216), width=2)
            f_num = ajustar_fuente(draw, t_clean, ancho_chip - 20, 28, 12, True)
            texto_centrado(draw, x_chip, x_chip + ancho_chip, y_chip + 28, t_clean, f_num, (255, 255, 255))
            texto_centrado(draw, x_chip, x_chip + ancho_chip, y_chip + 84, unidad_chip, f_chip_s, (191, 219, 254))
            draw.line([x_chip + 14, y_chip + 118, x_chip + ancho_chip - 14, y_chip + 118], fill=(96, 165, 250), width=1)
            f_disp = ajustar_fuente(draw, "DISPONIBLE", ancho_chip - 14, 14, 9, True)  # no se sale del chip angosto
            texto_centrado(draw, x_chip, x_chip + ancho_chip, y_chip + 134, "DISPONIBLE", f_disp, (255, 255, 255))
            x_chip += ancho_chip + 16
        if extra:
            texto_centrado(draw, 30, 1170, 1070, f"+ {extra} tallas más disponibles", cargar_fuente(15, True), (71, 85, 105))
    else:
        aviso = "Consulta las tallas disponibles de este modelo en la sección de preguntas."
        draw.rounded_rectangle([60, 890, 1140, 1060], radius=16, fill=(239, 246, 255), outline=(191, 219, 254), width=2)
        f_aviso = ajustar_fuente(draw, aviso, 980, 28, 16, True)
        texto_centrado(draw, 60, 1140, 960, aviso, f_aviso, (30, 64, 175))

    info = "¿Dudas con tu elección? Recibe asesoría sin compromiso en la sección de preguntas antes de comprar."
    draw.rounded_rectangle([60, 1095, 1140, 1152], radius=16, fill=(239, 246, 255), outline=(191, 219, 254), width=1)
    draw.ellipse([82, 1108, 112, 1138], fill=(37, 99, 235))
    texto_centrado(draw, 82, 112, 1111, "i", f_pill, (255, 255, 255))
    draw.text((128, 1112), info, fill=(30, 64, 175), font=ajustar_fuente(draw, info, f_info_ancho, 18, 12))
    return lienzo


def renderizar_lamina_04_guia_tallas_cm(perfil: Dict[str, Any], tallas_disponibles: List[str]) -> Image.Image:
    lienzo = Image.new("RGB", (1200, 1200), (244, 247, 252))
    draw = ImageDraw.Draw(lienzo)
    f_paso_h = cargar_fuente(21, True)

    draw.rounded_rectangle([30, 25, 1170, 150], radius=20, fill=(15, 23, 42))
    draw.rounded_rectangle([35, 142, 1165, 150], radius=4, fill=(245, 158, 11))
    t4, s4 = perfil["titulo_04"], perfil["subtitulo_04"]
    texto_centrado(draw, 30, 1170, 42, t4, ajustar_fuente(draw, t4, 1080, 32, 18, True), (255, 255, 255))
    texto_centrado(draw, 30, 1170, 94, s4, ajustar_fuente(draw, s4, 1080, 19, 12, True), (148, 163, 184))

    # Se resaltan solo las tallas realmente disponibles (sin inventar por defecto)
    base_activas = tallas_disponibles or (
        perfil["tallas_default"][:3] if perfil["tipo"] in TIPOS_CON_PRESENTACION_FIJA else []
    )
    set_activas = {normalizar_etiqueta_talla(t) for t in base_activas}
    cabeceras = perfil["columnas_04"]
    cols = ((28, 160), (195, 180), (385, 160))  # (desplazamiento x, ancho máx.)

    for x0, x1, filas in ((30, 585, perfil["filas_izq"]), (615, 1170, perfil["filas_der"])):
        draw.rounded_rectangle([x0, 172, x1, 830], radius=18, fill=(255, 255, 255), outline=(219, 228, 240), width=2)
        draw.rounded_rectangle([x0, 172, x1, 232], radius=16, fill=(15, 23, 42))
        draw.rectangle([x0, 210, x1, 232], fill=(15, 23, 42))
        for (off, ancho), txt in zip(cols, cabeceras):
            draw.text((x0 + off, 191), txt, fill=(255, 255, 255), font=ajustar_fuente(draw, txt, ancho, 18, 11, True))

        alto_f = min(74, 585 // max(len(filas), 1))
        y_f = 240
        for idx_f, (v1, v2, v3) in enumerate(filas):
            y_t = y_f + (alto_f - 28) // 2
            clave_fila = normalizar_etiqueta_talla(v1.split("(")[0].replace("TALLA", "").strip())
            if clave_fila in set_activas:
                draw.rounded_rectangle([x0 + 10, y_f, x1 - 10, y_f + alto_f - 6], radius=10, fill=(220, 252, 231), outline=(134, 239, 172), width=2)
                draw.ellipse([x0 + 20, y_f + alto_f // 2 - 9, x0 + 34, y_f + alto_f // 2 + 5], fill=(22, 163, 74))
                celdas = (
                    (42, 148, v1, (21, 128, 61), True),
                    (195, 180, v2, (21, 128, 61), True),
                    (385, 160, v3, (29, 78, 216), True),
                )
            else:
                bg_row = (248, 250, 252) if idx_f % 2 == 1 else (255, 255, 255)
                draw.rounded_rectangle([x0 + 10, y_f, x1 - 10, y_f + alto_f - 6], radius=8, fill=bg_row)
                celdas = (
                    (28, 162, v1, (15, 23, 42), True),
                    (195, 180, v2, (71, 85, 105), False),
                    (385, 160, v3, (37, 99, 235), True),
                )
            for off, ancho, txt, color, neg in celdas:
                draw.text((x0 + off, y_t), txt, fill=color, font=ajustar_fuente(draw, txt, ancho, 20, 11, neg))
            y_f += alto_f

    draw.rounded_rectangle([30, 852, 1170, 1175], radius=22, fill=(255, 255, 255), outline=(219, 228, 240), width=2)
    t1, t2 = "¿CÓMO VERIFICAR TU TALLA O MEDIDA EN 3 PASOS? ", "(Toma solo 1 minuto)"
    w1, w2 = draw.textlength(t1, font=f_paso_h), draw.textlength(t2, font=f_paso_h)
    x_ini = (1200 - (w1 + w2)) / 2
    draw.text((x_ini, 872), t1, fill=(15, 23, 42), font=f_paso_h)
    draw.text((x_ini + w1, 872), t2, fill=(22, 163, 74), font=f_paso_h)

    x_card = 55
    for idx_p, (tit_p, desc_p) in enumerate(perfil["pasos_04"], 1):
        draw.rounded_rectangle([x_card, 918, x_card + 346, 1152], radius=16, fill=(248, 250, 252), outline=(219, 228, 240), width=2)
        draw.rounded_rectangle([x_card, 918, x_card + 346, 926], radius=4, fill=(37, 99, 235))
        draw.rounded_rectangle([x_card + 20, 942, x_card + 62, 984], radius=10, fill=(37, 99, 235))
        texto_centrado(draw, x_card + 20, x_card + 62, 949, str(idx_p), f_paso_h, (255, 255, 255))
        draw.text((x_card + 76, 951), tit_p, fill=(15, 23, 42), font=ajustar_fuente(draw, tit_p, 255, 19, 12, True))
        f_d, lineas, _ = envolver_ajustado(draw, desc_p, 300, 4, 17, 13)
        for i, lin in enumerate(lineas):
            draw.text((x_card + 22, 1005 + i * 28), lin, fill=(71, 85, 105), font=f_d)
        x_card += 372
    return lienzo


# ======================================================================================
# DIRECTORIOS, IMÁGENES LOCALES Y FLUJO PRINCIPAL
# ======================================================================================
def descartar_carpeta(carpeta: Path) -> None:
    """Quita una carpeta fuera de catálogo: la mueve a DIR_DESCARTADOS (o la borra si se configuró)."""
    if ELIMINAR_DESCARTADAS:
        shutil.rmtree(carpeta, ignore_errors=True)
        return
    DIR_DESCARTADOS.mkdir(parents=True, exist_ok=True)
    destino = DIR_DESCARTADOS / carpeta.name
    if destino.exists():
        shutil.rmtree(destino, ignore_errors=True)
    try:
        shutil.move(str(carpeta), str(destino))
    except OSError as e:
        print(f"⚠️ No se pudo mover {carpeta.name} a descartados: {e}")


def intento_web_reciente(carpeta: Path) -> bool:
    """True si ya se buscó en la web para esta carpeta hace menos de REINTENTO_DIAS días."""
    marca = carpeta / MARCA_INTENTO_WEB
    try:
        return marca.exists() and (time.time() - marca.stat().st_mtime) < REINTENTO_DIAS * 86400
    except OSError:
        return False


def marcar_intento_web(carpeta: Path) -> None:
    try:
        (carpeta / MARCA_INTENTO_WEB).touch()
    except OSError:
        pass


def normalizar_y_depurar_directorios(datos_bd: Dict[str, Dict[str, Any]]) -> int:
    """
    1. Fusiona carpetas `PS-1342498` a `1342498`.
    2. Aparta (mueve a `DIR_DESCARTADOS`, o borra si ELIMINAR_DESCARTADAS) cualquier carpeta
       de `data/catalogo_listo` que NO pertenezca a los catálogos PDF oficiales de `datos_bd`.

    Protección: si `datos_bd` viene vacío (BD ausente, sin columna origen_catalogo o sin filas)
    NO se aparta nada, para no vaciar el catálogo por un error de lectura.
    """
    if not DIR_CATALOGO_LISTO.exists():
        return 0

    for d in list(DIR_CATALOGO_LISTO.iterdir()):
        if d.is_dir() and d.name.upper().startswith("PS-"):
            id_puro = d.name[3:].strip()
            if id_puro:
                dest = DIR_CATALOGO_LISTO / id_puro
                dest.mkdir(parents=True, exist_ok=True)
                for arch in d.iterdir():
                    if arch.is_file():
                        dest_arch = dest / arch.name
                        if not dest_arch.exists():
                            shutil.move(str(arch), str(dest_arch))
                        else:
                            arch.unlink()
                try:
                    d.rmdir()
                except OSError:
                    pass

    if not datos_bd:
        print("⚠️ Sin productos de catálogo PDF en la BD: se omite la depuración de carpetas.")
        return 0

    apartadas = 0
    for d in list(DIR_CATALOGO_LISTO.iterdir()):
        if not d.is_dir():
            continue
        m = re.search(r"(\d{5,8})", d.name)
        cid = m.group(1) if m else ""
        if not cid or (cid not in datos_bd and normalizar_id(cid) not in datos_bd):
            descartar_carpeta(d)
            apartadas += 1
        elif not any(d.iterdir()):
            try:
                d.rmdir()
            except OSError:
                pass

    return apartadas


def obtener_imagenes_base_locales(carpeta: Path) -> Tuple[Optional[Image.Image], Optional[Image.Image]]:
    candidatas_01 = ["01_original_raw.jpg", "01_portada_blanca.jpg", "01_portada.jpg", "01_portada_hd.jpg", "01.jpg"]
    candidatas_02 = ["02_angulo_raw.jpg", "02_angulo_galeria.jpg", "02_angulo.jpg", "02_detalle.jpg", "02_angulo_hd.jpg", "02.jpg"]
    img_01, img_02 = None, None

    for n1 in candidatas_01:
        p1 = carpeta / n1
        if p1.exists():
            try:
                img_01 = Image.open(p1).convert("RGB")
                break
            except ERRORES_IMAGEN:
                pass
    if img_01 is None:
        for arch in sorted(carpeta.glob("*.jpg")) + sorted(carpeta.glob("*.png")):
            if not arch.name.lower().startswith(("02", "03", "04", "lamina_03", "lamina_04")):
                try:
                    img_01 = Image.open(arch).convert("RGB")
                    break
                except ERRORES_IMAGEN:
                    pass
    if img_01 is not None and not (carpeta / "01_original_raw.jpg").exists():
        try:
            img_01.save(carpeta / "01_original_raw.jpg", **OPCIONES_JPEG)
        except OSError:
            pass

    for n2 in candidatas_02:
        p2 = carpeta / n2
        if p2.exists():
            try:
                cand2 = Image.open(p2).convert("RGB")
                if tiene_lupa_o_rotacion_vieja(cand2):
                    continue
                if img_01 is not None and not son_imagenes_iguales(img_01, cand2, revisar_espejo=True):
                    img_02 = cand2
                    if not (carpeta / "02_angulo_raw.jpg").exists():
                        try:
                            img_02.save(carpeta / "02_angulo_raw.jpg", **OPCIONES_JPEG)
                        except OSError:
                            pass
                    break
            except ERRORES_IMAGEN:
                pass
    return img_01, img_02


def ejecutar_estudio_fotografico() -> None:
    configurar_salida()
    print("=" * 88)
    print("📸 PASO 6: ESTUDIO FOTOGRÁFICO HD (EXCLUSIVO CATÁLOGOS PDF OFICIALES)")
    print("=" * 88)

    DIR_CATALOGO_LISTO.mkdir(parents=True, exist_ok=True)
    datos_bd = cargar_catalogo_sqlite()
    apartadas = normalizar_y_depurar_directorios(datos_bd)
    if apartadas > 0:
        destino = "eliminaron" if ELIMINAR_DESCARTADAS else f"movieron a {DIR_DESCARTADOS}"
        print(f"🧹 Se {destino} {apartadas} carpetas fuera de catálogo PDF.")

    carpetas_por_id: Dict[str, Path] = {}
    for d in DIR_CATALOGO_LISTO.iterdir():
        if d.is_dir() and (m := re.search(r"(\d{5,8})", d.name)):
            cid = m.group(1)
            carpetas_por_id[cid] = d
            carpetas_por_id[normalizar_id(cid)] = d

    nuevas_agregadas = 0
    skus_ordenados = sorted(
        datos_bd.values(),
        key=lambda x: (x.get("stock", 0), x.get("costo", 0.0)),
        reverse=True,
    )
    vistos: set[str] = set()
    for meta in skus_ordenados:
        if nuevas_agregadas >= MAX_DESCARGAS_WEB_POR_CORRIDA:
            break
        id_l = meta["id_limpio"]
        if id_l in vistos or id_l in carpetas_por_id:
            continue
        vistos.add(id_l)
        if meta["origen_catalogo"] and meta["costo"] > 0 and meta["stock"] > 0:
            nueva_c = DIR_CATALOGO_LISTO / id_l
            nueva_c.mkdir(parents=True, exist_ok=True)
            carpetas_por_id[id_l] = nueva_c
            nuevas_agregadas += 1

    def prioridad(c: Path) -> Tuple[int, int, str]:
        cid = m.group(1) if (m := re.search(r"(\d{5,8})", c.name)) else c.name
        info = datos_bd.get(cid) or datos_bd.get(normalizar_id(cid)) or {}
        return (1 if info.get("costo", 0) > 0 else 0, int(info.get("stock", 0)), c.name)

    carpetas = sorted([d for d in DIR_CATALOGO_LISTO.iterdir() if d.is_dir()], key=prioridad, reverse=True)
    total_actualizados = 0
    descargas_web = 0
    conservar = {
        "01_portada_blanca.jpg", "02_angulo_galeria.jpg", "03_ficha_beneficios.jpg", "04_guia_tallas_cm.jpg",
        "01_original_raw.jpg", "02_angulo_raw.jpg", "01.jpg", "02.jpg", "03.jpg", "04.jpg",
    }

    for carpeta in carpetas:
        id_carpeta = m.group(1) if (m := re.search(r"(\d{5,8})", carpeta.name)) else carpeta.name
        id_limpio = normalizar_id(id_carpeta)
        meta = datos_bd.get(id_carpeta) or datos_bd.get(id_limpio)
        if not meta:
            if datos_bd:
                descartar_carpeta(carpeta)
            continue

        img_01_raw, img_02_raw = obtener_imagenes_base_locales(carpeta)
        falta_foto = img_01_raw is None or img_02_raw is None
        if falta_foto and descargas_web < MAX_DESCARGAS_WEB_POR_CORRIDA and not intento_web_reciente(carpeta):
            tenia_01 = img_01_raw is not None
            descargadas = descargar_fotos_oficiales_ps(id_limpio, meta.get("url_real", ""), img_01_existente=img_01_raw)
            descargas_web += 1
            if descargadas:
                if not tenia_01:
                    img_01_raw = descargadas[0]
                    try:
                        img_01_raw.save(carpeta / "01_original_raw.jpg", **OPCIONES_JPEG)
                    except OSError:
                        pass
                if img_02_raw is None and len(descargadas) > 1:
                    img_02_raw = descargadas[1]
                    try:
                        img_02_raw.save(carpeta / "02_angulo_raw.jpg", **OPCIONES_JPEG)
                    except OSError:
                        pass
            if img_01_raw is None or img_02_raw is None:
                marcar_intento_web(carpeta)  # no insistir en cada corrida

        if img_01_raw is None:
            if not any(carpeta.iterdir()):
                try:
                    carpeta.rmdir()
                except OSError:
                    pass
            continue

        img_01_rec = recortar_espacio_blanco(img_01_raw)
        img_02_rec = (
            recortar_espacio_blanco(img_02_raw)
            if (img_02_raw is not None and not son_imagenes_iguales(img_01_rec, img_02_raw, revisar_espejo=False))
            else None
        )

        perfil = detectar_perfil_prenda(
            meta["nombre"], meta["marca"], meta["corte"], meta.get("tallas_lista", []), meta.get("origen_catalogo", "")
        )
        lam_01 = crear_lamina_01_portada_blanca(img_01_rec)
        lam_02 = crear_lamina_02_angulo_galeria(img_01_rec, img_02_rec)
        lam_03 = renderizar_lamina_03_ficha_beneficios(img_01_rec, meta["nombre"], perfil, meta.get("tallas_lista", []))
        lam_04 = renderizar_lamina_04_guia_tallas_cm(perfil, meta.get("tallas_lista", []))

        for base, lam in (
            ("01_portada_blanca", lam_01),
            ("02_angulo_galeria", lam_02),
            ("03_ficha_beneficios", lam_03),
            ("04_guia_tallas_cm", lam_04),
        ):
            destino = carpeta / f"{base}.jpg"
            lam.save(destino, **OPCIONES_JPEG)
            shutil.copyfile(destino, carpeta / f"{base[:2]}.jpg")

        for arch in list(carpeta.glob("*.jpg")) + list(carpeta.glob("*.png")):
            if arch.name not in conservar:
                try:
                    arch.unlink()
                except OSError:
                    pass

        estado_02 = "2.º ángulo PS" if img_02_rec is not None else "Encuadre detalle 0°"
        estado_tallas = f"{len(meta.get('tallas_lista', []))} tallas" if meta.get("tallas_lista") else "sin tallas"
        total_actualizados += 1
        print(f"✅ [{id_carpeta}] ({meta['origen_catalogo']} | {perfil['tipo']} | {estado_02} | {estado_tallas}) -> {meta['nombre']}")

    print("=" * 88)
    print(
        f"🎉 Total actualizado: {total_actualizados} carruseles exclusivos de Catálogos PDF "
        f"(Web: {descargas_web}/{MAX_DESCARGAS_WEB_POR_CORRIDA})."
    )

    if Path("paso5_motor_financiero.py").exists():
        print("🔄 Sincronizando diagnóstico 4/4 con paso5_motor_financiero.py...")
        subprocess.run([sys.executable, "paso5_motor_financiero.py"], check=False)


if __name__ == "__main__":
    ejecutar_estudio_fotografico()