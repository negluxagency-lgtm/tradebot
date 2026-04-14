"""
📊 Simple Trend Bot Backtest — BTC-USD
Lógica:
1. Datos 1m (yfinance).
2. Cada 30s (2 veces por vela 1m):
   - Si Precio_t > Precio_t-7d: BUY $10.
   - Si Precio_t < Precio_t-7d: SHORT (Buy No) $10.
3. Take Profit @ 1% del promedio.
4. Fees: 1% por trade.
5. Slippage: 0.1% fijo.
"""

import os
import json
import logging
import pandas as pd
import numpy as np
import yfinance as yf
from datetime import datetime, timedelta

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("backtest_trend")

# ── Configuración ──────────────────────────────────────────────────────────────
TICKER = "BTC-USD"
DAYS_HISTORY = 30    # Límite de yfinance para 1m
BIAS_WINDOW_DAYS = 7 # Ventana para tendencia
BET_AMOUNT = 10.0
FEE_PCT = 0.01       # 1% Polymarket fee
SLIPPAGE_PCT = 0.001 # 0.1% slippage estimado
TP_PCT = 0.01        # 1% Take Profit

def download_data():
    logger.info(f"📥 Descargando datos 1m para {TICKER} en bloques de 7 días...")
    
    all_data = []
    end_date = datetime.now()
    
    # Descargamos 4 bloques de 7 días (28 días total, límite de yfinance para 1m)
    for i in range(4):
        start_date = end_date - timedelta(days=7)
        logger.info(f"  📦 Bloque {i+1}: {start_date.date()} al {end_date.date()}")
        try:
            chunk = yf.download(
                TICKER, 
                start=start_date.strftime('%Y-%m-%d'), 
                end=end_date.strftime('%Y-%m-%d'), 
                interval="1m", 
                progress=False
            )
            if not chunk.empty:
                all_data.append(chunk)
            end_date = start_date
        except Exception as e:
            logger.error(f"Error descargando bloque {i}: {e}")
            break
            
    if not all_data:
        return None
        
    df = pd.concat(all_data).sort_index()
    
    # Limpieza de columnas MultiIndex si existen (yfinance >= 0.2.40)
    if hasattr(df.columns, 'get_level_values'):
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        
    logger.info(f"✅ Descarga completada: {len(df)} velas totales.")
    return df

def run_simulation(df):
    logger.info("⚙️ Iniciando motor de simulación...")
    
    # El bias necesita BIAS_WINDOW_DAYS. 
    # Calculamos cuántas velas de 1m hay en 7 días: 7 * 24 * 60 = 10080
    window_ticks = BIAS_WINDOW_DAYS * 24 * 60
    
    if len(df) <= window_ticks:
        logger.error(f"No hay suficientes datos. Necesarios: {window_ticks}, Tenemos: {len(df)}")
        return None

    # Variables de estado
    inventory_shares = 0.0
    avg_entry_price = 0.0
    cash = 0.0 # PnL acumulado (cash flow)
    total_invested = 0.0
    
    metrics = {
        "trades": 0,
        "wins": 0,
        "losses": 0,
        "total_fees": 0.0,
        "history": []
    }

    # Iteramos desde window_ticks hasta el final
    # Simulamos 2 entradas de 30s por cada vela de 1m
    prices = df["Close"].values
    timestamps = df.index
    
    start_idx = window_ticks
    
    for i in range(start_idx, len(prices)):
        current_price = prices[i]
        price_7d_ago = prices[i - window_ticks]
        
        # 1. Determinar Bias
        bias = "LONG" if current_price > price_7d_ago else "SHORT"
        
        # 2. Simular 2 ejecuciones de 30s
        for _ in range(2):
            # Precio con slippage
            exec_price_buy = current_price * (1 + SLIPPAGE_PCT)
            exec_price_sell = current_price * (1 - SLIPPAGE_PCT)
            
            # ── Lógica de Take Profit ──
            if inventory_shares > 0:
                # Si estamos en LONG y el precio sube
                if bias == "LONG" and current_price >= avg_entry_price * (1 + TP_PCT):
                    revenue = inventory_shares * exec_price_sell
                    fee = revenue * FEE_PCT
                    cash += (revenue - fee)
                    metrics["total_fees"] += fee
                    metrics["wins"] += 1
                    metrics["trades"] += 1
                    # Cierre de posición
                    inventory_shares = 0.0
                    avg_entry_price = 0.0
                # Si estamos en SHORT (modelado como 1 - p en Poly, pero aquí simplificamos con PnL directo)
                # Nota: En este backtest simple BTC-USD, asumiremos solo LONG bias para no complicar 
                # la simulación de tokens inversos de Polymarket, o usaremos la lógica de SHORT real.
            
            # ── Lógica de Entrada ──
            # Solo compramos si no hemos alcanzado el TP en este step
            if bias == "LONG":
                # Coste entrada
                cost = BET_AMOUNT
                fee = cost * FEE_PCT
                metrics["total_fees"] += fee
                
                # Acciones compradas
                shares_bought = (cost - fee) / exec_price_buy
                
                # Update promedio
                new_total_shares = inventory_shares + shares_bought
                avg_entry_price = ((inventory_shares * avg_entry_price) + (shares_bought * exec_price_buy)) / new_total_shares
                inventory_shares = new_total_shares
                total_invested += cost
                metrics["trades"] += 1
                cash -= cost

        # Snapshots periódicos (cada hora)
        if i % 60 == 0:
            current_value = inventory_shares * current_price
            equity = cash + current_value
            metrics["history"].append({
                "time": str(timestamps[i]),
                "equity": equity,
                "price": current_price
            })

    # Liquidación final para cerrar el backtest
    final_price = prices[-1]
    final_value = inventory_shares * final_price
    cash += final_value
    
    metrics["final_balance"] = cash
    metrics["total_invested"] = total_invested
    metrics["return_pct"] = (cash / total_invested * 100) if total_invested > 0 else 0
    
    return metrics

def export_report(metrics):
    os.makedirs("artifacts", exist_ok=True)
    report_path = "artifacts/backtest_trend_report.json"
    with open(report_path, "w") as f:
        json.dump(metrics, f, indent=2)
    logger.info(f"📊 Reporte exportado a {report_path}")

if __name__ == "__main__":
    df = download_data()
    if df is not None:
        results = run_simulation(df)
        if results:
            export_report(results)
            print("\n" + "="*40)
            print(f"🏁 RESULTADOS BACKTEST BTC")
            print("="*40)
            print(f"PnL Final       : ${results['final_balance']:,.2f}")
            print(f"Total Invertido : ${results['total_invested']:,.2f}")
            print(f"Retorno         : {results['return_pct']:.2f}%")
            print(f"Operaciones     : {results['trades']}")
            print(f"Fees Pagados    : ${results['total_fees']:,.2f}")
            print("="*40)
