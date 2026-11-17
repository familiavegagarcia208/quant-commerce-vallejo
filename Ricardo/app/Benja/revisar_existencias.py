"""
========================================================================================
REVISIÓN DE EXISTENCIAS POR TIENDA (se ejecuta dos veces al día: mañana y tarde)
========================================================================================
Visita la ficha pública de cada producto (no requiere sesión de socio), lee el inventario
de la tienda activa y avisa qué productos seleccionados deben pausarse por tener menos
piezas que el umbral (15 por defecto).

Uso:
  python revisar_existencias.py                  # revisa los productos seleccionados
  python revisar_existencias.py --explorar 60    # además revisa 60 productos aún sin revisar
  python revisar_existencias.py --tienda 30      # usa otra tienda solo en esta corrida
  python revisar_existencias.py --json           # resumen en JSON (para n8n u otro orquestador)
========================================================================================
"""

from __future__ import annotations

import argparse
import json
import random
import sqlite3
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

import inventario_tiendas as inv

URL_BASE = "https://www.priceshoes.com"
ESPERA_INVENTARIO_MS = 9000
MAX_FALLOS_SEGUIDOS = 8
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)

Producto = Tuple[str, str, str]  # (id_ps, nombre, url)


# ---------------------------------------------------------------------------
# SELECCIÓN DE PRODUCTOS A REVISAR
# ---------------------------------------------------------------------------
def productos_por_explorar(conn: sqlite3.Connection, tienda_id: int, limite: int) -> List[Producto]:
    """Productos del catálogo que todavía no tienen existencias registradas en la tienda."""
    if limite <= 0:
        return []
    filas = conn.execute(
        """
        SELECT z.id_ps, COALESCE(z.nombre_modelo, ''), COALESCE(z.url_real, '')
        FROM zapatos_candidatos z
        WHERE COALESCE(z.activo_monitoreo, 1) = 1
          AND z.id_ps NOT IN (SELECT id_ps FROM stock_tienda WHERE tienda_id = ?)
        ORDER BY z.ultima_sincronizacion IS NOT NULL, z.id_ps
        LIMIT ?
        """,
        (tienda_id, limite),
    ).fetchall()
    return [(str(i).strip(), str(n).strip(), str(u).strip()) for i, n, u in filas]


def armar_lista(conn: sqlite3.Connection, tienda_id: int, explorar: int) -> List[Producto]:
    lista = inv.productos_seleccionados(conn)
    vistos = {p[0] for p in lista}
    lista += [p for p in productos_por_explorar(conn, tienda_id, explorar) if p[0] not in vistos]
    return lista


def url_producto(id_ps: str, url: str) -> str:
    return url if url.startswith("http") else f"{URL_BASE}/buscar?q={id_ps}"


# ---------------------------------------------------------------------------
# LECTURA DE UNA FICHA
# ---------------------------------------------------------------------------
class CapturaInventario:
    """Escucha las respuestas que la propia página pide al sitio y guarda las de inventario."""

    def __init__(self) -> None:
        self.filas: List[Dict[str, Any]] = []
        self.tiendas: List[Dict[str, Any]] = []

    def al_responder(self, respuesta: Any) -> None:
        url = respuesta.url
        if "priceshoes.digital" not in url or respuesta.status != 200:
            return
        es_inventario = "inventories" in url and "arrival-date" not in url
        es_tiendas = "/stores/" in url and "inventories" not in url
        if not (es_inventario or es_tiendas):
            return
        try:
            datos = respuesta.json()
        except Exception:
            return
        if es_inventario:
            self.filas.extend(inv.extraer_inventario_json(datos))
        else:
            self.tiendas.extend(inv.extraer_tiendas_json(datos))

    def tiene_tienda(self, tienda_id: int) -> bool:
        return any(f["tienda_id"] == tienda_id for f in self.filas)


