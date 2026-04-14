"""
[SISTEMA] Test de Conectividad -- Whale Insider Tracker
Verifica Telegram y el acceso a la Gamma API de Polymarket.
"""
import asyncio
import sys
import os

# Fix encoding para terminales Windows (cp1252)
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.dirname(__file__))

from dotenv import load_dotenv
load_dotenv(".env.local")

import aiohttp
from alert_engine import send_startup_message, dispatch_alert


async def test_telegram():
    print("🔵 Test 1: Conectividad Telegram...")
    ok = await send_startup_message()
    print(f"   → {'✅ OK — revisa tu Telegram' if ok else '❌ FALLO — verifica BOT_TOKEN y CHAT_ID'}")
    return ok


async def test_gamma_api():
    print("🔵 Test 2: Gamma API Polymarket...")
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                "https://gamma-api.polymarket.com/markets",
                params={"active": "true", "limit": "3", "order": "volume24hr", "ascending": "false"},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    markets = data if isinstance(data, list) else data.get("markets", [])
                    print(f"   → ✅ OK — {len(markets)} mercados recibidos de prueba")
                    for m in markets[:3]:
                        vol = float(m.get("volume", 0) or 0)
                        print(f"      • {m.get('question', 'N/A')[:60]} | Vol: ${vol:,.0f}")
                    return True
                else:
                    print(f"   → ❌ HTTP {resp.status}")
                    return False
    except Exception as e:
        print(f"   → ❌ Excepción: {e}")
        return False


async def test_fake_signal():
    print("🔵 Test 3: Señal de prueba (DRY RUN)...")
    fake_signal = {
        "market_id":       "TEST_MARKET_001",
        "market_name":     "¿Ganará X las elecciones? (TEST)",
        "asset_id":        "TEST_ASSET",
        "outcome":         "YES",
        "side":            "BUY",
        "trade_size_usdc": 15000.0,
        "price":           0.62,
        "z_score":         3.14,
        "wallet_address":  "0xTEST1234567890",
        "wallet_count":    1,
        "signal_type":     "z_score",
        "all_signals":     ["z_score"],
        "copy_trade_usdc": float(os.getenv("COPY_TRADE_USDC", "100")),
    }
    await dispatch_alert(fake_signal)
    print("   → ✅ Señal enviada — revisa Telegram y artifacts/whale_signals.json")


async def main():
    print("\n" + "="*55)
    print("  [WHALE INSIDER TRACKER] Suite de Tests")
    print("="*55 + "\n")

    t1 = await test_telegram()
    print()
    t2 = await test_gamma_api()
    print()
    await test_fake_signal()

    print("\n" + "="*55)
    if t1 and t2:
        print("  [OK] TODOS LOS SISTEMAS OPERATIVOS -- Listo para LEVITATION")
        print("  Ejecuta: python src/whale_tracker_main.py")
    else:
        print("  [WARN] Revisa las fallas antes de lanzar el sistema")
    print("="*55 + "\n")


if __name__ == "__main__":
    asyncio.run(main())
