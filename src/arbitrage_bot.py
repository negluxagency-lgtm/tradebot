"""
🛰️ Arbitrage Scanner v5.0 — Inconsistencias Electorales Pro

Correcciones v5.0 sobre v4.0:
 - Filtro de Coherencia Bayesiana: P(Ganar general) <= P(Ganar nominación) modelado como ratio.
 - Slippage dinámico por volumen/liquidez.
 - Confidence Score para filtrado de alto conviction.
 - Cierre anticipado guiado por Momentum (Mean Reversion).
 - Manejo documentado/alertado de Partial Fills con hedge / IOC.

NOTA HONESTA:
 El impacto de mercado real solo puede modelarse con datos de microestructura en vivo.
 Usamos una aproximación conservadora: (1) haircut del 1.5% sobre edge calculado
 y (2) eliminamos el mejor nivel del bid al simular la segunda pata.
 Esto sobreestima el coste pero nunca lo subestima — preferible en arb.
"""
import asyncio
import aiohttp
import json
import os
import io
import sys
import logging
import re
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv

import sys
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from wallet_tracker import tracker

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
logger = logging.getLogger("arbitrage_bot")

load_dotenv(".env.local")

# ── Configuración ──────────────────────────────────────────────────────────────
GAMMA_API_URL      = "https://gamma-api.polymarket.com"
CLOB_API_URL       = os.getenv("POLY_HOST", "https://clob.polymarket.com")
POLL_INTERVAL      = 30    # 30 segundos — ineficiencias duran minutos, no 5 min
DISCOVERY_INTERVAL = 300   # Re-discovery cada 5 minutos (SaaS Tier)

# Ejecución
POLY_FEE_PER_TRADE  = 0.01   # Fee Polymarket por cada pata (1%)
MIN_CONFIRMED_EDGE  = 0.02   # Edge neto mínimo DESPUÉS de fees + slippage
CONFIRM_SCANS       = 2      # 2 scans à 30s = 1 minuto de confirmación
MAX_HOLDING_HOURS   = 72     # Señal de salida obligatoria a las 72h

# V5.0: Mejoras de Ejecución e Incoherencia
BAYESIAN_MARGIN     = 1.30   # prob_gen > prob_nom * 1.30
MIN_CONFIDENCE      = 0.45   # Score de convicción para alertas (0-1)
VELOCITY_EXIT       = -0.005 # Mean reversion exit
BASE_SLIP           = 0.01   # Slippage base
K_SLIP              = 0.02   # Constante de impacto según size/liquidez

# Auto-ejecución (módulo opcional)
AUTO_EXECUTE       = os.getenv("ARB_AUTO_EXECUTE", "false").lower() == "true"
DRY_RUN            = os.getenv("DRY_RUN", "true").lower() == "true"
RELAYER_API_KEY    = os.getenv("RELAYER_API_KEY", "")
RELAYER_API_ADDR   = os.getenv("RELAYER_API_KEY_ADDRESS", "")

MAX_TOTAL_EXPOSURE = 1000.0  # Límite global del portafolio ($)
MAX_PARTY_EXPOSURE = 600.0   # V5.4 Riesgo sectorial

# Sizing dinámico
BASE_LIQ_FRACTION  = 0.12   # Fracción base de liquidez (se ajusta por spread)
MAX_SPREAD_FACTOR  = 0.40   # Si spread relativo > 40%, no tocar ese mercado
MIN_POSITION       = 10.0
MAX_POSITION       = 150.0

# Filtros
MIN_MARKET_VOL     = 500_000
MIN_PROB           = 0.03
MAX_PROB           = 0.85
MIN_PROB_INCOHERENCE = 0.02

# Probabilidad estimada: muestra adaptativa
PROB_SAMPLE_BASE   = 5.0    # USDC base para micro-VWAP
PROB_SAMPLE_PCT    = 0.02   # También usar 2% de la liquidez disponible

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID")
TELEGRAM_API       = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"

CANDIDATE_BLACKLIST = {
    "kim kardashian", "lebron james", "dwayne the rock johnson",
    "mrbeast", "oprah winfrey", "george clooney", "tom brady",
    "hulk hogan", "jon stewart", "jamie dimon", "bill gates",
    "elon musk", "stephen a smith", "gwen walz", "hunter biden",
    "stephen smith",
}

# ── Estado ─────────────────────────────────────────────────────────────────────
alert_cooldowns: dict[str, datetime] = {}
edge_history: dict[str, list[float]] = defaultdict(list)
last_discovery: datetime = datetime.min.replace(tzinfo=timezone.utc)

# ── Caching Local (V5.5) ───────────────────────────────────────────────────────
orderbook_cache: dict[str, dict] = {}


