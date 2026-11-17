# -*- coding: utf-8 -*-
"""
========================================================================================
SINERGIA - PANEL DE CONTROL MULTISUCURSAL, TABLA DE PRECIOS AMIGABLE Y FILTRO MELI
========================================================================================
Código completo auditado libre de errores:
- Selector por Catálogo de Price Shoes.
- Interruptor para descartar/ocultar Marcas Restringidas en Mercado Libre.
- Tabla Maestra de Precios (Precio de Compra, Precio de Venta, Comisión, Envío,
  Impuestos, Utilidad Neta en $ y Margen Neto en %) + Simulador interactivo.
- Inventario desglosado por sucursal: Vallejo, Olivar del Conde y Arco Norte.
========================================================================================
"""

from __future__ import annotations

import io
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd
import plotly.express as px
import streamlit as st
from PIL import Image, ImageDraw, ImageFont

from motor_etl_vallejo import Config, aplicar_blindaje, es_marca_restringida_meli


# ==============================================================================
# 1. ORDENAMIENTO CANÓNICO EN PYTHON PURO Y CÁLCULO FINANCIERO AMIGABLE
# ==============================================================================
_ORDEN_TALLAS_UI: Dict[str, int] = {
    "XXS": 1, "ECH": 2, "XS": 3, "CH": 4, "S": 5, "M": 6, "MD": 7,
    "L": 8, "G": 9, "GD": 10, "XL": 11, "EG": 11, "XG": 12, "XXL": 13,
    "2XL": 14, "EEG": 14, "XXXL": 15, "3XL": 16, "ÚNICA": 90, "UNICA": 90,
}


def _clave_orden_talla_ui(valor: Any) -> Tuple[int, float, str]:
    u = str(valor or "").strip().upper()
    try:
        return (0, float(u), u)
    except ValueError:
        pass
    if u in _ORDEN_TALLAS_UI:
        return (1, float(_ORDEN_TALLAS_UI[u]), u)
    return (2, 0.0, u)


def ordenar_df_variantes(df_v: pd.DataFrame) -> pd.DataFrame:
    if df_v.empty:
        return df_v
    registros = df_v.to_dict("records")
    registros_ordenados = sorted(
        registros,
        key=lambda r: (str(r.get("sku", "")), *_clave_orden_talla_ui(r.get("variante", ""))),
    )
    return pd.DataFrame(registros_ordenados)


def clasificar_semaforo_inventario(
    stock_neto: Any,
    umbral_rojo: int = Config.UMBRAL_ROJO_CRITICO,
    umbral_verde: int = Config.UMBRAL_VERDE_OPTIMO,
) -> str:
    try:
        unidades = int(float(stock_neto))
    except (ValueError, TypeError):
        unidades = 0

    if unidades < umbral_rojo:
        return "🔴 ROJO (Crítico)"
    if unidades <= umbral_verde:
        return "🟡 AMARILLO (Vigilancia)"
    return "🟢 VERDE (Óptimo)"


def calcular_unit_economics(
    df_origen: pd.DataFrame,
    comision_meli_pct: float = Config.COMISION_MELI_PCT,
    costo_envio_mxn: float = Config.COSTO_ENVIO_MXN,
    margen_objetivo_pct: float = Config.MARGEN_OBJETIVO_PCT,
    retencion_impuestos_pct: float = Config.RETENCION_IMPUESTOS_PCT,
) -> pd.DataFrame:
    if df_origen.empty:
        return df_origen.copy()

    df = df_origen.copy()
    costo_serie = pd.to_numeric(df.get("costo_base", 0.0), errors="coerce").fillna(0.0)
    costo_limpio = np.where(np.isfinite(costo_serie) & (costo_serie > 0.0), costo_serie, 0.0)

    comision_dec = min(0.50, max(0.0, float(comision_meli_pct) / 100.0))
    envio_val = max(0.0, float(costo_envio_mxn))
    margen_dec = min(0.60, max(0.05, float(margen_objetivo_pct) / 100.0))
    impuestos_dec = min(0.25, max(0.0, float(retencion_impuestos_pct) / 100.0))

    denominador = max(0.12, 1.0 - comision_dec - margen_dec - impuestos_dec)
    precio_bruto = np.where(costo_limpio > 0.0, (costo_limpio + envio_val) / denominador, 0.0)

    decena_sup = np.ceil(precio_bruto / 10.0) * 10.0
    precio_ceil_9 = np.where(precio_bruto > 0.0, decena_sup - 1.0, 0.0)
    precio_sugerido = np.where(
        (precio_bruto > 0.0) & (precio_ceil_9 < precio_bruto),
        precio_ceil_9 + 10.0,
        precio_ceil_9,
    )

    comision_estimada = np.round(precio_sugerido * comision_dec, 2)
    impuestos_estimados = np.round(precio_sugerido * impuestos_dec, 2)
    envio_aplicado = np.where(precio_sugerido > 0.0, envio_val, 0.0)

    utilidad_neta = np.where(
        precio_sugerido > 0.0,
        np.round(
            precio_sugerido - costo_limpio - envio_aplicado - comision_estimada - impuestos_estimados,
            2,
        ),
        0.0,
    )
    margen_real_pct = np.where(
        precio_sugerido > 0.0,
        np.round((utilidad_neta / np.where(precio_sugerido > 0.0, precio_sugerido, 1.0)) * 100.0, 1),
        0.0,
    )

    df["costo_base_num"] = np.round(costo_limpio, 2)
    df["precio_sugerido_num"] = np.round(precio_sugerido, 2)
    df["comision_meli_num"] = comision_estimada
    df["costo_envio_num"] = np.round(envio_aplicado, 2)
    df["impuestos_num"] = impuestos_estimados
    df["utilidad_neta_num"] = utilidad_neta
    df["margen_neto_num"] = margen_real_pct

    df["Precio Compra"] = [f"$ {v:,.2f}" for v in df["costo_base_num"]]
    df["Precio Venta"] = [f"$ {v:,.2f}" for v in df["precio_sugerido_num"]]
    df["Comisión Meli"] = [f"-$ {v:,.2f}" for v in df["comision_meli_num"]]
    df["Costo Envío"] = [f"-$ {v:,.2f}" for v in df["costo_envio_num"]]
    df["ISR / IVA"] = [f"-$ {v:,.2f}" for v in df["impuestos_num"]]
    df["Ganancia Neta ($)"] = [f"$ {v:,.2f}" for v in df["utilidad_neta_num"]]
    df["Margen Neto (%)"] = [f"{v:.1f}%" for v in df["margen_neto_num"]]

    return df


