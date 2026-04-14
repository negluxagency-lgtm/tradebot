import asyncio
import websockets
import json

async def simple_ws_test():
    uri = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
    print(f"Connecting to {uri}")
    async with websockets.connect(uri, ping_interval=30, ping_timeout=10) as ws:
        print("Connected. Sending subscription...")
        msg = {
            "type": "market",
            "assets_ids": [],
        }
        await ws.send(json.dumps(msg))
        print(f"Sent: {msg}")
        
        try:
            while True:
                response = await asyncio.wait_for(ws.recv(), timeout=10)
                print(f"Received: {response[:200]}")
        except asyncio.TimeoutError:
            print("Timeout waiting for message.")
        except Exception as e:
            print(f"Error: {e}")

asyncio.run(simple_ws_test())
