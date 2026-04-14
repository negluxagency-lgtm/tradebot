# 🛠️ Guía: Preparación para el Despegue Real (MetaMask)

Esta guía detalla los pasos críticos para pasar de la **Simulación (Backtest)** a la **Operación Real** en Polymarket usando tu billetera MetaMask.

## 1. Infraestructura de Red (Polygon POS)
Polymarket opera sobre la red **Polygon (Mainnet)**. Debes asegurarte de lo siguiente:
- **MATIC**: Necesitas un pequeño saldo (~1-2 MATIC) para pagar el "gas" de las transacciones.
- **USDC.e**: Polymarket utiliza la variante `USDC.e` (Bridged USDC) en Polygon. Asegúrate de tener el capital que deseas operar ($1,750 o lo que prefieras) en esta moneda exacta.

## 2. Obtención de Credenciales
Para que el bot pueda firmar órdenes en tu nombre, necesita dos piezas de información:

> [!CAUTION]
> **SEGURIDAD**: Nunca compartas tu Clave Privada con nadie. El bot la usa localmente para firmar mensajes EIP-712 sin enviarla nunca por la red.

1. **Private Key (Clave Privada)**:
   - Abre MetaMask.
   - Selecciona "Detalles de la cuenta" -> "Exportar clave privada".
   - Cópjala (sin el prefijo `0x` o con él, el bot lo manejará).
   
2. **Funder Address** (Solo si usas Proxy):
   - Si tu cuenta es "External" (MetaMask directa), tu `FUNDER_ADDRESS` es tu dirección de billetera normal (`0x...`).

## 3. Configuración del Bot (`.env.local`)
Edita tu archivo `.env.local` y añade/actualiza estas líneas:

```bash
# Cambiar a false solo cuando quieras arriesgar capital real
DRY_RUN=true

# Tu clave privada de MetaMask
POLY_PRIVATE_KEY=tu_clave_privada_aqui

# Tu dirección de billetera (mismo que MetaMask)
FUNDER_ADDRESS=0xtu_direccion_aqui

# Capital por operación
TRADE_AMOUNT_USDC=1750
```

## 4. Primer Vuelo (DRY RUN)
Antes de poner `DRY_RUN=false`, **debes** ejecutar el bot con `DRY_RUN=true` durante al menos 1 ciclo completo (15 min) para verificar:
- [ ] El bot encuentra los mercados de Bitcoin.
- [ ] No hay errores de conexión con el Relayer de Polymarket.
- [ ] Tu API detecta correctamente el precio y los indicadores.

---

**Cuando estés listo, cambia `DRY_RUN=false` y reinicia el bot. ¡Buena suerte!**
