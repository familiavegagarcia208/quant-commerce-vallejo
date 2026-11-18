# -*- coding: utf-8 -*-
"""
========================================================================================
INVENTARIO POR TIENDA: tienda activa, tipos de producto, Matriz Maslow y alertas de pausa
========================================================================================
Biblioteca compartida que usan `paso3_recolector_ligero.py`, `exportar_mercadolibre.py`
y `paso4_panel_control.py`:

  - Tienda activa configurable (cualquier sucursal de Price Shoes, no solo Vallejo).
  - Clasificación de cada producto por tipo y por cuadrante de la Matriz de Maslow.
  - Sincronización automática entre `historial_stock_21_dias` y `stock_tienda`.
  - Alertas de pausa: producto seleccionado con menos de N piezas en la tienda activa.
========================================================================================
"""

from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import re
import sqlite3
from typing import Any, Dict, Iterable, List, Optional, Tuple

RUTA_BD: Path = Path("data/zapateria_vallejo.db")

UMBRAL_PAUSA_DEFAULT: int = 15
TIENDA_DEFAULT: Tuple[int, str] = (1, "Vallejo")
ID_TIENDA_ECOMMERCE: int = 29

TIENDAS_SEMILLA: Dict[int, str] = {
    1: "Vallejo", 3: "León", 4: "Iztapalapa", 5: "Ecatepec", 8: "Chalco", 17: "Cordoba",
    22: "Naucalpan", 25: "Olivar Del Conde", 29: "Ecommerce", 30: "Centro", 33: "Ixtapaluca",
    35: "Chihuahua", 36: "Nicolas Romero", 39: "Mexicali", 42: "Atemajac", 54: "Obregón",
    56: "Nogales", 59: "Cardenas", 61: "Hermosillo", 63: "Coatzacoalcos", 68: "Puebla",
    76: "Aguascalientes", 77: "Celaya", 87: "Puebla Centro", 96: "Medrano", 97: "Miravalle",
    98: "Arco Norte", 101: "Merida",
}

# ---------------------------------------------------------------------------
# TIPOS DE PRODUCTO Y MATRIZ DE MASLOW (CUADRANTES VERDES, NARANJAS Y GRISES)
# ---------------------------------------------------------------------------
TIPO_OTROS = "Otros"
TIPO_SIN_CLASIFICAR = "Sin clasificar"
_RE_NOMBRE_PROVISIONAL = re.compile(r"^\s*(?:(?:cat[aá]logo|modelo)\s+\S+)?\s*$", re.IGNORECASE)

_REGLAS_NO_CALZADO: List[Tuple[str, str]] = [
    ("Hogar", r"cobertor|edred[oó]n|s[aá]bana|almohada|coj[ií]n|toalla|cortina|tapete|sart[eé]n|olla|vajilla|"
              r"termo|botella|contenedor|licuadora|cafetera|peluche|hogar|frasco|vidrio|cristal|tapa|vaso|taza|"
              r"plato|recipiente|jarra|jarr[oó]n|molde|refractario|cubierto|cuchara|tenedor|cuchillo|especiero|"
              r"organizador|canasta|cesta|vela|difusor|florero|portarretrato|adorno|esfera|guirnalda|arrocera|"
              r"comal|cacerola|utensilio|frazada|mantel|tupper|cilindro|l[aá]mpara|charola|ensaladera|azucarera|"
              r"salero|servilletero|escurridor|basurero|juguete|mu[ñn]eca|rompecabezas"),
    ("Ropa", r"ropa|pantal[oó]n|pantalones|jeans|mezclilla|playera|camisa|blusa|chamarra|sudadera|su[eé]ter|"
             r"chaleco|vestido|falda|short|bermuda|legging|mall[oó]n|jogger|pants|pijama|bata|boxer|b[oó]xer|"
             r"calz[oó]n|trusa|brasier|bra|faja|calcet[ií]n|calcetines|calceta|calcetas|tines|medias|"
             r"abrigo|saco|blazer|conjunto|polo|top|body|camiseta|mameluco|sweater|capa|sobrecamisa|"
             r"gabardina|panty|jersey|romper|filipina|calentador|calentadores|bikini"),
    ("Accesorios", r"accesorio|bolsa|bolso|mochila|minimochila|backpack|cartera|cangurera|gorra|gorro|sombrero|"
                   r"cintur[oó]n|reloj|lentes|gafas|bufanda|guantes|paraguas|lonchera|lapicera|llavero|maleta|"
                   r"perfume|fragancia|loci[oó]n|shampoo|crema|cosmetiquera|monedero|collar|pulsera|arete|"
                   r"anillo|diadema|joyer[ií]a|aud[ií]fono|bocina|cepillo|peine|espejo|estuche"),
]

