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
from typing import Any, Dict, List, Sequence, Tuple

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
UTILIDAD_MINIMA_SOBRE_VENTA: float = 0.10  # utilidad mínima: 10% del precio de venta
LOGISTICA_POR_PIEZA: float = 60.0          # costo en pesos de ir por el producto y empacarlo

SEMAFORO_RECOMENDADO: str = "✅ RECOMENDADO"
SEMAFORO_NO_RECOMENDADO: str = "⚠️ NO RECOMENDADO"

ARCHIVOS_LAMINAS: Tuple[str, ...] = (
    "01_portada_blanca.jpg",
    "02_angulo_galeria.jpg",
    "03_ficha_beneficios.jpg",
    "04_guia_tallas_cm.jpg",
)

PALABRAS_ROPA: List[str] = [
    "camisa", "blusa", "playera", "pantalon", "pantalón", "jeans", "mezclilla",
    "vestido", "falda", "chamarra", "sudadera", "sueter", "suéter", "cardigan",
    "chaleco", "conjunto", "pans", "jogger", "legging", "malla", "short",
    "bermuda", "body", "top", "crop", "saco", "blazer", "abrigo", "gabardina",
    "pijama", "bata", "brassiere", "brasier", "boxer", "bóxer", "trusa", "calceta",
]

