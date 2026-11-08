"""
========================================================================================
ZAPATERÍA VALLEJO - PANEL DE ANÁLISIS ETL, CURVA DE INVENTARIO Y CALIBRACIÓN UCB
========================================================================================
Arquitectura de Visualización Analítica (Fase Pre-Mercado Libre):
1. RepositorioAnaliticoSQLite : Introspección dinámica de columnas en SQLite (WAL).
2. SimuladorUmbralesEnVivo    : Recálculo vectorizado de reglas de negocio y UCB para
                                validar umbrales de baja automática con Ricardo.
3. InspectorTimeSeries        : Trazabilidad por timestamp, curva de decaimiento (λ)
                                y visor de catálogo fotográfico local.
========================================================================================
"""

from __future__ import annotations

import math
import sqlite3
from pathlib import Path
from typing import Dict, List, Set, Tuple

import pandas as pd
import streamlit as st

st.set_page_config(
    page_title="Zapatería Vallejo · Análisis ETL & Curva de Inventario",
    page_icon="📊",
    layout="wide",
)

RUTA_BD = Path("data/zapateria_vallejo.db")
RUTA_FOTOS = Path("data/catalogo_listo")


# ==============================================================================
# CAPA 1: REPOSITORIO ANALÍTICO CON INTROSPECCIÓN DINÁMICA DE ESQUEMA
# ==============================================================================
class RepositorioAnaliticoSQLite:
    def __init__(self, ruta_bd: Path) -> None:
        self.ruta_bd = ruta_bd

    def bd_disponible(self) -> bool:
        return self.ruta_bd.exists() and self.ruta_bd.stat().st_size > 0

    def _conectar(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.ruta_bd, timeout=30.0)
        conn.row_factory = sqlite3.Row
        return conn

    def obtener_columnas(self, tabla: str) -> Set[str]:
        if not self.bd_disponible():
            return set()
        with self._conectar() as conn:
            filas = conn.execute(f"PRAGMA table_info({tabla});").fetchall()
            return {str(f["name"]) for f in filas}

    @staticmethod
    def _col_segura(disponibles: Set[str], opciones: List[str], defecto_sql: str) -> str:
        for op in opciones:
            if op in disponibles:
                return op
        return defecto_sql

    def cargar_catalogo_analitico(self) -> pd.DataFrame:
        cols = self.obtener_columnas("zapatos_candidatos")
        if not cols:
            return pd.DataFrame()

        c_nombre = self._col_segura(cols, ["nombre_modelo", "modelo", "titulo"], "'Modelo Sin Nombre'")
        c_marca = self._col_segura(cols, ["marca", "brand"], "'S/M'")
        c_origen = self._col_segura(cols, ["origen_catalogo", "catalogo"], "'S/C'")
        c_costo = self._col_segura(cols, ["costo_bodega", "costo", "precio_costo"], "0.0")
        c_stock = self._col_segura(cols, ["stock_total_anterior", "stock_actual", "stock"], "0")
        c_estatus = self._col_segura(cols, ["estatus_etl"], "'PENDIENTE'")
        c_lambda = self._col_segura(cols, ["tasa_decaimiento"], "0.0")
        c_bayes = self._col_segura(cols, ["media_bayesiana"], "0.0")
        c_ucb = self._col_segura(cols, ["score_ucb"], "0.0")
        c_lecturas = self._col_segura(cols, ["num_consultas_hist"], "0")
        c_fuente = self._col_segura(cols, ["fuente_ultimo_stock"], "'SIN_AUDITAR'")
        c_fecha = self._col_segura(cols, ["ultima_actualizacion"], "NULL")

        query = f"""
            SELECT 
                id_ps,
                COALESCE({c_nombre}, 'SKU ' || id_ps) AS nombre_modelo,
                COALESCE({c_marca}, 'S/M') AS marca,
                COALESCE({c_origen}, 'S/C') AS origen_catalogo,
                COALESCE({c_costo}, 0.0) AS costo_base,
                COALESCE({c_stock}, 0) AS stock_actual,
                COALESCE({c_estatus}, 'PENDIENTE') AS estatus_etl,
                COALESCE({c_lambda}, 0.0) AS tasa_decaimiento,
                COALESCE({c_bayes}, 0.0) AS media_bayesiana,
                COALESCE({c_ucb}, 0.0) AS score_ucb,
                COALESCE({c_lecturas}, 0) AS num_lecturas,
                COALESCE({c_fuente}, 'SIN_AUDITAR') AS fuente_extraccion,
                {c_fecha} AS ultima_actualizacion
            FROM zapatos_candidatos
            WHERE id_ps IS NOT NULL AND TRIM(id_ps) != ''
            ORDER BY 
                CASE WHEN COALESCE({c_lecturas}, 0) > 0 THEN 0 ELSE 1 END,
                COALESCE({c_ucb}, 0.0) DESC
        """
        with self._conectar() as conn:
            df = pd.read_sql_query(query, conn)

        # Cruzar con el último registro de estancamiento en historial_inventario
        cols_hist = self.obtener_columnas("historial_inventario")
        if "dias_estancado" in cols_hist and not df.empty:
            with self._conectar() as conn:
                df_est = pd.read_sql_query(
                    """
                    SELECT h.id_ps, h.dias_estancado, h.delta_stock, h.motivo_decision
                    FROM historial_inventario h
                    INNER JOIN (
                        SELECT id_ps, MAX(id_consulta) AS max_id
                        FROM historial_inventario
                        GROUP BY id_ps
                    ) ult ON h.id_ps = ult.id_ps AND h.id_consulta = ult.max_id
                    """,
                    conn,
                )
            df = df.merge(df_est, on="id_ps", how="left")
            df["dias_estancado"] = df["dias_estancado"].fillna(0.0)
            df["delta_stock"] = df["delta_stock"].fillna(0).astype(int)
            df["motivo_decision"] = df["motivo_decision"].fillna("Pendiente de primera lectura ETL")
        else:
            df["dias_estancado"] = 0.0
            df["delta_stock"] = 0
            df["motivo_decision"] = "Pendiente de primera lectura ETL"

        return df

    def cargar_historial_completo(self, id_ps: Optional[str] = None) -> pd.DataFrame:
        cols_hist = self.obtener_columnas("historial_inventario")
        if not cols_hist:
            return pd.DataFrame()

        filtro = "WHERE id_ps = ?" if id_ps else ""
        params: Tuple = (id_ps,) if id_ps else ()

        query = f"""
            SELECT 
                id_consulta,
                id_ps,
                stock_detectado,
                COALESCE(delta_stock, 0) AS delta_stock,
                COALESCE(tasa_decaimiento_lambda, 0.0) AS tasa_decaimiento_lambda,
                COALESCE(dias_estancado, 0.0) AS dias_estancado,
                COALESCE(media_bayesiana, 0.0) AS media_bayesiana,
                COALESCE(score_ucb, 0.0) AS score_ucb,
                estatus_calculado,
                COALESCE(motivo_decision, '') AS motivo_decision,
                COALESCE(fuente_extraccion, 'N/A') AS fuente_extraccion,
                fecha_hora_consulta
            FROM historial_inventario
            {filtro}
            ORDER BY fecha_hora_consulta DESC, id_consulta DESC
        """
        with self._conectar() as conn:
            return pd.read_sql_query(query, conn, params=params)


