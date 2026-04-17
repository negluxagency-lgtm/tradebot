"""
🛰️ Copy Trader — Whale Insider Tracker
Responsabilidad: Recibir señales del scanner, aplicar salvaguardas
y ejecutar copy trades en Polymarket CLOB (o simularlos en DRY_RUN).
"""
import os
import json
import asyncio
import logging
import aiohttp
from datetime import datetime, timezone
from dotenv import load_dotenv

from py_clob_client.client import ClobClient
from py_clob_client.clob_types import OrderArgs
from py_clob_client.order_builder.constants import BUY, SELL

load_dotenv(".env.local")

# ── Configuración ──────────────────────────────────────────────────────────────
CLOB_API_URL          = os.getenv("POLY_HOST", "https://clob.polymarket.com")
RELAYER_API_KEY       = os.getenv("RELAYER_API_KEY")
RELAYER_API_KEY_ADDR  = os.getenv("RELAYER_API_KEY_ADDRESS")
PRIVATE_KEY           = os.getenv("POLYMARKET_PRIVATE_KEY")
PROXY_ADDRESS         = os.getenv("POLYMARKET_PROXY_ADDRESS")

COPY_TRADE_USDC       = float(os.getenv("COPY_TRADE_USDC", "100"))
MAX_CONCURRENT        = int(os.getenv("MAX_CONCURRENT_POSITIONS", "8"))
DRY_RUN               = os.getenv("DRY_RUN", "true").lower() == "true"
SHADOW_COPY_RATIO     = float(os.getenv("SHADOW_COPY_RATIO", "0.10"))  # 10% del trade objetivo
SHADOW_MIN_USDC       = float(os.getenv("SHADOW_MIN_USDC", "1.0"))     # Minimo a ejecutar

# Salvaguardas duras

MAX_PRICE             = 0.85   # no copiar si el precio ya superó 0.85 USDC
MIN_MINUTES_TO_CLOSE  = 30     # no copiar si el mercado cierra en < 30 min

AGG_THRESHOLD_USDC = float(os.getenv("AGG_THRESHOLD_USDC", "100"))
AGG_TIMEOUT_SEC = int(os.getenv("AGG_TIMEOUT_SEC", "180"))
AGG_MAX_EXECS_PER_MIN = int(os.getenv("AGG_MAX_EXECS_PER_MIN", "5"))
AGG_MAX_TRADE_USDC = float(os.getenv("AGG_MAX_TRADE_USDC", "5.0"))
AGG_MAX_PORTFOLIO_EXPOSURE_PCT = float(os.getenv("AGG_MAX_PORTFOLIO_EXPOSURE_PCT", "50.0"))
INITIAL_CAPITAL = float(os.getenv("INITIAL_CAPITAL", "269"))

market_buffers: dict = {}
execs_timestamps: list = []


logger = logging.getLogger("copy_trader")

# ── Clob Client ────────────────────────────────────────────────────────────────
try:
    clob_client = ClobClient(
        host=CLOB_API_URL,
        key=PRIVATE_KEY,
        chain_id=int(os.getenv("POLY_CHAIN_ID", "137")),
        signature_type=int(os.getenv("POLY_SIGNATURE_TYPE", "0")),
        funder=PROXY_ADDRESS,
    )
    clob_creds = clob_client.create_or_derive_api_creds()
    clob_client.set_api_creds(clob_creds)
    logger.info("✅ ClobClient Inicializado con Éxito")
except Exception as e:
    logger.error(f"❌ Error inicializando ClobClient: {e}")
    clob_client = None



# ── Estado de posiciones ───────────────────────────────────────────────────────
open_positions: dict[str, dict] = {}  # trade_id → posición abierta
pnl_log_path = "artifacts/copy_trade_pnl.json"


