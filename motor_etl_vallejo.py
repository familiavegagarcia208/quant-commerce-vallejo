# -*- coding: utf-8 -*-
"""
========================================================================================
ZAPATERIA VALLEJO - ARQUITECTURA ETL DE PRODUCCION (REACT SPA + BAYESIANO UCB + MELI 24H)
========================================================================================
Integra integramente:
1. RepositorioSQLiteETL        : Persistencia en `data/zapateria_vallejo.db` (WAL) + Doble Carril
                                 y auto-migracion completa de columnas.
2. ExtractorReactSPA           : Hidratacion de sesion en Price Shoes, clic en tarjeta de
                                 producto e interceptacion XHR/Fetch + __NEXT_DATA__ + DOM.
3. MotorMatematicoBayesianoUCB : Curva de decaimiento (lambda), Media Bayesiana, Score UCB y
                                 Blindaje Anti-Quiebre a 24 horas para el Top 100 de MeLi.
4. EstudioFotograficoSinergia  : Renderizador de 4 laminas HD (1200x1200px) para Streamlit.
========================================================================================
"""

from __future__ import annotations

import argparse
from contextlib import closing
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import io
import json
import logging
import math
import random
import re
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

import numpy as np
import pandas as pd
from PIL import Image, ImageDraw, ImageFont, ImageOps

try:
    from playwright.sync_api import (
        Browser,
        BrowserContext,
        Page,
        Request,
        Response,
        sync_playwright,
    )
    PLAYWRIGHT_DISPONIBLE = True
except ImportError:
    PLAYWRIGHT_DISPONIBLE = False

try:
    from curl_cffi import requests as tls_requests
    CURL_CFFI_DISPONIBLE = True
except ImportError:
    CURL_CFFI_DISPONIBLE = False


# ==============================================================================
# 1. CONFIGURACION DE ARQUITECTURA E HIPERPARAMETROS MATEMATICOS
# ==============================================================================
RUTA_BD = Path("data/zapateria_vallejo.db")
RUTA_COLA_ML = Path("data/cola_sincronizacion_ml.json")

TAMANO_LOTE_ETL = 25                # SKUs por ejecucion cada 2h (8 Carril A + 17 Carril B)
CUOTA_CARRIL_A_VIVO = 8             # SKUs del Top 100 MeLi auditados con prioridad 24h
CUOTA_CARRIL_B_VIVO = 17            # SKUs nuevos/antiguos para avanzar el barrido de 8,165
TIEMPO_LIMITE_CORRIDA_SEG = 15 * 60 # 15 minutos maximo por corrida en GitHub Actions
TTL_MAXIMO_TOKEN_SEG = 1200         # Renovacion preventiva de token cada 20 min
PAUSA_MIN_SEG = 6.0                 # Pausa minima entre productos con sesion viva
PAUSA_MAX_SEG = 12.0                # Pausa maxima entre productos

UMBRAL_STOCK_CRITICO = 3            # <= 3 unidades dispara BAJA_AUTOMATICA
VENTANA_EXPLORACION_MIN_LECTURAS = 4
DIAS_MAX_ESTANCAMIENTO = 7.0
TASA_CAIDA_ALERTA_DIARIA = 4.0

PRIOR_PESO_ALPHA = 2.0
PRIOR_ROTACION_MU0 = 0.75
CONSTANTE_UCB_C = 1.20
SCORE_MINIMO_ACTIVO = 0.40


class Config:
    RUTA_BD: Path = RUTA_BD
    RUTA_BD_SECUNDARIA: Path = Path("data/sinergia.db")
    RUTA_COLA_MELI: Path = RUTA_COLA_ML
    RUTA_CATALOGO_LISTO: Path = Path("output/catalogo_listo")
    RUTA_CACHE_RAW: Path = Path("output/temp/raw_cache")
    RUTA_LOG_PUBLICO: Path = Path("logs/sinergia_publico.log")
    RUTA_LOG_INTERNO: Path = Path("logs/internos.log")

    CUOTA_CARRIL_A_MELI_24H: int = 30
    CUOTA_CARRIL_B_SEMANAL: int = 100

    LIMITE_CRITICO_BAJA: int = UMBRAL_STOCK_CRITICO
    LECTURAS_GRACIA_UCB: int = VENTANA_EXPLORACION_MIN_LECTURAS
    DIAS_MAX_ESTANCAMIENTO: float = DIAS_MAX_ESTANCAMIENTO
    PENDIENTE_DECAY_PELIGROSA: float = TASA_CAIDA_ALERTA_DIARIA
    FACTOR_UCB_C: float = CONSTANTE_UCB_C
    MEDIA_BAYESIANA_MIN: float = SCORE_MINIMO_ACTIVO
    HORAS_MIN_AUTONOMIA_MELI: float = 24.0

    COMISION_MELI_PCT: float = 17.5
    COSTO_ENVIO_MXN: float = 125.0
    MARGEN_OBJETIVO_PCT: float = 22.0
    RETENCION_IMPUESTOS_PCT: float = 3.5
    BUFFER_STOCK: int = 3

    TIPOS_ACTIVO: Tuple[str, ...] = (
        "01_portada_blanca",
        "02_vista_angulo",
        "98_lamina_impacto",
        "99_lamina_ficha",
    )

    MARCAS_RESTRINGIDAS_MELI: Tuple[str, ...] = (
        "NIKE",
        "ADIDAS",
        "PUMA",
        "REEBOK",
        "CONVERSE",
        "VANS",
        "NEW BALANCE",
        "UNDER ARMOUR",
        "SKECHERS",
        "FILA",
        "DISNEY",
        "MARVEL",
        "BARBIE",
        "HOT WHEELS",
        "PAW PATROL",
        "HELLO KITTY",
        "SONIC",
        "MINNIE",
        "MICKEY",
        "STITCH",
        "STICH",
        "BLUEY",
        "LEVI'S",
        "LEVIS",
        "GUESS",
        "LACOSTE",
        "TOMMY HILFIGER",
        "CATERPILLAR",
        "TIMBERLAND",
    )

    USER_AGENTS: Tuple[str, ...] = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36",
    )

    @classmethod
    def rutas_esperadas_sku(cls, sku: str) -> Dict[str, Path]:
        s = sanitizar_sku(sku)
        carpeta = cls.RUTA_CATALOGO_LISTO / s
        return {t: carpeta / f"SINERGIA_{s}_{t}.png" for t in cls.TIPOS_ACTIVO}

    @classmethod
    def rutas_cache_raw_sku(cls, sku: str) -> List[Path]:
        s = sanitizar_sku(sku)
        return [
            cls.RUTA_CACHE_RAW / f"{s}_raw_1.png",
            cls.RUTA_CACHE_RAW / f"{s}_raw_2.png",
        ]


# ==============================================================================
# 2. CONTRATOS DE DATOS (DTOs)
# ==============================================================================
@dataclass(frozen=True)
class ProductoCandidato:
    id_ps: str
    nombre_modelo: str
    url_real: str
    precio_ml: float
    stock_previo_registrado: int
    carril: str = "CARRIL_B"


@dataclass(frozen=True)
class LecturaExtraida:
    id_ps: str
    stock_confirmado: int
    url_resuelta: str
    fuente_dato: str
    token_activo: bool


@dataclass(frozen=True)
class DictamenTransformacion:
    id_ps: str
    stock_actual: int
    delta_stock: int
    tasa_decaimiento_lambda: float
    dias_estancado: float
    media_bayesiana: float
    bono_ucb: float
    score_ucb: float
    num_lecturas: int
    estatus_etl: str
    accion_ml: str
    motivo_tecnico: str
    fuente_extraccion: str


# ==============================================================================
# 3. UTILIDADES DE DOMINIO Y VALIDACION DE TIPOS
# ==============================================================================
def sanitizar_sku(raw: Any) -> str:
    txt = str(raw or "").strip().upper()
    if txt.endswith(".0"):
        txt = txt[:-2]
    return re.sub(r"[^A-Z0-9]", "", txt)


def num_seguro(valor: Any, default: float = 0.0) -> float:
    v = pd.to_numeric(valor, errors="coerce")
    return float(default) if pd.isna(v) else float(v)


def col_num(df: pd.DataFrame, col: str, default: float = 0.0) -> pd.Series:
    if col in df.columns:
        s = df[col]
        if isinstance(s, pd.DataFrame):
            s = s.iloc[:, 0]
        return pd.to_numeric(s, errors="coerce").fillna(float(default)).astype(float)
    return pd.Series(float(default), index=df.index, dtype="float64")


def col_int(df: pd.DataFrame, col: str, default: int = 0) -> pd.Series:
    return col_num(df, col, float(default)).round().astype(int)


def col_txt(df: pd.DataFrame, col: str, default: str = "") -> pd.Series:
    if col in df.columns:
        s = df[col]
        if isinstance(s, pd.DataFrame):
            s = s.iloc[:, 0]
        return s.fillna(default).astype(str)
    return pd.Series(str(default), index=df.index, dtype="object")


def configurar_loggers() -> Tuple[logging.Logger, logging.Logger]:
    Config.RUTA_LOG_PUBLICO.parent.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s", "%Y-%m-%d %H:%M:%S"
    )

    log_pub = logging.getLogger("vallejo_pub")
    if not log_pub.handlers:
        log_pub.setLevel(logging.INFO)
        fh_p = logging.FileHandler(Config.RUTA_LOG_PUBLICO, encoding="utf-8")
        fh_p.setFormatter(fmt)
        log_pub.addHandler(fh_p)

    log_int = logging.getLogger("vallejo_int")
    if not log_int.handlers:
        log_int.setLevel(logging.INFO)
        fh_i = logging.FileHandler(Config.RUTA_LOG_INTERNO, encoding="utf-8")
        fh_i.setFormatter(fmt)
        log_int.addHandler(fh_i)

    return log_pub, log_int


def es_marca_restringida_meli(
    marca: str,
    titulo: str,
    lista_prohibidas: Sequence[str] = Config.MARCAS_RESTRINGIDAS_MELI,
) -> bool:
    texto = f"{marca or ''} {titulo or ''}".upper()
    for p in lista_prohibidas:
        p_clean = p.strip().upper()
        if p_clean and re.search(rf"\b{re.escape(p_clean)}\b", texto):
            return True
    return False


def extraer_marca_real(titulo: str, marca_actual: str = "") -> str:
    m_limpia = str(marca_actual or "").strip()
    if m_limpia and m_limpia.upper() not in (
        "",
        "NONE",
        "NAN",
        "SINERGIA",
        "ORIGINAL",
        "GENERAL",
        "LINEA CATALOGO",
        "LÍNEA CATÁLOGO",
    ):
        return m_limpia
    texto = f"{titulo or ''} {marca_actual or ''}".upper()
    patrones = (
        (r"\bADIDAS\b|GRAND COURT", "Adidas"),
        (r"\bPUMA\b|TIFOSI", "Puma"),
        (r"\bNIKE\b|COURT VISION|AIR MAX", "Nike"),
        (r"\bSONIC\b", "Sonic"),
        (r"\bBLUEY\b", "Bluey"),
        (r"\bSTITCH\b|\bSTICH\b|\bMINNIE\b|\bMICKEY\b|\bDISNEY\b", "Disney"),
        (r"\bFLEXI\b", "Flexi"),
        (r"\bCHARLY\b", "Charly"),
        (r"\bPIRMA\b", "Pirma"),
        (r"\bPANAM\b", "Panam"),
    )
    for pat, nom in patrones:
        if re.search(pat, texto):
            return nom
    return "Línea Catálogo"


