"""
=========================================================================================
PASO 6: ESTUDIO FOTOGRÁFICO B2C (PILLOW HD 1200x1200) - TEXTOS HONESTOS ANTI-RECLAMOS
=========================================================================================
CAMBIOS APLICADOS:
1. Cero menciones a la palabra "Caja" (sustituido por "Empaque de Alta Protección").
2. Cero promesas de "Sale hoy" / "Envío hoy mismo" (sustituido por "Envío Seguro con
   Guía de Rastreo" y "Compra Protegida").
3. Soporte de bandera `--regenerar` para actualizar al instante las láminas 03 y 04
   de todos los SKUs existentes usando la portada ya recortada si no hay imagen cruda.
=========================================================================================
"""
from __future__ import annotations

import argparse
import re
import sqlite3
from io import BytesIO
from pathlib import Path

from curl_cffi import requests as http
from PIL import Image, ImageChops, ImageDraw, ImageEnhance, ImageFilter, ImageFont

RUTA_BD = Path("data/zapateria_vallejo.db")
DIR_ORIGEN = Path("data/imagenes_origen")
DIR_DESTINO = Path("data/catalogo_listo")
LIENZO_ML = (1200, 1200)


def cargar_fuente(tamano: int, negrita: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidatas = (
        [
            "C:/Windows/Fonts/arialbd.ttf",
            "C:/Windows/Fonts/segoeuib.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        ]
        if negrita
        else [
            "C:/Windows/Fonts/arial.ttf",
            "C:/Windows/Fonts/segoeui.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        ]
    )
    for ruta in candidatas:
        if Path(ruta).exists():
            try:
                return ImageFont.truetype(ruta, tamano)
            except Exception:
                continue
    return ImageFont.load_default()


def recortar_bordes_blancos(img: Image.Image) -> Image.Image:
    fondo = Image.new("RGB", img.size, (255, 255, 255))
    diff = ImageChops.difference(img.convert("RGB"), fondo)
    diff = ImageChops.add(diff, diff, 2.0, -25)
    bbox = diff.getbbox()
    return img.crop(bbox) if bbox else img


def obtener_imagen_base(id_ps: str, url_img: str | None) -> Image.Image | None:
    DIR_ORIGEN.mkdir(parents=True, exist_ok=True)

    # 1. Buscar en imagenes_origen
    for ext in (".jpg", ".jpeg", ".png", ".webp"):
        cand = DIR_ORIGEN / f"{id_ps}{ext}"
        if cand.exists():
            try:
                return Image.open(cand).convert("RGB")
            except Exception:
                pass

    # 2. Reutilizar 01_portada_blanca.jpg si ya fue generada antes (ideal para GitHub Actions)
    portada_existente = DIR_DESTINO / str(id_ps) / "01_portada_blanca.jpg"
    if portada_existente.exists():
        try:
            return Image.open(portada_existente).convert("RGB")
        except Exception:
            pass

    # 3. Descargar desde la URL si está disponible
    if url_img and str(url_img).startswith("http"):
        try:
            r = http.get(str(url_img), impersonate="chrome120", timeout=15)
            if r.status_code == 200:
                img = Image.open(BytesIO(r.content)).convert("RGB")
                img.save(DIR_ORIGEN / f"{id_ps}.jpg", "JPEG", quality=92)
                return img
        except Exception:
            pass

    return None


def generar_01_portada_blanca(zapato_limpio: Image.Image, ruta_salida: Path) -> None:
    lienzo = Image.new("RGB", LIENZO_ML, (255, 255, 255))
    copia = zapato_limpio.copy()
    copia.thumbnail((1020, 1020), Image.Resampling.LANCZOS)
    copia = ImageEnhance.Contrast(copia).enhance(1.04)
    copia = ImageEnhance.Sharpness(copia).enhance(1.08)

    pos_x = (1200 - copia.width) // 2
    pos_y = (1200 - copia.height) // 2
    lienzo.paste(copia, (pos_x, pos_y))
    lienzo.save(ruta_salida, "JPEG", quality=94, optimize=True)


def generar_02_angulo_detalle(zapato_limpio: Image.Image, ruta_salida: Path) -> None:
    lienzo = Image.new("RGB", LIENZO_ML, (255, 255, 255))
    copia = zapato_limpio.copy()
    copia.thumbnail((1120, 1120), Image.Resampling.LANCZOS)
    copia = ImageEnhance.Sharpness(copia).enhance(1.15)

    pos_x = (1200 - copia.width) // 2
    pos_y = (1200 - copia.height) // 2
    lienzo.paste(copia, (pos_x, pos_y))
    lienzo.save(ruta_salida, "JPEG", quality=93, optimize=True)


def generar_03_ficha_beneficios(zapato_limpio: Image.Image, ruta_salida: Path) -> None:
    """
    Infografía 1200x1200 SIN la palabra 'Caja' y SIN promesas de 'Sale hoy'.
    Enfocada en calidad, confort, empaque protegido y envío con seguimiento.
    """
    lienzo = Image.new("RGB", LIENZO_ML, (248, 250, 252))
    draw = ImageDraw.Draw(lienzo)

    # Encabezado oscuro elegante
    draw.rectangle([(0, 0), (1200, 175)], fill=(15, 23, 42))
    f_titulo = cargar_fuente(42, negrita=True)
    f_sub = cargar_fuente(24, negrita=False)
    draw.text((60, 42), "CALIDAD Y CONFORT GARANTIZADO", fill=(255, 255, 255), font=f_titulo)
    draw.text(
        (60, 105),
        "Calzado nuevo seleccionado · Revisión física par por par",
        fill=(148, 163, 184),
        font=f_sub,
    )

    # Tarjeta blanca izquierda para lucir el calzado
    draw.rounded_rectangle([(50, 220), (540, 1040)], radius=28, fill=(255, 255, 255), outline=(226, 232, 240), width=3)
    zapato_mini = zapato_limpio.copy()
    zapato_mini.thumbnail((430, 720), Image.Resampling.LANCZOS)
    zx = 50 + (490 - zapato_mini.width) // 2
    zy = 220 + (820 - zapato_mini.height) // 2
    lienzo.paste(zapato_mini, (zx, zy))

    # 4 Bloques de Beneficios Honestos (Cero "caja", cero "sale hoy")
    beneficios = [
        (
            "PRODUCTO 100% NUEVO Y VERIFICADO",
            "Inspeccionamos costuras, suela y acabados antes de despachar.",
        ),
        (
            "DISEÑO CÓMODO PARA USO DIARIO",
            "Horma confortable y materiales resistentes de gran durabilidad.",
        ),
        (
            "EMPAQUE DE ALTA PROTECCIÓN",
            "Acondicionado con material protector para cuidar tu calzado en ruta.",
        ),
        (
            "ENVÍO SEGURO CON RASTREO",
            "Despacho ágil por Mercado Envíos con guía de seguimiento paso a paso.",
        ),
    ]

    f_ben_tit = cargar_fuente(24, negrita=True)
    f_ben_desc = cargar_fuente(19, negrita=False)

    y_cursor = 220
    for titulo_b, desc_b in beneficios:
        draw.rounded_rectangle(
            [(580, y_cursor), (1150, y_cursor + 180)],
            radius=20,
            fill=(255, 255, 255),
            outline=(203, 213, 225),
            width=2,
        )
        # Barra lateral de acento azul
        draw.rounded_rectangle([(580, y_cursor), (596, y_cursor + 180)], radius=8, fill=(37, 99, 235))
        draw.text((620, y_cursor + 38), titulo_b, fill=(15, 23, 42), font=f_ben_tit)
        draw.text((620, y_cursor + 88), desc_b, fill=(71, 85, 105), font=f_ben_desc)
        y_cursor += 210

    # Pie de página informativo y transparente
    draw.rectangle([(0, 1090), (1200, 1200)], fill=(241, 245, 249))
    f_pie = cargar_fuente(22, negrita=True)
    draw.text(
        (60, 1132),
        "COMPRA PROTEGIDA · GARANTÍA DE SATISFACCIÓN DE 30 DÍAS",
        fill=(30, 41, 59),
        font=f_pie,
    )

    lienzo.save(ruta_salida, "JPEG", quality=93, optimize=True)


def generar_04_guia_tallas(tallas_disponibles: list[str], ruta_salida: Path) -> None:
    """
    Guía de tallas en centímetros (MX) sin promesas de horario ni referencias a caja.
    """
    lienzo = Image.new("RGB", LIENZO_ML, (255, 255, 255))
    draw = ImageDraw.Draw(lienzo)

    # Encabezado
    draw.rectangle([(0, 0), (1200, 170)], fill=(30, 41, 59))
    f_tit = cargar_fuente(42, negrita=True)
    f_sub = cargar_fuente(24, negrita=False)
    draw.text((60, 40), "GUÍA DE TALLAS NACIONAL (MÉXICO)", fill=(255, 255, 255), font=f_tit)
    draw.text(
        (60, 102),
        "Las tallas están expresadas en centímetros (CM) exactos del pie",
        fill=(203, 213, 225),
        font=f_sub,
    )

    # Extraer números de talla limpios
    nums = sorted(
        {
            float(re.findall(r"\d+(?:\.\d+)?", str(t))[0])
            for t in tallas_disponibles
            if re.findall(r"\d+(?:\.\d+)?", str(t))
        }
    )
    if not nums:
        nums = [22.0, 23.0, 24.0, 25.0, 26.0, 27.0]

    # Encabezados de tabla
    draw.rounded_rectangle([(80, 220), (1120, 300)], radius=14, fill=(241, 245, 249))
    f_th = cargar_fuente(26, negrita=True)
    draw.text((180, 245), "TALLA MÉXICO (MX)", fill=(15, 23, 42), font=f_th)
    draw.text((680, 245), "LARGO RECOMENDADO DEL PIE", fill=(15, 23, 42), font=f_th)

    f_td = cargar_fuente(25, negrita=False)
    max_filas = min(len(nums), 10)
    alto_fila = 62
    y_fila = 315

    for idx, n in enumerate(nums[:max_filas]):
        bg = (255, 255, 255) if idx % 2 == 0 else (248, 250, 252)
        draw.rectangle([(80, y_fila), (1120, y_fila + alto_fila)], fill=bg, outline=(226, 232, 240))
        etiqueta_mx = f"{int(n)} MX" if n.is_integer() else f"{n:.1f} MX"
        etiqueta_cm = f"{n:.1f} cm"
        draw.text((250, y_fila + 16), etiqueta_mx, fill=(15, 23, 42), font=f_td)
        draw.text((790, y_fila + 16), etiqueta_cm, fill=(37, 99, 235), font=f_td)
        y_fila += alto_fila

    # Recuadro de consejo práctico de medición
    y_tip = max(y_fila + 30, 950)
    draw.rounded_rectangle([(80, y_tip), (1120, 1140)], radius=18, fill=(239, 246, 255), outline=(191, 219, 254), width=2)
    f_tip_t = cargar_fuente(24, negrita=True)
    f_tip_d = cargar_fuente(20, negrita=False)
    draw.text((115, y_tip + 25), "¿Cómo confirmar tu talla ideal en casa?", fill=(30, 64, 175), font=f_tip_t)
    draw.text(
        (115, y_tip + 70),
        "Coloca tu pie sobre una hoja de papel pegado a la pared, marca desde el talón hasta el dedo",
        fill=(30, 41, 59),
        font=f_tip_d,
    )
    draw.text(
        (115, y_tip + 105),
        "más largo y mide los centímetros con una regla. Elige la talla MX equivalente a tus centímetros.",
        fill=(30, 41, 59),
        font=f_tip_d,
    )

    lienzo.save(ruta_salida, "JPEG", quality=93, optimize=True)


def ejecutar_estudio_fotografico(regenerar: bool = False) -> None:
    print("=" * 88)
    print("📸 PASO 6: ESTUDIO FOTOGRÁFICO B2C (SIN 'CAJA' Y SIN 'SALE HOY')")
    print("=" * 88)

    if not RUTA_BD.exists():
        print(f"❌ No se encontró la base de datos en {RUTA_BD}.")
        return

    conn = sqlite3.connect(RUTA_BD)
    cols = [c[1] for c in conn.execute("PRAGMA table_info(zapatos_candidatos)").fetchall()]
    col_img = "url_imagen" if "url_imagen" in cols else ("imagen_url" if "imagen_url" in cols else None)

    query = f"SELECT id_ps, nombre_modelo, {col_img if col_img else 'NULL'} FROM zapatos_candidatos"
    candidatos = conn.execute(query).fetchall()

    procesados = 0
    for id_ps, nombre, url_img in candidatos:
        id_str = str(id_ps)
        carpeta_sku = DIR_DESTINO / id_str
        f1 = carpeta_sku / "01_portada_blanca.jpg"
        f2 = carpeta_sku / "02_angulo_galeria.jpg"
        f3 = carpeta_sku / "03_ficha_beneficios.jpg"
        f4 = carpeta_sku / "04_guia_tallas_cm.jpg"

        if not regenerar and f1.exists() and f3.exists() and f4.exists():
            continue

        img_base = obtener_imagen_base(id_str, url_img)
        if img_base is None:
            continue

        carpeta_sku.mkdir(parents=True, exist_ok=True)
        zapato_recortado = recortar_bordes_blancos(img_base)

        if not f1.exists() or regenerar:
            generar_01_portada_blanca(zapato_recortado, f1)
        if not f2.exists() or regenerar:
            generar_02_angulo_detalle(zapato_recortado, f2)

        # Regenerar siempre la ficha de beneficios y guía de tallas cuando se pide --regenerar
        generar_03_ficha_beneficios(zapato_recortado, f3)

        tallas_rows = conn.execute(
            """
            SELECT DISTINCT talla
            FROM historial_stock_21_dias
            WHERE id_ps = ? AND stock_seguro_publicable > 0
            """,
            (id_str,),
        ).fetchall()
        lista_tallas = [str(r[0]) for r in tallas_rows]
        generar_04_guia_tallas(lista_tallas, f4)

        procesados += 1
        print(f"✅ [{id_str}] Carrusel actualizado sin 'caja' ni 'sale hoy' -> {nombre[:42]}")

    conn.close()
    print("=" * 88)
    print(f"🎉 Estudio finalizado: {procesados} carruseles generados/actualizados en `{DIR_DESTINO}`.")
    print("=" * 88)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Estudio Fotográfico B2C Pillow")
    parser.add_argument(
        "--regenerar",
        action="store_true",
        help="Regenera todas las fichas de beneficios y guías de tallas aunque ya existan",
    )
    args = parser.parse_args()
    ejecutar_estudio_fotografico(regenerar=args.regenerar)