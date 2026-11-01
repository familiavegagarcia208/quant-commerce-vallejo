from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

def crear_estudio_fotografico():
    """
    Simula el Estudio Gráfico Automático (Pillow Studio V6.0).
    Genera las carpetas de activos digitales y crea las imágenes estandarizadas
    para los productos aprobados sin gastar un solo token ni usar software pesado.
    """
    print("🎨 Iniciando Pillow Studio (Generación de Activos Gráficos)...")

    # 1. Definir un producto de prueba de nuestra incubadora (Ej. ID 884101)
    id_ps = "884101"
    nombre_sku = "Botin_Urbano_Tacon_Grueso_Negro"
    
    # Crear carpeta digital para este SKU en específico
    ruta_carpeta_sku = Path(f"almacen_digital/catalogo_skus/PS-VAL-BOTAS-{id_ps}-{nombre_sku}")
    ruta_carpeta_sku.mkdir(parents=True, exist_ok=True)

    # 2. Generar Portada Blanca (1200x1200 px - Fondo Blanco Puro)
    # Requisito algorítmico obligatorio de Mercado Libre y Amazon
    portada = Image.new("RGB", (1200, 1200), color=(255, 255, 255))
    draw_portada = ImageDraw.Draw(portada)
    
    # Dibujar un marco visual limpio de prueba representando el calzado
    draw_portada.rectangle([250, 200, 950, 900], outline=(220, 220, 220), width=4)
    draw_portada.text((320, 520), f"FOTO OFICIAL PORTADA\n[ SKU: {id_ps} ]", fill=(80, 80, 80))
    
    ruta_portada = ruta_carpeta_sku / "01_portada_blanca.jpg"
    portada.save(ruta_portada, quality=95)
    print(f"   • Creada: {ruta_portada}")

    # 3. Generar Infografía de Marketing (1200x1200 px con viñetas de valor)
    infografia = Image.new("RGB", (1200, 1200), color=(245, 247, 250))
    draw_ifo = ImageDraw.Draw(infografia)
    
    # Tarjeta lateral de beneficios generada por el sistema
    draw_ifo.rectangle([80, 80, 1120, 1120], fill=(255, 255, 255), outline=(210, 215, 220), width=2)
    draw_ifo.rectangle([120, 120, 1080, 240], fill=(20, 30, 45))  # Encabezado oscuro
    draw_ifo.text((150, 155), f"CALZADO SELECCIONADO VALLEJO | ID: {id_ps}", fill=(255, 255, 255))
    
    # Bullets de venta
    bullets = [
        "✓ Horma Exacta Ajustada al Mercado Mexicano",
        "✓ Suela Antiderrapante de Alta Durabilidad",
        "✓ Confort Premium Apto para Uso Diario Prolongado",
        "✓ Resurtido Constante Garantizado desde Bodega Vallejo"
    ]
    y_pos = 320
    for bullet in bullets:
        draw_ifo.text((160, y_pos), bullet, fill=(40, 50, 60))
        y_pos += 90

    ruta_ifo = ruta_carpeta_sku / "02_infografia_marketing.jpg"
    infografia.save(ruta_ifo, quality=95)
    print(f"   • Creada: {ruta_ifo}")

    # 4. Generar Guía de Tallas en Centímetros (1200x1200 px para evitar devoluciones)
    guia_tallas = Image.new("RGB", (1200, 1200), color=(255, 255, 255))
    draw_tallas = ImageDraw.Draw(guia_tallas)
    draw_tallas.text((100, 100), "GUÍA DE TALLAS OFICIAL (MÉXICO vs CM)", fill=(20, 30, 45))
    
    tabla_cm = [
        ("Talla MX", "Longitud del Pie (cm)"),
        ("22.5", "22.5 - 22.8 cm"),
        ("23.0", "23.0 - 23.3 cm"),
        ("23.5", "23.5 - 23.8 cm"),
        ("24.0", "24.0 - 24.3 cm"),
        ("24.5", "24.5 - 24.8 cm"),
        ("25.0", "25.0 - 25.3 cm"),
    ]
    
    y_t = 220
    for fila in tabla_cm:
        draw_tallas.rectangle([100, y_t, 1100, y_t + 70], outline=(200, 205, 210), width=1)
        draw_tallas.text((140, y_t + 22), fila[0], fill=(50, 50, 50))
        draw_tallas.text((600, y_t + 22), fila[1], fill=(50, 50, 50))
        y_t += 70

    ruta_tallas = ruta_carpeta_sku / "03_guia_tallas_cm.jpg"
    guia_tallas.save(ruta_tallas, quality=95)
    print(f"   • Creada: {ruta_tallas}")

    print(f"\n✅ ¡ESTUDIO FOTOGRÁFICO FINALIZADO!")
    print(f"📂 Archivos guardados en la ruta: {ruta_carpeta_sku}")

if __name__ == "__main__":
    crear_estudio_fotografico()