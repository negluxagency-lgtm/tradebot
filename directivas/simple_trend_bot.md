# 📜 Directiva: Simple Trend Bot v1.0

## 🚀 Misión
Ejecutar una estrategia de "Trend Following" simplificada mediante compras sistemáticas periódicas. El bot determina la tendencia semanal (Daily Bias) y realiza apuestas de $10 cada 30 segundos a favor de dicha tendencia, cerrando posiciones únicamente mediante un Take Profit del 1%.

## 🛠️ Herramientas y SDKs
- SDK: `antigravity` (imaginario, implementado vía wrappers de Polymarket/Gamma/Clob)
- Librerías: `asyncio`, `aiohttp`, `python-dotenv`
- API: Gamma API (para bias) y CLOB API (para ejecución)

## 📋 Protocolo de Ejecución
1. **Determinación del Bias:** Al iniciar, el bot consulta el historial de los últimos 7 días. Si el precio actual > precio hace 7 días, el bias es `LONG`. De lo contrario, `SHORT`.
2. **Ciclo de Apuesta:** Cada 30 segundos, si hay saldo disponible, el bot lanza una orden de $10 a favor del bias.
3. **Gestión de Salida:** No se utiliza Stop Loss. Se monitorea el precio de entrada promedio (break-even). Cuando el precio de mercado supera el breakeven en un +1%, se liquida la posición.
4. **Persistencia:** Registrar cada apuesta y el estado del inventario en el log.

## 📊 Hallazgos del Backtest (30 días BTC)
- **PnL Net:** -$2,143.44
- **Fees Totales:** $4,139.70 (Punto crítico de fallo)
- **Problemática:** Entrar cada 30 segundos genera una erosión de capital por comisiones que supera la capacidad de recuperación del Take Profit al 1%.
- **Conclusión:** La estrategia requiere o bien un TP superior (min 5-10%) o una frecuencia de entrada mucho menor (ej. cada 4 horas) para que el fee del 1% sea despreciable.

## ⚠️ Restricciones
- No hardcodear secretos (usar `.env.local`).
- Los outputs deben ir a `artifacts/simple_trend_bot_log.json`.
- Frecuencia estricta de 30 segundos (V1).
- **CRÍTICO:** No operar en vivo con fee del 1% y frecuencia de 30s sin un TP que compense el coste transaccional acumulado.

## 📂 Estructura de Salida
- `src/simple_trend_bot.py`: Lógica del bot.
- `artifacts/simple_trend_bot_log.json`: Telemetría de ejecución.

## 🛰️ Bitácora de Anomalías
| Fecha | Error Detectado | Solución Aplicada |
|-------|----------------|-------------------|
| 2026-04-14 | Creación Inicial | N/A |
| 2026-04-14 | Backtest BTC | Resultado Negativo (-$2.1k) | La frecuencia de 30s con fee del 1% es insostenible matemáticamente. |
