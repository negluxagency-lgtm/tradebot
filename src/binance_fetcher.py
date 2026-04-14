import requests
import pandas as pd
import time
from datetime import datetime
import os
from pathlib import Path

# Configuración
SYMBOL = "BTCUSDT"
INTERVAL = "15m"
LIMIT = 1000
DATA_DIR = Path("data")

def fetch_klines(symbol, interval, start_time=None, limit=1000):
    """Descarga un bloque de velas de Binance."""
    url = "https://api.binance.com/api/v3/klines"
    params = {
        "symbol": symbol,
        "interval": interval,
        "limit": limit
    }
    if start_time:
        params["startTime"] = start_time
    
    response = requests.get(url, params=params)
    response.raise_for_status()
    return response.json()

def download_historical_data(days=None, start_date=None, end_date=None):
    """
    Descarga datos históricos en bucle. 
    Prioriza start_date/end_date si se proporcionan.
    """
    DATA_DIR.mkdir(exist_ok=True)
    
    if start_date and end_date:
        start_ts_ms = int(datetime.strptime(start_date, "%Y-%m-%d").timestamp() * 1000)
        end_ts_limit = int(datetime.strptime(end_date, "%Y-%m-%d").timestamp() * 1000)
        days_label = f"{start_date}_to_{end_date}"
    else:
        # Calcular tiempo de inicio basado en días atrás
        start_ts_ms = int((time.time() - (days * 24 * 60 * 60)) * 1000)
        end_ts_limit = int(time.time() * 1000)
        days_label = f"last_{days}_days"

    current_ts = start_ts_ms
    filename = DATA_DIR / f"{SYMBOL}_{INTERVAL}_{days_label}.csv"
    
    all_candles = []
    
    print(f"🚀 Iniciando descarga de {SYMBOL} {INTERVAL} ({days_label})...")
    
    while True:
        try:
            klines = fetch_klines(SYMBOL, INTERVAL, start_time=current_ts, limit=LIMIT)
            if not klines:
                break
            
            all_candles.extend(klines)
            
            # El último timestamp de la lista + 1ms para la siguiente petición
            last_ts = klines[-1][0]
            current_ts = last_ts + 1
            
            # Mostrar progreso
            last_date = datetime.fromtimestamp(last_ts / 1000).strftime('%Y-%m-%d %H:%M')
            print(f"  📦 Descargado hasta: {last_date} ({len(all_candles)} velas)...")
            
            # Si el último timestamp supera el límite, paramos
            if last_ts >= end_ts_limit:
                print(f"✨ Hemos completado el rango solicitado.")
                break
                
            time.sleep(0.1) # Respetar rate limits
            
        except Exception as e:
            print(f"❌ Error durante la descarga: {e}")
            break
            
    # Formatear a DataFrame
    df = pd.DataFrame(all_candles, columns=[
        "Open_Time", "Open", "High", "Low", "Close", "Volume",
        "Close_Time", "Quote_Asset_Volume", "Number_of_Trades",
        "Taker_Buy_Base", "Taker_Buy_Quote", "Ignore"
    ])
    
    # Limpiar y convertir
    df["Open_Time"] = pd.to_datetime(df["Open_Time"], unit='ms')
    df.set_index("Open_Time", inplace=True)
    
    # Seleccionar columnas necesarias y convertir a float
    cols = ["Open", "High", "Low", "Close", "Volume"]
    df = df[cols].astype(float)
    
    # Guardar a CSV
    df.to_csv(filename)
    print(f"\n✅ Archivo guardado en: {filename}")
    print(f"📊 Total: {len(df)} velas de 15m.")
    return filename

if __name__ == "__main__":
    # Ejemplo: Descargar el año 2023 completo
    download_historical_data(start_date="2023-01-01", end_date="2024-01-01")
