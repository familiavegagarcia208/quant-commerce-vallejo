"""
=========================================================================================
PASO 5: MOTOR FINANCIERO, FISCAL Y DE BLINDAJE DE INVENTARIO (ROPA Y CALZADO)
=========================================================================================
Responsabilidad Única (SRP):
1. Sanitización Estricta (Pandas ETL): Limpia valores NaN, nulos o corruptos.
2. Normalización Híbrida de Tallas: Soporta tallas textiles (XCH, CH, M, G, EG, 2EG, 
   UNITALLA, 28, 30, 32, 34) y tallas de calzado en centímetros (22.0 a 31.5).
3. Blindaje Operativo por Talla:
   Stock Publicable = MAX(0, Stock Físico Vallejo - 6)
4. Unit Economics con Tabulador de Envío Diferenciado (Ropa Ligera vs. Ropa Pesada/Calzado).
=========================================================================================
"""

from __future__ import annotations

import logging
import math
import re
import sqlite3
from pathlib import Path
from typing import Dict, List, Tuple

import pandas as pd

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("MotorFinancieroVallejo")

RUTA_BD: Path = Path("data/zapateria_vallejo.db")
DIR_CATALOGO: Path = Path("data/catalogo_listo")

# ---------------------------------------------------------------------------
# CONSTANTES DE REGLAS DE NEGOCIO Y FISCALES (MÉXICO)
# ---------------------------------------------------------------------------
RESERVA_FIJA_POR_TALLA: int = 6         # 5 exhibición/devoluciones + 1 colchón intradía
MIN_TALLAS_SEGURAS_REQUERIDAS: int = 1  # Mínimo 1 talla segura (soporta prendas Unitalla o curvas textiles)
MIN_PARES_PUBLICABLES_MODELO: int = 3   # Mínimo 3 piezas publicables sumando tallas

IVA_TASA: float = 0.16
COMISION_ML_CLASICA: float = 0.175
RETENCION_IVA_ML: float = 0.08
RETENCION_ISR_ML: float = 0.025
RETENCION_SAT_EFECTIVA: float = (RETENCION_IVA_ML + RETENCION_ISR_ML) / (1.0 + IVA_TASA)
TASA_MERMA_SOBRE_COSTO: float = 0.03    # 3% de autoseguro sobre el costo
MARGEN_NETO_SOBRE_COSTO: float = 0.25   # 25% de ganancia neta limpia sobre el costo

PALABRAS_ROPA: List[str] = [
    "camisa", "blusa", "playera", "pantalon", "pantalón", "jeans", "mezclilla",
    "vestido", "falda", "chamarra", "sudadera", "sueter", "suéter", "cardigan",
    "chaleco", "conjunto", "pans", "jogger", "legging", "malla", "short",
    "bermuda", "body", "top", "crop", "saco", "blazer", "abrigo", "gabardina",
    "pijama", "bata", "brassiere", "brasier", "boxer", "bóxer", "trusa", "calceta",
]


# ===========================================================================
# 1. FUNCIONES DE CLASIFICACIÓN Y NORMALIZACIÓN HÍBRIDA (ROPA + CALZADO)
# ===========================================================================
def es_prenda_textil(nombre_modelo: str) -> bool:
    """
    Determina si un artículo pertenece a la división de Ropa/Textil según su nombre.

    Args:
        nombre_modelo (str): Nombre del artículo en el catálogo.

    Returns:
        bool: True si es prenda de vestir, False si es calzado.
    """
    nombre_low: str = str(nombre_modelo).lower()
    return any(palabra in nombre_low for palabra in PALABRAS_ROPA)


