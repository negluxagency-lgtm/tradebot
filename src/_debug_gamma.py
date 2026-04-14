import requests, json

resp = requests.get(
    "https://gamma-api.polymarket.com/markets",
    params={"active": "true", "limit": "10", "order": "volume24hr", "ascending": "false"}
)
data = resp.json()
markets = data if isinstance(data, list) else data.get("markets", [])

print(f"Total recibidos: {len(markets)}")
print("Campos disponibles:", list(markets[0].keys()) if markets else "N/A")
print()
for m in markets:
    vol = float(m.get("volume", 0) or 0)
    q = m.get("question", "")[:70]
    print(f"Vol: ${vol:,.0f} | {q}")
