"""
=========================================================================================
PASO 6: ESTUDIO FOTOGRÁFICO B2C (DISEÑO PREMIUM COMPLETO SIN 'CAJA' NI 'ENVÍO HOY')
=========================================================================================
Conserva el diseño visual completo de alta conversión (sello dorado, checks verdes,
recuadros azules de tallas y tabla doble con semáforo verde), eliminando al 100%
las palabras "Caja", "Envío hoy" e "Inmediato".
=========================================================================================
"""
from __future__ import annotations

import argparse
import re
import sqlite3
from io import BytesIO
from pathlib import Path

from curl_cffi import requests as http
from PIL import Image, ImageChops, ImageDraw, ImageEnhance, ImageFont

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


def limpiar_titulo_visual(nombre_crudo: str) -> str:
    sin_sku = re.sub(r"\b\d{5,8}\b", "", str(nombre_crudo))
    sin_par = re.sub(r"[()\[\]/]", " ", sin_sku)
    palabras: list[str] = []
    for p in sin_par.split():
        if p.lower() not in [x.lower() for x in palabras]:
            palabras.append(p)
    limpio = " ".join(palabras).upper().strip()
    return limpio[:38] if limpio else "CALZADO SELECCIONADO"


def recortar_bordes_blancos(img: Image.Image) -> Image.Image:
    fondo = Image.new("RGB", img.size, (255, 255, 255))
    diff = ImageChops.difference(img.convert("RGB"), fondo)
    diff = ImageChops.add(diff, diff, 2.0, -25)
    bbox = diff.getbbox()
    return img.crop(bbox) if bbox else img


def obtener_imagen_base(id_ps: str, url_img: str | None) -> Image.Image | None:
    DIR_ORIGEN.mkdir(parents=True, exist_ok=True)

    for ext in (".jpg", ".jpeg", ".png", ".webp"):
        cand = DIR_ORIGEN / f"{id_ps}{ext}"
        if cand.exists():
            try:
                return Image.open(cand).convert("RGB")
            except Exception:
                pass

    portada_existente = DIR_DESTINO / str(id_ps) / "01_portada_blanca.jpg"
    if portada_existente.exists():
        try:
            return Image.open(portada_existente).convert("RGB")
        except Exception:
            pass

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
    copia = ImageEnhance.Sharpness(copia).enhance(1.14)

    pos_x = (1200 - copia.width) // 2
    pos_y = (1200 - copia.height) // 2
    lienzo.paste(copia, (pos_x, pos_y))
    lienzo.save(ruta_salida, "JPEG", quality=93, optimize=True)