_REGLAS_CALZADO: List[Tuple[str, str]] = [
    ("Sandalias", r"sandalia|sandalias|huarache|huaraches|chancla|chanclas|slide|slides"),
    ("Pantuflas", r"pantufla|pantuflas"),
    ("Botas y botines", r"bota|botas|bot[ií]n|botines|hiker|borcego"),
    ("Zapatillas", r"zapatilla|zapatillas|tac[oó]n|stiletto|pump|pumps"),
    ("Tenis", r"tenis|sneaker|sneakers|running|deportivo|deportivos|skate"),
    ("Zapatos", r"zapato|zapatos|choclo|choclos|mocas[ií]n|mocasines|derby|oxford|loafer|balerina|balerinas|"
                r"ballerina|flat|cu[ñn]a|cu\s+a|sueco|zueco|escolar|top\s*sider|alpargata|calzado"),
]

TIPOS_CALZADO: Tuple[str, ...] = tuple(t for t, _ in _REGLAS_CALZADO)
TIPOS_NO_CALZADO: Tuple[str, ...] = tuple(t for t, _ in _REGLAS_NO_CALZADO)

TIPOS_PRODUCTO: List[str] = (
    list(TIPOS_CALZADO) + list(TIPOS_NO_CALZADO) + [TIPO_OTROS, TIPO_SIN_CLASIFICAR]
)

# Etapas y cuadrantes de la Matriz de Necesidades de Ricardo
ETAPA_VERDE = "🟢 Empezar aquí (Verdes)"
ETAPA_NARANJA = "🟠 Segunda etapa (Naranjas)"
ETAPA_GRIS = "⚪ Dejar para después"

CUADRANTE_ESCOLAR = "🟢 Calzado escolar (Niños 0-12)"
CUADRANTE_LICENCIA = "🟢 Tenis con licencia (Niños 0-12)"
CUADRANTE_SANDALIA_BOTA = "🟢 Sandalias y botas de moda (Adultos 25-54)"
CUADRANTE_PANTUFLA = "🟢 Pantuflas (Adultos 25-54)"

CUADRANTES_VERDES: Tuple[str, ...] = (
    CUADRANTE_ESCOLAR,
    CUADRANTE_LICENCIA,
    CUADRANTE_SANDALIA_BOTA,
    CUADRANTE_PANTUFLA,
)

# Catálogos 100% de calzado (solo para priorizar SKUs que aún tienen nombre provisional)
_CATALOGOS_SOLO_CALZADO = {
    "1370231", "1368789", "1345253", "1380328", "1380359", "1380363", "1380367",
}


def _coincide(patron: str, texto: str) -> bool:
    return bool(re.search(rf"\b(?:{patron})(?:s|es)?\b", texto, re.IGNORECASE))


def _primer_tipo(texto: str) -> str:
    mejor: Optional[Tuple[int, str]] = None
    for tipo, patron in _REGLAS_CALZADO + _REGLAS_NO_CALZADO:
        m = re.search(rf"\b(?:{patron})(?:s|es)?\b", texto, re.IGNORECASE)
        if m and (mejor is None or m.start() < mejor[0]):
            mejor = (m.start(), tipo)
    return mejor[1] if mejor else ""


def clasificar_tipo_producto(nombre: str, categoria_ps: str = "") -> str:
    partes = [p.strip() for p in str(categoria_ps or "").split(">") if p.strip()]
    nombre = str(nombre or "").strip()
    if not partes and _RE_NOMBRE_PROVISIONAL.match(nombre):
        return TIPO_SIN_CLASIFICAR

    raiz = partes[0] if partes else ""
    # Priorizar el nombre real del producto y las subcategorías antes que la raíz
    tipo = _primer_tipo(nombre) or _primer_tipo(" ".join(partes[1:])) or _primer_tipo(raiz)
    if tipo:
        return tipo
    return "Zapatos" if _coincide("calzado", raiz) else TIPO_OTROS


