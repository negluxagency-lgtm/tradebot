import yfinance as yf
import pandas as pd
import numpy as np
import vectorbt as vbt
import itertools
import logging
from pathlib import Path

# Configuración básica
logging.basicConfig(level=logging.ERROR)
TICKER = "BTC-USD"

def compute_rsi(series, period=14):
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, float('inf'))
    return 100 - (100 / (1 + rs))

def run_opt():
    print("🚀 Iniciando Optimizador Antigravity...")
    
    # 1. Descarga de datos
    df_daily = yf.download(TICKER, period="400d", interval="1d", progress=False)
    df_daily.columns = [c[0] if isinstance(c, tuple) else c for c in df_daily.columns]
    
    df_15m = yf.download(TICKER, period="60d", interval="15m", progress=False)
    df_15m.columns = [c[0] if isinstance(c, tuple) else c for c in df_15m.columns]
    
    if df_15m.empty:
        print("Error: No hay datos")
        return

    # 2. Espacio de búsqueda de parámetros
    ema_periods = [20, 50, 100]
    rsi_bounds  = [(30, 70), (35, 65), (40, 60)]
    rr_ratios   = [1.5, 2.0, 2.5, 3.0]
    sl_pcts     = [0.003, 0.0038, 0.005] # 0.3%, 0.38%, 0.5%

    results = []

    # Pre-calcular RSI
    df_15m["RSI"] = compute_rsi(df_15m["Close"], 14)
    opens = df_15m["Open"].values
    closes_i = df_15m["Close"].values
    rsi_vals = df_15m["RSI"].values
    times = df_15m.index

    for ema_p, rsi_b, rr, sl in itertools.product(ema_periods, rsi_bounds, rr_ratios, sl_pcts):
        # Calcular EMA diaria
        df_daily["EMA"] = df_daily["Close"].ewm(span=ema_p, adjust=False).mean()
        
        # Mapa de tendencia diaria
        daily_map = {}
        d_closes = df_daily["Close"].values
        d_emas = df_daily["EMA"].values
        for j in range(2, len(df_daily)):
            date = df_daily.index[j].date()
            c0, c1, c2 = d_closes[j-2], d_closes[j-1], d_closes[j]
            ema = d_emas[j]
            
            trend = 0
            if c2 > c1 > c0 and c2 > ema: trend = 1
            elif c2 < c1 < c0 and c2 < ema: trend = -1
            daily_map[date] = trend

        # Generar señales
        entries_buy = np.zeros(len(df_15m), dtype=bool)
        entries_sell = np.zeros(len(df_15m), dtype=bool)
        
        rsi_low, rsi_high = rsi_b
        tp = sl * rr

        for i in range(2, len(df_15m)):
            date = times[i].date()
            trend = daily_map.get(date, 0)
            if trend == 0: continue
            
            rsi = rsi_vals[i-1]
            a_bull = closes_i[i-2] > opens[i-2]
            b_bull = closes_i[i-1] > opens[i-1]
            
            if trend == 1 and not a_bull and not b_bull and rsi < rsi_low:
                entries_buy[i] = True
            elif trend == -1 and a_bull and b_bull and rsi > rsi_high:
                entries_sell[i] = True

        if entries_buy.sum() + entries_sell.sum() == 0: continue

        # Backtest rápido con VectorBT
        pf = vbt.Portfolio.from_signals(
            close=df_15m["Close"],
            entries=entries_buy,
            short_entries=entries_sell,
            sl_stop=sl,
            tp_stop=tp,
            init_cash=10000,
            size=500,
            size_type="value",
            freq="15T"
        )
        
        # Calcular beneficios netos para cortos y largos
        pnl = pf.total_profit()
        trades = pf.trades.count()
        if trades == 0: continue

        results.append({
            "EMA": ema_p, "RSI_B": rsi_b, "RR": rr, "SL": sl,
            "PnL": pnl,
            "Trades": trades,
            "WinRate": pf.trades.win_rate(),
            "Sharpe": pf.sharpe_ratio()
        })

    # Mostrar Top 5
    if not results:
        print("No results found.")
        return
        
    res_df = pd.DataFrame(results).sort_values(by="PnL", ascending=False)
    print("\n🏆 TOP 5 CONFIGURACIONES OPTIMIZADAS (60 días):")
    print(res_df.head(5).to_string(index=False))

if __name__ == "__main__":
    run_opt()
