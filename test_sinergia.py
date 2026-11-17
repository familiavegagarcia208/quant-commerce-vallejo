# -*- coding: utf-8 -*-
"""Suite de verificación integral para garantizar 0 errores en SINERGIA."""

from __future__ import annotations

import io
from pathlib import Path

from PIL import Image, PngImagePlugin

from motor_etl_vallejo import (
    Config,
    DBHandler,
    EstudioFotograficoSinergia,
    ResultadoExtraccion,
    VarianteSucursal,
    aplicar_blindaje,
    auditar_fuga,
    calcular_unit_economics_item,
    clasificar_catalogo_origen,
    clasificar_categoria_producto,
    configurar_loggers,
    distribuir_stock_sucursales,
    es_marca_restringida_meli,
    extraer_marca_real,
)


def test_blindaje_inteligente_y_sucursales() -> None:
    # Si hay 1 sola unidad física, se blinda a 0
    assert aplicar_blindaje(1) == 0
    assert aplicar_blindaje(0) == 0
    # Con buffer = 2, 10 brutos -> 8 netos
    assert aplicar_blindaje(10, buffer=2) == 8

    # Propiedades de VarianteSucursal (Vallejo + Olivar + Arco Norte)
    vs = VarianteSucursal(variante="26", stock_vallejo=5, stock_olivar=3, stock_arco_norte=2)
    assert vs.stock_bruto_total == 10
    assert vs.stock_neto == 8

    s_val, s_oli, s_arc = distribuir_stock_sucursales(15)
    assert (s_val + s_oli + s_arc) == 15


def test_filtro_marcas_restringidas_meli_y_catalogos() -> None:
    assert es_marca_restringida_meli("Adidas", "TENIS GRAND COURT") is True
    assert es_marca_restringida_meli("Puma", "TENIS TIFOSI") is True
    assert es_marca_restringida_meli("Sonic", "ZAPATO ESCOLAR SONIC") is True
    assert es_marca_restringida_meli("Flexi", "ZAPATO CONFORT PIEL") is False

    assert clasificar_catalogo_origen("ZAPATO ESCOLAR SONIC", "Sonic", "Calzado") == "Catálogo Escolares e Infantil"
    assert clasificar_catalogo_origen("TENIS CASUAL PUMA TIFOSI", "Puma", "Calzado") == "Catálogo Importados y Deportivos"


def test_persistencia_sqlite_deltas_y_estudio(tmp_path: Path) -> None:
    ruta_db_test = tmp_path / "test_sinergia.db"
    log_pub, log_int = configurar_loggers()
    estudio = EstudioFotograficoSinergia(log_pub, log_int)

    base = Image.new("RGB", (800, 800), (255, 255, 255))
    buf = io.BytesIO()
    base.save(buf, format="PNG")
    raw = buf.getvalue()

    vs_inicial = [
        VarianteSucursal("25", 5, 3, 2),  # Bruto 10 -> Neto 8
        VarianteSucursal("26", 6, 4, 2),  # Bruto 12 -> Neto 10
    ]
    res_dia_1 = ResultadoExtraccion(
        sku="1312917",
        titulo="TENIS MOTORSPORT GRAND COURT LO MER",
        marca="Adidas",
        categoria="Calzado",
        color="Azul Marino",
        material="Sintético",
        costo_base=360.0,
        catalogo_origen="Catálogo Importados y Deportivos",
        variantes_multisucursal=vs_inicial,
        variantes_brutas={"25": 10, "26": 12},
        variantes_netas={"25": 8, "26": 10},
        stock_bruto_total=22,
        stock_neto_total=18,
        imagenes_bytes=[("sha0", raw), ("sha1", raw)],
        urls_imagenes_origen=[],
        fuente_extraccion="TEST",
        url_final_secreta="https://www.priceshoes.com/buscar?q=1312917",
    )

    with DBHandler(ruta_db_test) as db:
        db.guardar_resultado_extraccion("RUN_1", res_dia_1)

    # Día 2: Se venden 3 pares de talla 25 (baja de 10 brutos a 7 brutos -> 5 netos, Delta = -3)
    vs_dia_2 = [
        VarianteSucursal("25", 3, 2, 2),  # Bruto 7 -> Neto 5 (Delta = -3)
        VarianteSucursal("26", 6, 4, 2),  # Bruto 12 -> Neto 10 (Delta = 0)
    ]
    res_dia_2 = ResultadoExtraccion(
        sku="1312917",
        titulo="TENIS MOTORSPORT GRAND COURT LO MER",
        marca="Adidas",
        categoria="Calzado",
        color="Azul Marino",
        material="Sintético",
        costo_base=360.0,
        catalogo_origen="Catálogo Importados y Deportivos",
        variantes_multisucursal=vs_dia_2,
        variantes_brutas={"25": 7, "26": 12},
        variantes_netas={"25": 5, "26": 10},
        stock_bruto_total=19,
        stock_neto_total=15,
        imagenes_bytes=[("sha0", raw), ("sha1", raw)],
        urls_imagenes_origen=[],
        fuente_extraccion="TEST",
        url_final_secreta="https://www.priceshoes.com/buscar?q=1312917",
    )

    with DBHandler(ruta_db_test) as db:
        db.guardar_resultado_extraccion("RUN_2", res_dia_2)
        fila_25 = db.conn.execute(
            "SELECT stock_anterior, stock_neto, delta_variante FROM variantes_stock WHERE sku='1312917' AND variante='25';"
        ).fetchone()
        assert int(fila_25["stock_anterior"]) == 8
        assert int(fila_25["stock_neto"]) == 5
        assert int(fila_25["delta_variante"]) == -3

    im98 = estudio.renderizar_lamina_98_impacto(res_dia_2, base)
    im99 = estudio.renderizar_lamina_99_split(res_dia_2, base)
    assert im98.size == (1200, 1200)
    assert im99.size == (1200, 1200)