def clasificar_matriz_maslow(nombre: str, origen_catalogo: str = "", tipo_prod: str = "") -> Tuple[str, str]:
    """
    Evalúa la etapa y cuadrante de la Matriz de Maslow con doble candado:
    ningún artículo de Hogar, Ropa o Accesorios puede entrar a los cuadrantes verdes/naranjas.
    """
    nom_clean = str(nombre or "").strip()
    cat = str(origen_catalogo or "").lower()
    tipo = str(tipo_prod or "").strip()

    # Recalcular tipo desde el nombre si viene vacío o genérico
    if tipo in ("", TIPO_OTROS, TIPO_SIN_CLASIFICAR):
        tipo = clasificar_tipo_producto(nom_clean)

    # Candado 1: Si es Hogar, Ropa o Accesorios, sale de inmediato hacia Grises
    if tipo in TIPOS_NO_CALZADO:
        return ETAPA_GRIS, "⚪ Dejar para después"

    es_provisional = bool(_RE_NOMBRE_PROVISIONAL.match(nom_clean))
    es_calzado_confirmado = tipo in TIPOS_CALZADO

    # Candado 2: Si ya tiene nombre real y NO es calzado confirmado, jamás entra a Verdes/Naranjas
    if not es_provisional and not es_calzado_confirmado:
        return ETAPA_GRIS, "⚪ Dejar para después"

    # Si aún tiene nombre provisional, solo entra a Verdes si viene de un catálogo 100% de calzado
    if es_provisional and not any(c in cat for c in _CATALOGOS_SOLO_CALZADO):
        return ETAPA_GRIS, "⚪ Dejar para después"

    txt = f"{nom_clean} {cat} {tipo}".lower()

    # 1. 🟢 Calzado escolar (Niños 0-12 · Fisiológicas)
    if "1370231" in cat or re.search(r"\b(escolar|escolares|colegial)\b", txt):
        return ETAPA_VERDE, CUADRANTE_ESCOLAR

    # 2. 🟢 Tenis con licencia (Niños 0-12 · Pertenencia)
    if (
        tipo in ("Tenis", "Zapatos", "Botas y botines", "Sandalias")
        and (
            any(c in cat for c in ("1348800", "1368804", "1348783", "1378033", "1381556"))
            or re.search(
                r"\b(licencia|disney|minnie|mickey|stitch|sonic|bluey|paw\s*patrol|spiderman|barbie|"
                r"hello\s*kitty|kuromi|mario|frozen|princesas|avengers|cars|toy\s*story|minions|peppa|batman|luces)\b",
                txt,
            )
        )
    ):
        return ETAPA_VERDE, CUADRANTE_LICENCIA

    # 3. 🟢 Pantuflas (Adultos 25-54 · Fisiológicas) -> SOLO si realmente es pantufla
    if tipo == "Pantuflas" or re.search(r"\b(pantufla|pantuflas)\b", nom_clean.lower()):
        return ETAPA_VERDE, CUADRANTE_PANTUFLA

    # 4. 🟢 Sandalias y botas de moda (Adultos 25-54 · Pertenencia)
    es_industrial_o_nino = bool(
        re.search(r"\b(industrial|casco|diel[eé]ctrico|goodyear|ni[ñn][oa]|infantil|kids|beb[eé])\b", txt)
    )
    if not es_industrial_o_nino and (
        tipo in ("Sandalias", "Botas y botines")
        or (es_provisional and any(c in cat for c in ("1368789", "1345253", "1380328", "1380359", "1380363", "1380367")))
    ):
        return ETAPA_VERDE, CUADRANTE_SANDALIA_BOTA

    # 5. 🟠 Segunda etapa: Línea confort y Tenis urbanos de dama
    if any(c in cat for c in ("1353843", "1381316")) or re.search(r"\b(confort|med\s*chef|ortop[eé]dico|descanso)\b", txt):
        return ETAPA_NARANJA, "🟠 Línea confort (Mayores 55+)"

    if (
        tipo == "Tenis"
        and not re.search(r"\b(futbol|f[uú]tbol|correr|running|caballero|hombre)\b", txt)
    ):
        return ETAPA_NARANJA, "🟠 Tenis urbanos de dama (Jóvenes 13-24)"

    return ETAPA_GRIS, "⚪ Dejar para después"


# ---------------------------------------------------------------------------
# BASE DE DATOS
# ---------------------------------------------------------------------------
def conectar(ruta: Path | str = RUTA_BD) -> sqlite3.Connection:
    Path(ruta).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(ruta), timeout=30)
    conn.execute("PRAGMA journal_mode=WAL;")
    return conn


