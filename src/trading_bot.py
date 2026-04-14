"""
Trading Bot Principal - Antigravity EUR/USD London Session Bot
─────────────────────────────────────────────────────────────
Estrategia: Retroceso 2x15m a favor de la tendencia diaria.
Horario:    09:00 - 14:00 (Hora Española CET/CEST)
Operando:   EUR/USD en Polymarket (mercados binarios de precio)
─────────────────────────────────────────────────────────────
"""
import os
import sys
import csv
import json
import time
import logging
import requests
from datetime import datetime
from typing import Optional
from dotenv import load_dotenv
from pathlib import Path

import pytz

# Cargar módulos propios
import sys
sys.path.insert(0, str(Path(__file__).parent))
from data_engine import (
    get_daily_candles, get_15m_candles, get_5m_candles,
    get_current_price
)
from strategy_engine import (
    detect_daily_trend, detect_entry_signal,
    build_trade_setup, check_exit,
    Signal, Trend, TradeSetup
)
from supabase_engine import log_trade_to_supabase, update_trade_in_supabase

# ── Configuración ──────────────────────────────────────────
load_dotenv(Path(__file__).parent.parent / ".env.local")

POLY_HOST          = os.getenv("POLY_HOST", "https://clob.polymarket.com")
RELAYER_API_KEY    = os.getenv("RELAYER_API_KEY", "")
RELAYER_ADDRESS    = os.getenv("RELAYER_API_KEY_ADDRESS", "")
# Sincronización con claves existentes en .env.local
POLY_PRIVATE_KEY   = os.getenv("POLYMARKET_PRIVATE_KEY", "")
FUNDER_ADDRESS     = os.getenv("POLYMARKET_PROXY_ADDRESS", "")
DRY_RUN            = os.getenv("DRY_RUN", "true").lower() == "true"
TRADE_AMOUNT_USDC  = float(os.getenv("TRADE_AMOUNT_USDC", "1750.0"))

CYCLE_INTERVAL_SEC = 60 * 5   # Cada 5 minutos (Hiper-Ignición)
LOG_FILE           = Path(__file__).parent.parent / "artifacts" / "strategy_log.csv"
TIMEZONE_ES        = pytz.timezone("Europe/Madrid")

# ── Logging ────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(
            Path(__file__).parent.parent / "artifacts" / "bot.log",
            encoding="utf-8"
        )
    ]
)
logger = logging.getLogger(__name__)

# ── Estado del Bot ─────────────────────────────────────────
LOCK_FILE = Path(__file__).parent.parent / ".bot.lock"

class BotState:
    def __init__(self):
        self.active_trade: Optional[TradeSetup] = None
        self.trade_open:   bool = False
        self.session_trades: int = 0
        self.session_pnl:    float = 0.0
        self.active_supabase_id: str = ""

    def acquire_lock(self):
        if LOCK_FILE.exists():
            try:
                # Verificar si el proceso del lock sigue vivo
                with open(LOCK_FILE, "r") as f:
                    old_pid = int(f.read().strip())
                os.kill(old_pid, 0) # No mata, solo chequea existencia
                logger.error(f"[ERROR] Ya hay una instancia activa (PID {old_pid}). Abortando misión.")
                sys.exit(1)
            except (OSError, ValueError):
                # El proceso no existe o el archivo está corrupto, lo sobreescribimos
                pass
        
        with open(LOCK_FILE, "w") as f:
            f.write(str(os.getpid()))
        logger.info(f"[LOCK] Instancia única bloqueada (PID {os.getpid()})")

    def release_lock(self):
        if LOCK_FILE.exists():
            LOCK_FILE.unlink()
            logger.info("[LOCK] Candado de seguridad liberado.")

state = BotState()


# ── Integración Polymarket (Relayer) ───────────────────────
def get_relayer_headers() -> dict:
    return {
        "x-api-key":               RELAYER_API_KEY,
        "x-api-signature-address": RELAYER_ADDRESS,
        "Content-Type":            "application/json",
        "User-Agent":              "Antigravity-Trading-Bot/2026"
    }

