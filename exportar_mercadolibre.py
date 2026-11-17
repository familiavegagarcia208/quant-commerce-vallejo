"""
========================================================================================
EXPORTACIÓN A MERCADO LIBRE: CSV por tipo de producto (nuevas y actualizaciones)
========================================================================================
Con los productos seleccionados en el panel y las existencias de la tienda activa genera,
en `data/exportes_ml/<fecha>/`:

  nuevas_<tipo>.csv      Productos seleccionados que aún no están publicados y tienen
                         inventario suficiente. Una fila por talla.
  actualizar_<tipo>.csv  Publicaciones vigentes: ACTUALIZAR (precio y cantidades),
                         PAUSAR o ELIMINAR (menos piezas que el umbral, 15 por defecto,
                         o producto deseleccionado) y REACTIVAR (pausada que se recuperó).
  omitidos.csv           Productos seleccionados que no se pudieron exportar y por qué.

Uso:
  python exportar_mercadolibre.py                         # genera los CSV
  python exportar_mercadolibre.py --bajo-stock eliminar   # ELIMINAR en lugar de PAUSAR
  python exportar_mercadolibre.py --confirmar             # ya subí la última exportación
  python exportar_mercadolibre.py --ids-ml archivo.csv    # registra los ID de Mercado Libre
                                                          # (columnas: id_price_shoes, ml_item_id)

El sistema no se conecta a Mercado Libre: sabe qué está publicado porque se le confirma
con `--confirmar` después de subir los archivos.
========================================================================================
"""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass, field
from datetime import datetime
import json
from pathlib import Path
import re
import sqlite3
import sys
from typing import Any, Dict, Iterable, List, Optional, Tuple
import unicodedata

import inventario_tiendas as inv
import paso5_motor_financiero as finanzas

DIR_EXPORTES: Path = Path("data/exportes_ml")
DIR_CATALOGO_LISTO: Path = Path("data/catalogo_listo")
ARCHIVO_RESUMEN = "resumen.json"

RESERVA_POR_TALLA_DEFAULT: int = 2   # piezas por talla que no se ofrecen (colchón contra ventas en tienda)
LARGO_MAXIMO_TITULO: int = 60        # límite de Mercado Libre para el título

ACCION_ACTUALIZAR = "ACTUALIZAR"
ACCION_PAUSAR = "PAUSAR"
ACCION_ELIMINAR = "ELIMINAR"
ACCION_REACTIVAR = "REACTIVAR"

ESTADO_ACTIVA = "activa"
ESTADO_PAUSADA = "pausada"
ESTADO_ELIMINADA = "eliminada"

# Estado de la publicación en Mercado Libre que corresponde a cada acción
ESTADO_ML_POR_ACCION: Dict[str, str] = {
    ACCION_ACTUALIZAR: "active",
    ACCION_REACTIVAR: "active",
    ACCION_PAUSAR: "paused",
    ACCION_ELIMINAR: "closed",
}
# Estado que guarda el sistema al confirmar cada acción
ESTADO_LOCAL_POR_ACCION: Dict[str, str] = {
    ACCION_ACTUALIZAR: ESTADO_ACTIVA,
    ACCION_REACTIVAR: ESTADO_ACTIVA,
    ACCION_PAUSAR: ESTADO_PAUSADA,
    ACCION_ELIMINAR: ESTADO_ELIMINADA,
}

COLUMNAS_NUEVAS: List[str] = [
    "tipo_producto", "sku", "id_price_shoes", "titulo", "marca", "modelo", "genero", "color",
    "talla", "cantidad", "precio", "moneda", "condicion", "tipo_publicacion", "descripcion",
    "foto_1", "foto_2", "foto_3", "foto_4",
    "costo_socio", "ganancia_estimada", "piezas_en_tienda", "tienda", "enlace_price_shoes",
]
COLUMNAS_ACTUALIZAR: List[str] = [
    "tipo_producto", "accion", "estado_ml", "ml_item_id", "sku", "id_price_shoes", "titulo",
    "talla", "cantidad", "precio", "motivo", "piezas_en_tienda", "tienda",
]
COLUMNAS_OMITIDOS: List[str] = ["id_price_shoes", "modelo", "tipo_producto", "motivo"]


