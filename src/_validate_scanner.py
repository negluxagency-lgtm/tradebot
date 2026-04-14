"""Script de validacion rapida del fix del scanner."""
import asyncio
import sys, os
sys.path.insert(0, os.path.dirname(__file__))
from dotenv import load_dotenv
load_dotenv(".env.local")

from whale_scanner import fetch_active_markets, build_market_meta, market_meta

async def main():
    markets = await fetch_active_markets()
    print(f"\nMercados objetivo encontrados: {len(markets)}")
    build_market_meta(markets)
    print(f"Asset IDs mapeados:             {len(market_meta)}")
    print("\nTop 10 mercados seleccionados:")
    for m in markets[:10]:
        vol = float(m.get("volume", 0) or 0)
        ids = m.get("clobTokenIds") or []
        print(f"  Vol: ${vol:>12,.0f} | IDs: {len(ids)} | {m.get('question','')[:65]}")

asyncio.run(main())
