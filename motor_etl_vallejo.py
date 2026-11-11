# -*- coding: utf-8 -*-
"""
========================================================================================
ZAPATERÍA VALLEJO - MOTOR ETL QUANT-COMMERCE (COMPATIBLE 100% CON GITHUB ACTIONS)
========================================================================================
Tablas oficiales en `data/zapateria_vallejo.db`:
- `zapatos_candidatos` (8,165 SKUs del catálogo maestro)
- `historial_stock_21_dias` (2,745 registros talla por talla en `stock_vallejo`)
- `historial_inventario` (Serie de tiempo con `stock_detectado`, `tasa_decaimiento_lambda`,
  `dias_estancado`, `media_bayesiana`, `score_ucb`, `estatus_calculado`, `motivo_decision`)
Cola oficial exportada en cada corrida:
- `data/cola_sincronizacion_ml.json`
========================================================================================
"""

from __future__ import annotations

import argparse
import asyncio
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
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

import numpy as np
import pandas as pd
from PIL import Image, ImageDraw, ImageFont, ImageOps

try:
    from curl_cffi import requests as curl_requests
except ImportError:
    curl_requests = None

try:
    from playwright.async_api import async_playwright
except ImportError:
    async_playwright = None


# ==============================================================================
# 1. CONFIGURACIÓN CENTRAL Y REGLAS DE NEGOCIO
# ==============================================================================
class Config:
    RUTA_BD: Path = Path("data/zapateria_vallejo.db")
    RUTA_BD_SECUNDARIA: Path = Path("data/sinergia.db")
    RUTA_COLA_MELI: Path = Path("data/cola_sincronizacion_ml.json")
    RUTA_CATALOGO_LISTO: Path = Path("output/catalogo_listo")
    RUTA_CACHE_RAW: Path = Path("output/temp/raw_cache")
    RUTA_LOG_PUBLICO: Path = Path("logs/sinergia_publico.log")
    RUTA_LOG_INTERNO: Path = Path("logs/internos.log")

    # Cuotas de selección para el Planificador de Doble Carril
    CUOTA_CARRIL_A_MELI_24H: int = 30
    CUOTA_CARRIL_B_SEMANAL: int = 100

    # Cuota de extracción web en vivo por corrida de GitHub Actions (cada 2h)
    # 10 de Carril A + 15 de Carril B = 25 SKUs en vivo (~60-90 seg, cero riesgo de timeout)
    LOTE_EXTRACCION_VIVO_CARRIL_A: int = 10
    LOTE_EXTRACCION_VIVO_CARRIL_B: int = 15
    CONCURRENCIA_MAX: int = 4
    TIMEOUT_RED_SEG: int = 15
    MAX_TIEMPO_CORRIDA_SEG: int = 900

    LIMITE_CRITICO_BAJA: int = 3
    LECTURAS_GRACIA_UCB: int = 4
    DIAS_MAX_ESTANCAMIENTO: float = 7.0
    PENDIENTE_DECAY_PELIGROSA: float = 4.0
    FACTOR_UCB_C: float = 1.20
    MEDIA_BAYESIANA_MIN: float = 0.40
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
# 2. UTILIDADES DE DOMINIO Y VALIDACIÓN DE TIPOS
# ==============================================================================
def sanitizar_sku(raw: Any) -> str:
    txt = str(raw or "").strip().upper()
    if txt.endswith(".0"):
        txt = txt[:-2]
    return re.sub(r"[^A-Z0-9]", "", txt)


