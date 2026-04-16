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
# Dejamos estos para compatibilidad con otros módulos, pero usaremos parámetros dinámicos cuando sea posible.
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID")
DASHBOARD_BOT_TOKEN = os.getenv("DASHBOARD_BOT_TOKEN")
DASHBOARD_CHAT_ID   = os.getenv("DASHBOARD_CHAT_ID")
SUPABASE_URL       = os.getenv("NEXT_PUBLIC_SUPABASE_URL")
SUPABASE_KEY       = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
DRY_RUN            = os.getenv("DRY_RUN", "true").lower() == "true"

def get_telegram_api(token: str = None) -> str:
    token = token or TELEGRAM_BOT_TOKEN
    return f"https://api.telegram.org/bot{token}/sendMessage"

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
async def send_telegram_alert(signal: dict, bot_token: str = None, chat_id: str = None) -> bool:
    """Envía alerta formateada al chat de Telegram."""
    token = bot_token or TELEGRAM_BOT_TOKEN
    cid   = chat_id or TELEGRAM_CHAT_ID
    
    if not token or not cid:
        logger.warning("Telegram no configurado. Omitiendo alerta.")
        return False

    message = _format_telegram_message(signal)
    payload = {
        "chat_id":    cid,
        "text":       message,
        "parse_mode": "Markdown",
    }

    try:
        async with aiohttp.ClientSession() as session:
            url = get_telegram_api(token)
            async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as resp:
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
async def dispatch_alert(signal: dict, bot_token: str = None, chat_id: str = None):
    """Punto de entrada. Dispara todas las acciones de alerta en paralelo."""
    signal.setdefault("timestamp", datetime.now(timezone.utc).isoformat())

    # shadow_mirror: no tiene SQS ni impact_score → saltar Supabase y solo usar Telegram + JSON local
    if signal.get("signal_type") == "shadow_mirror":
        persist_signal_local(signal)
        await send_telegram_alert(signal, bot_token=bot_token, chat_id=chat_id)
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
async def send_startup_message(bot_token: str = None, chat_id: str = None):
    """Envia mensaje de arranque para verificar conectividad."""
    token = bot_token or TELEGRAM_BOT_TOKEN
    cid   = chat_id or TELEGRAM_CHAT_ID
    
    if not token or not cid:
        return False
        
    mode = "DRY RUN" if DRY_RUN else "LIVE"
    payload = {
        "chat_id":    cid,
        "text":       f"Sistema Shadow Tracker Online\n{mode} -- Motor de deteccion activo.",
        "parse_mode": "Markdown",
    }
    try:
        async with aiohttp.ClientSession() as session:
            url = get_telegram_api(token)
            async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                return resp.status == 200
    except Exception as e:
        logger.error(f"Startup message fallo: {e}")
        return False


