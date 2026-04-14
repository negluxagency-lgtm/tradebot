# 📜 Directiva: Insider & Whale Tracker Bot - Polymarket

## 🚀 Misión
Monitorear en tiempo real los mercados de Polymarket con alto potencial de actividad "insider" (mercados políticos, geopolíticos, regulatorios). Detectar apuestas estadísticamente anómalas (> N desviaciones estándar sobre el volumen promedio) y ejecutar automáticamente una posición espejo ("copy trade") en el mismo outcome.

## 🎯 Categorías de Mercado con Potencial Insider
- **Política / Elecciones:** Resultados políticos donde el timing importa.
- **Regulatorio / Legal:** Decisiones de cortes, aprobaciones FDA, SEC.
- **Geopolítico:** Acuerdos de paz, sanciones, cambios de liderazgo.
- **Cripto:** Decisiones de ETFs, hacks, regulación.
- **Deportes (excluir):** Bajo ratio de insider, alto de azar.

## 🛠️ Herramientas y SDKs
- **API Principal:** WebSocket CLOB de Polymarket (`wss://ws-subscriptions-clob.polymarket.com/ws/`)
- **API de Mercados:** Gamma API REST (`https://gamma-api.polymarket.com`)
- **Librerías Python:** `websockets`, `aiohttp`, `requests`, `python-dotenv`, `statistics`, `json`, `asyncio`
- **Telemetría:** Supabase (tabla: `whale_alerts`)
- **Alertas:** Telegram Bot API

## 📊 Motor de Detección de Anomalías (Reglas Inmutables)

### Criterio 1: Filtro Anti-Ruido Estricto
- Descartar automáticamente y no registrar en memoria ningún trade `< 50 USDC`. Esto preserva la pureza estadística y evita la contaminación de las ventanas temporales.

### Criterio 2: Impact Score Global (V5.0) y Filtro Anti-Certeza
- Se elimina por completo el Z-Score por ser algebraicamente incompatible entre mercados masivos y vacíos. 
- La nueva métrica es el **Impact Score**: `(Volumen_Ráfaga / Market_Cap_24h) * 100,000`. Esto crea un estándar global equiparable para algoritmos de Machine Learning y elimina falsos positivos en mercados muertos.
- **Filtro Anti-Certeza**: El bot identifica "Late Execution" en áreas donde no hay incertidumbre real. Todas las órdenes lanzadas en zonas de certeza (Precio `> 0.90` USDC) se etiquetan como `late_execution` y quedan silenciadas para Telegram.

### Criterio 3: Topología Cualitativa por Execution Intent (Clustering V5.0)
El bot ya no agrupa simplemente todo por volumen grande, sino por la intención estructural del jugador midiendo `Trades`, `Volumen`, e `Impact Score`:
- 🎯 **Convicción Institucional (`institutional_block`):** Sniper directo. Volumen violento (≥ $15k) condensado en 3 transacciones o menos, barren la liquidez unidireccionalmente. **[A Telegram]**
- 🤖 **Bot Accumulador (`twap_accumulation`):** Algoritmo intentando minimizar slippage. Volumen sustancial (≥ $10k) dividido entre 4 a 15 transacciones, unidireccional. **[A Telegram]**
- 🌊 **Histeria Minorista (`retail_frenzy`):** Compras de +15 trades súper divididos por la masa entrando en FOMO. **[Solo log DB]**
- 🔁 **Falsa Liquidez (`wash_trading`):** Volumen grande (> $10k) pero ejecutado en ambas direcciones mitigándose mutuamente (bias central). **[Solo log DB]**

### Criterio 3: Frecuencia Algorítmica (Split Orders)
- Analiza retroactivamente una ventana de **10 segundos**.
- Agrupa los trades por tamaños idénticos (redondeados a decenas). Si un mismo bloque sustancial (>$100) se repite **4 o más veces**, se clasifica como ejecución algorítmica coordinada.

