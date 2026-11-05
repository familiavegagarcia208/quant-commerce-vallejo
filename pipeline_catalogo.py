"""
=========================================================================================
ORQUESTADOR EXCLUSIVO DE CATÁLOGOS PDF (AISLAMIENTO ESTRICTO DE LOS 8,165 SKUs)
=========================================================================================
"""

from __future__ import annotations

import sqlite3
import subprocess
import sys
from pathlib import Path

RUTA_BD: Path = Path("data/zapateria_vallejo.db")


def aislar_solo_catalogo_pdf() -> int:
    """
    Mueve los artículos que NO pertenecen a los catálogos PDF a una tabla de respaldo
    (`zapatos_respaldo_no_catalogo`) para que paso3, paso5 y paso6 trabajen 100% sobre
    los SKUs extraídos de los PDFs.
    """
    conn = sqlite3.connect(RUTA_BD, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL;")

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS zapatos_respaldo_no_catalogo AS
        SELECT * FROM zapatos_candidatos WHERE 1=0;
        """
    )

    # Mover a respaldo todo lo que no tenga origen_catalogo
    conn.execute(
        """
        INSERT OR REPLACE INTO zapatos_respaldo_no_catalogo
        SELECT * FROM zapatos_candidatos
        WHERE origen_catalogo IS NULL OR origen_catalogo = '';
        """
    )
    conn.execute(
        """
        DELETE FROM zapatos_candidatos
        WHERE origen_catalogo IS NULL OR origen_catalogo = '';
        """
    )
    conn.commit()

    total_catalogo = conn.execute("SELECT COUNT(*) FROM zapatos_candidatos").fetchone()[0]
    conn.close()
    return total_catalogo


def correr_script(script: str, titulo: str) -> None:
    ruta = Path(script)
    if not ruta.exists():
        print(f"⚠️ No se encontró el archivo {script}, saltando paso...")
        return
    print("\n" + "=" * 80)
    print(f"▶️ EJECUTANDO: {titulo} ({script})")
    print("=" * 80)
    subprocess.run([sys.executable, str(ruta)], check=False)


def main() -> None:
    print("=" * 80)
    print("🚀 PIPELINE 100% LOCAL (SIN IA): EXCLUSIVO PARA CATÁLOGOS PDF")
    print("=" * 80)

    total = aislar_solo_catalogo_pdf()
    print(f"📘 Universo aislado en SQLite (100% Catálogos PDF): {total:,} SKUs")

    correr_script("paso3_recolector_ligero.py", "PASO 3: RECOLECTOR LIGERO DE PRECIOS Y TALLAS")
    correr_script("paso5_motor_financiero.py", "PASO 5: MOTOR FINANCIERO Y UNIT ECONOMICS")
    correr_script("paso6_estudio_fotografico.py", "PASO 6: ESTUDIO FOTOGRÁFICO HD (PILLOW)")


if __name__ == "__main__":
    main()