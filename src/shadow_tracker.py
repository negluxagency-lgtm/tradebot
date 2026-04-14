"""
Shadow Tracker v2.0 — Mirror Trading Engine (REST Polling)
Arquitectura: Polling a la Data API de Polymarket cada 5s.
El canal WS no expone la identidad del trader, por eso usamos REST.
"""
import os
import json
import asyncio
import logging
import aiohttp
from datetime import datetime, timezone
from dotenv import load_dotenv

from copy_trader import run_copy_trader
from alert_engine import dispatch_alert, send_portfolio_summary, telegram_listener_loop

load_dotenv(".env.local")

# ── Configuracion ──────────────────────────────────────────────────────────────
DATA_API_URL   = "https://data-api.polymarket.com"
TARGET_WALLET  = os.getenv("SHADOW_TARGET_WALLET", "").lower()
POLL_INTERVAL  = int(os.getenv("SHADOW_POLL_INTERVAL", "5"))  # segundos

os.makedirs("artifacts", exist_ok=True)
logger = logging.getLogger("shadow_tracker")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s -- %(message)s",
    handlers=[
        logging.FileHandler("artifacts/shadow_tracker.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)

# ── Estado: IDs de trades ya procesados (evitar duplicados) ───────────────────
SEEN_PATH = "artifacts/shadow_seen.json"

def load_seen() -> set:
    """Carga los transactionHash ya procesados desde disco (idempotente)."""
    if os.path.exists(SEEN_PATH):
        try:
            with open(SEEN_PATH, "r", encoding="utf-8") as f:
                return set(json.load(f))
        except Exception:
            pass
    return set()

def save_seen(seen: set):
    """Persiste el set de hashes vistos (ventana de 500 para no crecer infinito)."""
    lst = list(seen)[-500:]
    with open(SEEN_PATH, "w", encoding="utf-8") as f:
        json.dump(lst, f)

seen_hashes: set = load_seen()

# ── Metricas ──────────────────────────────────────────────────────────────────
stats = {"polls": 0, "detected": 0, "errors": 0, "total_interceptadas": 0}

# ── Motor de Polling ──────────────────────────────────────────────────────────
async def fetch_latest_activity(session: aiohttp.ClientSession) -> list:
    """Consulta los ultimos trades del objetivo via REST."""
    url = f"{DATA_API_URL}/activity"
    params = {"user": TARGET_WALLET, "limit": 20}
    try:
        async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=8)) as resp:
            if resp.status == 200:
                return await resp.json()
            else:
                logger.warning(f"Data API respondio {resp.status}")
                stats["errors"] += 1
                return []
    except Exception as e:
        logger.error(f"Error en fetch_latest_activity: {e}")
        stats["errors"] += 1
        return []

async def process_new_trade(trade: dict, signal_queue: asyncio.Queue):
    """Procesa un trade nuevo del objetivo."""
    tx_hash     = trade.get("transactionHash", "")
    market_id   = trade.get("conditionId", "")
    market_name = trade.get("title", "Mercado desconocido")
    outcome     = trade.get("outcome", "Unknown")
    side        = trade.get("side", "BUY").upper()
    size        = float(trade.get("size", 0))
    price       = float(trade.get("price", 0))
    usdc_size   = float(trade.get("usdcSize", size * price))
    asset_id    = str(trade.get("asset", ""))
    ts          = trade.get("timestamp", 0)
    
    trade_time  = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%H:%M:%S")

    logger.info(
        f"[!] MOVIMIENTO DETECTADO! {side} {size:.2f} shares ({outcome}) "
        f"@ {price:.3f} | ${usdc_size:.2f} USDC | {market_name} [{trade_time}]"
    )
    stats["detected"] += 1
    stats["total_interceptadas"] += 1

    # Resumen de portfolio cada 100 apuestas interceptadas
    if stats["total_interceptadas"] % 100 == 0:
        logger.info(f"[HITO] {stats['total_interceptadas']} apuestas interceptadas. Enviando resumen a Telegram...")
        await send_portfolio_summary()

    signal = {
        "market_id":       market_id,
        "market_name":     market_name,
        "asset_id":        asset_id,
        "outcome":         outcome,
        "side":            side,
        "price":           price,
        "trade_size_usdc": usdc_size,
        "signal_type":     "shadow_mirror",
        "tier":            "TIER_1",
        "wallet_address":  TARGET_WALLET,
        "timestamp":       datetime.now(timezone.utc).isoformat(),
    }
    await signal_queue.put(signal)
    await dispatch_alert(signal)

async def shadow_poller(signal_queue: asyncio.Queue):
    """Loop principal: consulta la actividad del objetivo cada N segundos."""
    if not TARGET_WALLET:
        logger.error("ERROR: SHADOW_TARGET_WALLET no definida en .env.local")
        return

    logger.info(f"[SHADOW v2.0] Motor de Espejo activo.")
    logger.info(f"  -> Objetivo : {TARGET_WALLET}")
    logger.info(f"  -> Intervalo: {POLL_INTERVAL}s")
    logger.info(f"  -> Trades vistos en cache: {len(seen_hashes)}")

    async with aiohttp.ClientSession() as session:
        while True:
            stats["polls"] += 1
            trades = await fetch_latest_activity(session)

            new_trades = []
            for trade in trades:
                tx = trade.get("transactionHash", "")
                if tx and tx not in seen_hashes:
                    seen_hashes.add(tx)
                    new_trades.append(trade)

            if new_trades:
                save_seen(seen_hashes)
                logger.info(f"[RADAR] {len(new_trades)} trade(s) nuevos detectados del objetivo!")

                # Agrupar micro-trades del mismo mercado en una sola señal
                grouped: dict[str, dict] = {}
                for trade in new_trades:
                    cid   = trade.get("conditionId", "")
                    side  = trade.get("side", "BUY").upper()
                    key   = f"{cid}_{side}"
                    if key not in grouped:
                        grouped[key] = trade.copy()
                        grouped[key]["_total_usdc"] = float(trade.get("usdcSize", 0))
                        grouped[key]["_trade_count"] = 1
                    else:
                        grouped[key]["_total_usdc"] += float(trade.get("usdcSize", 0))
                        grouped[key]["_trade_count"] += 1

                for grouped_trade in grouped.values():
                    grouped_trade["usdcSize"] = grouped_trade["_total_usdc"]
                    await process_new_trade(grouped_trade, signal_queue)
            else:
                if stats["polls"] % 12 == 0:  # Heartbeat cada ~60s
                    logger.info(
                        f"[HEARTBEAT] Polls: {stats['polls']} | "
                        f"Detectados: {stats['detected']} | "
                        f"Errores: {stats['errors']} | "
                        f"Cache: {len(seen_hashes)} hashes"
                    )

            await asyncio.sleep(POLL_INTERVAL)

async def main():
    signal_queue = asyncio.Queue()
    logger.info("Iniciando Shadow Tracker v2.0 (REST Polling)...")
    await asyncio.gather(
        shadow_poller(signal_queue),
        run_copy_trader(signal_queue),
        telegram_listener_loop(),
    )

if __name__ == "__main__":
    asyncio.run(main())
