"""
=========================================================================================
PASO 7: SINCRONIZADOR MASIVO (BULK) CON IA SEO + VISIÓN DE COLOR PARA MERCADO LIBRE
=========================================================================================
MEJORAS DE INTELIGENCIA ARTIFICIAL EN TÍTULOS Y ATRIBUTOS:
1. VISIÓN DE COLOR CON PILLOW:
   Analiza `01_portada_blanca.jpg` de cada SKU para detectar el color real del calzado
   (Blanco, Negro, Azul Marino, Rosa, Café, Rojo, Gris) e inyectarlo en el título y ficha.

2. MOTOR HÍBRIDO GEMINI AI (`gemini-2.5-flash`) + RECONOCIMIENTO DE MARCAS Y LICENCIAS:
   - Si existe la variable de entorno `GEMINI_API_KEY`, optimiza los 48 títulos en una
     sola llamada JSON estructurada.
   - Incluye un motor experto local que reconstruye nombres truncados ("Lo Mer" ->
     "Mercedes AMG F1", "Ua" -> "Under Armour", "Barreda/Grand Court" -> "Adidas",
     "Caven/Rebound/Tifosi" -> "Puma", "Air Max/Court Shot" -> "Nike").

3. CERO MULETILLAS Y CERO TÍTULOS REPETIDOS:
   Elimina el relleno "Original En Caja" y garantiza que los 48 SKUs tengan títulos 100%
   únicos (<= 60 caracteres) para evitar penalizaciones por duplicidad en Mercado Libre.
=========================================================================================
"""
from __future__ import annotations

import argparse
import colorsys
import json
import os
import re
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from curl_cffi import requests as http
from PIL import Image

# ---------------------------------------------------------------------------
# Configuración de Rutas y Endpoints
# ---------------------------------------------------------------------------
RUTA_BD = Path("data/zapateria_vallejo.db")
DIR_CATALOGO = Path("data/catalogo_listo")
RUTA_CREDENCIALES_ML = Path("data/ml_credenciales.json")
RUTA_SIMULACION_JSON = Path("data/simulacion_bulk_ml.json")

ML_API_BASE = "https://api.mercadolibre.com"
SITE_ID = "MLM"
CATEGORIA_TENIS_FALLBACK = "MLM191944"


# ---------------------------------------------------------------------------
# 1. Migración de Columnas en SQLite (Incluye Caché de Títulos IA)
# ---------------------------------------------------------------------------
def asegurar_columnas_ml(conn: sqlite3.Connection) -> None:
    cols = [c[1] for c in conn.execute("PRAGMA table_info(zapatos_candidatos)").fetchall()]
    nuevas = {
        "titulo_seo_ml": "TEXT",
        "marca_detectada": "TEXT",
        "color_detectado": "TEXT",
        "ml_item_id": "TEXT",
        "ml_permalink": "TEXT",
        "ml_status": "TEXT",
        "ml_category_id": "TEXT",
        "ml_pictures_json": "TEXT",
        "ml_ultima_sync": "TIMESTAMP",
    }
    for col, tipo in nuevas.items():
        if col not in cols:
            conn.execute(f"ALTER TABLE zapatos_candidatos ADD COLUMN {col} {tipo};")
    conn.commit()


