import sqlite3
import hashlib
from pathlib import Path

RUTA_BD = "data/zapateria_vallejo.db"

def inicializar_tabla_ia(conn):
    """Crea la tabla de caché para que las respuestas de la IA no se cobren dos veces."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS cache_agentes_ia (
            hash_pregunta TEXT PRIMARY KEY,
            agente_nombre TEXT NOT NULL,
            prompt_original TEXT NOT NULL,
            respuesta_generada TEXT NOT NULL,
            tokens_gastados INTEGER DEFAULT 0,
            fecha_creacion DATETIME DEFAULT CURRENT_TIMESTAMP
        );
    """)
    conn.commit()

def consultar_agente_inteligente(agente_nombre: str, prompt: str) -> str:
    """
    Simula el Cortafuegos de Tokens con Caché local SQLite.
    Si la pregunta ya se hizo antes, devuelve la respuesta gratis en 0 ms.
    Si es nueva, genera la respuesta estructurada del agente.
    """
    conn = sqlite3.connect(RUTA_BD)
    inicializar_tabla_ia(conn)

    # Crear un identificador único (Hash SHA-256) del prompt para buscar en caché
    hash_p = hashlib.sha256(prompt.encode("utf-8")).hexdigest()

    # Buscar si ya tenemos la respuesta guardada (Paréntesis corregido aquí)
    cache_hit = conn.execute(
        "SELECT respuesta_generada FROM cache_agentes_ia WHERE hash_pregunta = ?", (hash_p,)
    ).fetchone()

    if cache_hit:
        conn.close()
        print(f"⚡ [Caché Activa - 0 Tokens]: Respuesta del '{agente_nombre}' recuperada de SQLite.")
        return cache_hit[0]

    # Si no está en caché, simular la respuesta estructurada del Agente Especializado
    print(f"🤖 [Llamada a IA]: El '{agente_nombre}' está procesando la solicitud...")
    
    if agente_nombre == "Agente 3 (Neuro-Marketing)":
        respuesta = (
            "Título SEO: Botín Urbano con Tacón Grueso y Confort Premium Vallejo\n"
            "Bullets:\n"
            "• Horma ajustada al pie mexicano con soporte anatómico.\n"
            "• Suela tracción fuerte antideslizante para ciudad.\n"
            "• Resurtido semanal directo desde bodega de fábrica."
        )
    elif agente_nombre == "Agente 4 (Vendedor Pre-Venta)":
        respuesta = (
            "¡Hola! Claro que sí tenemos stock disponible en este momento con envío express. "
            "Te recomendamos tu talla habitual de calzado en México. ¿Te ayudo a apartar tu par?"
        )
    else:
        respuesta = "Operación procesada y validada correctamente por el enjambre de agentes."

    # Guardar en caché para que las próximas consultas sean 100% gratuitas
    conn.execute(
        "INSERT OR REPLACE INTO cache_agentes_ia (hash_pregunta, agente_nombre, prompt_original, respuesta_generada, tokens_gastados) VALUES (?, ?, ?, ?, ?)",
        (hash_p, agente_nombre, prompt, respuesta, 320)
    )
    conn.commit()
    conn.close()
    return respuesta

def probar_enjambre():
    print("🧠 Probando el Cortafuegos de Agentes Inteligentes...\n")

    # Prueba 1: Generar copy de marketing con el Agente 3
    prompt_marketing = "Genera títulos y bullets para el botín urbano ID 884101"
    res1 = consultar_agente_inteligente("Agente 3 (Neuro-Marketing)", prompt_marketing)
    print(f"\n{res1}\n")
    print("=" * 60)

    # Prueba 2: Consultar la misma pregunta de nuevo para comprobar el ahorro de tokens (Caché Hit)
    print("\n🔄 Realizando la misma consulta por segunda vez (Probando Caché)...")
    res2 = consultar_agente_inteligente("Agente 3 (Neuro-Marketing)", prompt_marketing)
    print(f"\n{res2}\n")

if __name__ == "__main__":
    probar_enjambre()