# ==============================================================================
# CAPA 2: SIMULADOR MATEMÁTICO INTERACTIVO DE UMBRALES (CALIBRACIÓN DE RIESGO)
# ==============================================================================
def simular_clasificacion_en_vivo(
    df: pd.DataFrame,
    umbral_stock_critico: int,
    min_lecturas_exploracion: int,
    dias_max_estancamiento: float,
    tasa_caida_peligro: float,
    constante_c_ucb: float,
    score_min_supervivencia: float,
) -> pd.DataFrame:
    """Recalcula el Score UCB y el dictamen de riesgo con los parámetros del simulador."""
    df_sim = df.copy()
    auditados = df_sim[df_sim["num_lecturas"] > 0].copy()
    if auditados.empty:
        df_sim["score_ucb_simulado"] = 0.0
        df_sim["estatus_simulado"] = "PENDIENTE"
        df_sim["motivo_simulado"] = "Sin lecturas aún"
        return df_sim

    total_n = max(int(auditados["num_lecturas"].sum()), 1)

    scores_sim: List[float] = []
    estatus_sim: List[str] = []
    motivos_sim: List[str] = []

    for _, fila in df_sim.iterrows():
        n_i = int(fila["num_lecturas"])
        if n_i <= 0:
            scores_sim.append(0.0)
            estatus_sim.append("PENDIENTE")
            motivos_sim.append("Pendiente de extracción")
            continue

        stock = int(fila["stock_actual"])
        lam = float(fila["tasa_decaimiento"])
        dias_est = float(fila["dias_estancado"])
        mu_bayes = float(fila["media_bayesiana"])

        bono_ucb = round(constante_c_ucb * math.sqrt(math.log(total_n + 1) / n_i), 3)
        score_ucb = round(mu_bayes + bono_ucb, 3)

        if stock <= umbral_stock_critico:
            est = "BAJA_AUTOMATICA"
            mot = f"Stock ({stock}) <= Límite Crítico ({umbral_stock_critico})"
        elif n_i <= min_lecturas_exploracion:
            est = "EXPLORACION_UCB"
            mot = f"Protegido por exploración UCB ({n_i}/{min_lecturas_exploracion} lecturas)"
        elif dias_est >= dias_max_estancamiento and mu_bayes < score_min_supervivencia:
            est = "BAJA_AUTOMATICA"
            mot = f"Estancado {dias_est:.1f} días (>= {dias_max_estancamiento}d)"
        elif lam >= tasa_caida_peligro and stock <= (umbral_stock_critico * 2):
            est = "RIESGO_DECAY"
            mot = f"Caída acelerada (λ={lam}/día >= {tasa_caida_peligro})"
        else:
            est = "ACTIVO"
            mot = "Rotación y stock fuera de peligro"

        scores_sim.append(score_ucb)
        estatus_sim.append(est)
        motivos_sim.append(mot)

    df_sim["score_ucb_simulado"] = scores_sim
    df_sim["estatus_simulado"] = estatus_sim
    df_sim["motivo_simulado"] = motivos_sim
    return df_sim


