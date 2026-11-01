"""
=========================================================================================
PASO 5: MOTOR FINANCIERO OMNICANAL + COLCHÓN MÁGICO DE INVENTARIO (POISSON)
=========================================================================================

ESTRATEGIA FINANCIERA Y FISCAL DOCUMENTADA: "LIQUIDEZ BANCARIA PURA + BONO DE IVA A FAVOR"

1. EL PROBLEMA DE LA LIQUIDEZ EN MARKETPLACES (MÉXICO):
   Cuando vendes un producto en Mercado Libre, Amazon o TikTok Shop con tu RFC dado de alta,
   la plataforma aplica por ley una retención directa sobre el precio sin IVA (Precio / 1.16):
   - 8% de IVA retenido (la mitad del 16% de IVA total).
   - 1% de ISR retenido.
   Esto equivale a un descuento directo en efectivo del ~7.76% sobre el precio final de venta
   [(0.08 + 0.01) / 1.16 = 0.077586]. La plataforma le envía ese dinero directamente al SAT
   antes de depositar tus ganancias en tu cuenta bancaria.

2. ¿CÓMO SE GENERA EL "IVA A FAVOR" (Y POR QUÉ NO LO USAMOS PARA INFLAR LA UTILIDAD LÍQUIDA)?
   En México, el IVA funciona por compensación (IVA Cobrado al Cliente menos IVA Pagado en Gastos):
   - IVA Trasladado (Cobrado): El 16% incluido dentro del precio al que vendes el zapato.
   - IVA Acreditable (Ya pagado por ti):
       a) El 16% de IVA incluido en tu factura de compra de Price Shoes (Costo Socio).
       b) El 16% de IVA incluido en la factura de comisión que te cobra el Marketplace.
       c) El 16% de IVA incluido en el costo de envío (Mercado Envíos / Logística) y empaque.

   Ejemplo práctico (Tenis de $479.00 vendido en $958.00 en Mercado Libre)[cite: 1]:
   - IVA total cobrado al cliente ($958 / 1.16 * 0.16)       = $132.14
   - Menos IVA que ya pagó Mercado Libre al SAT por ti (8%)  = -$66.07
   - IVA restante que en teoría deberías pagar al SAT (8%)   =  $66.07
   - Menos tu IVA Acreditable (Price Shoes $66.07 + Comisión ML $23.12 + Envío $16.97) = -$106.16
   - Resultado en declaración mensual: $66.07 - $106.16 = -$40.09 (SALDO DE IVA A FAVOR EN EL SAT).

3. REGLA DE ORO OPERATIVA DE ESTE SCRIPT:
   Para evitar descapitalización al ir a resurtir a la sucursal Vallejo:
   - La columna `utilidad_neta_ml` (Efectivo Libre en Banco) DESCUENTA DE GOLPE el 7.76% de
     retención del SAT y el 2% del Fondo de Merma como dinero que ya salió de tu bolsillo.
     Representa billetes 100% líquidos depositados en tu cuenta tras reponer el par de zapatos.
   - La columna `iva_a_favor_ml` calcula por separado el excedente de IVA acreditable que se
     acumula a tu favor en el SAT al facturar tus compras en Price Shoes. Este monto se trata
     como un "Bono Fiscal Contable" independiente del flujo de caja diario.
=========================================================================================
"""
import math
import sqlite3
from pathlib import Path

RUTA_BD = Path("data/zapateria_vallejo.db")

# ===========================================================================
# PARÁMETROS DE FLUJO DE EFECTIVO PURO (LIQUIDEZ BANCARIA)
# ===========================================================================
TASA_IVA_MEXICO = 0.16          # 16% tasa general de IVA en México
FONDO_MERMA = 0.02              # 2% intocable separado en efectivo para absorber devoluciones
RETENCION_SAT_DIRECTA = 0.0776  # 7.76% retenido en efectivo por la plataforma ((8% IVA + 1% ISR) / 1.16)
ROI_EFECTIVO_META = 0.185       # 18.5% de retorno líquido en banco sobre Costo Socio
ROI_EFECTIVO_PISO = 0.120       # 12.0% de retorno líquido mínimo para guerra de precios
COSTO_EMPAQUE = 15.0            # Bolsa de seguridad / cinta / etiqueta ($ MXN con IVA incluido)

