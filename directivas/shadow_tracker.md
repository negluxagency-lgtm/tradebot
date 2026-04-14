# 📜 Directiva: Módulo de Replicación de Perfiles (Shadow Tracker) - Antigravity

## 🚀 Misión
Implementar un sistema de "espejo" (Mirror Trading) de alta fidelidad que rastree cada operación (Compra/Venta) de una dirección específica en Polymarket y ejecute órdenes idénticas de forma automática.

## 🛠️ Herramientas y SDKs
- SDK: `antigravity`
- Librerías: `websockets`, `aiohttp`, `asyncio`, `python-dotenv`
- Conectividad: Polymarket CLOB WebSocket (Stream de trades).

## 📋 Protocolo de Ejecución
1. **Identificación del Objetivo:** Configurar la dirección proxy del perfil objetivo en `.env.local`.
2. **Suscripción Masiva:** Suscribirse a los top 500 mercados por volumen para asegurar cobertura del 95% de la actividad del objetivo.
3. **Filtro de Identidad:** Procesar el stream de trades filtrando por `maker_address` o `taker_address` que coincida con el objetivo.
4. **Ejecución Simultánea:** Invocar el motor de ejecución (`copy_trader.py`) para replicar la orden con el capital configurado.
    - Si el objetivo compra -> Comprar.
    - Si el objetivo vende -> Vender la posición existente.
5. **Persistencia de Posiciones:** Mantener un estado en `artifacts/shadow_positions.json` para rastrear qué tokens poseemos de ese perfil.

## ⚠️ Restricciones
- El bot debe ser **idempotente**: si se reinicia, debe recuperar el estado de posiciones del perfil.
- **Latencia:** El procesamiento del WebSocket debe ser < 50ms para evitar deslizamiento (slippage) excesivo respecto al objetivo.
- **Protección de Saldo:** No intentar comprar si el balance de USDC no es suficiente.
- Todo log de ejecución debe guardarse en `artifacts/shadow_tracker.log`.

## 📂 Estructura de Salida
- `src/shadow_tracker.py`: Script principal de escucha y filtrado.
- `artifacts/shadow_positions.json`: Estado actual de la cartera "espejo".
- `artifacts/shadow_tracker.log`: Telemetría de detección y ejecución.

## 🛰️ Bitácora de Anomalías
| Fecha | Error Detectado | Solución Aplicada |
|-------|----------------|-------------------|
| 2026-04-14 | Inicialización de Directiva | N/A |