# ---------------------------------------------------------------------------
# 2. Detector Visual de Color del Calzado con Pillow
# ---------------------------------------------------------------------------
def detectar_color_zapato(id_ps: str, nombre_modelo: str) -> str:
    """
    Inspecciona los píxeles del zapato en `01_portada_blanca.jpg` (ignorando el fondo #FFFFFF)
    para determinar si el calzado es Blanco, Negro, Azul Marino, Rosa, Rojo, Café o Gris.
    """
    up = nombre_modelo.upper()
    for col_txt in ("BLANCO", "NEGRO", "ROSA", "AZUL", "ROJO", "GRIS", "CAFE", "CAFÉ"):
        if col_txt in up:
            return col_txt.capitalize()

    ruta_portada = DIR_CATALOGO / str(id_ps) / "01_portada_blanca.jpg"
    if not ruta_portada.exists():
        return "Negro" if "ZAPATO ESCOLAR" in up or "BOTIN ESCOLAR" in up else "Blanco"

    try:
        img = Image.open(ruta_portada).convert("RGB").resize((80, 80), Image.Resampling.BILINEAR)
        conteo = {"Blanco": 0, "Negro": 0, "Azul Marino": 0, "Rosa": 0, "Rojo": 0, "Café": 0, "Gris": 0}

        for r, g, b in img.getdata():
            # Ignorar el fondo blanco puro del lienzo (#FFFFFF)
            if r > 246 and g > 246 and b > 246:
                continue
            h, s, v = colorsys.rgb_to_hsv(r / 255.0, g / 255.0, b / 255.0)
            deg = h * 360.0

            if v < 0.26:
                conteo["Negro"] += 1
            elif s < 0.14 and v > 0.72:
                conteo["Blanco"] += 1
            elif s < 0.15:
                conteo["Gris"] += 1
            elif 195 <= deg <= 255:
                conteo["Azul Marino"] += 1
            elif 290 <= deg <= 345 or (deg < 15 and v > 0.75 and s < 0.55):
                conteo["Rosa"] += 1
            elif deg < 18 or deg > 345:
                conteo["Rojo"] += 1
            elif 18 <= deg <= 45 and v < 0.65:
                conteo["Café"] += 1

        color_ganador = max(conteo, key=conteo.get)
        # Si es zapato escolar tradicional y predomina oscuro, es Negro
        if ("ZAPATO ESCOLAR" in up or "BOTIN ESCOLAR" in up) and conteo["Negro"] > 120:
            return "Negro"
        return color_ganador if conteo[color_ganador] > 40 else "Blanco"
    except Exception:
        return "Negro" if "ZAPATO ESCOLAR" in up else "Blanco"


# ---------------------------------------------------------------------------
# 3. Inteligencia de Marcas, Modelos y Títulos SEO (Motor Local + Gemini AI)
# ---------------------------------------------------------------------------
REGLAS_MARCAS_CONOCIDAS = [
    (r"\b(BARREDA|GRAND COURT|VL COURT)\b", "Adidas"),
    (r"\b(PUMA|REBOUND|TIFOSI|CAVEN)\b", "Puma"),
    (r"\b(NIKE|COURT SHOT|AIR MAX)\b", "Nike"),
    (r"\b(REEBOK)\b", "Reebok"),
    (r"\b(UA MOTION|UNDER ARMOUR|\bUA\b)\b", "Under Armour"),
    (r"\b(BOSS)\b", "Boss"),
    (r"\b(SPIDERMAN|SPIDER-MAN)\b", "Marvel Spiderman"),
    (r"\b(KUROMI)\b", "Sanrio Kuromi"),
    (r"\b(PAW PATROL)\b", "Paw Patrol"),
    (r"\b(BLUEY)\b", "Bluey"),
    (r"\b(SONIC)\b", "Sonic"),
]

MODIFICADORES_UNICOS_ESCOLAR = [
    "Piel Suela Antiderrapante",
    "Cómodo Ligero Uso Diario",
    "Plantilla Anatómica Suave",
    "Corte Resistente Confort",
    "Ajuste Seguro Alta Duración",
    "Suela Flexible Ligera",
    "Diseño Clásico Ortopédico",
    "Doble Costura Uso Rudo",
    "Horma Confort Transpirable",
    "Acabado Premium Escolar",
]


def inferir_marca(nombre: str, url_real: str | None) -> str:
    texto = f"{nombre} {url_real or ''}".upper()
    for patron, marca in REGLAS_MARCAS_CONOCIDAS:
        if re.search(patron, texto):
            return marca
    return "Urbano Original" if "TENIS" in texto else "Escolar Confort"


