import os
import logging
import requests
from datetime import datetime
from dotenv import load_dotenv
from pathlib import Path

# Cargar configuración
load_dotenv(Path(__file__).parent.parent / ".env.local")

SUPABASE_URL = os.getenv("NEXT_PUBLIC_SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "") # Usar Service Role para bypass RLS

logger = logging.getLogger(__name__)

def is_supabase_enabled() -> bool:
    return bool(SUPABASE_URL and SUPABASE_KEY)

def get_supabase_headers() -> dict:
    return {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=representation"
    }

def log_trade_to_supabase(trade_data: dict) -> str:
    """
    Inserta una nueva operación en la tabla 'trades'.
    Retorna el UUID generado.
    """
    if not is_supabase_enabled():
        return ""

    url = f"{SUPABASE_URL}/rest/v1/trades"
    
    payload = {
        "symbol":      trade_data.get("symbol", "BTC-USD"),
        "side":        trade_data.get("side"),
        "trend":       trade_data.get("trend"),
        "entry_price": trade_data.get("entry_price"),
        "take_profit": trade_data.get("take_profit"),
        "stop_loss":   trade_data.get("stop_loss"),
        "result":      "OPEN",
        "is_live":     trade_data.get("is_live", False)
    }

    try:
        resp = requests.post(url, headers=get_supabase_headers(), json=payload, timeout=10)
        if resp.status_code in [201, 200]:
            new_id = resp.json()[0].get("id")
            logger.info(f"[SUPABASE] Operación registrada: {new_id}")
            return new_id
        else:
            logger.error(f"[SUPABASE] Error insertando: {resp.status_code} | {resp.text}")
            return ""
    except Exception as e:
        logger.error(f"[SUPABASE] Excepción en log_trade: {e}")
        return ""

def update_trade_in_supabase(trade_uuid: str, update_data: dict):
    """
    Actualiza una operación existente (Cierre).
    """
    if not is_supabase_enabled() or not trade_uuid:
        return

    url = f"{SUPABASE_URL}/rest/v1/trades?id=eq.{trade_uuid}"
    
    payload = {
        "exit_price": update_data.get("exit_price"),
        "pnl_pct":    update_data.get("pnl_pct"),
        "result":     update_data.get("result"),
        "reason":     update_data.get("reason")
    }

    try:
        resp = requests.patch(url, headers=get_supabase_headers(), json=payload, timeout=10)
        if resp.status_code in [200, 204]:
            logger.info(f"[SUPABASE] Operación actualizada: {trade_uuid}")
        else:
            logger.error(f"[SUPABASE] Error actualizando: {resp.status_code} | {resp.text}")
    except Exception as e:
        logger.error(f"[SUPABASE] Excepción en update_trade: {e}")
