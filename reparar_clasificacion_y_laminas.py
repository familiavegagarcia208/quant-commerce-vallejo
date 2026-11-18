# -*- coding: utf-8 -*-
"""
Reclasifica todos los SKUs de `zapatos_candidatos` con el doble candado de calzado
y regenera las láminas 03/04 de las carpetas existentes que NO son calzado.
"""

from pathlib import Path
import sqlite3
import subprocess
import sys

import inventario_tiendas as inv
import paso6_estudio_fotografico as paso6

RUTA_BD = Path("data/zapateria_vallejo.db")


def main() -> None:
    conn = inv.conectar(RUTA_BD)
    try:
        filas = conn.execute(
            "SELECT id_ps, COALESCE(nombre_modelo, ''), COALESCE(categoria_ps, ''), COALESCE(tipo_producto, '') "
            "FROM zapatos_candidatos"
        ).fetchall()

        cambios = []
        for id_ps, nom, cat_ps, tipo_act in filas:
            nuevo_tipo = inv.clasificar_tipo_producto(nom, cat_ps)
            if nuevo_tipo != tipo_act:
                cambios.append((nuevo_tipo, id_ps))

        if cambios:
            conn.executemany("UPDATE zapatos_candidatos SET tipo_producto = ? WHERE id_ps = ?", cambios)
            conn.commit()
        print(f"🧹 Reclasificados en SQLite: {len(cambios):,} SKUs corregidos de {len(filas):,} totales.")
    finally:
        conn.close()

    # Re-renderizar localmente cualquier carpeta existente cuyo perfil ya NO sea calzado
    datos_bd = paso6.cargar_catalogo_sqlite()
    reparadas = 0

    for carpeta in sorted(paso6.DIR_CATALOGO_LISTO.iterdir()):
        if not carpeta.is_dir():
            continue
        cid = paso6._id_de_carpeta(carpeta.name)
        meta = datos_bd.get(cid) or datos_bd.get(cid.lstrip("0") or cid)
        if not meta:
            continue

        perfil = paso6.detectar_perfil_prenda(
            meta["nombre"], meta["marca"], meta["corte"], meta.get("tallas_lista", []), meta.get("origen_catalogo", "")
        )
        if perfil["tipo"] in ("calzado_adulto", "calzado_infantil"):
            continue

        img_01, img_02 = paso6.obtener_imagenes_base_locales(carpeta)
        if img_01 is None:
            continue

        paso6._renderizar_carrusel(carpeta, meta, img_01, img_02)
        reparadas += 1
        print(f"   🔧 Reparada [{cid}] ({perfil['tipo']} | {meta['cuadrante']}) -> {meta['nombre']}")

    print(f"\n✅ Total de carpetas no-calzado regeneradas con su plantilla correcta: {reparadas:,}")
    subprocess.run([sys.executable, "paso5_motor_financiero.py"], check=False)


if __name__ == "__main__":
    main()