# ==============================================================================
# CAPA 3: INTERFAZ ANALÍTICA Y DE CALIBRACIÓN (STREAMLIT)
# ==============================================================================
repo = RepositorioAnaliticoSQLite(RUTA_BD)

st.title("📊 Zapatería Vallejo · Laboratorio de Análisis ETL & Curva de Inventario")
st.caption(
    "Arquitectura de Serie de Tiempo (Time-Series), Extracción React SPA y Modelo Probabilístico Bayesiano + UCB"
)

if not repo.bd_disponible():
    st.error("No se encontró `data/zapateria_vallejo.db`. Ejecuta `python motor_etl_vallejo.py` primero.")
    st.stop()

# Controles interactivos en la barra lateral para calibrar con Ricardo
with st.sidebar:
    st.header("🎛️ Calibrador de Umbrales")
    st.write("Ajusta estos parámetros para validar en vivo qué productos saldrían por riesgo:")

    sim_stock_critico = st.slider(
        "Límite de Stock para Baja Automática (unidades)",
        min_value=0,
        max_value=12,
        value=3,
        help="Si el inventario detectado es menor o igual a este número, se clasifica como BAJA_AUTOMATICA.",
    )
    sim_lecturas_ucb = st.slider(
        "Lecturas de Gracia UCB (Productos Nuevos)",
        min_value=1,
        max_value=10,
        value=4,
        help="Número de consultas mínimas antes de castigar un modelo nuevo por falta de movimiento.",
    )
    sim_dias_estancado = st.slider(
        "Días Máximos sin Movimiento (Estancamiento)",
        min_value=2.0,
        max_value=15.0,
        value=7.0,
        step=0.5,
        help="Si un producto cumple estos días con el mismo stock y baja media bayesiana, pasa a BAJA_AUTOMATICA.",
    )
    sim_lambda_peligro = st.slider(
        "Pendiente de Caída Peligrosa λ (pares/día)",
        min_value=1.0,
        max_value=10.0,
        value=4.0,
        step=0.5,
        help="Velocidad de caída diaria que enciende la alerta preventiva RIESGO_DECAY.",
    )
    sim_c_ucb = st.slider(
        "Factor de Incertidumbre UCB (c)",
        min_value=0.20,
        max_value=2.50,
        value=1.20,
        step=0.10,
    )
    sim_score_min = st.slider(
        "Media Bayesiana Mínima de Supervivencia",
        min_value=0.10,
        max_value=1.50,
        value=0.40,
        step=0.05,
    )

df_base = repo.cargar_catalogo_analitico()
df_hist_global = repo.cargar_historial_completo()