def clasificar_catalogo_origen(
    titulo: str, marca: str = "", catalogo_actual: str = ""
) -> str:
    c_act = str(catalogo_actual or "").strip()
    if c_act and c_act.upper() not in (
        "",
        "NONE",
        "NAN",
        "GENERAL",
        "CALZADO",
        "PRODUCTOS",
    ):
        return c_act
    t_up = f"{titulo or ''} {marca or ''}".upper()
    if any(
        k in t_up
        for k in (
            "ESCOLAR",
            "SONIC",
            "BLUEY",
            "STICH",
            "STITCH",
            "MINNIE",
            "INFANTIL",
            "LUCES",
            "NIÑO",
            "NINA",
        )
    ):
        return "Catálogo Escolares e Infantil"
    if any(
        k in t_up
        for k in (
            "MOTORSPORT",
            "GRAND COURT",
            "ADIDAS",
            "PUMA",
            "TIFOSI",
            "NIKE",
            "RUNNING",
            "DEPORTIVO",
            "CHARLY",
            "PIRMA",
        )
    ):
        return "Catálogo Importados y Deportivos"
    if any(k in t_up for k in ("BOTA", "BOTIN", "CATERPILLAR", "TIMBERLAND")):
        return "Catálogo Botas y Botines"
    if any(k in t_up for k in ("SANDALIA", "HUARACHE", "CHANCLA")):
        return "Catálogo Sandalias"
    if any(
        k in t_up for k in ("MOCASIN", "PIEL", "VESTIR", "CONFORT", "FLEXI", "OXFORD")
    ):
        return "Catálogo Confort y Vestir"
    return "Catálogo Urbano y Casual"


def calcular_unit_economics_item(
    costo_base: float,
    comision_meli_pct: float = Config.COMISION_MELI_PCT,
    costo_envio_mxn: float = Config.COSTO_ENVIO_MXN,
    margen_objetivo_pct: float = Config.MARGEN_OBJETIVO_PCT,
    retencion_impuestos_pct: float = Config.RETENCION_IMPUESTOS_PCT,
) -> Dict[str, float]:
    costo = max(0.0, float(costo_base or 0.0))
    if costo <= 0.0:
        return {
            "costo_base": 0.0,
            "precio_sugerido": 0.0,
            "comision_meli_estimada": 0.0,
            "costo_envio": 0.0,
            "impuestos_estimados": 0.0,
            "utilidad_neta": 0.0,
            "margen_neto_pct": 0.0,
        }
    c_dec = min(0.50, max(0.0, float(comision_meli_pct) / 100.0))
    m_dec = min(0.60, max(0.05, float(margen_objetivo_pct) / 100.0))
    i_dec = min(0.25, max(0.0, float(retencion_impuestos_pct) / 100.0))
    env = max(0.0, float(costo_envio_mxn))

    den = max(0.12, 1.0 - c_dec - m_dec - i_dec)
    p_bruto = (costo + env) / den
    decena = math.ceil(p_bruto / 10.0) * 10
    p_venta = float(decena - 1)
    if p_venta < p_bruto:
        p_venta += 10.0

    comision = round(p_venta * c_dec, 2)
    impuestos = round(p_venta * i_dec, 2)
    utilidad = round(p_venta - costo - env - comision - impuestos, 2)
    margen_real = round((utilidad / p_venta) * 100.0 if p_venta > 0 else 0.0, 1)

    return {
        "costo_base": round(costo, 2),
        "precio_sugerido": round(p_venta, 2),
        "comision_meli_estimada": comision,
        "costo_envio": round(env, 2),
        "impuestos_estimados": impuestos,
        "utilidad_neta": utilidad,
        "margen_neto_pct": margen_real,
    }


def validar_imagen_bytes(raw: bytes) -> bool:
    if not raw or len(raw) < 512:
        return False
    try:
        with Image.open(io.BytesIO(raw)) as im:
            im.verify()
        return True
    except Exception:
        return False