def normalizar_talla_universal(valor_talla: str | float, es_ropa: bool = False) -> str:
    """
    Estandariza tallas tanto de Ropa (letras y cintura/edad) como de Calzado (cm MX).

    Args:
        valor_talla (str | float): Talla cruda almacenada en la base de datos.
        es_ropa (bool): Indica si el SKU es una prenda textil.

    Returns:
        str: Talla estandarizada ('CH', 'M', 'G', 'EG', '32', 'UNITALLA' o '25.5').
    """
    if pd.isna(valor_talla):
        return "N/A"

    texto_limpio: str = str(valor_talla).strip().upper()
    if not texto_limpio:
        return "N/A"

    # 1. Mapeo canónico de tallas de ropa por letra (México / Internacional)
    mapa_tallas_letra: Dict[str, str] = {
        "XXS": "2XCH", "2XCH": "2XCH",
        "XS": "XCH", "XCH": "XCH", "ECH": "XCH", "EXTRA CHICA": "XCH",
        "S": "CH", "CH": "CH", "CHICA": "CH",
        "M": "M", "MED": "M", "MEDIANA": "M",
        "L": "G", "G": "G", "GDE": "G", "GRANDE": "G",
        "XL": "EG", "EG": "EG", "XG": "EG", "EXTRA GRANDE": "EG",
        "XXL": "2EG", "2XL": "2EG", "2EG": "2EG", "EEG": "2EG",
        "3XL": "3EG", "3EG": "3EG",
        "UNI": "UNITALLA", "UNITALLA": "UNITALLA", "U": "UNITALLA",
    }
    if texto_limpio in mapa_tallas_letra:
        return mapa_tallas_letra[texto_limpio]

    # Si contiene letras y no es simplemente "TALLA X", devolver en mayúsculas
    if re.search(r"[A-Z]", texto_limpio) and not texto_limpio.startswith("TALLA"):
        return texto_limpio

    # 2. Extracción numérica para cintura/pantalón o calzado
    coincidencias = re.findall(r"\d+(?:\.\d+)?", texto_limpio)
    if not coincidencias:
        return texto_limpio

    numero: float = float(coincidencias[0])

    # Si es Ropa (ej. Pantalón talla 28, 30, 32 o Infantil 4, 6, 8, 10, 12), mantener entero
    if es_ropa or (numero >= 28.0 and numero.is_integer() and "." not in texto_limpio):
        return str(int(numero)) if numero.is_integer() else f"{numero:.1f}"

    # Si es Calzado con numeración abreviada (ej. 2.0 a 11.5 -> 22.0 a 31.5 cm)
    if 2.0 <= numero <= 11.5:
        numero += 20.0
    return f"{numero:.1f}"