def asegurar_esquema(conn: sqlite3.Connection) -> None:
    """Crea tablas y columnas nuevas (idempotente) y sincroniza el stock de Vallejo capturado."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS tiendas (
            id INTEGER PRIMARY KEY,
            nombre TEXT NOT NULL,
            ciudad TEXT DEFAULT '',
            estado TEXT DEFAULT '',
            datos_json TEXT DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS configuracion (
            clave TEXT PRIMARY KEY,
            valor TEXT
        );
        CREATE TABLE IF NOT EXISTS stock_tienda (
            id_ps TEXT NOT NULL,
            tienda_id INTEGER NOT NULL,
            talla TEXT NOT NULL,
            cantidad INTEGER NOT NULL DEFAULT 0,
            fecha TIMESTAMP,
            PRIMARY KEY (id_ps, tienda_id, talla)
        );
        CREATE TABLE IF NOT EXISTS revisiones_stock (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            id_ps TEXT NOT NULL,
            tienda_id INTEGER NOT NULL,
            turno TEXT,
            fecha TIMESTAMP,
            stock_total INTEGER,
            estado TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_revisiones_ps ON revisiones_stock(id_ps, tienda_id, fecha);
        CREATE TABLE IF NOT EXISTS alertas_pausa (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            id_ps TEXT NOT NULL,
            tienda_id INTEGER NOT NULL,
            stock_total INTEGER,
            umbral INTEGER,
            fecha_creada TIMESTAMP,
            fecha_actualizada TIMESTAMP,
            activa INTEGER DEFAULT 1,
            atendida INTEGER DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS idx_alertas_activas ON alertas_pausa(tienda_id, activa);
        CREATE TABLE IF NOT EXISTS seleccion_ml (
            id_ps TEXT PRIMARY KEY,
            actualizado TEXT DEFAULT CURRENT_TIMESTAMP
        );
        """
    )

    tablas = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "zapatos_candidatos" in tablas:
        _asegurar_columnas_producto(conn)
        _clasificar_pendientes(conn)
    conn.executemany("INSERT OR IGNORE INTO tiendas (id, nombre) VALUES (?, ?)", list(TIENDAS_SEMILLA.items()))
    if "historial_stock_21_dias" in tablas:
        _migrar_stock_vallejo(conn)
    conn.commit()


def _columnas(conn: sqlite3.Connection, tabla: str) -> set:
    return {c[1] for c in conn.execute(f"PRAGMA table_info({tabla})")}


def _asegurar_columnas_producto(conn: sqlite3.Connection) -> None:
    existentes = _columnas(conn, "zapatos_candidatos")
    nuevas = {
        "tipo_producto": "TEXT DEFAULT ''",
        "categoria_ps": "TEXT DEFAULT ''",
        "precio_publico": "REAL DEFAULT 0.0",
        "costo_validacion": "TEXT DEFAULT ''",
    }
    for col, tipo in nuevas.items():
        if col not in existentes:
            conn.execute(f"ALTER TABLE zapatos_candidatos ADD COLUMN {col} {tipo};")


def _clasificar_pendientes(conn: sqlite3.Connection) -> None:
    pendientes = conn.execute(
        """
        SELECT id_ps, COALESCE(nombre_modelo, ''), COALESCE(categoria_ps, ''), COALESCE(tipo_producto, '')
        FROM zapatos_candidatos
        WHERE COALESCE(tipo_producto, '') IN ('', ?)
        """,
        (TIPO_SIN_CLASIFICAR,),
    ).fetchall()
    cambios = [
        (nuevo, id_ps)
        for id_ps, nombre, categoria, actual in pendientes
        if (nuevo := clasificar_tipo_producto(nombre, categoria)) != actual
    ]
    if cambios:
        conn.executemany("UPDATE zapatos_candidatos SET tipo_producto = ? WHERE id_ps = ?", cambios)


def _migrar_stock_vallejo(conn: sqlite3.Connection) -> None:
    """
    Sincroniza continuamente `historial_stock_21_dias` hacia `stock_tienda` (Tienda 1: Vallejo)
    para que todo lo auditado por `paso3_recolector_ligero.py` esté siempre disponible en el panel
    y en `exportar_mercadolibre.py`.
    """
    cols_h = _columnas(conn, "historial_stock_21_dias")
    col_fecha = next((c for c in ("fecha", "fecha_hora") if c in cols_h), "CURRENT_TIMESTAMP")
    conn.execute(
        f"""
        INSERT OR REPLACE INTO stock_tienda (id_ps, tienda_id, talla, cantidad, fecha)
        SELECT TRIM(id_ps), ?, TRIM(talla), MAX(COALESCE(stock_vallejo, 0)), MAX(COALESCE({col_fecha}, CURRENT_TIMESTAMP))
        FROM historial_stock_21_dias
        WHERE id_ps IS NOT NULL AND TRIM(id_ps) != '' AND talla IS NOT NULL AND TRIM(talla) != ''
        GROUP BY TRIM(id_ps), TRIM(talla)
        """,
        (TIENDA_DEFAULT[0],),
    )
    set_config(conn, "migracion_vallejo", datetime.now().isoformat())


def get_config(conn: sqlite3.Connection, clave: str, default: str = "") -> str:
    try:
        fila = conn.execute("SELECT valor FROM configuracion WHERE clave = ?", (clave,)).fetchone()
    except sqlite3.Error:
        return default
    return str(fila[0]) if fila and fila[0] is not None else default


def set_config(conn: sqlite3.Connection, clave: str, valor: Any) -> None:
    conn.execute(
        "INSERT INTO configuracion (clave, valor) VALUES (?, ?) "
        "ON CONFLICT(clave) DO UPDATE SET valor = excluded.valor",
        (clave, str(valor)),
    )