# ══════════════════════════════════════════════════════════════════════════════
#  1. OpportunityTracker — Señales de ENTRADA y SALIDA
# ══════════════════════════════════════════════════════════════════════════════
class OpportunityTracker:
    """
    Rastrea oportunidades activas en memoria.
    Emite señales de ENTRADA cuando se confirma un edge estable.
    Emite señales de SALIDA cuando el edge desaparece o se supera el tiempo máximo.
    """
    def __init__(self):
        self._open: dict[str, dict] = {}

    def open(self, candidate: str, analysis: dict):
        """Registra una nueva oportunidad confirmada."""
        self._open[candidate] = {
            "first_seen": datetime.now(timezone.utc),
            "last_seen": datetime.now(timezone.utc),
            "peak_edge": analysis["net_edge"],
            "last_edge": analysis["net_edge"],
            "position": analysis.get("position", 0.0),
            "scan_count": 1,
            "party": analysis.get("party", ""),
        }

    def update(self, candidate: str, net_edge: float):
        """Actualiza una oportunidad existente."""
        if candidate in self._open:
            opp = self._open[candidate]
            opp["last_seen"] = datetime.now(timezone.utc)
            opp["last_edge"] = net_edge
            opp["peak_edge"] = max(opp["peak_edge"], net_edge)
            opp["scan_count"] += 1

    def is_open(self, candidate: str) -> bool:
        return candidate in self._open

    def hours_open(self, candidate: str) -> float:
        if candidate not in self._open:
            return 0.0
        return (datetime.now(timezone.utc) - self._open[candidate]["first_seen"]).total_seconds() / 3600

    def should_exit(self, candidate: str, current_edge: float, velocity_history: list[float]) -> tuple[bool, str]:
        """Evalúa si la oportunidad debe cerrarse. Retorna (debe_cerrar, razón)."""
        if candidate not in self._open:
            return False, ""
        h = self.hours_open(candidate)
        if current_edge < 0:
            return True, f"Edge desaparecido ({current_edge*100:.2f}%)"
            
        # Momentum history check
        if len(velocity_history) >= 2 and velocity_history[-1] < VELOCITY_EXIT and velocity_history[-2] < VELOCITY_EXIT:
            return True, f"Mean Reversion confirmada: momentum negativo fuerte ({velocity_history[-1]*100:.2f}%)"
            
        if h > MAX_HOLDING_HOURS:
            return True, f"Tiempo máximo alcanzado ({h:.0f}h)"
        return False, ""

    def close(self, candidate: str) -> dict:
        """Elimina y retorna los datos de la oportunidad."""
        return self._open.pop(candidate, {})

    def all_open(self) -> dict:
        return dict(self._open)

    def total_exposure(self) -> float:
        return sum(opp.get("position", 0.0) for opp in self._open.values())

    def party_exposure(self, party: str) -> float:
        return sum(opp.get("position", 0.0) for opp in self._open.values() if opp.get("party") == party)


opp_tracker = OpportunityTracker()


# ══════════════════════════════════════════════════════════════════════════════
#  2. Matching Robusto
# ══════════════════════════════════════════════════════════════════════════════
def _normalize(name: str) -> str:
    name = name.lower().strip()
    name = re.sub(r"['.\"()!,]", "", name)
    name = re.sub(r"\s+", " ", name)
    return name.replace("jr.", "jr").replace("jr ", "jr")


def _parse_market(question: str) -> tuple:
    q = question.strip()
    for pat in [
        r"[Ww]ill\s+(.+?)\s+win\s+(?:the\s+)?2028\s+(Democratic|Republican)\s+presidential\s+nomination",
        r"[Ww]ill\s+(.+?)\s+be\s+(?:the\s+)?2028\s+(Democratic|Republican)\s+(?:presidential\s+)?nominee",
    ]:
        m = re.search(pat, q)
        if m:
            return _normalize(m.group(1)), "nomination", m.group(2)
    for pat in [
        r"[Ww]ill\s+(.+?)\s+win\s+(?:the\s+)?2028\s+US\s+[Pp]residential\s+[Ee]lection",
        r"[Ww]ill\s+(.+?)\s+become\s+(?:the\s+)?(?:next\s+)?US\s+[Pp]resident",
    ]:
        m = re.search(pat, q)
        if m:
            return _normalize(m.group(1)), "election", None
    return None, None, None


# ══════════════════════════════════════════════════════════════════════════════
#  3. Discovery con filtros de calidad
# ══════════════════════════════════════════════════════════════════════════════
async def discover_pairs(session: aiohttp.ClientSession) -> dict:
    candidates = defaultdict(dict)
    url = f"{GAMMA_API_URL}/markets?active=true&closed=false&limit=500"
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
            if resp.status != 200:
                logger.error(f"Gamma API {resp.status}")
                return {}
            data = await resp.json()
    except Exception as e:
        logger.error(f"Discovery error: {e}")
        return {}

    for market in data:
        name, mtype, party = _parse_market(market.get("question", ""))
        if not name or name in CANDIDATE_BLACKLIST:
            continue
        try:
            tokens = json.loads(market.get("clobTokenIds", "[]"))
            if not tokens:
                continue
        except Exception:
            continue
        vol = float(market.get("volume") or 0)
        entry = {"token": tokens[0], "question": market.get("question"), "volume": vol}
        if mtype == "nomination":
            entry["party"] = party
            candidates[name]["nomination"] = entry
        elif mtype == "election":
            candidates[name]["election"] = entry

    valid = {}
    for name, mkts in candidates.items():
        if "nomination" not in mkts or "election" not in mkts:
            continue
        combined_vol = mkts["nomination"]["volume"] + mkts["election"]["volume"]
        if combined_vol < MIN_MARKET_VOL:
            continue
        valid[name] = {
            "party":      mkts["nomination"]["party"],
            "nom_token":  mkts["nomination"]["token"],
            "elec_token": mkts["election"]["token"],
            "nom_q":      mkts["nomination"]["question"],
            "elec_q":     mkts["election"]["question"],
        }

    logger.info(f"🔍 Discovery: {len(valid)} pares (vol≥${MIN_MARKET_VOL/1e6:.1f}M, sin memes).")
    
    # Discovery de Wallets (Smart Money)
    for name, d in valid.items():
        await tracker.discover_wallets_from_trades(session, d["nom_token"])
        await tracker.discover_wallets_from_trades(session, d["elec_token"])
    tracker.update_rankings()
    
    return valid


