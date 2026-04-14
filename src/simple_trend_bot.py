"""
🛰️ Simple Trend Bot v1.0 — Momentum Quant
Estrategia: 
1. Daily Bias: Precio actual vs Precio hace 7 días.
2. Acción: Si Bias es alcista, compra $10 cada 30s.
3. Salida: Take Profit al +1% del precio promedio de entrada. Sin Stop Loss.
"""

import asyncio
import aiohttp
import os
import json
import logging
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
logger = logging.getLogger("simple_trend_bot")

load_dotenv(".env.local")

# ── Configuración ──────────────────────────────────────────────────────────────
GAMMA_API_URL = "https://gamma-api.polymarket.com"
CLOB_API_URL  = "https://clob.polymarket.com"
POLL_INTERVAL = 30  # 30 segundos

# Credenciales
API_KEY     = os.getenv("RELAYER_API_KEY", "")
API_ADDR    = os.getenv("RELAYER_API_KEY_ADDRESS", "")
PRIVATE_KEY = os.getenv("POLY_PRIVATE_KEY", "")
DRY_RUN     = os.getenv("DRY_RUN", "true").lower() == "true"

# Mercado: Will bitcoin hit $1m before GTA VI?
DEFAULT_TOKEN_ID_YES = "105267568073659068217311993901927962476298440625043565106676088842803600775810"
DEFAULT_TOKEN_ID_NO  = "91863162118308663069733924043159186005106558783397508844234610341221325526200"

BET_AMOUNT_USDC = 10.0
TAKE_PROFIT_PCT = 0.01  # 1%

# ── Estado del Bot ─────────────────────────────────────────────────────────────
class BotState:
    def __init__(self):
        self.inventory_shares = 0.0
        self.avg_entry_price = 0.0
        self.total_spent = 0.0
        self.bias = "NONE"  # LONG, SHORT, NONE
        self.target_token = ""

state = BotState()

# ── Funciones Core ─────────────────────────────────────────────────────────────

async def get_historical_bias(session: aiohttp.ClientSession, token_id: str):
    """Calcula el bias comparando el precio actual con el de hace 7 días."""
    # En la API de Polymarket, 'market' es el token_id para este endpoint
    url = f"{CLOB_API_URL}/prices-history?market={token_id}&interval=1h"
    try:
        async with session.get(url) as resp:
            if resp.status != 200:
                logger.error(f"Error fetching history: {resp.status} | {await resp.text()}")
                return "NONE"
            data = await resp.json()
            history = data.get("history", [])
            if not history:
                logger.warning("No hay historial disponible.")
                return "NONE"
            
            price_now = float(history[-1]["p"])
            # Buscamos el punto más cercano a hace 168h o el más antiguo si no hay tanto
            price_old = float(history[0]["p"]) 
            
            if price_now > price_old:
                logger.info(f"📈 Bias Detectado: LONG (Now: {price_now} > History Start: {price_old})")
                return "LONG"
            elif price_now < price_old:
                logger.info(f"📉 Bias Detectado: SHORT (Now: {price_now} < History Start: {price_old})")
                return "SHORT"
            else:
                return "NONE"
    except Exception as e:
        logger.error(f"Error en GetBias: {e}")
        return "NONE"

async def get_current_price(session: aiohttp.ClientSession, token_id: str) -> float:
    """Obtiene el último precio ejecutado (o mid price)."""
    url = f"{CLOB_API_URL}/last-trade-price?token_id={token_id}"
    try:
        async with session.get(url) as resp:
            if resp.status == 200:
                data = await resp.json()
                return float(data.get("price", 0.0))
    except Exception:
        pass
    return 0.0

async def execute_trade(session: aiohttp.ClientSession, token_id: str, side: str, amount_usdc: float):
    """Ejecuta una orden (Simulada o Real)."""
    mode = "🟡 DRY RUN" if DRY_RUN else "🟢 LIVE"
    price = await get_current_price(session, token_id)
    
    if price <= 0:
        logger.error("No se pudo obtener el precio para ejecutar el trade.")
        return None

    shares = amount_usdc / price
    
    logger.info(f"{mode} | {side} {shares:.4f} shares @ {price:.4f} (${amount_usdc})")
    
    if DRY_RUN:
        return {"status": "ok", "price": price, "shares": shares}
    
    # Aquí iría la lógica real de firmado y envío de orden al CLOB
    # Para este ejemplo 'sencillo', mostramos solo la intención.
    return {"status": "ok", "price": price, "shares": shares}

def persist_log(event_type: str, data: dict):
    os.makedirs("artifacts", exist_ok=True)
    log_file = "artifacts/simple_trend_bot_log.json"
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event": event_type,
        **data
    }
    existing = []
    if os.path.exists(log_file):
        try:
            with open(log_file, "r") as f:
                existing = json.load(f)
        except: pass
    existing.append(entry)
    with open(log_file, "w") as f:
        json.dump(existing, f, indent=2)

async def main():
    logger.info("🚀 Iniciando Simple Trend Bot...")
    
    async with aiohttp.ClientSession() as session:
        # 1. Determinar Bias
        state.bias = await get_historical_bias(session, DEFAULT_TOKEN_ID_YES)
        
        if state.bias == "LONG":
            state.target_token = DEFAULT_TOKEN_ID_YES
        elif state.bias == "SHORT":
            state.target_token = DEFAULT_TOKEN_ID_NO
        else:
            logger.error("No se pudo determinar una tendencia clara. Abortando.")
            return

        while True:
            # 2. Obtener precio actual para check de TP
            current_price = await get_current_price(session, state.target_token)
            
            # 3. Check Take Profit
            if state.inventory_shares > 0 and current_price >= state.avg_entry_price * (1 + TAKE_PROFIT_PCT):
                logger.warning(f"🎯 TAKE PROFIT ALCANZADO: {current_price:.4f} >= {state.avg_entry_price * (1.01):.4f}")
                await execute_trade(session, state.target_token, "SELL", state.inventory_shares * current_price)
                persist_log("EXIT_TP", {
                    "price": current_price, 
                    "avg_entry": state.avg_entry_price,
                    "profit": (current_price - state.avg_entry_price) * state.inventory_shares
                })
                # Reset estado
                state.inventory_shares = 0
                state.avg_entry_price = 0
                state.total_spent = 0
            
            # 4. Comprar $10
            res = await execute_trade(session, state.target_token, "BUY", BET_AMOUNT_USDC)
            if res:
                new_shares = res["shares"]
                new_price = res["price"]
                
                # Actualizar promedio
                total_shares = state.inventory_shares + new_shares
                state.avg_entry_price = ((state.inventory_shares * state.avg_entry_price) + (new_shares * new_price)) / total_shares
                state.inventory_shares = total_shares
                state.total_spent += BET_AMOUNT_USDC
                
                persist_log("BUY", {
                    "price": new_price, 
                    "shares": new_shares, 
                    "total_inventory": state.inventory_shares,
                    "avg_entry": state.avg_entry_price
                })

            logger.info(f"STATUS: Inv={state.inventory_shares:.2f} | Avg={state.avg_entry_price:.4f} | Spent={state.total_spent:.1f}")
            await asyncio.sleep(POLL_INTERVAL)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot detenido por el usuario.")
