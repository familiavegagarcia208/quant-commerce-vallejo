"""
=========================================================================================
PASO 5: MOTOR FINANCIERO Y MATEMÁTICO DE DOBLE BLINDAJE (ANTI-DEVOLUCIONES + BEST-SELLERS)
=========================================================================================
REGLAS DE ARQUITECTURA CORREGIDAS:
1. PISO FÍSICO ANTI-DEVOLUCIONES POR TALLA (`PISO_FANTASMA_TALLA = 5`):
   Cualquier talla con <= 5 pares en Vallejo se considera stock de riesgo (devoluciones,
   exhibición o cajas extraviadas) y recibe `stock_seguro_publicable = 0`.
2. COLCHÓN COMPUESTO DE POISSON (95% CONFIANZA):
   Sobre los 5 pares intocables, se descuenta además la demanda esperada de Poisson
   durante el tiempo de recolección: Colchón = 5 + ceil(lambda + 1.65 * sqrt(lambda)).
3. FILTRO DE ÉLITE MENSUAL (`apto_publicar = 1`):
   Solo autoriza los productos que más se venden (Tier S y A por rotación Bayesiana UCB
   y profundidad de lote), con mínimo 2 tallas activas y >= 6 pares libres post-blindaje.
=========================================================================================
"""
from __future__ import annotations

import math
import sqlite3
from pathlib import Path

RUTA_BD = Path("data/zapateria_vallejo.db")

# ---------------------------------------------------------------------------
# Parámetros del Nuevo Blindaje Físico y Estadístico
# ---------------------------------------------------------------------------
PISO_FANTASMA_TALLA = 5       # Los primeros 5 pares de cada talla NO se tocan (devoluciones/exhibición)
Z_CONF_95 = 1.645             # Cuantil normal para 95% de confianza unilateral
TAU_DIAS = 1.0                # Ventana de exposición antes de recolección en tienda (días)
LAMBDA_PRIOR_TALLA = 0.25     # Tasa previa conservadora por talla cuando inicia el historial
MIN_TALLAS_SEGURAS = 2        # Mínimo de tallas que deben superar el blindaje de 5 + Poisson
MIN_PARES_PUBLICABLES = 6     # Mínimo de pares libres totales después de descontar todo el blindaje
TIERS_PERMITIDOS = ("S", "A") # Solo trabajar con los productos estrella (Tier S y Tier A)

# ---------------------------------------------------------------------------
# Parámetros Financieros y Fiscales (México - Persona Física con Actividad Empresarial)
# ---------------------------------------------------------------------------
IVA_TASA = 0.16
COMISION_ML_CLASICA = 0.175
COMISION_AMAZON = 0.15
COMISION_TIKTOK = 0.08
RETENCION_IVA_ML = 0.08       # Mitad del IVA (8%)
RETENCION_ISR_ML = 0.025      # 2.5% sobre base sin IVA -> Total efectivo ~7.76% sobre precio bruto
RESERVA_MERMA_PCT = 0.02      # 2% de fondo de autoseguro para devoluciones
MARGEN_OBJETIVO_ML = 0.22     # Margen libre objetivo sobre precio de venta


def asegurar_columnas_financieras(conn: sqlite3.Connection) -> None:
    cols_cand = [c[1] for c in conn.execute("PRAGMA table_info(zapatos_candidatos)").fetchall()]
    nuevas_cand = {
        "velocidad_diaria": "REAL DEFAULT 0",
        "score_ucb": "REAL DEFAULT 0",
        "tier": "TEXT DEFAULT 'C'",
        "tallas_seguras_count": "INTEGER DEFAULT 0",
        "stock_publicable_total": "INTEGER DEFAULT 0",
        "precio_piso_ml": "REAL DEFAULT 0",
        "precio_ml": "REAL DEFAULT 0",
        "utilidad_neta_ml": "REAL DEFAULT 0",
        "fondo_merma_ml": "REAL DEFAULT 0",
        "iva_a_favor_ml": "REAL DEFAULT 0",
        "precio_amazon": "REAL DEFAULT 0",
        "utilidad_neta_amazon": "REAL DEFAULT 0",
        "precio_tiktok": "REAL DEFAULT 0",
        "utilidad_neta_tiktok": "REAL DEFAULT 0",
        "apto_publicar": "INTEGER DEFAULT 0",
    }
    for col, tipo in nuevas_cand.items():
        if col not in cols_cand:
            conn.execute(f"ALTER TABLE zapatos_candidatos ADD COLUMN {col} {tipo};")

    cols_hist = [c[1] for c in conn.execute("PRAGMA table_info(historial_stock_21_dias)").fetchall()]
    if "stock_seguro_publicable" not in cols_hist:
        conn.execute(
            "ALTER TABLE historial_stock_21_dias ADD COLUMN stock_seguro_publicable INTEGER DEFAULT 0;"
        )
    conn.commit()


