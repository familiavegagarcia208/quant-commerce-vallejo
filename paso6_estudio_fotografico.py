"""
=========================================================================================
PASO 6 (EDICIÓN COMERCIAL B2C + SECRETO INDUSTRIAL + GUÍA ADAPTATIVA TOTAL):
ESTUDIO FOTOGRÁFICO AUTOMATIZADO CON PILLOW
=========================================================================================
MEJORA CLAVE EN GUÍA DE TALLAS:
- Extrae el rango completo del modelo en Vallejo (con y sin stock) y construye la corrida
  continua (respetando medios puntos si el modelo los maneja).
- Si el modelo tiene <= 10 tallas: Renderiza Tabla Amplia de 1 Columna (Letra Grande).
- Si el modelo tiene > 10 tallas (hasta 22 tallas): Renderiza automáticamente una
  Tabla Doble (2 Columnas lado a lado) para que TODAS las tallas entren con nitidez HD.
=========================================================================================
"""
from __future__ import annotations

import io
import json
import re
import sqlite3
from pathlib import Path
from urllib.parse import urlparse

from bs4 import BeautifulSoup
from curl_cffi import requests as curl_requests
from PIL import Image, ImageChops, ImageDraw, ImageEnhance, ImageFilter, ImageFont

# ---------------------------------------------------------------------------
# Configuración de Rutas y Paleta Visual E-Commerce
# ---------------------------------------------------------------------------
RUTA_BD = Path("data/zapateria_vallejo.db")
DIR_RAW = Path("data/imagenes_origen")
DIR_CATALOGO = Path("data/catalogo_listo")

LIENZO_SIZE = (1200, 1200)
MAX_ANGULOS_POR_SKU = 8

BLANCO_PURO = (255, 255, 255)
FONDO_LIENZO = (244, 247, 252)
AZUL_NOCHE = (15, 23, 42)
AZUL_PROFUNDO = (30, 58, 138)
AZUL_ELECTRICO = (37, 99, 235)
CYAN_LUZ = (56, 189, 248)
AMBAR_ORO = (245, 158, 11)
VERDE_CONFIANZA = (22, 163, 74)
VERDE_PASTEL = (220, 252, 231)
AZUL_PASTEL = (239, 246, 255)
GRIS_TEXTO = (71, 85, 105)
GRIS_LINEA = (214, 222, 235)


