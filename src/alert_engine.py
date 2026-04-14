"""
🛰️ Alert Engine — Whale Insider Tracker
Responsabilidad: Enviar alertas Telegram y persistir señales en Supabase.
"""
import os
import json
import asyncio
import aiohttp
import logging
from datetime import datetime, timezone
from dotenv import load_dotenv

load_dotenv(".env.local")

# ── Configuración ──────────────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID")
SUPABASE_URL       = os.getenv("NEXT_PUBLIC_SUPABASE_URL")
SUPABASE_KEY       = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
DRY_RUN            = os.getenv("DRY_RUN", "true").lower() == "true"

TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"

logger = logging.getLogger("alert_engine")


# ── Formateador de mensajes ────────────────────────────────────────────────────
def _format_telegram_message(signal: dict) -> str:
    """Genera el mensaje formateado para Telegram."""
    mode_tag = "🟡 DRY RUN" if DRY_RUN else "🟢 LIVE"
    signal_icons = {
        "institutional_block": "🎯 Institucional Block (Sniper)",
        "twap_accumulation": "🤖 TWAP Accumulation",
        "retail_frenzy": "🌊 Frenesí Retail",
        "wash_trading": "🔁 Wash Trading",
        "algorithmic_split": "🤖 Split Algorítmico Puro",
        "algorithmic_split_algo_split": "⚡🤖 Ráfaga Algorítmica",
        "late_execution": "💤 Ejecución Tardía (Certainty)",
        "anomalous_impact": "💥 Impacto Anómalo (+1k IS)",
        "shadow_mirror": "👥 SHADOW MIRROR (Espejo)"
    }
    signal_type_label = signal_icons.get(signal.get("signal_type"), "🐋 Anomalía Estructural")

    copy_status = (
        f"💸 Copy Trade: ${signal.get('copy_trade_usdc', 0):.0f} USDC (simulado)"
        if DRY_RUN
        else f"💸 Copy Trade: ${signal.get('copy_trade_usdc', 0):.0f} USDC EJECUTADO ✅"
    )

    tier = signal.get("tier", "TIER_3")
    tier_label = {"TIER_1": "🏆 TIER 1 (Premium)", "TIER_2": "🥈 TIER 2 (Notable)"}.get(tier, "TIER_3")

    # Título dinámico para Shadow Mirror
    title = f"👥 *SHADOW MIRROR INT — {signal.get('market_name', 'Mercado Desconocido')}*" if signal.get("signal_type") == "shadow_mirror" else f"🐋 *SIGNAL INT — {signal.get('market_name', 'Mercado Desconocido')}*"

    return (
        f"{title}\n"
        f"─────────────────────\n"
        f"🎯 Outcome: `{signal.get('outcome', 'N/A')}`\n"
        f"↕️ Acción: *{signal.get('side', 'BUY')}*\n"
        f"💰 Vol Agregado: `${signal.get('trade_size_usdc', 0):,.0f} USDC`\n"
        f"🧩 Trades: `{signal.get('trades_count', 1)}`\n"
        f"📊 Impact Score: `{signal.get('impact_score', 0):.1f} IS`\n"
        f"⭐ SQS: `{signal.get('sqs', 0):.3f}` — *{tier_label}*\n"
        f"🔬 Patrón: *{signal_type_label}*\n"
        f"📍 Precio (prom.): `{signal.get('price', 0):.3f} USDC`\n"
        f"🏦 {copy_status}\n"
        f"─────────────────────\n"
        f"🌐 Wallets: `{signal.get('wallet_count', 1)}` • Burst: `{signal.get('wallets_in_burst', 1)}`\n"
        f"⏰ {signal.get('timestamp', datetime.now(timezone.utc).isoformat())[:19].replace('T', ' ')}\n"
        f"{mode_tag}"
    )


