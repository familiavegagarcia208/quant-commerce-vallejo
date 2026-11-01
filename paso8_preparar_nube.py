"""
=========================================================================================
PASO 8: PREPARADOR DE ARQUITECTURA CLOUD (GITHUB ACTIONS + STREAMLIT CLOUD)
=========================================================================================
Genera en codificación UTF-8 limpia:
1. `.gitignore` (Protege `venv/`, sesiones privadas, credenciales y archivos WAL).
2. `requirements.txt` (Librerías exactas para el servidor Ubuntu de GitHub y Streamlit).
3. `.github/workflows/recolector.yml` (Pipeline autónomo cada 6 horas: Paso 3 -> 5 -> 6 -> 7).
4. Checkpoint de SQLite (`PRAGMA wal_checkpoint(TRUNCATE)`) para empaquetar toda la BD.
=========================================================================================
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

RUTA_BD = Path("data/zapateria_vallejo.db")

CONTENIDO_GITIGNORE = """# Entorno virtual de Python (NUNCA subir a GitHub)
venv/
.venv/
env/
__pycache__/
*.pyc

# Secretos industriales y sesiones privadas (Se inyectan vía GitHub Secrets)
data/sesion_vallejo.json
data/ml_credenciales.json
.env

# Archivos temporales de SQLite y caché crudo
data/*.db-wal
data/*.db-shm
data/imagenes_origen/
data/simulacion_bulk_ml.json
"""

CONTENIDO_REQUIREMENTS = """playwright
curl_cffi
beautifulsoup4
pandas
streamlit>=1.43.0
Pillow
"""

CONTENIDO_WORKFLOW_YML = """name: Quant-Commerce Vallejo Pipeline 24-7

on:
  schedule:
    # Corre automáticamente cada 6 horas (00:00, 06:00, 12:00, 18:00 UTC)
    - cron: '0 */6 * * *'
  workflow_dispatch:

permissions:
  contents: write

jobs:
  quant-pipeline:
    runs-on: ubuntu-latest
    steps:
      - name: 1. Descargar repositorio
        uses: actions/checkout@v4

      - name: 2. Configurar Python 3.11
        uses: actions/setup-python@v5
        with:
          python-version: '3.11'
          cache: 'pip'

      - name: 3. Instalar fuentes del sistema y dependencias
        run: |
          sudo apt-get update && sudo apt-get install -y fonts-dejavu-core
          python -m pip install --upgrade pip
          pip install -r requirements.txt
          playwright install chromium

      - name: 4. Restaurar secretos desde GitHub Secrets
        env:
          SESSION_JSON: ${{ secrets.SESSION_VALLEJO }}
          ML_CREDS_JSON: ${{ secrets.ML_CREDENCIALES }}
        run: |
          mkdir -p data
          if [ -n "$SESSION_JSON" ]; then
            echo "$SESSION_JSON" > data/sesion_vallejo.json
          fi
          if [ -n "$ML_CREDS_JSON" ]; then
            echo "$ML_CREDS_JSON" > data/ml_credenciales.json
          fi

      - name: 5. Paso 3 - Recolector Bayesiano UCB (Vallejo)
        run: python paso3_recolector_ligero.py

      - name: 6. Paso 5 - Motor Financiero Liquidez y Colchon Poisson
        run: python paso5_motor_financiero.py

      - name: 7. Paso 6 - Estudio Fotografico B2C (Pillow HD)
        run: python paso6_estudio_fotografico.py

      - name: 8. Paso 7 - Sincronizador y Generador de Titulos SEO
        run: python paso7_sincronizador_ml.py --modo dry-run

      - name: 9. Consolidar SQLite WAL y guardar avances en GitHub
        run: |
          python -c "import sqlite3; c=sqlite3.connect('data/zapateria_vallejo.db'); c.execute('PRAGMA wal_checkpoint(TRUNCATE);'); c.close()"
          git config --global user.name "github-actions[bot]"
          git config --global user.email "github-actions[bot]@users.noreply.github.com"
          git add data/zapateria_vallejo.db data/catalogo_listo/
          git diff --quiet && git diff --staged --quiet || (git commit -m "Auto-sync: Stock Bayesiano + Finanzas + Fotos HD" && git push)
"""


def preparar_archivos_nube() -> None:
    print("=" * 88)
    print("☁️ PASO 8: PREPARANDO ARQUITECTURA LOCAL PARA DESPLIEGUE EN GITHUB")
    print("=" * 88)

    # 1. Consolidar transacciones pendientes de SQLite WAL en el archivo .db principal
    if RUTA_BD.exists():
        with sqlite3.connect(RUTA_BD) as conn:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
        print("✅ 1/4 Base de datos SQLite consolidada (WAL checkpoint completado).")

    # 2. Escribir .gitignore
    Path(".gitignore").write_text(CONTENIDO_GITIGNORE, encoding="utf-8")
    print("✅ 2/4 Archivo `.gitignore` creado (protege `venv/`, sesiones y llaves).")

    # 3. Escribir requirements.txt
    Path("requirements.txt").write_text(CONTENIDO_REQUIREMENTS, encoding="utf-8")
    print("✅ 3/4 Archivo `requirements.txt` generado.")

    # 4. Escribir .github/workflows/recolector.yml
    ruta_wf = Path(".github/workflows/recolector.yml")
    ruta_wf.parent.mkdir(parents=True, exist_ok=True)
    ruta_wf.write_text(CONTENIDO_WORKFLOW_YML, encoding="utf-8")
    print(f"✅ 4/4 Pipeline automático creado en `{ruta_wf}`.")

    print("=" * 88)
    print("🎉 ¡Arquitectura lista para subir a GitHub con `git init` y `git push`!")
    print("=" * 88)


if __name__ == "__main__":
    preparar_archivos_nube()