def listar_tiendas(conn: sqlite3.Connection, incluir_ecommerce: bool = False) -> List[Tuple[int, str]]:
    filas = conn.execute("SELECT id, nombre FROM tiendas ORDER BY nombre COLLATE NOCASE").fetchall()
    return [(int(i), str(n)) for i, n in filas if incluir_ecommerce or int(i) != ID_TIENDA_ECOMMERCE]


def tienda_activa(conn: sqlite3.Connection) -> Tuple[int, str]:
    try:
        id_t = int(get_config(conn, "tienda_id", str(TIENDA_DEFAULT[0])))
    except ValueError:
        id_t = TIENDA_DEFAULT[0]
    fila = conn.execute("SELECT nombre FROM tiendas WHERE id = ?", (id_t,)).fetchone()
    return (id_t, str(fila[0])) if fila else TIENDA_DEFAULT


def fijar_tienda_activa(conn: sqlite3.Connection, tienda_id: int) -> None:
    set_config(conn, "tienda_id", int(tienda_id))
    conn.commit()


def umbral_pausa(conn: sqlite3.Connection) -> int:
    try:
        return max(0, int(float(get_config(conn, "umbral_pausa", str(UMBRAL_PAUSA_DEFAULT)))))
    except ValueError:
        return UMBRAL_PAUSA_DEFAULT


def fijar_umbral_pausa(conn: sqlite3.Connection, umbral: int) -> None:
    set_config(conn, "umbral_pausa", int(umbral))
    conn.commit()


def registrar_tiendas(conn: sqlite3.Connection, tiendas: Iterable[Dict[str, Any]]) -> None:
    for t in tiendas:
        try:
            id_t = int(t.get("id"))
        except (TypeError, ValueError):
            continue
        nombre = str(t.get("nombre") or "").strip()
        if not nombre:
            continue
        conn.execute(
            """
            INSERT INTO tiendas (id, nombre, ciudad, estado, datos_json) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                nombre = excluded.nombre,
                ciudad = CASE WHEN excluded.ciudad != '' THEN excluded.ciudad ELSE tiendas.ciudad END,
                estado = CASE WHEN excluded.estado != '' THEN excluded.estado ELSE tiendas.estado END,
                datos_json = CASE WHEN excluded.datos_json != '' THEN excluded.datos_json ELSE tiendas.datos_json END
            """,
            (id_t, nombre, str(t.get("ciudad") or ""), str(t.get("estado") or ""), str(t.get("datos_json") or "")),
        )


def datos_tienda_json(conn: sqlite3.Connection, tienda_id: int) -> str:
    fila = conn.execute("SELECT nombre, datos_json FROM tiendas WHERE id = ?", (tienda_id,)).fetchone()
    if fila and fila[1]:
        return str(fila[1])
    nombre = str(fila[0]) if fila else str(tienda_id)
    return json.dumps({"id": int(tienda_id), "name": nombre, "city": nombre, "dark_store": False})


# ---------------------------------------------------------------------------
# LECTURA DE LAS RESPUESTAS DEL SITIO
# ---------------------------------------------------------------------------
def extraer_ficha_next_data(html: str) -> Dict[str, Any]:
    prod = _producto_next_data(html)
    if not prod:
        return {}
    attrs = _atributos(prod)
    tallas = [str(_atributos(hijo).get("size_label") or "").strip() for hijo in prod.get("children") or []]
    ruta = [str(c.get("name") or "").strip() for c in prod.get("category_path") or [] if isinstance(c, dict)]
    return {
        "sku": str(prod["sku"]).strip(),
        "nombre": str(prod.get("name") or "").strip().title(),
        "marca": str(attrs.get("marca_price") or "").strip(),
        "costo_socio": _a_numero(attrs.get("price_member")),
        "precio_publico": _a_numero(attrs.get("price_customer")),
        "categoria_ps": " > ".join(r for r in ruta if r),
        "tallas": [t for t in tallas if t],
    }


