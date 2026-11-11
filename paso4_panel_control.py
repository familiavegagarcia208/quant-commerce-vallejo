# -*- coding: utf-8 -*-
"""
========================================================================================
ZAPATERÍA VALLEJO - PANEL DE CONTROL QUANT-COMMERCE, TOP 100 MELI 24H & ESTUDIO HD
========================================================================================
- Conectado directamente a `cargar_estado_completo_bd` de `motor_etl_vallejo.py`
  (lee `zapatos_candidatos` [8,165], `historial_stock_21_dias` [2,745] e
  `historial_inventario` -> 931 SKUs auditados reales y Top 100 MeLi).
- Detecta automáticamente las láminas existentes en `data/catalogo_listo/` y
  `output/catalogo_listo/`.
========================================================================================
"""

from __future__ import annotations

import hashlib
import io
import json
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
from PIL import Image, ImageDraw, ImageFont

from motor_etl_vallejo import (
    Config,
    DBHandler,
    EstudioFotograficoSinergia,
    ExtractorAsyncSinergia,
    ResultadoExtraccion,
    VarianteSucursal,
    cargar_estado_completo_bd,
    clasificar_catalogo_origen,
    col_int,
    col_num,
    col_txt,
    configurar_loggers,
    es_marca_restringida_meli,
    evaluar_dataframe_quant,
    extraer_marca_real,
    generar_cola_sincronizacion_ml,
    sanitizar_sku,
    seleccionar_lote_doble_carril,
    validar_imagen_bytes,
)

