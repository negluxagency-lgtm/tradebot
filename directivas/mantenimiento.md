# 📜 Directiva: Mantenimiento y Reseteo del Sistema

## 🚀 Misión
Proveer protocolos deterministas para la limpieza de datos, rotación de logs y reseteo de historial en entornos de desarrollo y producción.

## 📋 Protocolo de Ejecución (Reset Total)

### Fase 1: Limpieza de Telemetría (Supabase)
Para vaciar el historial del Dashboard (tabla `trades`):
```bash
python src/reset_db.py
```
> **Nota:** Esto elimina todas las posiciones registradas en la base de datos remota.

### Fase 2: Limpieza de Artefactos Locales
Para eliminar logs y registros de señales locales:
1. Vaciar `artifacts/copy_trade_pnl.json`.
2. Vaciar `artifacts/whale_signals.json`.
3. Vaciar `artifacts/whale_tracker.log`.

## 🛠️ Herramientas de Mantenimiento
- `src/reset_db.py`: Limpia la tabla `trades` en Supabase.
- `src/clean_zombies.py`: Protocolo de cierre de posiciones huérfanas.

## ⚠️ Restricciones
- **NUNCA** resetear la base de datos en producción sin respaldo previo.
- La limpieza de artefactos es IRREVERSIBLE.

## 🛰️ Bitácora de Anomalías
| Fecha      | Evento | Acción |
|------------|--------|--------|
| 2026-04-20 | Creación de protocolo de mantenimiento | N/A |
| 2026-04-20 | `NameResolutionError` al intentar `reset_db.py` | DNS de Supabase no resuelve en entorno local. El historial remoto permanece intacto, pero el local ha sido purgado. |
| 2026-04-21 | Rechazos masivos por `Size lower than minimum` y `MIN_MINUTES_TO_CLOSE` | Se parametrizó `copy_trader.py` y se ajustó `.env.local` para permitir mercados de 5min y órdenes de hasta $50 para superar el mínimo del exchange. |