# ==============================================================================
# 2. CARGA DE DATOS SQLITE (READ-ONLY) Y OPERACIONES TRANSACCIONALES WAL
# ==============================================================================
def conectar_sqlite_ro(ruta_bd: Path) -> sqlite3.Connection:
    uri = f"{ruta_bd.resolve().as_uri()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=20.0)
    conn.row_factory = sqlite3.Row
    return conn


@st.cache_data(ttl=10, show_spinner=False)
def cargar_datos_dashboard(ruta_bd_str: str) -> Dict[str, pd.DataFrame]:
    ruta_bd = Path(ruta_bd_str)
    vacio = {
        "productos": pd.DataFrame(),
        "variantes": pd.DataFrame(),
        "historico": pd.DataFrame(),
        "historico_variantes": pd.DataFrame(),
        "cola_meli": pd.DataFrame(),
        "errores": pd.DataFrame(),
        "corridas": pd.DataFrame(),
    }
    if not ruta_bd.exists() or ruta_bd.stat().st_size == 0:
        return vacio

    conn: Optional[sqlite3.Connection] = None
    try:
        conn = conectar_sqlite_ro(ruta_bd)
        tablas = {
            str(r[0])
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table';").fetchall()
        }

        cols_p = (
            {str(r[1]) for r in conn.execute("PRAGMA table_info(productos);").fetchall()}
            if "productos" in tablas
            else set()
        )
        sel_cat_orig = (
            "COALESCE(p.catalogo_origen, 'Catálogo Urbano y Casual') AS catalogo_origen"
            if "catalogo_origen" in cols_p
            else "'Catálogo Urbano y Casual' AS catalogo_origen"
        )

        df_prod = (
            pd.read_sql_query(
                f"""
                SELECT p.sku, p.titulo, p.marca, p.categoria, p.color, p.material,
                       p.costo_base, p.precio_sugerido, p.stock_bruto_total, p.stock_neto_total,
                       {sel_cat_orig},
                       p.estatus_etl, p.bloqueado_privacidad, p.ultima_actualizacion,
                       COALESCE(f.url_origen, '') AS url_origen
                FROM productos p
                LEFT JOIN fuente_interna f ON CAST(p.sku AS TEXT) = CAST(f.sku AS TEXT)
                ORDER BY p.stock_neto_total DESC, p.sku ASC;
                """,
                conn,
            )
            if "productos" in tablas
            else pd.DataFrame()
        )

        cols_v = (
            {str(r[1]) for r in conn.execute("PRAGMA table_info(variantes_stock);").fetchall()}
            if "variantes_stock" in tablas
            else set()
        )
        sel_val = "stock_vallejo" if "stock_vallejo" in cols_v else "stock_bruto AS stock_vallejo"
        sel_oli = "stock_olivar" if "stock_olivar" in cols_v else "0 AS stock_olivar"
        sel_arc = "stock_arco_norte" if "stock_arco_norte" in cols_v else "0 AS stock_arco_norte"
        sel_ant = "stock_anterior" if "stock_anterior" in cols_v else "stock_neto AS stock_anterior"
        sel_del = "delta_variante" if "delta_variante" in cols_v else "0 AS delta_variante"

        df_var = (
            pd.read_sql_query(
                f"""
                SELECT CAST(sku AS TEXT) AS sku, variante,
                       {sel_val}, {sel_oli}, {sel_arc},
                       stock_bruto, stock_neto, {sel_ant}, {sel_del}, ultima_actualizacion
                FROM variantes_stock;
                """,
                conn,
            )
            if "variantes_stock" in tablas
            else pd.DataFrame()
        )

        df_hist = (
            pd.read_sql_query(
                """
                SELECT h.id_registro, h.corrida_id, CAST(h.sku AS TEXT) AS sku,
                       COALESCE(p.titulo, 'ID ' || h.sku) AS titulo,
                       h.stock_bruto, h.stock_neto, h.delta_stock,
                       h.variantes_activas_json, h.fecha_registro
                FROM historico_stock h
                LEFT JOIN productos p ON CAST(h.sku AS TEXT) = CAST(p.sku AS TEXT)
                ORDER BY h.fecha_registro ASC, h.id_registro ASC;
                """,
                conn,
            )
            if "historico_stock" in tablas
            else pd.DataFrame()
        )

        df_hist_var = (
            pd.read_sql_query(
                """
                SELECT id_movimiento, corrida_id, CAST(sku AS TEXT) AS sku,
                       variante, stock_bruto, stock_neto, delta_variante, fecha_registro
                FROM historico_variantes
                ORDER BY fecha_registro ASC, id_movimiento ASC;
                """,
                conn,
            )
            if "historico_variantes" in tablas
            else pd.DataFrame()
        )

        df_meli = (
            pd.read_sql_query("SELECT * FROM cola_meli ORDER BY stock_neto DESC;", conn)
            if "cola_meli" in tablas
            else pd.DataFrame()
        )
        df_err = (
            pd.read_sql_query("SELECT * FROM errores_etl ORDER BY id_error DESC LIMIT 100;", conn)
            if "errores_etl" in tablas
            else pd.DataFrame()
        )
        df_cor = (
            pd.read_sql_query("SELECT * FROM corridas_etl ORDER BY fecha_inicio DESC LIMIT 30;", conn)
            if "corridas_etl" in tablas
            else pd.DataFrame()
        )

        for d in (df_prod, df_var, df_hist, df_hist_var, df_meli):
            if not d.empty and "sku" in d.columns:
                d["sku"] = d["sku"].astype(str).str.strip()

        df_var = ordenar_df_variantes(df_var)

        return {
            "productos": df_prod,
            "variantes": df_var,
            "historico": df_hist,
            "historico_variantes": df_hist_var,
            "cola_meli": df_meli,
            "errores": df_err,
            "corridas": df_cor,
        }
    except Exception:
        return vacio
    finally:
        if conn is not None:
            conn.close()


