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
from alert_engine import dispatch_alert, send_portfolio_summary, telegram_listener_loop, send_startup_message, dashboard_listener_loop, auto_dashboard_loop

load_dotenv(".env.local", override=True)

# ── Configuracion ──────────────────────────────────────────────────────────────
DATA_API_URL   = "https://data-api.polymarket.com"
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
async def fetch_latest_activity(session: aiohttp.ClientSession, wallet: str) -> list:
    """Consulta los ultimos trades del objetivo via REST."""
    url = f"{DATA_API_URL}/activity"
    params = {"user": wallet, "limit": 20}
    try:
        async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=8)) as resp:
            if resp.status == 200:
                return await resp.json()
            else:
                logger.warning(f"Data API respondio {resp.status} para {wallet}")
                stats["errors"] += 1
                return []
    except Exception as e:
        logger.error(f"Error en fetch_latest_activity ({wallet}): {e}")
        stats["errors"] += 1
        return []

async def process_new_trade(trade: dict, signal_queue: asyncio.Queue, wallet: str, bot_token: str, chat_id: str, profile: dict = {}):
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
        "wallet_address":  wallet,
        "copy_ratio":      profile.get("copy_ratio", float(os.getenv("SHADOW_COPY_RATIO", "0.01"))),
        "timestamp":       datetime.now(timezone.utc).isoformat(),
    }
    await signal_queue.put(signal)
    await dispatch_alert(signal, bot_token=bot_token, chat_id=chat_id)

async def shadow_poller(signal_queue: asyncio.Queue, profile: dict):
    """Loop principal: consulta la actividad del objetivo cada N segundos."""
    wallet = profile["wallet"]
    bot_token = profile["bot_token"]
    chat_id = profile["chat_id"]
    copy_ratio = profile.get("copy_ratio", float(os.getenv("SHADOW_COPY_RATIO", "0.01")))

    if not wallet:
        logger.error(f"ERROR: Wallet no definida para un perfil.")
        return

    logger.info(f"[SHADOW v2.0] Motor de Espejo activo para {wallet[:10]}...")
    logger.info(f"  -> Destino Telegram: {chat_id}")
    logger.info(f"  -> Intervalo: {POLL_INTERVAL}s")

    async with aiohttp.ClientSession() as session:
        while True:
            stats["polls"] += 1
            trades = await fetch_latest_activity(session, wallet)

            new_trades = []
            for trade in trades:
                tx = trade.get("transactionHash", "")
                if tx and tx not in seen_hashes:
                    seen_hashes.add(tx)
                    new_trades.append(trade)

            if new_trades:
                save_seen(seen_hashes)
                logger.info(f"[RADAR] {len(new_trades)} trade(s) nuevos para {wallet[:8]}!")

                # Agrupar micro-trades del mismo mercado en una sola señal
                grouped: dict[str, dict] = {}
                for trade in new_trades:
                    cid   = trade.get("conditionId", "")
                    side  = trade.get("side", "BUY").upper()
                    key   = f"{cid}_{side}"
                    if key not in grouped:
                        grouped[key] = trade.copy()
                        grouped[key]["_total_usdc"] = float(trade.get("usdcSize", 0))
                    else:
                        grouped[key]["_total_usdc"] += float(trade.get("usdcSize", 0))

                for grouped_trade in grouped.values():
                    grouped_trade["usdcSize"] = grouped_trade["_total_usdc"]
                    await process_new_trade(grouped_trade, signal_queue, wallet, bot_token, chat_id, profile)
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
    
    # Cargar perfiles desde .env.local
    profiles = []
    for i in range(1, 11): # Soporte hasta 10 perfiles
        wallet = os.getenv(f"SHADOW_TARGET_WALLET_{i}")
        if wallet:
            profiles.append({
                "wallet": wallet.lower(),
                "bot_token": os.getenv(f"TELEGRAM_BOT_TOKEN_{i}"),
                "chat_id": os.getenv(f"TELEGRAM_CHAT_ID_{i}"),
                "copy_ratio": float(os.getenv(f"SHADOW_COPY_RATIO_{i}", os.getenv("SHADOW_COPY_RATIO", "0.01"))),
                "label": f"Bot {i} ({wallet[:6]}...)"
            })
    
    # Si no hay perfiles numerados, intentar el antiguo formato
    if not profiles:
        wallet = os.getenv("SHADOW_TARGET_WALLET")
        if wallet:
            profiles.append({
                "wallet": wallet.lower(),
                "bot_token": os.getenv("TELEGRAM_BOT_TOKEN"),
                "chat_id": os.getenv("TELEGRAM_CHAT_ID"),
                "copy_ratio": float(os.getenv("SHADOW_COPY_RATIO", "0.01"))
            })

    if not profiles:
        logger.error("No se encontraron perfiles de tracking configurados.")
        return

    logger.info(f"Iniciando Shadow Tracker v2.0 con {len(profiles)} objetivos...")
    
    tasks = [run_copy_trader(signal_queue)]
    for p in profiles:
        tasks.append(shadow_poller(signal_queue, p))
        tasks.append(telegram_listener_loop(bot_token=p["bot_token"], chat_id=p["chat_id"], wallet=p["wallet"]))
        asyncio.create_task(send_startup_message(bot_token=p["bot_token"], chat_id=p["chat_id"]))

    # Dashboard centralizado: bot de control que agrupa todos los perfiles
    dash_token = os.getenv("DASHBOARD_BOT_TOKEN")
    dash_chat  = os.getenv("DASHBOARD_CHAT_ID")
    if dash_token and dash_chat and dash_token != "TU_DASHBOARD_BOT_TOKEN_AQUI":
        tasks.append(dashboard_listener_loop(profiles, bot_token=dash_token, chat_id=dash_chat))
        tasks.append(auto_dashboard_loop(profiles, bot_token=dash_token, chat_id=dash_chat))
        logger.info(f"📊 Dashboard Bot activo. Escribe al bot de control para ver el reporte de flota.")
    else:
        logger.warning("⚠️  DASHBOARD_BOT_TOKEN no configurado. Listener de control offline.")

    await asyncio.gather(*tasks)

if __name__ == "__main__":
    asyncio.run(main())
