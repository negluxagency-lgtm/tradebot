# 📜 Directiva: Módulo de Detección de Smart Money - Antigravity

## 🚀 Misión
Implementar un sistema determinista para identificar y rastrear "Smart Money" (wallets con edge real) en Polymarket, permitiendo filtrar oportunidades de arbitraje basadas en la presencia de traders institucionales o con alto winrate.

## 🛠️ Herramientas y SDKs
- SDK: `antigravity`
- Librerías: `aiohttp`, `asyncio`, `json`, `logging`
- APIs: Polymarket CLOB API

## 📋 Protocolo de Ejecución
1. **Materialización del Motor:** Crear `src/wallet_tracker.py` con la arquitectura "plug & play" proporcionada.
2. **Descubrimiento:** Implementar `discover_wallets_from_trades` para extraer traders de la CLOB API.
3. **Persistencia:** Almacenar el estado de las wallets y rankings en `artifacts/wallet_rankings.json` para persistencia entre reinicios.
4. **Integración:** Actualizar los scanners (`arbitrage_bot.py` o `whale_scanner.py`) para invocar el tracker durante el loop de escaneo.
5. **Scoring:** Aplicar la fórmula de scoring multivariable (PnL, Volumen, Winrate, Actividad).

## ⚠️ Restricciones
- El scoring debe ser idempotente.
- Los rankings deben limitarse a las top 20 wallets para escalabilidad.
- No procesar trades de más de 24h para el cálculo de "Smart Money" actual (opcional, ajustable).
- **CRÍTICO:** Los outputs de rankings DEBEN guardarse en `artifacts/`.

## 📂 Estructura de Salida
- `src/wallet_tracker.py`: Lógica central del seguimiento.
- `artifacts/wallet_stats.json`: Base de datos local de performance.
- `artifacts/top_wallets.json`: Ranking actual de Smart Money.

## 🛰️ Bitácora de Anomalías
| Fecha | Error Detectado | Solución Aplicada |
|-------|----------------|-------------------|
| 2026-04-14 | Inicialización | N/A |
