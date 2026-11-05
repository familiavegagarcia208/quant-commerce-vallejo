"""
=========================================================================================
PANEL DE CONTROL BI Y GESTIÓN OPERATIVA (PASO 4) - STREAMLIT SESSION STATE ARCHITECTURE
=========================================================================================
Autor: Arquitectura Quant-Commerce Vallejo
Descripción: Panel interactivo de Business Intelligence optimizado con persistencia de 
estado (`st.session_state`), utilizando estrictamente las URLs canónicas originales del 
origen de datos, aplicando Unit Economics exactos y doble blindaje de inventario.
=========================================================================================
"""

from __future__ import annotations

import math
import re
import sqlite3
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import pandas as pd
import streamlit as st

# ===========================================================================
# CONFIGURACIÓN GLOBAL DE LA VISTA
# ===========================================================================
st.set_page_config(
    page_title="Quant-Commerce Vallejo · Centro Operativo BI",
    page_icon="👟",
    layout="wide",
    initial_sidebar_state="expanded",
)

RUTA_BD: Path = Path("data/zapateria_vallejo.db")
DIR_CATALOGO: Path = Path("data/catalogo_listo")
RESERVA_INVENTARIO: int = 6  # 5 pares de piso/exhibición + 1 par de rotación intradía


# ===========================================================================
# 1. CAPA DE EXTRACCIÓN Y SANITIZACIÓN DE DATOS (ETL - EXTRACT & CLEAN)
# ===========================================================================
def cargar_y_sanitizar_datos() -> Tuple[pd.DataFrame, pd.DataFrame]:
    """
    Extrae los datos crudos de SQLite y ejecuta una fase estricta de sanitización mediante Pandas.
    Respeta de manera absoluta la URL original del dataset de origen sin armar links dinámicos.

    Returns:
        Tuple[pd.DataFrame, pd.DataFrame]: Dataframe de productos sanitizado y dataframe de tallas limpio.
    """
    if not RUTA_BD.exists():
        return pd.DataFrame(), pd.DataFrame()

    conn = sqlite3.connect(RUTA_BD, timeout=15)
    df_cand: pd.DataFrame = pd.read_sql_query("SELECT * FROM zapatos_candidatos", conn)
    df_hist: pd.DataFrame = pd.read_sql_query(
        """
        SELECT CAST(id_ps AS TEXT) AS id_ps,
               CAST(talla AS TEXT) AS talla,
               COALESCE(stock_vallejo, 0) AS stock_vallejo
        FROM historial_stock_21_dias
        """,
        conn,
    )
    conn.close()

    if df_cand.empty:
        return pd.DataFrame(), pd.DataFrame()

    # --- FASE DE SANITIZACIÓN DEL HISTÓRICO DE TALLAS ---
    df_hist["id_ps"] = df_hist["id_ps"].astype(str).str.strip()
    df_hist["talla"] = df_hist["talla"].astype(str).str.strip()
    
    # Imputación estricta de nulos a 0 enteros en inventario físico
    df_hist["stock_vallejo"] = pd.to_numeric(df_hist["stock_vallejo"], errors="coerce").fillna(0).clip(lower=0).astype(int)
    
    # Normalización formal de tallas (ej. "talla 9" o "9" -> "29.0")
    df_hist["talla_norm"] = df_hist["talla"].apply(normalizar_talla_mx)
    
    # REGLA DE INVENTARIO: Stock Publicable = MAX(0, Stock Físico - 6)
    df_hist["stock_publicable"] = (df_hist["stock_vallejo"] - RESERVA_INVENTARIO).clip(lower=0)

    # --- FASE DE SANITIZACIÓN DEL DATASET DE PRODUCTOS ---
    df_cand["id_ps"] = df_cand["id_ps"].astype(str).str.strip()
    
    # Limpieza e imputación de columnas numéricas críticas (previene desbordamiento o NaN)
    columnas_numericas = ["costo_socio", "precio_ml", "utilidad_neta_ml", "apto_publicar"]
    for col in columnas_numericas:
        if col in df_cand.columns:
            df_cand[col] = pd.to_numeric(df_cand[col], errors="coerce").fillna(0.0)
        else:
            df_cand[col] = 0.0

    # Conversión segura de flags booleanas/enteras
    df_cand["apto_publicar"] = df_cand["apto_publicar"].astype(int).clip(lower=0, upper=1)

    # Sanitización de campos de texto (imputación de valores por defecto si son NaN o vacíos)
    df_cand["nombre_modelo"] = df_cand["nombre_modelo"].fillna("Modelo Desconocido").astype(str).str.strip()
    df_cand["genero"] = df_cand["genero"].fillna("Unisex").astype(str).str.strip()
    df_cand["tipo_calzado"] = df_cand.get("tipo_calzado", pd.Series(["Tenis / Casual"] * len(df_cand))).fillna("Tenis / Casual").astype(str).str.strip()
    
    # REGLA DE ORO SOBRE URLS: Leer ÚNICA y EXCLUSIVAMENTE el valor del dataset original ('url', 'enlace', 'link' o 'url_producto')
    columna_url_encontrada: Optional[str] = None
    for cand_col in ["url", "enlace", "link", "url_producto"]:
        if cand_col in df_cand.columns:
            columna_url_encontrada = cand_col
            break

    if columna_url_encontrada:
        df_cand["url_original_dataset"] = df_cand[columna_url_encontrada].fillna("").astype(str).str.strip()
    else:
        # Fallback de seguridad si ninguna columna común estuviera presente, preservando el string vacío o logueando alerta
        df_cand["url_original_dataset"] = ""

    return df_cand, df_hist