def abrir_ficha(page: Any, id_ps: str, url: str) -> bool:
    """Navega a la ficha del producto. Devuelve False si no se llegó a una ficha."""
    page.goto(url_producto(id_ps, url), timeout=35000, wait_until="domcontentloaded")
    if "/buscar" in page.url:
        enlace = page.locator(f"a[href*='{id_ps}']").first
        if enlace.count() == 0:
            return False
        href = enlace.get_attribute("href") or ""
        page.goto(href if href.startswith("http") else f"{URL_BASE}{href}", timeout=35000,
                  wait_until="domcontentloaded")
    return "/productos/" in page.url


def esperar_inventario(page: Any, captura: CapturaInventario, tienda_id: int, espera_ms: int) -> None:
    transcurrido = 0
    while transcurrido < espera_ms and not captura.tiene_tienda(tienda_id):
        page.wait_for_timeout(300)
        transcurrido += 300


def pedir_inventario_por_talla(page: Any, captura: CapturaInventario, tallas: List[str]) -> None:
    """
    Respaldo: abre "Ver disponibilidad en tiendas" y elige cada talla; el sitio responde
    con las existencias de todas las tiendas para esa talla.
    """
    boton = page.locator("text=Ver disponibilidad en tiendas").first
    if boton.count() == 0:
        return
    boton.scroll_into_view_if_needed()
    boton.click(force=True)
    page.wait_for_timeout(1500)
    for talla in tallas:
        opcion = page.get_by_text(talla, exact=True).last
        if opcion.count() == 0:
            continue
        opcion.click(force=True)
        page.wait_for_timeout(900)


def leer_producto(page: Any, id_ps: str, url: str, tienda_id: int) -> Tuple[Dict[str, Any], CapturaInventario]:
    """Devuelve (ficha, captura). `ficha` vacía significa que no se llegó a una ficha de producto."""
    captura = CapturaInventario()
    page.on("response", captura.al_responder)
    try:
        if not abrir_ficha(page, id_ps, url):
            return {}, captura
        ficha = inv.extraer_ficha_next_data(page.content())
        esperar_inventario(page, captura, tienda_id, ESPERA_INVENTARIO_MS)
        if not captura.tiene_tienda(tienda_id):
            pedir_inventario_por_talla(page, captura, ficha.get("tallas", []))
        if ficha:
            ficha["url"] = page.url.split("#")[0].split("?")[0]
        return ficha, captura
    finally:
        page.remove_listener("response", captura.al_responder)


# ---------------------------------------------------------------------------
# CICLO DE REVISIÓN
# ---------------------------------------------------------------------------
def revisar_producto(conn: sqlite3.Connection, page: Any, producto: Producto, tienda_id: int, turno: str) -> Dict[str, Any]:
    id_ps, _nombre, url = producto
    ficha, captura = leer_producto(page, id_ps, url, tienda_id)
    if captura.tiendas:
        inv.registrar_tiendas(conn, captura.tiendas)
    if ficha.get("url"):
        conn.execute("UPDATE zapatos_candidatos SET url_real = ? WHERE id_ps = ?", (ficha["url"], id_ps))
    return inv.guardar_revision(conn, id_ps, tienda_id, captura.filas, ficha or None, turno)


def pausa_humana() -> None:
    time.sleep(min(15.0, max(2.5, random.lognormvariate(mu=1.4, sigma=0.4))))


def revisar_lista(conn: sqlite3.Connection, page: Any, lista: List[Producto], tienda_id: int, turno: str,
                  avisar: Any = print) -> List[Dict[str, Any]]:
    resultados: List[Dict[str, Any]] = []
    fallos_seguidos = 0
    for indice, producto in enumerate(lista, 1):
        try:
            resultado = revisar_producto(conn, page, producto, tienda_id, turno)
        except Exception as error:
            conn.rollback()
            resultado = {"id_ps": producto[0], "estado": "error", "stock_total": None, "alerta": False,
                         "detalle": str(error)}
        resultado["nombre"] = producto[1]
        resultados.append(resultado)
        avisar(f"[{indice}/{len(lista)}] {producto[0]} {producto[1]} -> {describir(resultado)}")

        fallos_seguidos = 0 if resultado["estado"] == "ok" else fallos_seguidos + 1
        if fallos_seguidos >= MAX_FALLOS_SEGUIDOS:
            avisar(f"Se detiene la revisión: {fallos_seguidos} productos seguidos sin datos (posible bloqueo).")
            break
        if indice < len(lista):
            pausa_humana()
    return resultados


