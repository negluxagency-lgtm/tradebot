"""
🛰️ Whale Scanner — Whale Insider Tracker
Responsabilidad: Conexión WebSocket a Polymarket CLOB, ventana deslizante,
detección de anomalías Z-Score, Fresh Wallet y Clustering.
"""
import os
import json
import asyncio
import logging
import statistics
from collections import deque, defaultdict
from datetime import datetime, timezone, timedelta
import time
import math

import aiohttp
import websockets
from dotenv import load_dotenv

from alert_engine import dispatch_alert
from wallet_tracker import tracker

load_dotenv(".env.local")

# ── Configuración ──────────────────────────────────────────────────────────────
CLOB_WS_URL      = "wss://ws-subscriptions-clob.polymarket.com/ws/market"  # /market es obligatorio
GAMMA_API_URL    = "https://gamma-api.polymarket.com"
CLOB_API_URL     = os.getenv("POLY_HOST", "https://clob.polymarket.com")

Z_THRESHOLD           = float(os.getenv("Z_THRESHOLD", "2.0")) # Mantenido para alertas legacy Z-Score si aplica
WHALE_MIN_USDC        = float(os.getenv("WHALE_MIN_USDC", "10000"))
CLUSTER_WINDOW_MIN    = int(os.getenv("CLUSTER_WINDOW_MINUTES", "10"))
CLUSTER_MIN_WALLETS   = int(os.getenv("CLUSTER_MIN_WALLETS", "3"))
MIN_MARKET_VOLUME     = float(os.getenv("MIN_MARKET_VOLUME_USDC", "5000000"))

# Nuevas configuraciones temporales y de ruido
NOISE_FILTER_USDC     = 50.0   # Ignorar operaciones enanas para no ensuciar la estadística
MICRO_WINDOW_SEC      = 3.0    # 3s para evaluar 'Velocity Burst'
ALGO_WINDOW_SEC       = 10.0   # 10s para evaluar 'Algorithmic Splits'
MACRO_WINDOW_SEC      = 120.0  # Limpieza de memoria (retener solo max 2 mins)

# Categorías con mayor potencial insider — filtro por keywords en la pregunta
# Los campos 'tags' y 'category' son NULL en la Gamma API real.
TARGET_KEYWORDS = {
    # Política / Gobierno
    "president", "prime minister", "election", "minister", "senate", "congress",
    "governor", "parliament", "vote", "resign", "impeach", "chancellor",
    # Geopolítica / Regulación
    "war", "sanctions", "treaty", "nato", "un ", "ceasefire", "invasion",
    "tariff", "trade", "fed ", "interest rate", "gdp",
    # Cripto
    "bitcoin", "btc", "ethereum", "eth", "sec", "etf", "crypto", "blockchain",
    "coinbase", "binance", "stablecoin",
    # Legal / Corporativo
    "fda", "lawsuit", "court", "ruling", "merger", "acquisition", "ipo",
    "bankrupt", "indicted", "arrested", "charged",
    # World Cup / grandes eventos con insiders conocidos
    "world cup", "fifa", "championship", "tournament",
}