def normalizar_talla_mx(valor_str: str | float) -> str:
    """
    Estandariza tallas coloquiales mexicanas o de calzado a centímetros (formato MX).

    Args:
        valor_str (str | float): Talla cruda extraída del dataset.

    Returns:
        str: Talla normalizada en formato flotante a un decimal (ej. '29.0').
    """
    if pd.isna(valor_str):
        return "0.0"
    m = re.findall(r"\d+(?:\.\d+)?", str(valor_str))
    if not m:
        return str(valor_str).strip()
    num = float(m[0])
    # Estandarización de numeración entera de calzado en México (ej. "9" -> 29.0 cm)
    if 2.0 <= num <= 11.5:
        num += 20.0
    return f"{num:.1f}"


# ===========================================================================
# 2. CAPA DE TRANSFORMACIÓN Y NEGOCIO (BUSINESS LOGIC & UNIT ECONOMICS)
# ===========================================================================
def estimar_envio_real(costo_socio: float) -> float:
    """
    Estima el costo real de envío por Mercado Envíos en función del volumen/costo del calzado.

    Args:
        costo_socio (float): Costo de adquisición sanitizado (garantizado sin NaN).

    Returns:
        float: Costo de envío estimado en pesos mexicanos.
    """
    if costo_socio < 350:
        return 98.0
    elif costo_socio < 650:
        return 112.0
    return 128.0


def calcular_precio_unit_economics(costo_socio: float, margen_objetivo: float = 0.25) -> Dict[str, float]:
    """
    Calcula el precio de venta sugerido mediante ingeniería inversa financiera asumiendo datos 100% limpios.

    DEMOSTRACIÓN MATEMÁTICA DEL DESPEJE DE LA FÓRMULA:
    --------------------------------------------------
    Variables:
    - P = Precio de venta final en Mercado Libre
    - C = Costo de Socio (Vallejo) [Garantizado limpio por la capa ETL]
    - E = Envío real estimado
    - c_ml = Comisión de Mercado Libre (17.5% -> 0.175)
    - r_sat = Retención efectiva del SAT (IVA 8% + ISR 2.5% sobre base sin IVA P/1.16 -> ~9.05%)
    - m = Fondo de merma (3% del costo -> 0.03 * C)
    - g = Margen de ganancia neta deseado sobre el costo (ej. 25% -> 0.25 * C)

    Ecuación de equilibrio financiero:
    P = C + E + (c_ml * P) + (r_sat * P) + (m * C) + (g * C)

    Agrupando P a la izquierda y costos a la derecha:
    P - (c_ml * P) - (r_sat * P) = C(1 + m + g) + E
    P * (1 - c_ml - r_sat) = C * (1 + m + g) + E

    Despejando P:
    P = [ C * (1 + m + g) + E ] / [ 1 - c_ml - r_sat ]

    Args:
        costo_socio (float): Costo de adquisición numérico puro.
        margen_objetivo (float): Porcentaje de ganancia limpia deseada (por defecto 25%).

    Returns:
        Dict[str, float]: Diccionario con precios, utilidad neta e impuestos calculados de forma segura.
    """
    if costo_socio <= 0:
        return {"precio_ml": 0.0, "utilidad_neta": 0.0, "envio": 0.0, "comision": 0.0, "impuestos": 0.0}

    envio: float = estimar_envio_real(costo_socio)
    
    c_ml: float = 0.175
    r_sat: float = (0.08 + 0.025) / 1.16  # ~0.090517
    m: float = 0.03
    g: float = margen_objetivo

    # Aplicación directa del despeje algebraico validado
    numerador: float = (costo_socio * (1.0 + m + g)) + envio
    denominador: float = 1.0 - c_ml - r_sat
    
    if denominador <= 0:
        denominador = 0.734483  # Protección contra división por cero

    precio_bruto: float = numerador / denominador
    precio_ml: float = float(math_ceil_comercial(precio_bruto))
    
    comision: float = round(precio_ml * c_ml, 2)
    impuestos: float = round((precio_ml / 1.16) * (0.08 + 0.025), 2)
    merma: float = round(costo_socio * m, 2)
    
    utilidad_neta: float = round(precio_ml - costo_socio - envio - comision - impuestos - merma, 2)

    return {
        "precio_ml": precio_ml,
        "utilidad_neta": utilidad_neta,
        "envio": envio,
        "comision": comision,
        "impuestos": impuestos,
    }


