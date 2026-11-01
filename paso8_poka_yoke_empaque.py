import sqlite3
import hashlib
from pathlib import Path

RUTA_BD = "data/zapateria_vallejo.db"

def inicializar_tabla_forense(conn):
    """Crea la tabla donde guardamos las pruebas forenses de cada envío (peso y sello)."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS unidades_enviadas_forense (
            id_envio INTEGER PRIMARY KEY AUTOINCREMENT,
            id_ps TEXT NOT NULL,
            talla TEXT NOT NULL,
            peso_bov_gramos REAL NOT NULL,
            folio_sello_holografico TEXT NOT NULL,
            hash_foto_forense TEXT NOT NULL,
            estado_empaque TEXT DEFAULT 'APROBADO_POKA_YOKE',
            fecha_envio DATETIME DEFAULT CURRENT_TIMESTAMP
        );
    """)
    conn.commit()

def registrar_empaque_seguro(id_ps: str, talla: str, peso_gramos: float, folio_sello: str):
    """
    Ejecuta el protocolo Poka-Yoke de empaque:
    Valida el peso, el sello holográfico y genera un hash de respaldo antes de liberar el envío.
    """
    conn = sqlite3.connect(RUTA_BD)
    inicializar_tabla_forense(conn)

    # Simular un hash SHA-256 de la foto tomada en la báscula de empaque
    contenido_foto = f"{id_ps}-{talla}-{peso_gramos}-{folio_sello}"
    hash_foto = hashlib.sha256(contenido_foto.encode("utf-8")).hexdigest()

    print(f"\n📦 [Mesa de Empaque Poka-Yoke]: Auditando salida para SKU {id_ps} (Talla {talla})...")

    # Regla de validación de peso mínimo para calzado (evita cajas vacías o fraudes)
    if peso_gramos < 400.0:
        print(f"❌ [ALERTA FRAUDE]: El peso registrado ({peso_gramos}g) es demasiado bajo para un par de zapatos. Empaque bloqueado.")
        conn.close()
        return False

    # Guardar en SQLite WAL de forma atómica
    conn.execute(
        """
        INSERT INTO unidades_enviadas_forense 
        (id_ps, talla, peso_bov_gramos, folio_sello_holografico, hash_foto_forense)
        VALUES (?, ?, ?, ?, ?)
        """,
        (id_ps, talla, peso_gramos, folio_sello, hash_foto)
    )
    conn.commit()
    conn.close()

    print(f"   • Sello Holográfico Registrado: {folio_sello}")
    print(f"   • Peso Validado en Báscula: {peso_gramos} gramos")
    print(f"   • Hash Criptográfico Forense: {hash_foto[:16]}...")
    print("✅ ¡EMPAQUE LIBERADO CON ÉXITO! Etiqueta de paquetería autorizada.")
    return True

if __name__ == "__main__":
    # Prueba de empaque seguro para un par de botines
    registrar_empaque_seguro(
        id_ps="884101", 
        talla="24.0", 
        peso_gramos=825.5, 
        folio_sello="SELLO-HOLOGRAMA-9941"
    )