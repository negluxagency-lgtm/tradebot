import requests
import json
import sys
import io
from datetime import datetime

# Fix para codificación Unicode en terminales Windows
if sys.stdout.encoding != 'utf-8':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

WALLET = "0xe1d6b51521bd4365769199f392f9818661bd907c"
API_URL = "https://data-api.polymarket.com/positions"
TODAY_STR = "2026-04-21"

def extract_positions():
    print(f"🚀 Iniciando extracción de posiciones para: {WALLET}")
    print(f"📅 Filtrando por fecha: {TODAY_STR}")
    
    params = {
        "user": WALLET,
        "limit": 100
    }
    
    try:
        resp = requests.get(API_URL, params=params, timeout=15)
        if resp.status_code != 200:
            print(f"❌ Error API: {resp.status_code}")
            return
        
        data = resp.json()
        all_positions = data if isinstance(data, list) else []
        
        extracted = []
        if all_positions:
            print(f"DEBUG: Keys in first position: {all_positions[0].keys()}")
            print(f"DEBUG: Sample first position: {json.dumps(all_positions[0], indent=2)}")
        
        extracted = []
        for p in all_positions:
            end_date = p.get("endDate", "")
            title = p.get("title", "")
            
            # Solo posiciones que vencen hoy o tienen actividad
            if end_date == TODAY_STR or TODAY_STR in title:
                size = float(p.get("size", 0))
                extracted.append({
                    "title": title,
                    "outcome": p.get("outcome"),
                    "status": "OPEN" if size > 0 else "CLOSED",
                    "size": size,
                    "avg_price": p.get("avgPrice"),
                    "cur_price": p.get("curPrice"),
                    "realized_pnl": p.get("realizedPnl"),
                    "unrealized_pnl": p.get("cashPnl"),
                    "total_bought": p.get("totalBought")
                })




        # Guardar en artifacts
        output_path = "artifacts/extracted_wallet_positions.json"
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(extracted, f, indent=2)
            
        print(f"✅ Extracción completada. {len(extracted)} posiciones encontradas hoy.")
        print(f"📦 Artefacto materializado: {output_path}")

    except Exception as e:
        print(f"❌ Error durante la extracción: {e}")

if __name__ == "__main__":
    extract_positions()
