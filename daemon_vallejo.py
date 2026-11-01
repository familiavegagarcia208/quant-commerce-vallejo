import os
import sys
import time
import subprocess
from datetime import datetime
from pathlib import Path

ARCHIVO_LOG = Path("data/bitacora_daemon.log")
HORARIOS_BARRIDO = ["08:00", "13:00", "18:00", "23:00"]

def escribir_bitacora(mensaje: str):
    """Imprime en pantalla y guarda el registro en data/bitacora_daemon.log"""
    ARCHIVO_LOG.parent.mkdir(exist_ok=True)
    marca_tiempo = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    linea = f"[{marca_tiempo}] {mensaje}"
    print(linea)
    with open(ARCHIVO_LOG, "a", encoding="utf-8") as f:
        f.write(linea + "\n")

def ejecutar_modulo(nombre_script: str, descripcion: str) -> bool:
    """Ejecuta un script forzando codificacion UTF-8 para evitar errores con emojis y acentos."""
    escribir_bitacora(f"Iniciando {descripcion} ({nombre_script})...")
    
    # Forzar UTF-8 en subprocesos de Windows
    entorno_utf8 = os.environ.copy()
    entorno_utf8["PYTHONUTF8"] = "1"
    entorno_utf8["PYTHONIOENCODING"] = "utf-8"

    try:
        resultado = subprocess.run(
            [sys.executable, nombre_script],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=entorno_utf8,
            timeout=180
        )
        if resultado.returncode == 0:
            escribir_bitacora(f"OK - {descripcion} completado sin errores.")
            return True
        else:
            escribir_bitacora(f"ADVERTENCIA en {nombre_script}: {resultado.stderr.strip()}")
            return False
    except Exception as e:
        escribir_bitacora(f"ERROR critico ejecutando {nombre_script}: {e}")
        return False

def ciclo_operativo_completo(actualizar_catalogo: bool = False):
    """Corre la cadena de produccion completa de forma desatendida."""
    escribir_bitacora("=" * 60)
    escribir_bitacora("ARRANCANDO CICLO AUTOMATICO QUANT-COMMERCE VALLEJO")
    
    if actualizar_catalogo:
        ejecutar_modulo("paso2_radar_sitemap.py", "Curaduria Inteligente desde Sitemap XML")
    
    ejecutar_modulo("paso3_recolector_ligero.py", "Barrido de Stock y Precios en Vivo")
    ejecutar_modulo("paso5_motor_matematico.py", "Auditoria de Tesoreria y Colchon de Poisson")
    
    escribir_bitacora("CICLO FINALIZADO. Esperando siguiente ventana horaria...")
    escribir_bitacora("=" * 60)

def iniciar_daemon():
    escribir_bitacora("DAEMON 24/7 ENCENDIDO (Presiona Ctrl + C para detenerlo)")
    escribir_bitacora(f"Horarios programados de barrido automatico: {', '.join(HORARIOS_BARRIDO)}")

    ciclo_operativo_completo(actualizar_catalogo=False)
    ultimo_minuto_ejecutado = ""

    try:
        while True:
            ahora = datetime.now()
            hora_actual = ahora.strftime("%H:%M")

            if hora_actual in HORARIOS_BARRIDO and hora_actual != ultimo_minuto_ejecutado:
                refrescar_xml = (hora_actual == "08:00")
                ciclo_operativo_completo(actualizar_catalogo=refrescar_xml)
                ultimo_minuto_ejecutado = hora_actual

            time.sleep(20)
    except KeyboardInterrupt:
        escribir_bitacora("Daemon detenido manualmente por el usuario.")

if __name__ == "__main__":
    iniciar_daemon()