def math_ceil_comercial(valor: float) -> int:
    """Redondea un número flotante al múltiplo de 10 superior menos 1 unidad (ej. 1034 -> 1039)."""
    return int(math.ceil(float(valor) / 10.0) * 10.0 - 1.0)


def procesar_modelo_operativo(df_cand: pd.DataFrame, df_hist: pd.DataFrame) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """
    Cruza el catálogo sanitizado con las tallas procesadas y genera los indicadores de negocio.

    Args:
        df_cand (pd.DataFrame): Productos limpios.
        df_hist (pd.DataFrame): Histórico de tallas limpio.

    Returns:
        Tuple[pd.DataFrame, pd.DataFrame]: Productos enriquecidos y tallas calculadas.
    """
    if df_cand.empty:
        return pd.DataFrame(), pd.DataFrame()

    stk_fis_map: Dict[str, int] = df_hist.groupby("id_ps")["stock_vallejo"].sum().to_dict() if not df_hist.empty else {}
    stk_seg_map: Dict[str, int] = df_hist.groupby("id_ps")["stock_publicable"].sum().to_dict() if not df_hist.empty else {}
    tallas_ok_map: Dict[str, int] = df_hist[df_hist["stock_publicable"] > 0].groupby("id_ps")["talla_norm"].count().to_dict() if not df_hist.empty else {}

    registros = []
    for _, row in df_cand.iterrows():
        id_s: str = row["id_ps"]
        costo: float = row["costo_socio"]
        
        fisico: int = int(stk_fis_map.get(id_s, 0))
        publicable: int = int(stk_seg_map.get(id_s, 0))
        n_tallas_seguras: int = int(tallas_ok_map.get(id_s, 0))
        
        finanzas = calcular_precio_unit_economics(costo)
        
        carpeta_fotos = DIR_CATALOGO / id_s
        fotos_requeridas = [
            carpeta_fotos / "01_portada_blanca.jpg",
            carpeta_fotos / "02_angulo_galeria.jpg",
            carpeta_fotos / "03_ficha_beneficios.jpg",
            carpeta_fotos / "04_guia_tallas_cm.jpg",
        ]
        tiene_4_fotos: bool = all(f.exists() for f in fotos_requeridas)

        fallas = []
        if costo <= 0:
            fallas.append("Costo no registrado")
        if not tiene_4_fotos:
            fallas.append("Faltan fotografías HD (4/4)")
        if n_tallas_seguras < 2 or publicable < 4:
            fallas.append(f"Stock insuficiente tras blindaje de {RESERVA_INVENTARIO} pares")

        semaforo = "✅ RECOMENDADO" if not fallas else "⚠️ NO RECOMENDADO"
        motivo = "Modelo óptimo para e-commerce." if not fallas else " · ".join(fallas)

        registros.append({
            "subir_ml": bool(row["apto_publicar"]),
            "id_ps": id_s,
            "sku_oculto": f"PS-{id_s}",
            "nombre_modelo": row["nombre_modelo"],
            "url_producto": row["url_original_dataset"],  # URL exacta y original leída del dataset
            "genero": row["genero"],
            "categoria": row["tipo_calzado"],
            "semaforo": semaforo,
            "motivo_diagnostico": motivo,
            "costo_socio": costo,
            "precio_ml": finanzas["precio_ml"],
            "utilidad_neta_ml": finanzas["utilidad_neta"],
            "stock_fisico": fisico,
            "stock_publicable": publicable,
            "tallas_seguras_count": n_tallas_seguras,
            "fotos_ok": tiene_4_fotos,
        })

    return pd.DataFrame(registros), df_hist