# ---------------------------------------------------------------------------
# Tipografías Multiplataforma
# ---------------------------------------------------------------------------
def cargar_fuente(bold: bool = False, size: int = 28) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    rutas = (
        [
            "C:/Windows/Fonts/segoeuib.ttf",
            "C:/Windows/Fonts/arialbd.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        ]
        if bold
        else [
            "C:/Windows/Fonts/segoeui.ttf",
            "C:/Windows/Fonts/arial.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        ]
    )
    for ruta in rutas:
        if Path(ruta).exists():
            try:
                return ImageFont.truetype(ruta, size=size)
            except Exception:
                continue
    return ImageFont.load_default()


# ---------------------------------------------------------------------------
# Motor de Geometría Dinámica
# ---------------------------------------------------------------------------
def dibujar_pildora_ajustada(
    draw: ImageDraw.ImageDraw,
    x: int,
    y: int,
    texto: str,
    fuente: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    color_texto: tuple[int, int, int],
    color_fondo: tuple[int, int, int],
    color_borde: tuple[int, int, int] | None = None,
    pad_x: int = 24,
    pad_y: int = 12,
    radius: int = 14,
    centrar_en_x: int | None = None,
) -> tuple[int, int, int, int]:
    bbox = draw.textbbox((0, 0), texto, font=fuente)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]

    ancho_caja = tw + (pad_x * 2)
    alto_caja = th + (pad_y * 2)

    x1 = (centrar_en_x - ancho_caja // 2) if centrar_en_x is not None else x
    y1 = y
    x2 = x1 + ancho_caja
    y2 = y1 + alto_caja

    draw.rounded_rectangle(
        [x1, y1, x2, y2],
        radius=radius,
        fill=color_fondo,
        outline=color_borde,
        width=2 if color_borde else 0,
    )
    tx = x1 + pad_x - bbox[0]
    ty = y1 + pad_y - bbox[1]
    draw.text((tx, ty), texto, fill=color_texto, font=fuente)
    return x1, y1, x2, y2


def dibujar_texto_centrado(
    draw: ImageDraw.ImageDraw,
    x_centro: int,
    y: int,
    texto: str,
    fuente: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    fill: tuple[int, int, int],
) -> None:
    bbox = draw.textbbox((0, 0), texto, font=fuente)
    tw = bbox[2] - bbox[0]
    draw.text((x_centro - tw // 2 - bbox[0], y - bbox[1]), texto, fill=fill, font=fuente)


def dibujar_texto_envuelto(
    draw: ImageDraw.ImageDraw,
    texto: str,
    x: int,
    y: int,
    max_width: int,
    fuente: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    fill: tuple[int, int, int],
    interlineado: int = 6,
) -> int:
    palabras = texto.split()
    lineas: list[str] = []
    actual = ""

    for p in palabras:
        prueba = f"{actual} {p}".strip()
        bbox = draw.textbbox((0, 0), prueba, font=fuente)
        if (bbox[2] - bbox[0]) <= max_width or not actual:
            actual = prueba
        else:
            lineas.append(actual)
            actual = p
    if actual:
        lineas.append(actual)

    y_cur = y
    for lin in lineas:
        bbox_l = draw.textbbox((0, 0), lin, font=fuente)
        draw.text((x, y_cur - bbox_l[1]), lin, fill=fill, font=fuente)
        y_cur += (bbox_l[3] - bbox_l[1]) + interlineado
    return y_cur


def pegar_tarjeta_con_sombra(
    lienzo_rgba: Image.Image,
    box: list[int],
    radius: int = 22,
    fill: tuple[int, int, int] = BLANCO_PURO,
    outline: tuple[int, int, int] | None = GRIS_LINEA,
) -> None:
    x1, y1, x2, y2 = box
    w, h = x2 - x1, y2 - y1
    sombra = Image.new("RGBA", (w + 40, h + 40), (0, 0, 0, 0))
    d_som = ImageDraw.Draw(sombra)
    d_som.rounded_rectangle([20, 24, w + 20, h + 24], radius=radius, fill=(15, 23, 42, 18))
    sombra = sombra.filter(ImageFilter.GaussianBlur(radius=10))
    lienzo_rgba.alpha_composite(sombra, (x1 - 20, y1 - 20))

    draw = ImageDraw.Draw(lienzo_rgba)
    draw.rounded_rectangle(box, radius=radius, fill=(*fill, 255), outline=outline, width=2 if outline else 0)


def crear_icono_check_hd(diametro: int = 50, color_fondo: tuple[int, int, int] = VERDE_CONFIANZA) -> Image.Image:
    escala = 4
    tam = diametro * escala
    img_hr = Image.new("RGBA", (tam, tam), (0, 0, 0, 0))
    draw_hr = ImageDraw.Draw(img_hr)
    draw_hr.ellipse([4, 4, tam - 4, tam - 4], fill=(*color_fondo, 255))

    p1 = (int(tam * 0.28), int(tam * 0.52))
    p2 = (int(tam * 0.44), int(tam * 0.68))
    p3 = (int(tam * 0.74), int(tam * 0.34))
    grosor = int(tam * 0.11)

    draw_hr.line([p1, p2], fill=(255, 255, 255, 255), width=grosor, joint="curve")
    draw_hr.line([p2, p3], fill=(255, 255, 255, 255), width=grosor, joint="curve")
    return img_hr.resize((diametro, diametro), Image.Resampling.LANCZOS)


def crear_sombra_piso(ancho: int, alto: int) -> Image.Image:
    lienzo_sombra = Image.new("RGBA", (ancho + 120, alto + 80), (0, 0, 0, 0))
    draw = ImageDraw.Draw(lienzo_sombra)
    draw.ellipse([60, 40, ancho + 60, alto + 40], fill=(15, 23, 42, 46))
    return lienzo_sombra.filter(ImageFilter.GaussianBlur(radius=18))


def limpiar_titulo_comercial(nombre_crudo: str) -> str:
    limpio = re.sub(r"\b\d{5,8}\b", "", str(nombre_crudo)).strip()
    limpio = re.sub(r"\s+", " ", limpio)
    return limpio.upper()[:36] if limpio else "CALZADO DE TEMPORADA"


# ---------------------------------------------------------------------------
# Migración SQLite
# ---------------------------------------------------------------------------
def asegurar_columnas_estudio(conn: sqlite3.Connection) -> None:
    cols = [c[1] for c in conn.execute("PRAGMA table_info(zapatos_candidatos)").fetchall()]
    nuevas = {
        "galeria_urls_json": "TEXT",
        "total_fotos_galeria": "INTEGER DEFAULT 0",
        "ruta_portada": "TEXT",
        "ruta_infografia": "TEXT",
        "ruta_guia_tallas": "TEXT",
        "estudio_foto_listo": "INTEGER DEFAULT 0",
    }
    for col, tipo in nuevas.items():
        if col not in cols:
            conn.execute(f"ALTER TABLE zapatos_candidatos ADD COLUMN {col} {tipo};")
    conn.commit()


# ---------------------------------------------------------------------------
# Extractor y Descargador Multi-Ángulo
# ---------------------------------------------------------------------------
def normalizar_clave_angulo(url: str) -> str:
    path = urlparse(url).path.lower()
    nombre = Path(path).stem
    return re.sub(r"(_thumb|_small|_medium|_mini|-\d+x\d+)$", "", nombre)


def expandir_variantes_cdn(url_base: str, id_ps: str) -> list[str]:
    variantes: list[str] = []
    m = re.search(rf"({re.escape(id_ps)})([-_]\d+)?(\.(?:jpg|jpeg|webp|png))", url_base, flags=re.I)
    if not m:
        return variantes

    prefijo = url_base[: m.start()]
    sufijo_ext = url_base[m.start(3) :]

    for sep in ("_", "-"):
        for n in range(1, 7):
            variantes.append(f"{prefijo}{id_ps}{sep}{n}{sufijo_ext}")
    return variantes


def extraer_urls_galeria_sku(id_ps: str, html: str) -> list[str]:
    candidatas: list[str] = []
    soup = BeautifulSoup(html, "html.parser")

    og = soup.find("meta", property="og:image")
    if og and og.get("content"):
        candidatas.append(str(og["content"]).strip())

    for tag in soup.find_all(["img", "source", "a"]):
        for attr in ("data-zoom-image", "data-large_image", "data-src", "src", "srcset", "href"):
            val = tag.get(attr)
            if not val:
                continue
            for parte in str(val).split(","):
                u = parte.strip().split(" ")[0]
                if id_ps in u and u.startswith("http") and re.search(r"\.(jpg|jpeg|webp|png)", u, re.I):
                    candidatas.append(u)

    patrones_raw = re.findall(
        rf"https?:\\?/\\?/[^\s\"'<>\\]+{re.escape(id_ps)}[^\s\"'<>\\]*\.(?:jpg|jpeg|webp|png)",
        html,
        flags=re.IGNORECASE,
    )
    for p in patrones_raw:
        candidatas.append(p.replace("\\/", "/"))

    if candidatas:
        candidatas.extend(expandir_variantes_cdn(candidatas[0], id_ps))

    por_angulo: dict[str, str] = {}
    for url in candidatas:
        if any(bad in url.lower() for bad in ("logo", "icon", "banner", "avatar", "placeholder")):
            continue
        clave = normalizar_clave_angulo(url)
        if clave not in por_angulo:
            por_angulo[clave] = url
        elif any(k in por_angulo[clave].lower() for k in ("thumb", "small", "150", "300")) and not any(
            k in url.lower() for k in ("thumb", "small", "150", "300")
        ):
            por_angulo[clave] = url

    return list(por_angulo.values())[:MAX_ANGULOS_POR_SKU]


def obtener_galeria_imagenes_origen(
    id_ps: str, url_producto: str | None
) -> tuple[list[Image.Image], list[str]]:
    carpeta_raw_sku = DIR_RAW / id_ps
    carpeta_raw_sku.mkdir(parents=True, exist_ok=True)

    for ext in (".jpg", ".jpeg", ".png", ".webp"):
        legado = DIR_RAW / f"{id_ps}{ext}"
        destino_inicial = carpeta_raw_sku / "vista_01.jpg"
        if legado.exists() and not destino_inicial.exists():
            try:
                Image.open(legado).convert("RGB").save(destino_inicial, format="JPEG", quality=95)
            except Exception:
                pass

    urls_detectadas: list[str] = []
    if url_producto and url_producto.startswith("http"):
        try:
            resp = curl_requests.get(url_producto, impersonate="chrome120", timeout=15)
            if resp.status_code == 200:
                urls_detectadas = extraer_urls_galeria_sku(id_ps, resp.text)
        except Exception as exc:
            print(f"   ⚠️ Aviso al consultar galería web de {id_ps}: {exc}")

    imagenes_cargadas: list[Image.Image] = []
    hashes_vistos: set[bytes] = set()
    urls_exitosas: list[str] = []

    for idx, img_url in enumerate(urls_detectadas, start=1):
        ruta_vista = carpeta_raw_sku / f"vista_{idx:02d}.jpg"
        img_obj: Image.Image | None = None

        if ruta_vista.exists():
            try:
                img_obj = Image.open(ruta_vista).convert("RGB")
            except Exception:
                img_obj = None

        if img_obj is None:
            try:
                r_img = curl_requests.get(img_url, impersonate="chrome120", timeout=12)
                if r_img.status_code == 200 and len(r_img.content) > 4500:
                    img_obj = Image.open(io.BytesIO(r_img.content)).convert("RGB")
                    img_obj.save(ruta_vista, format="JPEG", quality=95)
            except Exception:
                continue

        if img_obj is not None:
            huella = img_obj.resize((16, 16), Image.Resampling.BILINEAR).tobytes()
            if huella not in hashes_vistos:
                hashes_vistos.add(huella)
                imagenes_cargadas.append(img_obj)
                urls_exitosas.append(img_url)

    if not imagenes_cargadas:
        archivos_locales = sorted(
            [p for p in carpeta_raw_sku.iterdir() if p.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp")]
        )
        for p in archivos_locales:
            try:
                img_obj = Image.open(p).convert("RGB")
                huella = img_obj.resize((16, 16), Image.Resampling.BILINEAR).tobytes()
                if huella not in hashes_vistos:
                    hashes_vistos.add(huella)
                    imagenes_cargadas.append(img_obj)
            except Exception:
                continue

    return imagenes_cargadas, urls_exitosas


# ---------------------------------------------------------------------------
# Motor de Visión: Limpieza Perimetral y Escalado Exacto HD
# ---------------------------------------------------------------------------
def limpiar_fondo_y_recortar_al_ras(img_origen: Image.Image) -> Image.Image:
    img = img_origen.convert("RGB").copy()
    w, h = img.size

    puntos_borde = (
        [(x, 0) for x in range(0, w, 12)]
        + [(x, h - 1) for x in range(0, w, 12)]
        + [(0, y) for y in range(0, h, 12)]
        + [(w - 1, y) for y in range(0, h, 12)]
    )

    for bx, by in puntos_borde:
        r, g, b = img.getpixel((bx, by))
        if min(r, g, b) >= 195 and (max(r, g, b) - min(r, g, b)) <= 26:
            ImageDraw.floodfill(img, (bx, by), BLANCO_PURO, thresh=24)

    fondo_blanco = Image.new("RGB", img.size, BLANCO_PURO)
    diff = ImageChops.difference(img, fondo_blanco).convert("L")
    mascara_estricta = diff.point(lambda p: 255 if p > 12 else 0)
    mascara_limpia = mascara_estricta.filter(ImageFilter.MedianFilter(size=3))
    bbox = mascara_limpia.getbbox() or diff.getbbox()

    if bbox:
        img = img.crop(bbox)
    return img


def escalar_exacto_hd(img: Image.Image, max_ancho: int, max_alto: int) -> Image.Image:
    w, h = img.size
    escala = min(max_ancho / max(1, w), max_alto / max(1, h))
    nuevo_w = max(1, int(round(w * escala)))
    nuevo_h = max(1, int(round(h * escala)))

    img_escalada = img.resize((nuevo_w, nuevo_h), Image.Resampling.LANCZOS)
    img_escalada = img_escalada.filter(ImageFilter.UnsharpMask(radius=1.4, percent=120, threshold=3))
    img_escalada = ImageEnhance.Contrast(img_escalada).enhance(1.04)
    return img_escalada


def crear_imagen_blanca_hd(zapato_limpio: Image.Image, ruta_salida: Path) -> None:
    lienzo = Image.new("RGB", LIENZO_SIZE, BLANCO_PURO)
    zapato_gigante = escalar_exacto_hd(zapato_limpio, max_ancho=1080, max_alto=1020)

    pos_x = (LIENZO_SIZE[0] - zapato_gigante.width) // 2
    pos_y = (LIENZO_SIZE[1] - zapato_gigante.height) // 2
    lienzo.paste(zapato_gigante, (pos_x, pos_y))
    lienzo.save(ruta_salida, format="JPEG", quality=96, subsampling=0)


# ---------------------------------------------------------------------------
# ACTIVO COMERCIAL 1: Ficha de Beneficios B2C
# ---------------------------------------------------------------------------
def crear_ficha_beneficios(
    zapato_limpio: Image.Image,
    nombre_modelo: str,
    tallas_disponibles: list[str],
    ruta_salida: Path,
) -> None:
    lienzo = Image.new("RGB", LIENZO_SIZE, FONDO_LIENZO).convert("RGBA")
    draw = ImageDraw.Draw(lienzo)

    draw.rectangle([0, 0, 1200, 162], fill=AZUL_NOCHE)
    draw.rectangle([0, 162, 1200, 170], fill=AMBAR_ORO)

    f_tit = cargar_fuente(bold=True, size=44)
    f_sub = cargar_fuente(bold=False, size=22)
    f_badge = cargar_fuente(bold=True, size=19)

    titulo_publico = limpiar_titulo_comercial(nombre_modelo)
    draw.text((55, 28), titulo_publico, fill=BLANCO_PURO, font=f_tit)
    draw.text(
        (55, 96),
        "CALZADO NUEVO Y 100% ORIGINAL EN CAJA   ·   GARANTÍA DIRECTA",
        fill=(203, 213, 225),
        font=f_sub,
    )

    dibujar_pildora_ajustada(
        draw,
        x=885,
        y=52,
        texto="CALIDAD GARANTIZADA",
        fuente=f_badge,
        color_texto=AZUL_NOCHE,
        color_fondo=AMBAR_ORO,
        pad_x=22,
        pad_y=12,
        radius=24,
    )

    pegar_tarjeta_con_sombra(lienzo, [45, 200, 625, 838], radius=28, fill=BLANCO_PURO)
    draw = ImageDraw.Draw(lienzo)
    draw.ellipse([135, 305, 535, 705], fill=(241, 245, 249, 255))

    f_tag = cargar_fuente(bold=True, size=20)
    dibujar_pildora_ajustada(
        draw,
        x=0,
        y=226,
        texto="ENVÍO INMEDIATO DESDE CDMX",
        fuente=f_tag,
        color_texto=VERDE_CONFIANZA,
        color_fondo=VERDE_PASTEL,
        color_borde=(134, 239, 172),
        pad_x=28,
        pad_y=11,
        radius=20,
        centrar_en_x=335,
    )

    zapato_vitrina = escalar_exacto_hd(zapato_limpio, max_ancho=530, max_alto=430)
    sombra = crear_sombra_piso(int(zapato_vitrina.width * 0.88), 34)

    zx = 45 + (580 - zapato_vitrina.width) // 2
    zy = 315 + (430 - zapato_vitrina.height) // 2
    sx = 45 + (580 - sombra.width) // 2
    sy = zy + zapato_vitrina.height - 46

    lienzo.alpha_composite(sombra, (sx, sy))
    lienzo.paste(zapato_vitrina, (zx, zy))

    f_vit_pie = cargar_fuente(bold=True, size=19)
    dibujar_pildora_ajustada(
        draw,
        x=0,
        y=768,
        texto="DISEÑO CÓMODO, LIGERO Y RESISTENTE",
        fuente=f_vit_pie,
        color_texto=AZUL_PROFUNDO,
        color_fondo=AZUL_PASTEL,
        pad_x=26,
        pad_y=10,
        radius=16,
        centrar_en_x=335,
    )

    beneficios = [
        ("100% ORIGINAL EN CAJA", "Recibes tu calzado totalmente nuevo, sellado y en su empaque original."),
        ("ENVÍO RÁPIDO Y PROTEGIDO", "Despachamos tu pedido de inmediato con empaque de alta seguridad."),
        ("MÁXIMA COMODIDAD DIARIA", "Plantilla suave y suela antiderrapante ideal para largas jornadas."),
        ("COMPRA 100% PROTEGIDA", "Si la talla no te queda, tu cambio o devolución es rápido y sin costo."),
    ]
    icono_check = crear_icono_check_hd(diametro=48, color_fondo=VERDE_CONFIANZA)
    f_ben_tit = cargar_fuente(bold=True, size=23)
    f_ben_txt = cargar_fuente(bold=False, size=19)

    y_card = 200
    for tit, desc in beneficios:
        pegar_tarjeta_con_sombra(lienzo, [650, y_card, 1155, y_card + 144], radius=20, fill=BLANCO_PURO)
        draw = ImageDraw.Draw(lienzo)
        draw.rounded_rectangle([650, y_card, 664, y_card + 144], radius=6, fill=AZUL_ELECTRICO)
        lienzo.alpha_composite(icono_check, (682, y_card + 48))

        draw.text((746, y_card + 22), tit, fill=AZUL_NOCHE, font=f_ben_tit)
        dibujar_texto_envuelto(
            draw, desc, x=746, y=y_card + 58, max_width=385, fuente=f_ben_txt, fill=GRIS_TEXTO, interlineado=5
        )
        y_card += 164

    pegar_tarjeta_con_sombra(lienzo, [45, 868, 1155, 1158], radius=24, fill=BLANCO_PURO)
    draw = ImageDraw.Draw(lienzo)
    draw.rounded_rectangle([45, 868, 1155, 932], radius=24, fill=AZUL_PROFUNDO)
    draw.rectangle([45, 908, 1155, 932], fill=AZUL_PROFUNDO)

    f_talla_hdr = cargar_fuente(bold=True, size=24)
    draw.text((80, 885), "TALLAS NACIONALES (MX) DISPONIBLES PARA ENVÍO HOY:", fill=BLANCO_PURO, font=f_talla_hdr)

    tallas_mostrar = tallas_disponibles[:8] if tallas_disponibles else ["18.0", "19.0", "20.0", "21.0", "22.0", "23.0"]
    f_talla_num = cargar_fuente(bold=True, size=28)
    f_talla_mx = cargar_fuente(bold=True, size=14)

    ancho_btn = 116
    espacio_btn = 16
    total_ancho_btns = len(tallas_mostrar) * ancho_btn + (len(tallas_mostrar) - 1) * espacio_btn
    x_btn = max(75, (1200 - total_ancho_btns) // 2)

    for t in tallas_mostrar:
        draw.rounded_rectangle([x_btn + 3, 956, x_btn + ancho_btn + 3, 1044], radius=16, fill=(203, 213, 225))
        draw.rounded_rectangle(
            [x_btn, 952, x_btn + ancho_btn, 1040], radius=16, fill=AZUL_ELECTRICO, outline=CYAN_LUZ, width=2
        )
        dibujar_texto_centrado(draw, x_btn + ancho_btn // 2, 964, str(t), f_talla_num, BLANCO_PURO)
        dibujar_texto_centrado(draw, x_btn + ancho_btn // 2, 1010, "TALLA MX", f_talla_mx, (191, 219, 254))
        x_btn += ancho_btn + espacio_btn

    draw.rounded_rectangle([75, 1068, 1125, 1134], radius=14, fill=AZUL_PASTEL, outline=(191, 219, 254), width=1)
    draw.ellipse([96, 1083, 132, 1119], fill=AZUL_ELECTRICO)
    f_info_i = cargar_fuente(bold=True, size=21)
    dibujar_texto_centrado(draw, 114, 1089, "i", f_info_i, BLANCO_PURO)

    f_consejo = cargar_fuente(bold=True, size=20)
    draw.text(
        (148, 1088),
        "¿Duda con tu número? Revisa la tabla en centímetros en la siguiente foto antes de comprar.",
        fill=AZUL_NOCHE,
        font=f_consejo,
    )

    lienzo.convert("RGB").save(ruta_salida, format="JPEG", quality=95)


# ---------------------------------------------------------------------------
# ACTIVO COMERCIAL 2: Guía de Tallas Híbrida Adaptativa (1 o 2 Columnas)
# ---------------------------------------------------------------------------
def obtener_tabla_equivalencias_adaptativa(
    tallas_registradas_modelo: list[str],
    nombre_modelo: str,
) -> tuple[str, list[tuple[float, str, str, str]]]:
    """
    Construye la tabla cubriendo TODAS las tallas del producto y su segmento:
    - Si hay tallas registradas en SQLite para el modelo, genera el rango completo
      desde la talla mínima hasta la máxima (con margen de ±1 número) respetando
      medios puntos si el zapato los maneja, o números enteros si es corrida entera.
    - Si el rango supera las 10 tallas, devuelve hasta 20 filas para que el motor
      visual active automáticamente el diseño de Doble Columna.
    """
    nums: list[float] = []
    for t in tallas_registradas_modelo:
        try:
            nums.append(round(float(re.findall(r"\d+(?:\.\d+)?", str(t))[0]), 1))
        except Exception:
            pass

    nombre_up = nombre_modelo.upper()
    es_infantil = (nums and min(nums) < 22.0) or any(
        k in nombre_up for k in ("NIÑO", "NIÑA", "ESCOLAR", "INFANTIL", "SPIDERMAN", "KUROMI", "BEBE", "BEBÉ")
    )

    if es_infantil:
        segmento = "CALZADO INFANTIL, ESCOLAR Y JUVENIL"
        rango_def_min, rango_def_max = 15.0, 24.0
        paso = 0.5
    elif any(k in nombre_up for k in ("CABALLERO", "HOMBRE", "INDUSTRIAL")):
        segmento = "CALZADO PARA CABALLERO"
        rango_def_min, rango_def_max = 25.0, 30.0
        paso = 0.5
    else:
        segmento = "CALZADO DAMA Y JUVENIL"
        rango_def_min, rango_def_max = 22.0, 27.0
        paso = 0.5

    if nums:
        min_obs = min(nums)
        max_obs = max(nums)
        # Expandir suavemente el rango observado para cubrir toda la línea del fabricante
        t_inicio = max(12.0, math.floor(min_obs) - 1.0 if min_obs >= 15.0 else math.floor(min_obs))
        t_fin = min(31.0, math.ceil(max_obs) + 1.0)
        # Garantizar al menos el rango completo del segmento si solo se habían visto pocas tallas
        if (t_fin - t_inicio) < 5.0:
            t_inicio = min(t_inicio, rango_def_min)
            t_fin = max(t_fin, rango_def_max)
    else:
        t_inicio, t_fin = rango_def_min, rango_def_max

    base_tallas: list[float] = []
    actual = t_inicio
    while actual <= t_fin + 0.01 and len(base_tallas) < 20:
        base_tallas.append(round(actual, 1))
        actual += paso

    filas = []
    for t_mx in base_tallas:
        largo_pie = f"{t_mx - 0.3:.1f} a {t_mx:.1f} cm"
        plantilla = f"{t_mx + 0.5:.1f} cm"
        filas.append((t_mx, f"{t_mx:.1f} MX", largo_pie, plantilla))

    return segmento, filas


def crear_guia_tallas_cm(
    nombre_modelo: str,
    tallas_disponibles_stock: list[str],
    tallas_totales_modelo: list[str],
    ruta_salida: Path,
) -> None:
    """
    Renderiza la Guía de Tallas en Centímetros adaptando su estructura:
    - Si `len(filas) <= 10`: Diseño amplio de 1 sola tabla centrada.
    - Si `len(filas) > 10` : Diseño de Doble Tabla (2 columnas paralelas) que cubre
      hasta 20 tallas completas (ej. 15.0 MX a 24.5 MX con medios puntos) con total nitidez.
    """
    segmento, filas = obtener_tabla_equivalencias_adaptativa(
        tallas_totales_modelo or tallas_disponibles_stock, nombre_modelo
    )

    set_tallas_activas = set()
    for t in tallas_disponibles_stock:
        try:
            set_tallas_activas.add(round(float(re.findall(r"\d+(?:\.\d+)?", str(t))[0]), 1))
        except Exception:
            pass

    lienzo = Image.new("RGB", LIENZO_SIZE, FONDO_LIENZO).convert("RGBA")
    draw = ImageDraw.Draw(lienzo)

    # 1. Cabecera Comercial (CERO SKU)
    draw.rectangle([0, 0, 1200, 158], fill=AZUL_NOCHE)
    draw.rectangle([0, 158, 1200, 166], fill=AMBAR_ORO)

    f_tit = cargar_fuente(bold=True, size=42)
    f_sub = cargar_fuente(bold=False, size=22)
    draw.text((55, 26), "GUÍA DE TALLAS EN CENTÍMETROS (MÉXICO)", fill=BLANCO_PURO, font=f_tit)
    draw.text(
        (55, 92),
        f"TABLA COMPLETA PARA {segmento}   ·   HORMA EXACTA",
        fill=(203, 213, 225),
        font=f_sub,
    )

    # 2. Renderizado Adaptativo: ¿1 Columna (<=10 tallas) o 2 Columnas (>10 tallas)?
    if len(filas) <= 10:
        pegar_tarjeta_con_sombra(lienzo, [55, 190, 1145, 795], radius=22, fill=BLANCO_PURO)
        draw = ImageDraw.Draw(lienzo)

        y_top = 190
        draw.rounded_rectangle([55, y_top, 1145, y_top + 62], radius=22, fill=AZUL_PROFUNDO)
        draw.rectangle([55, y_top + 40, 1145, y_top + 62], fill=AZUL_PROFUNDO)

        f_th = cargar_fuente(bold=True, size=22)
        dibujar_texto_centrado(draw, 220, y_top + 18, "TALLA MÉXICO (MX)", f_th, BLANCO_PURO)
        dibujar_texto_centrado(draw, 600, y_top + 18, "LARGO DE TU PIE (CM)", f_th, BLANCO_PURO)
        dibujar_texto_centrado(draw, 975, y_top + 18, "MEDIDA PLANTILLA", f_th, BLANCO_PURO)

        f_td_bold = cargar_fuente(bold=True, size=23)
        f_td = cargar_fuente(bold=False, size=22)
        f_tag_disp = cargar_fuente(bold=True, size=14)

        y_row = y_top + 62
        alto_fila = 54

        for idx, (t_num, t_mx, med_pie, plant) in enumerate(filas):
            destacada = round(t_num, 1) in set_tallas_activas
            bg = VERDE_PASTEL if destacada else (BLANCO_PURO if idx % 2 == 0 else (248, 250, 252))
            draw.rectangle([57, y_row, 1143, y_row + alto_fila], fill=bg)
            draw.line([57, y_row + alto_fila, 1143, y_row + alto_fila], fill=GRIS_LINEA, width=1)

            dibujar_texto_centrado(draw, 190 if destacada else 220, y_row + 14, t_mx, f_td_bold, AZUL_NOCHE)
            if destacada:
                dibujar_pildora_ajustada(
                    draw,
                    x=265,
                    y=y_row + 13,
                    texto="ENVÍO HOY",
                    fuente=f_tag_disp,
                    color_texto=BLANCO_PURO,
                    color_fondo=VERDE_CONFIANZA,
                    pad_x=10,
                    pad_y=5,
                    radius=10,
                )

            dibujar_texto_centrado(draw, 600, y_row + 14, med_pie, f_td, GRIS_TEXTO)
            dibujar_texto_centrado(draw, 975, y_row + 14, plant, f_td_bold, AZUL_ELECTRICO)
            y_row += alto_fila
    else:
        # DISEÑO DOBLE TABLA (2 COLUMNAS LADO A LADO PARA CUBRIR HASTA 20 TALLAS)
        mitad = (len(filas) + 1) // 2
        col_izq = filas[:mitad]
        col_der = filas[mitad:]

        bloques = [
            (45, 588, col_izq),   # Tabla Izquierda (Ancho: 543 px)
            (612, 1155, col_der), # Tabla Derecha   (Ancho: 543 px)
        ]

        f_th = cargar_fuente(bold=True, size=18)
        f_td_bold = cargar_fuente(bold=True, size=20)
        f_td = cargar_fuente(bold=False, size=19)
        f_dot = cargar_fuente(bold=True, size=12)

        for x1, x2, lista_filas in bloques:
            pegar_tarjeta_con_sombra(lienzo, [x1, 188, x2, 796], radius=20, fill=BLANCO_PURO)
            draw = ImageDraw.Draw(lienzo)

            draw.rounded_rectangle([x1, 188, x2, 244], radius=20, fill=AZUL_PROFUNDO)
            draw.rectangle([x1, 224, x2, 244], fill=AZUL_PROFUNDO)

            c_talla = x1 + 95
            c_pie = x1 + 285
            c_plant = x1 + 455

            dibujar_texto_centrado(draw, c_talla, 204, "TALLA MX", f_th, BLANCO_PURO)
            dibujar_texto_centrado(draw, c_pie, 204, "TU PIE (CM)", f_th, BLANCO_PURO)
            dibujar_texto_centrado(draw, c_plant, 204, "PLANTILLA", f_th, BLANCO_PURO)

            y_row = 244
            alto_fila = max(48, min(54, 540 // max(1, mitad)))

            for idx, (t_num, t_mx, med_pie, plant) in enumerate(lista_filas):
                destacada = round(t_num, 1) in set_tallas_activas
                bg = VERDE_PASTEL if destacada else (BLANCO_PURO if idx % 2 == 0 else (248, 250, 252))
                draw.rectangle([x1 + 2, y_row, x2 - 2, y_row + alto_fila], fill=bg)
                draw.line([x1 + 2, y_row + alto_fila, x2 - 2, y_row + alto_fila], fill=GRIS_LINEA, width=1)

                if destacada:
                    draw.ellipse([x1 + 14, y_row + (alto_fila // 2) - 6, x1 + 26, y_row + (alto_fila // 2) + 6], fill=VERDE_CONFIANZA)

                dibujar_texto_centrado(draw, c_talla, y_row + 13, t_mx, f_td_bold, AZUL_NOCHE)
                dibujar_texto_centrado(draw, c_pie, y_row + 13, med_pie, f_td, GRIS_TEXTO)
                dibujar_texto_centrado(draw, c_plant, y_row + 13, plant, f_td_bold, AZUL_ELECTRICO)
                y_row += alto_fila

    # 3. Tres Tarjetas Inferiores Paso a Paso
    f_sec = cargar_fuente(bold=True, size=24)
    draw.text((55, 816), "¿CÓMO MEDIR TU PIE EN CASA EN 3 SIMPLES PASOS?  (● = Talla lista para envío hoy)", fill=AZUL_NOCHE, font=f_sec)

    pasos_cards = [
        ("1", "APOYA EL TALÓN", "Coloca una hoja de papel en el piso pegada a la pared y pisa firme con el talón tocando la pared."),
        ("2", "HAZ UNA MARCA", "Con un lápiz derecho, traza una pequeña línea justo donde termina tu dedo más largo."),
        ("3", "MIDE CON REGLA", "Mide en centímetros desde el borde de la hoja hasta la marca y busca tu talla en la tabla."),
    ]

    f_num = cargar_fuente(bold=True, size=26)
    f_card_tit = cargar_fuente(bold=True, size=22)
    f_card_txt = cargar_fuente(bold=False, size=19)

    ancho_card = 346
    espacio = 25
    x_c = 55
    y_c = 862

    for num_str, tit_p, desc_p in pasos_cards:
        pegar_tarjeta_con_sombra(lienzo, [x_c, y_c, x_c + ancho_card, 1158], radius=22, fill=BLANCO_PURO, outline=AZUL_ELECTRICO)
        draw = ImageDraw.Draw(lienzo)
        draw.rounded_rectangle([x_c, y_c, x_c + ancho_card, y_c + 12], radius=6, fill=AZUL_ELECTRICO)

        draw.ellipse([x_c + 24, y_c + 28, x_c + 76, y_c + 80], fill=AZUL_ELECTRICO)
        dibujar_texto_centrado(draw, x_c + 50, y_c + 38, num_str, f_num, BLANCO_PURO)
        draw.text((x_c + 92, y_c + 40), tit_p, fill=AZUL_NOCHE, font=f_card_tit)

        dibujar_texto_envuelto(
            draw,
            desc_p,
            x=x_c + 24,
            y=y_c + 98,
            max_width=ancho_card - 48,
            fuente=f_card_txt,
            fill=GRIS_TEXTO,
            interlineado=6,
        )
        x_c += ancho_card + espacio

    lienzo.convert("RGB").save(ruta_salida, format="JPEG", quality=95)


# ---------------------------------------------------------------------------
# Orquestador Principal
# ---------------------------------------------------------------------------
def ejecutar_estudio_fotografico() -> None:
    print("=" * 88)
    print("📸 PASO 6: ESTUDIO FOTOGRÁFICO B2C (TABLA DE TALLAS ADAPTATIVA 1 Y 2 COLUMNAS)")
    print("=" * 88)

    if not RUTA_BD.exists():
        print(f"❌ No se encontró la base de datos en {RUTA_BD}.")
        return

    conn = sqlite3.connect(RUTA_BD)
    conn.execute("PRAGMA journal_mode=WAL;")
    asegurar_columnas_estudio(conn)

    skus = conn.execute("""
        SELECT id_ps, nombre_modelo, url_real, costo_socio, apto_publicar
        FROM zapatos_candidatos
        WHERE costo_socio > 0
        ORDER BY apto_publicar DESC, velocidad_diaria DESC
    """).fetchall()

    if not skus:
        print("⚠️ No hay SKUs auditados con costo de socio mayor a $0 para procesar.")
        conn.close()
        return

    print(f"🎯 Procesando carruseles comerciales y guías de tallas completas para {len(skus)} modelos...\n")

    for id_ps, nombre, url_real, costo, apto in skus:
        id_str = str(id_ps)
        nombre_str = nombre or "Calzado Escolar y Casual"
        carpeta_sku = DIR_CATALOGO / id_str
        carpeta_sku.mkdir(parents=True, exist_ok=True)

        for viejo in carpeta_sku.glob("*.jpg"):
            try:
                viejo.unlink()
            except Exception:
                pass

        # Tallas con stock positivo hoy
        filas_disp = conn.execute("""
            SELECT DISTINCT talla
            FROM historial_stock_21_dias
            WHERE id_ps = ? AND stock_vallejo > 0
            ORDER BY CAST(talla AS REAL) ASC
        """, (id_str,)).fetchall()
        tallas_disp = [str(r[0]) for r in filas_disp]

        # Todas las tallas conocidas de este SKU (con o sin stock) para dimensionar el rango completo
        filas_todas = conn.execute("""
            SELECT DISTINCT talla
            FROM historial_stock_21_dias
            WHERE id_ps = ?
            ORDER BY CAST(talla AS REAL) ASC
        """, (id_str,)).fetchall()
        tallas_todas = [str(r[0]) for r in filas_todas]

        print(f"👞 Procesando carpeta interna [{id_str}] ({limpiar_titulo_comercial(nombre_str)}) | Costo: ${costo:,.2f}")

        vistas_origen, urls_galeria = obtener_galeria_imagenes_origen(id_str, url_real)
        if not vistas_origen:
            print(f"   ⚠️ No se hallaron fotos para {id_str}, saltando...\n")
            continue

        zapatos_limpios: list[Image.Image] = []
        rutas_generadas: list[Path] = []

        for idx, img_vista in enumerate(vistas_origen, start=1):
            zap_limpio = limpiar_fondo_y_recortar_al_ras(img_vista)
            zapatos_limpios.append(zap_limpio)

            nombre_archivo = "01_portada_blanca.jpg" if idx == 1 else f"{idx:02d}_angulo_galeria.jpg"
            ruta_out = carpeta_sku / nombre_archivo
            crear_imagen_blanca_hd(zap_limpio, ruta_out)
            rutas_generadas.append(ruta_out)

        num_ficha = len(rutas_generadas) + 1
        num_guia = len(rutas_generadas) + 2

        ruta_ficha = carpeta_sku / f"{num_ficha:02d}_ficha_beneficios.jpg"
        ruta_guia = carpeta_sku / f"{num_guia:02d}_guia_tallas_cm.jpg"

        crear_ficha_beneficios(zapatos_limpios[0], nombre_str, tallas_disp, ruta_ficha)
        crear_guia_tallas_cm(nombre_str, tallas_disp, tallas_todas, ruta_guia)

        conn.execute("""
            UPDATE zapatos_candidatos
            SET galeria_urls_json = ?,
                total_fotos_galeria = ?,
                ruta_portada = ?,
                ruta_infografia = ?,
                ruta_guia_tallas = ?,
                estudio_foto_listo = 1
            WHERE id_ps = ?
        """, (
            json.dumps(urls_galeria, ensure_ascii=False),
            len(rutas_generadas) + 2,
            str(rutas_generadas[0]),
            str(ruta_ficha),
            str(ruta_guia),
            id_str,
        ))

        for r in rutas_generadas:
            print(f"      ├── 🖼️ {r.name:<26} (1200x1200 px · Fondo #FFFFFF · Escala 90%)")
        print(f"      ├── 🛡️ {ruta_ficha.name:<26} (Cero SKU · Etiquetas auto-ajustadas · B2C)")
        print(f"      └── 📏 {ruta_guia.name:<26} (Tabla Adaptativa Completa 1 o 2 Columnas)\n")

    conn.commit()
    conn.close()
    print("=" * 88)
    print(f"🎉 ¡LISTO! Revisa las nuevas guías de tallas completas en: {DIR_CATALOGO.resolve()}")
    print("=" * 88)


if __name__ == "__main__":
    import math
    ejecutar_estudio_fotografico()