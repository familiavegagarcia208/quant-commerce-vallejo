"""
========================================================================================
EXPEDITE FIN DE SEMANA: BARRIDO NOCTURNO CON PROTECCIÓN ANTIBAN Y LÍMITE DE TIEMPO
========================================================================================
Actualiza las existencias de los productos de catálogos PDF en la base de datos SQLite.
Optimizado para GitHub Actions: 
 - Se detiene antes de las 6 horas para permitir el Git Commit.
 - Prioriza los SKUs con la actualización más antigua.
 - Tolera la ubicación dinámica de la BD (raíz o carpeta data/).
========================================================================================
"""

from __future__ import annotations

import random
import sqlite3
import time
import urllib.request
import urllib.error
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    from curl_cffi import requests as tls_requests
except ImportError:
    tls_requests = None

# 1. Detección dinámica de la Base de Datos
if Path("zapateria_vallejo.db").exists():
    RUTA_BD: Path = Path("zapateria_vallejo.db")
elif Path("data/zapateria_vallejo.db").exists():
    RUTA_BD: Path = Path("data/zapateria_vallejo.db")
else:
    RUTA_BD: Path = Path("zapateria_vallejo.db")

# 2. Límite estricto de tiempo para GitHub Actions (5 horas = 18,000 segundos)
TIEMPO_MAXIMO_EJECUCION = 5 * 3600  

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2.1 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:123.0) Gecko/20100101 Firefox/123.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
]

def obtener_headers_aleatorios() -> Dict[str, str]:
    return {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "es-MX,es;q=0.9,en;q=0.8",
        "Referer": "https://www.priceshoes.com/",
    }

def peticion_segura(url: str, max_intentos: int = 3) -> Optional[str]:
    for intento in range(1, max_intentos + 1):
        # Pausa humana aleatoria obligatoria (45 a 180 segundos)
        pausa_segundos = random.uniform(45.0, 180.0)
        print(f"    ⏳ [Antiban] Pausa: {pausa_segundos:.1f}s antes de consultar...")
        time.sleep(pausa_segundos)

        try:
            headers = obtener_headers_aleatorios()
            if tls_requests is not None:
                nav_impersonate = random.choice(["chrome120", "safari15_5", "edge101"])
                resp = tls_requests.get(url, headers=headers, impersonate=nav_impersonate, timeout=30)
                status = resp.status_code
                texto = resp.text
            else:
                req = urllib.request.Request(url, headers=headers)
                with urllib.request.urlopen(req, timeout=30) as resp:
                    status = getattr(resp, "status", 200)
                    texto = resp.read().decode("utf-8", errors="ignore")

            if status == 200:
                return texto
            elif status in (403, 429):
                print(f"    ⚠️ [Alerta HTTP {status}] Límite de velocidad. Enfriando IP por 12 minutos...")
                time.sleep(720)
            else:
                print(f"    ⚠️ [HTTP {status}] Error en intento {intento}/{max_intentos}")

        except urllib.error.HTTPError as e:
            if e.code in (403, 429):
                print(f"    ⚠️ [Alerta HTTP {e.code}] Bloqueo detectado. Enfriando IP por 12 minutos...")
                time.sleep(720)
            else:
                print(f"    ❌ [HTTPError] Intento {intento}/{max_intentos}: {e}")
                time.sleep(60)
        except Exception as e:
            print(f"    ❌ [Error de red] Intento {intento}/{max_intentos}: {e}")
            time.sleep(120)

    return None

def cargar_productos_prioritarios() -> List[Dict[str, Any]]:
    """Carga los productos ordenados por los que llevan más tiempo sin revisarse."""
    if not RUTA_BD.exists():
        print(f"❌ BD no encontrada en {RUTA_BD}")
        return []

    conn = sqlite3.connect(RUTA_BD)
    conn.row_factory = sqlite3.Row
    productos = []
    try:
        tablas = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        if "zapatos_candidatos" in tablas:
            # Selecciona solo de catálogos y ordena del más viejo al más reciente
            filas = conn.execute(
                """
                SELECT id_ps, COALESCE(nombre_modelo,'') as nombre, COALESCE(url_real,'') as url 
                FROM zapatos_candidatos 
                WHERE origen_catalogo IS NOT NULL AND TRIM(origen_catalogo) != ''
                ORDER BY COALESCE(ultima_actualizacion, '2000-01-01') ASC
                """
            ).fetchall()
            productos = [dict(f) for f in filas]
    finally:
        conn.close()
    return productos

def actualizar_stock_en_bd(id_ps: str, nuevo_stock: int) -> None:
    """Modo WAL activado para asegurar la escritura sin corromper la BD."""
    conn = sqlite3.connect(RUTA_BD)
    try:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute(
            """
            UPDATE zapatos_candidatos 
            SET stock_total_anterior = ?, 
                ultima_actualizacion = CURRENT_TIMESTAMP 
            WHERE id_ps = ?
            """,
            (nuevo_stock, id_ps)
        )
        conn.commit()
    except Exception as e:
        print(f"    ❌ Error BD para ID {id_ps}: {e}")
    finally:
        conn.close()

def ejecutar_expedite_nocturno() -> None:
    print("=" * 88)
    print(f"🌙 EXPEDITE NOCTURNO ACTIVO [{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}]")
    print(f"📂 Usando base de datos en: {RUTA_BD.absolute()}")
    print("=" * 88)

    tiempo_inicio = time.time()
    productos = cargar_productos_prioritarios()
    
    if not productos:
        print("⚠️ No hay productos pendientes en catálogos oficiales.")
        return

    print(f"📦 SKUs en fila: {len(productos):,}")
    actualizados = 0

    for idx, prod in enumerate(productos, 1):
        # Validación de límite de tiempo para Github Actions
        tiempo_transcurrido = time.time() - tiempo_inicio
        if tiempo_transcurrido > TIEMPO_MAXIMO_EJECUCION:
            print(f"\n⏳ LÍMITE DE TIEMPO ALCANZADO ({(tiempo_transcurrido/3600):.1f} hrs).")
            print("🛑 Deteniendo el barrido para permitir que GitHub Actions guarde la Base de Datos.")
            break

        id_ps = prod["id_ps"]
        print(f"\n[{idx}/{len(productos)}] Revisando: {id_ps} - {prod['nombre'][:40]}...")

        url_objetivo = prod["url"] if (prod["url"] and "/productos/" in prod["url"]) else f"https://www.priceshoes.com/buscar?q={id_ps}"
        html = peticion_segura(url_objetivo)
        
        if not html:
            print(f"    ⏭️ Omitiendo SKU {id_ps}.")
            continue

        stock_detectado = 10
        html_low = html.lower()
        if "agotado" in html_low or "sin stock" in html_low or "no disponible" in html_low:
            stock_detectado = 0

        actualizar_stock_en_bd(id_ps, stock_detectado)
        actualizados += 1
        print(f"    ✅ SKU {id_ps} actualizado (Stock: {stock_detectado}).")

    print("\n" + "=" * 88)
    print(f"🎉 BARRIDO FINALIZADO. SKUs actualizados: {actualizados}")
    print("=" * 88)

if __name__ == "__main__":
    ejecutar_expedite_nocturno()