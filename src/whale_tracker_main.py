"""
🛰️ Whale Insider Tracker — Punto de Entrada Principal
Orquesta el scanner WebSocket y el copy trader via cola asyncio.
Arranca siempre en DRY_RUN a menos que .env.local indique lo contrario.

Uso:
    cd c:\\Users\\Usuario\\Trading
    python src/whale_tracker_main.py
"""
import os
import sys
import asyncio
import logging
from dotenv import load_dotenv

# Asegurar que src/ esté en el path
sys.path.insert(0, os.path.dirname(__file__))

load_dotenv(".env.local")

from whale_scanner  import run_scanner
from copy_trader    import run_copy_trader
from alert_engine   import send_startup_message

DRY_RUN = os.getenv("DRY_RUN", "true").lower() == "true"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s — %(message)s",
    handlers=[
        logging.FileHandler("artifacts/whale_tracker.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger("main")


async def main():
    os.makedirs("artifacts", exist_ok=True)

    mode = "DRY RUN 🟡" if DRY_RUN else "LIVE 🟢"
    logger.info("=" * 60)
    logger.info(f"  🐋 WHALE INSIDER TRACKER — {mode}")
    logger.info(f"  Z≥{os.getenv('Z_THRESHOLD', '2.0')}σ | "
                f"Min ${os.getenv('WHALE_MIN_USDC', '10000')} | "
                f"Copy ${os.getenv('COPY_TRADE_USDC', '100')}")
    logger.info("=" * 60)

    # Test de conectividad Telegram
    ok = await send_startup_message()
    if ok:
        logger.info("✅ Telegram conectado. Alertas activas.")
    else:
        logger.warning("⚠️ Telegram no disponible. Continuando sin alertas.")

    # Cola de señales entre scanner y copy trader
    signal_queue: asyncio.Queue = asyncio.Queue(maxsize=100)

    # Ejecutar ambos módulos concurrentemente
    await asyncio.gather(
        run_scanner(signal_queue),
        run_copy_trader(signal_queue),
    )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("🛑 Sistema detenido por el operador. Aterrizaje seguro.")