CANALES = {
    "mercado_libre": {
        "comision": 0.175,      # 17.5% Publicación Clásica Calzado (IVA incluido)
        "envio": 108.0          # Mercado Envíos promedio en calzado (< 1 kg, IVA incluido)
    },
    "amazon": {
        "comision": 0.150,      # 15.0% Tarifa por referencia Calzado Amazon MX
        "envio": 105.0
    },
    "tiktok_shop": {
        "comision": 0.080,      # 8.0% Comisión TikTok Shop MX
        "envio": 85.0
    }
}

# Parámetros del "Colchón Mágico" (Seguridad de Stock en Vallejo vía Distribución de Poisson)
STOCK_MINIMO_PISO = 3           # Si en Vallejo hay <= 3 pares en una talla, se publica 0
DIAS_RESPUESTA_COMPRA = 1.5     # Días de margen para acudir a Vallejo a recoger el par vendido


def asegurar_columnas_financieras(conn: sqlite3.Connection) -> None:
    """Garantiza que las columnas financieras y contables existan en SQLite."""
    cols_cand = [c[1] for c in conn.execute("PRAGMA table_info(zapatos_candidatos)").fetchall()]
    nuevas_cand = {
        "stock_publicable_total": "INTEGER DEFAULT 0",
        "precio_piso_ml": "REAL DEFAULT 0.0",
        "precio_ml": "REAL DEFAULT 0.0",
        "utilidad_neta_ml": "REAL DEFAULT 0.0",
        "fondo_merma_ml": "REAL DEFAULT 0.0",
        "iva_a_favor_ml": "REAL DEFAULT 0.0",
        "precio_amazon": "REAL DEFAULT 0.0",
        "utilidad_neta_amazon": "REAL DEFAULT 0.0",
        "precio_tiktok": "REAL DEFAULT 0.0",
        "utilidad_neta_tiktok": "REAL DEFAULT 0.0",
        "apto_publicar": "INTEGER DEFAULT 0"
    }
    for col, tipo in nuevas_cand.items():
        if col not in cols_cand:
            conn.execute(f"ALTER TABLE zapatos_candidatos ADD COLUMN {col} {tipo};")

    cols_hist = [c[1] for c in conn.execute("PRAGMA table_info(historial_stock_21_dias)").fetchall()]
    if "stock_seguro_publicable" not in cols_hist:
        conn.execute("ALTER TABLE historial_stock_21_dias ADD COLUMN stock_seguro_publicable INTEGER DEFAULT 0;")

    conn.commit()


def calcular_colchon_magico_poisson(stock_fisico: int, velocidad_diaria_sku: float, num_tallas: int) -> int:
    """
    Calcula cuántos pares se pueden publicar sin riesgo de quiebre en piso.
    Descuenta del stock físico la demanda máxima esperada al 95% de confianza (Poisson).
    """
    if stock_fisico <= STOCK_MINIMO_PISO:
        return 0
    tallas_activas = max(1, num_tallas)
    lambda_talla = max(0.15, (velocidad_diaria_sku / tallas_activas)) * DIAS_RESPUESTA_COMPRA
    demanda_max_poisson = math.ceil(lambda_talla + 1.65 * math.sqrt(lambda_talla))
    colchon_seguridad = max(STOCK_MINIMO_PISO, demanda_max_poisson)
    return max(0, stock_fisico - colchon_seguridad)


def desglosar_iva_incluido(monto_con_iva: float) -> float:
    """Extrae el monto de IVA (16%) contenido dentro de un precio o costo bruto."""
    return monto_con_iva - (monto_con_iva / (1.0 + TASA_IVA_MEXICO))