df_analitico = simular_clasificacion_en_vivo(
    df=df_base,
    umbral_stock_critico=sim_stock_critico,
    min_lecturas_exploracion=sim_lecturas_ucb,
    dias_max_estancamiento=sim_dias_estancado,
    tasa_caida_peligro=sim_lambda_peligro,
    constante_c_ucb=sim_c_ucb,
    score_min_supervivencia=sim_score_min,
)

df_auditados = df_analitico[df_analitico["num_lecturas"] > 0]

# KPIs Ejecutivos Superiores
k1, k2, k3, k4, k5, k6 = st.columns(6)
k1.metric("Catálogo Total", len(df_analitico))
k2.metric("SKUs Auditados (ETL)", len(df_auditados))
k3.metric(
    "En Exploración UCB",
    int((df_auditados["estatus_simulado"] == "EXPLORACION_UCB").sum()),
)
k4.metric(
    "Activos Sanos",
    int((df_auditados["estatus_simulado"] == "ACTIVO").sum()),
)
k5.metric(
    "Alerta Decay (λ)",
    int((df_auditados["estatus_simulado"] == "RIESGO_DECAY").sum()),
)
k6.metric(
    "Candidatos a Baja",
    int((df_auditados["estatus_simulado"] == "BAJA_AUTOMATICA").sum()),
)

st.divider()

tab_simulador, tab_curva, tab_bitacora, tab_ficha = st.tabs(
    [
        "🎯 1. Matriz de Riesgo y Simulación de Umbrales",
        "📉 2. Curva de Decaimiento por SKU (Time-Series)",
        "⏱️ 3. Bitácora Histórica de Consultas (Timestamps)",
        "👟 4. Inspección Visual de Catálogo y Stock",
    ]
)

# ------------------------------------------------------------------------------
# PESTAÑA 1: MATRIZ DE RIESGO Y CALIBRACIÓN DE UMBRALES
# ------------------------------------------------------------------------------
with tab_simulador:
    st.subheader("Resultados del Lote Auditado bajo los Umbrales Seleccionados")

    if df_auditados.empty:
        st.info("Todavía no hay SKUs con lecturas en `historial_inventario`.")
    else:
        col_f1, col_f2 = st.columns([2, 1])
        with col_f1:
            estados_disponibles = sorted(df_auditados["estatus_simulado"].unique().tolist())
            filtro_est = st.multiselect(
                "Filtrar por Clasificación de Riesgo:",
                options=estados_disponibles,
                default=estados_disponibles,
            )
        with col_f2:
            orden_col = st.selectbox(
                "Ordenar tabla por:",
                options=["stock_actual", "score_ucb_simulado", "tasa_decaimiento", "ultima_actualizacion"],
                index=0,
            )

        df_vista = df_auditados[df_auditados["estatus_simulado"].isin(filtro_est)].sort_values(
            by=orden_col, ascending=(orden_col == "stock_actual")
        )

        columnas_mostrar = [
            "id_ps",
            "nombre_modelo",
            "stock_actual",
            "delta_stock",
            "tasa_decaimiento",
            "media_bayesiana",
            "score_ucb_simulado",
            "num_lecturas",
            "estatus_simulado",
            "motivo_simulado",
            "fuente_extraccion",
            "ultima_actualizacion",
        ]
        st.dataframe(df_vista[columnas_mostrar], use_container_width=True, hide_index=True)

        # Distribución gráfica de unidades de stock por SKU auditado
        st.markdown("#### Comparativa de Existencias Reales por SKU Auditado")
        df_chart_stock = df_vista.set_index("id_ps")[["stock_actual", "score_ucb_simulado"]]
        st.bar_chart(df_chart_stock)