# ── Envío a Telegram ───────────────────────────────────────────────────────────
async def send_telegram_alert(signal: dict) -> bool:
    """Envía alerta formateada al chat de Telegram."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logger.warning("Telegram no configurado. Omitiendo alerta.")
        return False

    message = _format_telegram_message(signal)
    payload = {
        "chat_id":    TELEGRAM_CHAT_ID,
        "text":       message,
        "parse_mode": "Markdown",
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(TELEGRAM_API, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status == 200:
                    logger.info(f"✅ Alerta Telegram enviada: {signal.get('market_name')}")
                    return True
                else:
                    body = await resp.text()
                    logger.error(f"❌ Telegram error {resp.status}: {body}")
                    return False
    except Exception as e:
        logger.error(f"❌ Telegram excepción: {e}")
        return False


# ── Persistencia Supabase ──────────────────────────────────────────────────────
async def persist_signal_supabase(signal: dict) -> bool:
    """Persiste la señal whale en la tabla `whale_alerts` de Supabase."""
    if not SUPABASE_URL or not SUPABASE_KEY:
        logger.warning("Supabase no configurado. Omitiendo persistencia.")
        return False

    endpoint = f"{SUPABASE_URL}/rest/v1/whale_alerts"
    headers = {
        "apikey":        SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type":  "application/json",
        "Prefer":        "return=minimal",
    }
    row = {
        "market_id":        signal.get("market_id"),
        "market_name":      signal.get("market_name"),
        "outcome":          signal.get("outcome"),
        "trade_size_usdc":  signal.get("trade_size_usdc"),
        "impact_score":     signal.get("impact_score"),
        "signal_type":      signal.get("signal_type"),
        "price":            signal.get("price"),
        "wallet_address":   signal.get("wallet_address"),
        "wallet_count":     signal.get("wallet_count", 1),
        "wallets_in_burst": signal.get("wallets_in_burst", 1),
        "bias":             signal.get("bias", 0.5),
        "sqs":              signal.get("sqs", 0.0),
        "tier":             signal.get("tier", "TIER_3"),
        "copy_trade_usdc":  signal.get("copy_trade_usdc", 0),
        "dry_run":          DRY_RUN,
        "timestamp":        signal.get("timestamp", datetime.now(timezone.utc).isoformat()),
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(endpoint, json=row, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status in (200, 201):
                    logger.info(f"✅ Señal persistida en Supabase.")
                    return True
                elif resp.status == 404:
                    logger.warning(f"⚠️ Supabase 404: La tabla 'whale_alerts' no existe. Persistencia delegada al archivo JSON.")
                    return False
                else:
                    body = await resp.text()
                    logger.error(f"❌ Supabase error {resp.status}: {body}")
                    return False
    except Exception as e:
        logger.error(f"❌ Supabase excepción: {e}")
        return False


# ── Persistencia local JSON ────────────────────────────────────────────────────
def persist_signal_local(signal: dict, filepath: str = "artifacts/whale_signals.json"):
    """Persiste la señal en el archivo JSON local (modo offline/backup)."""
    os.makedirs("artifacts", exist_ok=True)
    existing = []
    if os.path.exists(filepath):
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                existing = json.load(f)
        except Exception:
            existing = []

    existing.append(signal)
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(existing, f, indent=2, ensure_ascii=False)
    logger.info(f"📁 Señal guardada localmente en {filepath}")


# ── Dispatcher principal ───────────────────────────────────────────────────────
async def dispatch_alert(signal: dict):
    """Punto de entrada. Dispara todas las acciones de alerta en paralelo."""
    signal.setdefault("timestamp", datetime.now(timezone.utc).isoformat())

    # shadow_mirror: no tiene SQS ni impact_score → saltar Supabase y solo usar Telegram + JSON local
    if signal.get("signal_type") == "shadow_mirror":
        persist_signal_local(signal)
        await send_telegram_alert(signal)
        return

    # Persistencia local siempre (no falla)
    persist_signal_local(signal)

    tier = signal.get("tier", "TIER_3")

    # TIER_3: descartado, solo JSON local ya guardado
    if tier == "TIER_3":
        return

    tasks = [persist_signal_supabase(signal)]

    # Solo TIER_1 activa Telegram
    if tier == "TIER_1":
        tasks.append(send_telegram_alert(signal))
    else:
        logger.info(f"🔇 Telegram silenciado [TIER_2]: SQS={signal.get('sqs', 0):.3f} | {signal.get('signal_type')}")

    await asyncio.gather(*tasks, return_exceptions=True)


# ── Test de conectividad ───────────────────────────────────────────────────────
async def send_startup_message():
    """Envia mensaje de arranque para verificar conectividad."""
    mode = "DRY RUN" if DRY_RUN else "LIVE"
    payload = {
        "chat_id":    TELEGRAM_CHAT_ID,
        "text":       f"Sistema Shadow Tracker Online\n{mode} -- Motor de deteccion activo.",
        "parse_mode": "Markdown",
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(TELEGRAM_API, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                return resp.status == 200
    except Exception as e:
        logger.error(f"Startup message fallo: {e}")
        return False


async def send_portfolio_summary():
    """Envia un resumen del portafolio Shadow Tracker a Telegram."""
    pnl_path = "artifacts/copy_trade_pnl.json"
    try:
        if not os.path.exists(pnl_path):
            return
        with open(pnl_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        abiertas       = [p for p in data if p.get("status") == "open"]
        cerradas       = [p for p in data if p.get("status") == "closed"]
        capital_total  = sum(p.get("copy_trade_usdc", 0) for p in data)
        capital_vivo   = sum(p.get("copy_trade_usdc", 0) for p in abiertas)
        pnl_realizado  = sum(p.get("pnl_usdc", 0) or 0 for p in cerradas)
        mode_tag       = "DRY RUN" if DRY_RUN else "LIVE"

        pnl_emoji = "+" if pnl_realizado >= 0 else ""
        msg = (
            f"*SHADOW TRACKER — Resumen de Portafolio*\n"
            f"_Informe automatico cada 100 apuestas interceptadas_\n"
            f"─────────────────────\n"
            f"Apuestas abiertas: `{len(abiertas)}`\n"
            f"Apuestas cerradas: `{len(cerradas)}`\n"
            f"Capital total usado: `${capital_total:.2f} USDC`\n"
            f"Capital en juego: `${capital_vivo:.2f} USDC`\n"
            f"P/L realizado: `${pnl_emoji}{pnl_realizado:.2f} USDC`\n"
            f"─────────────────────\n"
            f"_{mode_tag}_"
        )
        payload = {"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "Markdown"}
        async with aiohttp.ClientSession() as session:
            async with session.post(TELEGRAM_API, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status == 200:
                    logger.info("Resumen de portafolio enviado a Telegram.")
                else:
                    logger.error(f"Error enviando resumen: {resp.status}")
    except Exception as e:
        logger.error(f"Error en send_portfolio_summary: {e}")
