┌──────────────────────────────────────────────────────────────────────────────────┐
│                     FASE 1: EXTRACT (Portal Origen en React)                     │
│  • GestorTokensReact: Obtiene cookies/JWT iniciales y mide su TTL (Tiempo vida). │
│  • Si el token caduca por tiempo o recibe HTTP 401/403, renueva credenciales     │
│    en caliente sin abortar la corrida.                                           │
└────────────────────────────────────────┬─────────────────────────────────────────┘
                                         │ Datos crudos (Stock real + Timestamp)
                                         ▼
┌──────────────────────────────────────────────────────────────────────────────────┐
│              FASE 2: TRANSFORM (Curva de Inventario + Bayesiano UCB)             │
│  • Calcula ΔStock y la pendiente de caída diaria (λ = Pares perdidos / día).     │
│  • Media Bayesiana (μ_hat): Suaviza el historial con un Prior inicial.           │
│  • Bono de Exploración UCB: Protege productos nuevos con pocas lecturas.         │
│  • Clasificador de Riesgo:                                                       │
│      ├─► [EXPLORACION_UCB] : Producto nuevo (< 4 consultas) y stock seguro.      │
│      ├─► [ACTIVO]          : Curva sana y stock por encima del límite.           │
│      ├─► [RIESGO_DECAY]    : Pendiente de caída brusca (alerta preventiva).      │
│      └─► [BAJA_AUTOMATICA] : Toca el límite de stock o se estanca por ≥ 7 días.  │
└────────────────────────────────────────┬─────────────────────────────────────────┘
                                         │ Datos limpios y dictamen de riesgo
                                         ▼
┌──────────────────────────────────────────────────────────────────────────────────┐
│                        FASE 3: LOAD (Doble Destino)                              │
│                                                                                  │
│  ├─► DESTINO A: Base de Datos SQLite (`data/zapateria_vallejo.db`)               │
│  │     1. Tabla `historial_inventario`: Guarda cada consulta (Fecha y Hora).     │
│  │     2. Tabla `zapatos_candidatos`: Actualiza estado y métricas UCB.           │
│  │     3. Tabla `bitacora_ciclo_vida_ml`: Registra momento exacto de carga/baja. │
│  │                                                                               │
│  └─► DESTINO B: Sincronizador Mercado Libre (`paso7_sincronizador_ml.py`)        │
│        1. Publica o actualiza stock de SKUs en `ACTIVO` / `EXPLORACION_UCB`.     │
│        2. Dispara el Kill-Switch (pausa automática) en `BAJA_AUTOMATICA`.        │
└──────────────────────────────────────────────────────────────────────────────────┘