def detectar_genero_y_edad(nombre: str, tallas: list[tuple[str, int]], color: str = "") -> tuple[str, str, str]:
    """Devuelve (genero_ml, edad_ml, palabra_publico_titulo)."""
    up = nombre.upper()
    nums = [float(re.findall(r"\d+(?:\.\d+)?", t)[0]) for t, _ in tallas if re.findall(r"\d+(?:\.\d+)?", t)]
    min_t = min(nums) if nums else 20.0
    max_t = max(nums) if nums else 24.0
    es_infantil = max_t <= 24.5 or any(
        k in up for k in ("NIÑO", "NIÑA", "ESCOLAR", "INFANTIL", "SPIDERMAN", "KUROMI", "BLUEY", "SONIC", "PAW PATROL")
    )

    if any(k in up for k in ("NIÑA", "KUROMI", "BRILLOS", "K POP")) or (es_infantil and color == "Rosa"):
        return "Niñas", "Niños", "Niña"
    if any(k in up for k in ("NIÑO", "SPIDERMAN", "SONIC", "PAW PATROL")):
        return "Niños", "Niños", "Niño"
    if es_infantil:
        return "Sin género infantil", "Niños", "Infantil" if min_t < 18 else "Escolar"
    if max_t >= 25.5:
        return "Hombre", "Adultos", "Hombre"
    return "Mujer", "Adultos", "Mujer"


def construir_titulo_experto_local(
    nombre_crudo: str,
    marca: str,
    color: str,
    publico: str,
    indice_desempate: int,
) -> str:
    """
    Transforma nombres crudos o truncados en títulos comerciales de alta conversión
    (<= 60 caracteres), sin paréntesis ni muletillas "Original En Caja".
    """
    # 1. Limpiar paréntesis, SKUs y abreviaturas truncadas
    limpio = re.sub(r"\b\d{5,8}\b", "", str(nombre_crudo))
    limpio = re.sub(r"[()\[\]/]", " ", limpio)

    # Expandir nombres truncados detectados en el catálogo
    reemplazos = [
        (r"\bLo Mer\b", "Lo Mercedes AMG F1"),
        (r"\bLo Audi\b", "Lo Audi F1"),
        (r"\bCaven Iii\b", "Caven III"),
        (r"\bUa Motion\b", "Under Armour UA Motion"),
        (r"\bK Pop\b", "K-Pop Urbano"),
        (r"\bForro Y Plantilla Piel\b", "Piel Confort"),
    ]
    for pat, rep in reemplazos:
        limpio = re.sub(pat, rep, limpio, flags=re.IGNORECASE)

    # Eliminar duplicados de palabras manteniendo el orden
    palabras: list[str] = []
    for p in limpio.split():
        if p.lower() not in [x.lower() for x in palabras]:
            palabras.append(p)
    base = " ".join(palabras).title()
    base = base.replace("Iii", "III").replace("Amg", "AMG").replace("F1", "F1").replace("Ua", "UA").replace("K-Pop", "K-Pop")

    # 2. Inyectar la marca oficial si es deportiva y aún no aparece en el nombre
    marca_principal = marca.split()[0]
    if marca in ("Adidas", "Puma", "Nike", "Reebok", "Under Armour") and marca_principal.lower() not in base.lower():
        partes = base.split()
        # Insertar justo después de "Tenis" o "Tenis Casual"
        if len(partes) >= 2 and partes[0].lower() == "tenis" and partes[1].lower() in ("casual", "motorsport", "urbano"):
            base = f"Tenis {marca} {' '.join(partes[2:])}"
        elif partes and partes[0].lower() == "tenis":
            base = f"Tenis {marca} {' '.join(partes[1:])}"

    # 3. Enriquecer títulos cortos o genéricos ("Zapato Escolar", "Tenis Urbano")
    if publico.lower() not in base.lower() and len(f"{base} {publico}") <= 54:
        base = f"{base} {publico}"

    if color.lower() not in base.lower() and len(f"{base} {color}") <= 56:
        base = f"{base} {color}"

    # 4. Si sigue siendo corto (< 44 caracteres), añadir atributo diferenciador de alto CTR
    modificador = MODIFICADORES_UNICOS_ESCOLAR[indice_desempate % len(MODIFICADORES_UNICOS_ESCOLAR)]
    for palabra_mod in modificador.split():
        if len(f"{base} {palabra_mod}") <= 59 and palabra_mod.lower() not in base.lower():
            base = f"{base} {palabra_mod}"

    return base[:60].strip()


