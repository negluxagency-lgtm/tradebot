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

MAX_PRICE             = float(os.getenv("MAX_PRICE_GUARD", "0.85"))
MIN_MINUTES_TO_CLOSE  = int(os.getenv("MIN_MINUTES_TO_CLOSE", "30"))

AGG_THRESHOLD_USDC = float(os.getenv("AGG_THRESHOLD_USDC", "100"))
AGG_TIMEOUT_SEC = int(os.getenv("AGG_TIMEOUT_SEC", "180"))
AGG_MAX_EXECS_PER_MIN = int(os.getenv("AGG_MAX_EXECS_PER_MIN", "5"))
AGG_MAX_TRADE_USDC = float(os.getenv("AGG_MAX_TRADE_USDC", "5.0"))  # Legacy, ahora como fallback

AGG_MAX_PORTFOLIO_EXPOSURE_PCT = float(os.getenv("AGG_MAX_PORTFOLIO_EXPOSURE_PCT", "50.0"))
INITIAL_CAPITAL = float(os.getenv("INITIAL_CAPITAL", "269"))

AUTO_TAKE_PROFIT_PCT = float(os.getenv("AUTO_TAKE_PROFIT_PCT", "40.0")) / 100.0
AUTO_STOP_LOSS_PCT = float(os.getenv("AUTO_STOP_LOSS_PCT", "50.0")) / 100.0

# ── Piecewise Linear Sizing (v4.0) ─────────────────────────────────────────────
# Curva de sizing: mapea el volumen del objetivo a nuestra inversión.
# Formato: (whale_usdc, our_usdc)
_PIECEWISE_BREAKPOINTS = [
    (1,    1),
    (10,   7),
    (20,   16),
    (50,   30),
    (100,  70),
    (500,  200),
    (1000, 400),
    (2000, 600),
]