def clasificar_atributos_catalogo(
    nombre_modelo: str, lista_tallas: List[str]
) -> Tuple[str, str, str]:
    """
    Deduce Género, Categoría (Ropa o Calzado) y Color a partir del nombre y sus tallas.

    Args:
        nombre_modelo (str): Nombre limpio del producto.
        lista_tallas (List[str]): Lista de tallas normalizadas del producto.

    Returns:
        Tuple[str, str, str]: (genero, categoria, color)
    """
    n: str = nombre_modelo.lower()

    # 1. Categoría (Ropa vs Calzado)
    if any(k in n for k in ["blusa", "camisa", "playera", "polo", "top", "crop", "body"]):
        categoria = "Camisa / Blusa / Playera"
    elif any(k in n for k in ["pantalon", "pantalón", "jeans", "mezclilla", "jogger", "legging", "short", "bermuda", "pans"]):
        categoria = "Pantalón / Jeans"
    elif any(k in n for k in ["vestido", "falda", "jumper", "palazzo", "enterizo"]):
        categoria = "Vestido / Falda"
    elif any(k in n for k in ["chamarra", "sudadera", "sueter", "suéter", "cardigan", "chaleco", "abrigo", "saco", "blazer", "conjunto"]):
        categoria = "Chamarra / Sudadera / Suéter"
    elif any(k in n for k in ["pijama", "bata", "brasier", "brassiere", "boxer", "bóxer", "lenceria", "lencería"]):
        categoria = "Ropa Interior / Pijama"
    elif any(k in n for k in ["tenis", "sneaker", "runner", "running", "deportivo", "skate"]):
        categoria = "Tenis / Deportivo"
    elif any(k in n for k in ["bota", "botin", "botín", "industrial", "tactica", "táctica", "hiking"]):
        categoria = "Bota / Botín"
    elif any(k in n for k in ["sandalia", "huarache", "chancla", "slide", "mule", "pantufla"]):
        categoria = "Sandalia / Confort"
    elif any(k in n for k in ["escolar", "vestir", "oxford", "derby", "mocasin", "mocasín", "formal"]):
        categoria = "Formal / Escolar"
    else:
        categoria = "Casual / Urbano"

    # 2. Género
    tallas_num_calzado = [
        float(t) for t in lista_tallas
        if re.match(r"^\d{2}\.\d$", str(t))
    ]
    t_max = max(tallas_num_calzado) if tallas_num_calzado else 0.0
    t_min = min(tallas_num_calzado) if tallas_num_calzado else 0.0

    if any(k in n for k in ["niño", "niña", "infantil", "kids", "junior", "escolar", "bebé"]) or (
        0.0 < t_max <= 21.5
    ):
        genero = "Infantil / Juvenil"
    elif any(k in n for k in ["caballero", "hombre", "varon", "varón", "men", "camisa"]):
        genero = "Hombre"
    elif any(k in n for k in ["dama", "mujer", "women", "blusa", "vestido", "falda", "tacón", "tacon", "zapatilla", "legging"]):
        genero = "Mujer"
    elif t_max >= 27.0 or t_min >= 25.0:
        genero = "Hombre"
    elif 21.5 <= t_max <= 26.5:
        genero = "Mujer"
    else:
        genero = "Unisex"

    # 3. Color
    mapa_colores = [
        ("Negro", ["negro", "black", "onix", "ónix"]),
        ("Blanco", ["blanco", "white", "hueso", "ivory", "perla", "crudo"]),
        ("Café / Miel / Beige", ["cafe", "café", "miel", "camel", "tan", "chocolate", "arena", "beige", "caqui", "khaki"]),
        ("Azul / Mezclilla", ["azul", "marino", "navy", "denim", "mezclilla", "celeste", "rey"]),
        ("Rojo / Vino", ["rojo", "vino", "tinto", "guinda", "coral", "terracota"]),
        ("Gris / Plata", ["gris", "plata", "silver", "plomo", "oxford", "jaspe"]),
        ("Rosa / Lila", ["rosa", "fiusha", "fucsia", "lila", "morado", "lavanda", "bugambilia"]),
        ("Verde / Olivo", ["verde", "olivo", "militar", "menta", "esmeralda", "sage"]),
    ]
    color = "Multicolor / Clásico"
    for etiqueta_col, palabras in mapa_colores:
        if any(p in n for p in palabras):
            color = etiqueta_col
            break

    return genero, categoria, color


# ===========================================================================
# 2. FUNCIONES FINANCIERAS Y UNIT ECONOMICS POR CATEGORÍA
# ===========================================================================
def estimar_envio_mercado_envios(costo_socio: float, categoria: str) -> float:
    """
    Determina el costo real de la guía de Mercado Envíos diferenciando el peso volumétrico
    de prendas textiles ligeras (< 0.5 kg), prendas medias (< 1 kg) y calzado en caja.

    Args:
        costo_socio (float): Costo de socio en sucursal Vallejo (> 0).
        categoria (str): Categoría clasificada del artículo.

    Returns:
        float: Costo de envío en pesos mexicanos ($ MXN).
    """
    # Ropa ligera (Blusas, Camisas, Playeras, Ropa Interior): Paquete bolsa < 500g
    if categoria in ("Camisa / Blusa / Playera", "Ropa Interior / Pijama"):
        return 86.0 if costo_socio < 350.0 else 94.0

    # Ropa peso medio (Pantalones, Jeans, Vestidos): Paquete 500g a 1kg
    if categoria in ("Pantalón / Jeans", "Vestido / Falda"):
        return 94.0 if costo_socio < 450.0 else 106.0

    # Chamarras, Sudaderas y Calzado en caja (1kg a 2kg volumétrico)
    if costo_socio < 350.0:
        return 98.0
    if costo_socio < 650.0:
        return 112.0
    return 128.0


def redondeo_comercial_decena(valor: float) -> float:
    """Redondea un precio positivo hacia arriba a la decena terminada en 9 (ej. 642 -> 649)."""
    if valor <= 0.0:
        return 0.0
    return float(int(math.ceil(valor / 10.0) * 10) - 1)