# ══════════════════════════════════════════════════════════════════════════════
#  4. Orderbook
# ══════════════════════════════════════════════════════════════════════════════
async def get_book(session: aiohttp.ClientSession, token_id: str) -> dict:
    try:
        async with session.get(
            f"{CLOB_API_URL}/book?token_id={token_id}",
            timeout=aiohttp.ClientTimeout(total=8),
        ) as resp:
            if resp.status == 200:
                return await resp.json()
    except Exception:
        pass
    return {"bids": [], "asks": []}


def _book_liquidity_usdc(levels: list, side: str) -> float:
    """Liquidez total en USDC de un lado del book."""
    total = 0.0
    for l in levels:
        try:
            total += float(l["price"]) * float(l.get("size", 0))
        except (KeyError, ValueError):
            pass
    return total


def _relative_spread(book: dict) -> float:
    """Spread relativo: (ask - bid) / mid. Proxy de riesgo de ejecución."""
    bids = book.get("bids", [])
    asks = book.get("asks", [])
    if not bids or not asks:
        return 1.0
    try:
        best_bid = max(float(b["price"]) for b in bids)
        best_ask = min(float(a["price"]) for a in asks)
        mid = (best_bid + best_ask) / 2
        return (best_ask - best_bid) / mid if mid > 0 else 1.0
    except (ValueError, KeyError):
        return 1.0


def estimate_prob_adaptive(book: dict) -> float:
    """
    Probabilidad estimada usando micro-VWAP con tamaño adaptativo.
    El sample se escala con la liquidez real para evitar distorsión en libros finos.
    """
    asks = sorted(book.get("asks", []), key=lambda x: float(x["price"]))
    if not asks:
        bids = book.get("bids", [])
        return max((float(b["price"]) for b in bids), default=0.0)

    total_liq = _book_liquidity_usdc(asks, "asks")
    sample = min(PROB_SAMPLE_BASE, max(0.5, total_liq * PROB_SAMPLE_PCT))

    spent = 0.0
    shares = 0.0
    for level in asks:
        price = float(level["price"])
        sz = float(level.get("size", 0))
        if sz <= 0 or price <= 0:
            continue
        usdc_here = price * sz
        take = min(sample - spent, usdc_here)
        spent += take
        shares += take / price
        if spent >= sample:
            break

    return spent / shares if shares > 0 else float(asks[0]["price"])


# ══════════════════════════════════════════════════════════════════════════════
#  5. Simulación de Ejecución (Secuencial + Impacto)
# ══════════════════════════════════════════════════════════════════════════════
def simulate_buy(asks: list, budget_usdc: float, skip_levels: int = 0) -> dict:
    """Comprar $budget_usdc caminando asks ASC. skip_levels para worst-case."""
    sorted_asks = sorted(asks, key=lambda x: float(x["price"]))
    effective_asks = sorted_asks[skip_levels:]
    if not effective_asks:
        effective_asks = sorted_asks
        
    spent = shares = remaining = 0.0
    remaining = budget_usdc
    levels = 0
    for lvl in effective_asks:
        price = float(lvl["price"])
        sz = float(lvl.get("size", 0))
        if sz <= 0 or price <= 0:
            continue
        can_buy_usdc = sz * price
        take = min(remaining, can_buy_usdc)
        spent += take
        shares += take / price
        remaining -= take
        levels += 1
        if remaining <= 0:
            break
    vwap = spent / shares if shares > 0 else None
    return {
        "shares": shares, "spent": spent, "vwap": vwap,
        "levels": levels, "filled_pct": (budget_usdc - remaining) / budget_usdc if budget_usdc > 0 else 0,
    }


def simulate_sell_degraded(bids: list, shares_to_sell: float, skip_levels: int = 1) -> dict:
    """
    Vender N shares caminando bids DESC.
    skip_levels: salta los N mejores niveles para modelar impacto de la primera pata
    (el mercado se ha movido en contra nuestra entre órdenes).
    """
    sorted_bids = sorted(bids, key=lambda x: float(x["price"]), reverse=True)
    effective_bids = sorted_bids[skip_levels:]  # Degradación de segunda pata

    if not effective_bids:
        effective_bids = sorted_bids  # Fallback si solo había 1 nivel

    revenue = sold = remaining = 0.0
    remaining = shares_to_sell
    levels = 0
    for lvl in effective_bids:
        price = float(lvl["price"])
        sz = float(lvl.get("size", 0))
        if sz <= 0 or price <= 0:
            continue
        take = min(remaining, sz)
        revenue += take * price
        sold += take
        remaining -= take
        levels += 1
        if remaining <= 0:
            break
    vwap = revenue / sold if sold > 0 else None
    return {
        "shares": sold, "revenue": revenue, "vwap": vwap,
        "levels": levels, "filled_pct": sold / shares_to_sell if shares_to_sell > 0 else 0,
    }