def generar_03_ficha_beneficios(
    zapato_limpio: Image.Image,
    nombre_modelo: str,
    tallas_disponibles: list[float],
    ruta_salida: Path,
) -> None:
    """
    Mantiene el diseño visual completo (encabezado marino con línea dorada,
    4 tarjetas con check verde y barra inferior de tallas azules),
    pero SIN la palabra 'CAJA' y SIN 'ENVÍO HOY / INMEDIATO'.
    """
    lienzo = Image.new("RGB", LIENZO_ML, (241, 245, 249))
    draw = ImageDraw.Draw(lienzo)

    # 1. Encabezado Marino + Línea Dorada + Sello Dorado
    draw.rounded_rectangle([(20, 20), (1180, 165)], radius=24, fill=(13, 27, 46))
    draw.rectangle([(20, 157), (1180, 165)], fill=(245, 158, 11))

    titulo_limpio = limpiar_titulo_visual(nombre_modelo)
    f_tit = cargar_fuente(36, negrita=True)
    f_sub = cargar_fuente(20, negrita=True)
    draw.text((55, 45), titulo_limpio, fill=(255, 255, 255), font=f_tit)
    draw.text(
        (55, 102),
        "CALZADO 100% NUEVO Y ORIGINAL  ·  GARANTÍA DIRECTA",
        fill=(148, 163, 184),
        font=f_sub,
    )

    # Sello dorado superior derecho
    draw.rounded_rectangle([(855, 48), (1145, 98)], radius=22, fill=(245, 158, 11))
    f_sello = cargar_fuente(20, negrita=True)
    draw.text((882, 61), "CALIDAD GARANTIZADA", fill=(13, 27, 46), font=f_sello)

    # 2. Tarjeta Izquierda con Foto y Píldoras Honestras
    draw.rounded_rectangle(
        [(35, 190), (555, 885)],
        radius=26,
        fill=(255, 255, 255),
        outline=(219, 234, 254),
        width=3,
    )
    draw.ellipse([(105, 315), (485, 695)], fill=(241, 245, 249))

    # Píldora superior verde (Sin "Inmediato")
    draw.rounded_rectangle(
        [(95, 215), (495, 265)],
        radius=22,
        fill=(220, 252, 231),
        outline=(34, 197, 94),
        width=2,
    )
    f_pil = cargar_fuente(20, negrita=True)
    draw.text((150, 228), "ENVÍO SEGURO DESDE CDMX", fill=(21, 128, 61), font=f_pil)

    zapato_mini = zapato_limpio.copy()
    zapato_mini.thumbnail((450, 480), Image.Resampling.LANCZOS)
    zx = 35 + (520 - zapato_mini.width) // 2
    zy = 290 + (490 - zapato_mini.height) // 2
    lienzo.paste(zapato_mini, (zx, zy))

    # Píldora inferior azul
    draw.rounded_rectangle([(65, 810), (525, 860)], radius=20, fill=(219, 234, 254))
    f_pil_b = cargar_fuente(19, negrita=True)
    draw.text((95, 824), "DISEÑO CÓMODO, LIGERO Y RESISTENTE", fill=(29, 78, 216), font=f_pil_b)

    # 3. Columna Derecha: 4 Tarjetas con Check Verde (Sin "Caja" ni "Inmediato")
    tarjetas = [
        (
            "CALZADO 100% NUEVO Y ORIGINAL",
            "Recibe tu calzado totalmente nuevo, verificado",
            "par por par antes de su despacho.",
        ),
        (
            "EMPAQUE DE ALTA PROTECCIÓN",
            "Acondicionamos tu pedido con material protector",
            "de alta seguridad y guía de seguimiento.",
        ),
        (
            "MÁXIMA COMODIDAD DIARIA",
            "Plantilla suave y suela antiderrapante ideal",
            "para brindar confort en largas jornadas.",
        ),
        (
            "COMPRA 100% PROTEGIDA",
            "Si la talla no te queda, tu cambio o devolución",
            "es sencillo, seguro y respaldado por la plataforma.",
        ),
    ]

    f_card_t = cargar_fuente(23, negrita=True)
    f_card_d = cargar_fuente(19, negrita=False)
    f_check = cargar_fuente(24, negrita=True)

    y_c = 190
    for tit_c, lin1, lin2 in tarjetas:
        draw.rounded_rectangle(
            [(585, y_c), (1165, y_c + 158)],
            radius=20,
            fill=(255, 255, 255),
            outline=(203, 213, 225),
            width=2,
        )
        draw.rounded_rectangle([(585, y_c), (597, y_c + 158)], radius=6, fill=(29, 78, 216))
        # Círculo verde con check
        draw.ellipse([(615, y_c + 52), (665, y_c + 102)], fill=(22, 163, 74))
        draw.text((629, y_c + 61), "✓", fill=(255, 255, 255), font=f_check)

        draw.text((685, y_c + 24), tit_c, fill=(13, 27, 46), font=f_card_t)
        draw.text((685, y_c + 66), lin1, fill=(71, 85, 105), font=f_card_d)
        draw.text((685, y_c + 96), lin2, fill=(71, 85, 105), font=f_card_d)
        y_c += 178

    # 4. Barra Inferior de Tallas (Sin "ENVÍO HOY")
    draw.rounded_rectangle(
        [(35, 905), (1165, 1175)],
        radius=22,
        fill=(255, 255, 255),
        outline=(203, 213, 225),
        width=2,
    )
    draw.rounded_rectangle([(35, 905), (1165, 958)], radius=18, fill=(23, 37, 84))
    f_bar = cargar_fuente(22, negrita=True)
    draw.text(
        (310, 920),
        "TALLAS NACIONALES (MX) DISPONIBLES EN STOCK:",
        fill=(255, 255, 255),
        font=f_bar,
    )

    tallas_mostrar = tallas_disponibles[:8] if tallas_disponibles else [22.0, 23.0, 24.0, 25.0, 26.0, 27.0]
    ancho_box = 112
    espacio = 16
    total_w = len(tallas_mostrar) * ancho_box + (len(tallas_mostrar) - 1) * espacio
    x_talla = (1200 - total_w) // 2
    f_num = cargar_fuente(26, negrita=True)
    f_est = cargar_fuente(16, negrita=False)

    for t_val in tallas_mostrar:
        draw.rounded_rectangle(
            [(x_talla, 975), (x_talla + ancho_box, 1090)],
            radius=14,
            fill=(37, 99, 235),
        )
        txt_t = f"{t_val:.1f}" if not float(t_val).is_integer() else f"{int(t_val)}.0"
        draw.text((x_talla + 24, 990), txt_t, fill=(255, 255, 255), font=f_num)
        draw.text((x_talla + 22, 1042), "En stock", fill=(219, 234, 254), font=f_est)
        x_talla += ancho_box + espacio

    # Aviso inferior hacia la foto 4
    draw.rounded_rectangle([(65, 1110), (1135, 1158)], radius=14, fill=(239, 246, 255))
    draw.ellipse([(82, 1119), (112, 1149)], fill=(37, 99, 235))
    draw.text((93, 1121), "i", fill=(255, 255, 255), font=cargar_fuente(20, negrita=True))
    draw.text(
        (128, 1123),
        "¿Duda con tu número? Revisa la tabla en centímetros en la siguiente foto antes de comprar.",
        fill=(30, 58, 138),
        font=cargar_fuente(19, negrita=True),
    )

    lienzo.save(ruta_salida, "JPEG", quality=93, optimize=True)