def num_seguro(valor: Any, default: float = 0.0) -> float:
    """Convierte cualquier valor a float seguro; si es NaN/None devuelve `default`."""
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
# 3. MODELO MATEMÁTICO ANTI-QUIEBRE 24H Y EVALUADOR BAYESIANO + UCB
# ==============================================================================
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
            mot = f"Stock ({stk}) <= Límite Crítico ({lim_baja})"
        elif lec > gracia_ucb and mb < lim_bayes:
            est = "BAJA_AUTOMATICA"
            mot = f"Media Bayesiana ({mb:.2f}) < Mínimo ({lim_bayes:.2f})"
        elif lec > gracia_ucb and dias >= lim_dias and dlt == 0 and dias > 0:
            est = "BAJA_AUTOMATICA"
            mot = f"Estancado ({dias:.1f} días >= {lim_dias:.1f})"
        elif dec >= lim_lambda:
            est = "ALERTA_DECAY"
            mot = f"Caída Rápida λ ({dec:.2f} >= {lim_lambda:.2f})"
        elif lec <= gracia_ucb:
            est = "EXPLORACION_UCB"
            mot = f"En gracia UCB ({lec}/{gracia_ucb} lecturas) · Stock ({stk})"
        else:
            est = "ACTIVO_SANO"
            mot = f"Activo sano · Autonomía {aq['horas_autonomia']:.0f}h"

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
# 4. ESTRUCTURAS Y CLASES DEL ESTUDIO FOTOGRÁFICO HD (1200x1200px)
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
    """Gestor transaccional SQLite para `data/zapateria_vallejo.db`."""

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
            f"Catálogo: {res.catalogo_origen}  |  ID: {res.sku}",
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
            "Stock Verificado con Reserva Dinámica Anti-Quiebre 24h",
        ]
        y_b = 1000
        for b_txt in beneficios:
            dr.text((110, y_b), f"✔  {b_txt}", fill=(241, 245, 249), font=f_b)
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
            f"FICHA TÉCNICA Y GUÍA DE TALLAS · ID {res.sku}",
            fill=(16, 185, 129),
            font=f_b,
        )
        dr.text(
            (70, 110),
            f"{res.marca.upper()} — {res.titulo[:52]}",
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
# 5. LECTOR NATIVO DEL ESQUEMA REAL DE `data/zapateria_vallejo.db`
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
                    col_txt(df_h21, "talla", "Única"),
                )
                df_h21["stock_vallejo"] = col_int(df_h21, "stock_vallejo", 0)
                df_h21["stock_olivar"] = 0
                df_h21["stock_arco_norte"] = 0
                df_h21["stock_bruto"] = df_h21["stock_vallejo"]
                df_h21["stock_neto"] = col_int(
                    df_h21, "stock_seguro_publicable", 0
                )
                df_h21["fecha_str"] = col_txt(df_h21, "fecha", "")

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
        lec_v = max(n_l_cat, mapa_lec_hi.get(sku_id, 1 if es_aud else 0))
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
            "nombre_modelo": col_txt(df_cat, "nombre_modelo", "Modelo Catálogo"),
            "marca": pd.Series(marca_col, index=df_cat.index),
            "categoria": col_txt(df_cat, "tipo_calzado", "Calzado"),
            "color": np.where(
                col_txt(df_cat, "color", "") != "",
                col_txt(df_cat, "color", ""),
                col_txt(df_cat, "color_detectado", "Tono Original"),
            ),
            "material": col_txt(
                df_cat, "material_corte", "Corte Sintético de Alta Resistencia"
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


# ==============================================================================
# 6. EXTRACTOR HÍBRIDO EN VIVO (CURL_CFFI + PLAYWRIGHT REACT FALLBACK)
# ==============================================================================
def _parsear_stock_y_costo_desde_html(html: str) -> Tuple[Optional[int], float, str]:
    if not html:
        return None, 0.0, ""
    titulo = ""
    m_tit = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
    if m_tit:
        titulo = re.sub(r"\s+", " ", m_tit.group(1)).strip()

    costo = 0.0
    m_precio = re.search(r'"price"\s*:\s*"?(\d+(?:\.\d+)?)"?', html, re.IGNORECASE)
    if m_precio:
        try:
            costo = float(m_precio.group(1))
        except ValueError:
            costo = 0.0

    patrones_stock = (
        r'"stock(?:_total|_actual|Available|Quantity)?"\s*:\s*(\d+)',
        r'"available_quantity"\s*:\s*(\d+)',
        r'"existencia(?:s)?"\s*:\s*(\d+)',
        r'"quantity"\s*:\s*(\d+)',
    )
    valores_hallados: List[int] = []
    for pat in patrones_stock:
        for m in re.finditer(pat, html, re.IGNORECASE):
            try:
                v = int(m.group(1))
                if 0 <= v <= 5000:
                    valores_hallados.append(v)
            except ValueError:
                continue

    if valores_hallados:
        return sum(valores_hallados[:12]), costo, titulo
    if "agotado" in html.lower() or "sin existencias" in html.lower():
        return 0, costo, titulo
    return None, costo, titulo


async def _extraer_lote_hibrido(
    pares_sku_carril: List[Tuple[str, str]],
    mapa_urls: Dict[str, str],
) -> List[Dict[str, Any]]:
    """
    Intenta primero extracción rápida vía `curl_cffi`. Para los SKUs que requieren
    hidratación React/Next.js, usa una instancia compartida de Playwright Chromium.
    Garantiza no superar `Config.MAX_TIEMPO_CORRIDA_SEG` en GitHub Actions.
    """
    if not pares_sku_carril:
        return []

    t_inicio = time.monotonic()
    semaforo = asyncio.Semaphore(Config.CONCURRENCIA_MAX)
    resultados: List[Dict[str, Any]] = []
    pendientes_playwright: List[Tuple[str, str]] = []

    async def _intento_curl(sku: str, carril: str) -> None:
        if (time.monotonic() - t_inicio) > Config.MAX_TIEMPO_CORRIDA_SEG:
            return
        url_obj = mapa_urls.get(sku) or f"https://www.priceshoes.com/buscar?q={sku}"
        if not str(url_obj).startswith("http"):
            url_obj = f"https://www.priceshoes.com/buscar?q={sku}"
        headers = {"User-Agent": random.choice(Config.USER_AGENTS)}

        async with semaforo:
            if curl_requests is not None:
                try:
                    resp = await asyncio.to_thread(
                        curl_requests.get,
                        url_obj,
                        headers=headers,
                        impersonate="chrome120",
                        timeout=Config.TIMEOUT_RED_SEG,
                    )
                    if resp.status_code == 200:
                        stk, costo, tit = _parsear_stock_y_costo_desde_html(resp.text)
                        if stk is not None:
                            resultados.append(
                                {
                                    "id_ps": sku,
                                    "stock_actual": stk,
                                    "costo_base": costo,
                                    "nombre_modelo": tit,
                                    "carril_origen": carril,
                                    "fuente_extraccion": f"CURL_{carril}",
                                }
                            )
                            return
                except Exception:
                    pass
            pendientes_playwright.append((sku, carril))

    await asyncio.gather(*(_intento_curl(s, c) for s, c in pares_sku_carril))

    # Fallback con Playwright Chromium Headless para páginas SPA React
    if (
        pendientes_playwright
        and async_playwright is not None
        and (time.monotonic() - t_inicio) < Config.MAX_TIEMPO_CORRIDA_SEG
    ):
        try:
            async with async_playwright() as p_mgr:
                browser = await p_mgr.chromium.launch(
                    headless=True,
                    args=["--no-sandbox", "--disable-dev-shm-usage"],
                )
                context = await browser.new_context(
                    user_agent=random.choice(Config.USER_AGENTS),
                    viewport={"width": 1280, "height": 800},
                )
                page = await context.new_page()

                for sku, carril in pendientes_playwright[:15]:
                    if (time.monotonic() - t_inicio) > Config.MAX_TIEMPO_CORRIDA_SEG:
                        break
                    url_obj = (
                        mapa_urls.get(sku)
                        or f"https://www.priceshoes.com/buscar?q={sku}"
                    )
                    if not str(url_obj).startswith("http"):
                        url_obj = f"https://www.priceshoes.com/buscar?q={sku}"
                    try:
                        await page.goto(
                            url_obj,
                            wait_until="domcontentloaded",
                            timeout=Config.TIMEOUT_RED_SEG * 1000,
                        )
                        await page.wait_for_timeout(1200)
                        contenido = await page.content()
                        stk, costo, tit = _parsear_stock_y_costo_desde_html(contenido)
                        if stk is not None:
                            resultados.append(
                                {
                                    "id_ps": sku,
                                    "stock_actual": stk,
                                    "costo_base": costo,
                                    "nombre_modelo": tit,
                                    "carril_origen": carril,
                                    "fuente_extraccion": f"PLAYWRIGHT_{carril}",
                                }
                            )
                    except Exception:
                        continue

                await context.close()
                await browser.close()
        except Exception:
            pass

    return resultados


def registrar_lecturas_en_bd(
    ruta_bd: Path,
    lecturas: List[Dict[str, Any]],
    total_auditados_ref: int = 659,
) -> int:
    """
    Registra nuevas lecturas en `historial_inventario` calculando en el acto
    delta_stock, tasa_decaimiento_lambda, dias_estancado, media_bayesiana,
    score_ucb, estatus_calculado y motivo_decision, y actualiza `zapatos_candidatos`.
    """
    if not lecturas or not ruta_bd.exists():
        return 0
    ahora_dt = datetime.now(timezone.utc)
    ahora_iso = ahora_dt.strftime("%Y-%m-%d %H:%M:%S")
    ln_n = math.log(float(max(2, total_auditados_ref)))

    with DBHandler(ruta_bd) as db:
        assert db.conn is not None
        insertados = 0
        for item in lecturas:
            sku = sanitizar_sku(item.get("id_ps"))
            if not sku:
                continue
            stk = max(0, int(item.get("stock_actual") or 0))
            fuente = str(
                item.get("fuente_extraccion")
                or item.get("carril_origen")
                or "AUTO_ETL"
            )

            filas_prev = db.conn.execute(
                """
                SELECT stock_detectado, fecha_hora_consulta
                FROM historial_inventario
                WHERE id_ps = ?
                ORDER BY id_consulta ASC;
                """,
                (sku,),
            ).fetchall()

            n_lec_nueva = len(filas_prev) + 1
            if filas_prev:
                stk_ant = int(filas_prev[-1][0] or 0)
                delta = stk - stk_ant
                stk_primero = int(filas_prev[0][0] or stk)
                ts_primero = pd.to_datetime(
                    filas_prev[0][1], errors="coerce", utc=True
                )
                if pd.notna(ts_primero):
                    dias_trans = max(
                        0.08,
                        (
                            pd.Timestamp(ahora_dt) - ts_primero
                        ).total_seconds()
                        / 86400.0,
                    )
                else:
                    dias_trans = 1.0
                caida_neta = max(0, stk_primero - stk)
                tasa_lambda = round(caida_neta / dias_trans, 2)
                dias_est = round(dias_trans, 2) if delta == 0 else 0.0
                exitos = sum(
                    1
                    for r_p in filas_prev
                    if int(r_p[0] or 0) > Config.LIMITE_CRITICO_BAJA
                ) + (1 if stk > Config.LIMITE_CRITICO_BAJA else 0)
                media_bay = round((exitos + 1.0) / (n_lec_nueva + 2.0), 3)
            else:
                delta = 0
                tasa_lambda = 0.0
                dias_est = 0.0
                media_bay = 0.5

            score_ucb_val = round(
                media_bay
                + Config.FACTOR_UCB_C * math.sqrt(ln_n / float(n_lec_nueva)),
                3,
            )

            if stk <= Config.LIMITE_CRITICO_BAJA:
                est_calc = "BAJA_AUTOMATICA"
                mot_calc = f"Stock ({stk}) <= Límite ({Config.LIMITE_CRITICO_BAJA})"
            elif tasa_lambda >= Config.PENDIENTE_DECAY_PELIGROSA:
                est_calc = "ALERTA_DECAY"
                mot_calc = f"Caída rápida λ={tasa_lambda:.2f}/día"
            elif n_lec_nueva <= Config.LECTURAS_GRACIA_UCB:
                est_calc = "EXPLORACION_UCB"
                mot_calc = f"En gracia UCB ({n_lec_nueva}/{Config.LECTURAS_GRACIA_UCB})"
            else:
                est_calc = "ACTIVO_SANO"
                mot_calc = f"Stock estable ({stk}) y Media ({media_bay:.2f})"

            db.conn.execute(
                """
                INSERT INTO historial_inventario (
                    id_ps, stock_detectado, stock_bruto_detectado, delta_stock,
                    tasa_decaimiento_lambda, dias_estancado, media_bayesiana,
                    score_ucb, estatus_calculado, motivo_decision,
                    fecha_hora_consulta, fuente_extraccion
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    sku,
                    stk,
                    stk,
                    delta,
                    tasa_lambda,
                    dias_est,
                    media_bay,
                    score_ucb_val,
                    est_calc,
                    mot_calc,
                    ahora_iso,
                    fuente,
                ),
            )
            db.conn.execute(
                """
                UPDATE zapatos_candidatos
                SET stock_publicable_total = ?,
                    stock_bruto_anterior = ?,
                    tasa_decaimiento = ?,
                    media_bayesiana = ?,
                    score_ucb = ?,
                    estatus_etl = ?,
                    num_consultas_hist = COALESCE(num_consultas_hist, 0) + 1,
                    ultima_actualizacion = ?
                WHERE id_ps = ?;
                """,
                (
                    stk,
                    stk,
                    tasa_lambda,
                    media_bay,
                    score_ucb_val,
                    est_calc,
                    ahora_iso,
                    sku,
                ),
            )
            insertados += 1
        return insertados


# ==============================================================================
# 7. GENERADOR AUTOMÁTICO DE `data/cola_sincronizacion_ml.json`
# ==============================================================================
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


def consolidar_wal_sqlite(ruta_bd: Path = Config.RUTA_BD) -> None:
    if not ruta_bd.exists() or ruta_bd.stat().st_size == 0:
        return
    try:
        with closing(sqlite3.connect(ruta_bd, timeout=15.0)) as conn:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
            conn.execute("PRAGMA optimize;")
    except Exception:
        pass


# ==============================================================================
# 8. PUNTO DE ENTRADA CLI (GITHUB ACTIONS Y EJECUCIÓN MANUAL)
# ==============================================================================
def ejecutar_pipeline_completo(
    skus_forzados: Optional[List[str]] = None,
    solo_calculo: bool = False,
) -> None:
    log_pub, log_int = configurar_loggers()
    estado = cargar_estado_completo_bd(Config.RUTA_BD)
    df_maestro = estado["df_maestro"]

    if df_maestro.empty or "auditado_etl" not in df_maestro.columns:
        log_pub.error("Catálogo maestro vacío o BD no encontrada en %s", Config.RUTA_BD)
        print("[✗] No hay datos en la BD; pipeline cancelado.")
        return

    if skus_forzados:
        carril_a = [sanitizar_sku(s) for s in skus_forzados if sanitizar_sku(s)]
        carril_b = []
    else:
        carril_a, carril_b = seleccionar_lote_doble_carril(
            df_maestro,
            cuota_carril_a=Config.CUOTA_CARRIL_A_MELI_24H,
            cuota_carril_b=Config.CUOTA_CARRIL_B_SEMANAL,
        )

    log_pub.info(
        "Planificador Doble Carril: Carril A (24h MeLi)=%d SKUs | Carril B (Semanal)=%d SKUs",
        len(carril_a),
        len(carril_b),
    )

    # Si no se pidió `--solo-calculo`, ejecutar el extractor híbrido en vivo (GitHub Actions)
    if not solo_calculo:
        mapa_urls = dict(
            zip(
                col_txt(df_maestro, "id_ps", "").tolist(),
                col_txt(df_maestro, "url_origen", "").tolist(),
            )
        )
        if skus_forzados:
            pares_extraer = [(s, "FORZADO_UI") for s in carril_a]
        else:
            pares_extraer = [
                (s, "CARRIL_A_24H")
                for s in carril_a[: Config.LOTE_EXTRACCION_VIVO_CARRIL_A]
            ] + [
                (s, "CARRIL_B_SEMANAL")
                for s in carril_b[: Config.LOTE_EXTRACCION_VIVO_CARRIL_B]
            ]

        try:
            nuevas_lecturas = asyncio.run(
                _extraer_lote_hibrido(pares_extraer, mapa_urls)
            )
            if nuevas_lecturas:
                total_aud_actual = int((df_maestro["auditado_etl"] >= 1).sum())
                n_reg = registrar_lecturas_en_bd(
                    Config.RUTA_BD,
                    nuevas_lecturas,
                    total_auditados_ref=max(659, total_aud_actual),
                )
                log_pub.info(
                    "Se registraron %d nuevas lecturas en historial_inventario.", n_reg
                )
                estado = cargar_estado_completo_bd(Config.RUTA_BD)
                df_maestro = estado["df_maestro"]
        except Exception as exc:
            log_int.warning("Aviso en extracción en vivo: %s", exc)

    df_evaluado = evaluar_dataframe_quant(df_maestro)
    cola = generar_cola_sincronizacion_ml(
        df_evaluado,
        carril_a_ids=carril_a,
        carril_b_ids=carril_b,
        ruta_salida=Config.RUTA_COLA_MELI,
    )
    consolidar_wal_sqlite(Config.RUTA_BD)

    meta = cola["metadata"]
    print(
        f"[✓] Pipeline Quant completado | Catálogo: {meta['catalogo_maestro_total']:,} | "
        f"Auditados: {meta['skus_auditados_total']:,} | "
        f"Top 100 MeLi Listos: {meta['total_top_100_meli']} | "
        f"Cola Otras Plataformas: {meta['total_cola_otras_plataformas']}"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Motor ETL Quant-Commerce Zapatería Vallejo"
    )
    parser.add_argument(
        "--skus", nargs="*", help="Lista opcional de SKUs específicos a procesar"
    )
    parser.add_argument(
        "--forzar", action="store_true", help="Forzar actualización de láminas/lectura"
    )
    parser.add_argument(
        "--solo-calculo",
        action="store_true",
        help="Evalúa de inmediato la BD local y genera cola_sincronizacion_ml.json sin abrir navegador",
    )
    args = parser.parse_args()
    ejecutar_pipeline_completo(
        skus_forzados=args.skus,
        solo_calculo=bool(args.solo_calculo),
    )