# ==============================================================================
# 1. CONFIGURACIÓN DE PÁGINA Y ESTILOS
# ==============================================================================
st.set_page_config(
    page_title="Zapatería Vallejo · Laboratorio ETL, Lanzador MeLi & Estudio HD",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(
    """
    <style>
        .block-container { padding-top: 1.0rem; padding-bottom: 2rem; }
        div[data-testid="stMetric"] {
            background-color: #0F172A;
            border: 1px solid #1E293B;
            border-left: 5px solid #10B981;
            padding: 12px 14px;
            border-radius: 10px;
            color: #F8FAFC;
        }
        div[data-testid="stMetricLabel"] > div { color: #94A3B8 !important; font-weight: 600; }
        div[data-testid="stMetricValue"] > div { color: #F8FAFC !important; }
    </style>
    """,
    unsafe_allow_html=True,
)

CARPETAS_ESTUDIO: Tuple[Path, ...] = (
    Path("data/catalogo_listo"),
    Path("output/catalogo_listo"),
)

ETIQUETAS_LAMINAS: Dict[str, str] = {
    "01_portada_blanca": "1. Portada Blanca (Crop 95%)",
    "02_vista_angulo": "2. Vista en Ángulo / Galería",
    "98_lamina_impacto": "3. Lámina 98 · Impacto Comercial",
    "99_lamina_ficha": "4. Lámina 99 · Guía de Tallas y Ficha",
}

VARIANTES_ARCHIVO_LAMINA: Dict[str, Tuple[str, ...]] = {
    "01_portada_blanca": (
        "SINERGIA_{sku}_01_portada_blanca.png",
        "01_portada_blanca.png",
        "01_portada_blanca.jpg",
        "01.png",
        "01.jpg",
    ),
    "02_vista_angulo": (
        "SINERGIA_{sku}_02_vista_angulo.png",
        "02_vista_angulo.png",
        "02_vista_catalogo_1.png",
        "02_angulo_galeria.jpg",
        "02.png",
        "02.jpg",
    ),
    "98_lamina_impacto": (
        "SINERGIA_{sku}_98_lamina_impacto.png",
        "98_lamina_impacto.png",
        "98_lamina_collage_vistas.png",
        "03_ficha_beneficios.jpg",
        "03.png",
        "03.jpg",
    ),
    "99_lamina_ficha": (
        "SINERGIA_{sku}_99_lamina_ficha.png",
        "99_lamina_ficha.png",
        "99_lamina_ficha_oficial.png",
        "04_guia_tallas_cm.jpg",
        "04.png",
        "04.jpg",
    ),
}

defaults_sesion: Dict[str, Any] = {
    "meta_publicacion_meli": 100,
    "limite_baja_stock": Config.LIMITE_CRITICO_BAJA,
    "lecturas_gracia_ucb": Config.LECTURAS_GRACIA_UCB,
    "dias_max_estancamiento": Config.DIAS_MAX_ESTANCAMIENTO,
    "pendiente_decay_lambda": Config.PENDIENTE_DECAY_PELIGROSA,
    "factor_incertidumbre_ucb": Config.FACTOR_UCB_C,
    "media_bayesiana_min": Config.MEDIA_BAYESIANA_MIN,
    "comision_meli_pct": Config.COMISION_MELI_PCT,
    "costo_envio_mxn": Config.COSTO_ENVIO_MXN,
    "margen_objetivo_pct": Config.MARGEN_OBJETIVO_PCT,
    "buffer_seguridad": Config.BUFFER_STOCK,
    "usar_costo_estimado": False,
    "costo_estimado_default": 380.0,
    "marcas_bloqueadas_custom": list(Config.MARCAS_RESTRINGIDAS_MELI),
    "ultimo_log_cmd": "",
}
for k_def, v_def in defaults_sesion.items():
    if k_def not in st.session_state:
        st.session_state[k_def] = v_def


# ==============================================================================
# 2. LOCALIZADOR DE LÁMINAS EN `data/catalogo_listo` Y `output/catalogo_listo`
# ==============================================================================
def resolver_rutas_laminas_sku(sku: str) -> Dict[str, Optional[Path]]:
    s = sanitizar_sku(sku)
    encontradas: Dict[str, Optional[Path]] = {t: None for t in Config.TIPOS_ACTIVO}
    if not s:
        return encontradas

    for tipo, patrones in VARIANTES_ARCHIVO_LAMINA.items():
        for base_dir in CARPETAS_ESTUDIO:
            carpeta_sku = base_dir / s
            if not carpeta_sku.exists():
                continue
            for pat in patrones:
                cand = carpeta_sku / pat.format(sku=s)
                if cand.exists() and cand.stat().st_size > 512:
                    encontradas[tipo] = cand
                    break
            if encontradas[tipo] is not None:
                break
    return encontradas


def contar_laminas_listas_sku(sku: str) -> int:
    rutas = resolver_rutas_laminas_sku(sku)
    return sum(1 for r in rutas.values() if r is not None)


def listar_skus_con_laminas_en_disco() -> List[str]:
    skus: List[str] = []
    for base_dir in CARPETAS_ESTUDIO:
        if not base_dir.exists():
            continue
        for d in sorted(base_dir.iterdir()):
            if d.is_dir() and (any(d.glob("*.png")) or any(d.glob("*.jpg"))):
                s_clean = sanitizar_sku(d.name)
                if s_clean and s_clean not in skus:
                    skus.append(s_clean)
    return skus


def generar_placeholder_estudio(sku: str, tipo_activo: str, modelo: str = "") -> bytes:
    img = Image.new("RGB", (800, 800), (15, 23, 42))
    dr = ImageDraw.Draw(img)
    dr.rounded_rectangle(
        [(32, 32), (768, 768)],
        radius=28,
        fill=(30, 41, 59),
        outline=(16, 185, 129),
        width=3,
    )
    f_b = ImageFont.load_default()
    dr.text(
        (75, 290),
        "ESTUDIO FOTOGRÁFICO HD (1200x1200px)",
        fill=(16, 185, 129),
        font=f_b,
    )
    dr.text((75, 340), f"ID Catálogo: {sku}", fill=(248, 250, 252), font=f_b)
    if modelo:
        dr.text((75, 375), f"Modelo: {modelo[:48]}", fill=(203, 213, 225), font=f_b)
    dr.text(
        (75, 420),
        f"Lámina: {ETIQUETAS_LAMINAS.get(tipo_activo, tipo_activo)}",
        fill=(245, 158, 11),
        font=f_b,
    )
    dr.text(
        (75, 475),
        "Presiona 'Renderizar 4 Láminas HD' para generarla.",
        fill=(148, 163, 184),
        font=f_b,
    )
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def empaquetar_zip_4_laminas(sku: str) -> Optional[bytes]:
    rutas = resolver_rutas_laminas_sku(sku)
    existentes = [r for r in rutas.values() if r is not None and r.exists()]
    if not existentes:
        return None
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for r in existentes:
            zf.write(r, arcname=r.name)
    return buf.getvalue()


def renderizar_4_laminas_en_vivo(
    sku: str,
    titulo: str,
    marca: str,
    categoria: str,
    color: str,
    material: str,
    costo_base: float,
    catalogo_origen: str,
    stock_actual: int,
    df_variantes_sku: pd.DataFrame,
) -> Tuple[bool, str]:
    sku_limpio = sanitizar_sku(sku)
    if not sku_limpio:
        return False, "ID inválido."

    log_pub, log_int = configurar_loggers()
    estudio = EstudioFotograficoSinergia(log_pub, log_int)

    imgs: List[Tuple[str, bytes]] = []
    # 1. Buscar si ya tiene portada/ángulo en `data/catalogo_listo/{sku}` o en caché
    rutas_existentes = resolver_rutas_laminas_sku(sku_limpio)
    for k_base in ("01_portada_blanca", "02_vista_angulo"):
        r_ex = rutas_existentes.get(k_base)
        if r_ex is not None and r_ex.exists():
            raw_b = r_ex.read_bytes()
            if validar_imagen_bytes(raw_b):
                imgs.append((hashlib.sha256(raw_b).hexdigest(), raw_b))

    for r_raw in Config.rutas_cache_raw_sku(sku_limpio):
        if len(imgs) >= 2:
            break
        if r_raw.exists() and r_raw.stat().st_size > 1024:
            raw_b = r_raw.read_bytes()
            if validar_imagen_bytes(raw_b):
                imgs.append((hashlib.sha256(raw_b).hexdigest(), raw_b))

    if len(imgs) == 1:
        imgs = [imgs[0], imgs[0]]
    elif len(imgs) < 1:
        imgs = ExtractorAsyncSinergia._generar_siluetas_estudio_hd(
            sku_limpio, categoria or "Calzado"
        )

    lista_multi: List[VarianteSucursal] = []
    if not df_variantes_sku.empty and "variante" in df_variantes_sku.columns:
        for _, rv in df_variantes_sku.iterrows():
            t_nom = str(rv.get("variante") or "25")
            sv = max(
                0, int(pd.to_numeric(rv.get("stock_vallejo", 0), errors="coerce") or 0)
            )
            so = max(
                0, int(pd.to_numeric(rv.get("stock_olivar", 0), errors="coerce") or 0)
            )
            sa = max(
                0,
                int(pd.to_numeric(rv.get("stock_arco_norte", 0), errors="coerce") or 0),
            )
            lista_multi.append(VarianteSucursal(t_nom, sv, so, sa))

    if not lista_multi:
        stk_base = max(12, int(stock_actual or 12))
        c_part = max(3, stk_base // 4)
        tallas_def = (
            ("18", "19", "20", "21")
            if "ESCOLAR" in titulo.upper()
            else ("24", "25", "26", "27")
        )
        for t_d in tallas_def:
            lista_multi.append(VarianteSucursal(t_d, c_part, 0, 0))

    res = ResultadoExtraccion(
        sku=sku_limpio,
        titulo=titulo or f"Modelo {sku_limpio}",
        marca=marca or "Original",
        categoria=categoria or "Calzado",
        color=color or "Tono Original",
        material=material or "Corte Sintético de Alta Resistencia",
        costo_base=float(costo_base if costo_base > 0 else 390.0),
        catalogo_origen=catalogo_origen or "Catálogo Urbano y Casual",
        variantes_multisucursal=lista_multi,
        variantes_brutas={vs.variante: vs.stock_bruto_total for vs in lista_multi},
        variantes_netas={vs.variante: vs.stock_neto for vs in lista_multi},
        stock_bruto_total=sum(vs.stock_bruto_total for vs in lista_multi),
        stock_neto_total=sum(vs.stock_neto for vs in lista_multi),
        imagenes_bytes=imgs,
        fuente_extraccion="ESTUDIO_HD_DIRECTO",
        url_final_secreta=f"https://www.priceshoes.com/buscar?q={sku_limpio}",
    )

    with DBHandler() as db:
        ok = estudio.procesar_estudio_sku(db, "ESTUDIO_WEB_HD", res, dry_run=False)

    if ok:
        return True, f"4 láminas HD (1200x1200px) generadas para ID {sku_limpio}."
    return False, f"Error al renderizar ID {sku_limpio}."


# ==============================================================================
# 3. BARRA LATERAL: CALIBRADOR DE UMBRALES Y CONFIGURACIÓN
# ==============================================================================
with st.sidebar:
    st.markdown("### 🎛️ Calibrador de Umbrales")
    st.caption(
        "Ajusta estos parámetros para validar en vivo qué productos saldrían por riesgo:"
    )

    st.session_state["limite_baja_stock"] = st.slider(
        "Límite de Stock para Baja Automática (unidades)",
        min_value=0,
        max_value=15,
        value=int(st.session_state["limite_baja_stock"]),
        step=1,
    )
    st.session_state["lecturas_gracia_ucb"] = st.slider(
        "Lecturas de Gracia UCB (Productos Nuevos)",
        min_value=1,
        max_value=15,
        value=int(st.session_state["lecturas_gracia_ucb"]),
        step=1,
    )
    st.session_state["dias_max_estancamiento"] = st.slider(
        "Días Máximos sin Movimiento (Estancamiento)",
        min_value=1.0,
        max_value=30.0,
        value=float(st.session_state["dias_max_estancamiento"]),
        step=0.5,
    )
    st.session_state["pendiente_decay_lambda"] = st.slider(
        "Pendiente de Caída Peligrosa λ (pares/día)",
        min_value=0.5,
        max_value=15.0,
        value=float(st.session_state["pendiente_decay_lambda"]),
        step=0.5,
    )
    st.session_state["factor_incertidumbre_ucb"] = st.slider(
        "Factor de Incertidumbre UCB (c)",
        min_value=0.10,
        max_value=3.00,
        value=float(st.session_state["factor_incertidumbre_ucb"]),
        step=0.05,
    )
    st.session_state["media_bayesiana_min"] = st.slider(
        "Media Bayesiana Mínima de Supervivencia",
        min_value=0.05,
        max_value=0.95,
        value=float(st.session_state["media_bayesiana_min"]),
        step=0.05,
    )

    st.divider()
    if st.button("🔄 Recargar Base de Datos del Disco", use_container_width=True):
        st.session_state.pop("estado_bd_vallejo", None)
        st.rerun()

    st.divider()
    st.markdown("### 💵 Parámetros de Precio (MeLi)")
    st.session_state["comision_meli_pct"] = st.slider(
        "Comisión MeLi (%):",
        0.0,
        25.0,
        float(st.session_state["comision_meli_pct"]),
        0.5,
    )
    st.session_state["costo_envio_mxn"] = st.number_input(
        "Costo Envío ($ MXN):",
        0.0,
        400.0,
        float(st.session_state["costo_envio_mxn"]),
        5.0,
    )
    st.session_state["margen_objetivo_pct"] = st.slider(
        "Margen Neto Objetivo (%):",
        10.0,
        45.0,
        float(st.session_state["margen_objetivo_pct"]),
        1.0,
    )
    st.session_state["usar_costo_estimado"] = st.checkbox(
        "Simular costo base cuando la BD no tenga precio extraído",
        value=bool(st.session_state["usar_costo_estimado"]),
    )
    if st.session_state["usar_costo_estimado"]:
        st.session_state["costo_estimado_default"] = st.number_input(
            "Costo estimado ($):",
            100.0,
            2000.0,
            float(st.session_state["costo_estimado_default"]),
            10.0,
        )


# ==============================================================================
# 4. CARGA EN MEMORIA (`st.session_state`) USANDO EL LECTOR REAL DE `zapateria_vallejo.db`
# ==============================================================================
if "estado_bd_vallejo" not in st.session_state:
    with st.spinner("Cargando `data/zapateria_vallejo.db` en memoria por única vez..."):
        st.session_state["estado_bd_vallejo"] = cargar_estado_completo_bd(Config.RUTA_BD)

estado_bd = st.session_state["estado_bd_vallejo"]
df_maestro = estado_bd["df_maestro"].copy()
df_bitacora = estado_bd["df_bitacora"]
df_variantes = estado_bd["df_variantes"]

total_aud_bd = int((col_int(df_maestro, "auditado_etl", 0) >= 1).sum())

st.title("📊 Zapatería Vallejo · Laboratorio de Análisis ETL, Lanzador MeLi & Estudio HD")
st.caption(
    f"Arquitectura de Serie de Tiempo (Time-Series), Modelo Probabilístico Bayesiano + UCB y Estudio Fotográfico 1200x1200px  |  "
    f"📂 Catálogo: `zapateria_vallejo.db:zapatos_candidatos ({len(df_maestro):,} SKUs)` · "
    f"🔍 Auditoría ETL: `historial_stock_21_dias + historial_inventario ({total_aud_bd:,} SKUs auditados)`"
)

df_maestro["id_ps"] = col_txt(df_maestro, "id_ps", "").str.strip()
df_maestro["nombre_modelo"] = col_txt(df_maestro, "nombre_modelo", "Modelo Catálogo")
df_maestro["marca"] = [
    extraer_marca_real(str(r.get("nombre_modelo", "")), str(r.get("marca", "")))
    for _, r in df_maestro.iterrows()
]
df_maestro["catalogo_origen"] = [
    clasificar_catalogo_origen(
        str(r.get("nombre_modelo", "")),
        str(r.get("marca", "")),
        str(r.get("catalogo_origen", "")),
    )
    for _, r in df_maestro.iterrows()
]
df_maestro["es_restringida_meli"] = [
    es_marca_restringida_meli(
        str(r.get("marca", "")),
        str(r.get("nombre_modelo", "")),
        st.session_state["marcas_bloqueadas_custom"],
    )
    for _, r in df_maestro.iterrows()
]
df_maestro["¿Permitida en MeLi?"] = np.where(
    df_maestro["es_restringida_meli"],
    "🚫 Restringida en MeLi (Otra Plataforma)",
    "✅ Permitida en MeLi",
)

df_maestro["stock_vallejo"] = col_int(
    df_maestro,
    "stock_vallejo" if "stock_vallejo" in df_maestro.columns else "stock_actual",
    0,
)
df_maestro["stock_olivar"] = col_int(df_maestro, "stock_olivar", 0)
df_maestro["stock_arco_norte"] = col_int(df_maestro, "stock_arco_norte", 0)

c_f1, c_f2, c_f3, c_f4 = st.columns([1.45, 1.25, 1.30, 1.0])
with c_f1:
    modo_canal = st.radio(
        "1️⃣ Canal de Venta:",
        options=[
            "🌐 Catálogo Completo (Todas las marcas / Otra Plataforma)",
            "🤝 Solo Mercado Libre (Descartar marcas restringidas)",
        ],
        index=0,
    )
with c_f2:
    cats_disp = sorted(df_maestro["catalogo_origen"].dropna().unique().tolist())
    filtro_catalogo = st.multiselect(
        "2️⃣ Escoger por Catálogo:",
        options=cats_disp,
        default=[],
        placeholder="Todos los catálogos...",
    )
with c_f3:
    st.markdown("3️⃣ **Sucursales a considerar en Stock:**")
    cs1, cs2, cs3 = st.columns(3)
    inc_val = cs1.checkbox("Vallejo", value=True)
    inc_oli = cs2.checkbox("Olivar", value=True)
    inc_arc = cs3.checkbox("Arco N.", value=True)
with c_f4:
    busqueda_txt = st.text_input(
        "4️⃣ Buscar ID o Modelo:", value="", placeholder="Ej. 1262647, Mocasín..."
    ).strip()

suma_suc = (
    (df_maestro["stock_vallejo"] if inc_val else 0)
    + (df_maestro["stock_olivar"] if inc_oli else 0)
    + (df_maestro["stock_arco_norte"] if inc_arc else 0)
)
df_maestro["stock_actual"] = np.where(
    (
        df_maestro["stock_vallejo"]
        + df_maestro["stock_olivar"]
        + df_maestro["stock_arco_norte"]
    )
    > 0,
    suma_suc,
    col_int(df_maestro, "stock_actual", 0) if inc_val else 0,
).astype(int)

# Evaluar el modelo matemático (Score UCB + Autonomía 24h + Reserva Poisson)
df_maestro = evaluar_dataframe_quant(
    df=df_maestro,
    lim_baja=int(st.session_state["limite_baja_stock"]),
    gracia_ucb=int(st.session_state["lecturas_gracia_ucb"]),
    lim_dias=float(st.session_state["dias_max_estancamiento"]),
    lim_lambda=float(st.session_state["pendiente_decay_lambda"]),
    c_ucb=float(st.session_state["factor_incertidumbre_ucb"]),
    lim_bayes=float(st.session_state["media_bayesiana_min"]),
)

# Precios reales (usando costo_socio / costo_bodega / precio_ml de `zapatos_candidatos`)
costo_real_s = col_num(df_maestro, "costo_base", 0.0)
precio_ml_db_s = col_num(df_maestro, "precio_ml_db", 0.0)
util_ml_db_s = col_num(df_maestro, "utilidad_ml_db", 0.0)

if st.session_state["usar_costo_estimado"]:
    costo_efectivo = np.where(
        costo_real_s > 0,
        costo_real_s,
        float(st.session_state["costo_estimado_default"]),
    )
else:
    costo_efectivo = costo_real_s.to_numpy()

c_dec = float(st.session_state["comision_meli_pct"]) / 100.0
m_dec = float(st.session_state["margen_objetivo_pct"]) / 100.0
i_dec = 0.035
env_val = float(st.session_state["costo_envio_mxn"])
den = max(0.12, 1.0 - c_dec - m_dec - i_dec)

p_bruto = np.where(costo_efectivo > 0, (costo_efectivo + env_val) / den, 0.0)
p_ceil9 = np.where(costo_efectivo > 0, np.ceil(p_bruto / 10.0) * 10.0 - 1.0, 0.0)
p_calc = np.where(
    (costo_efectivo > 0) & (p_ceil9 < p_bruto), p_ceil9 + 10.0, p_ceil9
)
p_venta = np.where(p_calc > 0, p_calc, precio_ml_db_s.to_numpy())
gastos_v = np.where(p_venta > 0, np.round(p_venta * (c_dec + i_dec) + env_val, 2), 0.0)
ganancia_calc = np.where(
    (p_venta > 0) & (costo_efectivo > 0),
    np.round(p_venta - costo_efectivo - gastos_v, 2),
    util_ml_db_s.to_numpy(),
)

df_maestro["costo_num"] = np.round(costo_efectivo, 2)
df_maestro["precio_venta_num"] = np.round(p_venta, 2)
df_maestro["ganancia_num"] = np.round(ganancia_calc, 2)
df_maestro["Precio Compra"] = [
    f"$ {v:,.2f}" if v > 0 else "Sin precio ($0)" for v in df_maestro["costo_num"]
]
df_maestro["Precio Venta Sugerido"] = [
    f"$ {v:,.2f}" if v > 0 else "Pendiente costo"
    for v in df_maestro["precio_venta_num"]
]
df_maestro["Ganancia Libre ($)"] = [
    f"$ {v:,.2f}" if p > 0 else "—"
    for v, p in zip(df_maestro["ganancia_num"], df_maestro["precio_venta_num"])
]

df_vista = df_maestro.copy()
if modo_canal.startswith("🤝"):
    df_vista = df_vista[~df_vista["es_restringida_meli"]]
if filtro_catalogo:
    df_vista = df_vista[df_vista["catalogo_origen"].isin(filtro_catalogo)]
if busqueda_txt:
    q = busqueda_txt.lower()
    df_vista = df_vista[
        df_vista["id_ps"].str.lower().str.contains(q, na=False)
        | df_vista["nombre_modelo"].str.lower().str.contains(q, na=False)
        | df_vista["marca"].str.lower().str.contains(q, na=False)
    ]

df_auditados = df_vista[df_vista["auditado_etl"] >= 1].copy()

meta_n = int(st.session_state["meta_publicacion_meli"])
df_listos_meli_reales = df_maestro[
    (df_maestro["auditado_etl"] >= 1)
    & (~df_maestro["es_restringida_meli"])
    & (df_maestro["accion_meli"] == "PUBLICAR_ACTIVO")
    & (df_maestro["horas_autonomia"] > Config.HORAS_MIN_AUTONOMIA_MELI)
    & (df_maestro["stock_publicable_meli"] > 0)
].sort_values(
    by=[
        "score_prioridad_meli",
        "tasa_decaimiento",
        "score_ucb_simulado",
        "stock_publicable_meli",
    ],
    ascending=[False, False, False, False],
)

df_top100_meli = df_listos_meli_reales.head(meta_n).copy()


# ==============================================================================
# 5. KPIS DEL LABORATORIO Y DE AUTONOMÍA 24H
# ==============================================================================
k1, k2, k3, k4, k5, k6 = st.columns(6)
k1.metric("Catálogo Total", f"{len(df_vista):,}")
k2.metric("SKUs Auditados (ETL)", f"{len(df_auditados):,}")
k3.metric(
    "En Exploración UCB",
    f"{int((df_auditados['estatus_simulado'] == 'EXPLORACION_UCB').sum()):,}",
)
k4.metric(
    "Activos Sanos",
    f"{int((df_auditados['estatus_simulado'] == 'ACTIVO_SANO').sum()):,}",
)
k5.metric(
    "Alerta Decay (λ)",
    f"{int((df_auditados['estatus_simulado'] == 'ALERTA_DECAY').sum()):,}",
)
k6.metric(
    "Candidatos a Baja",
    f"{int((df_auditados['estatus_simulado'] == 'BAJA_AUTOMATICA').sum()):,}",
)

s1, s2, s3, s4, s5, s6 = st.columns(6)
s1.metric(
    "🚀 Con Stock Real p/ MeLi",
    f"{len(df_listos_meli_reales):,} SKUs",
    delta=f"Top {len(df_top100_meli)}/{meta_n} listos",
)
s2.metric(
    "📍 Stock Real Vallejo / Gral.",
    f"{int(df_auditados['stock_vallejo'].sum()):,} u.",
)
s3.metric(
    "📍 Stock Real Olivar",
    f"{int(df_auditados['stock_olivar'].sum()):,} u.",
)
s4.metric(
    "📍 Stock Real Arco Norte",
    f"{int(df_auditados['stock_arco_norte'].sum()):,} u.",
)
s5.metric(
    "📦 Stock Total Auditado",
    f"{int(df_auditados['stock_actual'].sum()):,} u.",
)
s6.metric(
    "✅ Publicables (Tras Reserva)",
    f"{int(df_auditados['stock_publicable_meli'].sum()):,} u.",
)

st.divider()


# ==============================================================================
# 6. LAS 7 PESTAÑAS COMPLETAS DEL ECOSISTEMA
# ==============================================================================
tab1, tab2, tab3, tab4, tab5, tab6, tab7 = st.tabs(
    [
        "🎯 1. Matriz de Riesgo y Simulación de Umbrales",
        f"🚀 2. Lanzador Real Top {meta_n} MeLi & Más Vendidos",
        "📸 3. Estudio Fotográfico HD (4 Láminas 1200x1200px)",
        "📊 4. Gráficos Analíticos (Bayes, UCB, Rotación λ)",
        "📉 5. Curva de Decaimiento por SKU (Time-Series)",
        "💵 6. Tabla de Precios, Sucursales y Simulador",
        "⏱️ 7. Bitácora Histórica (Timestamps) y Planificador",
    ]
)


# ==============================================================================
# TAB 1: MATRIZ DE RIESGO Y SIMULACIÓN DE UMBRALES
# ==============================================================================
with tab1:
    st.subheader("Resultados del Lote Auditado bajo los Umbrales Seleccionados")
    c_r1, c_r2 = st.columns([2.2, 1.2])
    with c_r1:
        opciones_r = [
            "BAJA_AUTOMATICA",
            "EXPLORACION_UCB",
            "ACTIVO_SANO",
            "ALERTA_DECAY",
        ]
        filtro_r = st.multiselect(
            "Filtrar por Clasificación de Riesgo:",
            options=opciones_r,
            default=opciones_r,
        )
    with c_r2:
        orden_col = st.selectbox(
            "Ordenar tabla por:",
            options=[
                "stock_actual",
                "score_ucb_simulado",
                "horas_autonomia",
                "tasa_decaimiento",
                "delta_stock",
                "media_bayesiana",
            ],
            index=0,
        )

    df_t1 = df_auditados[df_auditados["estatus_simulado"].isin(filtro_r)].sort_values(
        by=orden_col, ascending=False
    )
    st.dataframe(
        df_t1[
            [
                "id_ps",
                "nombre_modelo",
                "marca",
                "catalogo_origen",
                "¿Permitida en MeLi?",
                "stock_actual",
                "reserva_dinamica_24h",
                "stock_publicable_meli",
                "horas_autonomia",
                "delta_stock",
                "tasa_decaimiento",
                "media_bayesiana",
                "score_ucb_simulado",
                "num_lecturas",
                "estatus_simulado",
                "motivo_simulado",
            ]
        ],
        use_container_width=True,
        hide_index=True,
        height=470,
    )


# ==============================================================================
# TAB 2: LANZADOR REAL TOP 100 MELI & MÁS VENDIDOS
# ==============================================================================
with tab2:
    st.subheader(
        f"🚀 Lote Top {len(df_top100_meli)} Mercado Libre con Garantía Matemática Anti-Quiebre a 24 Horas"
    )
    st.caption(
        f"De los {len(df_listos_meli_reales):,} SKUs con stock físico real y marca permitida, estos son los {len(df_top100_meli)} mejores priorizados para publicación automática."
    )

    col_b1, col_b2 = st.columns(2)
    with col_b1:
        if st.button(
            "⚡ Actualizar `data/cola_sincronizacion_ml.json` Ahora",
            use_container_width=True,
            type="primary",
        ):
            c_a, c_b = seleccionar_lote_doble_carril(df_maestro)
            cola_out = generar_cola_sincronizacion_ml(
                df_maestro, c_a, c_b, Config.RUTA_COLA_MELI
            )
            st.success(
                f"Cola sincronizada en `{Config.RUTA_COLA_MELI}`: "
                f"{cola_out['metadata']['total_top_100_meli']} en Top 100 MeLi y "
                f"{cola_out['metadata']['total_cola_otras_plataformas']} para Otras Plataformas."
            )
    with col_b2:
        st.download_button(
            label=f"📥 Descargar Lote Top {len(df_top100_meli)} MeLi Blindado 24h (.csv)",
            data=df_top100_meli.to_csv(index=False).encode("utf-8-sig"),
            file_name="top_100_meli_blindado_24h.csv",
            mime="text/csv",
            use_container_width=True,
        )

    st.dataframe(
        df_top100_meli[
            [
                "id_ps",
                "score_prioridad_meli",
                "catalogo_origen",
                "marca",
                "nombre_modelo",
                "stock_actual",
                "reserva_dinamica_24h",
                "stock_publicable_meli",
                "horas_autonomia",
                "tasa_decaimiento",
                "score_ucb_simulado",
                "Precio Compra",
                "Precio Venta Sugerido",
                "Ganancia Libre ($)",
            ]
        ],
        use_container_width=True,
        hide_index=True,
        height=390,
    )

    st.divider()
    st.subheader(
        "🌐 Cola Gradual para Otras Plataformas (Incluye Marcas Restringidas en MeLi con Stock Sano)"
    )
    df_otras = df_maestro[
        (df_maestro["auditado_etl"] >= 1)
        & (~df_maestro["id_ps"].isin(df_top100_meli["id_ps"]))
        & (df_maestro["stock_actual"] > int(st.session_state["limite_baja_stock"]))
    ].sort_values(
        by=["tasa_decaimiento", "score_ucb_simulado", "stock_actual"],
        ascending=[False, False, False],
    )

    st.dataframe(
        df_otras[
            [
                "id_ps",
                "catalogo_origen",
                "marca",
                "¿Permitida en MeLi?",
                "nombre_modelo",
                "stock_actual",
                "tasa_decaimiento",
                "score_ucb_simulado",
                "estatus_simulado",
            ]
        ],
        use_container_width=True,
        hide_index=True,
    )


# ==============================================================================
# TAB 3: ESTUDIO FOTOGRÁFICO HD (4 LÁMINAS 1200x1200px)
# ==============================================================================
with tab3:
    st.subheader("📸 Estudio Fotográfico de Alta Gama (4 Láminas 1200x1200px)")

    skus_con_carpeta = listar_skus_con_laminas_en_disco()
    candidatos_estudio = (
        skus_con_carpeta
        + df_top100_meli["id_ps"].tolist()
        + df_auditados.sort_values(by="stock_actual", ascending=False)["id_ps"]
        .head(300)
        .tolist()
        + df_vista["id_ps"].head(200).tolist()
    )
    opciones_estudio = [s for s in dict.fromkeys(candidatos_estudio) if s]

    mapa_filas_est = (
        df_maestro[df_maestro["id_ps"].isin(opciones_estudio)]
        .drop_duplicates("id_ps")
        .set_index("id_ps")
        .to_dict("index")
    )

    def _fmt_est(sku_k: str) -> str:
        info = mapa_filas_est.get(sku_k, {})
        nom = str(info.get("nombre_modelo") or f"Modelo {sku_k}")
        mar = str(info.get("marca") or "Original")
        stk = int(info.get("stock_actual") or 0)
        n_lam = contar_laminas_listas_sku(sku_k)
        ic = "✅" if n_lam == 4 else ("🟡" if n_lam > 0 else "⚪")
        return (
            f"{ic} ID {sku_k} — {mar.upper()} · {nom} (Stock: {stk} u. | {n_lam}/4 láminas)"
        )

    col_sel_est, col_btn_fast, col_btn_zip = st.columns([2.4, 1.2, 1.2])
    with col_sel_est:
        sku_estudio = st.selectbox(
            "Seleccionar SKU para inspeccionar o renderizar sus 4 láminas:",
            options=opciones_estudio,
            format_func=_fmt_est,
        )

    info_sku_est = mapa_filas_est.get(str(sku_estudio), {})
    nom_est = str(info_sku_est.get("nombre_modelo") or f"Modelo {sku_estudio}")
    mar_est = str(info_sku_est.get("marca") or "Original")
    cat_est = str(info_sku_est.get("categoria") or "Calzado")
    col_est = str(info_sku_est.get("color") or "Tono Original")
    mat_est = str(
        info_sku_est.get("material") or "Corte Sintético de Alta Resistencia"
    )
    costo_est = float(info_sku_est.get("costo_num") or 390.0)
    cat_orig_est = str(
        info_sku_est.get("catalogo_origen") or "Catálogo Urbano y Casual"
    )
    stk_est = int(info_sku_est.get("stock_actual") or 12)

    df_var_sku_est = (
        df_variantes[
            col_txt(df_variantes, "sku", "").str.strip() == str(sku_estudio)
        ].copy()
        if not df_variantes.empty
        else pd.DataFrame()
    )

    with col_btn_fast:
        st.write("")
        if st.button(
            "⚡ Renderizar 4 Láminas HD (1 seg)",
            use_container_width=True,
            type="primary",
        ):
            ok_r, msg_r = renderizar_4_laminas_en_vivo(
                sku=str(sku_estudio),
                titulo=nom_est,
                marca=mar_est,
                categoria=cat_est,
                color=col_est,
                material=mat_est,
                costo_base=costo_est,
                catalogo_origen=cat_orig_est,
                stock_actual=stk_est,
                df_variantes_sku=df_var_sku_est,
            )
            if ok_r:
                st.success(msg_r)
                st.rerun()
            else:
                st.error(msg_r)

    with col_btn_zip:
        st.write("")
        zip_bytes = empaquetar_zip_4_laminas(str(sku_estudio))
        if zip_bytes:
            st.download_button(
                label=f"📦 Descargar ZIP 4 Láminas ({sku_estudio})",
                data=zip_bytes,
                file_name=f"SINERGIA_{sku_estudio}_4_laminas_HD.zip",
                mime="application/zip",
                use_container_width=True,
            )

    rutas_sku_actual = resolver_rutas_laminas_sku(str(sku_estudio))
    columnas_galeria = st.columns(4)

    for idx_col, tipo_act in enumerate(Config.TIPOS_ACTIVO):
        ruta_img = rutas_sku_actual.get(tipo_act)
        col_ui = columnas_galeria[idx_col]
        titulo_cap = ETIQUETAS_LAMINAS.get(tipo_act, tipo_act)

        if ruta_img is not None and ruta_img.exists():
            raw_img = ruta_img.read_bytes()
            col_ui.image(raw_img, caption=titulo_cap, use_container_width=True)
            col_ui.download_button(
                label=f"⬇️ {ruta_img.name}",
                data=raw_img,
                file_name=ruta_img.name,
                mime="image/png",
                key=f"dl_est_{sku_estudio}_{tipo_act}",
                use_container_width=True,
            )
        else:
            raw_ph = generar_placeholder_estudio(str(sku_estudio), tipo_act, nom_est)
            col_ui.image(
                raw_ph, caption=f"⚠️ Pendiente: {titulo_cap}", use_container_width=True
            )


# ==============================================================================
# TAB 4: GRÁFICOS ANALÍTICOS (EMBUDO, RIESGO, MAPA UCB Y CATÁLOGOS)
# ==============================================================================
with tab4:
    st.subheader("📊 Gráficos Analíticos del Lote Auditado y Blindaje 24h")
    g1, g2 = st.columns(2)
    with g1:
        fig_fun = go.Figure(
            go.Funnel(
                y=[
                    "1. Catálogo Maestro Total",
                    "2. SKUs Auditados (ETL)",
                    "3. ConStock > Reserva y Autonomía > 24h",
                    "4. Elegibles MeLi (Marca Permitida)",
                ],
                x=[
                    len(df_maestro),
                    int((df_maestro["auditado_etl"] >= 1).sum()),
                    int(
                        (
                            (df_maestro["auditado_etl"] >= 1)
                            & (df_maestro["accion_meli"] == "PUBLICAR_ACTIVO")
                        ).sum()
                    ),
                    len(df_listos_meli_reales),
                ],
                textinfo="value+percent initial",
                marker={"color": ["#334155", "#3B82F6", "#10B981", "#F59E0B"]},
            )
        )
        fig_fun.update_layout(
            title="Embudo Real de Conversión hacia Top 100 MeLi",
            template="plotly_dark",
            height=380,
        )
        st.plotly_chart(fig_fun, use_container_width=True)

    with g2:
        df_cnt_riesgo = (
            df_auditados.groupby("estatus_simulado", as_index=False)
            .size()
            .rename(columns={"size": "SKUs Auditados"})
        )
        fig_r = px.bar(
            df_cnt_riesgo,
            x="estatus_simulado",
            y="SKUs Auditados",
            color="estatus_simulado",
            text_auto=True,
            title=f"Distribución de Riesgo ({len(df_auditados):,} SKUs Auditados)",
            color_discrete_map={
                "EXPLORACION_UCB": "#3B82F6",
                "ACTIVO_SANO": "#10B981",
                "ALERTA_DECAY": "#F59E0B",
                "BAJA_AUTOMATICA": "#EF4444",
            },
        )
        fig_r.update_layout(template="plotly_dark", height=380)
        st.plotly_chart(fig_r, use_container_width=True)


# ==============================================================================
# TAB 5: CURVA DE DECAIMIENTO POR SKU (TIME-SERIES)
# ==============================================================================
with tab5:
    st.subheader(
        "📉 Curva de Decaimiento por SKU (Time-Series) y Proyección Anti-Quiebre 24h"
    )
    if not df_auditados.empty:
        df_ord_ts = df_auditados.sort_values(
            by=["stock_actual", "num_lecturas"], ascending=[False, False]
        )
        sku_sel_ts = st.selectbox(
            "Selecciona un SKU auditado:", options=df_ord_ts["id_ps"].tolist()
        )
        fila_sku = df_auditados[df_auditados["id_ps"] == sku_sel_ts].iloc[0]

        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Stock Actual Real", f"{int(fila_sku['stock_actual'])} u.")
        m2.metric(
            "Reserva Dinámica 24h", f"{int(fila_sku['reserva_dinamica_24h'])} u."
        )
        m3.metric(
            "Stock Publicable MeLi", f"{int(fila_sku['stock_publicable_meli'])} u."
        )
        m4.metric(
            "Autonomía Estimada", f"{float(fila_sku['horas_autonomia']):.1f} horas"
        )

        if (
            not df_bitacora.empty
            and "id_ps" in df_bitacora.columns
            and "_stk" in df_bitacora.columns
        ):
            df_sub_b = df_bitacora[df_bitacora["id_ps"] == str(sku_sel_ts)].copy()
            if not df_sub_b.empty:
                eje_x = (
                    "_ts"
                    if "_ts" in df_sub_b.columns and df_sub_b["_ts"].notna().any()
                    else df_sub_b.index
                )
                fig_b = px.line(
                    df_sub_b,
                    x=eje_x,
                    y="_stk",
                    markers=True,
                    title=f"Historial Real de Lecturas · SKU {sku_sel_ts} ({fila_sku['nombre_modelo']})",
                )
                fig_b.add_hline(
                    y=float(fila_sku["reserva_dinamica_24h"]),
                    line_dash="dash",
                    line_color="#EF4444",
                    annotation_text="Reserva Anti-Quiebre 24h",
                )
                fig_b.update_layout(template="plotly_dark", height=360)
                st.plotly_chart(fig_b, use_container_width=True)


# ==============================================================================
# TAB 6: TABLA DE PRECIOS Y SUCURSALES
# ==============================================================================
with tab6:
    st.subheader("💵 Tabla de Precios, Sucursales y Tallas Disponibles")
    st.dataframe(
        df_auditados[
            [
                "id_ps",
                "catalogo_origen",
                "marca",
                "¿Permitida en MeLi?",
                "nombre_modelo",
                "tallas_detalle",
                "stock_vallejo",
                "stock_olivar",
                "stock_arco_norte",
                "stock_actual",
                "reserva_dinamica_24h",
                "stock_publicable_meli",
                "Precio Compra",
                "Precio Venta Sugerido",
                "Ganancia Libre ($)",
                "accion_meli",
            ]
        ],
        use_container_width=True,
        hide_index=True,
        height=440,
    )


# ==============================================================================
# TAB 7: PLANIFICADOR DOBLE CARRIL Y BITÁCORA
# ==============================================================================
with tab7:
    st.subheader(
        "⏱️ Planificador de Doble Carril (Próxima Corrida GitHub Actions) y Bitácora"
    )
    carril_a_prox, carril_b_prox = seleccionar_lote_doble_carril(df_maestro)

    c_ca, c_cb = st.columns(2)
    with c_ca:
        st.markdown(
            f"##### 🛡️ Carril A: Vigilancia 24h Top MeLi ({len(carril_a_prox)} SKUs)"
        )
        st.dataframe(
            df_maestro[df_maestro["id_ps"].isin(carril_a_prox)][
                [
                    "id_ps",
                    "nombre_modelo",
                    "stock_actual",
                    "horas_autonomia",
                    "ultima_lectura",
                ]
            ],
            use_container_width=True,
            hide_index=True,
        )
    with c_cb:
        st.markdown(
            f"##### 🔄 Carril B: Barrido Semanal 8,165 IDs ({len(carril_b_prox)} SKUs)"
        )
        st.dataframe(
            df_maestro[df_maestro["id_ps"].isin(carril_b_prox)][
                ["id_ps", "nombre_modelo", "marca", "auditado_etl", "ultima_lectura"]
            ],
            use_container_width=True,
            hide_index=True,
        )