def generar_04_guia_tallas(tallas_disponibles: list[float], ruta_salida: Path) -> None:
    """
    Tabla doble de equivalencias en CM con semáforo verde y 3 pasos abajo,
    cambiando 'Talla lista para envío hoy' por 'Talla disponible en stock'.
    """
    lienzo = Image.new("RGB", LIENZO_ML, (241, 245, 249))
    draw = ImageDraw.Draw(lienzo)

    # Encabezado Marino + Línea Dorada
    draw.rounded_rectangle([(20, 20), (1180, 160)], radius=24, fill=(13, 27, 46))
    draw.rectangle([(20, 152), (1180, 160)], fill=(245, 158, 11))

    f_tit = cargar_fuente(38, negrita=True)
    f_sub = cargar_fuente(20, negrita=True)
    draw.text((135, 42), "GUÍA DE TALLAS EN CENTÍMETROS (MÉXICO)", fill=(255, 255, 255), font=f_tit)
    draw.text(
        (260, 100),
        "TABLA COMPLETA DE EQUIVALENCIAS  ·  HORMA NACIONAL EXACTA",
        fill=(148, 163, 184),
        font=f_sub,
    )

    # Determinar rango de tallas (Infantil vs Juvenil/Adulto)
    min_t = min(tallas_disponibles) if tallas_disponibles else 22.0
    if min_t < 21.0:
        rango_base = [15.0 + 0.5 * i for i in range(16)]  # 15.0 a 22.5 MX
    else:
        rango_base = [21.0 + 0.5 * i for i in range(16)]  # 21.0 a 28.5 MX

    set_activas = {round(float(x), 1) for x in tallas_disponibles}

    # Dos bloques de tabla lado a lado (8 filas cada uno)
    bloques = [(35, 585, rango_base[:8]), (615, 1165, rango_base[8:16])]
    f_th = cargar_fuente(19, negrita=True)
    f_td_b = cargar_fuente(20, negrita=True)
    f_td = cargar_fuente(19, negrita=False)

    for x1, x2, sub_lista in bloques:
        draw.rounded_rectangle(
            [(x1, 185), (x2, 825)],
            radius=20,
            fill=(255, 255, 255),
            outline=(203, 213, 225),
            width=2,
        )
        draw.rounded_rectangle([(x1, 185), (x2, 242)], radius=16, fill=(23, 37, 84))
        draw.text((x1 + 35, 203), "TALLA MX", fill=(255, 255, 255), font=f_th)
        draw.text((x1 + 205, 203), "TU PIE (CM)", fill=(255, 255, 255), font=f_th)
        draw.text((x1 + 395, 203), "PLANTILLA", fill=(255, 255, 255), font=f_th)

        y_r = 250
        for idx, t_mx in enumerate(sub_lista):
            activa = round(t_mx, 1) in set_activas
            bg = (220, 252, 231) if activa else ((255, 255, 255) if idx % 2 == 0 else (248, 250, 252))
            draw.rectangle([(x1 + 8, y_r), (x2 - 8, y_r + 68)], fill=bg)

            if activa:
                draw.ellipse([(x1 + 18, y_r + 26), (x1 + 32, y_r + 40)], fill=(22, 163, 74))

            color_t = (21, 128, 61) if activa else (15, 23, 42)
            draw.text((x1 + 42, y_r + 21), f"{t_mx:.1f} MX", fill=color_t, font=f_td_b)
            draw.text(
                (x1 + 195, y_r + 22),
                f"{t_mx - 0.3:.1f} a {t_mx:.1f} cm",
                fill=(71, 85, 105),
                font=f_td,
            )
            draw.text((x1 + 405, y_r + 21), f"{t_mx + 0.5:.1f} cm", fill=(37, 99, 235), font=f_td_b)
            y_r += 70

    # Sección inferior: 3 pasos (Sin "envío hoy")
    f_pasos_tit = cargar_fuente(22, negrita=True)
    draw.text(
        (95, 852),
        "¿CÓMO MEDIR TU PIE EN CASA EN 3 SIMPLES PASOS?",
        fill=(13, 27, 46),
        font=f_pasos_tit,
    )
    draw.ellipse([(680, 856), (698, 874)], fill=(22, 163, 74))
    draw.text(
        (706, 852),
        "(= Talla disponible en stock)",
        fill=(21, 128, 61),
        font=f_pasos_tit,
    )

    pasos = [
        (
            "1",
            "APOYA EL TALÓN",
            "Coloca una hoja de papel en el",
            "piso pegada a la pared y pisa",
            "firme con el talón tocando la pared.",
        ),
        (
            "2",
            "HAZ UNA MARCA",
            "Con un lápiz derecho, traza",
            "una pequeña línea justo donde",
            "termina tu dedo más largo.",
        ),
        (
            "3",
            "MIDE CON REGLA",
            "Mide en centímetros desde el",
            "borde de la hoja hasta la marca",
            "y busca tu talla en la tabla.",
        ),
    ]

    x_p = 35
    for num_p, tit_p, l1, l2, l3 in pasos:
        draw.rounded_rectangle(
            [(x_p, 895), (x_p + 360, 1170)],
            radius=18,
            fill=(255, 255, 255),
            outline=(191, 219, 254),
            width=2,
        )
        draw.rounded_rectangle([(x_p, 895), (x_p + 360, 907)], radius=6, fill=(37, 99, 235))
        draw.ellipse([(x_p + 22, 922), (x_p + 66, 966)], fill=(37, 99, 235))
        draw.text((x_p + 37, 930), num_p, fill=(255, 255, 255), font=cargar_fuente(24, negrita=True))
        draw.text((x_p + 80, 932), tit_p, fill=(13, 27, 46), font=cargar_fuente(22, negrita=True))

        f_p_txt = cargar_fuente(18, negrita=False)
        draw.text((x_p + 24, 990), l1, fill=(71, 85, 105), font=f_p_txt)
        draw.text((x_p + 24, 1020), l2, fill=(71, 85, 105), font=f_p_txt)
        draw.text((x_p + 24, 1050), l3, fill=(71, 85, 105), font=f_p_txt)
        x_p += 385

    lienzo.save(ruta_salida, "JPEG", quality=93, optimize=True)