### Criterio 4: Clustering y Fresh Wallets (Legacy)
- Si >= 3 wallets distintas apuestan en el mismo outcome dentro de **10 minutos**, dispara alerta.
- Wallets con < 10 tx históricas que operan volúmenes relevantes.

## ⚙️ Configuración Aprobada del Motor (Inmutable hasta revisión)
```env
WHALE_MIN_USDC=10000
COPY_TRADE_USDC=100
MAX_CONCURRENT_POSITIONS=8
MIN_MARKET_VOLUME_USDC=5000000
DRY_RUN=true
```
> **Rationale:** La "Signal Intelligence" abandona el umbral puro de dólares y el reporte 1 a 1 de trades (trade-based alerting). Evaluamos a través de agrupaciones temporales que deben superar un Z-Score agresivo (>= 3.0) y similitud rigurosa para merecer la atención, extinguiendo el 'alert spam'.

## 📋 Protocolo de Ejecución

### Fase 1: Escaneo (Scanner)
1. Obtener lista de mercados activos vía Gamma API, filtrando por categorías prioritarias.
2. Suscribirse via WebSocket al canal `market` de todos los mercados activos.
3. Mantener ventana histórica (basada en tiempo) de operaciones por mercado.

### Fase 2: Detección Topológica (Intents)
4. Filtrar trades menores a 50 USDC.
5. Agrupar trades de los últimos 3 segundos. Calcular su **Impact Score**.
6. Aplicar árbol de decisión de _Execution Intent_ evaluando precios (`Avg Price`), Trades Totales e Impacto.
7. Descartar `late_execution` (Average Price > 0.90) para el feed en vivo, aunque sí enviarlo a la BD.
8. Persistir absolutamente todo JSON/Supabase, enviando solo a Telegram las ejecuciones Block Institutional (`institutional_block` y `twap_accumulation`) o algorítmicas puras.

### Fase 3: Copy Trade (Ejecución)
7. Validar que el mercado tiene liquidez suficiente (`min_liquidity_usdc`).
8. Calcular tamaño de posición: `COPY_TRADE_USDC` (fijo, definido en `.env.local`).
9. Ejecutar orden de compra en el **mismo outcome** via `py-clob-client`.
10. Registrar la operación en Supabase y enviar alerta a Telegram.

### Fase 4: Gestión de Posición
11. Monitorear resolución del mercado.
12. Al resolver: registrar PnL real en `artifacts/copy_trade_pnl.json`.

## ⚠️ Restricciones y Salvaguardas
- **NUNCA** copiar trades en mercados con volumen total < `MIN_MARKET_VOLUME_USDC` (evitar mercados manipulables).
- **NUNCA** copiar si el precio del outcome ya supera **0.85 USDC** (riesgo asimétrico negativo, poca recompensa).
- **NUNCA** copiar si el mercado cierra en < **30 minutos** (slippage y liquidez críticos).
- **MÁXIMO** `MAX_CONCURRENT_POSITIONS = 5` posiciones abiertas simultáneamente.
- Los secretos SOLO viven en `.env.local` via `os.getenv`.
- **SIEMPRE** obtener metadatos del mercado (`market_meta`) antes de cualquier operación de logging o procesamiento que dependa del nombre del mercado o IDs.
- Modo `DRY_RUN=true` disponible: registra señales sin ejecutar órdenes reales.

## 📂 Estructura de Salida
- `src/whale_scanner.py`: Motor WebSocket + procesamiento algorítmico temporal.
- `src/copy_trader.py`: Lógica de copy trade + gestión de posiciones.
- `src/alert_engine.py`: Notificaciones Telegram + persistencia Supabase.
- `artifacts/whale_signals.json`: Log de todas las señales detectadas.
- `artifacts/copy_trade_pnl.json`: Registro de operaciones y PnL.
- `artifacts/whale_tracker.log`: Log de ejecución del sistema.

