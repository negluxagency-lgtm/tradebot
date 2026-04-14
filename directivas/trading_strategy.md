# 📜 Directiva: Estrategia Crypto 24/7 (BTC-USD)

## 🚀 Misión
Operar el par Bitcoin/USD (BTC-USD) en Polymarket de forma ininterrumpida (24/7), extrayendo valor estadístico mediante una estrategia asimétrica de reversión a la media acoplada a la tendencia macro.

## 📐 Reglas de la Estrategia (Inmutables)

### 1. Ventana de Operación
- **Modo de Operación:** 24 horas al día, 7 días a la semana (Mercado Crypto continuo).
- **Frecuencia de análisis:** Cada 5 minutos (Hiper-Ignición), al cierre de la vela.

### 2. Detección de Tendencia Diaria (Macro & Momentum)
- Obtener la **última vela diaria CERRADA** de BTC-USD (`yfinance`) y la **EMA20**.
- **Tendencia ALCISTA:** 1 vela cerrada al alza + Precio actual `> EMA20`.
- **Tendencia BAJISTA:** 1 vela cerrada a la baja + Precio actual `< EMA20`.
- Si el color de la última vela choca con la posición del precio respecto a la EMA20, la tendencia se declara *NEUTRAL*.

### 3. Señal de Entrada (Gatillo de 5m & RSI)
- Obtener histórico de **5 minutos** y calcular **RSI(14)**.
- **Señal Válida:**
  - **COMPRAR (BUY):** Tendencia Alcista + **1 vela de 5m bajista** + `RSI < 45` (retroceso confirmado).
  - **VENDER (SELL):** Tendencia Bajista + **1 vela de 5m alcista** + `RSI > 55` (rebote confirmado).
- Solo se permite **1 operación activa simultáneamente**.

### 4. Gestión de Riesgo (Escudo de Oro)
- **Tamaño de Posición:** Dinámico, invertirá exactamente `TRADE_AMOUNT_USDC` comprando la cantidad de acciones necesarias.
- **Take Profit (TP):** **+3.00%** sobre el precio del subyacente de entrada.
- **Stop Loss (SL):** **-2.15%** sobre el precio de entrada. (Configuración óptima post-estrés 720d).

## ⚠️ Restricciones
- Si el RSI no se alinea con la zona extrema de rebote (<40 o >60), el patrón de 2 velas contrarias **se ignora**.
- El bot no cierra operaciones a una hora específica, corre hasta tocar TP o SL o recibir un KeyboardInterrupt.

## 📂 Estructura de Módulos
- `src/data_engine.py`: Motor de datos, cálculo de EMA20 y RSI continuos.
- `src/strategy_engine.py`: Validación direccional, filtros macro y generador de TP/SL.
- `src/trading_bot.py`: Conector de la API Relayer Polymarket, buscador de contratos BTC activos, ejecutor de órdenes.
- `src/backtest_btc.py`: Simulador histórico 24/7 aislado para probar mejoras de código.

## 🛰️ Bitácora de Evolución
| Fecha      | Actualización Clave | Motivo |
|------------|----------------|--------|
| 2026-03-29 | **Estrategia v1** a **v4** | Desarrollo inicial sobre EUR/USD. Backtesting reveló PnL negativo por R:R invertido y falsas señales. Implementados filtros EMA20 y RSI en pruebas. |
| 2026-03-29 | **Migración estructural (v5)** | Los test revelaron un win rate pobre de Forex en cortos plazos vs un excelente comportamiento criptográfico. **Arquitectura migrada a BTC-USD.** |
| 2026-03-29 | **Modo Continuo 24/7** | Removidos los bloqueos de horario europeo (09h-14h). Backtest mostró +7.7% de ganancia en 60 días con Sharpe >3 en posiciones cortas y >1.3 en largas operando 24 horas. |
| 2026-03-29 | **Riesgo Dinámico USDC** | Abandonado el `size=10` fijo en acciones en favor de `TRADE_AMOUNT_USDC` para mantener exposición asimétrica real independientemente del precio del contrato. |
| 2026-03-29 | **Fase de Ignición (v6)** | Implementado el **Escudo de Oro** (SL 2.15/TP 3.0). Reducidas confirmaciones a 1 vela diaria y 1 vela 15m. Volumen aumentado a ~20 trades/mes con PnL de +$467 proyectado. |