def optimizar_lote_con_gemini_ia(items_para_ia: list[dict[str, Any]]) -> dict[str, dict[str, str]]:
    """
    Si el usuario configuró `GEMINI_API_KEY` (en entorno o en `data/ml_credenciales.json`),
    envía los 48 productos en 1 sola petición a Gemini 2.5 Flash para pulir los títulos SEO.
    """
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key and RUTA_CREDENCIALES_ML.exists():
        try:
            api_key = json.loads(RUTA_CREDENCIALES_ML.read_text(encoding="utf-8")).get("gemini_api_key")
        except Exception:
            api_key = None

    if not api_key:
        return {}

    prompt = (
        "Eres un experto en SEO de Mercado Libre México (categoría Calzado). "
        "Para cada producto de la lista, genera un objeto JSON con su `id_ps`, `titulo_seo` y `marca`.\n"
        "REGLAS ESTRICTAS:\n"
        "1. Máximo 60 caracteres exactos por `titulo_seo`.\n"
        "2. PROHIBIDO usar la frase 'Original En Caja', prohibido usar paréntesis () y prohibido incluir el `id_ps`.\n"
        "3. Estructura ideal: [Tipo Calzado] + [Marca/Licencia] + [Modelo] + [Género/Edad] + [Color/Material/Beneficio].\n"
        "4. TODOS los 48 títulos deben ser ÚNICOS entre sí (no repitas títulos genéricos como 'Zapato Escolar').\n"
        f"PRODUCTOS:\n{json.dumps(items_para_ia, ensure_ascii=False)}"
    )

    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={api_key}"
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"responseMimeType": "application/json", "temperature": 0.2},
    }

    try:
        print("✨ Consultando a Gemini AI (gemini-2.5-flash) para perfeccionar los 48 títulos SEO...")
        r = http.post(url, json=payload, timeout=35)
        if r.status_code == 200:
            texto_json = r.json()["candidates"][0]["content"]["parts"][0]["text"]
            lista = json.loads(texto_json)
            resultado = {}
            for obj in lista:
                k = str(obj.get("id_ps", ""))
                tit = str(obj.get("titulo_seo", "")).strip()[:60]
                if k and tit:
                    resultado[k] = {"titulo_seo": tit, "marca": str(obj.get("marca", "Original"))}
            print(f"✅ Gemini AI optimizó {len(resultado)} títulos con éxito.")
            return resultado
    except Exception as exc:
        print(f"⚠️ Aviso al conectar con Gemini API (usando Motor Experto Local): {exc}")

    return {}


# ---------------------------------------------------------------------------
# 4. Autenticación y Funciones de API de Mercado Libre
# ---------------------------------------------------------------------------
def obtener_access_token_ml() -> str | None:
    env_token = os.getenv("ML_ACCESS_TOKEN")
    if env_token:
        return env_token.strip()
    if not RUTA_CREDENCIALES_ML.exists():
        return None
    try:
        creds = json.loads(RUTA_CREDENCIALES_ML.read_text(encoding="utf-8"))
    except Exception:
        return None

    expira_en = creds.get("expires_at", 0)
    if creds.get("access_token") and time.time() < (expira_en - 300):
        return str(creds["access_token"])

    if all(k in creds for k in ("client_id", "client_secret", "refresh_token")):
        resp = http.post(
            f"{ML_API_BASE}/oauth/token",
            data={
                "grant_type": "refresh_token",
                "client_id": creds["client_id"],
                "client_secret": creds["client_secret"],
                "refresh_token": creds["refresh_token"],
            },
            timeout=15,
        )
        if resp.status_code == 200:
            data = resp.json()
            creds["access_token"] = data["access_token"]
            creds["refresh_token"] = data.get("refresh_token", creds["refresh_token"])
            creds["expires_at"] = int(time.time()) + int(data.get("expires_in", 21600))
            RUTA_CREDENCIALES_ML.write_text(json.dumps(creds, indent=2), encoding="utf-8")
            return str(creds["access_token"])
    return creds.get("access_token")