@dataclass
class Producto:
    """Un producto seleccionado o publicado, con sus existencias en la tienda activa."""

    id_ps: str
    nombre: str
    tipo: str
    costo: float
    url: str
    marca: str
    seleccionado: bool
    tallas: Dict[str, int] = field(default_factory=dict)  # talla -> piezas en la tienda
    revisado: bool = False                                 # ya se leyeron sus existencias en la tienda
    estado_ml: str = ""                                    # '', activa, pausada o eliminada
    ml_item_id: str = ""

    @property
    def piezas_en_tienda(self) -> int:
        return sum(self.tallas.values())

    @property
    def publicado(self) -> bool:
        return self.estado_ml in (ESTADO_ACTIVA, ESTADO_PAUSADA)


# ---------------------------------------------------------------------------
# BASE DE DATOS
# ---------------------------------------------------------------------------
def asegurar_tabla_publicaciones(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS publicaciones_ml (
            id_ps TEXT PRIMARY KEY,
            ml_item_id TEXT DEFAULT '',
            estado TEXT NOT NULL DEFAULT 'activa',
            fecha_publicada TIMESTAMP,
            fecha_actualizada TIMESTAMP
        )
        """
    )
    conn.commit()


def _clave(id_ps: Any) -> str:
    texto = str(id_ps).strip()
    return texto.lstrip("0") or texto


def _expresion_marca(conn: sqlite3.Connection) -> str:
    """Expresión SQL de la marca según las columnas que existan en `zapatos_candidatos`."""
    columnas = {c[1] for c in conn.execute("PRAGMA table_info(zapatos_candidatos)")}
    disponibles = [c for c in ("marca_extraida", "marca_detectada") if c in columnas]
    if not disponibles:
        return "''"
    return "COALESCE(" + ", ".join(f"NULLIF(TRIM({c}), '')" for c in disponibles) + ", '')"


def _existencias_por_producto(conn: sqlite3.Connection, tienda_id: int) -> Dict[str, Dict[str, int]]:
    """{id sin ceros: {talla: piezas}} de la tienda indicada."""
    existencias: Dict[str, Dict[str, int]] = {}
    for id_ps, talla, cantidad in conn.execute(
        "SELECT id_ps, talla, cantidad FROM stock_tienda WHERE tienda_id = ? ORDER BY id_ps, talla", (tienda_id,)
    ):
        existencias.setdefault(_clave(id_ps), {})[str(talla)] = int(cantidad or 0)
    return existencias


def cargar_productos(conn: sqlite3.Connection, tienda_id: int) -> List[Producto]:
    """Productos seleccionados o con publicación registrada, con existencias y estado en ML."""
    seleccion = {_clave(r[0]) for r in conn.execute("SELECT id_ps FROM seleccion_ml")}
    publicaciones = {
        _clave(id_ps): (str(estado or ""), str(ml_id or ""))
        for id_ps, estado, ml_id in conn.execute("SELECT id_ps, estado, ml_item_id FROM publicaciones_ml")
    }
    existencias = _existencias_por_producto(conn, tienda_id)

    filas = conn.execute(
        f"""
        SELECT id_ps, COALESCE(nombre_modelo, ''), COALESCE(tipo_producto, ''), COALESCE(costo_socio, 0),
               COALESCE(url_real, ''), {_expresion_marca(conn)}
        FROM zapatos_candidatos ORDER BY id_ps
        """
    ).fetchall()

    productos: List[Producto] = []
    for id_ps, nombre, tipo, costo, url, marca in filas:
        clave = _clave(id_ps)
        if clave not in seleccion and clave not in publicaciones:
            continue
        estado, ml_id = publicaciones.get(clave, ("", ""))
        productos.append(Producto(
            id_ps=str(id_ps).strip(), nombre=str(nombre).strip(), tipo=tipo or inv.TIPO_OTROS,
            costo=float(costo or 0), url=str(url).strip(), marca=str(marca).strip(),
            seleccionado=clave in seleccion, tallas=existencias.get(clave, {}),
            revisado=clave in existencias, estado_ml=estado, ml_item_id=ml_id,
        ))
    return productos


# ---------------------------------------------------------------------------
# DATOS DE LA PUBLICACIÓN
# ---------------------------------------------------------------------------
def tallas_publicables(producto: Producto, reserva: int) -> Dict[str, int]:
    """Piezas a ofrecer por talla: existencias de la tienda menos la reserva (se omiten las que quedan en 0)."""
    return {talla: piezas - reserva for talla, piezas in producto.tallas.items() if piezas - reserva > 0}


def armar_titulo(nombre: str, marca: str, genero: str) -> str:
    """Título de hasta 60 caracteres: nombre + marca + género, sin repetir palabras ni cortar a la mitad."""
    palabras: List[str] = []
    for palabra in f"{nombre} {marca} {'' if genero == 'Unisex' else genero.split(' / ')[0]}".split():
        if palabra.lower() not in (p.lower() for p in palabras):
            palabras.append(palabra)
    titulo = ""
    for palabra in palabras:
        candidato = f"{titulo} {palabra}".strip()
        if len(candidato) > LARGO_MAXIMO_TITULO:
            break
        titulo = candidato
    return titulo or nombre[:LARGO_MAXIMO_TITULO]


def rutas_fotos(id_ps: str) -> List[str]:
    """Rutas de las 4 láminas HD del producto ('' para las que falten)."""
    carpeta = DIR_CATALOGO_LISTO / _clave(id_ps)
    return [
        str((carpeta / nombre).resolve()) if (carpeta / nombre).exists() else ""
        for nombre in finanzas.ARCHIVOS_LAMINAS
    ]


def datos_comerciales(producto: Producto) -> Dict[str, Any]:
    """Género, color, precio de venta y ganancia estimada (mismas reglas del motor financiero)."""
    genero, categoria, color = finanzas.clasificar_atributos_catalogo(producto.nombre, list(producto.tallas))
    economia = finanzas.calcular_unit_economics_por_articulo(producto.costo, categoria)
    return {
        "genero": genero,
        "color": color,
        "precio": economia["precio_ml"],
        "ganancia": economia["utilidad_neta_ml"],
        "titulo": armar_titulo(producto.nombre, producto.marca, genero),
    }


def motivo_no_publicable(producto: Producto, umbral: int, reserva: int) -> str:
    """Por qué un producto seleccionado no puede publicarse; '' si sí puede."""
    if not producto.revisado:
        return "Sin existencias revisadas en la tienda (ejecuta revisar_existencias.py)"
    if producto.piezas_en_tienda < umbral:
        return f"Solo {producto.piezas_en_tienda} piezas en la tienda (mínimo {umbral})"
    if producto.costo <= 0:
        return "Sin costo de socio: no se puede calcular el precio"
    if not tallas_publicables(producto, reserva):
        return f"Ninguna talla con más de {reserva} piezas"
    return ""


# ---------------------------------------------------------------------------
# FILAS DE LOS CSV
# ---------------------------------------------------------------------------
def filas_nueva_publicacion(producto: Producto, tienda: str, reserva: int) -> List[Dict[str, Any]]:
    comercial = datos_comerciales(producto)
    fotos = rutas_fotos(producto.id_ps)
    tallas = tallas_publicables(producto, reserva)
    descripcion = (
        f"{producto.nombre} {producto.marca}".strip()
        + ". Producto nuevo y original. Tallas disponibles: " + ", ".join(tallas) + "."
    )
    base = {
        "tipo_producto": producto.tipo,
        "sku": f"PS-{producto.id_ps}",
        "id_price_shoes": producto.id_ps,
        "titulo": comercial["titulo"],
        "marca": producto.marca,
        "modelo": producto.nombre,
        "genero": comercial["genero"],
        "color": comercial["color"],
        "precio": comercial["precio"],
        "moneda": "MXN",
        "condicion": "Nuevo",
        "tipo_publicacion": "Clásica",
        "descripcion": descripcion,
        "foto_1": fotos[0], "foto_2": fotos[1], "foto_3": fotos[2], "foto_4": fotos[3],
        "costo_socio": producto.costo,
        "ganancia_estimada": comercial["ganancia"],
        "piezas_en_tienda": producto.piezas_en_tienda,
        "tienda": tienda,
        "enlace_price_shoes": producto.url,
    }
    return [{**base, "talla": talla, "cantidad": cantidad} for talla, cantidad in tallas.items()]


def decidir_accion(producto: Producto, umbral: int, reserva: int, accion_bajo_stock: str) -> Tuple[str, str]:
    """
    Qué hacer con una publicación vigente. Devuelve (acción, motivo); acción '' = no hacer nada.
    Regla principal: con menos piezas que el umbral la publicación se pausa (o elimina).
    """
    motivo_baja = "" if producto.seleccionado else "Producto deseleccionado en el panel"
    motivo_baja = motivo_baja or motivo_no_publicable(producto, umbral, reserva)

    if producto.estado_ml == ESTADO_PAUSADA:
        return ("", "") if motivo_baja else (ACCION_REACTIVAR, "Inventario recuperado")
    if motivo_baja:
        return accion_bajo_stock, motivo_baja
    return ACCION_ACTUALIZAR, "Actualizar precio y cantidades"


def filas_actualizacion(
    producto: Producto, accion: str, motivo: str, tienda: str, reserva: int
) -> List[Dict[str, Any]]:
    comercial = datos_comerciales(producto)
    base = {
        "tipo_producto": producto.tipo,
        "accion": accion,
        "estado_ml": ESTADO_ML_POR_ACCION[accion],
        "ml_item_id": producto.ml_item_id,
        "sku": f"PS-{producto.id_ps}",
        "id_price_shoes": producto.id_ps,
        "titulo": comercial["titulo"],
        "precio": comercial["precio"],
        "motivo": motivo,
        "piezas_en_tienda": producto.piezas_en_tienda,
        "tienda": tienda,
    }
    if accion in (ACCION_PAUSAR, ACCION_ELIMINAR):
        return [{**base, "talla": "", "cantidad": 0}]  # una sola fila: afecta a toda la publicación
    tallas = tallas_publicables(producto, reserva)
    return [{**base, "talla": talla, "cantidad": cantidad} for talla, cantidad in tallas.items()]


def clasificar_productos(
    productos: Iterable[Producto], umbral: int, reserva: int, accion_bajo_stock: str, tienda: str
) -> Dict[str, Any]:
    """Reparte los productos en filas de nuevas, filas de actualización y omitidos."""
    nuevas: List[Dict[str, Any]] = []
    actualizar: List[Dict[str, Any]] = []
    omitidos: List[Dict[str, Any]] = []
    ids_nuevas: List[str] = []
    acciones: Dict[str, str] = {}

    for producto in productos:
        if producto.publicado:
            accion, motivo = decidir_accion(producto, umbral, reserva, accion_bajo_stock)
            if accion:
                actualizar.extend(filas_actualizacion(producto, accion, motivo, tienda, reserva))
                acciones[producto.id_ps] = accion
            continue
        motivo = motivo_no_publicable(producto, umbral, reserva)
        if motivo:
            omitidos.append({"id_price_shoes": producto.id_ps, "modelo": producto.nombre,
                             "tipo_producto": producto.tipo, "motivo": motivo})
            continue
        nuevas.extend(filas_nueva_publicacion(producto, tienda, reserva))
        ids_nuevas.append(producto.id_ps)

    return {"nuevas": nuevas, "actualizar": actualizar, "omitidos": omitidos,
            "ids_nuevas": ids_nuevas, "acciones": acciones}


# ---------------------------------------------------------------------------
# ESCRITURA DE ARCHIVOS
# ---------------------------------------------------------------------------
def nombre_archivo_tipo(tipo: str) -> str:
    """'Botas y botines' -> 'botas_y_botines' (sin acentos ni espacios)."""
    sin_acentos = unicodedata.normalize("NFKD", tipo).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "_", sin_acentos.lower()).strip("_") or "otros"


def escribir_csv(ruta: Path, columnas: List[str], filas: List[Dict[str, Any]]) -> None:
    with ruta.open("w", newline="", encoding="utf-8-sig") as archivo:  # utf-8-sig: Excel respeta los acentos
        escritor = csv.DictWriter(archivo, fieldnames=columnas)
        escritor.writeheader()
        escritor.writerows(filas)


def escribir_por_tipo(carpeta: Path, prefijo: str, columnas: List[str], filas: List[Dict[str, Any]]) -> Dict[str, int]:
    """Un CSV por tipo de producto. Devuelve {archivo: filas}."""
    por_tipo: Dict[str, List[Dict[str, Any]]] = {}
    for fila in filas:
        por_tipo.setdefault(fila["tipo_producto"], []).append(fila)
    escritos: Dict[str, int] = {}
    for tipo, filas_tipo in sorted(por_tipo.items()):
        nombre = f"{prefijo}_{nombre_archivo_tipo(tipo)}.csv"
        escribir_csv(carpeta / nombre, columnas, filas_tipo)
        escritos[nombre] = len(filas_tipo)
    return escritos


def exportar(conn: sqlite3.Connection, dir_exportes: Path, reserva: int, accion_bajo_stock: str,
             ahora: Optional[datetime] = None) -> Dict[str, Any]:
    """Genera los CSV de la tienda activa y devuelve el resumen de la exportación."""
    ahora = ahora or datetime.now()
    tienda_id, tienda = inv.tienda_activa(conn)
    umbral = inv.umbral_pausa(conn)
    resultado = clasificar_productos(cargar_productos(conn, tienda_id), umbral, reserva, accion_bajo_stock, tienda)

    carpeta = dir_exportes / ahora.strftime("%Y%m%d_%H%M%S")
    carpeta.mkdir(parents=True, exist_ok=True)
    archivos = escribir_por_tipo(carpeta, "nuevas", COLUMNAS_NUEVAS, resultado["nuevas"])
    archivos.update(escribir_por_tipo(carpeta, "actualizar", COLUMNAS_ACTUALIZAR, resultado["actualizar"]))
    if resultado["omitidos"]:
        escribir_csv(carpeta / "omitidos.csv", COLUMNAS_OMITIDOS, resultado["omitidos"])
        archivos["omitidos.csv"] = len(resultado["omitidos"])

    resumen = {
        "carpeta": str(carpeta),
        "fecha": ahora.isoformat(timespec="seconds"),
        "tienda_id": tienda_id,
        "tienda": tienda,
        "umbral_pausa": umbral,
        "reserva_por_talla": reserva,
        "archivos": archivos,
        "ids_nuevas": resultado["ids_nuevas"],
        "acciones": resultado["acciones"],
        "omitidos": len(resultado["omitidos"]),
        "confirmada": False,
    }
    (carpeta / ARCHIVO_RESUMEN).write_text(json.dumps(resumen, ensure_ascii=False, indent=2), encoding="utf-8")
    return resumen


# ---------------------------------------------------------------------------
# CONFIRMACIÓN (lo que ya se subió a Mercado Libre)
# ---------------------------------------------------------------------------
def ultima_exportacion(dir_exportes: Path) -> Optional[Path]:
    carpetas = sorted(c for c in dir_exportes.glob("*") if (c / ARCHIVO_RESUMEN).exists()) if dir_exportes.exists() else []
    return carpetas[-1] if carpetas else None


def confirmar_exportacion(conn: sqlite3.Connection, carpeta: Path, ahora: Optional[datetime] = None) -> Dict[str, int]:
    """Registra como aplicada en Mercado Libre una exportación. Devuelve cuántas publicaciones cambió."""
    ruta_resumen = carpeta / ARCHIVO_RESUMEN
    resumen = json.loads(ruta_resumen.read_text(encoding="utf-8"))
    if resumen.get("confirmada"):
        return {"ya_confirmada": 1}
    fecha = (ahora or datetime.now()).isoformat(timespec="seconds")

    conn.executemany(
        """
        INSERT INTO publicaciones_ml (id_ps, estado, fecha_publicada, fecha_actualizada) VALUES (?, ?, ?, ?)
        ON CONFLICT(id_ps) DO UPDATE SET estado = excluded.estado, fecha_publicada = excluded.fecha_publicada,
                                         fecha_actualizada = excluded.fecha_actualizada
        """,
        [(id_ps, ESTADO_ACTIVA, fecha, fecha) for id_ps in resumen["ids_nuevas"]],
    )
    conn.executemany(
        "UPDATE publicaciones_ml SET estado = ?, fecha_actualizada = ? WHERE id_ps = ?",
        [(ESTADO_LOCAL_POR_ACCION[accion], fecha, id_ps) for id_ps, accion in resumen["acciones"].items()],
    )
    conn.commit()

    resumen["confirmada"] = True
    ruta_resumen.write_text(json.dumps(resumen, ensure_ascii=False, indent=2), encoding="utf-8")
    conteo: Dict[str, int] = {"nuevas": len(resumen["ids_nuevas"])}
    for accion in resumen["acciones"].values():
        conteo[accion.lower()] = conteo.get(accion.lower(), 0) + 1
    return conteo


def registrar_ids_ml(conn: sqlite3.Connection, ruta_csv: Path) -> int:
    """Guarda los ID de Mercado Libre de un CSV con columnas id_price_shoes y ml_item_id."""
    with ruta_csv.open(newline="", encoding="utf-8-sig") as archivo:
        pares = [
            (str(fila.get("ml_item_id") or "").strip(), str(fila.get("id_price_shoes") or "").strip())
            for fila in csv.DictReader(archivo)
        ]
    pares = [(ml_id, id_ps) for ml_id, id_ps in pares if ml_id and id_ps]
    conn.executemany("UPDATE publicaciones_ml SET ml_item_id = ? WHERE LTRIM(id_ps, '0') = LTRIM(?, '0')", pares)
    conn.commit()
    return len(pares)


# ---------------------------------------------------------------------------
# ENTRADA
# ---------------------------------------------------------------------------
def leer_argumentos(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Genera los CSV para Mercado Libre por tipo de producto.")
    parser.add_argument("--bajo-stock", choices=["pausar", "eliminar"], default="pausar",
                        help="Qué hacer con las publicaciones bajo el umbral de piezas")
    parser.add_argument("--reserva", type=int, default=RESERVA_POR_TALLA_DEFAULT,
                        help="Piezas por talla que no se ofrecen")
    parser.add_argument("--confirmar", action="store_true", help="Marca la última exportación como subida a ML")
    parser.add_argument("--ids-ml", type=Path, default=None, help="CSV con id_price_shoes y ml_item_id")
    parser.add_argument("--bd", type=Path, default=inv.RUTA_BD)
    parser.add_argument("--salida", type=Path, default=DIR_EXPORTES)
    return parser.parse_args(argv)


def imprimir_resumen(resumen: Dict[str, Any]) -> None:
    print(f"Exportación para Mercado Libre · tienda {resumen['tienda']} · umbral {resumen['umbral_pausa']} piezas")
    print(f"Carpeta: {resumen['carpeta']}")
    if not resumen["archivos"]:
        print("No hay nada que exportar: selecciona productos en el panel y revisa sus existencias.")
        return
    for archivo, filas in resumen["archivos"].items():
        print(f"  {archivo}: {filas} filas")
    acciones = list(resumen["acciones"].values())
    print(f"Nuevas: {len(resumen['ids_nuevas'])} productos · "
          + " · ".join(f"{a.lower()}: {acciones.count(a)}" for a in sorted(set(acciones)))
          + f" · omitidos: {resumen['omitidos']}")
    print("Después de subirlos a Mercado Libre ejecuta: python exportar_mercadolibre.py --confirmar")


def main(argv: Optional[List[str]] = None) -> int:
    args = leer_argumentos(argv)
    conn = inv.conectar(args.bd)
    try:
        inv.asegurar_esquema(conn)
        asegurar_tabla_publicaciones(conn)
        if args.ids_ml:
            print(f"ID de Mercado Libre registrados: {registrar_ids_ml(conn, args.ids_ml)}")
            return 0
        if args.confirmar:
            carpeta = ultima_exportacion(args.salida)
            if carpeta is None:
                print("No hay exportaciones que confirmar.")
                return 1
            print(f"Exportación {carpeta.name} confirmada: {confirmar_exportacion(conn, carpeta)}")
            return 0
        accion = ACCION_ELIMINAR if args.bajo_stock == "eliminar" else ACCION_PAUSAR
        imprimir_resumen(exportar(conn, args.salida, max(0, args.reserva), accion))
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