def estimar_envio_ml(costo_socio: float) -> float:
    if costo_socio < 350:
        return 98.0
    if costo_socio < 650:
        return 112.0
    return 128.0


def calcular_finanzas_omnicanal(costo_socio: float) -> dict[str, float]:
    if costo_socio <= 0:
        return {
            "precio_piso_ml": 0.0,
            "precio_ml": 0.0,
            "utilidad_neta_ml": 0.0,
            "fondo_merma_ml": 0.0,
            "iva_a_favor_ml": 0.0,
            "precio_amazon": 0.0,
            "utilidad_neta_amazon": 0.0,
            "precio_tiktok": 0.0,
            "utilidad_neta_tiktok": 0.0,
        }

    envio_ml = estimar_envio_ml(costo_socio)
    retencion_efectiva_bruta = (RETENCION_IVA_ML + RETENCION_ISR_ML) / (1.0 + IVA_TASA)
    descuento_total_pct = COMISION_ML_CLASICA + retencion_efectiva_bruta + RESERVA_MERMA_PCT

    # Precio piso de guerra (punto de equilibrio + merma cubierta + $50 MXN de ganancia mínima)
    piso_bruto = (costo_socio + envio_ml + 50.0) / (1.0 - descuento_total_pct)
    precio_piso_ml = math.ceil(piso_bruto / 10.0) * 10.0 - 1.0

    # Precio óptimo comercial (redondeado a terminación X9.00)
    optimo_bruto = (costo_socio + envio_ml) / (1.0 - descuento_total_pct - MARGEN_OBJETIVO_ML)
    precio_ml = max(precio_piso_ml + 30.0, math.ceil(optimo_bruto / 10.0) * 10.0 - 1.0)

    comision_ml_pesos = precio_ml * COMISION_ML_CLASICA
    retenciones_pesos = (precio_ml / (1.0 + IVA_TASA)) * (RETENCION_IVA_ML + RETENCION_ISR_ML)
    fondo_merma_ml = round(precio_ml * RESERVA_MERMA_PCT, 2)

    # Liquidez pura que entra a tu cuenta bancaria descontando el costo del zapato y el fondo de merma
    utilidad_neta_ml = round(
        precio_ml - costo_socio - comision_ml_pesos - envio_ml - retenciones_pesos - fondo_merma_ml, 2
    )

    # IVA acreditable a favor (IVA pagado en Price Shoes + IVA de comisión/envío ML)
    iva_compras = (costo_socio + comision_ml_pesos + envio_ml) * (IVA_TASA / (1.0 + IVA_TASA))
    iva_retenido = (precio_ml / (1.0 + IVA_TASA)) * RETENCION_IVA_ML
    iva_trasladado = precio_ml * (IVA_TASA / (1.0 + IVA_TASA))
    iva_a_favor_ml = round(max(0.0, (iva_compras + iva_retenido) - iva_trasladado), 2)

    # Amazon y TikTok Shop
    precio_amazon = math.ceil((precio_ml * 0.98) / 10.0) * 10.0 - 1.0
    utilidad_neta_amazon = round(
        precio_amazon * (1.0 - COMISION_AMAZON - retencion_efectiva_bruta - RESERVA_MERMA_PCT)
        - costo_socio
        - envio_ml,
        2,
    )

    precio_tiktok = math.ceil((precio_ml * 0.92) / 10.0) * 10.0 - 1.0
    utilidad_neta_tiktok = round(
        precio_tiktok * (1.0 - COMISION_TIKTOK - retencion_efectiva_bruta - RESERVA_MERMA_PCT)
        - costo_socio
        - 95.0,
        2,
    )

    return {
        "precio_piso_ml": round(precio_piso_ml, 2),
        "precio_ml": round(precio_ml, 2),
        "utilidad_neta_ml": utilidad_neta_ml,
        "fondo_merma_ml": fondo_merma_ml,
        "iva_a_favor_ml": iva_a_favor_ml,
        "precio_amazon": round(precio_amazon, 2),
        "utilidad_neta_amazon": utilidad_neta_amazon,
        "precio_tiktok": round(precio_tiktok, 2),
        "utilidad_neta_tiktok": utilidad_neta_tiktok,
    }


