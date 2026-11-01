import sqlite3
from pathlib import Path

RUTA_BD = "data/zapateria_vallejo.db"
META_POR_CATEGORIA = 30

def generar_120_candidatos_semilla() -> list[dict]:
    """
    Genera 120 candidatos realistas (30 por categoría: Botas, Tenis, Casual y Hombre)
    con costos óptimos entre $310 y $560 MXN para probar el sistema de inmediato.
    """
    prefijos = {
        "BOTAS_DAMA": ("884", ["Botín Urbano Tacón Grueso", "Bota Militar Piel Sintética", "Botín Chelsea Plataforma", "Bota Larga Casual"]),
        "TENIS_DAMA": ("772", ["Tenis Urbano Chunky", "Tenis Casual Plataforma", "Sneaker Deportivo Ligero", "Tenis Bota Urbano"]),
        "CASUAL_CONFORT_DAMA": ("651", ["Mocasín Confort Memory", "Zapato Casual Antiderrapante", "Flats Confort Piel", "Lace-Up Urbano"]),
        "CALZADO_HOMBRE": ("910", ["Bota Industrial Casquillo", "Tenis Urbano Caballero", "Botín Casual Piel", "Zapato Confort Hombre"]),
    }
    colores = ["Negro", "Hueso", "Camel", "Chocolate", "Blanco", "Marino"]
    semilla = []

    for categoria, (prefijo_id, nombres_base) in prefijos.items():
        for i in range(1, META_POR_CATEGORIA + 1):
            id_ps = f"{prefijo_id}{100 + i}"
            modelo = f"{nombres_base[i % len(nombres_base)]} {colores[i % len(colores)]}"
            costo = round(310.0 + ((i * 17) % 250), 2)
            semilla.append({
                "id_ps": id_ps, 
                "nombre_modelo": modelo, 
                "categoria": categoria, 
                "costo_socio": costo
            })
    return semilla

def ejecutar_radar():
    conn = sqlite3.connect(RUTA_BD)
    conn.execute("PRAGMA journal_mode=WAL;")
    
    candidatos = generar_120_candidatos_semilla()

    conn.execute("BEGIN IMMEDIATE;")
    for prod in candidatos:
        conn.execute("""
            INSERT INTO zapatos_candidatos (id_ps, nombre_modelo, categoria, costo_socio, activo_monitoreo)
            VALUES (?, ?, ?, ?, 1)
            ON CONFLICT(id_ps) DO UPDATE SET 
                costo_socio = excluded.costo_socio, 
                activo_monitoreo = 1
        """, (prod["id_ps"], prod["nombre_modelo"], prod["categoria"], prod["costo_socio"]))
    conn.commit()

    total = conn.execute("SELECT COUNT(*) FROM zapatos_candidatos WHERE activo_monitoreo = 1").fetchone()[0]
    conn.close()
    
    print(f"✅ ¡RADAR EXITOSO! {total} modelos candidatos cargados en la Incubadora de 21 días.")

if __name__ == "__main__":
    ejecutar_radar()