## 🛰️ Bitácora de Anomalías
| Fecha      | Error Detectado | Solución Aplicada |
|------------|----------------|-------------------|
| 2026-04-12 | Inicio Misión  | N/A               |
| 2026-04-12 | `UnicodeEncodeError: 'charmap' codec can't encode character` en terminales Windows con cp1252 al usar emojis en `print()` | Añadir `import io; sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")` al inicio de cualquier script con salida por consola. Ejecutar siempre con `python -X utf8 src/script.py` |
| 2026-04-12 | Supabase DNS no resuelve en entorno de test local (`getaddrinfo failed`) | No crítico — señales persisten localmente en `artifacts/whale_signals.json`. Verificar conectividad de red antes del lanzamiento en producción. |
| 2026-04-13 | **CRÍTICO:** `0 mercados objetivo identificados` — Los campos `tags` y `category` de la Gamma API devuelven `null` en todos los mercados. El filtro original bloqueaba el 100% de mercados. | **Solución:** Reemplazar filtro por `tags`/`category` con filtro por **keywords en el campo `question`** (lista `TARGET_KEYWORDS`). Adicionalmente, los token IDs están en `clobTokenIds` (no en `tokens`), y los nombres de outcomes en `outcomes` (string JSON o lista). Fix aplicado en `whale_scanner.py`. |
| 2026-04-13 | **CRÍTICO:** WebSocket rechaza con `HTTP 404` — El endpoint `wss://.../ws/` es incorrecto. | **Solución:** El endpoint correcto es `wss://ws-subscriptions-clob.polymarket.com/ws/market` (sufijo `/market` obligatorio). Adicionalmente el campo `type` del mensaje de suscripción debe ser `"market"` en minúsculas, no `"Market"`. |
| 2026-04-13 | `local variable 'market_name' referenced before assignment` en `process_trade_event`. | **Solución:** Mover la recuperación de metadatos (`market_meta.get`) al inicio de la función, antes del bloque de descarte por tamaño de trade que usaba `market_name` en su log de debug. |
| 2026-04-13 | **CUELLO BOTELLA:** El Z-Score escalaba a medidas surrealistas (>10σ) y se mezclaban bots de liquidez (Wash Trading) con compradores informados reales. Telemetría incompleta. | **Solución:** Reforestación mediante *Qualitative Signal Intelligence*. Agregado de Varianza Mínima Cap (1000σ), métrica *Bias* para determinar `informed_conviction`, filtrado drástico de alertas. |
| 2026-04-13 | **ANOMALÍA LÓGICA GRAVE:** El Z-Score capado seguía siendo inestable e incomparable entre mercados ($12M = 3σ vs $45K = 44σ). Falta de identificación de bots de absorción frente a retail. Spams en zonas > 0.90 de precio de mercado. | **Solución Definitiva (v5.0):** Erradicación del Z-Score y establecimiento de un `Impact Score` global dictado por el volumen de 24h. Inyección de modelo multi-tipo "Execution Intent" (Block vs TWAP vs Wash vs Retail). Creación de **Filtro Anti-Certeza** para enmudecer trades tardíos `> 0.90 USDC`. |
| 2026-04-13 | **FALLO DE PRIORIZACIÓN:** Todas las señales que pasaban los filtros tenían el mismo peso. El precio se usaba como corte binario (>0.90 bloqueado), sin gradación continua. Los umbrales USD absolutos ($10k, $15k) eran incompatibles con mercados pequeños. | **Solución (v6.0 — Signal Quality Score):** Se implementó `_calculate_sqs()` como producto de 4 factores continuos: `price_factor` (curva cúbica con máximo en 0.50), `structure_weight` (por intent), `concentration_factor` (bias × wallets) e `impact_factor` (log-normalizado). Sistema de 3 Tiers (TIER_1≥0.50→Telegram, TIER_2≥0.20→DB, TIER_3→JSON local). Cooldown solo para TIER_1/TIER_2. Bonus de contexto ×1.2 si el mercado estaba silencioso. |