def calcular_iva_a_favor_par(precio_venta: float, costo_socio: float, canal_key: str) -> float:
    """
    Calcula el saldo de IVA a favor generado en el SAT cuando facturas tu compra
    en Price Shoes y tus gastos de plataforma (Comisión + Envío + Empaque).
    NOTA: Este monto NO se suma al efectivo líquido bancario; es un crédito fiscal.
    """
    cfg = CANALES[canal_key]
    comision_bruta = precio_venta * cfg["comision"]

    # 1. El 8% de IVA que te queda por cubrir ante el SAT (porque el otro 8% ya te lo retuvo la plataforma)
    iva_total_venta = desglosar_iva_incluido(precio_venta)
    iva_retenido_plataforma = iva_total_venta * 0.50
    iva_pendiente_sat = iva_total_venta - iva_retenido_plataforma

    # 2. El IVA Acreditable que tú ya pagaste en tus compras y gastos facturados
    iva_acreditable_total = (
        desglosar_iva_incluido(costo_socio)
        + desglosar_iva_incluido(comision_bruta)
        + desglosar_iva_incluido(cfg["envio"])
        + desglosar_iva_incluido(COSTO_EMPAQUE)
    )

    # Si tu IVA acreditable supera al 8% pendiente del SAT, la diferencia es Saldo de IVA a Favor
    saldo_a_favor = max(0.0, iva_acreditable_total - iva_pendiente_sat)
    return round(saldo_a_favor, 2)


def calcular_precio_efectivo(
    costo_socio: float,
    canal_key: str,
    roi_liquido: float = ROI_EFECTIVO_META
) -> tuple[float, float, float, float]:
    """
    Devuelve: (precio_comercial, efectivo_libre_banco, monto_fondo_merma_2pct, iva_a_favor_sat).
    Descuenta de golpe toda la retención del SAT (7.76%), comisión, envío, empaque y 2% de merma.
    """
    if costo_socio <= 0:
        return 0.0, 0.0, 0.0, 0.0

    cfg = CANALES[canal_key]
    ganancia_efectivo_meta = costo_socio * roi_liquido
    salida_efectivo_fija = costo_socio + cfg["envio"] + COSTO_EMPAQUE + ganancia_efectivo_meta
    porcentaje_retenido = cfg["comision"] + RETENCION_SAT_DIRECTA + FONDO_MERMA

    precio_crudo = salida_efectivo_fija / (1.0 - porcentaje_retenido)
    # Redondeo comercial a la decena más cercana terminada en 9 (ej. $949.00)
    precio_comercial = round(precio_crudo / 10.0) * 10.0 - 1.0
    if precio_comercial < precio_crudo - 5.0:
        precio_comercial += 10.0

    monto_merma = round(precio_comercial * FONDO_MERMA, 2)
    comision_monto = precio_comercial * cfg["comision"]
    retencion_sat_monto = precio_comercial * RETENCION_SAT_DIRECTA

    efectivo_libre_banco = round(
        precio_comercial - (costo_socio + cfg["envio"] + COSTO_EMPAQUE) - comision_monto - retencion_sat_monto - monto_merma,
        2
    )
    iva_favor = calcular_iva_a_favor_par(precio_comercial, costo_socio, canal_key)

    return round(precio_comercial, 2), efectivo_libre_banco, monto_merma, iva_favor