async def send_portfolio_summary(bot_token: str = None, chat_id: str = None, wallet: str = None):
    """Envia un resumen detallado del portafolio filtrado por billetera si se especifica."""
    token = bot_token or TELEGRAM_BOT_TOKEN
    cid   = chat_id or TELEGRAM_CHAT_ID
    
    if not token or not cid:
        return
        
    pnl_path = "artifacts/copy_trade_pnl.json"
    try:
        if not os.path.exists(pnl_path):
            msg = "Sin posiciones registradas todavía."
            payload = {"chat_id": cid, "text": msg}
            async with aiohttp.ClientSession() as session:
                url = get_telegram_api(token)
                await session.post(url, json=payload)
            return
            
        with open(pnl_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        if not data:
            return

        if wallet:
            data = [p for p in data if p.get("wallet_address") == wallet]
            if not data:
                msg = f"Sin posiciones registradas para la billetera {wallet[:10]}..."
                payload = {"chat_id": cid, "text": msg}
                async with aiohttp.ClientSession() as session:
                    url = get_telegram_api(token)
                    await session.post(url, json=payload)
                return

        abiertas       = [p for p in data if p.get("status") == "open"]
        cerradas       = [p for p in data if p.get("status") == "closed"]
        fallidas       = [p for p in data if p.get("status") == "failed"]
        
        capital_total  = sum(p.get("copy_trade_usdc", 0) for p in data)
        capital_abierto= sum(p.get("copy_trade_usdc", 0) for p in abiertas)
        pnl_realizado  = sum(p.get("pnl_usdc", 0) or 0 for p in cerradas)
        mode_tag       = "DRY RUN 🟡" if DRY_RUN else "LIVE 🟢"

        pnl_emoji = "+" if pnl_realizado >= 0 else ""
        
        msg = (
            f"📊 *REPORTE SHADOW TRACKER* ({mode_tag})\n"
            f"─────────────────────\n"
            f"📉 *Total trades*        : `{len(data)}`\n"
            f"🔓 *Posiciones abiertas* : `{len(abiertas)}`\n"
            f"🔒 *Posiciones cerradas* : `{len(cerradas)}`\n"
            f"❌ *Fallidas*            : `{len(fallidas)}`\n"
            f"─────────────────────\n"
            f"💸 *Capital total usado* : `${capital_total:.2f} USDC`\n"
            f"⏳ *Capital en juego*    : `${capital_abierto:.2f} USDC`\n"
            f"💰 *P/L realizado*       : `${pnl_emoji}{pnl_realizado:.2f} USDC`\n"
            f"─────────────────────\n"
        )
        
        # Add list of open positions like report_portfolio.py
        if abiertas:
            msg += "\n📂 *POSICIONES ABIERTAS:*\n"
            for p in abiertas[:25]: # Limit to 25 to avoid Telegram length limit
                name = p.get('market_name', 'N/A')[:25]
                outcome = p.get('outcome', 'N/A')[:8]
                cap = p.get('copy_trade_usdc', 0)
                price = p.get('entry_price', 0)
                msg += f"• `{name}` | {outcome} | `${cap:.2f} @ {price:.3f}`\n"
                
            if len(abiertas) > 25:
                msg += f"\n_... y {len(abiertas) - 25} más._\n"

        payload = {"chat_id": cid, "text": msg, "parse_mode": "Markdown"}
        async with aiohttp.ClientSession() as session:
            url = get_telegram_api(token)
            async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status == 200:
                    logger.info("Resumen de portafolio enviado a Telegram.")
                else:
                    body = await resp.text()
                    logger.error(f"Error enviando resumen: {resp.status} - {body}")
    except Exception as e:
        logger.error(f"Error en send_portfolio_summary: {e}")

async def telegram_listener_loop(bot_token: str = None, chat_id: str = None, wallet: str = None):
    """Escucha mensajes de Telegram usando HTTP long polling de forma asíncrona.
    Permite interactuar con el bot y pedirle un reporte escribiendo cualquier mensaje."""
    token = bot_token or TELEGRAM_BOT_TOKEN
    cid   = chat_id or TELEGRAM_CHAT_ID

    if not token or not cid:
        logger.warning("Faltan credenciales de Telegram. Listener offline.")
        return
        
    offset = None
    logger.info(f"📡 Telegram Listener Activo [{cid}]. Escribe cualquier cosa al bot para recibir el reporte.")
    
    while True:
        try:
            url = f"https://api.telegram.org/bot{token}/getUpdates"
            params = {"timeout": 30}
            if offset:
                params["offset"] = offset
                
            async with aiohttp.ClientSession() as session:
                async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=40)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        for result in data.get("result", []):
                            offset = result["update_id"] + 1
                            message = result.get("message")
                            
                            if message and "text" in message:
                                chat_id = str(message.get("chat", {}).get("id"))
                                # Seguridad: Solo procesar respuestas para el dueño del bot (nuestro chat_id)
                                if chat_id == cid:
                                    logger.info(f"📨 Comando recibido en Telegram ({cid}). Enviando reporte para {wallet[:10]}...")
                                    await send_portfolio_summary(bot_token=token, chat_id=cid, wallet=wallet)
        except Exception as e:
            # Ignorar errores menores de conexión por timeout
            logger.debug(f"Error en telegram listener polling: {e}")
            
        await asyncio.sleep(2)


# ── Funciones Utilitarias ─ Balances USDC ─────────────────────────────────────────
async def get_usdc_balance(address: str) -> float:
    """Obtiene el balance de USDC de la wallet usando el nodo publico de Polygon."""
    if not address or address == "unknown":
        return 0.0
    url = "https://polygon-rpc.com"
    payload = {
        "jsonrpc": "2.0",
        "method": "eth_call",
        "params": [{
            "to": "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174",
            "data": "0x70a08231000000000000000000000000" + address[2:].zfill(40)
        }, "latest"],
        "id": 1
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, timeout=5) as resp:
                data = await resp.json()
                rhex = data.get("result", "0x0")
                if rhex == "0x": rhex = "0x0"
                return int(rhex, 16) / 1e6
    except Exception as e:
        logger.error(f"Error RPC: {e}")
        return 0.0