def _producto_next_data(html: str) -> Dict[str, Any]:
    m = re.search(r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>', html or "", re.I | re.S)
    if not m:
        return {}
    try:
        prod = json.loads(m.group(1))["props"]["pageProps"]["productInitial"]
    except (ValueError, KeyError, TypeError):
        return {}
    return prod if isinstance(prod, dict) and prod.get("sku") else {}


def _atributos(objeto: Any) -> Dict[str, Any]:
    lista = objeto.get("custom_attributes") if isinstance(objeto, dict) else None
    return {
        str(a["attribute_code"]): a.get("value")
        for a in lista or []
        if isinstance(a, dict) and a.get("attribute_code")
    }


def _a_numero(valor: Any) -> float:
    try:
        return float(valor)
    except (TypeError, ValueError):
        return 0.0


_CLAVES_CANTIDAD = ("quantity", "qty", "stock", "available_quantity", "inventory", "existence", "existencia")
_CLAVES_TALLA = ("size_label", "size", "talla")


def _texto_talla(valor: Any) -> str:
    if isinstance(valor, float) and valor.is_integer():
        return f"{valor:.1f}"
    return str(valor).strip()


def extraer_inventario_json(data: Any, talla_contexto: str = "") -> List[Dict[str, Any]]:
    if isinstance(data, list):
        return [fila for item in data for fila in extraer_inventario_json(item, talla_contexto)]
    if not isinstance(data, dict):
        return []

    talla = next((_texto_talla(data[c]) for c in _CLAVES_TALLA if data.get(c) not in (None, "")), talla_contexto)
    if "store_id" in data:
        fila = _fila_inventario(data, talla)
        return [fila] if fila else []
    return [fila for valor in data.values() for fila in extraer_inventario_json(valor, talla)]


def _fila_inventario(data: Dict[str, Any], talla: str) -> Optional[Dict[str, Any]]:
    cantidad = next(
        (data[c] for c in _CLAVES_CANTIDAD if isinstance(data.get(c), (int, float)) and not isinstance(data[c], bool)),
        None,
    )
    if cantidad is None or not talla:
        return None
    try:
        tienda_id = int(data["store_id"])
    except (TypeError, ValueError):
        return None
    return {
        "tienda_id": tienda_id,
        "tienda_nombre": str(data.get("store_name") or "").strip(),
        "talla": talla,
        "cantidad": max(0, int(cantidad)),
    }


def extraer_tiendas_json(data: Any) -> List[Dict[str, Any]]:
    tiendas: List[Dict[str, Any]] = []
    if not isinstance(data, list):
        return tiendas
    for t in data:
        if isinstance(t, dict) and "id" in t and t.get("name") and ("lat" in t or "city" in t):
            tiendas.append({
                "id": t["id"],
                "nombre": t["name"],
                "ciudad": t.get("city") or "",
                "estado": t.get("state") or "",
                "datos_json": json.dumps(t, ensure_ascii=False),
            })
    return tiendas


# ---------------------------------------------------------------------------
# REVISIONES Y ALERTAS
# ---------------------------------------------------------------------------
def turno_actual(ahora: Optional[datetime] = None) -> str:
    return "mañana" if (ahora or datetime.now()).hour < 13 else "tarde"


def productos_seleccionados(conn: sqlite3.Connection) -> List[Tuple[str, str, str]]:
    filas = conn.execute(
        """
        SELECT z.id_ps, COALESCE(z.nombre_modelo, ''), COALESCE(z.url_real, '')
        FROM zapatos_candidatos z
        WHERE LTRIM(z.id_ps, '0') IN (SELECT LTRIM(id_ps, '0') FROM seleccion_ml)
        ORDER BY z.id_ps
        """
    ).fetchall()
    return [(str(i).strip(), str(n).strip(), str(u).strip()) for i, n, u in filas]


def esta_seleccionado(conn: sqlite3.Connection, id_ps: str) -> bool:
    fila = conn.execute(
        "SELECT 1 FROM seleccion_ml WHERE LTRIM(id_ps, '0') = LTRIM(?, '0') LIMIT 1", (str(id_ps),)
    ).fetchone()
    return fila is not None


def guardar_revision(
    conn: sqlite3.Connection,
    id_ps: str,
    tienda_id: int,
    filas_inventario: List[Dict[str, Any]],
    ficha: Optional[Dict[str, Any]] = None,
    turno: Optional[str] = None,
    ahora: Optional[datetime] = None,
) -> Dict[str, Any]:
    id_ps = str(id_ps).strip()
    ahora = ahora or datetime.now()
    fecha = ahora.isoformat(timespec="seconds")
    turno = turno or turno_actual(ahora)
    resultado: Dict[str, Any] = {"id_ps": id_ps, "estado": "sin_datos", "stock_total": None, "alerta": False}

    if ficha:
        _guardar_ficha(conn, id_ps, ficha)

    if filas_inventario:
        stock_total = _guardar_existencias(conn, id_ps, int(tienda_id), filas_inventario, fecha)
        resultado.update(estado="ok", stock_total=stock_total)
        if esta_seleccionado(conn, id_ps):
            resultado["alerta"] = _actualizar_alerta(conn, id_ps, int(tienda_id), stock_total, fecha)

    conn.execute(
        "INSERT INTO revisiones_stock (id_ps, tienda_id, turno, fecha, stock_total, estado) VALUES (?, ?, ?, ?, ?, ?)",
        (id_ps, int(tienda_id), turno, fecha, resultado["stock_total"], resultado["estado"]),
    )
    conn.commit()
    return resultado


def _guardar_ficha(conn: sqlite3.Connection, id_ps: str, ficha: Dict[str, Any]) -> None:
    nombre = str(ficha.get("nombre") or "")
    categoria = str(ficha.get("categoria_ps") or "")
    costo = _a_numero(ficha.get("costo_socio"))
    publico = _a_numero(ficha.get("precio_publico"))
    conn.execute(
        """
        UPDATE zapatos_candidatos
        SET nombre_modelo = CASE WHEN ? != '' THEN ? ELSE nombre_modelo END,
            costo_socio = CASE WHEN ? > 0 THEN ? ELSE costo_socio END,
            precio_publico = CASE WHEN ? > 0 THEN ? ELSE precio_publico END,
            categoria_ps = CASE WHEN ? != '' THEN ? ELSE categoria_ps END,
            tipo_producto = ?
        WHERE id_ps = ?
        """,
        (nombre, nombre, costo, costo, publico, publico, categoria, categoria,
         clasificar_tipo_producto(nombre, categoria), id_ps),
    )


def _guardar_existencias(
    conn: sqlite3.Connection, id_ps: str, tienda_id: int, filas: List[Dict[str, Any]], fecha: str
) -> int:
    registrar_tiendas(conn, [{"id": f["tienda_id"], "nombre": f["tienda_nombre"]} for f in filas])
    por_tienda: Dict[int, Dict[str, int]] = {tienda_id: {}}
    for f in filas:
        por_tienda.setdefault(int(f["tienda_id"]), {})[str(f["talla"])] = int(f["cantidad"])

    for id_t, tallas in por_tienda.items():
        conn.execute("DELETE FROM stock_tienda WHERE id_ps = ? AND tienda_id = ?", (id_ps, id_t))
        conn.executemany(
            "INSERT INTO stock_tienda (id_ps, tienda_id, talla, cantidad, fecha) VALUES (?, ?, ?, ?, ?)",
            [(id_ps, id_t, talla, cantidad, fecha) for talla, cantidad in tallas.items()],
        )

    stock_total = sum(por_tienda[tienda_id].values())
    if tienda_id == TIENDA_DEFAULT[0]:
        conn.execute(
            "UPDATE zapatos_candidatos SET stock_total_anterior = ?, ultima_sincronizacion = ? WHERE id_ps = ?",
            (stock_total, fecha, id_ps),
        )
    return stock_total


def _clave_id(id_ps: Any) -> str:
    texto = str(id_ps).strip()
    return texto.lstrip("0") or texto


def _actualizar_alerta(conn: sqlite3.Connection, id_ps: str, tienda_id: int, stock_total: int, fecha: str) -> bool:
    umbral = umbral_pausa(conn)
    activa = conn.execute(
        "SELECT id FROM alertas_pausa WHERE id_ps = ? AND tienda_id = ? AND activa = 1", (id_ps, tienda_id)
    ).fetchone()

    if stock_total < umbral:
        if activa:
            conn.execute(
                "UPDATE alertas_pausa SET stock_total = ?, umbral = ?, fecha_actualizada = ? WHERE id = ?",
                (stock_total, umbral, fecha, activa[0]),
            )
        else:
            conn.execute(
                "INSERT INTO alertas_pausa (id_ps, tienda_id, stock_total, umbral, fecha_creada, fecha_actualizada) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (id_ps, tienda_id, stock_total, umbral, fecha, fecha),
            )
        return True

    if activa:
        conn.execute(
            "UPDATE alertas_pausa SET activa = 0, stock_total = ?, fecha_actualizada = ? WHERE id = ?",
            (stock_total, fecha, activa[0]),
        )
    return False


def recalcular_alertas(conn: sqlite3.Connection, tienda_id: int) -> int:
    fecha = datetime.now().isoformat(timespec="seconds")
    seleccion = {_clave_id(r[0]) for r in conn.execute("SELECT id_ps FROM seleccion_ml")}

    activas = conn.execute(
        "SELECT id, id_ps FROM alertas_pausa WHERE tienda_id = ? AND activa = 1", (tienda_id,)
    ).fetchall()
    conn.executemany(
        "UPDATE alertas_pausa SET activa = 0, fecha_actualizada = ? WHERE id = ?",
        [(fecha, id_alerta) for id_alerta, id_ps in activas if _clave_id(id_ps) not in seleccion],
    )

    revisados = conn.execute(
        "SELECT id_ps, SUM(cantidad) FROM stock_tienda WHERE tienda_id = ? GROUP BY id_ps", (tienda_id,)
    ).fetchall()
    for id_ps, total in revisados:
        if _clave_id(id_ps) in seleccion:
            _actualizar_alerta(conn, str(id_ps), tienda_id, int(total or 0), fecha)

    conn.commit()
    return conn.execute(
        "SELECT COUNT(*) FROM alertas_pausa WHERE tienda_id = ? AND activa = 1", (tienda_id,)
    ).fetchone()[0]


def alertas_activas(conn: sqlite3.Connection, tienda_id: int) -> List[Dict[str, Any]]:
    filas = conn.execute(
        """
        SELECT a.id, a.id_ps, COALESCE(z.nombre_modelo, ''), COALESCE(z.tipo_producto, ''),
               a.stock_total, a.umbral, a.fecha_creada, a.fecha_actualizada, a.atendida
        FROM alertas_pausa a
        LEFT JOIN zapatos_candidatos z ON z.id_ps = a.id_ps
        WHERE a.tienda_id = ? AND a.activa = 1
        ORDER BY a.atendida, a.stock_total, a.id_ps
        """,
        (tienda_id,),
    ).fetchall()
    claves = ("id", "id_ps", "nombre", "tipo", "stock_total", "umbral", "fecha_creada", "fecha_actualizada", "atendida")
    return [dict(zip(claves, f)) for f in filas]


def marcar_alerta_atendida(conn: sqlite3.Connection, id_alerta: int, atendida: bool = True) -> None:
    conn.execute("UPDATE alertas_pausa SET atendida = ? WHERE id = ?", (1 if atendida else 0, int(id_alerta)))
    conn.commit()


# ---------------------------------------------------------------------------
# COMPARACIÓN CONTRA EL PRECIO PÚBLICO DE PRICE SHOES
# ---------------------------------------------------------------------------
COLUMNAS_COMPARACION: List[str] = [
    "Precio público PS", "Descuento socio (%)", "Sobreprecio vs PS (%)", "Ganancia directa ($)"
]


def comparar_con_precio_publico(costo: float, precio_venta: float, precio_publico: float) -> Dict[str, Optional[float]]:
    vacio: Dict[str, Optional[float]] = {"descuento_socio": None, "sobreprecio": None, "ganancia_directa": None}
    if precio_publico <= 0:
        return vacio
    resultado = dict(vacio)
    if costo > 0:
        resultado["descuento_socio"] = round((1 - costo / precio_publico) * 100, 1)
        resultado["ganancia_directa"] = round(precio_publico - costo, 2)
    if precio_venta > 0:
        resultado["sobreprecio"] = round((precio_venta / precio_publico - 1) * 100, 1)
    return resultado


def precios_publicos(conn: sqlite3.Connection) -> Dict[str, float]:
    if "precio_publico" not in _columnas(conn, "zapatos_candidatos"):
        return {}
    filas = conn.execute("SELECT id_ps, precio_publico FROM zapatos_candidatos WHERE precio_publico > 0")
    return {_clave_id(id_ps): float(precio) for id_ps, precio in filas}


def agregar_comparacion_publico(
    df: Any, conn: sqlite3.Connection, col_id: str = "ID Price Shoes", col_costo: str = "Costo ($)",
    col_venta: str = "Precio Sugerido",
) -> Any:
    publicos = precios_publicos(conn)
    df = df.copy()
    precios = [publicos.get(_clave_id(i)) for i in df[col_id]]
    comparaciones = [
        comparar_con_precio_publico(float(costo or 0), float(venta or 0), float(publico or 0))
        for costo, venta, publico in zip(df[col_costo], df[col_venta], precios)
    ]
    df["Precio público PS"] = precios
    df["Descuento socio (%)"] = [c["descuento_socio"] for c in comparaciones]
    df["Sobreprecio vs PS (%)"] = [c["sobreprecio"] for c in comparaciones]
    df["Ganancia directa ($)"] = [c["ganancia_directa"] for c in comparaciones]
    return df


def resumen_por_producto(conn: sqlite3.Connection, tienda_id: int) -> Dict[str, Dict[str, Any]]:
    resumen: Dict[str, Dict[str, Any]] = {
        _clave_id(id_ps): {"tipo": tipo or TIPO_OTROS, "stock": None, "tallas": "", "fecha": ""}
        for id_ps, tipo in conn.execute("SELECT id_ps, COALESCE(tipo_producto, '') FROM zapatos_candidatos")
    }
    filas = conn.execute(
        """
        SELECT id_ps, SUM(cantidad), MAX(COALESCE(fecha, '')),
               GROUP_CONCAT(CASE WHEN cantidad > 0 THEN talla || ' (' || cantidad || ')' END, ', ')
        FROM stock_tienda WHERE tienda_id = ? GROUP BY id_ps
        """,
        (tienda_id,),
    ).fetchall()
    for id_ps, total, fecha, tallas in filas:
        producto = resumen.setdefault(_clave_id(id_ps), {"tipo": TIPO_OTROS})
        producto.update(stock=int(total or 0), tallas=tallas or "", fecha=fecha)
    return resumen