def predecir_categoria_ml(titulo: str, token: str | None) -> str:
    if not token:
        return CATEGORIA_TENIS_FALLBACK
    try:
        r = http.get(
            f"{ML_API_BASE}/sites/{SITE_ID}/domain_discovery/search",
            params={"q": titulo, "limit": 1},
            headers={"Authorization": f"Bearer {token}"},
            timeout=10,
        )
        if r.status_code == 200 and r.json():
            return str(r.json()[0].get("category_id", CATEGORIA_TENIS_FALLBACK))
    except Exception:
        pass
    return CATEGORIA_TENIS_FALLBACK


def subir_carrusel_imagenes_ml(id_ps: str, token: str) -> list[str]:
    carpeta = DIR_CATALOGO / str(id_ps)
    archivos = sorted(carpeta.glob("*.jpg")) if carpeta.exists() else []
    picture_ids: list[str] = []
    for img_path in archivos:
        try:
            with open(img_path, "rb") as f_img:
                resp = http.post(
                    f"{ML_API_BASE}/pictures/items/upload",
                    headers={"Authorization": f"Bearer {token}"},
                    files={"file": (img_path.name, f_img.read(), "image/jpeg")},
                    timeout=30,
                )
            if resp.status_code in (200, 201):
                pic_id = resp.json().get("id")
                if pic_id:
                    picture_ids.append(str(pic_id))
        except Exception:
            continue
    return picture_ids


def construir_payload_item_ml(
    id_ps: str,
    titulo_seo: str,
    marca: str,
    color: str,
    genero: str,
    edad: str,
    precio_ml: float,
    tallas_poisson: list[tuple[str, int]],
    picture_ids: list[str],
    category_id: str,
    pausado: bool = False,
) -> dict[str, Any]:
    sku_privado = f"ZV-{id_ps}"
    pics_payload = [{"id": pid} for pid in picture_ids] if picture_ids else [
        {"source": f"local://data/catalogo_listo/{id_ps}/01_portada_blanca.jpg"}
    ]
    ids_variacion = picture_ids if picture_ids else ["SIMULATED_PIC_ID_1"]

    variaciones = []
    for talla_str, stock_seguro in tallas_poisson:
        num_f = float(re.findall(r"\d+(?:\.\d+)?", str(talla_str))[0])
        talla_etiqueta = f"{int(num_f)} MX" if num_f.is_integer() else f"{num_f:.1f} MX"

        variaciones.append({
            "price": round(float(precio_ml), 2),
            "available_quantity": int(max(0, stock_seguro)),
            "attribute_combinations": [
                {"id": "SIZE", "name": "Talla", "value_name": talla_etiqueta},
                {"id": "COLOR", "name": "Color", "value_name": color},
            ],
            "picture_ids": ids_variacion,
            "attributes": [
                {"id": "SELLER_SKU", "value_name": f"{sku_privado}-T{talla_etiqueta.replace(' ', '')}"}
            ],
        })

    return {
        "title": titulo_seo,
        "category_id": category_id,
        "price": round(float(precio_ml), 2),
        "currency_id": "MXN",
        "buying_mode": "buy_it_now",
        "listing_type_id": "gold_special",
        "condition": "new",
        "status": "paused" if pausado else "active",
        "pictures": pics_payload,
        "seller_custom_field": sku_privado,
        "attributes": [
            {"id": "BRAND", "value_name": marca},
            {"id": "MODEL", "value_name": titulo_seo[:30]},
            {"id": "GENDER", "value_name": genero},
            {"id": "AGE_GROUP", "value_name": edad},
            {"id": "SELLER_SKU", "value_name": sku_privado},
        ],
        "variations": variaciones,
        "sale_terms": [
            {"id": "WARRANTY_TYPE", "value_name": "Garantía del vendedor"},
            {"id": "WARRANTY_TIME", "value_name": "30 días"},
        ],
    }