# ── Dashboard Centralizado (Reporte Consolidado de todos los perfiles) ─────────
async def send_dashboard_summary(profiles: list, bot_token: str = None, chat_id: str = None):
    """
    Envía un reporte consolidado de los 3 perfiles al bot de control central.
    Cada perfil muestra: capital en juego, total jugado y P/L individual.
    Al final, calcula el Portfolio Total y Beneficio Neto usando INITIAL_CAPITAL.
    """
    token = bot_token or DASHBOARD_BOT_TOKEN
    cid   = chat_id or DASHBOARD_CHAT_ID
    
    INITIAL_CAPITAL = float(os.getenv("INITIAL_CAPITAL", "269"))
    PROXY_ADDRESS = os.getenv("POLYMARKET_PROXY_ADDRESS", "")

    if not token or not cid:
        logger.warning("Dashboard Bot no configurado. Omitiendo reporte consolidado.")
        return

    pnl_path = "artifacts/copy_trade_pnl.json"
    mode_tag = "🟡 DRY RUN" if DRY_RUN else "🟢 LIVE"

    try:
        all_data = []
        if os.path.exists(pnl_path):
            with open(pnl_path, "r", encoding="utf-8") as f:
                all_data = json.load(f)
    except Exception:
        all_data = []

    msg = f"📊 *FLOTA SHADOW — REPORTE CONSOLIDADO* ({mode_tag})\n"
    msg += f"─────────────────────\n"

    total_en_juego  = 0.0
    total_jugado    = 0.0
    total_pnl       = 0.0

    for idx, profile in enumerate(profiles, 1):
        wallet = profile.get("wallet", "").lower()
        label  = profile.get("label", f"Bot {idx}")

        data = [p for p in all_data if p.get("wallet_address", "").lower() == wallet]

        abiertas = [p for p in data if p.get("status") == "open"]
        cerradas = [p for p in data if p.get("status") == "closed"]

        en_juego = sum(float(p.get("copy_trade_usdc", 0) or 0) for p in abiertas)
        jugado   = sum(float(p.get("copy_trade_usdc", 0) or 0) for p in data)
        pnl      = sum(float(p.get("pnl_usdc", 0) or 0) for p in cerradas)

        total_en_juego += en_juego
        total_jugado   += jugado
        total_pnl      += pnl

        pnl_sign = "+" if pnl >= 0 else ""
        msg += (
            f"\n*{label}* (`{wallet[:8]}...`)\n"
            f"  💰 En juego: `${en_juego:.2f}`\n"
            f"  🎲 Total jugado: `${jugado:.2f}`\n"
            f"  📈 P/L: `{pnl_sign}${pnl:.2f}`\n"
        )

    # Calcular Balance Real Proxy y Portfolio
    balance_usdc = await get_usdc_balance(PROXY_ADDRESS) if not DRY_RUN else 269.0
    total_portfolio = balance_usdc + total_en_juego
    real_net_profit = total_portfolio - INITIAL_CAPITAL
    
    profit_sign = "+" if real_net_profit >= 0 else ""
    
    msg += (
        f"\n─────────────────────\n"
        f"🌐 *TOTALES FLOTA*\n"
        f"  💰 En juego: `${total_en_juego:.2f}`\n"
        f"  🎲 Total jugado: `${total_jugado:.2f}`\n"
        f"  📈 Shadow P/L (Cerradas): `{'+' if total_pnl >= 0 else ''}{total_pnl:.2f}`\n\n"
        f"🏦 *CONTABILIDAD MAESTRA*\n"
        f"  💵 Wallet Saldo: `${balance_usdc:.2f}`\n"
        f"  📊 Portfolio Actual: `${total_portfolio:.2f}`\n"
        f"  🚀 *Beneficio Neto:* `{profit_sign}{real_net_profit:.2f}`\n"
        f"─────────────────────\n"
        f"⏰ {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC"
    )

    payload = {"chat_id": cid, "text": msg, "parse_mode": "Markdown"}
    try:
        async with aiohttp.ClientSession() as session:
            url = get_telegram_api(token)
            async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status == 200:
                    logger.info("✅ Dashboard consolidado enviado.")
                else:
                    body = await resp.text()
                    logger.error(f"❌ Error enviando dashboard: {resp.status} - {body}")
    except Exception as e:
        logger.error(f"❌ Excepción en send_dashboard_summary: {e}")


async def dashboard_listener_loop(profiles: list, bot_token: str = None, chat_id: str = None):
    """
    Escucha mensajes en el bot de control central y responde con el reporte
    consolidado de todos los perfiles.
    """
    token = bot_token or DASHBOARD_BOT_TOKEN
    cid   = chat_id or DASHBOARD_CHAT_ID

    if not token or not cid:
        logger.warning("Dashboard Bot no configurado. Listener offline.")
        return

    offset = None
    logger.info(f"📡 Dashboard Listener Activo [{cid}]. Escribe al bot de control para ver el reporte total.")

    while True:
        try:
            url = f"https://api.telegram.org/bot{token}/getUpdates"
            params = {"timeout": 30}
            if offset:
                params["offset"] = offset

            async with aiohttp.ClientSession() as session:
                async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=40)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        for result in data.get("result", []):
                            offset = result["update_id"] + 1
                            message = result.get("message")
                            if message and "text" in message:
                                incoming_chat_id = str(message.get("chat", {}).get("id"))
                                if incoming_chat_id == cid:
                                    logger.info("📨 Comando recibido en Dashboard. Preparando reporte de flota...")
                                    await send_dashboard_summary(profiles, bot_token=token, chat_id=cid)
        except Exception as e:
            logger.debug(f"Error en dashboard listener: {e}")

        await asyncio.sleep(2)