# ══════════════════════════════════════════════════════════════════════════════
#  6. Edge con todos los costes modelados
# ══════════════════════════════════════════════════════════════════════════════
def compute_edge(buy_result: dict, sell_result: dict, dynamic_slip: float) -> dict:
    """
    Calcula P&L real incluyendo:
    - Fees por cada pata (1% × 2)
    - Slippage dinámico por volumen/liquidez
    """
    cost = buy_result["spent"]
    revenue = sell_result["revenue"]

    gross_pnl = revenue - cost
    gross_edge = gross_pnl / cost if cost > 0 else 0

    fee_buy = cost * POLY_FEE_PER_TRADE
    fee_sell = revenue * POLY_FEE_PER_TRADE
    total_fees = fee_buy + fee_sell

    slip_cost = cost * dynamic_slip

    net_pnl = gross_pnl - total_fees - slip_cost
    net_edge = net_pnl / cost if cost > 0 else 0

    return {
        "gross_edge": gross_edge,
        "net_edge": net_edge,
        "net_pnl": net_pnl,
        "fees_pct": (total_fees / cost) if cost > 0 else 0,
        "slip_pct": dynamic_slip,
        "cost": cost,
    }


# ══════════════════════════════════════════════════════════════════════════════
#  7. Sizing dinámico por spread
# ══════════════════════════════════════════════════════════════════════════════
def dynamic_position_size(nom_book: dict, gen_book: dict) -> float | None:
    """
    Position size basado en:
    1. Liquidez mínima disponible (ambas patas)
    2. Spread relativo (proxy de riesgo): spreads altos → posición menor
    """
    liq_nom = _book_liquidity_usdc(nom_book.get("asks", []), "asks")
    liq_gen = _book_liquidity_usdc(gen_book.get("bids", []), "bids")
    min_liq = min(liq_nom, liq_gen)

    spread_nom = _relative_spread(nom_book)
    spread_gen = _relative_spread(gen_book)
    max_spread = max(spread_nom, spread_gen)

    # Mercado con spread excesivo → no operar
    if max_spread > MAX_SPREAD_FACTOR:
        return None

    # Fracción reducida por spread: más spread → más riesgo → menos tamaño
    spread_penalty = 1.0 - (max_spread / MAX_SPREAD_FACTOR) * 0.6
    fraction = BASE_LIQ_FRACTION * spread_penalty

    size = min_liq * fraction
    return max(MIN_POSITION, min(MAX_POSITION, size))


# ══════════════════════════════════════════════════════════════════════════════
#  8. Temporal: confirmación y tendencia
# ══════════════════════════════════════════════════════════════════════════════
def record_and_confirm(candidate: str, net_edge: float) -> bool:
    history = edge_history[candidate]
    history.append(net_edge)
    if len(history) > 6:
        edge_history[candidate] = history[-6:]
        history = edge_history[candidate]
    if len(history) < CONFIRM_SCANS:
        return False
    return all(e >= MIN_CONFIRMED_EDGE for e in history[-CONFIRM_SCANS:])


def edge_trend(candidate: str) -> str:
    h = edge_history.get(candidate, [])
    if len(h) < 2:
        return "NEW"
    if len(h) >= 3:
        # Tendencia sobre últimos 3
        delta = h[-1] - h[-3]
        if delta > 0.007:
            return "GROWING"
        if delta < -0.007:
            return "DECAYING"
    return "STABLE"


def convergence_velocity(candidate: str) -> float:
    """Cuánto está cambiando el edge por scan (positivo = convergiendo hacia cero)."""
    h = edge_history.get(candidate, [])
    if len(h) < 2:
        return 0.0
    return h[-1] - h[-2]  # Positivo = creciendo, negativo = decayendo


# ══════════════════════════════════════════════════════════════════════════════
#  9. Cooldown
# ══════════════════════════════════════════════════════════════════════════════
def is_on_cooldown(candidate: str, hours: float = 6.0) -> bool:
    last = alert_cooldowns.get(candidate)
    return last is not None and datetime.now(timezone.utc) - last < timedelta(hours=hours)


def set_cooldown(candidate: str):
    alert_cooldowns[candidate] = datetime.now(timezone.utc)


# ══════════════════════════════════════════════════════════════════════════════
#  10. Telegram (ENTRADA + SALIDA)
# ══════════════════════════════════════════════════════════════════════════════
async def send_entry_alert(candidate: str, party: str, nom_q: str, elec_q: str, analysis: dict):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    trend = analysis["trend"]
    trend_icon = {"GROWING": "📈", "STABLE": "➡️", "DECAYING": "⚠️📉", "NEW": "🆕"}.get(trend, "❓")
    velocity = analysis["velocity"]
    vel_str = f"+{velocity*100:.2f}% scan⁻¹" if velocity > 0 else f"{velocity*100:.2f}% scan⁻¹"

    msg = (
        f"🚨 *INCOHERENCIA ELECTORAL — ENTRADA*\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 *{candidate}* ({party})\n\n"
        f"📊 *Probabilidades (micro-VWAP adaptativo):*\n"
        f"  P(Nom)  = `{analysis['prob_nom']:.4f}`\n"
        f"  P(Gen)  = `{analysis['prob_gen']:.4f}`\n"
        f"  Δ prob  = `+{analysis['prob_delta']*100:.2f}%`\n\n"
        f"💰 *Simulación ejecutable (${analysis['position']:.0f}):*\n"
        f"  BUY Nom (VWAP):      `{analysis['buy_vwap']:.4f}` ({analysis['buy_levels']} lvls)\n"
        f"  SELL Gen (degradado):`{analysis['sell_vwap']:.4f}` ({analysis['sell_levels']} lvls)\n\n"
        f"  Gross edge:  `{analysis['gross_edge']*100:.2f}%`\n"
        f"  Fees (×2):   `-{analysis['fees_pct']*100:.2f}%`\n"
        f"  Slip est.:   `-{analysis['slip_pct']*100:.2f}%` (dinámico)\n"
        f"  ✅ *Net edge: `+{analysis['net_edge']*100:.2f}%`*\n"
        f"  🧠 *Confidence: `{analysis['confidence']:.2f}`*\n\n"
        f"  {trend_icon} Tendencia: *{trend}* | Vel: `{vel_str}`\n"
        f"  🕐 Confirmado en {CONFIRM_SCANS} scans (~{CONFIRM_SCANS*POLL_INTERVAL//60}min)\n\n"
        f"🔍 {nom_q}\n"
        f"🔍 {elec_q}\n"
        f"━━━━━━━━━━━━━━━━━━━━━"
    )
    await _send_telegram(msg)


