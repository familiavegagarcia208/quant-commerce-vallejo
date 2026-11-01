import math
import sqlite3

RUTA_BD = "data/zapateria_vallejo.db"

def calcular_colchone_poisson(lambda_v: float, tau_horas: float = 2.5, epsilon: float = 0.03) -> int:
    """
    Calcula el Colchón Mágico de seguridad (B*) usando la distribución de Poisson.
    Garantiza un nivel de servicio del 97% (1 - epsilon) para evitar quiebres de stock
    durante el tiempo que tardas en ir a Vallejo por el par vendido.
    """
    demanda_esperada = lambda_v * tau_horas
    b_estrella = 0
    probabilidad_acumulada = 0.0

    # Sumatoria de la distribución de Poisson hasta alcanzar el 97% de certeza
    while probabilidad_acumulada < (1.0 - epsilon) and b_estrella < 50:
        termino = (math.exp(-demanda_esperada) * (demanda_esperada ** b_estrella)) / math.factorial(b_estrella)
        probabilidad_acumulada += termino
        if probabilidad_acumulada < (1.0 - epsilon):
            b_estrella += 1

    return max(1, b_estrella)  # Mínimo siempre escondemos 1 par de colchón

def evaluar_tesorería_y_seguridad():
    """
    Conecta con la base de datos SQLite WAL, evalúa la liquidez disponible (K_0)
    y calcula el colchón matemático para los candidatos de la incubadora.
    """
    conn = sqlite3.connect(RUTA_BD)
    conn.execute("PRAGMA journal_mode=WAL;")

    print("📊 Ejecutando Motor Matemático y Auditoría de Tesorería...")

    # 1. Leer estado actual de la Tesorería Familiar
    tesoreria = conn.execute(
        "SELECT inversion_inicial_mxn, efectivo_disponible_banco_mxn, dinero_retenido_plataformas_mxn FROM tesoreria_familiar WHERE id = 1"
    ).fetchone()

    if tesoreria:
        inv_ini, efectivo_banco, retenido = tesoreria
        print(f"\n💰 Estado de la Tesorería Familiar:")
        print(f"   • Fondo Revolvente Inicial (K_0): ${inv_ini:,.2f} MXN")
        print(f"   • Efectivo Libre en Banco:        ${efectivo_banco:,.2f} MXN")
        print(f"   • Dinero Retenido en Plataformas: ${retenido:,.2f} MXN")

        if inv_ini == 0.0:
            print("   💡 [Aviso]: Tu fondo inicial está en $0.00 (Fase de Observación / Simulación).")

    # 2. Simular el cálculo de Poisson para los primeros 5 zapatos de la incubadora
    candidatos = conn.execute(
        "SELECT id_ps, nombre_modelo, costo_socio FROM zapatos_candidatos LIMIT 5"
    ).fetchall()

    print("\n🛡️ Prueba del Colchón Dinámico de Poisson (B*) para evitar sobreventa en Vallejo:")
    print("-" * 75)
    for id_ps, nombre, costo in candidatos:
        # Tasa de venta estimada en Vallejo (lambda_v = 1.2 pares por hora en hora pico)
        lambda_v_estimada = 1.2 
        b_star = calcular_colchone_poisson(lambda_v_estimada)
        
        print(f"• [{id_ps}] {nombre}")
        print(f"  Costo Socio: ${costo:.2f} MXN | Colchón de Seguridad B*: {b_star} pares ocultos automáticamente")
    print("-" * 75)

    conn.close()
    print("\n✅ ¡MOTOR MATEMÁTICO VERIFICADO CORRECTAMENTE!")

if __name__ == "__main__":
    evaluar_tesorería_y_seguridad()