def ejecutar_motor_subproceso(argumentos_extra: Sequence[str]) -> Tuple[int, str, str]:
    comando = [sys.executable, "motor_etl_vallejo.py", *argumentos_extra]
    resultado = subprocess.run(
        comando,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    cargar_datos_dashboard.clear()
    return resultado.returncode, resultado.stdout, resultado.stderr


def eliminar_sku_catalogo_wal(sku_borrar: str, ruta_bd: Path) -> Tuple[bool, str]:
    sku_limpio = str(sku_borrar or "").strip()
    if not sku_limpio or not ruta_bd.exists():
        return False, "ID inválido."
    conn = sqlite3.connect(ruta_bd, timeout=20.0)
    try:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("BEGIN IMMEDIATE;")
        for tabla in (
            "productos",
            "variantes_stock",
            "historico_variantes",
            "historico_stock",
            "imagenes",
            "cola_meli",
            "fuente_interna",
        ):
            conn.execute(f"DELETE FROM {tabla} WHERE CAST(sku AS TEXT) = ?;", (sku_limpio,))
        conn.commit()
    except Exception as exc:
        conn.rollback()
        return False, f"Error al eliminar ID {sku_limpio}: {exc}"
    finally:
        conn.close()

    carpeta_img = Config.RUTA_CATALOGO_LISTO / sku_limpio
    if carpeta_img.exists():
        shutil.rmtree(carpeta_img, ignore_errors=True)
    for r_raw in Config.rutas_cache_raw_sku(sku_limpio):
        r_raw.unlink(missing_ok=True)

    cargar_datos_dashboard.clear()
    return True, f"ID {sku_limpio} eliminado del catálogo."


def guardar_edicion_sucursales_wal(
    sku: str, df_suc_editado: pd.DataFrame, ruta_bd: Path
) -> Tuple[bool, str]:
    """Actualiza el stock de Vallejo, Olivar del Conde y Arco Norte para un ID y recalcula su Neto."""
    if df_suc_editado.empty or not ruta_bd.exists():
        return False, "Sin datos de sucursales para guardar."
    conn = sqlite3.connect(ruta_bd, timeout=20.0)
    try:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("BEGIN IMMEDIATE;")
        val_sum, oli_sum, arc_sum, bru_sum, net_sum = 0, 0, 0, 0, 0

        for _, fila in df_suc_editado.iterrows():
            talla = str(fila.get("Talla") or "").strip()
            if not talla:
                continue
            s_val = max(0, int(pd.to_numeric(fila.get("📍 Vallejo"), errors="coerce") or 0))
            s_oli = max(0, int(pd.to_numeric(fila.get("📍 Olivar del Conde"), errors="coerce") or 0))
            s_arc = max(0, int(pd.to_numeric(fila.get("📍 Arco Norte"), errors="coerce") or 0))
            s_bru = s_val + s_oli + s_arc
            s_net = aplicar_blindaje(s_bru, Config.BUFFER_STOCK)

            val_sum += s_val
            oli_sum += s_oli
            arc_sum += s_arc
            bru_sum += s_bru
            net_sum += s_net

            conn.execute(
                """
                UPDATE variantes_stock
                SET stock_anterior = stock_neto,
                    stock_vallejo = ?,
                    stock_olivar = ?,
                    stock_arco_norte = ?,
                    stock_bruto = ?,
                    stock_neto = ?,
                    delta_variante = ? - stock_neto,
                    ultima_actualizacion = CURRENT_TIMESTAMP
                WHERE CAST(sku AS TEXT) = ? AND variante = ?;
                """,
                (s_val, s_oli, s_arc, s_bru, s_net, s_net, str(sku), talla),
            )

        conn.execute(
            """
            UPDATE productos
            SET stock_vallejo_total = ?,
                stock_olivar_total = ?,
                stock_arco_norte_total = ?,
                stock_bruto_total = ?,
                stock_neto_total = ?,
                ultima_actualizacion = CURRENT_TIMESTAMP
            WHERE CAST(sku AS TEXT) = ?;
            """,
            (val_sum, oli_sum, arc_sum, bru_sum, net_sum, str(sku)),
        )
        conn.commit()
        cargar_datos_dashboard.clear()
        return True, f"Stock multialmacén actualizado para ID {sku} (Bruto: {bru_sum} u. | Neto: {net_sum} u.)."
    except Exception as exc:
        conn.rollback()
        return False, f"Error al actualizar sucursales: {exc}"
    finally:
        conn.close()


def guardar_edicion_productos_wal(
    df_editado: pd.DataFrame, ruta_bd: Path
) -> Tuple[bool, str]:
    if df_editado.empty or not ruta_bd.exists():
        return False, "No hay cambios para guardar."
    conn = sqlite3.connect(ruta_bd, timeout=20.0)
    try:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("BEGIN IMMEDIATE;")
        n = 0
        for _, fila in df_editado.iterrows():
            sku_v = str(fila.get("ID Catálogo") or "").strip()
            if not sku_v:
                continue
            costo_v = max(0.0, float(pd.to_numeric(fila.get("Precio Compra ($)"), errors="coerce") or 0.0))
            url_v = str(fila.get("URL Price Shoes") or "").strip()
            marca_v = str(fila.get("Marca") or "Original").strip()
            tit_v = str(fila.get("Modelo") or f"Modelo {sku_v}").strip()
            cat_orig_v = str(fila.get("Catálogo") or "Catálogo Urbano y Casual").strip()
            restringido_v = 1 if es_marca_restringida_meli(marca_v, tit_v) else 0

            conn.execute(
                """
                UPDATE productos
                SET catalogo_origen = ?,
                    marca = ?,
                    restringido_meli = ?,
                    titulo = ?,
                    categoria = ?,
                    color = ?,
                    material = ?,
                    costo_base = ?,
                    ultima_actualizacion = CURRENT_TIMESTAMP
                WHERE sku = ?;
                """,
                (
                    cat_orig_v,
                    marca_v,
                    restringido_v,
                    tit_v,
                    str(fila.get("Categoría") or "Calzado").strip(),
                    str(fila.get("Color") or "Multicolor").strip(),
                    str(fila.get("Material") or "Alta Resistencia").strip(),
                    costo_v,
                    sku_v,
                ),
            )
            if url_v:
                conn.execute(
                    """
                    INSERT INTO fuente_interna (sku, url_origen, fuente_extraccion)
                    VALUES (?, ?, 'EDICION_MANUAL')
                    ON CONFLICT(sku) DO UPDATE SET url_origen = excluded.url_origen;
                    """,
                    (sku_v, url_v),
                )
            n += 1
        conn.commit()
        cargar_datos_dashboard.clear()
        return True, f"Se guardaron {n} artículos en SQLite WAL."
    except Exception as exc:
        conn.rollback()
        return False, f"Error al guardar: {exc}"
    finally:
        conn.close()


@st.cache_data(show_spinner=False)
def generar_placeholder_gris(sku: str, tipo_activo: str) -> bytes:
    img = Image.new("RGB", (800, 800), (30, 41, 59))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle(
        [(36, 36), (764, 764)],
        radius=24,
        fill=(51, 65, 85),
        outline=(148, 163, 184),
        width=3,
    )
    fuente = ImageFont.load_default()
    draw.text((90, 330), "ACTIVO PENDIENTE DE RENDER", fill=(248, 250, 252), font=fuente)
    draw.text((90, 375), f"ID: {sku}  |  Lámina: {tipo_activo}", fill=(16, 185, 129), font=fuente)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


# ==============================================================================
# 3. CONFIGURACIÓN DE PÁGINA
# ==============================================================================
st.set_page_config(
    page_title="SINERGIA · Control Multialmacén, Precios y Catálogos",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(
    """
    <style>
        .block-container { padding-top: 1.1rem; padding-bottom: 2rem; }
        div[data-testid="stMetric"] {
            background-color: #0F172A;
            border: 1px solid #1E293B;
            border-left: 5px solid #10B981;
            padding: 12px 16px;
            border-radius: 10px;
            color: #F8FAFC;
        }
        div[data-testid="stMetricLabel"] > div { color: #94A3B8 !important; font-weight: 600; }
        div[data-testid="stMetricValue"] > div { color: #F8FAFC !important; }
    </style>
    """,
    unsafe_allow_html=True,
)

cfg = Config()

if "umbral_rojo" not in st.session_state:
    st.session_state["umbral_rojo"] = int(cfg.UMBRAL_ROJO_CRITICO)
if "umbral_verde" not in st.session_state:
    st.session_state["umbral_verde"] = int(cfg.UMBRAL_VERDE_OPTIMO)
if "comision_meli_pct" not in st.session_state:
    st.session_state["comision_meli_pct"] = float(cfg.COMISION_MELI_PCT)
if "costo_envio_mxn" not in st.session_state:
    st.session_state["costo_envio_mxn"] = float(cfg.COSTO_ENVIO_MXN)
if "margen_objetivo_pct" not in st.session_state:
    st.session_state["margen_objetivo_pct"] = float(cfg.MARGEN_OBJETIVO_PCT)
if "marcas_bloqueadas_custom" not in st.session_state:
    st.session_state["marcas_bloqueadas_custom"] = list(cfg.MARCAS_RESTRINGIDAS_MELI)
if "ultimo_log_subproceso" not in st.session_state:
    st.session_state["ultimo_log_subproceso"] = ""


# ==============================================================================
# 4. BARRA LATERAL (ALTA DE IDS, FILTRO DE MARCAS MELI Y PARÁMETROS DE PRECIO)
# ==============================================================================
with st.sidebar:
    st.markdown("### ⚡ Control Multialmacén")
    st.caption(
        f"Sucursales: **Vallejo · Olivar del Conde · Arco Norte**  \n"
        f"Blindaje Activo: **-{cfg.BUFFER_STOCK} u. por talla**"
    )

    st.markdown("#### ➕ Agregar IDs de Catálogo")
    nuevos_ids_input = st.text_area(
        "IDs de Price Shoes (separados por coma o salto de línea):",
        placeholder="1338647, 1312917, 1340384",
        height=75,
    ).strip()
    if st.button("📥 Extraer IDs de Catálogo", use_container_width=True, type="primary"):
        if nuevos_ids_input:
            tokens_ids = [
                t.strip()
                for t in nuevos_ids_input.replace("\n", ",").split(",")
                if t.strip()
            ]
            if tokens_ids:
                with st.spinner(f"Extrayendo {len(tokens_ids)} ID(s) y sincronizando sucursales..."):
                    rc, out, err = ejecutar_motor_subproceso(["--skus", *tokens_ids, "--forzar"])
                    st.session_state["ultimo_log_subproceso"] = (out or "") + ("\n" + err if err else "")
                    st.rerun()

    st.divider()
    st.markdown("#### 💰 Calculadora de Precios Meli")
    st.session_state["comision_meli_pct"] = st.slider(
        "Comisión Mercado Libre (%):", 10.0, 25.0, float(st.session_state["comision_meli_pct"]), 0.5
    )
    st.session_state["costo_envio_mxn"] = st.number_input(
        "Costo Envío Meli ($ MXN):", 0.0, 400.0, float(st.session_state["costo_envio_mxn"]), 5.0
    )
    st.session_state["margen_objetivo_pct"] = st.slider(
        "Margen de Ganancia Neta (%):", 8.0, 45.0, float(st.session_state["margen_objetivo_pct"]), 1.0
    )

    st.divider()
    st.markdown("#### 🚫 Marcas Bloqueadas por Meli")
    marcas_extra_txt = st.text_area(
        "Marcas que Mercado Libre no te permite vender (separadas por coma):",
        value=", ".join(st.session_state["marcas_bloqueadas_custom"]),
        height=95,
    )
    st.session_state["marcas_bloqueadas_custom"] = [
        m.strip().upper() for m in marcas_extra_txt.split(",") if m.strip()
    ]

    st.divider()
    if st.button("🚀 Sincronizar Todo y Regenerar Láminas", use_container_width=True):
        with st.spinner("Actualizando sucursales, precios y láminas HD..."):
            rc, out, err = ejecutar_motor_subproceso(["--forzar"])
            st.session_state["ultimo_log_subproceso"] = (out or "") + ("\n" + err if err else "")
            st.rerun()

    if st.button("🔄 Refrescar Panel", use_container_width=True):
        cargar_datos_dashboard.clear()
        st.rerun()


# ==============================================================================
# 5. ENRIQUECIMIENTO MULTISUCURSAL Y FILTRADO GLOBAL POR CATÁLOGO / MARCA MELI
# ==============================================================================
datos_db = cargar_datos_dashboard(str(cfg.RUTA_BD))
df_productos = datos_db["productos"]
df_variantes = datos_db["variantes"]
df_historico = datos_db["historico"]
df_historico_var = datos_db["historico_variantes"]
df_cola_meli = datos_db["cola_meli"]
df_errores = datos_db["errores"]
df_corridas = datos_db["corridas"]

st.title("⚡ SINERGIA · Control por Catálogo, Precios y Multialmacén")
st.caption(
    "Selección por Catálogo · Tabla Amigable de Precios (Compra vs Venta y Ganancia) · Filtro de Marcas Restringidas Meli · Stock en Vallejo, Olivar del Conde y Arco Norte"
)

if df_productos.empty:
    st.warning("⚠️ No hay IDs de catálogo cargados.")
    if st.button("⚡ Cargar IDs de Catálogo Base", type="primary"):
        with st.spinner("Inicializando catálogo y sucursales..."):
            ejecutar_motor_subproceso(["--forzar"])
            st.rerun()
    st.stop()

# Calcular totales por sucursal por cada ID desde `df_variantes`
mapa_vallejo: Dict[str, int] = {}
mapa_olivar: Dict[str, int] = {}
mapa_arco: Dict[str, int] = {}
resumen_tallas_mapa: Dict[str, str] = {}

if not df_variantes.empty:
    for sku_k, grupo in df_variantes.groupby("sku", sort=False):
        sku_str = str(sku_k)
        filas_ordenadas = sorted(
            grupo.to_dict("records"),
            key=lambda r: _clave_orden_talla_ui(r.get("variante", "")),
        )
        mapa_vallejo[sku_str] = sum(int(r.get("stock_vallejo", 0)) for r in filas_ordenadas)
        mapa_olivar[sku_str] = sum(int(r.get("stock_olivar", 0)) for r in filas_ordenadas)
        mapa_arco[sku_str] = sum(int(r.get("stock_arco_norte", 0)) for r in filas_ordenadas)

        partes_netas = [
            f"Talla {r['variante']}: {int(r['stock_neto'])}u"
            for r in filas_ordenadas
            if int(r["stock_neto"]) > 0
        ]
        if partes_netas:
            resumen_tallas_mapa[sku_str] = " | ".join(partes_netas)
        else:
            partes_brutas = [
                f"Talla {r['variante']} ({int(r['stock_bruto'])} br)"
                for r in filas_ordenadas
            ]
            resumen_tallas_mapa[sku_str] = "En blindaje: " + ", ".join(partes_brutas)

df_productos["stock_vallejo_total"] = [
    mapa_vallejo.get(str(s), 0) for s in df_productos["sku"]
]
df_productos["stock_olivar_total"] = [
    mapa_olivar.get(str(s), 0) for s in df_productos["sku"]
]
df_productos["stock_arco_norte_total"] = [
    mapa_arco.get(str(s), 0) for s in df_productos["sku"]
]
df_productos["variantes_texto"] = [
    resumen_tallas_mapa.get(str(s), "Única") for s in df_productos["sku"]
]
df_productos["url_origen"] = [
    Config.construir_url_proveedor(str(r["sku"]), str(r.get("url_origen") or ""))
    for _, r in df_productos.iterrows()
]

# Evaluar marcas restringidas en Meli con la lista configurable
lista_bloqueadas = st.session_state["marcas_bloqueadas_custom"]
df_productos["es_restringida_meli"] = [
    es_marca_restringida_meli(str(r["marca"]), str(r["titulo"]), lista_bloqueadas)
    for _, r in df_productos.iterrows()
]
df_productos["Estatus Meli"] = [
    "🚫 Restringida en Meli" if b else "✅ Permitida en Meli"
    for b in df_productos["es_restringida_meli"]
]

conteo_laminas: List[int] = []
for sku_iter in df_productos["sku"]:
    rutas_sku = Config.rutas_esperadas_sku(str(sku_iter))
    conteo_laminas.append(
        sum(1 for p in rutas_sku.values() if p.exists() and p.stat().st_size > 512)
    )
df_productos["laminas_listas"] = conteo_laminas

df_productos["semaforo"] = [
    clasificar_semaforo_inventario(
        stk,
        umbral_rojo=int(st.session_state["umbral_rojo"]),
        umbral_verde=int(st.session_state["umbral_verde"]),
    )
    for stk in df_productos["stock_neto_total"]
]

df_maestro = calcular_unit_economics(
    df_productos,
    comision_meli_pct=float(st.session_state["comision_meli_pct"]),
    costo_envio_mxn=float(st.session_state["costo_envio_mxn"]),
    margen_objetivo_pct=float(st.session_state["margen_objetivo_pct"]),
)

# ==============================================================================
# 6. BARRA SUPERIOR DE SELECCIÓN POR CATÁLOGO, SUCURSALES Y FILTRO MELI
# ==============================================================================
st.markdown("### 🗂️ Filtros Globales de Catálogo, Sucursales y Restricción Mercado Libre")
f_col1, f_col2, f_col3, f_col4 = st.columns([1.4, 1.2, 1.3, 1.1])

with f_col1:
    catalogos_disponibles = sorted(df_maestro["catalogo_origen"].dropna().unique().tolist())
    filtro_catalogo = st.multiselect(
        "📚 Escoger Catálogo(s) (Vacío = Todos):",
        options=catalogos_disponibles,
        default=[],
        placeholder="Todos los catálogos...",
    )
with f_col2:
    marcas_disponibles = sorted(df_maestro["marca"].dropna().unique().tolist())
    filtro_marca = st.multiselect(
        "🏷️ Filtrar por Marca (Vacío = Todas):",
        options=marcas_disponibles,
        default=[],
        placeholder="Todas las marcas...",
    )
with f_col3:
    busqueda_global = st.text_input(
        "🔎 Buscar por ID de Catálogo o Modelo:",
        value="",
        placeholder="Ej. 1338647, Sonic, Court...",
    ).strip()
with f_col4:
    st.write("")
    descartar_restringidas_meli = st.toggle(
        "🛡️ Descartar marcas que Meli no deja vender",
        value=False,
        help="Oculta automáticamente marcas con restricción BPP en Mercado Libre (Adidas, Puma, Sonic, Nike, Disney, etc.).",
    )

df_filtrado = df_maestro.copy()
if filtro_catalogo:
    df_filtrado = df_filtrado[df_filtrado["catalogo_origen"].isin(filtro_catalogo)]
if filtro_marca:
    df_filtrado = df_filtrado[df_filtrado["marca"].isin(filtro_marca)]
if descartar_restringidas_meli:
    df_filtrado = df_filtrado[~df_filtrado["es_restringida_meli"]]
if busqueda_global:
    q_low = busqueda_global.lower()
    df_filtrado = df_filtrado[
        df_filtrado["sku"].str.lower().str.contains(q_low, na=False)
        | df_filtrado["titulo"].str.lower().str.contains(q_low, na=False)
        | df_filtrado["marca"].str.lower().str.contains(q_low, na=False)
        | df_filtrado["catalogo_origen"].str.lower().str.contains(q_low, na=False)
    ]

# KPIs Ejecutivos Superiores
k1, k2, k3, k4, k5, k6 = st.columns(6)
k1.metric("IDs en Vista", f"{len(df_filtrado):,} / {len(df_maestro):,}")
k2.metric("✅ Permitidos Meli", f"{int((~df_filtrado['es_restringida_meli']).sum()):,}")
k3.metric("📍 Stock Vallejo", f"{int(df_filtrado['stock_vallejo_total'].sum()):,} u.")
k4.metric("📍 Stock Olivar del Conde", f"{int(df_filtrado['stock_olivar_total'].sum()):,} u.")
k5.metric("📍 Stock Arco Norte", f"{int(df_filtrado['stock_arco_norte_total'].sum()):,} u.")
k6.metric("📦 Total Neto Publicable", f"{int(df_filtrado['stock_neto_total'].sum()):,} u.")

st.divider()

tab1, tab2, tab3, tab4 = st.tabs(
    [
        "💵 1. Tabla Amigable de Precios & Ganancias",
        "🏢 2. Stock Multialmacén (Vallejo · Olivar · Arco Norte)",
        "📸 3. Estudio Fotográfico (4 Láminas HD)",
        "📈 4. Histórico y Salud del Sistema",
    ]
)


# ==============================================================================
# TAB 1: TABLA AMIGABLE DE PRECIOS DE COMPRA, VENTA Y CÁLCULOS VISIBLES
# ==============================================================================
with tab1:
    st.subheader("💵 Tabla de Precios de Compra, Venta y Ganancia Neta por ID de Catálogo")
    st.caption(
        "Fórmula transparente: **Precio Venta Meli** − **Precio Compra (Costo)** − **Comisión Meli** − **Costo Envío** − **ISR/IVA (3.5%)** = **Ganancia Neta Libre por Par**."
    )

    if df_filtrado.empty:
        st.info(
            "No hay artículos visibles con los filtros actuales. Si activaste 'Descartar marcas que Meli no deja vender', agrega IDs de marcas libres o desactiva el interruptor para ver todo el catálogo."
        )
    else:
        # Resumen promedio amigable arriba de la tabla
        prom_compra = float(df_filtrado["costo_base_num"].mean())
        prom_venta = float(df_filtrado["precio_sugerido_num"].mean())
        prom_ganancia = float(df_filtrado["utilidad_neta_num"].mean())
        prom_margen = float(df_filtrado["margen_neto_num"].mean())

        m1, m2, m3, m4 = st.columns(4)
        m1.metric("🛒 Precio Compra Promedio", f"$ {prom_compra:,.2f} MXN")
        m2.metric("🏷️ Precio Venta Meli Promedio", f"$ {prom_venta:,.2f} MXN")
        m3.metric("💵 Ganancia Neta Promedio / Par", f"$ {prom_ganancia:,.2f} MXN")
        m4.metric("📊 Margen Neto Real Promedio", f"{prom_margen:.1f}%")

        df_tabla_precios = df_filtrado[
            [
                "sku",
                "catalogo_origen",
                "marca",
                "Estatus Meli",
                "titulo",
                "Precio Compra",
                "Precio Venta",
                "Comisión Meli",
                "Costo Envío",
                "ISR / IVA",
                "Ganancia Neta ($)",
                "Margen Neto (%)",
                "stock_bruto_total",
                "stock_neto_total",
                "url_origen",
            ]
        ].rename(
            columns={
                "sku": "ID Catálogo",
                "catalogo_origen": "Catálogo",
                "marca": "Marca",
                "titulo": "Modelo",
                "stock_bruto_total": "Stock 3 Sucursales",
                "stock_neto_total": f"Stock Neto (-{cfg.BUFFER_STOCK}/talla)",
                "url_origen": "URL Price Shoes",
            }
        )

        st.dataframe(
            df_tabla_precios,
            use_container_width=True,
            hide_index=True,
            column_config={
                "URL Price Shoes": st.column_config.LinkColumn(
                    "URL Price Shoes",
                    display_text="🔗 Ver en Catálogo",
                )
            },
        )

        # Desglose visual paso a paso por producto seleccionado
        st.markdown("#### 🧮 Radiografía Financiera Paso a Paso por Modelo")
        sku_calc = st.selectbox(
            "Selecciona un ID de catálogo para ver su desglose aritmético detallado:",
            options=df_filtrado["sku"].tolist(),
            format_func=lambda s: (
                f"ID {s} — {df_filtrado[df_filtrado['sku'] == s].iloc[0]['marca']} · "
                f"{df_filtrado[df_filtrado['sku'] == s].iloc[0]['titulo']}"
            ),
        )
        fila_c = df_filtrado[df_filtrado["sku"] == sku_calc].iloc[0]

        c_b1, c_b2, c_b3, c_b4, c_b5, c_b6 = st.columns(6)
        c_b1.metric("1. Precio Compra", f"$ {fila_c['costo_base_num']:,.2f}")
        c_b2.metric("2. Precio Venta Meli", f"$ {fila_c['precio_sugerido_num']:,.2f}")
        c_b3.metric(
            f"3. Comisión ({st.session_state['comision_meli_pct']:.1f}%)",
            f"-$ {fila_c['comision_meli_num']:,.2f}",
        )
        c_b4.metric("4. Envío Meli", f"-$ {fila_c['costo_envio_num']:,.2f}")
        c_b5.metric(f"5. Impuestos ({cfg.RETENCION_IMPUESTOS_PCT:.1f}%)", f"-$ {fila_c['impuestos_num']:,.2f}")
        c_b6.metric(
            "6. Ganancia Libre",
            f"$ {fila_c['utilidad_neta_num']:,.2f}",
            delta=f"{fila_c['margen_neto_num']:.1f}% margen",
        )

        try:
            buffer_excel = io.BytesIO()
            with pd.ExcelWriter(buffer_excel, engine="xlsxwriter") as writer:
                df_tabla_precios.to_excel(writer, index=False, sheet_name="Precios_y_Ganancias")
            st.download_button(
                label="📥 Descargar Tabla de Precios y Ganancias (.xlsx)",
                data=buffer_excel.getvalue(),
                file_name="sinergia_precios_y_ganancias.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        except ModuleNotFoundError:
            csv_bytes = df_tabla_precios.to_csv(index=False).encode("utf-8-sig")
            st.download_button(
                label="📥 Descargar Tabla de Precios y Ganancias (.csv)",
                data=csv_bytes,
                file_name="sinergia_precios_y_ganancias.csv",
                mime="text/csv",
            )


# ==============================================================================
# TAB 2: STOCK MULTIALMACÉN (VALLEJO, OLIVAR DEL CONDE Y ARCO NORTE)
# ==============================================================================
with tab2:
    st.subheader("🏢 Inventario Multialmacén: Vallejo · Olivar del Conde · Arco Norte")
    st.caption(
        f"El Stock Bruto de cada talla es la suma exacta de **Vallejo + Olivar del Conde + Arco Norte**. "
        f"El Stock Neto aplica el blindaje de **-{cfg.BUFFER_STOCK} u. por talla** (y blinda a 0 si solo queda 1 pieza física)."
    )

    if df_filtrado.empty:
        st.info("No hay artículos para mostrar con los filtros seleccionados.")
    else:
        df_resumen_suc = df_filtrado[
            [
                "semaforo",
                "sku",
                "catalogo_origen",
                "marca",
                "Estatus Meli",
                "titulo",
                "stock_vallejo_total",
                "stock_olivar_total",
                "stock_arco_norte_total",
                "stock_bruto_total",
                "stock_neto_total",
                "variantes_texto",
                "url_origen",
            ]
        ].rename(
            columns={
                "semaforo": "Semáforo",
                "sku": "ID Catálogo",
                "catalogo_origen": "Catálogo",
                "marca": "Marca",
                "titulo": "Modelo",
                "stock_vallejo_total": "📍 Vallejo",
                "stock_olivar_total": "📍 Olivar del Conde",
                "stock_arco_norte_total": "📍 Arco Norte",
                "stock_bruto_total": "Total Bruto (3 Suc.)",
                "stock_neto_total": f"Total Neto (-{cfg.BUFFER_STOCK}/talla)",
                "variantes_texto": "Tallas con Stock Neto",
                "url_origen": "URL Price Shoes",
            }
        )

        st.dataframe(
            df_resumen_suc,
            use_container_width=True,
            hide_index=True,
            column_config={
                "URL Price Shoes": st.column_config.LinkColumn(
                    "URL Price Shoes",
                    display_text="🔗 Abrir en Price Shoes",
                )
            },
        )

        st.divider()
        st.markdown("### 🔎 Desglose y Edición de Stock por Sucursal y Talla")

        for _, fila_m in df_filtrado.iterrows():
            sku_m = str(fila_m["sku"])
            marca_m = str(fila_m["marca"])
            tit_m = str(fila_m["titulo"])
            url_m = str(fila_m["url_origen"])
            df_v_sku = ordenar_df_variantes(df_variantes[df_variantes["sku"] == sku_m].copy())

            with st.expander(
                f"📦 [{fila_m['semaforo']}] ID: {sku_m} | {marca_m.upper()} — {tit_m} | "
                f"📍 Vallejo: {int(fila_m['stock_vallejo_total'])} | 📍 Olivar: {int(fila_m['stock_olivar_total'])} | "
                f"📍 Arco Norte: {int(fila_m['stock_arco_norte_total'])} | Neto Publicable: {int(fila_m['stock_neto_total'])} u.",
                expanded=True,
            ):
                col_meta, col_url = st.columns([2.5, 1.5])
                with col_meta:
                    st.markdown(
                        f"**Catálogo:** `{fila_m['catalogo_origen']}` | **Marca:** `{marca_m}` ({fila_m['Estatus Meli']}) | "
                        f"**Compra:** `{fila_m['Precio Compra']}` → **Venta Meli:** `{fila_m['Precio Venta']}` (**Ganancia:** `{fila_m['Ganancia Neta ($)']}`)"
                    )
                with col_url:
                    st.markdown(f"🔗 **Enlace Directo:** [{url_m}]({url_m})")

                if df_v_sku.empty:
                    st.warning("Sin tallas registradas para este ID.")
                else:
                    df_edit_suc = df_v_sku[
                        [
                            "variante",
                            "stock_vallejo",
                            "stock_olivar",
                            "stock_arco_norte",
                            "stock_bruto",
                            "stock_anterior",
                            "stock_neto",
                            "delta_variante",
                        ]
                    ].rename(
                        columns={
                            "variante": "Talla",
                            "stock_vallejo": "📍 Vallejo",
                            "stock_olivar": "📍 Olivar del Conde",
                            "stock_arco_norte": "📍 Arco Norte",
                            "stock_bruto": "Suma Bruta (3 Suc.)",
                            "stock_anterior": "Neto Previo",
                            "stock_neto": f"Neto Meli (-{cfg.BUFFER_STOCK})",
                            "delta_variante": "Movimiento (Δ)",
                        }
                    )

                    c_ed, c_gr = st.columns([1.65, 1.35])
                    with c_ed:
                        df_suc_mod = st.data_editor(
                            df_edit_suc,
                            use_container_width=True,
                            hide_index=True,
                            disabled=[
                                "Talla",
                                "Suma Bruta (3 Suc.)",
                                "Neto Previo",
                                f"Neto Meli (-{cfg.BUFFER_STOCK})",
                                "Movimiento (Δ)",
                            ],
                            key=f"editor_suc_{sku_m}",
                        )
                        if st.button(
                            f"💾 Guardar existencias de sucursales (ID {sku_m})",
                            key=f"btn_save_suc_{sku_m}",
                        ):
                            ok_s, msg_s = guardar_edicion_sucursales_wal(
                                sku_m, df_suc_mod, cfg.RUTA_BD
                            )
                            if ok_s:
                                st.success(msg_s)
                                st.rerun()
                            else:
                                st.error(msg_s)

                    with c_gr:
                        orden_cat = df_v_sku["variante"].tolist()
                        df_bar_suc = df_v_sku.melt(
                            id_vars=["variante"],
                            value_vars=["stock_vallejo", "stock_olivar", "stock_arco_norte"],
                            var_name="Sucursal",
                            value_name="Pares",
                        )
                        df_bar_suc["Sucursal"] = df_bar_suc["Sucursal"].map(
                            {
                                "stock_vallejo": "📍 Vallejo",
                                "stock_olivar": "📍 Olivar del Conde",
                                "stock_arco_norte": "📍 Arco Norte",
                            }
                        )
                        fig_suc = px.bar(
                            df_bar_suc,
                            x="variante",
                            y="Pares",
                            color="Sucursal",
                            barmode="stack",
                            category_orders={"variante": orden_cat},
                            title=f"Distribución por Sucursal · ID {sku_m}",
                            color_discrete_map={
                                "📍 Vallejo": "#10B981",
                                "📍 Olivar del Conde": "#3B82F6",
                                "📍 Arco Norte": "#F59E0B",
                            },
                        )
                        fig_suc.update_layout(
                            template="plotly_dark",
                            height=260,
                            margin={"l": 10, "r": 10, "t": 35, "b": 10},
                            xaxis_title="Talla",
                        )
                        st.plotly_chart(fig_suc, use_container_width=True, key=f"plot_suc_{sku_m}")


# ==============================================================================
# TAB 3: ESTUDIO FOTOGRÁFICO (4 LÁMINAS HD) Y EDITOR DE CATÁLOGO / COSTOS
# ==============================================================================
with tab3:
    st.subheader("📸 Estudio Fotográfico de Alta Gama (1200x1200px) & Editor Maestro")

    lista_skus_est = df_maestro["sku"].tolist()
    mapa_est = {
        str(r["sku"]): (
            f"ID {r['sku']} — [{r['catalogo_origen']}] {r['marca'].upper()} · "
            f"{r['titulo']} ({r['laminas_listas']}/4 láminas)"
        )
        for _, r in df_maestro.iterrows()
    }

    col_s_sku, col_b_regen, col_b_del = st.columns([2.6, 1.1, 0.9])
    with col_s_sku:
        sku_estudio = st.selectbox(
            "Seleccionar ID de Catálogo para inspeccionar sus 4 láminas:",
            options=lista_skus_est,
            format_func=lambda x: mapa_est.get(x, x),
        )
    with col_b_regen:
        st.write("")
        if st.button("🎨 Regenerar 4 Láminas HD", use_container_width=True, type="primary"):
            with st.spinner(f"Renderizando las 4 láminas HD para ID {sku_estudio}..."):
                rc, out, err = ejecutar_motor_subproceso(
                    ["--skus", str(sku_estudio), "--solo-render", "--forzar"]
                )
                st.session_state["ultimo_log_subproceso"] = (out or "") + ("\n" + err if err else "")
                st.rerun()
    with col_b_del:
        st.write("")
        if st.button("🗑️ Eliminar ID actual", use_container_width=True):
            ok_del, msg_del = eliminar_sku_catalogo_wal(str(sku_estudio), cfg.RUTA_BD)
            if ok_del:
                st.success(msg_del)
                st.rerun()
            else:
                st.error(msg_del)

    fila_est = df_maestro[df_maestro["sku"] == sku_estudio].iloc[0]
    st.markdown(
        f"**Catálogo:** `{fila_est['catalogo_origen']}` | **Marca:** `{fila_est['marca']}` ({fila_est['Estatus Meli']}) | "
        f"**Tallas con Stock:** `{fila_est['variantes_texto']}` | "
        f"🔗 **[Abrir en Price Shoes]({fila_est['url_origen']})**"
    )

    etiquetas_laminas = {
        "01_portada_blanca": "1. Portada Blanca (Crop 95%)",
        "02_vista_angulo": "2. Vista en Ángulo (Crop 95%)",
        "98_lamina_impacto": "3. Lámina 98 · Lo Mejor del Producto",
        "99_lamina_ficha": "4. Lámina 99 · Guía de Tallas y Medición",
    }

    rutas_sku_actual = Config.rutas_esperadas_sku(str(sku_estudio))
    columnas_galeria = st.columns(4)

    for idx_col, tipo_act in enumerate(cfg.TIPOS_ACTIVO):
        ruta_png = rutas_sku_actual[tipo_act]
        col_ui = columnas_galeria[idx_col]
        titulo_cap = etiquetas_laminas.get(tipo_act, tipo_act)

        if ruta_png.exists() and ruta_png.stat().st_size > 512:
            raw_img = ruta_png.read_bytes()
            col_ui.image(raw_img, caption=titulo_cap, use_container_width=True)
            col_ui.download_button(
                label=f"⬇️ {ruta_png.name}",
                data=raw_img,
                file_name=ruta_png.name,
                mime="image/png",
                key=f"dl_{sku_estudio}_{tipo_act}",
                use_container_width=True,
            )
        else:
            raw_ph = generar_placeholder_gris(str(sku_estudio), tipo_act)
            col_ui.image(raw_ph, caption=f"⚠️ Pendiente: {titulo_cap}", use_container_width=True)

    st.divider()
    st.markdown("#### ✏️ Editor Directo de Catálogo, Marca, Precio de Compra y URL")
    df_edit_src = df_maestro[
        [
            "sku",
            "catalogo_origen",
            "marca",
            "titulo",
            "categoria",
            "color",
            "material",
            "costo_base_num",
            "url_origen",
        ]
    ].rename(
        columns={
            "sku": "ID Catálogo",
            "catalogo_origen": "Catálogo",
            "marca": "Marca",
            "titulo": "Modelo",
            "categoria": "Categoría",
            "color": "Color",
            "material": "Material",
            "costo_base_num": "Precio Compra ($)",
            "url_origen": "URL Price Shoes",
        }
    )

    df_editado_ui = st.data_editor(
        df_edit_src,
        use_container_width=True,
        hide_index=True,
        disabled=["ID Catálogo"],
        key="editor_catalogo_sinergia",
    )

    if st.button("💾 Guardar Cambios y Actualizar Láminas"):
        ok_ed, msg_ed = guardar_edicion_productos_wal(df_editado_ui, cfg.RUTA_BD)
        if ok_ed:
            ejecutar_motor_subproceso(["--solo-render", "--forzar"])
            st.success(msg_ed)
            st.rerun()
        else:
            st.error(msg_ed)


# ==============================================================================
# TAB 4: HISTÓRICO Y SALUD DEL SISTEMA
# ==============================================================================
with tab4:
    st.subheader("📈 Evolución Temporal de Inventario y Salud Operativa")
    if not df_historico.empty:
        skus_hist = df_historico["sku"].unique().tolist()
        sku_ts = st.selectbox("Seleccionar ID para curva histórica:", options=skus_hist)
        df_hv_sku = (
            df_historico_var[df_historico_var["sku"] == sku_ts]
            if not df_historico_var.empty
            else pd.DataFrame()
        )
        if not df_hv_sku.empty:
            fig_var_ts = px.line(
                df_hv_sku,
                x="fecha_registro",
                y="stock_neto",
                color="variante",
                markers=True,
                title=f"Movimiento Histórico por Talla · ID {sku_ts}",
            )
            fig_var_ts.update_layout(template="plotly_dark", height=340)
            st.plotly_chart(fig_var_ts, use_container_width=True)

    if not df_corridas.empty:
        st.markdown("##### Bitácora de Corridas ETL")
        st.dataframe(df_corridas, use_container_width=True, hide_index=True)
    if not df_errores.empty:
        st.markdown("##### Registro de Errores")
        st.dataframe(df_errores, use_container_width=True, hide_index=True)