import sqlite3
from pathlib import Path

def crear_base_de_datos():
    # Asegurar que exista la carpeta 'data'
    Path("data").mkdir(exist_ok=True)
    ruta_bd = "data/zapateria_vallejo.db"

    # Conectar a SQLite y activar el modo de alta velocidad WAL
    conn = sqlite3.connect(ruta_bd)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")

    # Crear las tablas iniciales
    conn.executescript("""
        -- 1. Zapatos candidatos capturados por el Radar Explorador (120 modelos)
        CREATE TABLE IF NOT EXISTS zapatos_candidatos (
            id_ps TEXT PRIMARY KEY,
            nombre_modelo TEXT NOT NULL,
            categoria TEXT NOT NULL,
            costo_socio REAL NOT NULL CHECK(costo_socio > 0),
            activo_monitoreo INTEGER DEFAULT 1,
            fecha_alta DATETIME DEFAULT CURRENT_TIMESTAMP
        );

        -- 2. Serie de tiempo para la incubadora de 21 días (Lecturas de stock)
        CREATE TABLE IF NOT EXISTS historial_stock_21_dias (
            id_registro INTEGER PRIMARY KEY AUTOINCREMENT,
            id_ps TEXT NOT NULL,
            talla TEXT NOT NULL,
            stock_vallejo INTEGER NOT NULL CHECK(stock_vallejo >= 0),
            fecha_hora DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(id_ps) REFERENCES zapatos_candidatos(id_ps)
        );

        CREATE INDEX IF NOT EXISTS idx_historial_ps_talla 
        ON historial_stock_21_dias(id_ps, talla, id_registro);

        -- 3. Tesorería Familiar (Control de Inversión K_0 y Freno Bancario)
        CREATE TABLE IF NOT EXISTS tesoreria_familiar (
            id INTEGER PRIMARY KEY CHECK(id = 1),
            inversion_inicial_mxn REAL NOT NULL DEFAULT 0.0,
            efectivo_disponible_banco_mxn REAL NOT NULL DEFAULT 0.0,
            dinero_retenido_plataformas_mxn REAL NOT NULL DEFAULT 0.0,
            alcancia_merma_2_porciento_mxn REAL NOT NULL DEFAULT 0.0
        );
    """)

    # Registrar un registro inicial por defecto para el control de tesorería
    conn.execute("""
        INSERT OR IGNORE INTO tesoreria_familiar 
        (id, inversion_inicial_mxn, efectivo_disponible_banco_mxn) 
        VALUES (1, 0.0, 0.0)
    """)

    conn.commit()
    conn.close()
    print("✅ ¡ÉXITO! Base de datos 'data/zapateria_vallejo.db' creada correctamente en modo WAL.")

if __name__ == "__main__":
    crear_base_de_datos()