def describir(resultado: Dict[str, Any]) -> str:
    if resultado["estado"] != "ok":
        return f"sin datos ({resultado['estado']})"
    texto = f"{resultado['stock_total']} piezas"
    return texto + "  ** PAUSAR **" if resultado["alerta"] else texto


def armar_resumen(conn: sqlite3.Connection, tienda: Tuple[int, str], turno: str,
                  resultados: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {
        "tienda_id": tienda[0],
        "tienda": tienda[1],
        "turno": turno,
        "umbral_pausa": inv.umbral_pausa(conn),
        "revisados": len(resultados),
        "con_datos": sum(1 for r in resultados if r["estado"] == "ok"),
        "sin_datos": [r["id_ps"] for r in resultados if r["estado"] != "ok"],
        "alertas_pausa": inv.alertas_activas(conn, tienda[0]),
    }


def imprimir_resumen(resumen: Dict[str, Any]) -> None:
    print("=" * 80)
    print(f"Tienda {resumen['tienda']} · turno {resumen['turno']} · "
          f"{resumen['con_datos']}/{resumen['revisados']} productos con datos")
    alertas = resumen["alertas_pausa"]
    if not alertas:
        print(f"Sin alertas: ningún producto seleccionado bajo {resumen['umbral_pausa']} piezas.")
        return
    print(f"PAUSAR {len(alertas)} productos (menos de {resumen['umbral_pausa']} piezas):")
    for a in alertas:
        print(f"  - {a['id_ps']} {a['nombre']}: {a['stock_total']} piezas")


# ---------------------------------------------------------------------------
# ENTRADA
# ---------------------------------------------------------------------------
def leer_argumentos(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Revisa existencias por tienda y genera alertas de pausa.")
    parser.add_argument("--tienda", type=int, default=None, help="ID de tienda solo para esta corrida")
    parser.add_argument("--explorar", type=int, default=0, help="Productos sin revisar a sumar a la corrida")
    parser.add_argument("--turno", choices=["mañana", "tarde"], default=None)
    parser.add_argument("--visible", action="store_true", help="Muestra el navegador")
    parser.add_argument("--json", action="store_true", help="Imprime el resumen en JSON")
    return parser.parse_args(argv)


def ejecutar(args: argparse.Namespace) -> Dict[str, Any]:
    from playwright.sync_api import sync_playwright  # se importa aquí para poder probar sin navegador

    avisar = (lambda *_: None) if args.json else print
    conn = inv.conectar()
    try:
        inv.asegurar_esquema(conn)
        tienda = inv.tienda_activa(conn)
        if args.tienda is not None:
            nombre = dict(inv.listar_tiendas(conn, incluir_ecommerce=True)).get(args.tienda, str(args.tienda))
            tienda = (args.tienda, nombre)
        turno = args.turno or inv.turno_actual()
        lista = armar_lista(conn, tienda[0], args.explorar)
        avisar(f"Revisión de existencias · tienda {tienda[1]} · turno {turno} · {len(lista)} productos")

        resultados: List[Dict[str, Any]] = []
        if lista:
            favorita = inv.datos_tienda_json(conn, tienda[0])
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=not args.visible)
                try:
                    context = browser.new_context(user_agent=USER_AGENT, viewport={"width": 1366, "height": 768})
                    context.add_init_script(
                        f"try {{ localStorage.setItem('ps-favorite-store', {json.dumps(favorita)}); }} catch (e) {{}}"
                    )
                    resultados = revisar_lista(conn, context.new_page(), lista, tienda[0], turno, avisar)
                finally:
                    browser.close()
        return armar_resumen(conn, tienda, turno, resultados)
    finally:
        conn.close()


def main(argv: Optional[List[str]] = None) -> int:
    args = leer_argumentos(argv)
    resumen = ejecutar(args)
    if args.json:
        print(json.dumps(resumen, ensure_ascii=False))
    else:
        imprimir_resumen(resumen)
    return 0


if __name__ == "__main__":
    sys.exit(main())