def calcular_stock_seguro_talla(stock_fisico: int, lambda_talla: float) -> int:
    """
    Aplica el Doble Blindaje:
    1. Si en tienda hay <= 5 pares (PISO_FANTASMA_TALLA), devuelve 0 de inmediato.
    2. Si hay > 5 pares, resta los 5 pares de devoluciones/exhibición Y ADEMÁS resta
       el colchón superior de Poisson al 95% de confianza.
    """
    if stock_fisico <= PISO_FANTASMA_TALLA:
        return 0

    lam = max(LAMBDA_PRIOR_TALLA, float(lambda_talla))
    colchon_poisson = math.ceil(lam * TAU_DIAS + Z_CONF_95 * math.sqrt(lam * TAU_DIAS))
    colchon_total = PISO_FANTASMA_TALLA + max(1, colchon_poisson)

    return max(0, int(stock_fisico) - colchon_total)


def ejecutar_motor_doble_blindaje() -> None:
    print("=" * 92)
    print("🛡️ PASO 5: MOTOR DE DOBLE BLINDAJE (PISO > 5 PARES POR TALLA + POISSON + BEST-SELLERS)")
    print("=" * 92)

    if not RUTA_BD.exists():
        print(f"❌ No existe la base de datos en {RUTA_BD}.")
        return

    conn = sqlite3.connect(RUTA_BD)
    conn.execute("PRAGMA journal_mode=WAL;")
    asegurar_columnas_financieras(conn)

    candidatos = conn.execute(
        "SELECT id_ps, nombre_modelo, costo_socio, stock_total_anterior, velocidad_diaria FROM zapatos_candidatos"
    ).fetchall()

    # 1. Calcular rotación real por SKU y por talla desde el historial de lecturas
    metricas_sku: list[dict] = []

    for id_ps, nombre, costo, stock_ant, vel_actual in candidatos:
        id_str = str(id_ps)
        costo_f = float(costo or 0.0)
        stock_f = int(stock_ant or 0)

        lecturas = conn.execute(
            """
            SELECT fecha, talla, stock_vallejo
            FROM historial_stock_21_dias
            WHERE id_ps = ?
            ORDER BY talla, fecha ASC
            """,
            (id_str,),
        ).fetchall()

        # Agrupar lecturas por talla para medir caídas de stock (ventas en Vallejo)
        por_talla: dict[str, list[tuple[str, int]]] = {}
        fechas_set: set[str] = set()
        for fecha_s, talla_s, stk_v in lecturas:
            t_key = str(talla_s)
            por_talla.setdefault(t_key, []).append((str(fecha_s), int(stk_v or 0)))
            fechas_set.add(str(fecha_s))

        ultima_fecha = max(fechas_set) if fechas_set else None
        num_intervalos = max(1, len(fechas_set) - 1)

        ventas_totales_detectadas = 0
        stock_publicable_sku = 0
        tallas_seguras_sku = 0

        for t_key, serie in por_talla.items():
            caidas_talla = 0
            for i in range(1, len(serie)):
                delta = serie[i - 1][1] - serie[i][1]
                if delta > 0:
                    caidas_talla += delta
            ventas_totales_detectadas += caidas_talla

            # Estimador Bayesiano Gamma-Poisson por talla
            lambda_talla = (0.35 + caidas_talla) / (1.0 + num_intervalos * 0.5)

            # Aplicar blindaje a la última lectura de esta talla
            fecha_ult_talla, stock_ult_talla = serie[-1]
            if fecha_ult_talla == ultima_fecha:
                seguro_talla = calcular_stock_seguro_talla(stock_ult_talla, lambda_talla)
                stock_publicable_sku += seguro_talla
                if seguro_talla > 0:
                    tallas_seguras_sku += 1

                conn.execute(
                    """
                    UPDATE historial_stock_21_dias
                    SET stock_seguro_publicable = ?
                    WHERE id_ps = ? AND talla = ? AND fecha = ?
                    """,
                    (seguro_talla, id_str, t_key, ultima_fecha),
                )

        # Velocidad diaria Bayesiana + Score UCB (prioriza best-sellers comprobados y lotes profundos)
        alpha_post = 1.2 + ventas_totales_detectadas + (min(stock_f, 120) * 0.025)
        beta_post = 1.0 + (num_intervalos * 0.5)
        lambda_bayes = alpha_post / beta_post
        score_ucb = lambda_bayes + Z_CONF_95 * (math.sqrt(alpha_post) / beta_post)

        velocidad_final = round(max(float(vel_actual or 0.0), lambda_bayes), 2) if stock_f > 0 else 0.0

        metricas_sku.append({
            "id_ps": id_str,
            "nombre": str(nombre or ""),
            "costo": costo_f,
            "stock_fisico": stock_f,
            "velocidad": velocidad_final,
            "score_ucb": score_ucb,
            "stock_publicable_total": stock_publicable_sku,
            "tallas_seguras_count": tallas_seguras_sku,
        })

    # 2. Clasificar en Tiers (S = Top 15% Best-Sellers, A = Siguiente 25%, B = Resto con stock, C = Sin tracción)
    con_traccion = sorted(
        [m for m in metricas_sku if m["stock_publicable_total"] > 0 and m["costo"] > 0],
        key=lambda x: (x["score_ucb"], x["stock_publicable_total"]),
        reverse=True,
    )
    n_activos = len(con_traccion)
    corte_s = max(1, int(n_activos * 0.30))
    corte_a = max(2, int(n_activos * 0.70))

    mapa_tier: dict[str, str] = {}
    for rank_idx, item in enumerate(con_traccion):
        if rank_idx < corte_s:
            mapa_tier[item["id_ps"]] = "S"
        elif rank_idx < corte_a:
            mapa_tier[item["id_ps"]] = "A"
        else:
            mapa_tier[item["id_ps"]] = "B"

    # 3. Guardar resultados y autorizar SOLO los Best-Sellers con Stock Ultra-Seguro
    aptos_total = 0
    pares_seguros_totales = 0

    for m in metricas_sku:
        id_str = m["id_ps"]
        tier = mapa_tier.get(id_str, "B" if m["stock_fisico"] > 0 else "C")
        fin = calcular_finanzas_omnicanal(m["costo"])

        # Regla estricta para publicar este mes:
        # 1) Costo socio auditado y utilidad bancaria positiva
        # 2) Al menos MIN_TALLAS_SEGURAS (2+) tallas que superen los 5 pares fantasma + Poisson
        # 3) Al menos MIN_PARES_PUBLICABLES (6+) pares libres totales después del descuento
        # 4) Pertenecer a los productos estrella más vendidos (Tier S o Tier A)
        es_apto = (
            1
            if (
                m["costo"] > 0
                and fin["utilidad_neta_ml"] > 0
                and m["tallas_seguras_count"] >= MIN_TALLAS_SEGURAS
                and m["stock_publicable_total"] >= MIN_PARES_PUBLICABLES
                and tier in TIERS_PERMITIDOS
            )
            else 0
        )

        if es_apto == 1:
            aptos_total += 1
            pares_seguros_totales += m["stock_publicable_total"]

        conn.execute(
            """
            UPDATE zapatos_candidatos
            SET velocidad_diaria = ?,
                score_ucb = ?,
                tier = ?,
                tallas_seguras_count = ?,
                stock_publicable_total = ?,
                precio_piso_ml = ?,
                precio_ml = ?,
                utilidad_neta_ml = ?,
                fondo_merma_ml = ?,
                iva_a_favor_ml = ?,
                precio_amazon = ?,
                utilidad_neta_amazon = ?,
                precio_tiktok = ?,
                utilidad_neta_tiktok = ?,
                apto_publicar = ?
            WHERE id_ps = ?
            """,
            (
                m["velocidad"],
                round(m["score_ucb"], 3),
                tier,
                m["tallas_seguras_count"],
                m["stock_publicable_total"],
                fin["precio_piso_ml"],
                fin["precio_ml"],
                fin["utilidad_neta_ml"],
                fin["fondo_merma_ml"],
                fin["iva_a_favor_ml"],
                fin["precio_amazon"],
                fin["utilidad_neta_amazon"],
                fin["precio_tiktok"],
                fin["utilidad_neta_tiktok"],
                es_apto,
                id_str,
            ),
        )

    conn.commit()
    conn.close()

    print(f"✅ Regla de Exclusión Física activa: Tallas con <= {PISO_FANTASMA_TALLA} pares fueron puestas en 0.")
    print(f"✅ Filtro de Curva y Rotación: Mínimo {MIN_TALLAS_SEGURAS} tallas seguras y >= {MIN_PARES_PUBLICABLES} pares libres (Solo Tiers {TIERS_PERMITIDOS}).")
    print(f"🏆 SKUs Best-Sellers Aptos este mes: {aptos_total} modelos élite ({pares_seguros_totales:,} pares ultra-seguros).")
    print("=" * 92)


if __name__ == "__main__":
    ejecutar_motor_doble_blindaje()