def find_btc_market() -> Optional[dict]:
    """Busca el mercado de BTC activo más cercano en Polymarket."""
    try:
        resp = requests.get(
            f"{POLY_HOST}/markets",
            headers=get_relayer_headers(),
            params={"limit": 100},
            timeout=10
        )
        if resp.status_code != 200:
            logger.warning(f"[POLY] Error buscando mercados: {resp.status_code}")
            return None

        markets = resp.json().get("data", [])
        # Filtrar mercados de Bitcoin vs USD activos
        btc_markets = [
            m for m in markets
            if ("BTC" in m.get("question", "").upper() or "BITCOIN" in m.get("question", "").upper())
            and "USD" in m.get("question", "").upper()
            and m.get("active", False)
        ]

        if not btc_markets:
            logger.warning("[POLY] No se encontraron mercados BTC/USD activos.")
            return None

        # Elegir el mercado con mayor liquidez (mayor volumen)
        best = max(btc_markets, key=lambda m: float(m.get("volume", 0)))
        logger.info(f"[POLY] Mercado seleccionado: {best.get('question')} | ID: {best.get('id')}")
        return best

    except Exception as e:
        logger.error(f"[POLY] Error en find_btc_market: {e}")
        return None

def place_order(market_data: dict, setup: TradeSetup, share_price: float = 0.5) -> bool:
    """
    Coloca una orden en Polymarket calculando el número de acciones.
    Invierta TRADE_AMOUNT_USDC en el contrato correspondiente (YES/NO).
    """
    market_id = market_data.get('id')
    
    # Calcular cuántas acciones comprar para gastar TRADE_AMOUNT_USDC
    # size = total_usdc / price_per_share
    size = int(TRADE_AMOUNT_USDC / share_price)

    if DRY_RUN:
        logger.info(f"[DRY-RUN] Invirtiendo ${TRADE_AMOUNT_USDC} USDC ({size} acciones a ${share_price}) en {market_id}")
        return True

    # YES suele ser el primer token, NO el segundo en el array 'tokens' del mercado
    tokens = market_data.get("tokens", [])
    token_index = 0 if setup.signal == Signal.BUY else 1
    if len(tokens) <= token_index:
        logger.error("[POLY] No se encontraron tokens para el mercado.")
        return False
        
    token_id = tokens[token_index].get("token_id")

    payload = {
        "marketId":  market_id,
        "tokenId":   token_id,
        "side":      "BUY",  # Siempre BUY para entrar en la posición
        "size":      size,
        "price":     share_price,
        "type":      "LIMIT"
    }

    try:
        resp = requests.post(
            f"{POLY_HOST}/order",
            headers=get_relayer_headers(),
            json=payload,
            timeout=10
        )
        if resp.status_code in [200, 201]:
            logger.info(f"[POLY] Orden ejecutada exitosamente en token {token_id}")
            return True
        else:
            logger.error(f"[POLY] Error en orden: {resp.status_code} | {resp.text[:200]}")
            return False
    except Exception as e:
        logger.error(f"[POLY] Excepción colocando orden: {e}")
        return False

def close_position(reason: str, current_price: float):
    """Cierra la posición activa y registra el resultado."""
    global state
    if not state.trade_open or state.active_trade is None:
        return

    entry = state.active_trade.entry_price
    if state.active_trade.signal == Signal.BUY:
        pnl_pct = (current_price - entry) / entry * 100
    else:
        pnl_pct = (entry - current_price) / entry * 100

    state.session_pnl += pnl_pct
    state.trade_open   = False

    logger.info(
        f"[CLOSE] Posición cerrada por {reason} | "
        f"Entrada: {entry:.5f} | Salida: {current_price:.5f} | PnL: {pnl_pct:+.4f}%"
    )

    # Actualizar Supabase
    if state.active_supabase_id:
        update_trade_in_supabase(state.active_supabase_id, {
            "exit_price": current_price,
            "pnl_pct":    pnl_pct,
            "result":     reason,
            "reason":     f"Closed by strategy: {reason}"
        })
        state.active_supabase_id = ""

    log_trade(
        signal=state.active_trade.signal.value,
        trend=state.active_trade.trend.value,
        entry=entry,
        exit_price=current_price,
        tp=state.active_trade.take_profit,
        sl=state.active_trade.stop_loss,
        result=reason,
        pnl=pnl_pct
    )
    state.active_trade = None


# ── Logging de Operaciones ─────────────────────────────────
def log_trade(**kwargs):
    """Materializa el registro en artifacts/strategy_log.csv"""
    LOG_FILE.parent.mkdir(exist_ok=True)
    write_header = not LOG_FILE.exists()
    with open(LOG_FILE, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "timestamp", "signal", "trend", "entry", "exit_price",
            "tp", "sl", "result", "pnl"
        ])
        if write_header:
            writer.writeheader()
        writer.writerow({
            "timestamp": datetime.now(TIMEZONE_ES).strftime("%Y-%m-%d %H:%M:%S"),
            **kwargs
        })