def load_open_positions():
    """Carga posiciones abiertas desde disco (idempotente al reiniciar)."""
    global open_positions
    open_positions = {}
    try:
        if os.path.exists(pnl_log_path):
            with open(pnl_log_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                for entry in data:
                    if entry.get("status") == "open":
                        open_positions[entry.get("trade_id")] = entry
        logger.info(f"📂 {len(open_positions)} posiciones abiertas cargadas desde disco.")
    except Exception as e:
        logger.warning(f"No se pudo cargar el historial de posiciones: {e}")


def save_position(position: dict):
    """Persiste posición/trade en el log de PnL."""
    os.makedirs("artifacts", exist_ok=True)
    existing = []
    if os.path.exists(pnl_log_path):
        try:
            with open(pnl_log_path, "r", encoding="utf-8") as f:
                existing = json.load(f)
        except Exception:
            existing = []

    # Actualizar si ya existe la misma trade_id
    updated = False
    for i, entry in enumerate(existing):
        if entry.get("trade_id") == position.get("trade_id"):
            existing[i] = position
            updated = True
            break
    if not updated:
        existing.append(position)

    with open(pnl_log_path, "w", encoding="utf-8") as f:
        json.dump(existing, f, indent=2, ensure_ascii=False)


# ── Salvaguardas ───────────────────────────────────────────────────────────────
def check_price_guard(price: float) -> tuple[bool, str]:
    """Rechaza si el precio es demasiado alto (riesgo asimétrico negativo)."""
    if price > MAX_PRICE:
        return False, f"Precio {price:.3f} > máximo {MAX_PRICE} USDC"
    return True, ""


def check_concurrent_guard() -> tuple[bool, str]:
    """Rechaza si superamos el máximo de posiciones concurrentes."""
    open_count = len(open_positions)
    if open_count >= MAX_CONCURRENT:
        return False, f"Posiciones abiertas ({open_count}) >= máximo ({MAX_CONCURRENT})"
    return True, ""


async def check_market_timing(market_id: str) -> tuple[bool, str]:
    """
    Verifica que el mercado no cierre en menos de MIN_MINUTES_TO_CLOSE minutos.
    Consulta Gamma API para obtener el end_date del mercado.
    """
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"https://gamma-api.polymarket.com/markets/{market_id}",
                timeout=aiohttp.ClientTimeout(total=8),
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    end_date_str = data.get("endDate") or data.get("end_date_iso")
                    if end_date_str:
                        end_date = datetime.fromisoformat(end_date_str.replace("Z", "+00:00"))
                        minutes_left = (end_date - datetime.now(timezone.utc)).total_seconds() / 60
                        if minutes_left < MIN_MINUTES_TO_CLOSE:
                            return False, f"Mercado cierra en {minutes_left:.0f} min (mín: {MIN_MINUTES_TO_CLOSE})"
    except Exception as e:
        logger.warning(f"No se pudo verificar timing del mercado: {e}")

    return True, ""  # Si no se puede verificar, permitimos (fail-open)


def check_duplicate_position(market_id: str) -> tuple[bool, str]:
    """Rechaza si ya tenemos una posición abierta en este mercado."""
    if market_id in open_positions:
        return False, f"Ya existe posición abierta en mercado {market_id}"
    return True, ""


# ── Ejecución de orden ─────────────────────────────────────────────────────────
async def execute_copy_trade(signal: dict) -> dict:
    """
    Ejecuta (o simula) una orden de compra o venta en el mismo outcome que la señal.
    Retorna el registro de la posición.
    """
    market_id  = signal["market_id"]
    asset_id   = signal["asset_id"]
    outcome    = signal["outcome"]
    price      = signal["price"]
    side       = signal.get("side", "BUY").upper()
    timestamp  = datetime.now(timezone.utc).isoformat()
    trade_id   = f"{market_id[:8]}_{int(datetime.now(timezone.utc).timestamp())}"

    # Extracción del capital de Inyección del motor Aggregator 
    if "_agg_capital_override_" in signal:
        capital = signal["_agg_capital_override_"]
        logger.info(f"[SHADOW] Capital Override (Aggregator): ${capital:.2f} USDC")
    else:
        # Fallback de venta o señal legacy (usa el de .env)
        capital = COPY_TRADE_USDC


    # Cálculo inicial de shares basado en capital
    size_shares = round(capital / price, 2) if price > 0 else 0

    # ── Min Size Guard & Safety Cap ($7) ──────────────────────────────────────
    if not DRY_RUN and clob_client and side == "BUY":
        try:
            book = await asyncio.to_thread(clob_client.get_book, asset_id)
            min_shares = float(book.get("min_order_size", 0))
            if min_shares > 0 and size_shares < min_shares:
                required_capital = round(min_shares * price, 2)
                if required_capital > 7.0:
                    logger.warning(
                        f"⚠️ ABORTO: Mínimo de mercado ({min_shares} shares) requiere ${required_capital:.2f}, "
                        f"que supera el tope de $7.00. Mercado: {market_id}"
                    )
                    return {
                        "status": "failed",
                        "error": f"Safety Cap Exceeded: needs ${required_capital} for min {min_shares} shares",
                        "market_id": market_id
                    }
                
                logger.info(f"⚖️ ESCALANDO AL MÍNIMO: {size_shares} -> {min_shares} shares (Costo: ${required_capital:.2f})")
                size_shares = min_shares
                capital = required_capital
        except Exception as e:
            logger.warning(f"No se pudo consultar min_order_size del libro: {e}. Procediendo con cálculo base.")

    position = {
        "trade_id":        trade_id,
        "market_id":       market_id,
        "market_name":     signal["market_name"],
        "asset_id":        asset_id,
        "outcome":         outcome,
        "entry_price":     price,
        "side":            side,
        "copy_trade_usdc": capital,
        "size_shares":     size_shares,
        "signal_type":     signal.get("signal_type", "shadow_mirror"),
        "wallet_address":  signal.get("wallet_address", "unknown"),
        "whale_size_usdc": signal.get("trade_size_usdc", 0.0),
        "impact_score":    signal.get("impact_score", 0.0),
        "status":          "open",
        "dry_run":         DRY_RUN,
        "entry_timestamp": timestamp,
        "exit_timestamp":  None,
        "exit_price":      None,
        "pnl_usdc":        None,
    }

    if DRY_RUN:
        logger.info(
            f"🟡 [DRY RUN] Copy trade simulado ({side}): {outcome} @ {price:.3f} | "
            f"${COPY_TRADE_USDC} | {signal['market_name'][:50]}"
        )
        if side == "BUY":
            open_positions[trade_id] = position
        elif side == "SELL":
            # Lógica simple de cierre para todas las posiciones de este mercado
            to_close = [tid for tid, p in open_positions.items() if p.get("market_id") == market_id]
            for tid in to_close:
                prev = open_positions.pop(tid)
                pos_close = prev.copy()
                pos_close["status"] = "closed"
                pos_close["exit_price"] = price
                pos_close["exit_timestamp"] = timestamp
                pos_close["pnl_usdc"] = (price - prev["entry_price"]) * prev["size_shares"]
                logger.info(f"Cerrando posición simulada {tid} con PnL: ${pos_close['pnl_usdc']:.2f}")
                save_position(pos_close)
            
            if not to_close:
                position["status"] = "failed"
                position["error"] = "No open positions found to close"
        
        save_position(position) if side == "BUY" else None
        return position

    # ── Ejecución Real ─────────────────────────────────────────────────────────
    if not clob_client:
        logger.error("❌ ClobClient no inicializado. Cancelando ejecución.")
        position["status"] = "failed"
        position["error"]  = "ClobClient offline"
        save_position(position)
        return position

    order_args = OrderArgs(
        price=price,
        size=position["size_shares"],
        side=BUY if side == "BUY" else SELL,
        token_id=asset_id,
    )

    try:
        # Petición a Polymarket API enviada en un thread separado para no bloquear asynico
        resp = await asyncio.to_thread(clob_client.create_and_post_order, order_args)
        
        if resp and resp.get("success"):
            position["order_id"] = resp.get("orderID", "unknown")
            logger.info(f"✅ Orden {side} ejecutada: {position['order_id']}")
            if side == "SELL":
                to_close = [tid for tid, p in open_positions.items() if p.get("market_id") == market_id]
                for tid in to_close:
                    prev = open_positions.pop(tid)
                    pos_close = prev.copy()
                    pos_close["status"] = "closed"
                    pos_close["exit_price"] = price
                    pos_close["exit_timestamp"] = timestamp
                    pos_close["pnl_usdc"] = (price - prev["entry_price"]) * prev["size_shares"]
                    logger.info(f"Cerrando posición {tid} en API Live con PnL: ${pos_close['pnl_usdc']:.2f}")
                    save_position(pos_close)
                position["status"] = "closed" if to_close else "failed"
        else:
            logger.error(f"❌ Error en orden {side}: {resp}")
            position["status"] = "failed"
            position["error"]  = str(resp)
    except Exception as e:
        logger.error(f"❌ Excepción ejecutando orden {side}: {e}")
        position["status"] = "failed"
        position["error"]  = str(e)

    if side == "BUY" and position["status"] != "failed":
        open_positions[trade_id] = position
    
    save_position(position) if side == "BUY" else None
    return position


async def auto_settle_loop():
    """
    Revisa periódicamente los mercados abiertos en disco.
    Si han completado resolución en Polymarket, cierra la posición automáticamente 
    (settlement) actualizando el P/L.
    """
    while True:
        await asyncio.sleep(60 * 5)  # Cada 5 minutos
        logger.info("🔄 Verificando resolución automática de mercados (Settlement)...")
        
        for trade_id, pos in list(open_positions.items()):
            market_id = pos.get("market_id")
            if not market_id or pos.get("status") != "open":
                continue
                
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(
                        f"{CLOB_API_URL}/markets/{market_id}",
                        timeout=aiohttp.ClientTimeout(total=10)
                    ) as resp:
                        if resp.status == 200:
                            m = await resp.json()
                            is_closed = m.get("closed", False)
                            tokens = m.get("tokens", [])
                            target_outcome = pos.get("outcome")
                            
                            c_price = None
                            is_winner = False
                            for token in tokens:
                                if token.get("outcome") == target_outcome:
                                    c_price = float(token.get("price", 0.0))
                                    is_winner = token.get("winner", False)
                                    break
                            
                            if c_price is not None:
                                settle = False
                                final_price = c_price
                                
                                if is_winner:
                                    settle, final_price = True, 1.0
                                elif is_closed:
                                    settle = True
                                    final_price = 1.0 if is_winner else 0.0
                                elif c_price >= 0.99:
                                    settle, final_price = True, 1.0
                                elif c_price <= 0.01:
                                    settle, final_price = True, 0.0
                                    
                                if settle:
                                    pos["status"] = "closed"
                                    pos["exit_price"] = final_price
                                    pos["exit_timestamp"] = datetime.now(timezone.utc).isoformat()
                                    
                                    shares = float(pos.get("size_shares", 0))
                                    entry_usdc = float(pos.get("copy_trade_usdc", 0))
                                    exit_usdc = shares * final_price
                                    pos["pnl_usdc"] = exit_usdc - entry_usdc
                                    
                                    open_positions.pop(trade_id, None)
                                    save_position(pos)
                                    logger.info(f"✅ [SETTLEMENT] Mercado resuelto: {pos['market_name'][:40]} | Outcome final: {final_price:.2f} | P/L: ${pos['pnl_usdc']:+.2f} USDC")
            except Exception as e:
                logger.error(f"Error en auto-settlement para {market_id[:8]}: {e}")
            
            await asyncio.sleep(1.0)



# ── Tareas de fondo ────────────────────────────────────────────────────────────

async def buffer_reaper_loop():
    """Limpia los buffers de agregación que han excedido el timeout de inactividad."""
    while True:
        await asyncio.sleep(10)
        now = datetime.now(timezone.utc).timestamp()
        expired = []
        for mid, b in list(market_buffers.items()):
            if now - b["last_update"] > AGG_TIMEOUT_SEC:
                expired.append(mid)
        for mid in expired:
            logger.info(f"🧹 [AGG TIMEOUT] Expurgando buffer inactivo para {mid[:8]}")
            market_buffers.pop(mid, None)

def clean_execs_rate_limit():
    """Limpia el histórico de rate limit."""
    global execs_timestamps
    now = datetime.now(timezone.utc).timestamp()
    execs_timestamps = [ts for ts in execs_timestamps if now - ts < 60]

# ── Proceso principal ──────────────────────────────────────────────────────────
async def run_copy_trader(signal_queue: asyncio.Queue):
    """
    Loop que consume señales de la cola del scanner y las procesa mediante el
    motor de agregación por intención.
    """
    load_open_positions()
    logger.info(f"🏦 Copy Trader (v3.0 Aggregation) activo | Modo: {'DRY RUN 🟡' if DRY_RUN else 'LIVE 🟢'}")

    asyncio.create_task(auto_settle_loop())
    asyncio.create_task(buffer_reaper_loop())

    while True:
        try:
            signal = await asyncio.wait_for(signal_queue.get(), timeout=60)
        except asyncio.TimeoutError:
            continue

        market_id = signal["market_id"]
        price     = signal["price"]
        side      = signal.get("side", "BUY")
        outcome   = signal.get("outcome", "UNKNOWN")
        raw_usdc  = float(signal.get("trade_size_usdc", 0))

        # Procesar SELL si es para cerrar posición. (Bypass de agregación)
        if side == "SELL":
            has_positions = any(p.get("market_id") == market_id for p in open_positions.values())
            if not has_positions:
                continue
            position = await execute_copy_trade(signal)
            logger.info(f"☑️ Posición cerrada por SELL manual: {position.get('trade_id', '?')}")
            continue

        if raw_usdc <= 0:
            continue

        ts_now = datetime.now(timezone.utc).timestamp()

        # Iniciar o recuperar buffer
        if market_id not in market_buffers:
            market_buffers[market_id] = {
                "outcome": outcome,
                "total_usdc": 0.0,
                "trade_count": 0,
                "last_update": ts_now
            }

        buf = market_buffers[market_id]

        # Si cambió la dirección dominante, reseteamos porque no hay consistencia
        if buf["outcome"] != outcome:
            logger.info(f"🔄 [AGG OVERRIDE] Cambio direccional en {market_id[:8]}: {buf['outcome']} -> {outcome}. Reseteando buffer.")
            buf["outcome"] = outcome
            buf["total_usdc"] = 0.0
            buf["trade_count"] = 0

        # Acumular volumen
        buf["total_usdc"] += raw_usdc
        buf["trade_count"] += 1
        buf["last_update"] = ts_now

        logger.info(f"💧 [AGG UPDATE] {market_id[:8]} ({outcome}) | Acumulado: ${buf['total_usdc']:.0f} (Threshold: {AGG_THRESHOLD_USDC}) | Trades: {buf['trade_count']}")

        # Disparador de Ejecución
        if buf["total_usdc"] >= AGG_THRESHOLD_USDC:
            
            # Filtro Frecuencia
            clean_execs_rate_limit()
            if len(execs_timestamps) >= AGG_MAX_EXECS_PER_MIN:
                logger.warning(f"⏳ [AGG SKIP] Rate limit excedido ({AGG_MAX_EXECS_PER_MIN}/min). Trade pospuesto.")
                continue
                
            # Filtro de Exposición Total
            total_invested = sum([float(p.get("copy_trade_usdc", 0)) for p in open_positions.values()])
            max_allowed = INITIAL_CAPITAL * (AGG_MAX_PORTFOLIO_EXPOSURE_PCT / 100.0)
            if total_invested >= max_allowed:
                logger.warning(f"🛡️ [AGG SKIP] Exposición máxima de portfolio alcanzada: ${total_invested:.2f} >= ${max_allowed:.2f}.")
                continue

            # Calcular tamaño de inversión (ej. 1% del volumen total que disparó la operación)
            ratio = float(signal.get("copy_ratio", SHADOW_COPY_RATIO))
            capital = round(buf["total_usdc"] * ratio, 2)
            
            # Min/Max boundaries
            if capital < SHADOW_MIN_USDC:
                capital = SHADOW_MIN_USDC
                
            if capital > AGG_MAX_TRADE_USDC:
                capital = AGG_MAX_TRADE_USDC
            
            logger.info(f"🎯 [AGG TRIGGER] ¡Intención validada! Ejecutando señal en {market_id[:8]} por ${capital:.2f} USDC.")

            # Limpiar buffer tras disparar
            del market_buffers[market_id]

            # Inyectar el capital sobreescrito en la señal para que execute_copy_trade no recalcule
            signal["_agg_capital_override_"] = capital

            # Registrar marca temporal para el rate limit
            execs_timestamps.append(datetime.now(timezone.utc).timestamp())

            # Ejecutar
            position = await execute_copy_trade(signal)
            logger.info(f"📋 Ejecución AGG: {position.get('trade_id', '?')} | Status: {position.get('status', 'failed')}")


