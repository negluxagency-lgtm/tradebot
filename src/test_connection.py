import os
import json
import requests
from dotenv import load_dotenv
from datetime import datetime

# Cargar configuracin de entorno
load_dotenv('.env.local')

def test_polymarket_connection():
    """Valida la conexin con el Relayer de Polymarket."""
    
    # Parmetros de env
    host = os.getenv('POLY_HOST', 'https://clob.polymarket.com')
    api_key = os.getenv('RELAYER_API_KEY')
    api_address = os.getenv('RELAYER_API_KEY_ADDRESS')
    
    print(f"--- INICIANDO PING DE TELEMETRA ---")
    print(f"Host: {host}")
    print(f"RELAYER_API_KEY: {api_key[:8]}...{api_key[-4:] if api_key else 'None'}")
    print(f"RELAYER_API_KEY_ADDRESS: {api_address}")

    if not api_key or not api_address:
        print("[ERROR] Faltan credenciales en .env.local")
        return False

    # Endpoint de prueba: Mercados (Acceso pblico pero validamos headers)
    # Algunos Relayers requieren auth incluso para GET /markets
    endpoint = f"{host}/markets"
    
    headers = {
        "x-api-key": api_key,
        "x-api-signature-address": api_address, # Nombre de header comn para el address
        "Content-Type": "application/json",
        "User-Agent": "Antigravity-Trading-Bot/2026"
    }

    try:
        response = requests.get(endpoint, headers=headers, timeout=10)
        
        # Guardar resultado en artifacts
        result = {
            "timestamp": datetime.now().isoformat(),
            "status_code": response.status_code,
            "success": response.status_code == 200,
            "headers_sent": {k: "HIDDEN" if "key" in k.lower() else v for k, v in headers.items()},
            "response_snippet": response.text[:500] if response.text else "Empty Response"
        }
        
        with open('artifacts/connection_test.json', 'w', encoding='utf-8') as f:
            json.dump(result, f, indent=4)
            
        if response.status_code == 200:
            print(f"[OK] Conexin exitosa. Relayer respondiendo (Status 200).")
            return True
        else:
            print(f"[TURBULENCIA] Error de conexin. Status: {response.status_code}")
            print(f"Detalle: {response.text[:200]}")
            return False

    except Exception as e:
        print(f"[CRTICO] Error de comunicacin: {str(e)}")
        return False

if __name__ == "__main__":
    test_polymarket_connection()
