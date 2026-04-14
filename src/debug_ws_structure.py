"""
Diagnostico de Urgencia - Estructura real del WebSocket CLOB
Verifica que campos llegan realmente en los eventos de precio/trade.
"""
import asyncio
import websockets
import json
import aiohttp
import sys
import io

# Forzar UTF-8 en stdout para Windows
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

GAMMA_API_URL = "https://gamma-api.polymarket.com"
CLOB_WS_URL   = "wss://ws-subscriptions-clob.polymarket.com/ws/market"

async def get_active_assets():
    async with aiohttp.ClientSession() as session:
        async with session.get(f"{GAMMA_API_URL}/markets", params={"active":"true", "limit":50, "order":"volume", "ascending":"false"}) as r:
            data = await r.json()
            assets = []
            for m in data:
                ids = json.loads(m.get("clobTokenIds", "[]"))
                assets.extend(ids)
            return assets

async def diagnose():
    assets = await get_active_assets()
    print(f"[OK] Suscribiendose a {len(assets)} assets...\n", flush=True)

    wallet_found_ever = False

    async with websockets.connect(CLOB_WS_URL, ping_interval=30) as ws:
        await ws.send(json.dumps({"type": "market", "assets_ids": assets[:500]}))

        count = 0
        async for msg in ws:
            events = json.loads(msg)
            if not isinstance(events, list): events = [events]

            for ev in events:
                et = ev.get("event_type") or ev.get("type", "UNKNOWN")
                keys = sorted(ev.keys())

                has_wallet = any("address" in k or "trader" in k or "maker" in k or "taker" in k for k in keys)

                if has_wallet:
                    wallet_found_ever = True
                    print(f"[!! CON WALLET] type={et} | keys={keys}", flush=True)
                    print(f"   -> {str(ev)[:400]}\n", flush=True)
                elif count % 300 == 0:
                    print(f"[sin wallet #{count}] type={et} | keys={keys}", flush=True)

                for pc in ev.get("price_changes", []):
                    pc_keys = sorted(pc.keys())
                    if any("address" in k or "maker" in k or "taker" in k for k in pc_keys):
                        wallet_found_ever = True
                        print(f"[!! price_change CON WALLET] keys={pc_keys}", flush=True)
                        print(f"   -> {str(pc)[:400]}\n", flush=True)

                count += 1
                if count >= 3000:
                    print(f"\n--- DIAGNOSTICO COMPLETADO ({count} eventos) ---", flush=True)
                    if wallet_found_ever:
                        print("RESULTADO: El canal market SI incluye direcciones de wallet.", flush=True)
                    else:
                        print("RESULTADO: El canal market NO incluye direcciones de wallet.", flush=True)
                        print("SOLUCION: Debemos usar el endpoint REST /trades para polling.", flush=True)
                    return

asyncio.run(diagnose())
