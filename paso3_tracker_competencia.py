"""
=========================================================================================
PASO 3.5: MOTOR DE TRACKING DE COMPETENCIA VÍA BÚSQUEDA WEB DE MERCADO LIBRE
=========================================================================================
Responsabilidad Única (SRP):
Consultar los precios de referencia de Mercado Libre utilizando headers de navegador
legítimos para evitar bloqueos 403 Forbidden en el análisis de competencia.
=========================================================================================
"""

from __future__ import annotations

import logging
import sqlite3
import time
from pathlib import Path
from typing import Dict, List, Tuple
from urllib.parse import quote_plus

import pandas as pd
import requests

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("TrackerCompetenciaML")

RUTA_BD: Path = Path("data/zapateria_vallejo.db")
SITIO_ML: str = "MLM"
LIMITE_TOP_RESULTADOS: int = 5
PAUSA_ENTRE_CONSULTAS_SEG: float = 2.0


def asegurar_columnas_competencia(conn: sqlite3.Connection) -> None:
    """Verifica e inyecta las columnas de competencia en la base de datos."""
    columnas_actuales: List[str] = [
        col[1] for col in conn.execute("PRAGMA table_info(zapatos_candidatos)").fetchall()
    ]
    nuevas_columnas: Dict[str, str] = {
        "precio_minimo_ml": "REAL DEFAULT 0.0",
        "precio_promedio_ml": "REAL DEFAULT 0.0",
        "url_competencia": "TEXT DEFAULT ''",
    }
    for nombre_col, definicion_sql in nuevas_columnas.items():
        if nombre_col not in columnas_actuales:
            conn.execute(f"ALTER TABLE zapatos_candidatos ADD COLUMN {nombre_col} {definicion_sql};")
    conn.commit()


def consultar_api_mercado_libre(termino_busqueda: str) -> Tuple[float, float, str]:
    """Consulta la API de Mercado Libre con headers completos de navegador para evitar 403."""
    termino_limpio: str = termino_busqueda.strip()[:45]
    if not termino_limpio:
        return 0.0, 0.0, ""

    query_codificada: str = quote_plus(f"calzado {termino_limpio}")
    endpoint: str = (
        f"https://api.mercadolibre.com/sites/{SITIO_ML}/search"
        f"?q={query_codificada}&limit={LIMITE_TOP_RESULTADOS}"
    )

    # Headers simulando un navegador real para superar el firewall de la API
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "es-MX,es;q=0.9",
        "Referer": "https://www.mercadolibre.com.mx/",
    }

    try:
        respuesta = requests.get(endpoint, headers=headers, timeout=10)
        if respuesta.status_code != 200:
            logger.warning("API ML respondió con código %s para '%s'", respuesta.status_code, termino_limpio)
            return 0.0, 0.0, ""

        payload: dict = respuesta.json()
        resultados: List[dict] = payload.get("results", [])[:LIMITE_TOP_RESULTADOS]
        if not resultados:
            return 0.0, 0.0, ""

        precios_validos: List[float] = []
        url_lider: str = ""

        for item in resultados:
            precio_raw = item.get("price")
            permalink = str(item.get("permalink") or "").strip()
            if precio_raw is not None:
                try:
                    precio_val = float(precio_raw)
                    if precio_val > 100.0:
                        precios_validos.append(precio_val)
                        if not url_lider and permalink.startswith("http"):
                            url_lider = permalink
                except (ValueError, TypeError):
                    continue

        if not precios_validos:
            return 0.0, 0.0, ""

        precio_minimo: float = round(min(precios_validos), 2)
        precio_promedio: float = round(sum(precios_validos) / len(precios_validos), 2)
        return precio_minimo, precio_promedio, url_lider

    except Exception as exc:
        logger.warning("Excepción consultando API ML ('%s'): %s", termino_limpio, exc)
        return 0.0, 0.0, ""


def ejecutar_tracking_competencia() -> None:
    """Orquesta la lectura de modelos y la actualización de precios de referencia."""
    if not RUTA_BD.exists():
        logger.error("No existe la base de datos en %s", RUTA_BD)
        return

    conn = sqlite3.connect(RUTA_BD, timeout=20)
    asegurar_columnas_competencia(conn)

    df_modelos: pd.DataFrame = pd.read_sql_query(
        """
        SELECT CAST(id_ps AS TEXT) AS id_ps,
               COALESCE(nombre_modelo, '') AS nombre_modelo
        FROM zapatos_candidatos
        WHERE COALESCE(costo_socio, 0) > 0
           OR COALESCE(stock_total_anterior, 0) > 0
        """,
        conn,
    )

    if df_modelos.empty:
        logger.info("No hay modelos con costo o stock auditado para consultar en Mercado Libre.")
        conn.close()
        return

    logger.info("Consultando benchmark en Mercado Libre para %d modelos auditados...", len(df_modelos))
    actualizados: int = 0

    for _, fila in df_modelos.iterrows():
        id_ps: str = str(fila["id_ps"]).strip()
        nombre_modelo: str = str(fila["nombre_modelo"]).strip()

        p_min, p_prom, url_comp = consultar_api_mercado_libre(nombre_modelo)

        if p_prom > 0.0:
            conn.execute(
                """
                UPDATE zapatos_candidatos
                SET precio_minimo_ml = ?,
                    precio_promedio_ml = ?,
                    url_competencia = ?
                WHERE CAST(id_ps AS TEXT) = ?
                """,
                (p_min, p_prom, url_comp, id_ps),
            )
            actualizados += 1

        time.sleep(PAUSA_ENTRE_CONSULTAS_SEG)

    conn.commit()
    conn.close()
    logger.info("Tracking finalizado: %d modelos actualizados con precios de competencia.", actualizados)


if __name__ == "__main__":
    ejecutar_tracking_competencia()