# ── Ciclo Principal ────────────────────────────────────────
def run_cycle():
    """Ejecuta un ciclo de análisis y toma de decisiones."""
    global state

    now_es = datetime.now(TIMEZONE_ES)
    logger.info(f"{'='*55}")
    logger.info(f"[CICLO] {now_es.strftime('%Y-%m-%d %H:%M:%S')} CET/CEST | Modo Crypto 24/7 activo.")

    # 1. Obtener precio actual
    current_price = get_current_price()
    if current_price <= 0:
        logger.error("[CICLO] Precio no disponible. Saltando ciclo.")
        return

    # 3. Monitorear posición abierta (TP/SL)
    if state.trade_open and state.active_trade:
        exit_reason = check_exit(state.active_trade, current_price)
        if exit_reason:
            close_position(exit_reason, current_price)
            return
        else:
            logger.info(
                f"[POSICION] Abierta {state.active_trade.signal.value} | "
                f"Precio: {current_price:.5f} | "
                f"TP: {state.active_trade.take_profit:.5f} | "
                f"SL: {state.active_trade.stop_loss:.5f}"
            )
            return  # Ya hay una posición activa, no abrir otra

    # 3. Analizar tendencia diaria
    daily_df = get_daily_candles(5)
    trend    = detect_daily_trend(daily_df)

    if trend == Trend.NEUTRAL:
        logger.info("[CICLO] Tendencia neutral o bloqueada por Macro EMA20.")
        return

    # 4. Detectar señal de 5m
    candles_5m = get_5m_candles()
    signal     = detect_entry_signal(candles_5m, trend)

    if signal == Signal.NONE:
        return

    # 5. Construir setup y ejecutar orden
    setup = build_trade_setup(signal, trend, current_price)
    if not setup:
        return

    # 6. Buscar mercado BTC/USD en Polymarket y colocar orden
    market_data = find_btc_market()
    market_id   = market_data.get('id') if market_data else "MOCK_ID"

    if DRY_RUN:
        logger.info(f"[DRY-RUN] Simulación de orden: {setup}")
        success = True
    elif market_data and place_order(market_data, setup):
        success = True
    else:
        logger.warning("[CICLO] No se pudo colocar la orden o no hay mercado disponible.")
        success = False

    if success:
        state.active_trade = setup
        state.trade_open   = True
        state.session_trades += 1
        
        # Registrar en Supabase (SIEMPRE, incluso en DRY_RUN)
        state.active_supabase_id = log_trade_to_supabase({
            "symbol":      "BTC-USD",
            "side":        setup.signal.value,
            "trend":       setup.trend.value,
            "entry_price": current_price,
            "take_profit": setup.take_profit,
            "stop_loss":   setup.stop_loss,
            "is_live":     not DRY_RUN
        })


def print_banner():
    logger.info("""
╔══════════════════════════════════════════════════╗
║    ANTIGRAVITY TRADING BOT — BITCOIN CRYPTO      ║
║    Operación Continua: 24/7 (Fase Ignición)      ║
║    TP: +3.00% | SL: -2.15% (Escudo de Oro)       ║
║    Capital: $1,750 USDC p/ trade                 ║
╚══════════════════════════════════════════════════╝
""")


def main():
    print_banner()

    if DRY_RUN:
        logger.info("[CONFIG] Modo DRY_RUN activado. No se usará capital real.")
    else:
        logger.info("[CONFIG] Modo PRODUCCIÓN. Capital real en riesgo.")
        if not POLY_PRIVATE_KEY or not FUNDER_ADDRESS:
            logger.critical("[CONFIG] Faltan credenciales POLYMARKET_PRIVATE_KEY o POLYMARKET_PROXY_ADDRESS. Abortando misión.")
            return

    logger.info(f"[CONFIG] Ciclo cada {CYCLE_INTERVAL_SEC // 60} minutos.")
    logger.info("[LAUNCH] Propulsores a máxima potencia. Iniciando bucle de trading.")

    state.acquire_lock()
    
    try:
        while True:
            try:
                run_cycle()
                logger.info(
                    f"[STATUS] Operaciones sesión: {state.session_trades} | "
                    f"PnL sesión: {state.session_pnl:+.4f}%"
                )
            except KeyboardInterrupt:
                logger.info("[ABORT] Interrupción manual. Cerrando bot.")
                if state.trade_open:
                    close_position("ABORT_MANUAL", get_current_price())
                break
            except Exception as e:
                logger.error(f"[ERROR] Turbulencia inesperada: {e}")

            logger.info(f"[SLEEP] Próximo ciclo en {CYCLE_INTERVAL_SEC // 60} minutos.")
            time.sleep(CYCLE_INTERVAL_SEC)
    finally:
        state.release_lock()


if __name__ == "__main__":
    main()