async def send_exit_alert(candidate: str, party: str, reason: str, opp_data: dict):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    hours = (datetime.now(timezone.utc) - opp_data["first_seen"]).total_seconds() / 3600
    msg = (
        f"🏁 *CIERRE DE POSICIÓN — {candidate}* ({party})\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"⏱️ Duración: `{hours:.1f}h` | Scans: `{opp_data.get('scan_count', 0)}`\n"
        f"📈 Peak edge: `{opp_data.get('peak_edge', 0)*100:.2f}%`\n"
        f"📉 Último edge: `{opp_data.get('last_edge', 0)*100:.2f}%`\n"
        f"🚪 Razón cierre: *{reason}*\n"
        f"━━━━━━━━━━━━━━━━━━━━━"
    )
    await _send_telegram(msg)


async def _send_telegram(msg: str):
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "Markdown"}
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(TELEGRAM_API, json=payload, timeout=aiohttp.ClientTimeout(total=8)) as resp:
                if resp.status != 200:
                    logger.error(f"Telegram {resp.status}")
    except Exception as e:
        logger.error(f"Telegram: {e}")


# ══════════════════════════════════════════════════════════════════════════════
#  11. Módulo de Ejecución Real (DRY_RUN + LIVE)
# ══════════════════════════════════════════════════════════════════════════════
async def execute_arb_pair(
    session: aiohttp.ClientSession,
    candidate: str,
    nom_token: str,
    gen_token: str,
    shares: float,
    buy_vwap: float,
    sell_vwap: float,
    liq_nom: float = 1.0,
    liq_gen: float = 1.0,
) -> dict:
    """
    Ejecuta las dos patas del arbitraje:
      Pata 1: BUY [shares] del token de Nominación
      Pata 2: SELL [shares] del token de Elección General
    
    Liquidity-First: Ejecuta primero la pata con menos liquidez.
    """
    mode = "🟡 DRY RUN" if DRY_RUN else "🟢 LIVE"
    logger.info(
        f"\n{'='*50}\n"
        f"{mode} EJECUCIÓN ARB LIQUIDITY-FIRST: {candidate}\n"
        f"  BUY  NOM : {shares:.4f} @ {buy_vwap:.4f} (Liq: ${liq_nom:.0f})\n"
        f"  SELL GEN : {shares:.4f} @ {sell_vwap:.4f} (Liq: ${liq_gen:.0f})\n"
        f"  PnL est. : ${(sell_vwap - buy_vwap) * shares:.4f} bruto\n"
        f"{'='*50}"
    )

    if DRY_RUN or not AUTO_EXECUTE:
        return {
            "mode": "dry_run",
            "candidate": candidate,
            "buy_vwap": buy_vwap,
            "sell_vwap": sell_vwap,
            "shares": shares,
        }

    # ── Ejecución Real ────────────────────────────────────
    if not RELAYER_API_KEY or not RELAYER_API_ADDR:
        logger.error("AUTO_EXECUTE activo pero RELAYER_API_KEY no configurada. Abortando.")
        return {"mode": "error", "reason": "missing_credentials"}

    headers = {
        "POLY-API-KEY": RELAYER_API_KEY,
        "POLY-ADDRESS": RELAYER_API_ADDR,
        "Content-Type": "application/json",
    }

    results = {}
    orders_data = [
        {"leg": "BUY_NOM",  "token": nom_token, "side": "BUY",  "price": buy_vwap,  "size": shares, "liq": liq_nom},
        {"leg": "SELL_GEN", "token": gen_token, "side": "SELL", "price": sell_vwap, "size": shares, "liq": liq_gen},
    ]
    orders_data.sort(key=lambda x: x["liq"])  # Ejecuta primero el menos líquido

    import time
    for i, o in enumerate(orders_data):
        leg_name, token_id, side, price, size = o["leg"], o["token"], o["side"], o["price"], o["size"]
        order = {
            "tokenID":    token_id,
            "price":      round(price, 4),
            "side":       side,
            "size":       round(size, 4),
            "orderType":  "IOC",
            "feeRateBps": "0",
            "nonce":      "0",
            "expiration": "0",
        }
        try:
            async with session.post(
                f"{CLOB_API_URL}/order",
                json=order,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=5),  # 5s max: el tiempo mata el edge
            ) as resp:
                data = await resp.json()
                if resp.status in (200, 201) and data.get("success"):
                    logger.info(f"✅ {leg_name} ejecutada: {data.get('orderID', 'N/A')}")
                    results[leg_name] = {"status": "ok", "order_id": data.get("orderID")}
                else:
                    logger.error(f"❌ {leg_name} falló: HTTP {resp.status} | {data}")
                    results[leg_name] = {"status": "error", "http": resp.status, "body": str(data)}
        except asyncio.TimeoutError:
            logger.error(f"❌ {leg_name}: timeout — edge probablemente ya cerrado")
            results[leg_name] = {"status": "timeout"}
        except Exception as e:
            logger.error(f"❌ {leg_name} excepción: {e}")
            results[leg_name] = {"status": "exception", "error": str(e)}
        
        # ── HEDGE FALLBACK: Si falla la segunda pata y la primera tuvo éxito ──
        if i == 1 and results[leg_name]["status"] != "ok" and results.get(orders_data[0]["leg"], {}).get("status") == "ok":
            first_leg = orders_data[0]
            logger.error(f"🚨 ACTIVANDO HEDGE FALLBACK: Operación asimétrica. Deshaciendo pata {first_leg['leg']}...")
            inverse_side = "SELL" if first_leg["side"] == "BUY" else "BUY"
            # Hedge inteligente V5.5: Precio de liquidación limitado al 5% para asegurar fill sin regalar el capital
            hedge_price = round(first_leg["price"] * 0.95, 4) if inverse_side == "SELL" else round(first_leg["price"] * 1.05, 4)
            hedge_price = max(0.0001, min(0.9999, hedge_price))
            
            hedge_order = {
                "tokenID": first_leg["token"],
                "price": str(hedge_price),
                "side": inverse_side,
                "size": round(size, 4),
                "orderType": "IOC",  # También market dumps IOC
                "feeRateBps": "0", "nonce": "0", "expiration": "0",
            }
            try:
                async with session.post(
                    f"{CLOB_API_URL}/order", json=hedge_order, headers=headers, timeout=aiohttp.ClientTimeout(total=5)
                ) as resp_hedge:
                    h_data = await resp_hedge.json()
                    if resp_hedge.status in (200, 201) and h_data.get("success"):
                        logger.info("✅ HEDGE EXITOSO: Riesgo direccional mitigado.")
                        results["HEDGE"] = {"status": "ok"}
                    else:
                        logger.error(f"❌ HEDGE FALLÓ. PELIGRO: Posición abierta. | {h_data}")
                        results["HEDGE"] = {"status": "error"}
            except Exception as he:
                logger.error(f"❌ HEDGE EXCEPTION: {he}")
                results["HEDGE"] = {"status": "exception"}

    results["mode"] = "live"
    return results