def piecewise_copy_size(whale_usdc: float) -> float:
    """
    Calcula nuestra inversión usando interpolación lineal por tramos.
    Por encima de $2000 se aplica un 20% fijo.
    """
    if whale_usdc <= 0:
        return 0.0
    if whale_usdc < _PIECEWISE_BREAKPOINTS[0][0]:
        return whale_usdc  # 1:1 para micro-importes
    if whale_usdc > 2000:
        return round(whale_usdc * 0.20, 2)
    for i in range(len(_PIECEWISE_BREAKPOINTS) - 1):
        x0, y0 = _PIECEWISE_BREAKPOINTS[i]
        x1, y1 = _PIECEWISE_BREAKPOINTS[i + 1]
        if x0 <= whale_usdc <= x1:
            t = (whale_usdc - x0) / (x1 - x0)
            return round(y0 + t * (y1 - y0), 2)
    return _PIECEWISE_BREAKPOINTS[-1][1]


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
                if required_capital > 50.0:
                    logger.warning(
                        f"⚠️ ABORTO: Mínimo de mercado ({min_shares} shares) requiere ${required_capital:.2f}, "
                        f"que supera el tope de $50.00. Mercado: {market_id}"
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



async def auto_exit_loop():
    """
    Robot Francotirador de Ventas (Take-Profit & Stop-Loss).
    Revisa el precio actual cada 15 segundos y gatilla un SELL si el ROI
    alcanza los márgenes configurados.
    """
    await asyncio.sleep(5)  # Espera antes de arrancar
    while True:
        await asyncio.sleep(15)
        
        # Iterar sobre las posiciones existentes
        for trade_id, pos in list(open_positions.items()):
            market_id = pos.get("market_id")
            if not market_id or pos.get("status") != "open":
                continue
                
            entry_price = float(pos.get("entry_price", 0.0))
            if entry_price <= 0:
                continue

            try:
                # Consultamos Gamma API de forma ligera (podríamos usar CLOB, pero la gamma no requiere balance/sigs y es REST)
                async with aiohttp.ClientSession() as session:
                    async with session.get(
                        f"https://gamma-api.polymarket.com/markets/{market_id}",
                        timeout=aiohttp.ClientTimeout(total=5)
                    ) as resp:
                        if resp.status == 200:
                            m = await resp.json()
                            if m.get("closed", False):
                                continue # Lo maneja el auto_settle_loop
                                
                            tokens = m.get("tokens", [])
                            target_outcome = pos.get("outcome")
                            
                            c_price = None
                            for token in tokens:
                                if token.get("outcome") == target_outcome:
                                    c_price = float(token.get("price", 0.0))
                                    break
                            
                            if c_price is not None and c_price > 0:
                                roi = (c_price - entry_price) / entry_price
                                
                                trigger_sell = False
                                sell_reason = ""
                                
                                if roi >= AUTO_TAKE_PROFIT_PCT or c_price >= 0.95:
                                    trigger_sell = True
                                    sell_reason = f"TAKE PROFIT (+{roi*100:.1f}%)"
                                elif roi <= -AUTO_STOP_LOSS_PCT:
                                    trigger_sell = True
                                    sell_reason = f"STOP LOSS ({roi*100:.1f}%)"
                                
                                if trigger_sell:
                                    logger.warning(f"🚨 [AUTO-EXIT] {sell_reason} disparado en {market_id[:8]}! C_Price: {c_price:.2f} (Entry: {entry_price:.2f})")
                                    # Formar señal sintética de SELL
                                    exit_signal = {
                                        "market_id": market_id,
                                        "market_name": pos.get("market_name", "UNKNOWN"),
                                        "asset_id": pos.get("asset_id"),
                                        "outcome": target_outcome,
                                        "side": "SELL",
                                        "price": c_price, # Manda al Clob el peor caso de mercado o el precio mid actual
                                        "trade_size_usdc": 0,
                                        "signal_type": "auto_exit"
                                    }
                                    
                                    # Llamar al ejecutor (que calculará automaticamente si es SELL el aggregate)
                                    asyncio.create_task(execute_copy_trade(exit_signal))
            except Exception as e:
                logger.error(f"[AUTO-EXIT] Error revisando mercado {market_id[:8]}: {e}")

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

def get_current_portfolio_value() -> float:
    """Calcula el P/L total cerrado y lo suma al INITIAL_CAPITAL para saber si estamos arruinados."""
    try:
        if not os.path.exists(pnl_log_path): return INITIAL_CAPITAL
        with open(pnl_log_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            total_pnl = sum(float(p.get("pnl_usdc", 0) or 0) for p in data if p.get("status") in ["closed", "closed_tp", "closed_sl"])
            return INITIAL_CAPITAL + total_pnl
    except Exception:
        return INITIAL_CAPITAL

async def run_copy_trader(signal_queue: asyncio.Queue):
    """
    Loop que consume señales de la cola del scanner y las procesa mediante el
    motor de agregación por intención.
    """
    load_open_positions()
    logger.info(f"🏦 Copy Trader (v3.0 Aggregation) activo | Modo: {'DRY RUN 🟡' if DRY_RUN else 'LIVE 🟢'}")

    asyncio.create_task(auto_settle_loop())
    asyncio.create_task(buffer_reaper_loop())
    asyncio.create_task(auto_exit_loop())

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

        # ── SELLs: Bypass directo para cerrar posición ───────────────────────
        if side == "SELL":
            has_positions = any(p.get("market_id") == market_id for p in open_positions.values())
            if not has_positions:
                continue
            asyncio.create_task(execute_copy_trade(signal))
            continue

        if raw_usdc <= 0:
            continue

        # ── CIRCUIT BREAKER DE SALDO ──────────────────────────────────────────
        current_account_value = get_current_portfolio_value()
        if current_account_value <= 150.0:
            logger.error(f"🛑 [CIRCUIT BREAKER] SALDO CRÍTICO: ${current_account_value:.2f}. Bot detenido (<= $150).")
            await asyncio.sleep(60)
            continue

        # ── Rate limit de ejecución ───────────────────────────────────────────
        clean_execs_rate_limit()
        if len(execs_timestamps) >= AGG_MAX_EXECS_PER_MIN:
            logger.warning(f"⏳ [SKIP] Rate limit excedido ({AGG_MAX_EXECS_PER_MIN}/min). Trade omitido.")
            continue
            
        # ── Filtro de Exposición Total ────────────────────────────────────────
        total_invested = sum([float(p.get("copy_trade_usdc", 0)) for p in open_positions.values()])
        max_allowed = INITIAL_CAPITAL * (AGG_MAX_PORTFOLIO_EXPOSURE_PCT / 100.0)
        if total_invested >= max_allowed:
            logger.warning(f"🛡️ [SKIP] Exposición máxima alcanzada: ${total_invested:.2f} >= ${max_allowed:.2f}.")
            continue

        # ── Piecewise Linear Sizing v4.0 ──────────────────────────────────────
        # Ahora el sizing se aplica sobre el tamaño INDIVIDUAL de cada operación
        # detectada, sin esperar a acumular volumen (Mirror Trading puro).
        capital = piecewise_copy_size(raw_usdc)

        # Garantizar mínimo operativo (evitar rechazos de exchange)
        if capital < SHADOW_MIN_USDC:
            capital = SHADOW_MIN_USDC

        logger.info(
            f"🎯 [MIRROR] Whale: ${raw_usdc:.2f} -> Nosotros: ${capital:.2f} | "
            f"{signal.get('market_name', 'Mercado')[:40]}"
        )

        # Inyectar capital sobreescrito
        signal["_agg_capital_override_"] = capital

        # Registrar marca temporal
        execs_timestamps.append(datetime.now(timezone.utc).timestamp())

        # Ejecución inmediata
        position = await execute_copy_trade(signal)
        logger.info(f"📋 Mirror Execution: {position.get('trade_id', '?')} | Status: {position.get('status', 'failed')}")