def ejecutar_estudio_fotografico(regenerar: bool = False) -> None:
    print("=" * 88)
    print("📸 PASO 6: REGENERANDO ESTUDIO FOTOGRÁFICO HD (SIN 'CAJA' NI 'ENVÍO HOY')")
    print("=" * 88)

    if not RUTA_BD.exists():
        print(f"❌ No se encontró la base de datos en {RUTA_BD}.")
        return

    conn = sqlite3.connect(RUTA_BD)
    cols = [c[1] for c in conn.execute("PRAGMA table_info(zapatos_candidatos)").fetchall()]
    col_img = "url_imagen" if "url_imagen" in cols else ("imagen_url" if "imagen_url" in cols else None)

    query = f"SELECT id_ps, nombre_modelo, {col_img if col_img else 'NULL'} FROM zapatos_candidatos"
    mapa_bd: dict[str, tuple[str, str | None]] = {}
    for id_ps, nombre, url_img in conn.execute(query).fetchall():
        mapa_bd[str(id_ps)] = (str(nombre or f"Modelo {id_ps}"), url_img)

    # Incluir también cualquier carpeta que ya exista en data/catalogo_listo (ej. 1250365)
    if DIR_DESTINO.exists():
        for carpeta in DIR_DESTINO.iterdir():
            if carpeta.is_dir() and carpeta.name not in mapa_bd:
                mapa_bd[carpeta.name] = (f"Calzado {carpeta.name}", None)

    procesados = 0
    for id_str, (nombre, url_img) in sorted(mapa_bd.items()):
        carpeta_sku = DIR_DESTINO / id_str
        f1 = carpeta_sku / "01_portada_blanca.jpg"
        f2 = carpeta_sku / "02_angulo_galeria.jpg"
        f3 = carpeta_sku / "03_ficha_beneficios.jpg"
        f4 = carpeta_sku / "04_guia_tallas_cm.jpg"

        # Solo procesar SKUs que ya tienen carpeta en catalogo_listo o imagen base
        if not carpeta_sku.exists() and not (DIR_ORIGEN / f"{id_str}.jpg").exists():
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

        tallas_rows = conn.execute(
            """
            SELECT DISTINCT talla
            FROM historial_stock_21_dias
            WHERE id_ps = ? AND stock_seguro_publicable > 0
            ORDER BY CAST(talla AS REAL) ASC
            """,
            (id_str,),
        ).fetchall()

        nums_tallas = sorted(
            {
                float(re.findall(r"\d+(?:\.\d+)?", str(r[0]))[0])
                for r in tallas_rows
                if re.findall(r"\d+(?:\.\d+)?", str(r[0]))
            }
        )

        generar_03_ficha_beneficios(zapato_recortado, nombre, nums_tallas, f3)
        generar_04_guia_tallas(nums_tallas, f4)

        procesados += 1
        print(f"✅ [{id_str}] Láminas 03 y 04 actualizadas -> {nombre[:40]}")

    conn.close()
    print("=" * 88)
    print(f"🎉 Total actualizado: {procesados} carruseles limpios en `{DIR_DESTINO}`.")
    print("=" * 88)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Estudio Fotográfico B2C Pillow")
    parser.add_argument("--regenerar", action="store_true", help="Regenera todas las láminas")
    args = parser.parse_args()
    ejecutar_estudio_fotografico(regenerar=args.regenerar)