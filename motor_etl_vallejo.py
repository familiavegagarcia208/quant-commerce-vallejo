"""
========================================================================================
ZAPATERÍA VALLEJO - ARQUITECTURA ETL DE PRODUCCIÓN (REACT SPA + BAYESIANO UCB)
========================================================================================
Capas de la Arquitectura:
1. RepositorioSQLiteETL        : Introspección dinámica de esquema, Time-Series y WAL.
2. ExtractorReactSPA           : Interceptación de APIs XHR/Fetch en React, ciclo de
                                 vida de Tokens y extracción real de inventario/tallas.
3. MotorMatematicoBayesianoUCB : Cálculo de curva de decaimiento (λ), Media Bayesiana
                                 y margen de exploración UCB sin inflación por Δt corto.
4. OrquestadorPipelineETL      : Coordinación de flujo y carga dual (SQLite + Meli JSON).
========================================================================================
"""

from __future__ import annotations

import json
import math
import random
import re
import sqlite3
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

try:
    from playwright.sync_api import Browser, BrowserContext, Page, Request, Response, sync_playwright
    PLAYWRIGHT_DISPONIBLE = True
except ImportError:
    PLAYWRIGHT_DISPONIBLE = False

try:
    from curl_cffi import requests as tls_requests
    CURL_CFFI_DISPONIBLE = True
except ImportError:
    CURL_CFFI_DISPONIBLE = False


# ==============================================================================
# CONFIGURACIÓN DE ARQUITECTURA E HIPERPARÁMETROS MATEMÁTICOS
# ==============================================================================
RUTA_BD = Path("data/zapateria_vallejo.db")
RUTA_COLA_ML = Path("data/cola_sincronizacion_ml.json")

# Parámetros Operativos
TAMANO_LOTE_ETL = 20                # SKUs por ejecución
TIEMPO_LIMITE_CORRIDA_SEG = 15 * 60 # 15 minutos máximo por lote
TTL_MAXIMO_TOKEN_SEG = 1200         # Renovación preventiva de contexto/token cada 20 min
PAUSA_MIN_SEG = 8.0                 # Pausa mínima entre productos con sesión viva
PAUSA_MAX_SEG = 16.0                # Pausa máxima entre productos

# Parámetros de la Curva de Decaimiento y Modelo Bayesiano UCB
UMBRAL_STOCK_CRITICO = 3            # <= 3 unidades/tallas dispara BAJA_AUTOMATICA
VENTANA_EXPLORACION_MIN_LECTURAS = 4 # Lecturas de gracia UCB para productos nuevos
DIAS_MAX_ESTANCAMIENTO = 7.0        # Días sin cambio de stock para baja por freno
TASA_CAIDA_ALERTA_DIARIA = 4.0      # Caída >= 4 unidades/día activa RIESGO_DECAY

PRIOR_PESO_ALPHA = 2.0              # Peso de la creencia inicial Bayesiana
PRIOR_ROTACION_MU0 = 0.75           # Velocidad inicial esperada (0.75 unidades/día)
CONSTANTE_UCB_C = 1.20              # Constante de exploración UCB
SCORE_MINIMO_ACTIVO = 0.40          # Score mínimo para sostener un producto maduro


# ==============================================================================
# CONTRATOS DE DATOS (DATA TRANSFER OBJECTS)
# ==============================================================================
@dataclass(frozen=True)
class ProductoCandidato:
    id_ps: str
    nombre_modelo: str
    url_real: str
    precio_ml: float
    stock_previo_registrado: int


@dataclass(frozen=True)
class LecturaExtraida:
    id_ps: str
    stock_confirmado: int
    url_resuelta: str
    fuente_dato: str  # 'API_JSON_INTERCEPTADA', 'NEXT_DATA_SSR', 'LD_JSON', 'DOM_TALLAS'
    token_activo: bool


@dataclass(frozen=True)
class DictamenTransformacion:
    id_ps: str
    stock_actual: int
    delta_stock: int
    tasa_decaimiento_lambda: float
    dias_estancado: float
    media_bayesiana: float
    bono_ucb: float
    score_ucb: float
    num_lecturas: int
    estatus_etl: str
    accion_ml: str
    motivo_tecnico: str
    fuente_extraccion: str