# Mapeo canónico de tallas de ropa por letra (México / Internacional)
MAPA_TALLAS_LETRA: Dict[str, str] = {
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

# Reglas (etiqueta, palabras clave): gana la primera regla con alguna palabra en el nombre
Reglas = Sequence[Tuple[str, Sequence[str]]]

CATEGORIA_DEFAULT: str = "Casual / Urbano"
REGLAS_CATEGORIA: Reglas = (
    ("Camisa / Blusa / Playera", ["blusa", "camisa", "playera", "polo", "top", "crop", "body"]),
    ("Pantalón / Jeans", ["pantalon", "pantalón", "jeans", "mezclilla", "jogger", "legging", "short", "bermuda", "pans"]),
    ("Vestido / Falda", ["vestido", "falda", "jumper", "palazzo", "enterizo"]),
    ("Chamarra / Sudadera / Suéter", ["chamarra", "sudadera", "sueter", "suéter", "cardigan", "chaleco", "abrigo", "saco", "blazer", "conjunto"]),
    ("Ropa Interior / Pijama", ["pijama", "bata", "brasier", "brassiere", "boxer", "bóxer", "lenceria", "lencería"]),
    ("Tenis / Deportivo", ["tenis", "sneaker", "runner", "running", "deportivo", "skate"]),
    ("Bota / Botín", ["bota", "botin", "botín", "industrial", "tactica", "táctica", "hiking"]),
    ("Sandalia / Confort", ["sandalia", "huarache", "chancla", "slide", "mule", "pantufla"]),
    ("Formal / Escolar", ["escolar", "vestir", "oxford", "derby", "mocasin", "mocasín", "formal"]),
)

GENERO_INFANTIL: str = "Infantil / Juvenil"
PALABRAS_INFANTIL: Sequence[str] = ("niño", "niña", "infantil", "kids", "junior", "escolar", "bebé")
REGLAS_GENERO: Reglas = (
    ("Hombre", ["caballero", "hombre", "varon", "varón", "men", "camisa"]),
    ("Mujer", ["dama", "mujer", "women", "blusa", "vestido", "falda", "tacón", "tacon", "zapatilla", "legging"]),
)

COLOR_DEFAULT: str = "Multicolor / Clásico"
REGLAS_COLOR: Reglas = (
    ("Negro", ["negro", "black", "onix", "ónix"]),
    ("Blanco", ["blanco", "white", "hueso", "ivory", "perla", "crudo"]),
    ("Café / Miel / Beige", ["cafe", "café", "miel", "camel", "tan", "chocolate", "arena", "beige", "caqui", "khaki"]),
    ("Azul / Mezclilla", ["azul", "marino", "navy", "denim", "mezclilla", "celeste", "rey"]),
    ("Rojo / Vino", ["rojo", "vino", "tinto", "guinda", "coral", "terracota"]),
    ("Gris / Plata", ["gris", "plata", "silver", "plomo", "oxford", "jaspe"]),
    ("Rosa / Lila", ["rosa", "fiusha", "fucsia", "lila", "morado", "lavanda", "bugambilia"]),
    ("Verde / Olivo", ["verde", "olivo", "militar", "menta", "esmeralda", "sage"]),
)

CATEGORIAS_ENVIO_LIGERO: Tuple[str, ...] = ("Camisa / Blusa / Playera", "Ropa Interior / Pijama")
CATEGORIAS_ENVIO_MEDIO: Tuple[str, ...] = ("Pantalón / Jeans", "Vestido / Falda")


# ===========================================================================
# 1. FUNCIONES DE CLASIFICACIÓN Y NORMALIZACIÓN HÍBRIDA (ROPA + CALZADO)
# ===========================================================================
def es_prenda_textil(nombre_modelo: str) -> bool:
    """Determina si un artículo pertenece a la división de Ropa/Textil según su nombre."""
    nombre_low: str = str(nombre_modelo).lower()
    return any(palabra in nombre_low for palabra in PALABRAS_ROPA)


def normalizar_talla_universal(valor_talla: str | float, es_ropa: bool = False) -> str:
    """
    Estandariza tallas tanto de Ropa (letras y cintura/edad) como de Calzado (cm MX).

    Returns:
        str: Talla estandarizada ('CH', 'M', 'G', 'EG', '32', 'UNITALLA' o '25.5').
    """
    if pd.isna(valor_talla):
        return "N/A"

    texto_limpio: str = str(valor_talla).strip().upper()
    if not texto_limpio:
        return "N/A"
    if texto_limpio in MAPA_TALLAS_LETRA:
        return MAPA_TALLAS_LETRA[texto_limpio]

    # Si contiene letras y no es simplemente "TALLA X", devolver en mayúsculas
    if re.search(r"[A-Z]", texto_limpio) and not texto_limpio.startswith("TALLA"):
        return texto_limpio
    return _normalizar_talla_numerica(texto_limpio, es_ropa)


def _normalizar_talla_numerica(texto_limpio: str, es_ropa: bool) -> str:
    """Talla numérica: cintura/edad de ropa (entero) o calzado en centímetros (un decimal)."""
    coincidencias = re.findall(r"\d+(?:\.\d+)?", texto_limpio)
    if not coincidencias:
        return texto_limpio

    numero: float = float(coincidencias[0])

    # Si es Ropa (ej. Pantalón talla 28, 30, 32 o Infantil 4, 6, 8, 10, 12), mantener entero
    es_cintura = numero >= 28.0 and numero.is_integer() and "." not in texto_limpio
    if es_ropa or es_cintura:
        return str(int(numero)) if numero.is_integer() else f"{numero:.1f}"

    # Si es Calzado con numeración abreviada (ej. 2.0 a 11.5 -> 22.0 a 31.5 cm)
    if 2.0 <= numero <= 11.5:
        numero += 20.0
    return f"{numero:.1f}"


def _primera_etiqueta(texto: str, reglas: Reglas, default: str) -> str:
    """Etiqueta de la primera regla que tenga alguna de sus palabras dentro del texto."""
    return next(
        (etiqueta for etiqueta, palabras in reglas if any(p in texto for p in palabras)),
        default,
    )


def _rango_tallas_calzado(lista_tallas: List[str]) -> Tuple[float, float]:
    """(mínima, máxima) de las tallas de calzado en cm; (0.0, 0.0) si no hay ninguna."""
    tallas_num_calzado = [
        float(t) for t in lista_tallas
        if re.match(r"^\d{2}\.\d$", str(t))
    ]
    if not tallas_num_calzado:
        return 0.0, 0.0
    return min(tallas_num_calzado), max(tallas_num_calzado)


def _detectar_genero(nombre_low: str, lista_tallas: List[str]) -> str:
    """Género por palabras del nombre y, si no hay, por el rango de tallas de calzado."""
    t_min, t_max = _rango_tallas_calzado(lista_tallas)

    if any(k in nombre_low for k in PALABRAS_INFANTIL) or 0.0 < t_max <= 21.5:
        return GENERO_INFANTIL
    genero = _primera_etiqueta(nombre_low, REGLAS_GENERO, "")
    if genero:
        return genero
    if t_max >= 27.0 or t_min >= 25.0:
        return "Hombre"
    if 21.5 <= t_max <= 26.5:
        return "Mujer"
    return "Unisex"


def clasificar_atributos_catalogo(
    nombre_modelo: str, lista_tallas: List[str]
) -> Tuple[str, str, str]:
    """
    Deduce Género, Categoría (Ropa o Calzado) y Color a partir del nombre y sus tallas.

    Returns:
        Tuple[str, str, str]: (genero, categoria, color)
    """
    n: str = nombre_modelo.lower()
    categoria = _primera_etiqueta(n, REGLAS_CATEGORIA, CATEGORIA_DEFAULT)
    genero = _detectar_genero(n, lista_tallas)
    color = _primera_etiqueta(n, REGLAS_COLOR, COLOR_DEFAULT)
    return genero, categoria, color


# ===========================================================================
# 2. FUNCIONES FINANCIERAS Y UNIT ECONOMICS POR CATEGORÍA
# ===========================================================================
def estimar_envio_mercado_envios(costo_socio: float, categoria: str) -> float:
    """
    Determina el costo real de la guía de Mercado Envíos diferenciando el peso volumétrico
    de prendas textiles ligeras (< 0.5 kg), prendas medias (< 1 kg) y calzado en caja.
    """
    # Ropa ligera (Blusas, Camisas, Playeras, Ropa Interior): Paquete bolsa < 500g
    if categoria in CATEGORIAS_ENVIO_LIGERO:
        return 86.0 if costo_socio < 350.0 else 94.0

    # Ropa peso medio (Pantalones, Jeans, Vestidos): Paquete 500g a 1kg
    if categoria in CATEGORIAS_ENVIO_MEDIO:
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


def redondeo_minimo_terminado_en_9(valor: float) -> float:
    """
    Menor precio terminado en 9 que NO queda por debajo del valor (ej. 642 -> 649, 649.2 -> 659).
    A diferencia de `redondeo_comercial_decena`, nunca baja del valor, así que respeta un mínimo.
    """
    if valor <= 0.0:
        return 0.0
    return float(math.ceil((valor - 9.0) / 10.0) * 10 + 9)


def calcular_unit_economics_por_articulo(
    costo_socio: float,
    categoria: str = "Casual / Urbano",
    utilidad_sobre_venta: float = UTILIDAD_MINIMA_SOBRE_VENTA,
) -> Dict[str, float]:
    """
    Despeja algebraicamente el Precio de Venta en Mercado Libre (P) para que la utilidad sea
    al menos el 10% del propio precio de venta:
      P = [ Costo * (1 + Merma_3%) + Envío + Logística ]
          / [ 1 - Comisión_17.5% - Retención_SAT_9.05% - Utilidad_10% ]
    El redondeo comercial es hacia arriba, así que la utilidad nunca baja del mínimo.
    `precio_piso_ml` es ese mismo precio: por debajo de él no se cumple la utilidad mínima.

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

    costos_fijos: float = (costo_socio * (1.0 + TASA_MERMA_SOBRE_COSTO)) + envio + LOGISTICA_POR_PIEZA
    precio_ml: float = redondeo_minimo_terminado_en_9(
        costos_fijos / (denominador_retenciones - utilidad_sobre_venta)
    )
    precio_piso_ml: float = precio_ml

    comision_pesos: float = round(precio_ml * COMISION_ML_CLASICA, 2)
    impuestos_sat_pesos: float = round(precio_ml * RETENCION_SAT_EFECTIVA, 2)
    fondo_merma_pesos: float = round(costo_socio * TASA_MERMA_SOBRE_COSTO, 2)

    utilidad_neta: float = round(
        precio_ml - costo_socio - envio - LOGISTICA_POR_PIEZA
        - comision_pesos - impuestos_sat_pesos - fondo_merma_pesos,
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
def _agregar_columnas_faltantes(conn: sqlite3.Connection, tabla: str, esquema: Dict[str, str]) -> None:
    existentes = [c[1] for c in conn.execute(f"PRAGMA table_info({tabla})").fetchall()]
    for col, tipo in esquema.items():
        if col not in existentes:
            conn.execute(f"ALTER TABLE {tabla} ADD COLUMN {col} {tipo};")


def asegurar_esquema_financiero(conn: sqlite3.Connection) -> None:
    """Verifica y crea las columnas requeridas en SQLite."""
    _agregar_columnas_faltantes(conn, "zapatos_candidatos", {
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
    })
    _agregar_columnas_faltantes(conn, "historial_stock_21_dias", {
        "talla_norm": "TEXT DEFAULT 'N/A'",
        "reserva_operativa": f"INTEGER DEFAULT {RESERVA_FIJA_POR_TALLA}",
        "stock_seguro_publicable": "INTEGER DEFAULT 0",
    })
    conn.commit()


def _sanitizar_historial_tallas(
    conn: sqlite3.Connection, df_hist: pd.DataFrame, mapa_es_ropa: Dict[str, bool]
) -> pd.DataFrame:
    """
    FASE 1: normaliza tallas, aplica la reserva por talla y la guarda en SQLite.
    Devuelve la última lectura de cada (SKU, talla normalizada).
    """
    if df_hist.empty:
        return pd.DataFrame(
            columns=["id_ps", "talla_norm", "stock_vallejo", "stock_seguro_publicable"]
        )

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

    conn.executemany(
        """
        UPDATE historial_stock_21_dias
        SET talla_norm = ?,
            reserva_operativa = ?,
            stock_seguro_publicable = ?
        WHERE rowid = ?
        """,
        list(
            zip(
                df_hist["talla_norm"],
                df_hist["reserva_operativa"],
                df_hist["stock_seguro_publicable"],
                df_hist["rowid"],
            )
        ),
    )

    ordenado = df_hist.sort_values("fecha") if "fecha" in df_hist.columns else df_hist
    return ordenado.drop_duplicates(subset=["id_ps", "talla_norm"], keep="last").copy()


def _resumir_tallas_por_sku(df_tallas_ult: pd.DataFrame) -> Dict[str, Dict[str, Any]]:
    """Mapas por SKU: stock físico, stock publicable, tallas seguras, lista de tallas y detalle."""
    if df_tallas_ult.empty:
        return {"fisico": {}, "publicable": {}, "seguras": {}, "lista": {}, "detalle": {}}

    por_sku = df_tallas_ult.groupby("id_ps")
    df_seguras = df_tallas_ult[df_tallas_ult["stock_seguro_publicable"] > 0].copy()
    detalle: Dict[str, str] = {}
    if not df_seguras.empty:
        df_seguras["etiqueta_t"] = (
            df_seguras["talla_norm"].astype(str)
            + " ("
            + df_seguras["stock_seguro_publicable"].astype(str)
            + ")"
        )
        detalle = (
            df_seguras.groupby("id_ps")["etiqueta_t"]
            .apply(lambda s: ", ".join(s.astype(str)))
            .to_dict()
        )

    return {
        "fisico": por_sku["stock_vallejo"].sum().to_dict(),
        "publicable": por_sku["stock_seguro_publicable"].sum().to_dict(),
        "seguras": df_seguras.groupby("id_ps")["talla_norm"].count().to_dict(),
        "lista": por_sku["talla_norm"].apply(list).to_dict(),
        "detalle": detalle,
    }


def _estado_fotos(id_ps: str) -> Tuple[int, int, str]:
    """Cuenta las láminas HD del SKU. Devuelve (conteo, fotos_ok, texto de estado)."""
    carpeta_sku = DIR_CATALOGO / id_ps
    conteo_fotos: int = sum(1 for nombre in ARCHIVOS_LAMINAS if (carpeta_sku / nombre).exists())
    fotos_ok: int = 1 if conteo_fotos == len(ARCHIVOS_LAMINAS) else 0
    return conteo_fotos, fotos_ok, f"{conteo_fotos}/4 {'✅' if fotos_ok else '❌'}"


def _diagnosticar_sku(
    costo: float,
    conteo_fotos: int,
    fotos_ok: int,
    n_tallas_seguras: int,
    stock_publicable: int,
    categoria: str,
    utilidad_neta: float,
) -> Tuple[str, str]:
    """Semáforo de publicación y su motivo."""
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

    if motivos_rechazo:
        return SEMAFORO_NO_RECOMENDADO, " · ".join(motivos_rechazo)
    return SEMAFORO_RECOMENDADO, (
        f"Óptimo ({categoria}): {n_tallas_seguras} tallas seguras ({stock_publicable} pzas), "
        f"4/4 fotos HD y ganancia limpia de ${utilidad_neta:,.2f}/unidad."
    )


def _calcular_registro_sku(id_ps: str, nombre: str, costo: float, resumen: Dict[str, Dict[str, Any]]) -> tuple:
    """FASE 2 para un SKU: atributos, unit economics, fotos y semáforo (fila lista para el UPDATE)."""
    stock_fisico: int = int(resumen["fisico"].get(id_ps, 0))
    stock_publicable: int = int(resumen["publicable"].get(id_ps, 0))
    n_tallas_seguras: int = int(resumen["seguras"].get(id_ps, 0))
    detalle_tallas: str = resumen["detalle"].get(id_ps, "Sin tallas > 6 pzas")

    genero, categoria, color = clasificar_atributos_catalogo(nombre, resumen["lista"].get(id_ps, []))
    fin = calcular_unit_economics_por_articulo(costo, categoria)
    conteo_fotos, fotos_ok, fotos_status = _estado_fotos(id_ps)
    semaforo, motivo_txt = _diagnosticar_sku(
        costo, conteo_fotos, fotos_ok, n_tallas_seguras, stock_publicable, categoria, fin["utilidad_neta_ml"]
    )

    return (
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


POSICION_SEMAFORO: int = 18  # índice del semáforo dentro de la fila de _calcular_registro_sku


def _guardar_registros(conn: sqlite3.Connection, registros: List[tuple]) -> None:
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
        registros,
    )
    conn.commit()


def _sanitizar_candidatos(df_cand: pd.DataFrame) -> pd.DataFrame:
    df_cand["id_ps"] = df_cand["id_ps"].fillna("").astype(str).str.strip()
    df_cand["nombre_modelo"] = (
        df_cand["nombre_modelo"].fillna("Modelo Sin Nombre").astype(str).str.strip()
    )
    if "costo_socio" in df_cand.columns:
        df_cand["costo_socio"] = (
            pd.to_numeric(df_cand["costo_socio"], errors="coerce").fillna(0.0).clip(lower=0.0)
        )
    else:
        df_cand["costo_socio"] = 0.0
    return df_cand


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

    df_cand = _sanitizar_candidatos(df_cand)
    mapa_es_ropa: Dict[str, bool] = {
        id_ps: es_prenda_textil(nombre)
        for id_ps, nombre in zip(df_cand["id_ps"], df_cand["nombre_modelo"])
    }

    # FASE 1: sanitización del histórico de tallas (ropa + calzado)
    resumen = _resumir_tallas_por_sku(_sanitizar_historial_tallas(conn, df_hist, mapa_es_ropa))

    # FASE 2: unit economics internos y semáforo
    df_validos = df_cand[df_cand["id_ps"] != ""]
    registros_actualizacion: List[tuple] = [
        _calcular_registro_sku(str(id_ps), str(nombre), float(costo), resumen)
        for id_ps, nombre, costo in zip(df_validos["id_ps"], df_validos["nombre_modelo"], df_validos["costo_socio"])
    ]
    _guardar_registros(conn, registros_actualizacion)
    conn.close()

    con_costo_totales: int = int((df_validos["costo_socio"] > 0).sum())
    recomendados_totales: int = sum(
        1 for registro in registros_actualizacion if registro[POSICION_SEMAFORO] == SEMAFORO_RECOMENDADO
    )
    print(f"✅ Catálogo total en BD procesado : {len(registros_actualizacion):,} SKUs")
    print(f"💰 Artículos con Costo Auditado   : {con_costo_totales:,} SKUs")
    print(f"🚀 Artículos ✅ RECOMENDADOS       : {recomendados_totales:,} SKUs")
    print("=" * 75)


if __name__ == "__main__":
    ejecutar_motor_financiero_y_sanitizacion()