def ejecutar_motor_financiero() -> None:
    print("=" * 84)
    print("💵 PASO 5: MOTOR FINANCIERO (FLUJO DE EFECTIVO PURO + AUDITORÍA DE IVA A FAVOR)")
    print("=" * 84)

    if not RUTA_BD.exists():
        print(f"❌ No existe la base de datos en {RUTA_BD}.")
        return

    conn = sqlite3.connect(RUTA_BD)
    conn.execute("PRAGMA journal_mode=WAL;")
    asegurar_columnas_financieras(conn)

    skus_auditados = conn.execute("""
        SELECT id_ps, nombre_modelo, costo_socio, velocidad_diaria, stock_total_anterior, tier
        FROM zapatos_candidatos
        WHERE costo_socio > 0
    """).fetchall()

    print(f"🔎 Calculando liquidez bancaria real e IVA a favor para {len(skus_auditados)} SKUs...\n")

    for id_ps, nombre, costo, vel, stock_tot, tier in skus_auditados:
        vel = vel if vel and vel > 0 else 0.5

        tallas_actuales = conn.execute("""
            SELECT rowid, talla, stock_vallejo
            FROM historial_stock_21_dias
            WHERE id_ps = ?
        """, (id_ps,)).fetchall()

        num_tallas_con_stock = sum(1 for _, _, s in tallas_actuales if s > 0)
        stock_publicable_acumulado = 0

        for rowid, talla, stock_val in tallas_actuales:
            seguro = calcular_colchon_magico_poisson(stock_val, vel, num_tallas_con_stock)
            stock_publicable_acumulado += seguro
            conn.execute("""
                UPDATE historial_stock_21_dias
                SET stock_seguro_publicable = ?
                WHERE rowid = ?
            """, (seguro, rowid))

        p_piso_ml, u_piso_ml, _, _ = calcular_precio_efectivo(costo, "mercado_libre", ROI_EFECTIVO_PISO)
        p_ml, u_ml, merma_ml, iva_fav_ml = calcular_precio_efectivo(costo, "mercado_libre", ROI_EFECTIVO_META)
        p_amz, u_amz, _, _ = calcular_precio_efectivo(costo, "amazon", ROI_EFECTIVO_META)
        p_tk, u_tk, _, _ = calcular_precio_efectivo(costo, "tiktok_shop", ROI_EFECTIVO_META)

        ref_competencia_x2 = costo * 2.0
        apto = 1 if (stock_publicable_acumulado >= 3 and u_ml >= 45.0 and tier != 'Z') else 0

        conn.execute("""
            UPDATE zapatos_candidatos
            SET stock_publicable_total = ?,
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
        """, (
            stock_publicable_acumulado,
            p_piso_ml, p_ml, u_ml, merma_ml, iva_fav_ml,
            p_amz, u_amz,
            p_tk, u_tk,
            apto, id_ps
        ))

        estado_str = "✅ APTO PARA PUBLICAR" if apto else "⏸️ EN OBSERVACIÓN / POCO STOCK"
        print(f"👞 SKU {id_ps} ({nombre}) | Costo Socio: ${costo:,.2f} | {estado_str}")
        print(f"   🛡️ Stock Vallejo: {stock_tot} pares -> Colchón Mágico Publicable: {stock_publicable_acumulado} pares")
        print(f"   🎯 Ref. Competencia (Costo x2): ${ref_competencia_x2:,.2f} | Piso Guerra ML: ${p_piso_ml:,.2f} (Efectivo: ${u_piso_ml:,.2f})")
        print(f"   🛒 Mercado Libre : ${p_ml:,.2f} -> 💵 Efectivo en Banco: ${u_ml:,.2f} | 🛡️ Merma 2%: ${merma_ml:,.2f} | 🏛️ Bono IVA a Favor SAT: +${iva_fav_ml:,.2f}")
        print(f"   📦 Amazon México : ${p_amz:,.2f} -> 💵 Efectivo en Banco: ${u_amz:,.2f}")
        print(f"   🎵 TikTok Shop   : ${p_tk:,.2f} -> 💵 Efectivo en Banco: ${u_tk:,.2f}\n")

    conn.commit()
    conn.close()
    print("✅ ¡Flujo de efectivo puro y crédito fiscal de IVA a favor guardados en SQLite!")


if __name__ == "__main__":
    ejecutar_motor_financiero()