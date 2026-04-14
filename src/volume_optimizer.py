import yfinance as yf
import pandas as pd
import numpy as np
import vectorbt as vbt
import itertools
from datetime import datetime

TICKER = "BTC-USD"

def compute_rsi(series, period=14):
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, float('inf'))
    return 100 - (100 / (1 + rs))

def run_systemic_opt():
    print("🚀 Iniciando Optimizador Sistémico (Volumen y Estrategia)...")
    
    df_daily = yf.download(TICKER, period="400d", interval="1d", progress=False)
    df_daily.columns = [c[0] if isinstance(c, tuple) else c for c in df_daily.columns]
    
    df_15m = yf.download(TICKER, period="60d", interval="15m", progress=False)
    df_15m.columns = [c[0] if isinstance(c, tuple) else c for c in df_15m.columns]
    
    # Pre-calcular RSI e indicadores base
    df_15m["RSI"] = compute_rsi(df_15m["Close"], 14)
    opens = df_15m["Open"].values
    closes_i = df_15m["Close"].values
    rsi_vals = df_15m["RSI"].values
    times = df_15m.index

    # Espacio de búsqueda sistémico
    daily_trend_rules = ["2candles+EMA", "1candle+EMA", "OnlyEMA"]
    contrary_candles_rules = [1, 2]
    rsi_thresholds = [40, 45, 50, None] # None means no RSI filter
    
    # Fijamos los mejores TP/SL encontrados antes para comparar manzanas con manzanas
    tp, sl = 0.03, 0.0215 

    results = []

    for d_rule, c_rule, rsi_t in itertools.product(daily_trend_rules, contrary_candles_rules, rsi_thresholds):
        # 1. Mapa de tendencia diaria diario
        df_daily["EMA20"] = df_daily["Close"].ewm(span=20, adjust=False).mean()
        daily_map = {}
        d_closes = df_daily["Close"].values
        d_emas = df_daily["EMA20"].values
        
        for j in range(2, len(df_daily)):
            date = df_daily.index[j].date()
            c0, c1, c2 = d_closes[j-2], d_closes[j-1], d_closes[j]
            ema = d_emas[j]
            
            trend = 0
            if d_rule == "2candles+EMA":
                if c2 > c1 > c0 and c2 > ema: trend = 1
                elif c2 < c1 < c0 and c2 < ema: trend = -1
            elif d_rule == "1candle+EMA":
                if c2 > c1 and c2 > ema: trend = 1
                elif c2 < c1 and c2 < ema: trend = -1
            elif d_rule == "OnlyEMA":
                if c2 > ema: trend = 1
                else: trend = -1
            daily_map[date] = trend

        # 2. Generación de señales
        entries_buy = np.zeros(len(df_15m), dtype=bool)
        entries_sell = np.zeros(len(df_15m), dtype=bool)

        for i in range(2, len(df_15m)):
            date = times[i].date()
            trend = daily_map.get(date, 0)
            if trend == 0: continue
            
            rsi = rsi_vals[i-1]
            # Velas contrarias (retroceso)
            if c_rule == 2:
                is_pullback_buy = (closes_i[i-1] < opens[i-1]) and (closes_i[i-2] < opens[i-2])
                is_pullback_sell = (closes_i[i-1] > opens[i-1]) and (closes_i[i-2] > opens[i-2])
            else:
                is_pullback_buy = (closes_i[i-1] < opens[i-1])
                is_pullback_sell = (closes_i[i-1] > opens[i-1])

            # Filtro RSI
            pass_rsi_buy = True if rsi_t is None else (rsi < rsi_t)
            pass_rsi_sell = True if rsi_t is None else (rsi > (100 - rsi_t))

            if trend == 1 and is_pullback_buy and pass_rsi_buy:
                entries_buy[i] = True
            elif trend == -1 and is_pullback_sell and pass_rsi_sell:
                entries_sell[i] = True

        if entries_buy.sum() + entries_sell.sum() == 0: continue

        pf = vbt.Portfolio.from_signals(
            close=df_15m["Close"], entries=entries_buy, short_entries=entries_sell,
            sl_stop=sl, tp_stop=tp, init_cash=10000, size=500, size_type="value", freq="15T"
        )
        
        results.append({
            "TrendRule": d_rule, "Contrary#": c_rule, "RSI_T": rsi_t,
            "PnL": pf.total_profit(),
            "Trades": pf.trades.count(),
            "WinRate": pf.trades.win_rate(),
            "Sharpe": pf.sharpe_ratio()
        })

    res_df = pd.DataFrame(results).sort_values(by="PnL", ascending=False)
    print("\n🏆 OPTIMIZACIÓN DE VOLUMEN (Ordenado por PnL):")
    print(res_df.to_string(index=False))

if __name__ == "__main__":
    run_systemic_opt()