def calcular_unit_economics_por_articulo(
    costo_socio: float,
    categoria: str = "Casual / Urbano",
    margen_objetivo_costo: float = MARGEN_NETO_SOBRE_COSTO,
) -> Dict[str, float]:
    """
    Despeja algebraicamente el Precio de Venta en Mercado Libre (P):
      P = [ Costo * (1 + Merma_3% + Margen_25%) + Envío ] / [ 1 - Comisión_17.5% - Retención_SAT_9.05% ]

    Args:
        costo_socio (float): Costo unitario en Vallejo sanitizado (float >= 0).
        categoria (str): Categoría del producto para determinar la tarifa de envío.
        margen_objetivo_costo (float): Margen limpio objetivo sobre el costo (default 0.25).

    Returns:
        Dict[str, float]: Diccionario con todos los rubros financieros unitarios redondeados.
    """
    if costo_socio <= 0.0:
        return {
            "precio_piso_ml": 0.0,
            "precio_ml": 0.0,
            "envio_ml_pesos": 0.0,
            "comision_ml_pesos": 0.0,
            "impuestos_sat_pesos": 0.0,
            "fondo_merma_ml": 0.0,
            "utilidad_neta_ml": 0.0,
            "iva_a_favor_ml": 0.0,
        }

    envio: float = estimar_envio_mercado_envios(costo_socio, categoria)
    denominador_retenciones: float = 1.0 - COMISION_ML_CLASICA - RETENCION_SAT_EFECTIVA  # 0.73448276

    numerador_optimo: float = (
        costo_socio * (1.0 + TASA_MERMA_SOBRE_COSTO + margen_objetivo_costo)
    ) + envio
    precio_ml: float = redondeo_comercial_decena(numerador_optimo / denominador_retenciones)

    numerador_piso: float = (costo_socio * (1.0 + TASA_MERMA_SOBRE_COSTO)) + envio + 70.0
    precio_piso_ml: float = redondeo_comercial_decena(numerador_piso / denominador_retenciones)

    comision_pesos: float = round(precio_ml * COMISION_ML_CLASICA, 2)
    impuestos_sat_pesos: float = round(precio_ml * RETENCION_SAT_EFECTIVA, 2)
    fondo_merma_pesos: float = round(costo_socio * TASA_MERMA_SOBRE_COSTO, 2)

    utilidad_neta: float = round(
        precio_ml - costo_socio - envio - comision_pesos - impuestos_sat_pesos - fondo_merma_pesos,
        2,
    )

    factor_iva_desglose: float = IVA_TASA / (1.0 + IVA_TASA)
    iva_compras_gastos: float = (costo_socio + comision_pesos + envio) * factor_iva_desglose
    iva_retenido_plataforma: float = (precio_ml / (1.0 + IVA_TASA)) * RETENCION_IVA_ML
    iva_trasladado_cliente: float = precio_ml * factor_iva_desglose
    iva_a_favor: float = round(
        max(0.0, (iva_compras_gastos + iva_retenido_plataforma) - iva_trasladado_cliente),
        2,
    )

    return {
        "precio_piso_ml": precio_piso_ml,
        "precio_ml": precio_ml,
        "envio_ml_pesos": envio,
        "comision_ml_pesos": comision_pesos,
        "impuestos_sat_pesos": impuestos_sat_pesos,
        "fondo_merma_ml": fondo_merma_pesos,
        "utilidad_neta_ml": utilidad_neta,
        "iva_a_favor_ml": iva_a_favor,
    }