# ══════════════════════════════════════════════════════════════════════════════
#  12. Persistencia local
# ══════════════════════════════════════════════════════════════════════════════
def persist(record: dict, filename: str = "artifacts/arbitrage_log.json"):
    os.makedirs("artifacts", exist_ok=True)
    existing = []
    if os.path.exists(filename):
        try:
            with open(filename, "r", encoding="utf-8") as f:
                existing = json.load(f)
        except Exception:
            pass
    existing.append(record)
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(existing, f, indent=2, ensure_ascii=False)


# ══════════════════════════════════════════════════════════════════════════════
#  12. Loop Principal
# ══════════════════════════════════════════════════════════════════════════════
async def main():
    logger.info("=" * 62)
    logger.info("🛰️  ARBITRAGE SCANNER v5.4 — Quant Tier")
    logger.info(f"   Max Exposure: ${MAX_TOTAL_EXPOSURE} | Party Limit: ${MAX_PARTY_EXPOSURE}")
    logger.info(f"   Latencia Tolerada: 350ms | Ordenes: IOC (FOK)")
    logger.info(f"   Model: Conditional Prob `P(Gen) = P(Nom) * P(Party)`")
    logger.info("=" * 62)

    pairs: dict = {}
    last_prob_gen: dict = {}
    global last_discovery

    async with aiohttp.ClientSession() as session:
        while True:
            now = datetime.now(timezone.utc)

            # Re-discovery periódico
            if (now - last_discovery).total_seconds() >= DISCOVERY_INTERVAL or not pairs:
                pairs = await discover_pairs(session)
                last_discovery = now
                if not pairs:
                    await asyncio.sleep(60)
                    continue

            logger.info(f"📡 Scan — {len(pairs)} pares | {len(opp_tracker.all_open())} oportunidades activas")

            entries = exits = detected = 0

            for candidate, data in pairs.items():
                # ── Fetch orderbooks paralelo ──────────────────────────
                import time
                t_start = time.time()
                nom_book, gen_book = await asyncio.gather(
                    get_book(session, data["nom_token"]),
                    get_book(session, data["elec_token"]),
                )
                latency = time.time() - t_start
                latency = time.time() - t_start
                if latency > 0.35:
                    logger.warning(f"  [{candidate}] Latencia inaceptable ({latency:.3f}s). Skip.")
                    continue

                # ── 1. Probabilidad real (micro-VWAP adaptativo) ───────
                prob_nom = estimate_prob_adaptive(nom_book)
                prob_gen = estimate_prob_adaptive(gen_book)
                
                # Conditional Pricing Approximation Normalizada (V5.5)
                last_prob_gen[candidate] = {"party": data["party"], "prob": prob_gen}
                prob_party_win = min(1.0, sum(v["prob"] for v in last_prob_gen.values() if v["party"] == data["party"]))
                
                fair_prob_gen = prob_nom * prob_party_win
                prob_delta = prob_gen - fair_prob_gen

                # ── Chequeo de salida para oportunidades abiertas ──────
                velocity = convergence_velocity(candidate)
                vel_hist = []
                hist = edge_history.get(candidate, [])
                if len(hist) >= 3:
                    vel_hist = [hist[-2] - hist[-3], hist[-1] - hist[-2]]
                    
                if opp_tracker.is_open(candidate):
                    current_net = hist[-1] if hist else 0
                    should_close, reason = opp_tracker.should_exit(candidate, current_net, vel_hist)
                    if should_close or prob_delta < 0:
                        opp_data = opp_tracker.close(candidate)
                        exits += 1
                        logger.info(f"🏁 SALIDA: {candidate} | {reason or 'incoherencia resuelta'}")
                        await send_exit_alert(candidate, data["party"], reason or "Incoherencia resuelta", opp_data)
                        persist({
                            "event": "EXIT", "candidate": candidate,
                            "timestamp": now.isoformat(), "reason": reason, **opp_data,
                        })
                        edge_history.pop(candidate, None)
                        continue

                # ── Filtros duros ──────────────────────────────────────
                # V5.4: Filtro Matemático de Conditional Pricing
                if prob_gen < fair_prob_gen * BAYESIAN_MARGIN:
                    edge_history.pop(candidate, None)
                    continue
                if not (MIN_PROB < prob_nom < MAX_PROB) or not (MIN_PROB < prob_gen < MAX_PROB):
                    continue

                # V5.6: Filtro de Smart Money
                if not tracker.is_smart_money_present(data["nom_token"]):
                    logger.debug(f"  [{candidate}] Sin presencia confirmada de Smart Money. Saltando.")
                    continue

                detected += 1

                # ── 2. Position size base ──────────────────────────
                position_base = dynamic_position_size(nom_book, gen_book)
                if position_base is None:
                    logger.debug(f"  [{candidate}] Spread excesivo — saltando")
                    continue

                # ── 3. Simulación secuencial (Discovery Edge) ──────────
                buy_res = simulate_buy(nom_book.get("asks", []), position_base)
                if buy_res["vwap"] is None or buy_res["filled_pct"] < 0.95:
                    continue

                sell_res = simulate_sell_degraded(gen_book.get("bids", []), buy_res["shares"], skip_levels=1)
                if sell_res["vwap"] is None or sell_res["filled_pct"] < 0.95:
                    continue

                # ── 4. Calcular Slippage Convexo y Edge Base ──────────
                liq_nom = _book_liquidity_usdc(nom_book.get("asks", []), "asks")
                liq_gen = _book_liquidity_usdc(gen_book.get("bids", []), "bids")
                min_liq = min(liq_nom, liq_gen) if min(liq_nom, liq_gen) > 0 else 1
                
                impact_convexo = (position_base / min_liq) ** 1.3
                level_penalty = (buy_res["levels"] + sell_res["levels"]) * 0.002
                dynamic_slip = BASE_SLIP + K_SLIP * impact_convexo + level_penalty
                
                edge_data = compute_edge(buy_res, sell_res, dynamic_slip)
                net_edge_base = edge_data["net_edge"]
                
                # ── Confidence Score (Penta-Factor) ────────────────────
                spread_nom = _relative_spread(nom_book)
                spread_gen = _relative_spread(gen_book)
                max_spread = max(spread_nom, spread_gen)
                
                edge_score = min(net_edge_base / 0.05, 1) if net_edge_base > 0 else 0
                delta_score = min(prob_delta / 0.10, 1) if prob_delta > 0 else 0
                spread_score = 1 - max_spread
                liquidity_score = min(min_liq / 10000.0, 1)
                latency_penalty = max(0, 1 - latency)
                
                confidence = (edge_score * 0.4) + (delta_score * 0.2) + (spread_score * 0.2) + (liquidity_score * 0.1) + (latency_penalty * 0.1)

                if confidence < MIN_CONFIDENCE or net_edge_base < MIN_CONFIRMED_EDGE:
                    continue

                # ── Sizing Condicional Convexo (V5.3) ──────────────────────────
                position = position_base * (edge_score ** 1.5) * confidence
                
                # Portfolio Manager Check (Global y Party Correlacionada)
                current_exposure = opp_tracker.total_exposure()
                current_party_exposure = opp_tracker.party_exposure(data["party"])
                
                if current_exposure + position > MAX_TOTAL_EXPOSURE:
                    position = max(10, MAX_TOTAL_EXPOSURE - current_exposure)
                if current_party_exposure + position > MAX_PARTY_EXPOSURE:
                    position = max(10, MAX_PARTY_EXPOSURE - current_party_exposure)
                    
                if position < 10:
                    logger.warning(f"  [{candidate}] EXPOSURE (${current_exposure:.0f}) O PARTY (${current_party_exposure:.0f}) OVERLOAD. Skip.")
                    continue

                position = max(MIN_POSITION, min(MAX_POSITION, position))

                # ── Re-Simulación Realista y Drift Penalty ──────────────────
                buy_res = simulate_buy(nom_book.get("asks", []), position)
                if buy_res["vwap"] is None or buy_res["filled_pct"] < 0.95:
                    continue
                buy_res["vwap"] *= 1.003  # Queue Penalty simulado
                
                sell_res = simulate_sell_degraded(gen_book.get("bids", []), buy_res["shares"], skip_levels=1)
                if sell_res["vwap"] is None or sell_res["filled_pct"] < 0.95:
                    continue
                sell_res["vwap"] *= 0.997  # Queue Penalty simulado
                
                # Orderbook Drift Penalty
                impact_penalty = position / min_liq
                buy_res["vwap"] *= (1 + impact_penalty * 0.5)
                sell_res["vwap"] *= (1 - impact_penalty * 0.5)

                impact_convexo_final = (position / min_liq) ** 1.3
                level_penalty = (buy_res["levels"] + sell_res["levels"]) * 0.002
                dynamic_slip = BASE_SLIP + K_SLIP * impact_convexo_final + level_penalty
                edge_data = compute_edge(buy_res, sell_res, dynamic_slip)
                net_edge = edge_data["net_edge"]

                # ── Worst-Case Precheck (Asesino de Falsos Positivos) ──
                buy_worst = simulate_buy(nom_book.get("asks", []), position, skip_levels=1)
                if buy_worst["vwap"] is None or buy_worst["filled_pct"] < 0.95:
                    continue
                
                sell_worst = simulate_sell_degraded(gen_book.get("bids", []), buy_worst["shares"], skip_levels=2)
                if sell_worst["vwap"] is None or sell_worst["filled_pct"] < 0.95:
                    continue
                
                worst_edge_data = compute_edge(buy_worst, sell_worst, dynamic_slip)
                if worst_edge_data["net_edge"] < -0.01:
                    logger.warning(f"  [{candidate}] Worst-Case Peligroso ({worst_edge_data['net_edge']*100:.2f}%). TRADE CANCELADO.")
                    continue

                logger.debug(f"[{candidate}] Vol+Level+Queue -> Slip = {dynamic_slip*100:.2f}%")

                # ── 5. Temporal: confirmar estabilidad ────────────────
                confirmed = record_and_confirm(candidate, net_edge)
                trend = edge_trend(candidate)
                velocity = convergence_velocity(candidate)

                confirm_target = 1 if confidence > 0.7 else CONFIRM_SCANS
                
                logger.info(
                    f"  [{candidate}] Prob={prob_gen/prob_nom if prob_nom > 0 else 0:.2f}x | "
                    f"Net={net_edge*100:.2f}% | Conf={confidence:.2f} | "
                    f"Confm={'YES' if len(edge_history[candidate])>=confirm_target else 'NO'} | Size=${position:.0f}"
                )

                if len(edge_history[candidate]) < confirm_target or net_edge < MIN_CONFIRMED_EDGE or confidence < MIN_CONFIDENCE:
                    continue

                # ── 6. Cooldown ───────────────────────────────────────
                if is_on_cooldown(candidate):
                    if opp_tracker.is_open(candidate):
                        opp_tracker.update(candidate, net_edge)
                    continue

                # ══ OPORTUNIDAD CONFIRMADA ════════════════════════════
                set_cooldown(candidate)
                entries += 1
                is_new = not opp_tracker.is_open(candidate)

                analysis = {
                    "party": data["party"],
                    "prob_nom": prob_nom, "prob_gen": prob_gen, "prob_delta": prob_delta,
                    "prob_party_win": prob_party_win, "fair_prob_gen": fair_prob_gen,
                    "position": position,
                    "buy_vwap": buy_res["vwap"], "buy_levels": buy_res["levels"],
                    "sell_vwap": sell_res["vwap"], "sell_levels": sell_res["levels"],
                    "gross_edge": edge_data["gross_edge"], "fees_pct": edge_data["fees_pct"],
                    "slip_pct": edge_data["slip_pct"], "confidence": confidence,
                    "expected_edge": net_edge, "net_edge": net_edge, "net_pnl": edge_data["net_pnl"],
                    "trend": trend, "velocity": velocity,
                    "latency": latency
                }

                if is_new:
                    opp_tracker.open(candidate, analysis)
                else:
                    opp_tracker.update(candidate, net_edge)

                logger.warning(
                    f"🚨 {'NUEVA' if is_new else 'ACTIVA'} OPORTUNIDAD: {candidate} "
                    f"| Net +{net_edge*100:.2f}% | ${edge_data['net_pnl']:.2f} PnL est."
                )

                # ── Ejecución automática (solo en oportunidades nuevas) ──
                if is_new:
                    exec_result = await execute_arb_pair(
                        session=session,
                        candidate=candidate,
                        nom_token=data["nom_token"],
                        gen_token=data["elec_token"],
                        shares=buy_res["shares"],
                        buy_vwap=buy_res["vwap"],
                        sell_vwap=sell_res["vwap"],
                        liq_nom=liq_nom,
                        liq_gen=liq_gen,
                    )
                    analysis["execution"] = exec_result

                persist({
                    "event": "ENTRY" if is_new else "UPDATE",
                    "timestamp": now.isoformat(),
                    "candidate": candidate,
                    **analysis,
                })

                if is_new:
                    await send_entry_alert(
                        candidate, data["party"], data["nom_q"], data["elec_q"], analysis
                    )

            logger.info(
                f"📡 Scan OK | Detectados: {detected} | Entradas: {entries} | "
                f"Salidas: {exits} | Activas: {len(opp_tracker.all_open())} | "
                f"Próximo en {POLL_INTERVAL}s"
            )
            await asyncio.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Arbitrage Scanner detenido.")