# ------------------------------------------------------------------------------
# PESTAÑA 2: CURVA DE DECAIMIENTO POR SKU (SERIE DE TIEMPO)
# ------------------------------------------------------------------------------
with tab_curva:
    st.subheader("Evolución Temporal del Inventario y Curva de Decaimiento ($\lambda$)")

    if df_hist_global.empty:
        st.info("No hay lecturas históricas registradas.")
    else:
        skus_con_historial = df_auditados["id_ps"].tolist()
        mapa_nombres: Dict[str, str] = dict(zip(df_auditados["id_ps"], df_auditados["nombre_modelo"]))

        sku_seleccionado = st.selectbox(
            "Selecciona el SKU para inspeccionar su curva de inventario:",
            options=skus_con_historial,
            format_func=lambda x: f"{x} — {mapa_nombres.get(x, '')}",
        )

        df_sku_serie = repo.cargar_historial_completo(sku_seleccionado).sort_values(
            by="fecha_hora_consulta", ascending=True
        )

        if not df_sku_serie.empty:
            ult = df_sku_serie.iloc[-1]
            m1, m2, m3, m4 = st.columns(4)
            m1.metric("Stock Actual Detectado", int(ult["stock_detectado"]), delta=int(ult["delta_stock"]))
            m2.metric("Tasa de Caída (λ)", f"{ult['tasa_decaimiento_lambda']} / día")
            m3.metric("Media Bayesiana (μ)", f"{ult['media_bayesiana']}")
            m4.metric("Score UCB Actual", f"{ult['score_ucb']}")

            st.line_chart(
                df_sku_serie.set_index("fecha_hora_consulta")[["stock_detectado", "score_ucb"]]
            )
            st.dataframe(
                df_sku_serie.sort_values("fecha_hora_consulta", ascending=False),
                use_container_width=True,
                hide_index=True,
            )

# ------------------------------------------------------------------------------
# PESTAÑA 3: BITÁCORA HISTÓRICA GLOBAL (REQUERIMIENTO DE TIMESTAMP DE RICARDO)
# ------------------------------------------------------------------------------
with tab_bitacora:
    st.subheader("Bitácora de Consultas Diarias con Fecha y Hora Exacta")
    st.write(
        "Cada fila representa una extracción confirmada desde la API JSON del portal en React (`historial_inventario`)."
    )
    if df_hist_global.empty:
        st.info("Sin registros en la bitácora histórica.")
    else:
        st.dataframe(df_hist_global, use_container_width=True, hide_index=True)
        csv_bytes = df_hist_global.to_csv(index=False).encode("utf-8")
        st.download_button(
            label="📥 Descargar Bitácora Histórica en CSV (Para análisis en Excel)",
            data=csv_bytes,
            file_name="historial_inventario_vallejo.csv",
            mime="text/csv",
        )

# ------------------------------------------------------------------------------
# PESTAÑA 4: INSPECCIÓN VISUAL DE CATÁLOGO Y LÁMINAS HD
# ------------------------------------------------------------------------------
with tab_ficha:
    st.subheader("Inspección Visual de Modelos y Trazabilidad")
    lista_skus = df_analitico["id_ps"].tolist()
    mapa_todos: Dict[str, str] = dict(zip(df_analitico["id_ps"], df_analitico["nombre_modelo"]))

    sku_ficha = st.selectbox(
        "Buscar modelo en catálogo:",
        options=lista_skus,
        format_func=lambda x: f"{x} — {mapa_todos.get(x, '')}",
    )

    if sku_ficha:
        fila_sku = df_analitico[df_analitico["id_ps"] == sku_ficha].iloc[0]
        c_info, c_fotos = st.columns([1, 2])
        with c_info:
            st.markdown(f"### {fila_sku['nombre_modelo']}")
            st.write(f"**SKU:** `{fila_sku['id_ps']}`")
            st.write(f"**Marca:** {fila_sku['marca']}")
            st.write(f"**Catálogo Origen:** {fila_sku['origen_catalogo']}")
            st.write(f"**Stock Confirmado:** `{fila_sku['stock_actual']}` unidades")
            st.write(f"**Estatus Simulado:** `{fila_sku['estatus_simulado']}`")
            st.write(f"**Diagnóstico:** {fila_sku['motivo_simulado']}")
            st.write(f"**Fuente de Extracción:** `{fila_sku['fuente_extraccion']}`")
            st.write(f"**Última Consulta:** `{fila_sku['ultima_actualizacion']}`")

        with c_fotos:
            carpeta_sku = RUTA_FOTOS / str(sku_ficha)
            if carpeta_sku.exists():
                imgs = sorted(carpeta_sku.glob("*.png")) + sorted(carpeta_sku.glob("*.jpg"))
                if imgs:
                    columnas_img = st.columns(min(len(imgs), 3))
                    for idx_img, ruta_img in enumerate(imgs[:3]):
                        columnas_img[idx_img].image(
                            str(ruta_img), caption=ruta_img.name, use_container_width=True
                        )
                else:
                    st.info("La carpeta del SKU existe en `data/catalogo_listo/`, pero no contiene imágenes.")
            else:
                st.info(f"Sin láminas generadas aún en `data/catalogo_listo/{sku_ficha}`.")