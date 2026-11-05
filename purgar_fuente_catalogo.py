"""
Script de purga total para eliminar de SQLite y de data/catalogo_listo
todos los productos que no pertenezcan a los catálogos PDF oficiales.
"""

from pathlib import Path
import re
import shutil
import sqlite3

RUTA_BD = Path("data/zapateria_vallejo.db")
DIR_CATALOGO = Path("data/catalogo_listo")


def purgar_inventario() -> None:
    if not RUTA_BD.exists():
        print("⚠️ No se encontró la base de datos en data/zapateria_vallejo.db")
        return

    conn = sqlite3.connect(RUTA_BD)
    cursor = conn.cursor()

    # Verificar si existe la tabla zapatos_candidatos
    tablas = [r[0] for r in cursor.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
    if "zapatos_candidatos" not in tablas:
        print("⚠️ La tabla zapatos_candidatos no existe en la base de datos.")
        conn.close()
        return

    # Contar antes de borrar
    antes = cursor.execute("SELECT COUNT(*) FROM zapatos_candidatos").fetchone()[0]

    # Eliminar registros que no tengan un catálogo PDF oficial asignado
    cursor.execute(
        """
        DELETE FROM zapatos_candidatos
        WHERE origen_catalogo IS NULL
           OR TRIM(origen_catalogo) = ''
           OR LOWER(TRIM(origen_catalogo)) IN ('none', 'null', 'nan', 'sin catálogo (web)')
        """
    )
    conn.commit()

    # Obtener la lista de IDs válidos restantes
    ids_validos = {
        str(r[0]).strip().lstrip("0")
        for r in cursor.execute("SELECT id_ps FROM zapatos_candidatos").fetchall()
    }
    despues = len(ids_validos)
    conn.close()

    # Purgar carpetas físicas fuera de catálogo en data/catalogo_listo
    borradas = 0
    if DIR_CATALOGO.exists():
        for d in DIR_CATALOGO.iterdir():
            if d.is_dir():
                m = re.search(r"(\d{5,8})", d.name)
                cid = m.group(1).lstrip("0") if m else ""
                if not cid or cid not in ids_validos:
                    shutil.rmtree(d, ignore_errors=True)
                    borradas += 1

    print("=" * 88)
    print(f"🧹 PURGA COMPLETADA CON ÉXITO:")
    print(f"   - Registros en SQLite: pasó de {antes} a {despues} SKUs exclusivos de Catálogos PDF.")
    print(f"   - Carpetas eliminadas en disco: {borradas}")
    print("=" * 88)


if __name__ == "__main__":
    purgar_fuente = purgar_inventario()