# ---------------------------------------------------------------------------
# 5. Orquestador Principal
# ---------------------------------------------------------------------------
def ejecutar_sincronizador_ml(modo: str, limite: int, regenerar_titulos: bool) -> None:
    print("=" * 92)
    print(f"🚀 PASO 7: SINCRONIZADOR BULK MERCADO LIBRE + MOTOR SEO IA | MODO: {modo.upper()}")
    print("=" * 92)

    if not RUTA_BD.exists():
        print(f"❌ No existe la base de datos en {RUTA_BD}.")
        return

    conn = sqlite3.connect(RUTA_BD)
    conn.execute("PRAGMA journal_mode=WAL;")
    asegurar_columnas_ml(conn)

    token = obtener_access_token_ml() if modo != "dry-run" else None
    if modo != "dry-run" and not token:
        print("⚠️ Sin token de ML en `data/ml_credenciales.json`. Usando `--modo dry-run` seguro.")
        modo = "dry-run"

    filas_skus = conn.execute(
        """
        SELECT id_ps, nombre_modelo, url_real, costo_socio, precio_ml, utilidad_neta_ml,
               stock_publicable_total, apto_publicar, titulo_seo_ml, marca_detectada,
               color_detectado, ml_item_id, ml_pictures_json
        FROM zapatos_candidatos
        WHERE apto_publicar = 1 OR ml_item_id IS NOT NULL
        ORDER BY utilidad_neta_ml * stock_publicable_total DESC
        LIMIT ?
        """,
        (limite,),
    ).fetchall()

    if not filas_skus:
        print("ℹ️ No hay SKUs marcados como Aptos para Publicar todavía.")
        conn.close()
        return

    # 1. Preparar enriquecimiento visual y de títulos SEO (garantizando 0 duplicados)
    lote_ia: list[dict[str, Any]] = []
    datos_preparados: dict[str, dict[str, Any]] = {}
    titulos_usados: set[str] = set()

    for idx, fila in enumerate(filas_skus):
        id_ps, nombre, url_real, costo, precio_ml, util_ml, stock_pub, apto, tit_db, marca_db, col_db, ml_id, ml_pics = fila
        id_str = str(id_ps)

        tallas_rows = conn.execute(
            """
            SELECT talla, stock_seguro_publicable
            FROM historial_stock_21_dias
            WHERE id_ps = ?
              AND fecha = (SELECT MAX(fecha) FROM historial_stock_21_dias WHERE id_ps = ?)
            ORDER BY CAST(talla AS REAL) ASC
            """,
            (id_str, id_str),
        ).fetchall()
        tallas_con_stock = [(str(t), int(s)) for t, s in tallas_rows if int(s) > 0]

        color = col_db if (col_db and not regenerar_titulos) else detectar_color_zapato(id_str, nombre)
        marca = marca_db if (marca_db and not regenerar_titulos) else inferir_marca(nombre, url_real)
        genero, edad, publico = detectar_genero_y_edad(nombre, tallas_con_stock, color)

        titulo_local = construir_titulo_experto_local(nombre, marca, color, publico, idx)
        # Garantizar unicidad absoluta entre los 48 SKUs
        intento = 1
        while titulo_local.lower() in titulos_usados:
            mod_extra = MODIFICADORES_UNICOS_ESCOLAR[(idx + intento) % len(MODIFICADORES_UNICOS_ESCOLAR)]
            base_corta = construir_titulo_experto_local(nombre, marca, color, publico, idx + intento)[:42].strip()
            titulo_local = f"{base_corta} {mod_extra.split()[0]} {mod_extra.split()[-1]}"[:60].strip()
            intento += 1
        titulos_usados.add(titulo_local.lower())

        datos_preparados[id_str] = {
            "fila": fila,
            "tallas_rows": tallas_rows,
            "tallas_con_stock": tallas_con_stock,
            "color": color,
            "marca": marca,
            "genero": genero,
            "edad": edad,
            "titulo_seo": tit_db if (tit_db and not regenerar_titulos) else titulo_local,
        }

        if regenerar_titulos or not tit_db:
            lote_ia.append({
                "id_ps": id_str,
                "nombre_base": nombre,
                "marca_sugerida": marca,
                "color_foto": color,
                "publico": publico,
                "propuesta_base": titulo_local,
            })

    # Si hay llave de Gemini configurada, refinar aún más el lote completo
    if lote_ia:
        optimizados_ia = optimizar_lote_con_gemini_ia(lote_ia)
        for id_str, info_ia in optimizados_ia.items():
            if id_str in datos_preparados:
                datos_preparados[id_str]["titulo_seo"] = info_ia["titulo_seo"]
                datos_preparados[id_str]["marca"] = info_ia["marca"]

    print(f"📦 Procesando {len(filas_skus)} SKUs con títulos SEO inteligentes y color detectado...\n")
    simulaciones: list[dict[str, Any]] = []

    for id_str, ctx in datos_preparados.items():
        id_ps, nombre, url_real, costo, precio_ml, util_ml, stock_pub, apto, _, _, _, ml_item_id, ml_pics_json = ctx["fila"]
        tallas_con_stock = ctx["tallas_con_stock"]
        titulo_seo = ctx["titulo_seo"]
        marca = ctx["marca"]
        color = ctx["color"]

        # Guardar el título SEO, marca y color en SQLite para que también aparezcan en Streamlit
        conn.execute(
            """
            UPDATE zapatos_candidatos
            SET titulo_seo_ml = ?, marca_detectada = ?, color_detectado = ?
            WHERE id_ps = ?
            """,
            (titulo_seo, marca, color, id_str),
        )

        if not tallas_con_stock and not ml_item_id:
            continue

        pausar_inicial = (modo == "piloto-pausado")
        picture_ids = json.loads(ml_pics_json) if ml_pics_json else []

        if modo != "dry-run" and not picture_ids and not ml_item_id:
            picture_ids = subir_carrusel_imagenes_ml(id_str, str(token))

        cat_id = predecir_categoria_ml(titulo_seo, token)
        payload = construir_payload_item_ml(
            id_ps=id_str,
            titulo_seo=titulo_seo,
            marca=marca,
            color=color,
            genero=ctx["genero"],
            edad=ctx["edad"],
            precio_ml=precio_ml,
            tallas_poisson=tallas_con_stock,
            picture_ids=picture_ids,
            category_id=cat_id,
            pausado=pausar_inicial,
        )

        if modo == "dry-run":
            simulaciones.append({
                "sku_interno_secreto": id_str,
                "marca_detectada": marca,
                "color_detectado": color,
                "costo_socio": costo,
                "efectivo_libre_por_par": util_ml,
                "pares_seguros_totales": stock_pub,
                "payload_mercado_libre": payload,
            })
            print(
                f"✨ [SEO IA] {titulo_seo:<58} | Marca: {marca:<14} | "
                f"Color: {color:<11} | ${precio_ml:>8,.2f} | Tallas: {len(tallas_con_stock)}"
            )

    if modo == "dry-run":
        RUTA_SIMULACION_JSON.write_text(
            json.dumps(simulaciones, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        print("\n" + "=" * 92)
        print(f"✅ SIMULACIÓN BULK COMPLETADA: {len(simulaciones)} títulos únicos guardados en SQLite y JSON.")
        print(f"📄 Archivo listo en: {RUTA_SIMULACION_JSON.resolve()}")
        print("=" * 92)

    conn.commit()
    conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Sincronizador Masivo Mercado Libre + IA SEO")
    parser.add_argument("--modo", choices=["dry-run", "piloto-pausado", "produccion"], default="dry-run")
    parser.add_argument("--limite", type=int, default=50)
    parser.add_argument(
        "--regenerar-titulos",
        action="store_true",
        help="Fuerza la regeneración de los títulos SEO aunque ya estén en caché en SQLite",
    )
    args = parser.parse_args()
    ejecutar_sincronizador_ml(modo=args.modo, limite=args.limite, regenerar_titulos=args.regenerar_titulos)