# ===========================================================================
# 3. CAPA DE SANITIZACIÓN Y PERSISTENCIA EN SQLITE (ETL PIPELINE)
# ===========================================================================
def asegurar_esquema_financiero(conn: sqlite3.Connection) -> None:
    """Verifica y crea las columnas requeridas en SQLite."""
    cols_cand = [c[1] for c in conn.execute("PRAGMA table_info(zapatos_candidatos)").fetchall()]
    esquema_cand: Dict[str, str] = {
        "sku_interno_ml": "TEXT DEFAULT ''",
        "genero": "TEXT DEFAULT 'Unisex'",
        "tipo_calzado": "TEXT DEFAULT 'Casual / Urbano'",
        "color": "TEXT DEFAULT 'Multicolor / Clásico'",
        "tallas_seguras_count": "INTEGER DEFAULT 0",
        "stock_publicable_total": "INTEGER DEFAULT 0",
        "tallas_seguras_detalle": "TEXT DEFAULT 'Sin stock > 6 pzas'",
        "precio_piso_ml": "REAL DEFAULT 0.0",
        "precio_ml": "REAL DEFAULT 0.0",
        "envio_ml_pesos": "REAL DEFAULT 0.0",
        "comision_ml_pesos": "REAL DEFAULT 0.0",
        "impuestos_sat_pesos": "REAL DEFAULT 0.0",
        "fondo_merma_ml": "REAL DEFAULT 0.0",
        "utilidad_neta_ml": "REAL DEFAULT 0.0",
        "iva_a_favor_ml": "REAL DEFAULT 0.0",
        "fotos_completas": "INTEGER DEFAULT 0",
        "fotos_status": "TEXT DEFAULT '0/4 ❌'",
        "semaforo": "TEXT DEFAULT '⚠️ NO RECOMENDADO'",
        "motivo_diagnostico": "TEXT DEFAULT 'Pendiente de cálculo'",
        "apto_publicar": "INTEGER DEFAULT 0",
    }
    for col, tipo in esquema_cand.items():
        if col not in cols_cand:
            conn.execute(f"ALTER TABLE zapatos_candidatos ADD COLUMN {col} {tipo};")

    cols_hist = [c[1] for c in conn.execute("PRAGMA table_info(historial_stock_21_dias)").fetchall()]
    esquema_hist: Dict[str, str] = {
        "talla_norm": "TEXT DEFAULT 'N/A'",
        "reserva_operativa": f"INTEGER DEFAULT {RESERVA_FIJA_POR_TALLA}",
        "stock_seguro_publicable": "INTEGER DEFAULT 0",
    }
    for col, tipo in esquema_hist.items():
        if col not in cols_hist:
            conn.execute(f"ALTER TABLE historial_stock_21_dias ADD COLUMN {col} {tipo};")

    conn.commit()