# ==============================================================================
# CAPA 1: REPOSITORIO SQLITE CON INTROSPECCIÓN DINÁMICA Y SERIE DE TIEMPO
# ==============================================================================
class RepositorioSQLiteETL:
    """Gestiona la persistencia en SQLite en modo WAL y asegura compatibilidad de esquema."""

    def __init__(self, ruta_bd: Path) -> None:
        self.ruta_bd = ruta_bd
        self.ruta_bd.parent.mkdir(parents=True, exist_ok=True)
        self._asegurar_esquema_y_migraciones()

    def _conectar(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.ruta_bd, timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        return conn

    def _obtener_columnas_tabla(self, conn: sqlite3.Connection, tabla: str) -> Set[str]:
        filas = conn.execute(f"PRAGMA table_info({tabla});").fetchall()
        return {str(f["name"]) for f in filas}

    def _asegurar_esquema_y_migraciones(self) -> None:
        with self._conectar() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS zapatos_candidatos (
                    id_ps TEXT PRIMARY KEY,
                    nombre_modelo TEXT,
                    marca TEXT,
                    color TEXT,
                    costo_bodega REAL DEFAULT 0.0,
                    precio_sugerido_ml REAL DEFAULT 0.0,
                    origen_catalogo TEXT,
                    url_real TEXT,
                    stock_total_anterior INTEGER DEFAULT 0,
                    ultima_actualizacion TIMESTAMP
                );
                """
            )

            columnas_actuales = self._obtener_columnas_tabla(conn, "zapatos_candidatos")
            nuevas_columnas = [
                ("url_real", "TEXT"),
                ("stock_total_anterior", "INTEGER DEFAULT 0"),
                ("ultima_actualizacion", "TIMESTAMP"),
                ("estatus_etl", "TEXT DEFAULT 'PENDIENTE'"),
                ("tasa_decaimiento", "REAL DEFAULT 0.0"),
                ("media_bayesiana", "REAL DEFAULT 0.0"),
                ("score_ucb", "REAL DEFAULT 0.0"),
                ("num_consultas_hist", "INTEGER DEFAULT 0"),
                ("fuente_ultimo_stock", "TEXT"),
                ("id_publicacion_ml", "TEXT"),
                ("estatus_ml", "TEXT DEFAULT 'NO_PUBLICADO'"),
                ("fecha_carga_ml", "TIMESTAMP"),
                ("fecha_baja_ml", "TIMESTAMP"),
            ]
            for col, tipo in nuevas_columnas:
                if col not in columnas_actuales:
                    conn.execute(f"ALTER TABLE zapatos_candidatos ADD COLUMN {col} {tipo};")

            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS historial_inventario (
                    id_consulta INTEGER PRIMARY KEY AUTOINCREMENT,
                    id_ps TEXT NOT NULL,
                    stock_detectado INTEGER NOT NULL,
                    delta_stock INTEGER DEFAULT 0,
                    tasa_decaimiento_lambda REAL DEFAULT 0.0,
                    dias_estancado REAL DEFAULT 0.0,
                    media_bayesiana REAL DEFAULT 0.0,
                    score_ucb REAL DEFAULT 0.0,
                    estatus_calculado TEXT NOT NULL,
                    motivo_decision TEXT,
                    fuente_extraccion TEXT,
                    fecha_hora_consulta TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_hist_sku_fecha ON historial_inventario(id_ps, fecha_hora_consulta);"
            )

            # Si una tabla existente no tiene la columna fuente_extraccion en historial_inventario, la agrega
            cols_hist = self._obtener_columnas_tabla(conn, "historial_inventario")
            if "fuente_extraccion" not in cols_hist:
                conn.execute("ALTER TABLE historial_inventario ADD COLUMN fuente_extraccion TEXT;")
            if "dias_estancado" not in cols_hist:
                conn.execute("ALTER TABLE historial_inventario ADD COLUMN dias_estancado REAL DEFAULT 0.0;")
            if "motivo_decision" not in cols_hist:
                conn.execute("ALTER TABLE historial_inventario ADD COLUMN motivo_decision TEXT;")

            conn.commit()

    def obtener_lote_candidatos(self, limite: int) -> List[ProductoCandidato]:
        with self._conectar() as conn:
            cols = self._obtener_columnas_tabla(conn, "zapatos_candidatos")
            col_nombre = "nombre_modelo" if "nombre_modelo" in cols else ("modelo" if "modelo" in cols else "'Zapato'")
            col_precio = "precio_sugerido_ml" if "precio_sugerido_ml" in cols else "0.0"

            filas = conn.execute(
                f"""
                SELECT id_ps,
                       COALESCE({col_nombre}, 'SKU ' || id_ps) AS nombre,
                       COALESCE(url_real, '') AS url,
                       COALESCE({col_precio}, 0.0) AS precio_ml,
                       COALESCE(stock_total_anterior, 0) AS stock_prev
                FROM zapatos_candidatos
                WHERE id_ps IS NOT NULL AND TRIM(id_ps) != ''
                ORDER BY COALESCE(ultima_actualizacion, '2000-01-01') ASC
                LIMIT ?
                """,
                (limite,),
            ).fetchall()

            return [
                ProductoCandidato(
                    id_ps=str(f["id_ps"]).strip(),
                    nombre_modelo=str(f["nombre"]).strip(),
                    url_real=str(f["url"]).strip(),
                    precio_ml=float(f["precio_ml"] or 0.0),
                    stock_previo_registrado=int(f["stock_prev"] or 0),
                )
                for f in filas
            ]

    def obtener_historial_sku(self, id_ps: str) -> Tuple[int, List[sqlite3.Row]]:
        with self._conectar() as conn:
            total_global = int(
                conn.execute("SELECT COUNT(*) AS n FROM historial_inventario").fetchone()["n"] or 1
            )
            historial = conn.execute(
                """
                SELECT stock_detectado,
                       tasa_decaimiento_lambda,
                       MAX(0.0, (julianday('now') - julianday(fecha_hora_consulta))) AS dias_transcurridos
                FROM historial_inventario
                WHERE id_ps = ?
                ORDER BY fecha_hora_consulta DESC
                LIMIT 25
                """,
                (id_ps,),
            ).fetchall()
            return total_global, historial

    def guardar_resultado_etl(self, dictamen: DictamenTransformacion, url_resuelta: str) -> None:
        with self._conectar() as conn:
            conn.execute(
                """
                INSERT INTO historial_inventario (
                    id_ps, stock_detectado, delta_stock, tasa_decaimiento_lambda,
                    dias_estancado, media_bayesiana, score_ucb, estatus_calculado,
                    motivo_decision, fuente_extraccion, fecha_hora_consulta
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                """,
                (
                    dictamen.id_ps,
                    dictamen.stock_actual,
                    dictamen.delta_stock,
                    dictamen.tasa_decaimiento_lambda,
                    dictamen.dias_estancado,
                    dictamen.media_bayesiana,
                    dictamen.score_ucb,
                    dictamen.estatus_etl,
                    dictamen.motivo_tecnico,
                    dictamen.fuente_extraccion,
                ),
            )

            conn.execute(
                """
                UPDATE zapatos_candidatos
                SET stock_total_anterior = ?,
                    url_real = CASE WHEN ? != '' THEN ? ELSE url_real END,
                    ultima_actualizacion = CURRENT_TIMESTAMP,
                    estatus_etl = ?,
                    tasa_decaimiento = ?,
                    media_bayesiana = ?,
                    score_ucb = ?,
                    num_consultas_hist = ?,
                    fuente_ultimo_stock = ?,
                    fecha_carga_ml = CASE
                        WHEN ? IN ('ACTIVO', 'EXPLORACION_UCB') THEN COALESCE(fecha_carga_ml, CURRENT_TIMESTAMP)
                        ELSE fecha_carga_ml
                    END,
                    fecha_baja_ml = CASE
                        WHEN ? = 'BAJA_AUTOMATICA' THEN COALESCE(fecha_baja_ml, CURRENT_TIMESTAMP)
                        ELSE NULL
                    END
                WHERE id_ps = ?
                """,
                (
                    dictamen.stock_actual,
                    url_resuelta,
                    url_resuelta,
                    dictamen.estatus_etl,
                    dictamen.tasa_decaimiento_lambda,
                    dictamen.media_bayesiana,
                    dictamen.score_ucb,
                    dictamen.num_lecturas,
                    dictamen.fuente_extraccion,
                    dictamen.estatus_etl,
                    dictamen.estatus_etl,
                    dictamen.id_ps,
                ),
            )
            conn.commit()

    def consolidar_wal(self) -> None:
        with self._conectar() as conn:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
            conn.commit()


# ==============================================================================
# CAPA 2: EXTRACTOR REACT SPA (INTERCEPTOR DE APIs XHR/FETCH + GESTOR DE TOKENS)
# ==============================================================================
class AnalizadorPayloadReact:
    """Extrae existencias reales inspeccionando árboles JSON de APIs React y el DOM hidratado."""

    CLAVES_CANTIDAD = {"stock", "availablequantity", "available_quantity", "inventory", "existencia", "quantity", "qty"}
    CLAVES_COLECCION_VARIANTES = {"variants", "sizes", "tallas", "skus", "items", "children", "options"}

    @classmethod
    def buscar_stock_en_json(cls, data: Any, id_ps: str) -> Optional[int]:
        """Recorre recursivamente un JSON de API buscando el nodo del SKU y sumando su stock/variantes."""
        nodos_coincidentes: List[Dict[str, Any]] = []
        cls._recolectar_nodos_sku(data, id_ps, nodos_coincidentes)

        # Si el JSON completo ya pertenece al producto (ej. respuesta de detalle de producto)
        if not nodos_coincidentes and isinstance(data, dict):
            nodos_coincidentes = [data]

        for nodo in nodos_coincidentes:
            stock_calculado = cls._extraer_unidades_de_nodo(nodo)
            if stock_calculado is not None:
                return stock_calculado
        return None

    @classmethod
    def _recolectar_nodos_sku(cls, obj: Any, id_ps: str, acumulador: List[Dict[str, Any]]) -> None:
        if isinstance(obj, dict):
            valores_texto = [str(v) for k, v in obj.items() if not isinstance(v, (dict, list))]
            if any(id_ps in val for val in valores_texto):
                acumulador.append(obj)
            for v in obj.values():
                cls._recolectar_nodos_sku(v, id_ps, acumulador)
        elif isinstance(obj, list):
            for item in obj:
                cls._recolectar_nodos_sku(item, id_ps, acumulador)

    @classmethod
    def _extraer_unidades_de_nodo(cls, nodo: Dict[str, Any]) -> Optional[int]:
        # 1. Revisar si hay bandera explícita de agotado
        for k, v in nodo.items():
            kl = k.lower()
            if kl in ("instock", "in_stock", "available", "disponible") and isinstance(v, bool) and not v:
                return 0
            if kl in ("availability", "status") and isinstance(v, str) and ("outofstock" in v.lower() or "agotado" in v.lower()):
                return 0

        # 2. Revisar si tiene lista de tallas/variantes y sumar sus unidades o tallas activas
        for k, v in nodo.items():
            if k.lower() in cls.CLAVES_COLECCION_VARIANTES and isinstance(v, list) and len(v) > 0:
                total_variantes = 0
                encontro_metrica = False
                for item in v:
                    if isinstance(item, dict):
                        sub_stock = cls._extraer_unidades_directas(item)
                        if sub_stock is not None:
                            total_variantes += sub_stock
                            encontro_metrica = True
                        elif item.get("available") is True or item.get("inStock") is True:
                            total_variantes += 1
                            encontro_metrica = True
                if encontro_metrica:
                    return total_variantes

        # 3. Revisar cantidad directa en el nodo
        directo = cls._extraer_unidades_directas(nodo)
        if directo is not None:
            return directo

        return None

    @classmethod
    def _extraer_unidades_directas(cls, d: Dict[str, Any]) -> Optional[int]:
        for k, v in d.items():
            if k.lower() in cls.CLAVES_CANTIDAD and isinstance(v, (int, float)) and not isinstance(v, bool):
                return max(0, int(v))
        return None


class ExtractorReactSPA:
    """
    Motor de extracción que ejecuta el ciclo de vida de React, intercepta los tokens
    generados por el frontend y captura las respuestas JSON de la API interna.
    """

    def __init__(self) -> None:
        self._playwright = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None
        self._page: Optional[Page] = None

        self.token_capturado: Optional[str] = None
        self.inicio_vida_token: float = 0.0
        self.num_renovaciones_token: int = 0
        self.token_requiere_refresh: bool = False
        self._respuestas_json_sku: List[Any] = []

    def iniciar(self) -> None:
        if PLAYWRIGHT_DISPONIBLE:
            self._playwright = sync_playwright().start()
            self._browser = self._playwright.chromium.launch(headless=True)
            self._renovar_contexto_y_token()

    def cerrar(self) -> None:
        for recurso in (self._page, self._context, self._browser):
            if recurso is not None:
                try:
                    recurso.close()
                except Exception:
                    pass
        if self._playwright is not None:
            try:
                self._playwright.stop()
            except Exception:
                pass

    def _token_caducado(self) -> bool:
        if self.token_requiere_refresh or self.inicio_vida_token == 0.0:
            return True
        return (time.time() - self.inicio_vida_token) >= TTL_MAXIMO_TOKEN_SEG

    def _interceptar_request(self, request: Request) -> None:
        headers = request.headers
        for clave in ("authorization", "x-access-token", "x-api-key", "x-auth-token"):
            if clave in headers and headers[clave]:
                self.token_capturado = headers[clave]

    def _interceptar_response(self, response: Response) -> None:
        if response.status in (401, 403) and ("api" in response.url or "graphql" in response.url):
            self.token_requiere_refresh = True
            return

        content_type = response.headers.get("content-type", "").lower()
        if "application/json" in content_type and response.status == 200:
            try:
                data = response.json()
                self._respuestas_json_sku.append(data)
            except Exception:
                pass

    def _renovar_contexto_y_token(self) -> None:
        """Reinicia la sesión de navegador cuando el token de React caduca y negocia uno nuevo."""
        self.num_renovaciones_token += 1
        print(f"    🔑 [React Token Manager] Inicializando sesión limpia y negociando token (#{self.num_renovaciones_token})...")

        if self._context is not None:
            try:
                self._context.close()
            except Exception:
                pass

        self._context = self._browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            viewport={"width": 1366, "height": 768},
            locale="es-MX",
        )
        self._page = self._context.new_page()
        self._page.on("request", self._interceptar_request)
        self._page.on("response", self._interceptar_response)

        try:
            self._page.goto("https://www.priceshoes.com/", wait_until="domcontentloaded", timeout=30000)
            self._page.wait_for_timeout(2500)
        except Exception as exc:
            print(f"    ⚠️ [React Token Manager] Aviso en carga inicial del portal: {exc}")

        self.inicio_vida_token = time.time()
        self.token_requiere_refresh = False
        print("    ✅ [React Token Manager] Sesión React hidratada y lista.")

    def extraer_sku(self, candidato: ProductoCandidato) -> Optional[LecturaExtraida]:
        pausa = random.uniform(PAUSA_MIN_SEG, PAUSA_MAX_SEG)
        print(f"    ⏳ [Extract] Consultando SKU {candidato.id_ps} (pausa {pausa:.1f}s)...")
        time.sleep(pausa)

        if PLAYWRIGHT_DISPONIBLE and self._browser is not None:
            return self._extraer_con_playwright_react(candidato)
        return self._extraer_fallback_ssr(candidato)

    def _extraer_con_playwright_react(self, candidato: ProductoCandidato) -> Optional[LecturaExtraida]:
        if self._token_caducado():
            self._renovar_contexto_y_token()

        self._respuestas_json_sku.clear()
        url_destino = (
            candidato.url_real
            if (candidato.url_real and candidato.url_real.startswith("http"))
            else f"https://www.priceshoes.com/buscar?q={candidato.id_ps}"
        )

        try:
            assert self._page is not None
            self._page.goto(url_destino, wait_until="domcontentloaded", timeout=30000)
            try:
                self._page.wait_for_load_state("networkidle", timeout=7000)
            except Exception:
                self._page.wait_for_timeout(2500)

            # Si el token expiró durante la navegación, renovamos y repetimos una vez
            if self.token_requiere_refresh:
                print("    🔄 [React Token Manager] Token expirado detectado en tráfico XHR. Renovando...")
                self._renovar_contexto_y_token()
                self._respuestas_json_sku.clear()
                self._page.goto(url_destino, wait_until="domcontentloaded", timeout=30000)
                self._page.wait_for_timeout(3000)

            # Si caímos en página de búsqueda, intentar abrir la tarjeta del producto para ver sus tallas
            if "/buscar" in self._page.url:
                enlace_prod = self._page.locator(f"a[href*='{candidato.id_ps}'], a[href*='/productos/'], a[href*='/catalogo/']").first
                if enlace_prod.count() > 0:
                    try:
                        enlace_prod.click(timeout=4000)
                        self._page.wait_for_timeout(2500)
                    except Exception:
                        pass

            url_final = self._page.url

            # ESTRATEGIA 1: Inspeccionar los paquetes JSON interceptados de la API de React
            for payload in reversed(self._respuestas_json_sku):
                stock_api = AnalizadorPayloadReact.buscar_stock_en_json(payload, candidato.id_ps)
                if stock_api is not None:
                    return LecturaExtraida(
                        id_ps=candidato.id_ps,
                        stock_confirmado=stock_api,
                        url_resuelta=url_final,
                        fuente_dato="API_JSON_INTERCEPTADA",
                        token_activo=True,
                    )

            # ESTRATEGIA 2: Inspeccionar estado hidratado (__NEXT_DATA__ / application/ld+json)
            html_renderizado = self._page.content()
            resultado_ssr = self._parsear_estructuras_hidratadas(html_renderizado, candidato.id_ps, url_final)
            if resultado_ssr is not None:
                return resultado_ssr

            # ESTRATEGIA 3: Evaluar el DOM renderizado de React (Tallas activas vs Producto agotado)
            stock_dom = self._page.evaluate(
                """(sku) => {
                    const texto = (document.body.innerText || '').toLowerCase();
                    if (texto.includes('no encontramos resultados') || texto.includes('0 resultados') || texto.includes('producto agotado')) {
                        return 0;
                    }
                    // Contar botones de tallas habilitados en el selector de corrida de calzado
                    const selectoresTallas = document.querySelectorAll(
                        '[data-testid*="size"]:not([disabled]), [class*="size"]:not([disabled]):not([class*="disabled"]), button[class*="talla"]:not([disabled])'
                    );
                    let tallasActivas = 0;
                    selectoresTallas.forEach(el => {
                        const t = (el.innerText || '').trim();
                        if (/^(1[0-9]|2[0-9]|3[0-3])(\\.5)?$/.test(t)) {
                            tallasActivas += 1;
                        }
                    });
                    if (tallasActivas > 0) return tallasActivas * 2; // Estimación conservadora por talla activa
                    if (texto.includes('agregar al carrito') || texto.includes('comprar ahora') || texto.includes('disponible')) {
                        return 6;
                    }
                    return null;
                }""",
                candidato.id_ps,
            )

            if isinstance(stock_dom, int):
                return LecturaExtraida(
                    id_ps=candidato.id_ps,
                    stock_confirmado=stock_dom,
                    url_resuelta=url_final,
                    fuente_dato="DOM_REACT_HIDRATADO",
                    token_activo=True,
                )

        except Exception as exc:
            print(f"    ❌ [Extract Error] Fallo al inspeccionar SKU {candidato.id_ps}: {exc}")

        return None

    def _parsear_estructuras_hidratadas(self, html: str, id_ps: str, url_actual: str) -> Optional[LecturaExtraida]:
        # 1. Buscar __NEXT_DATA__
        match_next = re.search(
            r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>',
            html,
            re.DOTALL | re.IGNORECASE,
        )
        if match_next:
            try:
                data_next = json.loads(match_next.group(1))
                stock_n = AnalizadorPayloadReact.buscar_stock_en_json(data_next, id_ps)
                if stock_n is not None:
                    return LecturaExtraida(id_ps, stock_n, url_actual, "NEXT_DATA_SSR", True)
            except Exception:
                pass

        # 2. Buscar JSON-LD (Schema.org Product)
        bloques_ld = re.findall(
            r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
            html,
            re.DOTALL | re.IGNORECASE,
        )
        for bloque in bloques_ld:
            try:
                data_ld = json.loads(bloque.strip())
                stock_ld = AnalizadorPayloadReact.buscar_stock_en_json(data_ld, id_ps)
                if stock_ld is not None:
                    return LecturaExtraida(id_ps, stock_ld, url_actual, "LD_JSON_SCHEMA", True)
                texto_ld = bloque.lower()
                if "outofstock" in texto_ld:
                    return LecturaExtraida(id_ps, 0, url_actual, "LD_JSON_SCHEMA", True)
                if "instock" in texto_ld:
                    return LecturaExtraida(id_ps, 8, url_actual, "LD_JSON_SCHEMA", True)
            except Exception:
                pass

        return None

    def _extraer_fallback_ssr(self, candidato: ProductoCandidato) -> Optional[LecturaExtraida]:
        """Fallback si Playwright no está instalado en el entorno."""
        if not CURL_CFFI_DISPONIBLE:
            return None
        url = (
            candidato.url_real
            if (candidato.url_real and candidato.url_real.startswith("http"))
            else f"https://www.priceshoes.com/buscar?q={candidato.id_ps}"
        )
        try:
            resp = tls_requests.get(url, impersonate="chrome120", timeout=25)
            if resp.status_code == 200:
                return self._parsear_estructuras_hidratadas(resp.text, candidato.id_ps, url)
        except Exception:
            pass
        return None


# ==============================================================================
# CAPA 3: MOTOR MATEMÁTICO (CURVA DE DECAIMIENTO + BAYESIANO + UCB)
# ==============================================================================
class MotorMatematicoBayesianoUCB:
    """
    Evalúa matemáticamente la serie de tiempo del inventario.
    Garantiza que corridas seguidas el mismo día no distorsionen la pendiente diaria (λ).
    """

    @staticmethod
    def evaluar_curva_sku(
        id_ps: str,
        stock_actual: int,
        fuente_extraccion: str,
        total_global_lecturas: int,
        historial_sku: List[sqlite3.Row],
    ) -> DictamenTransformacion:
        lecturas_previas = len(historial_sku)
        delta_stock = 0
        lambda_diaria = 0.0
        dias_estancado = 0.0

        if lecturas_previas > 0:
            ultima = historial_sku[0]
            stock_previo = int(ultima["stock_detectado"])
            delta_stock = stock_actual - stock_previo

            # Piso de 0.5 días para evitar inflación artificial de λ en pruebas consecutivas
            ventana_dias = max(float(ultima["dias_transcurridos"] or 0.5), 0.5)
            if delta_stock < 0:
                lambda_diaria = round(abs(delta_stock) / ventana_dias, 3)

            # Calcular cuántos días acumulados lleva el SKU sin mover su stock
            for fila in historial_sku:
                if int(fila["stock_detectado"]) == stock_actual:
                    dias_estancado = round(float(fila["dias_transcurridos"] or 0.0), 2)
                else:
                    break

        n_i = lecturas_previas + 1

        # 1. Media Bayesiana Empírica (combina Prior inicial con las tasas observadas)
        suma_lambdas = lambda_diaria + sum(float(r["tasa_decaimiento_lambda"] or 0.0) for r in historial_sku)
        media_bayesiana = round(
            (PRIOR_PESO_ALPHA * PRIOR_ROTACION_MU0 + suma_lambdas) / (PRIOR_PESO_ALPHA + n_i),
            3,
        )

        # 2. Bono de Incertidumbre UCB (alto cuando n_i es pequeño; decrece conforme hay historial)
        bono_ucb = round(
            CONSTANTE_UCB_C * math.sqrt(math.log(max(total_global_lecturas, 1) + 1) / n_i),
            3,
        )
        score_ucb = round(media_bayesiana + bono_ucb, 3)

        # 3. Reglas Deterministas de Clasificación de Riesgo
        if stock_actual <= UMBRAL_STOCK_CRITICO:
            estatus = "BAJA_AUTOMATICA"
            accion_ml = "PAUSAR_PUBLICACION"
            motivo = f"Límite crítico alcanzado ({stock_actual} <= {UMBRAL_STOCK_CRITICO} unidades)"
        elif n_i <= VENTANA_EXPLORACION_MIN_LECTURAS:
            estatus = "EXPLORACION_UCB"
            accion_ml = "PUBLICAR_O_ACTUALIZAR"
            motivo = f"En margen de exploración UCB ({n_i}/{VENTANA_EXPLORACION_MIN_LECTURAS} lecturas)"
        elif dias_estancado >= DIAS_MAX_ESTANCAMIENTO and media_bayesiana < SCORE_MINIMO_ACTIVO:
            estatus = "BAJA_AUTOMATICA"
            accion_ml = "PAUSAR_PUBLICACION"
            motivo = f"Inventario estancado por {dias_estancado} días (μ_bayes={media_bayesiana})"
        elif lambda_diaria >= TASA_CAIDA_ALERTA_DIARIA and stock_actual <= (UMBRAL_STOCK_CRITICO * 2):
            estatus = "RIESGO_DECAY"
            accion_ml = "REDUCIR_STOCK_PREVENTIVO"
            motivo = f"Curva de caída pronunciada (λ={lambda_diaria}/día) cerca del límite"
        else:
            estatus = "ACTIVO"
            accion_ml = "PUBLICAR_O_ACTUALIZAR"
            motivo = "Curva de inventario saludable"

        return DictamenTransformacion(
            id_ps=id_ps,
            stock_actual=stock_actual,
            delta_stock=delta_stock,
            tasa_decaimiento_lambda=lambda_diaria,
            dias_estancado=dias_estancado,
            media_bayesiana=media_bayesiana,
            bono_ucb=bono_ucb,
            score_ucb=score_ucb,
            num_lecturas=n_i,
            estatus_etl=estatus,
            accion_ml=accion_ml,
            motivo_tecnico=motivo,
            fuente_extraccion=fuente_extraccion,
        )


# ==============================================================================
# CAPA 4: ORQUESTADOR DEL PIPELINE ETL
# ==============================================================================
class OrquestadorPipelineETL:
    def __init__(self) -> None:
        self.repo = RepositorioSQLiteETL(RUTA_BD)
        self.extractor = ExtractorReactSPA()
        self.motor_matematico = MotorMatematicoBayesianoUCB()

    def ejecutar(self) -> None:
        print("=" * 92)
        print(f"🚀 ARQUITECTURA ETL ZAPATERÍA VALLEJO [{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}]")
        print("=" * 92)

        if not PLAYWRIGHT_DISPONIBLE:
            print("⚠️ [Aviso Arquitectura] Playwright no está instalado en el venv.")
            print("   Ejecuta: pip install playwright && playwright install chromium")

        candidatos = self.repo.obtener_lote_candidatos(TAMANO_LOTE_ETL)
        if not candidatos:
            print("⚠️ No se encontraron SKUs en `zapatos_candidatos`.")
            return

        self.extractor.iniciar()
        inicio_corrida = time.time()
        cola_sincronizacion_ml: List[Dict[str, Any]] = []
        exitosos, omitidos, bajas = 0, 0, 0

        try:
            for idx, candidato in enumerate(candidatos, 1):
                if (time.time() - inicio_corrida) >= TIEMPO_LIMITE_CORRIDA_SEG:
                    print("\n⏱️ Tiempo límite de lote alcanzado (15 min). Cerrando ciclo limpiamente...")
                    break

                print(f"\n[{idx}/{len(candidatos)}] SKU {candidato.id_ps} - {candidato.nombre_modelo[:40]}")

                # 1. EXTRACT
                lectura = self.extractor.extraer_sku(candidato)
                if lectura is None:
                    omitidos += 1
                    print("    ⚠️ [Extract] El portal no devolvió estructura de inventario verificable; se omite sin alterar serie.")
                    continue

                # 2. TRANSFORM
                total_global, historial_sku = self.repo.obtener_historial_sku(candidato.id_ps)
                dictamen = self.motor_matematico.evaluar_curva_sku(
                    id_ps=candidato.id_ps,
                    stock_actual=lectura.stock_confirmado,
                    fuente_extraccion=lectura.fuente_dato,
                    total_global_lecturas=total_global,
                    historial_sku=historial_sku,
                )

                # 3. LOAD (Destino 1: SQLite)
                self.repo.guardar_resultado_etl(dictamen, lectura.url_resuelta)

                # 4. LOAD (Destino 2: Cola Mercado Libre)
                registro_ml = asdict(dictamen)
                registro_ml["nombre_modelo"] = candidato.nombre_modelo
                registro_ml["precio_ml"] = candidato.precio_ml
                registro_ml["timestamp_utc"] = datetime.now(timezone.utc).isoformat()
                cola_sincronizacion_ml.append(registro_ml)

                exitosos += 1
                if dictamen.estatus_etl == "BAJA_AUTOMATICA":
                    bajas += 1

                print(
                    f"    📊 [ETL OK | {lectura.fuente_dato}] Stock: {dictamen.stock_actual} (Δ:{dictamen.delta_stock}) | "
                    f"λ: {dictamen.tasa_decaimiento_lambda}/día | UCB: {dictamen.score_ucb} -> "
                    f"{dictamen.estatus_etl} ({dictamen.motivo_tecnico})"
                )

            RUTA_COLA_ML.parent.mkdir(parents=True, exist_ok=True)
            RUTA_COLA_ML.write_text(
                json.dumps(cola_sincronizacion_ml, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            self.repo.consolidar_wal()

            print("\n" + "=" * 92)
            print(
                f"✅ RESUMEN ETL | Auditados con éxito: {exitosos} | "
                f"Bajas Automáticas: {bajas} | Omitidos: {omitidos}"
            )
            print("=" * 92)
        finally:
            self.extractor.cerrar()


if __name__ == "__main__":
    OrquestadorPipelineETL().ejecutar()