# ==============================================================================
# 4. CAPA 1: REPOSITORIO SQLITE CON AUTO-MIGRACION COMPLETA Y DOBLE CARRIL
# ==============================================================================
class RepositorioSQLiteETL:
    """Gestiona la persistencia en SQLite en modo WAL y selecciona lotes por Doble Carril."""

    def __init__(self, ruta_bd: Path) -> None:
        self.ruta_bd = ruta_bd
        self.ruta_bd.parent.mkdir(parents=True, exist_ok=True)
        self._asegurar_esquema_y_migraciones()

    def _conectar(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.ruta_bd, timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        return conn

    def _obtener_columnas_tabla(self, conn: sqlite3.Connection, tabla: str) -> Set[str]:
        filas = conn.execute(f"PRAGMA table_info({tabla});").fetchall()
        return {str(f["name"]) for f in filas}

    def _asegurar_esquema_y_migraciones(self) -> None:
        with self._conectar() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS zapatos_candidatos (
                    id_ps TEXT PRIMARY KEY,
                    nombre_modelo TEXT,
                    marca TEXT,
                    color TEXT,
                    costo_bodega REAL DEFAULT 0.0,
                    precio_sugerido_ml REAL DEFAULT 0.0,
                    origen_catalogo TEXT,
                    url_real TEXT,
                    stock_total_anterior INTEGER DEFAULT 0,
                    ultima_actualizacion TIMESTAMP
                );
                """
            )

            columnas_actuales = self._obtener_columnas_tabla(conn, "zapatos_candidatos")
            nuevas_columnas = [
                ("url_real", "TEXT"),
                ("stock_total_anterior", "INTEGER DEFAULT 0"),
                ("stock_publicable_total", "INTEGER DEFAULT 0"),
                ("stock_bruto_anterior", "INTEGER DEFAULT 0"),
                ("costo_socio", "REAL DEFAULT 0.0"),
                ("costo_bodega", "REAL DEFAULT 0.0"),
                ("precio_ml", "REAL DEFAULT 0.0"),
                ("precio_sugerido_ml", "REAL DEFAULT 0.0"),
                ("utilidad_neta_ml", "REAL DEFAULT 0.0"),
                ("precio_amazon", "REAL DEFAULT 0.0"),
                ("precio_tiktok", "REAL DEFAULT 0.0"),
                ("ultima_actualizacion", "TIMESTAMP"),
                ("ultima_sincronizacion", "TIMESTAMP"),
                ("ultimo_intento", "TIMESTAMP"),
                ("estatus_etl", "TEXT DEFAULT 'PENDIENTE'"),
                ("tasa_decaimiento", "REAL DEFAULT 0.0"),
                ("velocidad_diaria", "REAL DEFAULT 0.0"),
                ("media_bayesiana", "REAL DEFAULT 0.0"),
                ("score_ucb", "REAL DEFAULT 0.0"),
                ("num_consultas_hist", "INTEGER DEFAULT 0"),
                ("fuente_ultimo_stock", "TEXT"),
                ("id_publicacion_ml", "TEXT"),
                ("estatus_ml", "TEXT DEFAULT 'NO_PUBLICADO'"),
                ("fecha_carga_ml", "TIMESTAMP"),
                ("fecha_baja_ml", "TIMESTAMP"),
            ]
            for col, tipo in nuevas_columnas:
                if col not in columnas_actuales:
                    conn.execute(
                        f"ALTER TABLE zapatos_candidatos ADD COLUMN {col} {tipo};"
                    )

            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS historial_inventario (
                    id_consulta INTEGER PRIMARY KEY AUTOINCREMENT,
                    id_ps TEXT NOT NULL,
                    stock_detectado INTEGER NOT NULL DEFAULT 0,
                    stock_bruto_detectado INTEGER DEFAULT 0,
                    delta_stock INTEGER DEFAULT 0,
                    tasa_decaimiento_lambda REAL DEFAULT 0.0,
                    dias_estancado REAL DEFAULT 0.0,
                    media_bayesiana REAL DEFAULT 0.0,
                    score_ucb REAL DEFAULT 0.0,
                    estatus_calculado TEXT NOT NULL DEFAULT 'AUDITADO',
                    motivo_decision TEXT,
                    fuente_extraccion TEXT,
                    fecha_hora_consulta TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_hist_sku_fecha ON historial_inventario(id_ps, fecha_hora_consulta);"
            )

            cols_hist = self._obtener_columnas_tabla(conn, "historial_inventario")
            columnas_hist_req = [
                ("fuente_extraccion", "TEXT"),
                ("dias_estancado", "REAL DEFAULT 0.0"),
                ("motivo_decision", "TEXT"),
                ("stock_bruto_detectado", "INTEGER DEFAULT 0"),
                ("fotos_procesadas", "INTEGER DEFAULT 0"),
            ]
            for col_h, tipo_h in columnas_hist_req:
                if col_h not in cols_hist:
                    conn.execute(
                        f"ALTER TABLE historial_inventario ADD COLUMN {col_h} {tipo_h};"
                    )
            conn.commit()

    def obtener_lote_candidatos(
        self,
        limite: int = TAMANO_LOTE_ETL,
        skus_forzados: Optional[List[str]] = None,
    ) -> List[ProductoCandidato]:
        with self._conectar() as conn:
            cols = self._obtener_columnas_tabla(conn, "zapatos_candidatos")
            col_nombre = (
                "nombre_modelo"
                if "nombre_modelo" in cols
                else ("modelo" if "modelo" in cols else "'Zapato'")
            )
            col_precio = (
                "precio_ml"
                if "precio_ml" in cols
                else ("precio_sugerido_ml" if "precio_sugerido_ml" in cols else "0.0")
            )

            if skus_forzados:
                limpios = [sanitizar_sku(s) for s in skus_forzados if sanitizar_sku(s)]
                placeholders = ",".join("?" for _ in limpios)
                filas = conn.execute(
                    f"""
                    SELECT id_ps,
                           COALESCE({col_nombre}, 'SKU ' || id_ps) AS nombre,
                           COALESCE(url_real, '') AS url,
                           COALESCE({col_precio}, 0.0) AS precio_ml,
                           COALESCE(stock_total_anterior, 0) AS stock_prev
                    FROM zapatos_candidatos
                    WHERE id_ps IN ({placeholders})
                    """,
                    tuple(limpios),
                ).fetchall()
                return [
                    ProductoCandidato(
                        id_ps=str(f["id_ps"]).strip(),
                        nombre_modelo=str(f["nombre"]).strip(),
                        url_real=str(f["url"]).strip(),
                        precio_ml=float(f["precio_ml"] or 0.0),
                        stock_previo_registrado=int(f["stock_prev"] or 0),
                        carril="FORZADO",
                    )
                    for f in filas
                ]

            # 1. CARRIL A: Vigilancia 24h de SKUs con stock activo (> 3) cuya lectura tenga > 6h
            cuota_a = min(CUOTA_CARRIL_A_VIVO, max(1, limite // 3))
            filas_a = conn.execute(
                f"""
                SELECT id_ps,
                       COALESCE({col_nombre}, 'SKU ' || id_ps) AS nombre,
                       COALESCE(url_real, '') AS url,
                       COALESCE({col_precio}, 0.0) AS precio_ml,
                       COALESCE(stock_total_anterior, 0) AS stock_prev
                FROM zapatos_candidatos
                WHERE id_ps IS NOT NULL AND TRIM(id_ps) != ''
                  AND (COALESCE(stock_total_anterior, 0) > 3 OR COALESCE(stock_publicable_total, 0) > 3)
                  AND COALESCE(ultima_actualizacion, ultimo_intento, '2000-01-01') <= datetime('now', '-6 hours')
                ORDER BY COALESCE(tasa_decaimiento, 0.0) DESC,
                         COALESCE(ultima_actualizacion, ultimo_intento, '2000-01-01') ASC
                LIMIT ?
                """,
                (cuota_a,),
            ).fetchall()

            seleccionados: List[ProductoCandidato] = [
                ProductoCandidato(
                    id_ps=str(f["id_ps"]).strip(),
                    nombre_modelo=str(f["nombre"]).strip(),
                    url_real=str(f["url"]).strip(),
                    precio_ml=float(f["precio_ml"] or 0.0),
                    stock_previo_registrado=int(f["stock_prev"] or 0),
                    carril="CARRIL_A_24H",
                )
                for f in filas_a
            ]
            ids_ya = {c.id_ps for c in seleccionados}

            # 2. CARRIL B: Barrido Semanal rotativo (prioriza los nunca consultados o mas antiguos)
            restantes = max(1, limite - len(seleccionados))
            filas_b = conn.execute(
                f"""
                SELECT id_ps,
                       COALESCE({col_nombre}, 'SKU ' || id_ps) AS nombre,
                       COALESCE(url_real, '') AS url,
                       COALESCE({col_precio}, 0.0) AS precio_ml,
                       COALESCE(stock_total_anterior, 0) AS stock_prev
                FROM zapatos_candidatos
                WHERE id_ps IS NOT NULL AND TRIM(id_ps) != ''
                ORDER BY COALESCE(ultima_actualizacion, ultimo_intento, '2000-01-01') ASC
                LIMIT ?
                """,
                (restantes + len(ids_ya),),
            ).fetchall()

            for f in filas_b:
                s_id = str(f["id_ps"]).strip()
                if s_id not in ids_ya:
                    seleccionados.append(
                        ProductoCandidato(
                            id_ps=s_id,
                            nombre_modelo=str(f["nombre"]).strip(),
                            url_real=str(f["url"]).strip(),
                            precio_ml=float(f["precio_ml"] or 0.0),
                            stock_previo_registrado=int(f["stock_prev"] or 0),
                            carril="CARRIL_B_SEMANAL",
                        )
                    )
                    ids_ya.add(s_id)
                    if len(seleccionados) >= limite:
                        break

            return seleccionados

    def marcar_intento_omitido(self, id_ps: str) -> None:
        """Actualiza `ultimo_intento` para no atascar la cola en SKUs sin respuesta."""
        with self._conectar() as conn:
            conn.execute(
                "UPDATE zapatos_candidatos SET ultimo_intento = CURRENT_TIMESTAMP WHERE id_ps = ?;",
                (id_ps,),
            )
            conn.commit()

    def obtener_historial_sku(self, id_ps: str) -> Tuple[int, List[sqlite3.Row]]:
        with self._conectar() as conn:
            total_global = int(
                conn.execute(
                    "SELECT COUNT(*) AS n FROM historial_inventario"
                ).fetchone()["n"]
                or 1
            )
            historial = conn.execute(
                """
                SELECT stock_detectado,
                       tasa_decaimiento_lambda,
                       MAX(0.0, (julianday('now') - julianday(fecha_hora_consulta))) AS dias_transcurridos
                FROM historial_inventario
                WHERE id_ps = ?
                ORDER BY fecha_hora_consulta DESC
                LIMIT 25
                """,
                (id_ps,),
            ).fetchall()
            return total_global, historial

    def guardar_resultado_etl(
        self, dictamen: DictamenTransformacion, url_resuelta: str
    ) -> None:
        with self._conectar() as conn:
            conn.execute(
                """
                INSERT INTO historial_inventario (
                    id_ps, stock_detectado, stock_bruto_detectado, delta_stock,
                    tasa_decaimiento_lambda, dias_estancado, media_bayesiana,
                    score_ucb, estatus_calculado, motivo_decision,
                    fuente_extraccion, fecha_hora_consulta
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                """,
                (
                    dictamen.id_ps,
                    dictamen.stock_actual,
                    dictamen.stock_actual,
                    dictamen.delta_stock,
                    dictamen.tasa_decaimiento_lambda,
                    dictamen.dias_estancado,
                    dictamen.media_bayesiana,
                    dictamen.score_ucb,
                    dictamen.estatus_etl,
                    dictamen.motivo_tecnico,
                    dictamen.fuente_extraccion,
                ),
            )

            conn.execute(
                """
                UPDATE zapatos_candidatos
                SET stock_total_anterior = ?,
                    stock_publicable_total = ?,
                    stock_bruto_anterior = ?,
                    url_real = CASE WHEN ? != '' THEN ? ELSE url_real END,
                    ultima_actualizacion = CURRENT_TIMESTAMP,
                    ultimo_intento = CURRENT_TIMESTAMP,
                    estatus_etl = ?,
                    tasa_decaimiento = ?,
                    media_bayesiana = ?,
                    score_ucb = ?,
                    num_consultas_hist = ?,
                    fuente_ultimo_stock = ?,
                    fecha_carga_ml = CASE
                        WHEN ? IN ('ACTIVO', 'EXPLORACION_UCB') THEN COALESCE(fecha_carga_ml, CURRENT_TIMESTAMP)
                        ELSE fecha_carga_ml
                    END,
                    fecha_baja_ml = CASE
                        WHEN ? = 'BAJA_AUTOMATICA' THEN COALESCE(fecha_baja_ml, CURRENT_TIMESTAMP)
                        ELSE NULL
                    END
                WHERE id_ps = ?
                """,
                (
                    dictamen.stock_actual,
                    dictamen.stock_actual,
                    dictamen.stock_actual,
                    url_resuelta,
                    url_resuelta,
                    dictamen.estatus_etl,
                    dictamen.tasa_decaimiento_lambda,
                    dictamen.media_bayesiana,
                    dictamen.score_ucb,
                    dictamen.num_lecturas,
                    dictamen.fuente_extraccion,
                    dictamen.estatus_etl,
                    dictamen.estatus_etl,
                    dictamen.id_ps,
                ),
            )
            conn.commit()

    def consolidar_wal(self) -> None:
        with self._conectar() as conn:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
            conn.execute("PRAGMA optimize;")
            conn.commit()


# ==============================================================================
# 5. CAPA 2: EXTRACTOR REACT SPA (INTERCEPTOR DE APIs XHR/FETCH + TOKENS + DOM)
# ==============================================================================
class AnalizadorPayloadReact:
    """Extrae existencias reales inspeccionando arboles JSON de APIs React y el DOM hidratado."""

    CLAVES_CANTIDAD = {
        "stock",
        "availablequantity",
        "available_quantity",
        "inventory",
        "existencia",
        "quantity",
        "qty",
    }
    CLAVES_COLECCION_VARIANTES = {
        "variants",
        "sizes",
        "tallas",
        "skus",
        "items",
        "children",
        "options",
    }

    @classmethod
    def buscar_stock_en_json(cls, data: Any, id_ps: str) -> Optional[int]:
        nodos_coincidentes: List[Dict[str, Any]] = []
        cls._recolectar_nodos_sku(data, id_ps, nodos_coincidentes)

        if not nodos_coincidentes and isinstance(data, dict):
            nodos_coincidentes = [data]

        for nodo in nodos_coincidentes:
            stock_calculado = cls._extraer_unidades_de_nodo(nodo)
            if stock_calculado is not None:
                return stock_calculado
        return None

    @classmethod
    def _recolectar_nodos_sku(
        cls, obj: Any, id_ps: str, acumulador: List[Dict[str, Any]]
    ) -> None:
        if isinstance(obj, dict):
            valores_texto = [
                str(v) for k, v in obj.items() if not isinstance(v, (dict, list))
            ]
            if any(id_ps in val for val in valores_texto):
                acumulador.append(obj)
            for v in obj.values():
                cls._recolectar_nodos_sku(v, id_ps, acumulador)
        elif isinstance(obj, list):
            for item in obj:
                cls._recolectar_nodos_sku(item, id_ps, acumulador)

    @classmethod
    def _extraer_unidades_de_nodo(cls, nodo: Dict[str, Any]) -> Optional[int]:
        for k, v in nodo.items():
            kl = k.lower()
            if (
                kl in ("instock", "in_stock", "available", "disponible")
                and isinstance(v, bool)
                and not v
            ):
                return 0
            if (
                kl in ("availability", "status")
                and isinstance(v, str)
                and ("outofstock" in v.lower() or "agotado" in v.lower())
            ):
                return 0

        for k, v in nodo.items():
            if (
                k.lower() in cls.CLAVES_COLECCION_VARIANTES
                and isinstance(v, list)
                and len(v) > 0
            ):
                total_variantes = 0
                encontro_metrica = False
                for item in v:
                    if isinstance(item, dict):
                        sub_stock = cls._extraer_unidades_directas(item)
                        if sub_stock is not None:
                            total_variantes += sub_stock
                            encontro_metrica = True
                        elif (
                            item.get("available") is True
                            or item.get("inStock") is True
                        ):
                            total_variantes += 1
                            encontro_metrica = True
                if encontro_metrica:
                    return total_variantes

        directo = cls._extraer_unidades_directas(nodo)
        if directo is not None:
            return directo
        return None

    @classmethod
    def _extraer_unidades_directas(cls, d: Dict[str, Any]) -> Optional[int]:
        for k, v in d.items():
            if (
                k.lower() in cls.CLAVES_CANTIDAD
                and isinstance(v, (int, float))
                and not isinstance(v, bool)
            ):
                return max(0, int(v))
        return None


class ExtractorReactSPA:
    """
    Motor de extraccion que ejecuta el ciclo de vida de React, intercepta los tokens
    generados por el frontend y captura las respuestas JSON de la API interna.
    """

    def __init__(self) -> None:
        self._playwright = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None
        self._page: Optional[Page] = None

        self.token_capturado: Optional[str] = None
        self.inicio_vida_token: float = 0.0
        self.num_renovaciones_token: int = 0
        self.token_requiere_refresh: bool = False
        self._respuestas_json_sku: List[Any] = []

    def iniciar(self) -> None:
        if PLAYWRIGHT_DISPONIBLE:
            self._playwright = sync_playwright().start()
            self._browser = self._playwright.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage"],
            )
            self._renovar_contexto_y_token()

    def cerrar(self) -> None:
        for recurso in (self._page, self._context, self._browser):
            if recurso is not None:
                try:
                    recurso.close()
                except Exception:
                    pass
        if self._playwright is not None:
            try:
                self._playwright.stop()
            except Exception:
                pass

    def _token_caducado(self) -> bool:
        if self.token_requiere_refresh or self.inicio_vida_token == 0.0:
            return True
        return (time.time() - self.inicio_vida_token) >= TTL_MAXIMO_TOKEN_SEG

    def _interceptar_request(self, request: Request) -> None:
        headers = request.headers
        for clave in ("authorization", "x-access-token", "x-api-key", "x-auth-token"):
            if clave in headers and headers[clave]:
                self.token_capturado = headers[clave]

    def _interceptar_response(self, response: Response) -> None:
        if response.status in (401, 403) and (
            "api" in response.url or "graphql" in response.url
        ):
            self.token_requiere_refresh = True
            return

        content_type = response.headers.get("content-type", "").lower()
        if "application/json" in content_type and response.status == 200:
            try:
                data = response.json()
                self._respuestas_json_sku.append(data)
            except Exception:
                pass

    def _renovar_contexto_y_token(self) -> None:
        self.num_renovaciones_token += 1
        print(
            f"    🔑 [React Token Manager] Inicializando sesion limpia y negociando token (#{self.num_renovaciones_token})..."
        )

        if self._context is not None:
            try:
                self._context.close()
            except Exception:
                pass

        assert self._browser is not None
        self._context = self._browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            viewport={"width": 1366, "height": 768},
            locale="es-MX",
        )
        self._page = self._context.new_page()
        self._page.on("request", self._interceptar_request)
        self._page.on("response", self._interceptar_response)

        try:
            self._page.goto(
                "https://www.priceshoes.com/",
                wait_until="domcontentloaded",
                timeout=30000,
            )
            self._page.wait_for_timeout(2500)
        except Exception as exc:
            print(
                f"    ⚠️ [React Token Manager] Aviso en carga inicial del portal: {exc}"
            )

        self.inicio_vida_token = time.time()
        self.token_requiere_refresh = False
        print("    ✅ [React Token Manager] Sesion React hidratada y lista.")

    def extraer_sku(self, candidato: ProductoCandidato) -> Optional[LecturaExtraida]:
        pausa = random.uniform(PAUSA_MIN_SEG, PAUSA_MAX_SEG)
        print(
            f"    ⏳ [Extract | {candidato.carril}] Consultando SKU {candidato.id_ps} (pausa {pausa:.1f}s)..."
        )
        time.sleep(pausa)

        if PLAYWRIGHT_DISPONIBLE and self._browser is not None:
            return self._extraer_con_playwright_react(candidato)
        return self._extraer_fallback_ssr(candidato)

    def _extraer_con_playwright_react(
        self, candidato: ProductoCandidato
    ) -> Optional[LecturaExtraida]:
        if self._token_caducado():
            self._renovar_contexto_y_token()

        self._respuestas_json_sku.clear()
        url_destino = (
            candidato.url_real
            if (candidato.url_real and candidato.url_real.startswith("http"))
            else f"https://www.priceshoes.com/buscar?q={candidato.id_ps}"
        )

        try:
            assert self._page is not None
            self._page.goto(url_destino, wait_until="domcontentloaded", timeout=30000)
            try:
                self._page.wait_for_load_state("networkidle", timeout=7000)
            except Exception:
                self._page.wait_for_timeout(2500)

            if self.token_requiere_refresh:
                print(
                    "    🔄 [React Token Manager] Token expirado detectado en trafico XHR. Renovando..."
                )
                self._renovar_contexto_y_token()
                self._respuestas_json_sku.clear()
                self._page.goto(
                    url_destino, wait_until="domcontentloaded", timeout=30000
                )
                self._page.wait_for_timeout(3000)

            if "/buscar" in self._page.url:
                enlace_prod = self._page.locator(
                    f"a[href*='{candidato.id_ps}'], a[href*='/productos/'], a[href*='/catalogo/']"
                ).first
                if enlace_prod.count() > 0:
                    try:
                        enlace_prod.click(timeout=4000)
                        self._page.wait_for_timeout(2500)
                    except Exception:
                        pass

            url_final = self._page.url

            # ESTRATEGIA 1: Paquetes JSON interceptados de la API de React
            for payload in reversed(self._respuestas_json_sku):
                stock_api = AnalizadorPayloadReact.buscar_stock_en_json(
                    payload, candidato.id_ps
                )
                if stock_api is not None:
                    return LecturaExtraida(
                        id_ps=candidato.id_ps,
                        stock_confirmado=stock_api,
                        url_resuelta=url_final,
                        fuente_dato="API_JSON_INTERCEPTADA",
                        token_activo=True,
                    )

            # ESTRATEGIA 2: Estado hidratado (__NEXT_DATA__ / application/ld+json)
            html_renderizado = self._page.content()
            resultado_ssr = self._parsear_estructuras_hidratadas(
                html_renderizado, candidato.id_ps, url_final
            )
            if resultado_ssr is not None:
                return resultado_ssr

            # ESTRATEGIA 3: Evaluar el DOM renderizado de React (Tallas activas vs Producto agotado)
            stock_dom = self._page.evaluate(
                """(sku) => {
                    const texto = (document.body.innerText || '').toLowerCase();
                    if (texto.includes('no encontramos resultados') || texto.includes('0 resultados') || texto.includes('producto agotado')) {
                        return 0;
                    }
                    const selectoresTallas = document.querySelectorAll(
                        '[data-testid*="size"]:not([disabled]), [class*="size"]:not([disabled]):not([class*="disabled"]), button[class*="talla"]:not([disabled])'
                    );
                    let tallasActivas = 0;
                    selectoresTallas.forEach(el => {
                        const t = (el.innerText || '').trim();
                        if (/^(1[0-9]|2[0-9]|3[0-3])(\\.5)?$/.test(t)) {
                            tallasActivas += 1;
                        }
                    });
                    if (tallasActivas > 0) return tallasActivas * 2;
                    if (texto.includes('agregar al carrito') || texto.includes('comprar ahora') || texto.includes('disponible')) {
                        return 6;
                    }
                    return null;
                }""",
                candidato.id_ps,
            )

            if isinstance(stock_dom, int):
                return LecturaExtraida(
                    id_ps=candidato.id_ps,
                    stock_confirmado=stock_dom,
                    url_resuelta=url_final,
                    fuente_dato="DOM_REACT_HIDRATADO",
                    token_activo=True,
                )

        except Exception as exc:
            print(
                f"    ❌ [Extract Error] Fallo al inspeccionar SKU {candidato.id_ps}: {exc}"
            )

        return None

    def _parsear_estructuras_hidratadas(
        self, html: str, id_ps: str, url_actual: str
    ) -> Optional[LecturaExtraida]:
        match_next = re.search(
            r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>',
            html,
            re.DOTALL | re.IGNORECASE,
        )
        if match_next:
            try:
                data_next = json.loads(match_next.group(1))
                stock_n = AnalizadorPayloadReact.buscar_stock_en_json(
                    data_next, id_ps
                )
                if stock_n is not None:
                    return LecturaExtraida(
                        id_ps, stock_n, url_actual, "NEXT_DATA_SSR", True
                    )
            except Exception:
                pass

        bloques_ld = re.findall(
            r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
            html,
            re.DOTALL | re.IGNORECASE,
        )
        for bloque in bloques_ld:
            try:
                data_ld = json.loads(bloque.strip())
                stock_ld = AnalizadorPayloadReact.buscar_stock_en_json(
                    data_ld, id_ps
                )
                if stock_ld is not None:
                    return LecturaExtraida(
                        id_ps, stock_ld, url_actual, "LD_JSON_SCHEMA", True
                    )
                texto_ld = bloque.lower()
                if "outofstock" in texto_ld:
                    return LecturaExtraida(
                        id_ps, 0, url_actual, "LD_JSON_SCHEMA", True
                    )
                if "instock" in texto_ld:
                    return LecturaExtraida(
                        id_ps, 8, url_actual, "LD_JSON_SCHEMA", True
                    )
            except Exception:
                pass

        return None

    def _extraer_fallback_ssr(
        self, candidato: ProductoCandidato
    ) -> Optional[LecturaExtraida]:
        if not CURL_CFFI_DISPONIBLE:
            return None
        url = (
            candidato.url_real
            if (candidato.url_real and candidato.url_real.startswith("http"))
            else f"https://www.priceshoes.com/buscar?q={candidato.id_ps}"
        )
        try:
            resp = tls_requests.get(url, impersonate="chrome120", timeout=25)
            if resp.status_code == 200:
                return self._parsear_estructuras_hidratadas(
                    resp.text, candidato.id_ps, url
                )
        except Exception:
            pass
        return None


# ==============================================================================
# 6. CAPA 3: MOTOR MATEMATICO BAYESIANO + UCB + ANTI-QUIEBRE 24H
# ==============================================================================
class MotorMatematicoBayesianoUCB:
    @staticmethod
    def evaluar_curva_sku(
        id_ps: str,
        stock_actual: int,
        fuente_extraccion: str,
        total_global_lecturas: int,
        historial_sku: List[sqlite3.Row],
    ) -> DictamenTransformacion:
        lecturas_previas = len(historial_sku)
        delta_stock = 0
        lambda_diaria = 0.0
        dias_estancado = 0.0

        if lecturas_previas > 0:
            ultima = historial_sku[0]
            stock_previo = int(ultima["stock_detectado"])
            delta_stock = stock_actual - stock_previo

            ventana_dias = max(float(ultima["dias_transcurridos"] or 0.5), 0.5)
            if delta_stock < 0:
                lambda_diaria = round(abs(delta_stock) / ventana_dias, 3)

            for fila in historial_sku:
                if int(fila["stock_detectado"]) == stock_actual:
                    dias_estancado = round(
                        float(fila["dias_transcurridos"] or 0.0), 2
                    )
                else:
                    break

        n_i = lecturas_previas + 1
        suma_lambdas = lambda_diaria + sum(
            float(r["tasa_decaimiento_lambda"] or 0.0) for r in historial_sku
        )
        media_bayesiana = round(
            (PRIOR_PESO_ALPHA * PRIOR_ROTACION_MU0 + suma_lambdas)
            / (PRIOR_PESO_ALPHA + n_i),
            3,
        )

        bono_ucb = round(
            CONSTANTE_UCB_C
            * math.sqrt(math.log(max(total_global_lecturas, 1) + 1) / n_i),
            3,
        )
        score_ucb = round(media_bayesiana + bono_ucb, 3)

        if stock_actual <= UMBRAL_STOCK_CRITICO:
            estatus = "BAJA_AUTOMATICA"
            accion_ml = "PAUSAR_PUBLICACION"
            motivo = f"Limite critico alcanzado ({stock_actual} <= {UMBRAL_STOCK_CRITICO} unidades)"
        elif n_i <= VENTANA_EXPLORACION_MIN_LECTURAS:
            estatus = "EXPLORACION_UCB"
            accion_ml = "PUBLICAR_O_ACTUALIZAR"
            motivo = f"En margen de exploracion UCB ({n_i}/{VENTANA_EXPLORACION_MIN_LECTURAS} lecturas)"
        elif (
            dias_estancado >= DIAS_MAX_ESTANCAMIENTO
            and media_bayesiana < SCORE_MINIMO_ACTIVO
        ):
            estatus = "BAJA_AUTOMATICA"
            accion_ml = "PAUSAR_PUBLICACION"
            motivo = f"Inventario estancado por {dias_estancado} dias (media={media_bayesiana})"
        elif lambda_diaria >= TASA_CAIDA_ALERTA_DIARIA and stock_actual <= (
            UMBRAL_STOCK_CRITICO * 2
        ):
            estatus = "RIESGO_DECAY"
            accion_ml = "REDUCIR_STOCK_PREVENTIVO"
            motivo = f"Curva de caida pronunciada (lambda={lambda_diaria}/dia) cerca del limite"
        else:
            estatus = "ACTIVO"
            accion_ml = "PUBLICAR_O_ACTUALIZAR"
            motivo = "Curva de inventario saludable"

        return DictamenTransformacion(
            id_ps=id_ps,
            stock_actual=stock_actual,
            delta_stock=delta_stock,
            tasa_decaimiento_lambda=lambda_diaria,
            dias_estancado=dias_estancado,
            media_bayesiana=media_bayesiana,
            bono_ucb=bono_ucb,
            score_ucb=score_ucb,
            num_lecturas=n_i,
            estatus_etl=estatus,
            accion_ml=accion_ml,
            motivo_tecnico=motivo,
            fuente_extraccion=fuente_extraccion,
        )


def calcular_metricas_antiquiebre_24h(
    stock_actual: int,
    tasa_decaimiento_lambda: float,
    delta_stock: int,
    horas_entre_lecturas: float = 24.0,
    limite_critico_baja: int = Config.LIMITE_CRITICO_BAJA,
) -> Dict[str, Any]:
    stk = max(0, int(stock_actual))
    dlt = int(delta_stock)
    dias_ventana = max(0.083, float(horas_entre_lecturas) / 24.0)
    caida_reciente_diaria = abs(min(0, dlt)) / dias_ventana if dlt < 0 else 0.0

    lambda_real = max(0.0, float(tasa_decaimiento_lambda))
    lambda_segura = max(lambda_real, caida_reciente_diaria, 0.25)

    horas_autonomia = round((float(stk) / lambda_segura) * 24.0, 1) if stk > 0 else 0.0

    demanda_36h = 1.5 * lambda_segura
    reserva_poisson = math.ceil(demanda_36h + 1.65 * math.sqrt(demanda_36h))
    reserva_dinamica_24h = max(int(limite_critico_baja), int(reserva_poisson))

    stock_publicable_meli = max(0, stk - reserva_dinamica_24h)

    if (
        stk <= limite_critico_baja
        or stock_publicable_meli <= 0
        or horas_autonomia <= Config.HORAS_MIN_AUTONOMIA_MELI
    ):
        accion_meli = "PAUSAR_MELI"
    else:
        accion_meli = "PUBLICAR_ACTIVO"

    return {
        "lambda_segura": round(lambda_segura, 2),
        "horas_autonomia": horas_autonomia,
        "reserva_dinamica_24h": reserva_dinamica_24h,
        "stock_publicable_meli": stock_publicable_meli,
        "accion_meli": accion_meli,
    }


def evaluar_dataframe_quant(
    df: pd.DataFrame,
    lim_baja: int = Config.LIMITE_CRITICO_BAJA,
    gracia_ucb: int = Config.LECTURAS_GRACIA_UCB,
    lim_dias: float = Config.DIAS_MAX_ESTANCAMIENTO,
    lim_lambda: float = Config.PENDIENTE_DECAY_PELIGROSA,
    c_ucb: float = Config.FACTOR_UCB_C,
    lim_bayes: float = Config.MEDIA_BAYESIANA_MIN,
) -> pd.DataFrame:
    n = len(df)
    if n == 0:
        return df

    aud_list = col_int(df, "auditado_etl", 0).tolist()
    stk_list = col_int(df, "stock_actual", 0).tolist()
    dlt_list = col_int(df, "delta_stock", 0).tolist()
    dec_list = col_num(df, "tasa_decaimiento", 0.0).tolist()
    mb_list = col_num(df, "media_bayesiana", 0.5).tolist()
    lec_list = col_int(df, "num_lecturas", 0).tolist()
    dias_list = col_num(df, "dias_sin_movimiento", 0.0).tolist()

    total_auditados = max(
        2, sum(1 for a, l in zip(aud_list, lec_list) if a >= 1 or l >= 1)
    )
    ln_n = math.log(float(total_auditados))

    out_ucb: List[float] = []
    out_est: List[str] = []
    out_mot: List[str] = []
    out_autonomia: List[float] = []
    out_reserva: List[int] = []
    out_pub_meli: List[int] = []
    out_accion: List[str] = []
    out_prio: List[float] = []

    for i in range(n):
        es_aud = int(aud_list[i]) >= 1 or int(lec_list[i]) >= 1
        stk = int(stk_list[i])
        dlt = int(dlt_list[i])
        dec = float(dec_list[i])
        mb = float(mb_list[i])
        if mb <= 0.0 or math.isnan(mb):
            mb = 0.5
        lec = max(1, int(lec_list[i])) if es_aud else 0
        dias = float(dias_list[i])

        if not es_aud:
            out_ucb.append(0.0)
            out_est.append("PENDIENTE_ESCANEO")
            out_mot.append("Pendiente de auditar en Carril B Semanal")
            out_autonomia.append(0.0)
            out_reserva.append(lim_baja)
            out_pub_meli.append(0)
            out_accion.append("PENDIENTE_ESCANEO")
            out_prio.append(0.0)
            continue

        ucb = round(mb + c_ucb * math.sqrt(ln_n / float(lec)), 3)
        aq = calcular_metricas_antiquiebre_24h(
            stock_actual=stk,
            tasa_decaimiento_lambda=dec,
            delta_stock=dlt,
            limite_critico_baja=lim_baja,
        )

        if stk <= lim_baja:
            est = "BAJA_AUTOMATICA"
            mot = f"Stock ({stk}) <= Limite Critico ({lim_baja})"
        elif lec > gracia_ucb and mb < lim_bayes:
            est = "BAJA_AUTOMATICA"
            mot = f"Media Bayesiana ({mb:.2f}) < Minimo ({lim_bayes:.2f})"
        elif lec > gracia_ucb and dias >= lim_dias and dlt == 0 and dias > 0:
            est = "BAJA_AUTOMATICA"
            mot = f"Estancado ({dias:.1f} dias >= {lim_dias:.1f})"
        elif dec >= lim_lambda:
            est = "ALERTA_DECAY"
            mot = f"Caida Rapida lambda ({dec:.2f} >= {lim_lambda:.2f})"
        elif lec <= gracia_ucb:
            est = "EXPLORACION_UCB"
            mot = f"En gracia UCB ({lec}/{gracia_ucb} lecturas) · Stock ({stk})"
        else:
            est = "ACTIVO_SANO"
            mot = f"Activo sano · Autonomia {aq['horas_autonomia']:.0f}h"

        if aq["accion_meli"] == "PAUSAR_MELI" or est == "BAJA_AUTOMATICA":
            prio = 0.0
        else:
            pts_stock = min(40.0, float(aq["stock_publicable_meli"]) * 1.6)
            pts_velocidad = min(35.0, (dec * 6.0) + (abs(min(0, dlt)) * 3.5))
            pts_ucb = min(25.0, ucb * 5.0)
            prio = round(pts_stock + pts_velocidad + pts_ucb, 2)

        out_ucb.append(ucb)
        out_est.append(est)
        out_mot.append(mot)
        out_autonomia.append(aq["horas_autonomia"])
        out_reserva.append(aq["reserva_dinamica_24h"])
        out_pub_meli.append(aq["stock_publicable_meli"])
        out_accion.append(aq["accion_meli"])
        out_prio.append(prio)

    df["score_ucb_simulado"] = out_ucb
    df["estatus_simulado"] = out_est
    df["motivo_simulado"] = out_mot
    df["horas_autonomia"] = out_autonomia
    df["reserva_dinamica_24h"] = out_reserva
    df["stock_publicable_meli"] = out_pub_meli
    df["accion_meli"] = out_accion
    df["score_prioridad_meli"] = out_prio
    return df


# ==============================================================================
# 7. CLASES DEL ESTUDIO FOTOGRAFICO HD (1200x1200px) PARA STREAMLIT
# ==============================================================================
@dataclass
class VarianteSucursal:
    variante: str
    stock_vallejo: int = 0
    stock_olivar: int = 0
    stock_arco_norte: int = 0

    @property
    def stock_bruto_total(self) -> int:
        return max(
            0,
            int(self.stock_vallejo)
            + int(self.stock_olivar)
            + int(self.stock_arco_norte),
        )

    @property
    def stock_neto(self) -> int:
        b = self.stock_bruto_total
        return 0 if b <= 1 else max(0, b - Config.BUFFER_STOCK)


@dataclass
class ResultadoExtraccion:
    sku: str
    titulo: str
    marca: str
    categoria: str
    color: str
    material: str
    costo_base: float
    catalogo_origen: str = "Catálogo Urbano y Casual"
    variantes_multisucursal: List[VarianteSucursal] = field(default_factory=list)
    variantes_brutas: Dict[str, int] = field(default_factory=dict)
    variantes_netas: Dict[str, int] = field(default_factory=dict)
    stock_bruto_total: int = 0
    stock_neto_total: int = 0
    imagenes_bytes: List[Tuple[str, bytes]] = field(default_factory=list)
    urls_imagenes_origen: List[str] = field(default_factory=list)
    fuente_extraccion: str = "ETL_REACT"
    url_final_secreta: str = ""


class DBHandler:
    def __init__(self, ruta_bd: Optional[Path] = None) -> None:
        self.ruta_bd = ruta_bd or Config.RUTA_BD
        self.conn: Optional[sqlite3.Connection] = None

    def __enter__(self) -> "DBHandler":
        self.ruta_bd.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.ruta_bd, timeout=20.0)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL;")
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        if self.conn is not None:
            if exc_type is None:
                self.conn.commit()
            else:
                self.conn.rollback()
            self.conn.close()
            self.conn = None


class ExtractorAsyncSinergia:
    @staticmethod
    def _generar_siluetas_estudio_hd(
        sku: str, categoria: str = "Calzado"
    ) -> List[Tuple[str, bytes]]:
        salidas: List[Tuple[str, bytes]] = []
        for idx_v in (1, 2):
            im = Image.new("RGB", (1200, 1200), (255, 255, 255))
            dr = ImageDraw.Draw(im)
            dr.ellipse([(260, 860), (940, 960)], fill=(230, 234, 240))
            pts = (
                [(260, 740), (340, 480), (580, 520), (880, 680), (920, 780), (260, 800)]
                if idx_v == 1
                else [
                    (240, 720),
                    (420, 460),
                    (660, 510),
                    (900, 660),
                    (930, 770),
                    (240, 790),
                ]
            )
            dr.polygon(pts, fill=(30, 41, 59), outline=(15, 23, 42))
            dr.rectangle(
                [(250, 770), (930, 825)],
                fill=(241, 245, 249),
                outline=(148, 163, 184),
                width=2,
            )
            buf = io.BytesIO()
            im.save(buf, format="PNG")
            raw = buf.getvalue()
            salidas.append((hashlib.sha256(raw).hexdigest(), raw))
        return salidas


class EstudioFotograficoSinergia:
    def __init__(self, log_pub: logging.Logger, log_int: logging.Logger) -> None:
        self.log_pub = log_pub
        self.log_int = log_int

    @staticmethod
    def _encuadrar_95_blanca(raw_bytes: bytes, angulo: float = 0.0) -> bytes:
        with Image.open(io.BytesIO(raw_bytes)) as im_in:
            im = im_in.convert("RGBA")
        fondo_blanco = Image.new("RGBA", im.size, (255, 255, 255, 255))
        comp = Image.alpha_composite(fondo_blanco, im).convert("RGB")
        inv = ImageOps.invert(comp).convert("L")
        bbox = inv.point(lambda p: 255 if p > 12 else 0).getbbox()
        recorte = comp.crop(bbox) if bbox else comp
        if abs(angulo) > 0.1:
            recorte = recorte.rotate(
                angulo,
                expand=True,
                resample=Image.Resampling.BICUBIC,
                fillcolor=(255, 255, 255),
            )
        esc = min(1100.0 / max(1, recorte.width), 1100.0 / max(1, recorte.height))
        nw, nh = max(1, int(recorte.width * esc)), max(1, int(recorte.height * esc))
        redim = recorte.resize((nw, nh), Image.Resampling.LANCZOS)
        lienzo = Image.new("RGB", (1200, 1200), (255, 255, 255))
        lienzo.paste(redim, ((1200 - nw) // 2, (1200 - nh) // 2))
        buf = io.BytesIO()
        lienzo.save(buf, format="PNG", optimize=True)
        return buf.getvalue()

    @staticmethod
    def _renderizar_lamina_98(res: ResultadoExtraccion, raw_base: bytes) -> bytes:
        lienzo = Image.new("RGB", (1200, 1200), (15, 23, 42))
        dr = ImageDraw.Draw(lienzo)
        dr.rounded_rectangle(
            [(40, 40), (1160, 1160)],
            radius=36,
            fill=(30, 41, 59),
            outline=(16, 185, 129),
            width=4,
        )
        f_b = ImageFont.load_default()
        dr.text(
            (90, 95),
            f"{res.marca.upper()} · CALIDAD CERTIFICADA",
            fill=(16, 185, 129),
            font=f_b,
        )
        dr.text((90, 145), res.titulo[:48].upper(), fill=(248, 250, 252), font=f_b)
        dr.text(
            (90, 200),
            f"Catalogo: {res.catalogo_origen}  |  ID: {res.sku}",
            fill=(148, 163, 184),
            font=f_b,
        )

        with Image.open(io.BytesIO(raw_base)) as im_p:
            im_c = im_p.convert("RGB")
            im_c.thumbnail((680, 680), Image.Resampling.LANCZOS)
            px = (1200 - im_c.width) // 2
            lienzo.paste(im_c, (px, 270))

        beneficios = [
            f"Material: {res.material}",
            f"Acabado / Color: {res.color}",
            "Stock Verificado con Reserva Dinamica Anti-Quiebre 24h",
        ]
        y_b = 1000
        for b_txt in beneficios:
            dr.text((110, y_b), f"OK  {b_txt}", fill=(241, 245, 249), font=f_b)
            y_b += 40

        buf = io.BytesIO()
        lienzo.save(buf, format="PNG", optimize=True)
        return buf.getvalue()

    @staticmethod
    def _renderizar_lamina_99(res: ResultadoExtraccion) -> bytes:
        lienzo = Image.new("RGB", (1200, 1200), (248, 250, 252))
        dr = ImageDraw.Draw(lienzo)
        dr.rectangle([(0, 0), (1200, 180)], fill=(15, 23, 42))
        f_b = ImageFont.load_default()
        dr.text(
            (70, 60),
            f"FICHA TECNICA Y GUIA DE TALLAS · ID {res.sku}",
            fill=(16, 185, 129),
            font=f_b,
        )
        dr.text(
            (70, 110),
            f"{res.marca.upper()} - {res.titulo[:52]}",
            fill=(255, 255, 255),
            font=f_b,
        )

        y_cursor = 240
        dr.text(
            (80, y_cursor),
            "TALLA MX       VALLEJO       OLIVAR       ARCO NORTE       DISPONIBLE",
            fill=(15, 23, 42),
            font=f_b,
        )
        y_cursor += 45
        dr.line([(80, y_cursor), (1120, y_cursor)], fill=(148, 163, 184), width=2)
        y_cursor += 25

        for vs in res.variantes_multisucursal[:12]:
            linea = f"  {vs.variante:<12} {vs.stock_vallejo:<13} {vs.stock_olivar:<12} {vs.stock_arco_norte:<16} {vs.stock_neto} pares"
            dr.text((80, y_cursor), linea, fill=(30, 41, 59), font=f_b)
            y_cursor += 55

        buf = io.BytesIO()
        lienzo.save(buf, format="PNG", optimize=True)
        return buf.getvalue()

    def procesar_estudio_sku(
        self,
        db: DBHandler,
        corrida_id: str,
        res: ResultadoExtraccion,
        dry_run: bool = False,
    ) -> bool:
        try:
            imgs = (
                res.imagenes_bytes
                or ExtractorAsyncSinergia._generar_siluetas_estudio_hd(
                    res.sku, res.categoria
                )
            )
            raw_1 = imgs[0][1]
            raw_2 = imgs[1][1] if len(imgs) > 1 else raw_1

            b_01 = self._encuadrar_95_blanca(raw_1, angulo=0.0)
            b_02 = self._encuadrar_95_blanca(
                raw_2, angulo=-4.0 if raw_2 == raw_1 else 0.0
            )
            b_98 = self._renderizar_lamina_98(res, b_01)
            b_99 = self._renderizar_lamina_99(res)

            if not dry_run:
                rutas = Config.rutas_esperadas_sku(res.sku)
                for r in rutas.values():
                    r.parent.mkdir(parents=True, exist_ok=True)
                rutas["01_portada_blanca"].write_bytes(b_01)
                rutas["02_vista_angulo"].write_bytes(b_02)
                rutas["98_lamina_impacto"].write_bytes(b_98)
                rutas["99_lamina_ficha"].write_bytes(b_99)
            return True
        except Exception as exc:
            self.log_int.error("Error en estudio SKU %s: %s", res.sku, exc)
            return False


# ==============================================================================
# 8. LECTOR UNIFICADO DE `data/zapateria_vallejo.db` Y EXPORTADOR DE COLA MELI
# ==============================================================================
def cargar_estado_completo_bd(
    ruta_bd: Path = Config.RUTA_BD,
) -> Dict[str, pd.DataFrame]:
    vacio = {
        "df_maestro": pd.DataFrame(columns=["id_ps", "nombre_modelo"]),
        "df_bitacora": pd.DataFrame(),
        "df_variantes": pd.DataFrame(),
    }
    if not ruta_bd.exists() or ruta_bd.stat().st_size == 0:
        return vacio

    with closing(sqlite3.connect(ruta_bd, timeout=15.0)) as conn:
        tbs = {
            str(r[0])
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table';"
            ).fetchall()
        }

        if "zapatos_candidatos" in tbs:
            df_cat = pd.read_sql_query("SELECT * FROM zapatos_candidatos;", conn)
        else:
            return vacio

        df_cat["id_ps"] = (
            col_txt(df_cat, "id_ps", "")
            .str.strip()
            .str.replace(r"\.0$", "", regex=True)
        )
        df_cat = (
            df_cat[df_cat["id_ps"] != ""]
            .drop_duplicates(subset=["id_ps"], keep="first")
            .reset_index(drop=True)
        )

        df_variantes = pd.DataFrame()
        mapa_stk_21: Dict[str, int] = {}
        mapa_fec_21: Dict[str, str] = {}
        mapa_lec_21: Dict[str, int] = {}
        mapa_tallas_det: Dict[str, str] = {}

        if "historial_stock_21_dias" in tbs:
            df_h21 = pd.read_sql_query("SELECT * FROM historial_stock_21_dias;", conn)
            if not df_h21.empty:
                df_h21["sku"] = (
                    col_txt(df_h21, "id_ps", "")
                    .str.strip()
                    .str.replace(r"\.0$", "", regex=True)
                )
                df_h21["variante"] = np.where(
                    col_txt(df_h21, "talla_norm", "") != "",
                    col_txt(df_h21, "talla_norm", ""),
                    col_txt(df_h21, "talla", "Unica"),
                )
                df_h21["stock_vallejo"] = col_int(df_h21, "stock_vallejo", 0)
                df_h21["stock_olivar"] = 0
                df_h21["stock_arco_norte"] = 0
                df_h21["stock_bruto"] = df_h21["stock_vallejo"]
                df_h21["stock_neto"] = col_int(
                    df_h21, "stock_seguro_publicable", 0
                )
                df_h21["fecha_str"] = col_txt(df_h21, "fecha", "")

                mapa_lec_21 = (
                    df_h21.groupby("sku", sort=False)["fecha_str"]
                    .nunique()
                    .to_dict()
                )

                df_h21_ult = (
                    df_h21.sort_values(by=["sku", "fecha_str"], ascending=[True, True])
                    .drop_duplicates(subset=["sku", "variante"], keep="last")
                    .reset_index(drop=True)
                )
                df_variantes = df_h21_ult[
                    [
                        "sku",
                        "variante",
                        "stock_vallejo",
                        "stock_olivar",
                        "stock_arco_norte",
                        "stock_bruto",
                        "stock_neto",
                        "fecha_str",
                    ]
                ].copy()

                ag_21 = df_h21_ult.groupby("sku", sort=False).agg(
                    stk_tot=("stock_vallejo", "sum"),
                    ult_fec=("fecha_str", "max"),
                )
                mapa_stk_21 = ag_21["stk_tot"].to_dict()
                mapa_fec_21 = ag_21["ult_fec"].to_dict()

                df_con_stk = df_h21_ult[df_h21_ult["stock_vallejo"] > 0].copy()
                if not df_con_stk.empty:
                    df_con_stk["_t_txt"] = (
                        "T"
                        + df_con_stk["variante"].astype(str)
                        + ":"
                        + df_con_stk["stock_vallejo"].astype(str)
                    )
                    mapa_tallas_det = (
                        df_con_stk.groupby("sku", sort=False)["_t_txt"]
                        .apply(" | ".join)
                        .to_dict()
                    )

        df_bit_raw = pd.DataFrame()
        mapa_stk_hi: Dict[str, int] = {}
        mapa_dlt_hi: Dict[str, int] = {}
        mapa_lam_hi: Dict[str, float] = {}
        mapa_dia_hi: Dict[str, float] = {}
        mapa_mb_hi: Dict[str, float] = {}
        mapa_lec_hi: Dict[str, int] = {}
        mapa_fec_hi: Dict[str, str] = {}

        if "historial_inventario" in tbs:
            df_hi = pd.read_sql_query("SELECT * FROM historial_inventario;", conn)
            if not df_hi.empty:
                df_hi["id_ps"] = (
                    col_txt(df_hi, "id_ps", "")
                    .str.strip()
                    .str.replace(r"\.0$", "", regex=True)
                )
                df_hi = df_hi[df_hi["id_ps"] != ""].copy()
                df_hi["_stk"] = np.maximum(
                    col_int(df_hi, "stock_detectado", 0),
                    col_int(df_hi, "stock_bruto_detectado", 0),
                )
                df_hi["_ts"] = pd.to_datetime(
                    col_txt(df_hi, "fecha_hora_consulta", ""), errors="coerce"
                )
                df_hi = df_hi.sort_values(
                    by=["id_ps", "_ts"], ascending=[True, True]
                ).reset_index(drop=True)
                df_bit_raw = df_hi

                for id_k, grp in df_hi.groupby("id_ps", sort=False):
                    id_str = str(id_k)
                    ult = grp.iloc[-1]
                    mapa_stk_hi[id_str] = int(ult["_stk"])
                    mapa_dlt_hi[id_str] = int(num_seguro(ult.get("delta_stock"), 0))
                    mapa_lam_hi[id_str] = num_seguro(
                        ult.get("tasa_decaimiento_lambda"), 0.0
                    )
                    mapa_dia_hi[id_str] = num_seguro(ult.get("dias_estancado"), 0.0)
                    mapa_mb_hi[id_str] = num_seguro(ult.get("media_bayesiana"), 0.5)
                    mapa_lec_hi[id_str] = len(grp)
                    f_raw = ult.get("fecha_hora_consulta")
                    mapa_fec_hi[id_str] = "" if pd.isna(f_raw) else str(f_raw)

    stk_cat_pub = col_int(df_cat, "stock_publicable_total", 0)
    stk_cat_bru = col_int(df_cat, "stock_bruto_anterior", 0)
    stk_cat_ant = col_int(df_cat, "stock_total_anterior", 0)
    stk_cat_max = np.maximum(stk_cat_pub, np.maximum(stk_cat_bru, stk_cat_ant))

    lec_cat = col_int(df_cat, "num_consultas_hist", 0)
    estatus_etl_cat = col_txt(df_cat, "estatus_etl", "").str.strip()

    stock_final_list: List[int] = []
    delta_final_list: List[int] = []
    lambda_final_list: List[float] = []
    mb_final_list: List[float] = []
    lec_final_list: List[int] = []
    dias_final_list: List[float] = []
    fec_final_list: List[str] = []
    aud_final_list: List[int] = []

    lam_cat = np.maximum(
        col_num(df_cat, "tasa_decaimiento", 0.0),
        col_num(df_cat, "velocidad_diaria", 0.0),
    ).tolist()
    mb_cat = col_num(df_cat, "media_bayesiana", 0.5).tolist()
    fec_cat = np.where(
        col_txt(df_cat, "ultima_actualizacion", "") != "",
        col_txt(df_cat, "ultima_actualizacion", ""),
        col_txt(df_cat, "ultima_sincronizacion", ""),
    ).tolist()

    for idx, sku_id in enumerate(df_cat["id_ps"].tolist()):
        en_21 = sku_id in mapa_stk_21
        en_hi = sku_id in mapa_stk_hi
        n_l_cat = int(lec_cat.iloc[idx])
        s_c_max = int(stk_cat_max.iloc[idx])
        est_c = str(estatus_etl_cat.iloc[idx])

        es_aud = (
            en_21
            or en_hi
            or n_l_cat >= 1
            or s_c_max > 0
            or (est_c != "" and est_c.upper() not in ("PENDIENTE", "NONE", "NAN"))
        )

        if en_hi:
            stk_v = mapa_stk_hi[sku_id]
        elif en_21:
            stk_v = mapa_stk_21[sku_id]
        else:
            stk_v = s_c_max

        dlt_v = mapa_dlt_hi.get(sku_id, 0)
        lam_v = mapa_lam_hi.get(sku_id, float(lam_cat[idx]))
        mb_v = mapa_mb_hi.get(sku_id, float(mb_cat[idx]))
        if mb_v <= 0.0 or math.isnan(mb_v):
            mb_v = 0.5
        lec_v = max(
            n_l_cat,
            mapa_lec_hi.get(sku_id, 0) + mapa_lec_21.get(sku_id, 0),
            1 if es_aud else 0,
        )
        dias_v = mapa_dia_hi.get(sku_id, 0.0)
        fec_v = mapa_fec_hi.get(sku_id) or mapa_fec_21.get(sku_id) or str(fec_cat[idx])

        stock_final_list.append(int(stk_v))
        delta_final_list.append(int(dlt_v))
        lambda_final_list.append(round(float(lam_v), 2))
        mb_final_list.append(round(float(mb_v), 3))
        lec_final_list.append(int(lec_v))
        dias_final_list.append(round(float(dias_v), 2))
        fec_final_list.append(str(fec_v))
        aud_final_list.append(1 if es_aud else 0)

    marca_col = np.where(
        col_txt(df_cat, "marca", "") != "",
        col_txt(df_cat, "marca", ""),
        np.where(
            col_txt(df_cat, "marca_detectada", "") != "",
            col_txt(df_cat, "marca_detectada", ""),
            col_txt(df_cat, "marca_extraida", ""),
        ),
    )
    costo_col = np.where(
        col_num(df_cat, "costo_socio", 0.0) > 0,
        col_num(df_cat, "costo_socio", 0.0),
        col_num(df_cat, "costo_bodega", 0.0),
    )

    df_maestro = pd.DataFrame(
        {
            "id_ps": df_cat["id_ps"],
            "nombre_modelo": col_txt(df_cat, "nombre_modelo", "Modelo Catalogo"),
            "marca": pd.Series(marca_col, index=df_cat.index),
            "categoria": col_txt(df_cat, "tipo_calzado", "Calzado"),
            "color": np.where(
                col_txt(df_cat, "color", "") != "",
                col_txt(df_cat, "color", ""),
                col_txt(df_cat, "color_detectado", "Tono Original"),
            ),
            "material": col_txt(
                df_cat, "material_corte", "Corte Sintetico de Alta Resistencia"
            ),
            "catalogo_origen": col_txt(df_cat, "origen_catalogo", ""),
            "costo_base": np.round(costo_col, 2),
            "precio_ml_db": col_num(df_cat, "precio_ml", 0.0),
            "utilidad_ml_db": col_num(df_cat, "utilidad_neta_ml", 0.0),
            "precio_amazon_db": col_num(df_cat, "precio_amazon", 0.0),
            "precio_tiktok_db": col_num(df_cat, "precio_tiktok", 0.0),
            "url_origen": col_txt(df_cat, "url_real", ""),
            "imagen_url_origen": np.where(
                col_txt(df_cat, "url_portada_raw", "") != "",
                col_txt(df_cat, "url_portada_raw", ""),
                col_txt(df_cat, "imagen_url_origen", ""),
            ),
            "tallas_detalle": [
                mapa_tallas_det.get(s, t_seg)
                for s, t_seg in zip(
                    df_cat["id_ps"].tolist(),
                    col_txt(df_cat, "tallas_seguras_detalle", "").tolist(),
                )
            ],
            "stock_vallejo": stock_final_list,
            "stock_olivar": 0,
            "stock_arco_norte": 0,
            "stock_actual": stock_final_list,
            "delta_stock": delta_final_list,
            "tasa_decaimiento": lambda_final_list,
            "media_bayesiana": mb_final_list,
            "num_lecturas": lec_final_list,
            "dias_sin_movimiento": dias_final_list,
            "ultima_lectura": fec_final_list,
            "auditado_etl": aud_final_list,
        }
    )

    return {
        "df_maestro": df_maestro.reset_index(drop=True),
        "df_bitacora": df_bit_raw.reset_index(drop=True),
        "df_variantes": df_variantes.reset_index(drop=True),
    }


def seleccionar_lote_doble_carril(
    df_maestro: pd.DataFrame,
    cuota_carril_a: int = Config.CUOTA_CARRIL_A_MELI_24H,
    cuota_carril_b: int = Config.CUOTA_CARRIL_B_SEMANAL,
) -> Tuple[List[str], List[str]]:
    if df_maestro.empty:
        return [], []

    df = df_maestro.copy()
    df["id_ps"] = col_txt(df, "id_ps", "").str.strip()
    df = df[df["id_ps"] != ""].copy()

    skus_cola_meli: Set[str] = set()
    if Config.RUTA_COLA_MELI.exists():
        try:
            raw_json = json.loads(Config.RUTA_COLA_MELI.read_text(encoding="utf-8"))
            lista_top = (
                raw_json.get("top_100_meli", [])
                if isinstance(raw_json, dict)
                else (raw_json if isinstance(raw_json, list) else [])
            )
            for item in lista_top:
                if isinstance(item, dict):
                    s_id = sanitizar_sku(item.get("id_ps") or item.get("sku"))
                    if s_id:
                        skus_cola_meli.add(s_id)
        except Exception:
            pass

    ahora_utc = pd.Timestamp.now(tz="UTC").tz_localize(None)
    ts_parsed = pd.to_datetime(df["ultima_lectura"], errors="coerce", utc=True)
    ts_parsed = ts_parsed.dt.tz_localize(None)
    horas = (ahora_utc - ts_parsed).dt.total_seconds() / 3600.0
    df["_horas_sin_leer"] = horas.clip(lower=0.0).fillna(99999.0)

    df["_en_top_meli"] = df["id_ps"].map(sanitizar_sku).isin(skus_cola_meli).astype(int)

    df_cand_a = df[
        (df["_en_top_meli"] == 1)
        | (
            (df["auditado_etl"] >= 1)
            & (df["stock_actual"] > Config.LIMITE_CRITICO_BAJA)
        )
    ].copy()
    df_cand_a["_vencido_6h"] = (df_cand_a["_horas_sin_leer"] >= 6.0).astype(int)
    df_cand_a = df_cand_a.sort_values(
        by=["_vencido_6h", "_en_top_meli", "tasa_decaimiento", "_horas_sin_leer"],
        ascending=[False, False, False, False],
    )
    carril_a = df_cand_a["id_ps"].head(int(cuota_carril_a)).tolist()

    set_a = set(carril_a)
    df_cand_b = df[~df["id_ps"].isin(set_a)].copy()
    df_cand_b["_nunca_leido"] = (df_cand_b["auditado_etl"] == 0).astype(int)
    df_cand_b["_permitido_meli"] = [
        0 if es_marca_restringida_meli(str(m), str(t)) else 1
        for m, t in zip(
            col_txt(df_cand_b, "marca", ""), col_txt(df_cand_b, "nombre_modelo", "")
        )
    ]

    df_cand_b = df_cand_b.sort_values(
        by=["_nunca_leido", "_horas_sin_leer", "_permitido_meli"],
        ascending=[False, False, False],
    )
    carril_b = df_cand_b["id_ps"].head(int(cuota_carril_b)).tolist()

    return carril_a, carril_b


def generar_cola_sincronizacion_ml(
    df_evaluado: pd.DataFrame,
    carril_a_ids: Sequence[str] = (),
    carril_b_ids: Sequence[str] = (),
    ruta_salida: Path = Config.RUTA_COLA_MELI,
) -> Dict[str, Any]:
    ruta_salida.parent.mkdir(parents=True, exist_ok=True)
    df = df_evaluado.copy()

    df["marca"] = [
        extraer_marca_real(str(r.get("nombre_modelo", "")), str(r.get("marca", "")))
        for _, r in df.iterrows()
    ]
    df["catalogo_origen"] = [
        clasificar_catalogo_origen(
            str(r.get("nombre_modelo", "")),
            str(r.get("marca", "")),
            str(r.get("catalogo_origen", "")),
        )
        for _, r in df.iterrows()
    ]
    df["es_restringida_meli"] = [
        es_marca_restringida_meli(
            str(r.get("marca", "")), str(r.get("nombre_modelo", ""))
        )
        for _, r in df.iterrows()
    ]

    df_auditados = df[df["auditado_etl"] >= 1].copy()

    df_top_meli = (
        df_auditados[
            (~df_auditados["es_restringida_meli"])
            & (df_auditados["accion_meli"] == "PUBLICAR_ACTIVO")
            & (df_auditados["horas_autonomia"] > Config.HORAS_MIN_AUTONOMIA_MELI)
            & (df_auditados["stock_publicable_meli"] > 0)
        ]
        .sort_values(
            by=[
                "score_prioridad_meli",
                "tasa_decaimiento",
                "score_ucb_simulado",
                "stock_publicable_meli",
            ],
            ascending=[False, False, False, False],
        )
        .head(100)
    )

    df_pausa_meli = (
        df_auditados[
            (~df_auditados["es_restringida_meli"])
            & (df_auditados["accion_meli"] == "PAUSAR_MELI")
        ]
        .sort_values(by=["tasa_decaimiento", "stock_actual"], ascending=[False, True])
        .head(150)
    )

    ids_en_top100 = set(df_top_meli["id_ps"].tolist())
    df_otras_plat = (
        df_auditados[
            (~df_auditados["id_ps"].isin(ids_en_top100))
            & (df_auditados["stock_actual"] > Config.LIMITE_CRITICO_BAJA)
        ]
        .sort_values(
            by=["tasa_decaimiento", "score_ucb_simulado", "stock_actual"],
            ascending=[False, False, False],
        )
        .head(300)
    )

    def _serializar_filas(sub_df: pd.DataFrame) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for _, r in sub_df.iterrows():
            costo_v = float(r.get("costo_base") or 0.0)
            p_ml_db = float(r.get("precio_ml_db") or 0.0)
            u_ml_db = float(r.get("utilidad_ml_db") or 0.0)
            econ = (
                calcular_unit_economics_item(costo_v)
                if costo_v > 0
                else {"precio_sugerido": p_ml_db, "utilidad_neta": u_ml_db}
            )
            out.append(
                {
                    "id_ps": str(r["id_ps"]),
                    "nombre_modelo": str(r.get("nombre_modelo", "")),
                    "marca": str(r.get("marca", "")),
                    "catalogo_origen": str(r.get("catalogo_origen", "")),
                    "restringida_meli": bool(r.get("es_restringida_meli", False)),
                    "stock_actual": int(r.get("stock_actual", 0)),
                    "reserva_dinamica_24h": int(r.get("reserva_dinamica_24h", 3)),
                    "stock_publicable_meli": int(r.get("stock_publicable_meli", 0)),
                    "horas_autonomia": float(r.get("horas_autonomia", 0.0)),
                    "tasa_decaimiento_lambda": float(r.get("tasa_decaimiento", 0.0)),
                    "delta_stock": int(r.get("delta_stock", 0)),
                    "media_bayesiana": float(r.get("media_bayesiana", 0.5)),
                    "score_ucb_simulado": float(r.get("score_ucb_simulado", 0.0)),
                    "score_prioridad_meli": float(r.get("score_prioridad_meli", 0.0)),
                    "num_lecturas": int(r.get("num_lecturas", 1)),
                    "estatus_simulado": str(r.get("estatus_simulado", "")),
                    "accion_meli": str(r.get("accion_meli", "")),
                    "costo_compra": costo_v,
                    "precio_venta_sugerido": float(
                        p_ml_db if p_ml_db > 0 else econ["precio_sugerido"]
                    ),
                    "ganancia_libre_estimada": float(
                        u_ml_db if u_ml_db != 0 else econ["utilidad_neta"]
                    ),
                }
            )
        return out

    estructura_cola: Dict[str, Any] = {
        "metadata": {
            "generado_utc": datetime.now(timezone.utc).strftime(
                "%Y-%m-%d %H:%M:%S UTC"
            ),
            "catalogo_maestro_total": int(len(df)),
            "skus_auditados_total": int(len(df_auditados)),
            "skus_pendientes_escaneo": int((df["auditado_etl"] == 0).sum()),
            "total_top_100_meli": int(len(df_top_meli)),
            "total_cola_otras_plataformas": int(len(df_otras_plat)),
            "total_alertas_pausa_24h": int(len(df_pausa_meli)),
            "ultimo_lote_carril_a_24h": list(carril_a_ids),
            "ultimo_lote_carril_b_semanal": list(carril_b_ids),
        },
        "top_100_meli": _serializar_filas(df_top_meli),
        "cola_otras_plataformas": _serializar_filas(df_otras_plat),
        "alertas_pausa_24h": _serializar_filas(df_pausa_meli),
    }

    ruta_salida.write_text(
        json.dumps(estructura_cola, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return estructura_cola


# ==============================================================================
# 9. CAPA 4: ORQUESTADOR DEL PIPELINE ETL (REACT SPA + TOP 100 MELI)
# ==============================================================================
class OrquestadorPipelineETL:
    def __init__(self) -> None:
        self.repo = RepositorioSQLiteETL(RUTA_BD)
        self.extractor = ExtractorReactSPA()
        self.motor_matematico = MotorMatematicoBayesianoUCB()

    def ejecutar(
        self,
        skus_forzados: Optional[List[str]] = None,
        solo_calculo: bool = False,
    ) -> None:
        print("=" * 92)
        print(
            f"🚀 ARQUITECTURA ETL ZAPATERIA VALLEJO [{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}]"
        )
        print("=" * 92)

        exitosos, omitidos, bajas = 0, 0, 0

        if not solo_calculo:
            if not PLAYWRIGHT_DISPONIBLE:
                print(
                    "⚠️ [Aviso Arquitectura] Playwright no esta instalado en el venv."
                )

            candidatos = self.repo.obtener_lote_candidatos(
                limite=TAMANO_LOTE_ETL,
                skus_forzados=skus_forzados,
            )
            if candidatos:
                self.extractor.iniciar()
                inicio_corrida = time.time()

                try:
                    for idx, candidato in enumerate(candidatos, 1):
                        if (
                            time.time() - inicio_corrida
                        ) >= TIEMPO_LIMITE_CORRIDA_SEG:
                            print(
                                "\n⏱️ Tiempo limite de lote alcanzado (15 min). Cerrando ciclo limpiamente..."
                            )
                            break

                        print(
                            f"\n[{idx}/{len(candidatos)} | {candidato.carril}] SKU {candidato.id_ps} - {candidato.nombre_modelo[:40]}"
                        )

                        # 1. EXTRACT (Con ExtractorReactSPA original)
                        lectura = self.extractor.extraer_sku(candidato)
                        if lectura is None:
                            omitidos += 1
                            self.repo.marcar_intento_omitido(candidato.id_ps)
                            print(
                                "    ⚠️ [Extract] Sin estructura verificable; se registra intento para avanzar la cola."
                            )
                            continue

                        # 2. TRANSFORM (Con MotorMatematicoBayesianoUCB original)
                        total_global, historial_sku = (
                            self.repo.obtener_historial_sku(candidato.id_ps)
                        )
                        dictamen = self.motor_matematico.evaluar_curva_sku(
                            id_ps=candidato.id_ps,
                            stock_actual=lectura.stock_confirmado,
                            fuente_extraccion=lectura.fuente_dato,
                            total_global_lecturas=total_global,
                            historial_sku=historial_sku,
                        )

                        # 3. LOAD EN SQLITE (`historial_inventario` + `zapatos_candidatos`)
                        self.repo.guardar_resultado_etl(
                            dictamen, lectura.url_resuelta
                        )

                        exitosos += 1
                        if dictamen.estatus_etl == "BAJA_AUTOMATICA":
                            bajas += 1

                        print(
                            f"    📊 [ETL OK | {lectura.fuente_dato}] Stock: {dictamen.stock_actual} (Δ:{dictamen.delta_stock}) | "
                            f"λ: {dictamen.tasa_decaimiento_lambda}/dia | UCB: {dictamen.score_ucb} -> "
                            f"{dictamen.estatus_etl} ({dictamen.motivo_tecnico})"
                        )
                finally:
                    self.extractor.cerrar()

        # Recalcular toda la base maestra (8,165 SKUs) y exportar `data/cola_sincronizacion_ml.json`
        estado = cargar_estado_completo_bd(RUTA_BD)
        df_maestro = estado["df_maestro"]
        carril_a, carril_b = seleccionar_lote_doble_carril(df_maestro)
        df_evaluado = evaluar_dataframe_quant(df_maestro)

        if solo_calculo or exitosos > 0 or not RUTA_COLA_ML.exists():
            cola = generar_cola_sincronizacion_ml(
                df_evaluado,
                carril_a_ids=carril_a,
                carril_b_ids=carril_b,
                ruta_salida=RUTA_COLA_ML,
            )
            meta = cola["metadata"]
        else:
            meta = {
                "catalogo_maestro_total": len(df_evaluado),
                "skus_auditados_total": int(
                    (df_evaluado["auditado_etl"] >= 1).sum()
                ),
                "total_top_100_meli": 100,
                "total_cola_otras_plataformas": 300,
            }

        self.repo.consolidar_wal()

        print("\n" + "=" * 92)
        print(
            f"✅ RESUMEN ETL | Nuevos auditados en vivo: {exitosos} (Bajas: {bajas} | Omitidos: {omitidos}) | "
            f"Catalogo: {meta['catalogo_maestro_total']:,} | Total Auditados BD: {meta['skus_auditados_total']:,} | "
            f"Top 100 MeLi Listos: {meta['total_top_100_meli']}"
        )
        print("=" * 92)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Motor ETL Quant-Commerce Zapateria Vallejo"
    )
    parser.add_argument(
        "--skus", nargs="*", help="Lista opcional de SKUs especificos a procesar"
    )
    parser.add_argument(
        "--forzar", action="store_true", help="Forzar actualizacion de laminas/lectura"
    )
    parser.add_argument(
        "--solo-calculo",
        action="store_true",
        help="Evalua de inmediato la BD local sin abrir Chromium",
    )
    args = parser.parse_args()
    OrquestadorPipelineETL().ejecutar(
        skus_forzados=args.skus,
        solo_calculo=bool(args.solo_calculo),
    )