# ===========================================================================
# 3. CAPA DE INTERFAZ GRÁFICA Y UI CON SESSION STATE (PRESENTATION LAYER)
# ===========================================================================
def inicializar_session_state() -> None:
    """
    Inicializa el estado persistente en Streamlit (`st.session_state`).
    CRÍTICO: Almacena el DataFrame principal para evitar que las casillas de verificación 
    del `st.data_editor` se reseteen a su valor original en cada recarga de la aplicación.
    """
    if "df_master" not in st.session_state:
        df_cand_raw, df_hist_raw = cargar_y_sanitizar_datos()
        df_prod_init, df_tallas_init = procesar_modelo_operativo(df_cand_raw, df_hist_raw)
        st.session_state["df_master"] = df_prod_init
        st.session_state["df_tallas"] = df_tallas_init


def renderizar_interfaz() -> None:
    """Renderiza el panel interactivo completo gestionando la reactividad mediante `st.session_state`."""
    
    # Inicializar el estado persistente
    inicializar_session_state()
    
    df_prod: pd.DataFrame = st.session_state["df_master"]
    df_tallas: pd.DataFrame = st.session_state["df_tallas"]

    if df_prod.empty:
        st.error("⚠️ No se encontró información válida en la base de datos local.")
        return

    # 1. Barra Lateral (Filtros tipo Power BI - Slicers)
    with st.sidebar:
        st.markdown("### 🎛️ Filtros Avanzados (Slicers)")
        
        modo_filtro = st.selectbox(
            "📌 Estado de Publicación:",
            options=["Todos los Registros", "✅ Solo Recomendados", "🚀 Seleccionados para ML (Apto = 1)"]
        )
        
        generos_disponibles = sorted(df_prod["genero"].unique().tolist())
        generos_sel = st.multiselect("👥 Género / Departamento:", options=generos_disponibles, default=generos_disponibles)
        
        categorias_disponibles = sorted(df_prod["categoria"].unique().tolist())
        categorias_sel = st.multiselect("👟 Categoría de Calzado:", options=categorias_disponibles, default=categorias_disponibles)
        
        max_costo_val: float = float(df_prod["costo_socio"].max() or 1000.0)
        rango_costo = st.slider("💵 Rango de Costo en Vallejo ($):", 0.0, max_costo_val, (0.0, max_costo_val), 25.0)
        
        min_ganancia_val = st.slider("📈 Ganancia Neta Mínima ($/par):", 0, 500, 100, 25)
        
        solo_fotos = st.checkbox("📸 Exigir 4 Fotos HD Confirmadas", value=False)

    # Aplicación de Slicers sobre una copia de visualización
    df_filtrado = df_prod.copy()
    if modo_filtro == "✅ Solo Recomendados":
        df_filtrado = df_filtrado[df_filtrado["semaforo"] == "✅ RECOMENDADO"]
    elif modo_filtro == "🚀 Seleccionados para ML (Apto = 1)":
        df_filtrado = df_filtrado[df_filtrado["subir_ml"]]

    if generos_sel:
        df_filtrado = df_filtrado[df_filtrado["genero"].isin(generos_sel)]
    if categorias_sel:
        df_filtrado = df_filtrado[df_filtrado["categoria"].isin(categorias_sel)]

    df_filtrado = df_filtrado[
        (df_filtrado["costo_socio"] >= rango_costo[0]) &
        (df_filtrado["costo_socio"] <= rango_costo[1]) &
        (df_filtrado["utilidad_neta_ml"] >= min_ganancia_val)
    ]

    if solo_fotos:
        df_filtrado = df_filtrado[df_filtrado["fotos_ok"]]

    # 2. Métricas Ejecutivas Dinámicas (KPIs vinculados al estado y filtros)
    st.title("👟 Centro Operativo BI · Quant-Commerce Vallejo")
    st.markdown("Gestión de inventario bajo demanda y unit economics con persistencia de estado reactiva.")

    k1, k2, k3, k4 = st.columns(4)
    k1.metric("📦 Modelos Filtrados", f"{len(df_filtrado)} SKUs", f"{int(df_filtrado['subir_ml'].sum())} activos en ML")
    k2.metric("💵 Costo Promedio", f"${df_filtrado['costo_socio'].mean():,.0f} MXN" if not df_filtrado.empty else "$0 MXN", "Por par en Vallejo")
    k3.metric("💰 Ganancia Neta Promedio", f"${df_filtrado['utilidad_neta_ml'].mean():,.0f} MXN" if not df_filtrado.empty else "$0 MXN", "Libre en bolsa por par")
    k4.metric("🛡️ Pares Publicables", f"{df_filtrado['stock_publicable'].sum():,}", f"De {df_filtrado['stock_fisico'].sum():,} físicos")

    st.markdown("---")

    # 3. Matriz Principal Interactiva vinculada a st.session_state
    st.markdown("### 📋 Matriz de Control e Inventario")
    st.caption("Marca o desmarca la casilla 'Subir a ML'. Los cambios se mantienen guardados gracias al manejo de estado en Streamlit.")

    columnas_matriz = [
        "subir_ml", "id_ps", "sku_oculto", "nombre_modelo", "url_producto",
        "semaforo", "costo_socio", "precio_ml", "utilidad_neta_ml",
        "stock_publicable", "motivo_diagnostico"
    ]
    
    df_editable_output = st.data_editor(
        df_filtrado[columnas_matriz],
        column_config={
            "subir_ml": st.column_config.CheckboxColumn("🚀 Subir a ML"),
            "id_ps": st.column_config.TextColumn("ID Price Shoes", disabled=True),
            "sku_oculto": st.column_config.TextColumn("SKU Interno", disabled=True),
            "nombre_modelo": st.column_config.TextColumn("Modelo", disabled=True),
            "url_producto": st.column_config.LinkColumn("Enlace Original", display_text="🔗 Ver en Price Shoes", disabled=True),
            "semaforo": st.column_config.TextColumn("Semáforo", disabled=True),
            "costo_socio": st.column_config.NumberColumn("Costo ($)", format="$%.0f", disabled=True),
            "precio_ml": st.column_config.NumberColumn("Precio Sugerido", format="$%.0f", disabled=True),
            "utilidad_neta_ml": st.column_config.NumberColumn("Ganancia Neta", format="$%.2f", disabled=True),
            "stock_publicable": st.column_config.NumberColumn("Stock Publicable", disabled=True),
            "motivo_diagnostico": st.column_config.TextColumn("Diagnóstico Técnico", disabled=True),
        },
        use_container_width=True,
        hide_index=True,
        height=350,
        key="editor_matriz_inventario",
    )

    # Sincronización automática de st.session_state y persistencia en Base de Datos al modificar el editor
    if not df_editable_output.equals(df_filtrado[columnas_matriz]):
        for idx, row in df_editable_output.iterrows():
            sku_id = row["id_ps"]
            nuevo_estado_ml = bool(row["subir_ml"])
            st.session_state["df_master"].loc[st.session_state["df_master"]["id_ps"] == sku_id, "subir_ml"] = nuevo_estado_ml

    if st.button("💾 Persistir Cambios en Base de Datos Local", type="primary"):
        conn = sqlite3.connect(RUTA_BD, timeout=15)
        for _, fila in st.session_state["df_master"].iterrows():
            conn.execute(
                "UPDATE zapatos_candidatos SET apto_publicar = ? WHERE CAST(id_ps AS TEXT) = ?;",
                (1 if fila["subir_ml"] else 0, str(fila["id_ps"])),
            )
        conn.commit()
        conn.close()
        st.success("✅ ¡Cambios sincronizados correctamente en la base de datos SQLite!")
        st.rerun()

    st.markdown("---")

    # 4. Vista Detallada (Drill-Down Unitario)
    st.markdown("### 🔍 Vista en Profundidad (Drill-Down por Producto)")
    
    opciones_select = (df_filtrado["id_ps"] + " · " + df_filtrado["nombre_modelo"]).tolist()
    if opciones_select:
        seleccion_sku = st.selectbox("Selecciona un modelo para auditar sus finanzas, stock por talla y fotografías:", opciones_select)
        sku_activo = seleccion_sku.split(" · ")[0]
        producto_activo = df_prod[df_prod["id_ps"] == sku_activo].iloc[0]

        col_kpi_drill, col_tallas_drill = st.columns([6, 6])

        with col_kpi_drill:
            st.markdown(f"#### 💰 Unit Economics · SKU {sku_activo}")
            p_venta = float(producto_activo["precio_ml"])
            c_socio = float(producto_activo["costo_socio"])
            fin_unit = calcular_unit_economics_desglose(c_socio, p_venta)

            df_desglose = pd.DataFrame([
                {"Concepto": "Precio de Venta Sugerido (ML)", "Monto ($ MXN)": f"+ ${p_venta:,.2f}"},
                {"Concepto": "Costo de Socio (Vallejo)", "Monto ($ MXN)": f"- ${c_socio:,.2f}"},
                {"Concepto": "Comisión Plataforma (17.5%)", "Monto ($ MXN)": f"- ${fin_unit['comision']:,.2f}"},
                {"Concepto": "Envío Real Estimado", "Monto ($ MXN)": f"- ${fin_unit['envio']:,.2f}"},
                {"Concepto": "Retención Impuestos SAT (~9.05%)", "Monto ($ MXN)": f"- ${fin_unit['impuestos']:,.2f}"},
                {"Concepto": "Fondo de Merma (3% s/costo)", "Monto ($ MXN)": f"- ${fin_unit['merma']:,.2f}"},
                {"Concepto": "==================================", "Monto ($ MXN)": "================"},
                {"Concepto": "💰 GANANCIA NETA LIBRE EN BOLSA", "Monto ($ MXN)": f"$ {producto_activo['utilidad_neta_ml']:,.2f} MXN"}
            ])
            st.table(df_desglose)

        with col_tallas_drill:
            st.markdown(f"#### 📏 Stock por Talla en Vallejo (Blindaje de {RESERVA_INVENTARIO} pares)")
            tallas_modelo = df_tallas[df_tallas["id_ps"] == sku_activo].copy()
            if not tallas_modelo.empty:
                tallas_modelo["reserva_fija"] = RESERVA_INVENTARIO
                st.dataframe(
                    tallas_modelo[["talla", "stock_vallejo", "reserva_fija", "stock_publicable"]].rename(
                        columns={
                            "talla": "Talla MX",
                            "stock_vallejo": "Inventario Físico",
                            "reserva_fija": "Reserva Operativa",
                            "stock_publicable": "Stock Publicable ML"
                        }
                    ),
                    use_container_width=True,
                    hide_index=True,
                    height=280
                )
            else:
                st.info("Sin registros de tallas para este modelo.")

        st.markdown("#### 📸 Galería de Fotografías HD")
        carpeta_imgs = DIR_CATALOGO / sku_activo
        cols_imgs = st.columns(4)
        archivos_hd = [
            ("Portada Blanca", carpeta_imgs / "01_portada_blanca.jpg"),
            ("Ángulo de Detalle", carpeta_imgs / "02_angulo_galeria.jpg"),
            ("Ficha de Beneficios", carpeta_imgs / "03_ficha_beneficios.jpg"),
            ("Guía de Tallas (CM)", carpeta_imgs / "04_guia_tallas_cm.jpg"),
        ]
        for idx, (etiqueta_foto, ruta_foto) in enumerate(archivos_hd):
            with cols_imgs[idx]:
                if ruta_foto.exists():
                    st.image(str(ruta_foto), caption=etiqueta_foto, use_container_width=True)
                else:
                    st.info(f"Falta: {etiqueta_foto}")


def calcular_unit_economics_desglose(costo: float, precio: float) -> Dict[str, float]:
    """Calcula el desglose monetario unitario para fines de visualización en el Drill-Down."""
    envio = estimar_envio_real(costo)
    comision = round(precio * 0.175, 2)
    impuestos = round((precio / 1.16) * (0.08 + 0.025), 2)
    merma = round(costo * 0.03, 2)
    return {
        "envio": envio,
        "comision": comision,
        "impuestos": impuestos,
        "merma": merma
    }


if __name__ == "__main__":
    renderizar_interfaz()