"""
=========================================================================================
PASO 4: PANEL DE CONTROL BI MULTI-CATEGORÍA (ROPA Y CALZADO) EN STREAMLIT
=========================================================================================
Responsabilidad Única (SRP):
Capa de visualización e interacción (UI/BI) con soporte para Ropa y Calzado,
persistencia de casillas en `st.session_state` y uso estricto de `url_real`.
=========================================================================================
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import Dict, List, Tuple

import pandas as pd
import streamlit as st

st.set_page_config(
    page_title="Quant-Commerce Vallejo · Ropa y Calzado BI",
    page_icon="🛍️",
    layout="wide",
    initial_sidebar_state="expanded",
)

RUTA_BD: Path = Path("data/zapateria_vallejo.db")
DIR_CATALOGO: Path = Path("data/catalogo_listo")


def leer_tablas_procesadas_sqlite() -> Tuple[pd.DataFrame, pd.DataFrame]:
    """Lee desde SQLite los artículos procesados por `paso5_motor_financiero.py`."""
    if not RUTA_BD.exists():
        return pd.DataFrame(), pd.DataFrame()

    conn = sqlite3.connect(RUTA_BD, timeout=15)
    df_prod: pd.DataFrame = pd.read_sql_query(
        """
        SELECT CAST(id_ps AS TEXT) AS id_ps,
               COALESCE(sku_interno_ml, 'PS-' || CAST(id_ps AS TEXT)) AS sku_interno_ml,
               COALESCE(nombre_modelo, 'Modelo Sin Nombre') AS nombre_modelo,
               COALESCE(url_real, '') AS url_real,
               COALESCE(genero, 'Unisex') AS genero,
               COALESCE(tipo_calzado, 'Casual / Urbano') AS categoria,
               COALESCE(color, 'Multicolor / Clásico') AS color,
               COALESCE(tier, 'B') AS tier,
               COALESCE(costo_socio, 0.0) AS costo_socio,
               COALESCE(precio_piso_ml, 0.0) AS precio_piso_ml,
               COALESCE(precio_ml, 0.0) AS precio_ml,
               COALESCE(envio_ml_pesos, 0.0) AS envio_ml_pesos,
               COALESCE(comision_ml_pesos, 0.0) AS comision_ml_pesos,
               COALESCE(impuestos_sat_pesos, 0.0) AS impuestos_sat_pesos,
               COALESCE(fondo_merma_ml, 0.0) AS fondo_merma_ml,
               COALESCE(utilidad_neta_ml, 0.0) AS utilidad_neta_ml,
               COALESCE(iva_a_favor_ml, 0.0) AS iva_a_favor_ml,
               COALESCE(stock_total_anterior, 0) AS stock_fisico,
               COALESCE(stock_publicable_total, 0) AS stock_publicable,
               COALESCE(tallas_seguras_count, 0) AS tallas_seguras_count,
               COALESCE(tallas_seguras_detalle, 'Sin tallas > 6 pzas') AS tallas_seguras_detalle,
               COALESCE(fotos_completas, 0) AS fotos_completas,
               COALESCE(fotos_status, '0/4 ❌') AS fotos_status,
               COALESCE(semaforo, '⚠️ NO RECOMENDADO') AS semaforo,
               COALESCE(motivo_diagnostico, '') AS motivo_diagnostico,
               COALESCE(apto_publicar, 0) AS apto_publicar
        FROM zapatos_candidatos
        WHERE COALESCE(costo_socio, 0) > 0
           OR COALESCE(stock_total_anterior, 0) > 0
           OR COALESCE(fotos_completas, 0) = 1
        """,
        conn,
    )

    df_tallas: pd.DataFrame = pd.read_sql_query(
        """
        SELECT CAST(h.id_ps AS TEXT) AS id_ps,
               COALESCE(h.talla_norm, CAST(h.talla AS TEXT)) AS talla_norm,
               COALESCE(h.stock_vallejo, 0) AS stock_vallejo,
               COALESCE(h.reserva_operativa, 6) AS reserva_operativa,
               COALESCE(h.stock_seguro_publicable, 0) AS stock_seguro_publicable,
               h.fecha
        FROM historial_stock_21_dias h
        INNER JOIN (
            SELECT id_ps, MAX(fecha) AS max_fecha
            FROM historial_stock_21_dias
            GROUP BY id_ps
        ) ult ON h.id_ps = ult.id_ps AND h.fecha = ult.max_fecha
        """,
        conn,
    )
    conn.close()

    if not df_prod.empty:
        df_prod["subir_ml"] = df_prod["apto_publicar"].astype(int) == 1
        df_prod["fotos_ok"] = df_prod["fotos_completas"].astype(int) == 1
        df_prod["stock_fisico"] = df_prod["stock_fisico"].clip(lower=0).astype(int)
        df_prod["stock_publicable"] = df_prod["stock_publicable"].clip(lower=0).astype(int)

    return df_prod, df_tallas


def mapear_talla_busqueda_ui(entrada_talla: str) -> List[str]:
    """
    Traduce la entrada de talla del usuario en el filtro lateral a las posibles
    representaciones normalizadas tanto de Ropa ('CH', 'M', 'G', 'EG', '32') como de Calzado ('29.0').

    Args:
        entrada_talla (str): Texto escrito por el usuario (ej. 'M', 'S', '32', 'talla 9', '25.5').

    Returns:
        List[str]: Lista de códigos de talla equivalentes a buscar en `talla_norm`.
    """
    txt: str = entrada_talla.strip().upper()
    if not txt:
        return []

    equivalencias_ropa: Dict[str, str] = {
        "XXS": "2XCH", "2XCH": "2XCH",
        "XS": "XCH", "XCH": "XCH", "ECH": "XCH",
        "S": "CH", "CH": "CH", "CHICA": "CH",
        "M": "M", "MED": "M", "MEDIANA": "M",
        "L": "G", "G": "G", "GDE": "G", "GRANDE": "G",
        "XL": "EG", "EG": "EG", "XG": "EG",
        "XXL": "2EG", "2XL": "2EG", "2EG": "2EG",
        "3XL": "3EG", "3EG": "3EG",
        "UNI": "UNITALLA", "UNITALLA": "UNITALLA",
    }
    if txt in equivalencias_ropa:
        return [equivalencias_ropa[txt]]

    coincidencias = re.findall(r"\d+(?:\.\d+)?", txt)
    if not coincidencias:
        return [txt]

    valor: float = float(coincidencias[0])
    candidatos: List[str] = [f"{valor:.1f}"]
    if valor.is_integer():
        candidatos.append(str(int(valor)))
    if 2.0 <= valor <= 11.5:
        candidatos.append(f"{valor + 20.0:.1f}")
    return list(dict.fromkeys(candidatos))


def inicializar_estado_sesion(forzar_recarga: bool = False) -> None:
    """Inicializa el DataFrame maestro en st.session_state para garantizar reactividad."""
    if forzar_recarga or "df_master" not in st.session_state:
        df_p, df_t = leer_tablas_procesadas_sqlite()
        st.session_state["df_master"] = df_p
        st.session_state["df_tallas"] = df_t
        st.session_state["ids_vista_actual"] = []


def callback_sincronizar_checkboxes() -> None:
    """Actualiza st.session_state al marcar o desmarcar casillas en el editor sin reiniciar."""
    estado_editor: dict = st.session_state.get("editor_matriz_bi", {})
    filas_editadas: dict = estado_editor.get("edited_rows", {})
    ids_vista: List[str] = st.session_state.get("ids_vista_actual", [])

    if not filas_editadas or not ids_vista:
        return

    df_master: pd.DataFrame = st.session_state["df_master"]
    for idx_pos_str, cambios_dict in filas_editadas.items():
        idx_pos: int = int(idx_pos_str)
        if 0 <= idx_pos < len(ids_vista) and "subir_ml" in cambios_dict:
            sku_objetivo: str = ids_vista[idx_pos]
            nuevo_valor_bool: bool = bool(cambios_dict["subir_ml"])
            df_master.loc[df_master["id_ps"] == sku_objetivo, "subir_ml"] = nuevo_valor_bool


def guardar_seleccion_en_sqlite() -> None:
    """Persiste el estado de 'subir_ml' en la base de datos SQLite."""
    df_master: pd.DataFrame = st.session_state.get("df_master", pd.DataFrame())
    if df_master.empty:
        return

    conn = sqlite3.connect(RUTA_BD, timeout=20)
    pares_actualizacion = [
        (1 if bool(fila["subir_ml"]) else 0, str(fila["id_ps"]))
        for _, fila in df_master.iterrows()
    ]
    conn.executemany(
        "UPDATE zapatos_candidatos SET apto_publicar = ? WHERE CAST(id_ps AS TEXT) = ?;",
        pares_actualizacion,
    )
    conn.commit()
    conn.close()


def renderizar_dashboard_bi() -> None:
    """Renderiza el panel BI multi-categoría (Ropa y Calzado) en Streamlit."""
    inicializar_estado_sesion()

    df_master: pd.DataFrame = st.session_state["df_master"]
    df_tallas: pd.DataFrame = st.session_state["df_tallas"]

    if df_master.empty:
        st.error("⚠️ No se encontraron artículos procesados. Ejecuta primero `python paso5_motor_financiero.py`.")
        return

    with st.sidebar:
        st.markdown("### 🎛️ Segmentadores BI (Ropa y Calzado)")
        if st.button("🔄 Recargar Datos desde SQLite", use_container_width=True):
            inicializar_estado_sesion(forzar_recarga=True)
            st.rerun()

        filtro_semaforo = st.selectbox(
            "📌 Estado / Semáforo:",
            options=[
                "Todos los Modelos Auditados",
                "✅ Solo Recomendados",
                "🚀 Seleccionados para ML (Subir = True)",
                "⚠️ No Recomendados",
            ],
            index=0,
        )

        generos_disp = sorted(df_master["genero"].unique().tolist())
        sel_generos = st.multiselect("👥 Género:", options=generos_disp, default=generos_disp)

        cats_disp = sorted(df_master["categoria"].unique().tolist())
        sel_categorias = st.multiselect("👗👟 Categoría (Ropa / Calzado):", options=cats_disp, default=cats_disp)

        colores_disp = sorted(df_master["color"].unique().tolist())
        sel_colores = st.multiselect("🎨 Color:", options=colores_disp, default=colores_disp)

        max_costo = float(max(500.0, df_master["costo_socio"].max()))
        rango_costo = st.slider("💵 Rango de Costo ($ MXN):", 0.0, max_costo, (0.0, max_costo), 25.0)

        max_util = float(max(300.0, df_master["utilidad_neta_ml"].max()))
        rango_utilidad = st.slider("📈 Rango de Ganancia Neta ($ MXN):", 0.0, max_util, (0.0, max_util), 20.0)

        entrada_talla_usuario = st.text_input(
            "📏 Filtrar Talla Exacta (ej. 'CH', 'M', 'G', '32', '25.5' o '9'):",
            placeholder="Escribe talla de ropa o calzado...",
        )
        solo_4_fotos = st.checkbox("📸 Exigir 4 Fotos HD Listas", value=False)

    df_filtrado: pd.DataFrame = df_master.copy()

    if filtro_semaforo == "✅ Solo Recomendados":
        df_filtrado = df_filtrado[df_filtrado["semaforo"] == "✅ RECOMENDADO"]
    elif filtro_semaforo == "🚀 Seleccionados para ML (Subir = True)":
        df_filtrado = df_filtrado[df_filtrado["subir_ml"]]
    elif filtro_semaforo == "⚠️ No Recomendados":
        df_filtrado = df_filtrado[df_filtrado["semaforo"] == "⚠️ NO RECOMENDADO"]

    if sel_generos:
        df_filtrado = df_filtrado[df_filtrado["genero"].isin(sel_generos)]
    if sel_categorias:
        df_filtrado = df_filtrado[df_filtrado["categoria"].isin(sel_categorias)]
    if sel_colores:
        df_filtrado = df_filtrado[df_filtrado["color"].isin(sel_colores)]

    df_filtrado = df_filtrado[
        (df_filtrado["costo_socio"] >= rango_costo[0])
        & (df_filtrado["costo_socio"] <= rango_costo[1])
        & (df_filtrado["utilidad_neta_ml"] >= rango_utilidad[0])
        & (df_filtrado["utilidad_neta_ml"] <= rango_utilidad[1])
    ]

    if solo_4_fotos:
        df_filtrado = df_filtrado[df_filtrado["fotos_ok"]]

    if entrada_talla_usuario.strip() and not df_tallas.empty:
        tallas_buscadas: List[str] = mapear_talla_busqueda_ui(entrada_talla_usuario)
        if tallas_buscadas:
            skus_con_talla = df_tallas[
                (df_tallas["talla_norm"].isin(tallas_buscadas))
                & (df_tallas["stock_seguro_publicable"] > 0)
            ]["id_ps"].unique()
            df_filtrado = df_filtrado[df_filtrado["id_ps"].isin(skus_con_talla)]
            st.sidebar.info(f"📏 Talla buscada con stock publicable: **{', '.join(tallas_buscadas)}**")

    st.session_state["ids_vista_actual"] = df_filtrado["id_ps"].tolist()

    st.title("🛍️ Centro de Control BI · Quant-Commerce Vallejo (Ropa y Calzado)")
    st.caption("Enfoque interno multi-categoría: Unit Economics diferenciados por prenda/calzado y Doble Blindaje de Stock.")

    df_seleccionados_ml = df_filtrado[df_filtrado["subir_ml"]]
    base_promedios = df_seleccionados_ml if not df_seleccionados_ml.empty else df_filtrado

    costo_prom: float = float(base_promedios["costo_socio"].mean()) if not base_promedios.empty else 0.0
    ganancia_prom: float = float(base_promedios["utilidad_neta_ml"].mean()) if not base_promedios.empty else 0.0
    pares_pub_tot: int = int(df_filtrado["stock_publicable"].sum())
    pares_fis_tot: int = int(df_filtrado["stock_fisico"].sum())

    k1, k2, k3, k4, k5 = st.columns(5)
    k1.metric("📦 Modelos Filtrados", f"{len(df_filtrado)} SKUs")
    k2.metric("🚀 Activos para ML", f"{len(df_seleccionados_ml)} SKUs", f"De {int(df_master['subir_ml'].sum())} totales")
    k3.metric("💵 Costo Unitario Prom.", f"${costo_prom:,.0f} MXN", "En sucursal Vallejo")
    k4.metric("💰 Ganancia Neta Prom.", f"${ganancia_prom:,.2f} MXN", "Libre por unidad")
    k5.metric("🛡️ Unidades Publicables", f"{pares_pub_tot:,} pzas", f"De {pares_fis_tot:,} físicas")

    st.markdown("---")

    st.markdown("### 📋 Matriz de Selección y Diagnóstico Interno")
    columnas_visibles = [
        "subir_ml",
        "id_ps",
        "sku_interno_ml",
        "nombre_modelo",
        "categoria",
        "url_real",
        "semaforo",
        "fotos_status",
        "costo_socio",
        "precio_ml",
        "utilidad_neta_ml",
        "iva_a_favor_ml",
        "stock_publicable",
        "tallas_seguras_detalle",
        "motivo_diagnostico",
    ]

    st.data_editor(
        df_filtrado[columnas_visibles],
        column_config={
            "subir_ml": st.column_config.CheckboxColumn("🚀 Subir a ML"),
            "id_ps": st.column_config.TextColumn("ID Price Shoes", disabled=True),
            "sku_interno_ml": st.column_config.TextColumn("SKU Oculto ML", disabled=True),
            "nombre_modelo": st.column_config.TextColumn("Artículo", disabled=True),
            "categoria": st.column_config.TextColumn("Categoría", disabled=True),
            "url_real": st.column_config.LinkColumn("Ficha Price Shoes", display_text="🔗 Ver Original", disabled=True),
            "semaforo": st.column_config.TextColumn("Semáforo", disabled=True),
            "fotos_status": st.column_config.TextColumn("Fotos HD", disabled=True),
            "costo_socio": st.column_config.NumberColumn("Costo Vallejo", format="$%.0f", disabled=True),
            "precio_ml": st.column_config.NumberColumn("Precio Sugerido", format="$%.0f", disabled=True),
            "utilidad_neta_ml": st.column_config.NumberColumn("Ganancia Neta", format="$%.2f", disabled=True),
            "iva_a_favor_ml": st.column_config.NumberColumn("IVA a Favor", format="$%.2f", disabled=True),
            "stock_publicable": st.column_config.NumberColumn("Pzas Libres", disabled=True),
            "tallas_seguras_detalle": st.column_config.TextColumn("Tallas Publicables (>6 pzas)", disabled=True),
            "motivo_diagnostico": st.column_config.TextColumn("Diagnóstico Técnico", disabled=True),
        },
        use_container_width=True,
        hide_index=True,
        height=370,
        key="editor_matriz_bi",
        on_change=callback_sincronizar_checkboxes,
    )

    if st.button("💾 Guardar Selección de 'Subir a ML' en Base de Datos SQLite", type="primary"):
        guardar_seleccion_en_sqlite()
        st.success("✅ Selección guardada exitosamente.")

    st.markdown("---")

    st.markdown("### 🔍 Inspector Unitario de Artículo (Drill-Down)")
    if df_filtrado.empty:
        st.info("Ajusta los filtros laterales.")
        return

    opciones_selector = (
        df_filtrado["id_ps"]
        + " · ["
        + df_filtrado["categoria"]
        + "] "
        + df_filtrado["nombre_modelo"]
        + " ("
        + df_filtrado["semaforo"]
        + ")"
    ).tolist()
    seleccion_actual = st.selectbox("Selecciona un artículo:", options=opciones_selector)
    sku_sel: str = seleccion_actual.split(" · ")[0]
    fila_sku = df_master[df_master["id_ps"] == sku_sel].iloc[0]

    col_link1, col_diag = st.columns([3, 9])
    with col_link1:
        if fila_sku["url_real"]:
            st.link_button("🔗 Ver en Price Shoes", url=str(fila_sku["url_real"]), use_container_width=True)
    with col_diag:
        st.info(f"**{fila_sku['semaforo']}** — {fila_sku['motivo_diagnostico']}")

    col_fin_unit, col_tallas_unit = st.columns([6, 6])
    with col_fin_unit:
        st.markdown(f"#### 💰 Desglose Unitario · SKU `{sku_sel}` ({fila_sku['categoria']})")
        df_tabla_fin = pd.DataFrame(
            [
                {"Rubro": "1. (+) Precio de Venta Sugerido en ML", "Monto": f"${fila_sku['precio_ml']:,.2f}"},
                {"Rubro": "2. (-) Costo de Socio en Vallejo", "Monto": f"-${fila_sku['costo_socio']:,.2f}"},
                {"Rubro": "3. (-) Comisión Clásica ML (17.5%)", "Monto": f"-${fila_sku['comision_ml_pesos']:,.2f}"},
                {"Rubro": "4. (-) Costo de Envío (Mercado Envíos)", "Monto": f"-${fila_sku['envio_ml_pesos']:,.2f}"},
                {"Rubro": "5. (-) Retención SAT Efectiva (IVA+ISR)", "Monto": f"-${fila_sku['impuestos_sat_pesos']:,.2f}"},
                {"Rubro": "6. (-) Fondo de Merma (3% s/costo)", "Monto": f"-${fila_sku['fondo_merma_ml']:,.2f}"},
                {"Rubro": "💰 (=) GANANCIA NETA LIBRE POR UNIDAD", "Monto": f"${fila_sku['utilidad_neta_ml']:,.2f} MXN"},
                {"Rubro": "🏦 IVA a Favor Acreditable Estimado", "Monto": f"${fila_sku['iva_a_favor_ml']:,.2f} MXN"},
            ]
        )
        st.table(df_tabla_fin)

    with col_tallas_unit:
        st.markdown(f"#### 📏 Stock por Talla en Vallejo · SKU `{sku_sel}`")
        tallas_sku = df_tallas[df_tallas["id_ps"] == sku_sel].copy()
        if tallas_sku.empty:
            st.warning("No hay lecturas de tallas registradas para este SKU.")
        else:
            st.dataframe(
                tallas_sku[["talla_norm", "stock_vallejo", "reserva_operativa", "stock_seguro_publicable", "fecha"]].rename(
                    columns={
                        "talla_norm": "Talla",
                        "stock_vallejo": "Físico Vallejo",
                        "reserva_operativa": "Candado Fijo",
                        "stock_seguro_publicable": "Publicable ML",
                        "fecha": "Última Lectura",
                    }
                ),
                use_container_width=True,
                hide_index=True,
                height=340,
            )

    st.markdown(f"#### 📸 Galería del Estudio Fotográfico HD ({fila_sku['fotos_status']})")
    carpeta_sku = DIR_CATALOGO / sku_sel
    archivos_galeria = [
        ("1. Portada Fondo Blanco", carpeta_sku / "01_portada_blanca.jpg"),
        ("2. Ángulo de Detalle", carpeta_sku / "02_angulo_galeria.jpg"),
        ("3. Ficha de Beneficios", carpeta_sku / "03_ficha_beneficios.jpg"),
        ("4. Guía de Tallas", carpeta_sku / "04_guia_tallas_cm.jpg"),
    ]
    columnas_fotos = st.columns(4)
    for idx, (titulo_img, ruta_img) in enumerate(archivos_galeria):
        with columnas_fotos[idx]:
            if ruta_img.exists():
                st.image(str(ruta_img), caption=titulo_img, use_container_width=True)
            else:
                st.warning(f"Falta: `{ruta_img.name}`")


if __name__ == "__main__":
    renderizar_dashboard_bi()