# ── Logger ─────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s — %(message)s",
    handlers=[
        logging.FileHandler("artifacts/whale_tracker.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger("whale_scanner")
os.makedirs("artifacts", exist_ok=True)


# ── Estado en Memoria ──────────────────────────────────────────────────────────
# Historial temporal: market_id → deque de dicts {'ts': float, 'size': float, 'wallet': str, 'side': str, 'price': float}
trade_history: dict[str, deque] = defaultdict(deque)

# Clustering tracker: market_id → outcome → lista de (wallet, timestamp)
cluster_tracker: dict[str, dict] = defaultdict(lambda: defaultdict(list))

# Caché de metadatos de mercados: asset_id → {market_id, market_name, outcome, condition_id}
market_meta: dict[str, dict] = {}

# Control de señales enviadas recientemente (evitar duplicados)
recent_signals: dict[str, datetime] = {}
SIGNAL_COOLDOWN_MIN = 30  # no repetir alerta del mismo mercado en 30 min


# ── Paso 1: Obtener mercados activos ────────────────────────────────────────────
def _matches_target(question: str) -> bool:
    """Verifica si la pregunta contiene alguna keyword de interés insider."""
    q_lower = question.lower()
    return any(kw in q_lower for kw in TARGET_KEYWORDS)


async def fetch_active_markets() -> list[dict]:
    """Obtiene mercados activos de Polymarket filtrando por volumen y keywords."""
    logger.info("Obteniendo mercados activos de Gamma API...")
    markets = []
    offset  = 0
    limit   = 100

    async with aiohttp.ClientSession() as session:
        while True:
            params = {
                "active":    "true",
                "closed":    "false",
                "limit":     limit,
                "offset":    offset,
                "order":     "volume",
                "ascending": "false",
            }
            try:
                async with session.get(
                    f"{GAMMA_API_URL}/markets",
                    params=params,
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as resp:
                    if resp.status != 200:
                        logger.error(f"Gamma API error {resp.status}")
                        break
                    data = await resp.json()
                    batch = data if isinstance(data, list) else data.get("markets", [])
                    if not batch:
                        break

                    for m in batch:
                        volume = float(m.get("volume", 0) or 0)
                        if volume < MIN_MARKET_VOLUME:
                            # Los mercados están ordenados por volumen desc;
                            # si este ya está por debajo del mínimo, los siguientes también.
                            break

                        question = m.get("question") or ""
                        if not _matches_target(question):
                            continue  # no es un mercado de interés

                        if not m.get("enableOrderBook") and not m.get("acceptingOrders"):
                            continue  # mercado sin order book activo

                        markets.append(m)

                    if len(batch) < limit:
                        break
                    offset += limit

            except Exception as e:
                logger.error(f"Error fetching markets: {e}")
                break

    logger.info(f"✅ {len(markets)} mercados objetivo identificados.")
    return markets


# ── Paso 2: Construir caché de metadatos ────────────────────────────────────────
def build_market_meta(markets: list[dict]):
    """Mapea asset_id → metadatos del mercado para resolución rápida.
    
    La Gamma API devuelve los token IDs en el campo 'clobTokenIds' (lista de strings).
    El campo 'outcomes' contiene los nombres (ej: ['Yes','No']).
    """
    global market_meta
    market_meta.clear()

    for m in markets:
        market_id   = m.get("conditionId") or m.get("condition_id") or m.get("id")
        market_name = m.get("question") or m.get("title") or "Mercado Desconocido"
        volume      = float(m.get("volume", 0) or 0)

        # clobTokenIds: lista JSON en string o lista directa
        raw_clob_ids = m.get("clobTokenIds") or "[]"
        if isinstance(raw_clob_ids, str):
            try:
                import json as _json
                clob_ids = _json.loads(raw_clob_ids)
            except Exception:
                clob_ids = []
        else:
            clob_ids = raw_clob_ids

        # outcomes: string JSON o lista
        raw_outcomes = m.get("outcomes") or []
        if isinstance(raw_outcomes, str):
            try:
                import json as _json
                raw_outcomes = _json.loads(raw_outcomes)
            except Exception:
                raw_outcomes = ["YES", "NO"]

        for i, token_id in enumerate(clob_ids):
            if not token_id:
                continue
            outcome = raw_outcomes[i] if i < len(raw_outcomes) else ("YES" if i == 0 else "NO")
            market_meta[str(token_id)] = {
                "market_id":   market_id,
                "market_name": market_name,
                "outcome":     outcome,
                "volume":      volume,
            }

    logger.info(f"Cache de metadatos construida: {len(market_meta)} asset_ids mapeados.")


# ── Paso 3: Motor de Puntuación y Detección ───────────────────────────────────
def _calculate_sqs(intent_type: str, avg_price: float, bias: float,
                   wallets: int, impact_score: float, recent_activity: int) -> float:
    """Signal Quality Score: producto de 4 factores independientes (0.0 → 1.0).
    
    - price_factor:        Curva cúbica de incertidumbre. Máximo en precio=0.50, colapsa en extremos.
    - structure_weight:    Peso fijo por tipo de ejecución detectado.
    - concentration_factor: Convicción = direccionalidad del bias × concentración de wallets.
    - impact_factor:       Impacto volumétrico normalizado en escala logarítmica.
    """
    # Factor 1: Incertidumbre del mercado
    # precio=0.50 → 1.0 | precio=0.75 → ~0.44 | precio=0.90 → ~0.07 | precio=0.99 → ~0.0
    price_factor = max(0.0, (1.0 - abs(avg_price - 0.5) * 2.0) ** 3)

    # Factor 2: Calidad estructural del intent
    structure_weights = {
        'institutional_block': 1.00,
        'twap_accumulation':   0.80,
        'algorithmic_split':   0.70,
        'anomalous_impact':    0.55,
        'retail_frenzy':       0.30,
        'wash_trading':        0.10,
        'late_execution':      0.05,
    }
    structure_weight = structure_weights.get(intent_type, 0.20)

    # Factor 3: Convicción del cluster  
    directional_strength = abs(bias - 0.5) * 2.0                      # 0=neutral, 1=unidireccional
    wallet_concentration = 1.0 / (1.0 + math.log(max(1, wallets)))    # decae con más wallets
    concentration_factor = directional_strength * wallet_concentration

    # Factor 4: Impacto volumétrico (log-normalizado, IS=5000 → 1.0)
    impact_factor = min(1.0, math.log(1.0 + impact_score) / math.log(1.0 + 5000.0))

    sqs = price_factor * structure_weight * concentration_factor * impact_factor

    # Bonus: el mercado estaba silencioso antes de la ráfaga → señal más valiosa
    context_multiplier = 1.2 if recent_activity < 5 else 1.0
    return round(min(1.0, sqs * context_multiplier), 4)


def _get_tier(sqs: float) -> str:
    """Jerarquía de prioridad basada en SQS."""
    if sqs >= 0.50:
        return "TIER_1"   # Premium  → Telegram + Supabase + JSON
    elif sqs >= 0.20:
        return "TIER_2"   # Notable  → Supabase + JSON
    else:
        return "TIER_3"   # Descarte → Solo JSON local


def analyze_activity(asset_id: str, new_size_usdc: float, wallet: str, side: str, price: float, market_vol: float) -> dict:
    """Clasifica el cluster de actividad, calcula el SQS y determina el tier de prioridad.
       Retorna el dict del evento con score y tier, o None si es irrelevante.
    """
    now = time.time()
    history = trade_history[asset_id]
    history.append({'ts': now, 'size': new_size_usdc, 'wallet': wallet, 'side': side, 'price': price})

    cutoff = now - 15.0
    while history and history[0]['ts'] < cutoff:
        history.popleft()

    # ─── Ventana micro (3s) ────────────────────────────────────────────────────
    micro_window = [t for t in history if now - t['ts'] <= MICRO_WINDOW_SEC]
    micro_vol    = sum(t['size'] for t in micro_window)
    micro_count  = len(micro_window)
    impact_score = (micro_vol / market_vol) * 100_000 if market_vol > 0 else 0.0

    signal_data = None

    if micro_count >= 2 and micro_vol > 0:
        avg_price = sum(t['price'] * t['size'] for t in micro_window) / micro_vol
        buy_vol   = sum(t['size'] for t in micro_window if t['side'] == 'BUY')
        bias      = buy_vol / micro_vol
        wallets   = len(set(t['wallet'] for t in micro_window))
        # Trades previos en los últimos 60s (fuera de la micro window actual)
        recent_activity = len([t for t in history if MICRO_WINDOW_SEC < now - t['ts'] <= 60])

        # ─── Árbol de clasificación (sin umbrales USD absolutos) ───────────────
        if avg_price >= 0.90:
            intent = 'late_execution'
        elif micro_count <= 3 and (bias >= 0.9 or bias <= 0.1):
            intent = 'institutional_block'
        elif 4 <= micro_count <= 15 and (bias >= 0.8 or bias <= 0.2):
            intent = 'twap_accumulation'
        elif micro_count > 15 and wallets >= 4:
            intent = 'retail_frenzy'
        elif 0.4 <= bias <= 0.6 and impact_score >= 10:
            intent = 'wash_trading'
        elif impact_score >= 500:
            intent = 'anomalous_impact'
        else:
            intent = None

        if intent:
            sqs  = _calculate_sqs(intent, avg_price, bias, wallets, impact_score, recent_activity)
            tier = _get_tier(sqs)
            signal_data = {
                'type': intent, 'agg_vol': micro_vol, 'trades': micro_count,
                'impact_score': impact_score, 'bias': bias,
                'wallets_in_burst': wallets, 'sqs': sqs, 'tier': tier,
            }

    # ─── Detección Algorítmica Pura (10s) ─────────────────────────────────────
    algo_window = [t for t in history if now - t['ts'] <= ALGO_WINDOW_SEC]
    size_freq   = defaultdict(int)
    for t in algo_window:
        size_freq[round(t['size'], 1)] += 1

    for size_bucket, count in size_freq.items():
        if size_bucket >= 100 and count >= 4:
            algo_trades  = [t for t in algo_window if round(t['size'], 1) == size_bucket]
            total_burst  = size_bucket * count
            algo_impact  = (total_burst / market_vol) * 100_000 if market_vol > 0 else 0.0
            algo_buy     = sum(t['size'] for t in algo_trades if t['side'] == 'BUY')
            algo_bias    = algo_buy / total_burst if total_burst > 0 else 0.5
            algo_wallets = len(set(t['wallet'] for t in algo_trades))
            algo_avg_p   = sum(t['price'] * t['size'] for t in algo_trades) / total_burst
            algo_recent  = len([t for t in history if ALGO_WINDOW_SEC < now - t['ts'] <= 60])
            algo_sqs     = _calculate_sqs('algorithmic_split', algo_avg_p, algo_bias, algo_wallets, algo_impact, algo_recent)
            algo_tier    = _get_tier(algo_sqs)
            algo_data    = {
                'type': 'algorithmic_split', 'agg_vol': total_burst, 'trades': count,
                'impact_score': algo_impact, 'bias': algo_bias,
                'wallets_in_burst': algo_wallets, 'sqs': algo_sqs, 'tier': algo_tier,
            }
            # Conservar la señal con mayor SQS
            if signal_data is None or algo_sqs > signal_data['sqs']:
                signal_data = algo_data
            break

    return signal_data


def check_clustering(asset_id: str, outcome: str, wallet: str) -> int:
    """Registra wallet en el tracker de clustering. Retorna nº de wallets únicas en ventana."""
    now     = datetime.now(timezone.utc)
    cutoff  = now - timedelta(minutes=CLUSTER_WINDOW_MIN)

    entries = cluster_tracker[asset_id][outcome]
    # Limpiar entradas viejas
    entries[:] = [(w, t) for w, t in entries if t > cutoff]
    # Añadir la nueva
    if not any(w == wallet for w, _ in entries):
        entries.append((wallet, now))

    return len(set(w for w, _ in entries))


def is_fresh_wallet(wallet: str, tx_count: int) -> bool:
    """Determina si la wallet es 'fresca' (< 10 txs históricas)."""
    return tx_count < 10


def is_on_cooldown(market_id: str) -> bool:
    """Evita enviar la misma alerta dos veces en SIGNAL_COOLDOWN_MIN minutos."""
    last = recent_signals.get(market_id)
    if last and (datetime.now(timezone.utc) - last).total_seconds() < SIGNAL_COOLDOWN_MIN * 60:
        return True
    return False


# ── Paso 4: Procesador de eventos WebSocket ────────────────────────────────────
async def process_trade_event(event: dict):
    """Procesa un evento de trade recibido y decide si disparar señal."""
    asset_id    = event.get("asset_id") or event.get("market")
    trade_size  = float(event.get("size", 0) or 0)
    price       = float(event.get("price", 0) or 0)
    wallet      = event.get("maker_address") or event.get("taker_address") or "unknown"
    side        = event.get("side", "BUY").upper()

    if not asset_id or trade_size <= 0:
        return

    trade_size_usdc = trade_size * price  # aproximación en USDC

    # 1. Filtro Anti-Ruido
    if trade_size_usdc < NOISE_FILTER_USDC:
        return

    meta = market_meta.get(asset_id, {})
    if not meta:
        return  # mercado no está en nuestro scope

    market_id   = meta["market_id"]
    market_name = meta["market_name"]
    outcome     = meta["outcome"]
    market_vol  = meta.get("volume", 0.0)

    # V1.0: Alimentar al WalletTracker para aprendizaje dinámico
    tracker.register_trade(wallet, {
        "token_id": asset_id,
        "size": trade_size,
        "price": price,
        "side": side
    })

    # ── Detección y Puntuación (Signal Quality Score) ──────────────────────────
    event_data = analyze_activity(asset_id, trade_size_usdc, wallet, side, price, market_vol)

    if not event_data:
        return

    sqs              = event_data.get('sqs', 0.0)
    tier             = event_data.get('tier', 'TIER_3')
    signal_type      = event_data['type']
    agg_vol          = event_data['agg_vol']
    trades_count     = event_data['trades']
    impact_score     = event_data['impact_score']
    bias             = event_data.get('bias', 0.5)
    wallets_in_burst = event_data.get('wallets_in_burst', 1)

    # TIER_3 → descartar silenciosamente (solo debug)
    if tier == 'TIER_3':
        logger.debug(f"📋 [{tier}] {signal_type} | SQS={sqs:.3f} | ${agg_vol:,.0f} — descartado")
        return

    # Cooldown solo para TIER_1/TIER_2 (evitar flood de notificaciones)
    if is_on_cooldown(market_id):
        return
    recent_signals[market_id] = datetime.now(timezone.utc)

    wallet_count  = check_clustering(asset_id, outcome, wallet)

    tier_emoji = {"TIER_1": "🏆", "TIER_2": "🥈"}.get(tier, "📋")
    logger.info(
        f"🚨 {tier_emoji} [{tier}] [{signal_type}] | {market_name[:45]} | "
        f"${agg_vol:,.0f} en {trades_count} trades | IS={impact_score:.1f} | SQS={sqs:.3f}"
    )

    # ── Construir señal ────────────────────────────────────────────────────────
    signal = {
        "market_id":       market_id,
        "market_name":     market_name,
        "asset_id":        asset_id,
        "outcome":         outcome,
        "side":            side,
        "trade_size_usdc": agg_vol,
        "price":           price,
        "impact_score":    impact_score,
        "sqs":             sqs,
        "tier":            tier,
        "wallet_address":  wallet,
        "wallet_count":    wallet_count,
        "wallets_in_burst": wallets_in_burst,
        "bias":            bias,
        "signal_type":     signal_type,
        "all_signals":     [signal_type],
        "copy_trade_usdc": float(os.getenv("COPY_TRADE_USDC", "100")),
        "timestamp":       datetime.now(timezone.utc).isoformat(),
        "trades_count":    trades_count,
    }

    # Despachar alerta (Telegram + Supabase + local)
    await dispatch_alert(signal)

    # El copy_trader.py se suscribirá a estas señales a través de una cola
    return signal


# ── Paso 5: Loop WebSocket principal ──────────────────────────────────────────
async def run_scanner(signal_queue: asyncio.Queue):
    """Loop principal. Se reconecta automáticamente si se cae la conexión."""
    markets = await fetch_active_markets()
    if not markets:
        logger.error("❌ No se obtuvieron mercados. Abortando scanner.")
        return

    build_market_meta(markets)
    asset_ids = list(market_meta.keys())
    logger.info(f"🔌 Suscribiéndose a {len(asset_ids)} asset_ids via WebSocket...")

    backoff = 1
    while True:
        try:
            async with websockets.connect(
                CLOB_WS_URL,
                ping_interval=30,
                ping_timeout=10,
                open_timeout=20,
            ) as ws:
                logger.info("✅ WebSocket conectado a Polymarket CLOB.")
                backoff = 1  # reset backoff en conexión exitosa

                subscribe_msg = {
                    "type":       "market",
                    "assets_ids": asset_ids[:500],  # límite por mensaje
                }
                await ws.send(json.dumps(subscribe_msg))

                _debug_count = 0  # contador de mensajes crudos para debug inicial
                async for raw_msg in ws:
                    try:
                        events = json.loads(raw_msg)
                        if not isinstance(events, list):
                            events = [events]

                        # DEBUG: loguear primeros 15 mensajes crudos para confirmar formato
                        if _debug_count < 15:
                            for ev in events:
                                et = ev.get("event_type") or ev.get("type") or "SIN_TIPO"
                                keys = list(ev.keys())
                                logger.info(f"[DEBUG WS #{_debug_count}] type={et} | keys={keys} | raw={str(ev)[:200]}")
                                _debug_count += 1

                        for event in events:
                            et = event.get("event_type") or event.get("type") or ""
                            
                            # Los trades reales en Polymarket vienen anidados bajo price_changes
                            if et == "price_change" and "price_changes" in event:
                                for pc in event["price_changes"]:
                                    mock_trade = pc.copy()
                                    mock_trade["market"] = event.get("market")
                                    signal = await process_trade_event(mock_trade)
                                    if signal:
                                        await signal_queue.put(signal)
                            
                            elif et in ("trade", "TRADE"):
                                signal = await process_trade_event(event)
                                if signal:
                                    await signal_queue.put(signal)
                    except json.JSONDecodeError:
                        pass
                    except Exception as e:
                        logger.warning(f"Error procesando evento: {e}")

        except (websockets.exceptions.ConnectionClosed,
                websockets.exceptions.WebSocketException) as e:
            logger.warning(f"🔄 WebSocket desconectado: {e}. Reconectando en {backoff}s...")
        except Exception as e:
            logger.error(f"❌ Error inesperado en scanner: {e}")

        await asyncio.sleep(backoff)
        backoff = min(backoff * 2, 60)  # exponential backoff máx 60s
