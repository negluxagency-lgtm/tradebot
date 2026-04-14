import asyncio
import aiohttp
import json

TARGET_KEYWORDS = [
    "president", "election", "democrat", "republican", "senate", "house of", 
    "congress", "trump", "biden", "harris", "politics", "minister", "governor", 
    "mayor", "vote", "votar", "elección", "presidencial", "gop", "dnc", 
    "primary", "primaries", "nominee"
]

async def fetch_politics_markets():
    markets_found = set()
    url = "https://gamma-api.polymarket.com/markets?limit=500&active=true&closed=false"
    
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(url) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    
                    for market in data:
                        question = market.get("question", "").lower()
                        title = market.get("title", "").lower()
                        
                        text_to_search = f"{question} {title}"
                        
                        is_politics = any(kw in text_to_search for kw in TARGET_KEYWORDS)
                        
                        if is_politics:
                            market_name = market.get("question") or market.get("title")
                            if market_name:
                                markets_found.add(market_name)
                                
                    print(f"✅ Se encontraron {len(markets_found)} mercados políticos activos:\n")
                    for name in sorted(list(markets_found)):
                        print(f"- {name}")
                else:
                    print(f"Error HTTP {resp.status}")
        except Exception as e:
            print(f"Error: {e}")

if __name__ == "__main__":
    asyncio.run(fetch_politics_markets())
