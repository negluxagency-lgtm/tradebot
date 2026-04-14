import asyncio
import aiohttp
import json
import re
from collections import defaultdict

async def extract_candidate_pairs():
    url = "https://gamma-api.polymarket.com/markets?limit=500&active=true&closed=false"
    
    candidates = defaultdict(dict)
    
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(url) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    
                    for market in data:
                        q = market.get("question", "")
                        
                        nom_match = re.match(r"Will (.+?) win the 2028 (Democratic|Republican) presidential nomination\?", q)
                        elec_match = re.match(r"Will (.+?) win the 2028 US Presidential Election\?", q)
                        
                        if nom_match:
                            name = nom_match.group(1).strip()
                            party = nom_match.group(2)
                            candidates[name]['nomination'] = market
                            candidates[name]['party'] = party
                        elif elec_match:
                            name = elec_match.group(1).strip()
                            candidates[name]['election'] = market

                    target_pairs = 0
                    inconsistencies = 0
                    
                    for name, markets in candidates.items():
                        if 'nomination' in markets and 'election' in markets:
                            target_pairs += 1
                            nom_market = markets['nomination']
                            elec_market = markets['election']
                            
                            nom_prices = nom_market.get("outcomePrices")
                            elec_prices = elec_market.get("outcomePrices")
                            
                            nom_price = 0.0
                            elec_price = 0.0
                            
                            if nom_prices:
                                try:
                                    # Fix: extract float properly from string array
                                    prices_array = json.loads(nom_prices)
                                    if prices_array and prices_array[0]:
                                        nom_price = float(prices_array[0])
                                except:
                                    pass
                            
                            if elec_prices:
                                try:
                                    prices_array = json.loads(elec_prices)
                                    if prices_array and prices_array[0]:
                                        elec_price = float(prices_array[0])
                                except:
                                    pass
                                    
                            edge = elec_price - nom_price
                            
                            # Validar que los mercados tengan liquidez (ej. ignorar los que cuestan 0 o >0.90)
                            if (0.01 <= nom_price <= 0.90) and (0.01 <= elec_price <= 0.90):
                                print(f"[{name}] ({markets.get('party', 'N/A')})")
                                print(f"  - Nominación: {nom_price:.4f} USDC | Vol: ${float(nom_market.get('volume', 0)):.0f}")
                                print(f"  - General:    {elec_price:.4f} USDC | Vol: ${float(elec_market.get('volume', 0)):.0f}")
                                
                                if edge > 0:
                                    print(f"  🚨 INCONSISTENCIA: General es +{edge*100:.2f}% mayor que Nominación")
                                    inconsistencies += 1
                                print("-" * 40)
                                
                    print(f"\nResumen: {inconsistencies} inconsistencias iniciales encontradas.")
                        
        except Exception as e:
            print(f"Error: {e}")

if __name__ == "__main__":
    asyncio.run(extract_candidate_pairs())