def ejecutar_motor_financiero_y_sanitizacion() -> None:
    """Ejecuta el ciclo ETL completo para Ropa y Calzado y muestra resumen en consola."""
    print("=" * 75)
    print("⚙️ EJECUTANDO PASO 5: MOTOR FINANCIERO HÍBRIDO (ROPA Y CALZADO)")
    print("=" * 75)

    if not RUTA_BD.exists():
        print(f"❌ No se encontró la base de datos en {RUTA_BD}")
        return

    conn = sqlite3.connect(RUTA_BD, timeout=30)
    asegurar_esquema_financiero(conn)

    df_cand: pd.DataFrame = pd.read_sql_query("SELECT * FROM zapatos_candidatos", conn)
    df_hist: pd.DataFrame = pd.read_sql_query("SELECT rowid, * FROM historial_stock_21_dias", conn)

    if df_cand.empty:
        print("⚠️ La tabla zapatos_candidatos está vacía.")
        conn.close()
        return

    df_cand["id_ps"] = df_cand["id_ps"].fillna("").astype(str).str.strip()
    df_cand["nombre_modelo"] = (
        df_cand["nombre_modelo"].fillna("Modelo Sin Nombre").astype(str).str.strip()
    )
    mapa_es_ropa: Dict[str, bool] = {
        str(r["id_ps"]): es_prenda_textil(str(r["nombre_modelo"]))
        for _, r in df_cand.iterrows()
    }

    # -----------------------------------------------------------------------
    # FASE 1: SANITIZACIÓN DEL HISTÓRICO DE TALLAS (ROPA + CALZADO)
    # -----------------------------------------------------------------------
    if not df_hist.empty:
        df_hist["id_ps"] = df_hist["id_ps"].fillna("").astype(str).str.strip()
        df_hist["talla"] = df_hist["talla"].fillna("N/A").astype(str).str.strip()
        df_hist["stock_vallejo"] = (
            pd.to_numeric(df_hist["stock_vallejo"], errors="coerce")
            .fillna(0)
            .clip(lower=0)
            .astype(int)
        )
        df_hist["talla_norm"] = df_hist.apply(
            lambda r: normalizar_talla_universal(r["talla"], mapa_es_ropa.get(str(r["id_ps"]), False)),
            axis=1,
        )
        df_hist["reserva_operativa"] = RESERVA_FIJA_POR_TALLA
        df_hist["stock_seguro_publicable"] = (
            df_hist["stock_vallejo"] - RESERVA_FIJA_POR_TALLA
        ).clip(lower=0).astype(int)

        datos_update_hist = list(
            zip(
                df_hist["talla_norm"],
                df_hist["reserva_operativa"],
                df_hist["stock_seguro_publicable"],
                df_hist["rowid"],
            )
        )
        conn.executemany(
            """
            UPDATE historial_stock_21_dias
            SET talla_norm = ?,
                reserva_operativa = ?,
                stock_seguro_publicable = ?
            WHERE rowid = ?
            """,
            datos_update_hist,
        )

        if "fecha" in df_hist.columns:
            df_tallas_ult = (
                df_hist.sort_values("fecha")
                .drop_duplicates(subset=["id_ps", "talla_norm"], keep="last")
                .copy()
            )
        else:
            df_tallas_ult = df_hist.drop_duplicates(subset=["id_ps", "talla_norm"], keep="last").copy()
    else:
        df_tallas_ult = pd.DataFrame(
            columns=["id_ps", "talla_norm", "stock_vallejo", "stock_seguro_publicable"]
        )

    stk_fisico_map: Dict[str, int] = (
        df_tallas_ult.groupby("id_ps")["stock_vallejo"].sum().to_dict()
        if not df_tallas_ult.empty
        else {}
    )
    stk_publicable_map: Dict[str, int] = (
        df_tallas_ult.groupby("id_ps")["stock_seguro_publicable"].sum().to_dict()
        if not df_tallas_ult.empty
        else {}
    )
    tallas_seguras_count_map: Dict[str, int] = (
        df_tallas_ult[df_tallas_ult["stock_seguro_publicable"] > 0]
        .groupby("id_ps")["talla_norm"]
        .count()
        .to_dict()
        if not df_tallas_ult.empty
        else {}
    )
    tallas_lista_map: Dict[str, List[str]] = (
        df_tallas_ult.groupby("id_ps")["talla_norm"].apply(list).to_dict()
        if not df_tallas_ult.empty
        else {}
    )

    df_seguras_only = (
        df_tallas_ult[df_tallas_ult["stock_seguro_publicable"] > 0].copy()
        if not df_tallas_ult.empty
        else pd.DataFrame()
    )
    if not df_seguras_only.empty:
        df_seguras_only["etiqueta_t"] = (
            df_seguras_only["talla_norm"].astype(str)
            + " ("
            + df_seguras_only["stock_seguro_publicable"].astype(str)
            + ")"
        )
        detalle_tallas_map: Dict[str, str] = (
            df_seguras_only.groupby("id_ps")["etiqueta_t"]
            .apply(lambda s: ", ".join(s.astype(str)))
            .to_dict()
        )
    else:
        detalle_tallas_map = {}

    # -----------------------------------------------------------------------
    # FASE 2: CÁLCULO DE UNIT ECONOMICS INTERNOS Y SEMÁFORO
    # -----------------------------------------------------------------------
    if "costo_socio" in df_cand.columns:
        df_cand["costo_socio"] = (
            pd.to_numeric(df_cand["costo_socio"], errors="coerce").fillna(0.0).clip(lower=0.0)
        )
    else:
        df_cand["costo_socio"] = 0.0

    registros_actualizacion: List[tuple] = []
    recomendados_totales: int = 0
    con_costo_totales: int = 0

    for _, row in df_cand.iterrows():
        id_ps: str = str(row["id_ps"])
        if not id_ps:
            continue

        nombre: str = str(row["nombre_modelo"])
        costo: float = float(row["costo_socio"])
        if costo > 0:
            con_costo_totales += 1

        stock_fisico: int = int(stk_fisico_map.get(id_ps, 0))
        stock_publicable: int = int(stk_publicable_map.get(id_ps, 0))
        n_tallas_seguras: int = int(tallas_seguras_count_map.get(id_ps, 0))
        detalle_tallas: str = detalle_tallas_map.get(id_ps, "Sin tallas > 6 pzas")

        lista_t = tallas_lista_map.get(id_ps, [])
        genero, categoria, color = clasificar_atributos_catalogo(nombre, lista_t)

        fin = calcular_unit_economics_por_articulo(costo, categoria)

        carpeta_sku = DIR_CATALOGO / id_ps
        archivos_fotos = [
            carpeta_sku / "01_portada_blanca.jpg",
            carpeta_sku / "02_angulo_galeria.jpg",
            carpeta_sku / "03_ficha_beneficios.jpg",
            carpeta_sku / "04_guia_tallas_cm.jpg",
        ]
        conteo_fotos: int = sum(1 for ruta_f in archivos_fotos if ruta_f.exists())
        fotos_ok: int = 1 if conteo_fotos == 4 else 0
        fotos_status: str = f"{conteo_fotos}/4 {'✅' if fotos_ok else '❌'}"

        motivos_rechazo: List[str] = []
        if costo <= 0.0:
            motivos_rechazo.append("Costo socio no registrado")
        if not fotos_ok:
            motivos_rechazo.append(f"Faltan fotos HD ({conteo_fotos}/4)")
        if n_tallas_seguras < MIN_TALLAS_SEGURAS_REQUERIDAS or stock_publicable < MIN_PARES_PUBLICABLES_MODELO:
            motivos_rechazo.append(
                f"Stock insuficiente tras reserva de {RESERVA_FIJA_POR_TALLA} pzas "
                f"({n_tallas_seguras} tallas / {stock_publicable} pzas libres)"
            )

        if not motivos_rechazo:
            semaforo = "✅ RECOMENDADO"
            motivo_txt = (
                f"Óptimo ({categoria}): {n_tallas_seguras} tallas seguras ({stock_publicable} pzas), "
                f"4/4 fotos HD y ganancia limpia de ${fin['utilidad_neta_ml']:,.2f}/unidad."
            )
            recomendados_totales += 1
        else:
            semaforo = "⚠️ NO RECOMENDADO"
            motivo_txt = " · ".join(motivos_rechazo)

        registros_actualizacion.append(
            (
                f"PS-{id_ps}",
                genero,
                categoria,
                color,
                stock_fisico,
                n_tallas_seguras,
                stock_publicable,
                detalle_tallas,
                fin["precio_piso_ml"],
                fin["precio_ml"],
                fin["envio_ml_pesos"],
                fin["comision_ml_pesos"],
                fin["impuestos_sat_pesos"],
                fin["fondo_merma_ml"],
                fin["utilidad_neta_ml"],
                fin["iva_a_favor_ml"],
                fotos_ok,
                fotos_status,
                semaforo,
                motivo_txt,
                id_ps,
            )
        )

    conn.executemany(
        """
        UPDATE zapatos_candidatos
        SET sku_interno_ml = ?,
            genero = ?,
            tipo_calzado = ?,
            color = ?,
            stock_total_anterior = ?,
            tallas_seguras_count = ?,
            stock_publicable_total = ?,
            tallas_seguras_detalle = ?,
            precio_piso_ml = ?,
            precio_ml = ?,
            envio_ml_pesos = ?,
            comision_ml_pesos = ?,
            impuestos_sat_pesos = ?,
            fondo_merma_ml = ?,
            utilidad_neta_ml = ?,
            iva_a_favor_ml = ?,
            fotos_completas = ?,
            fotos_status = ?,
            semaforo = ?,
            motivo_diagnostico = ?
        WHERE CAST(id_ps AS TEXT) = ?
        """,
        registros_actualizacion,
    )

    conn.commit()
    conn.close()

    print(f"✅ Catálogo total en BD procesado : {len(registros_actualizacion):,} SKUs")
    print(f"💰 Artículos con Costo Auditado   : {con_costo_totales:,} SKUs")
    print(f"🚀 Artículos ✅ RECOMENDADOS       : {recomendados_totales:,} SKUs")
    print("=" * 75)


if __name__ == "__main__":
    ejecutar_motor_financiero_y_sanitizacion()