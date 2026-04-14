"""
Backtesting v3 - Antigravity EUR/USD London Session
Filtro Macro: EMA20 diaria (solo LONG si precio > EMA20, solo SHORT si < EMA20)
Períodos:
  - Corto (15m, 60 días): datos exactos de la estrategia
  - Largo  (1h,  1 año):  adaptación temporal para validar el concepto
───────────────────────────────────────────────────────────────────────────
"""
import sys
import json
import warnings
import logging
from pathlib import Path
from datetime import datetime

import numpy as np
import pandas as pd
import yfinance as yf
import vectorbt as vbt
import pytz

warnings.filterwarnings("ignore")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# ── Parámetros ─────────────────────────────────────────────────────────────
TICKER        = "BTC-USD"
TIMEZONE_ES   = pytz.timezone("Europe/Madrid")
TAKE_PROFIT   = 0.03     # 3.0%
STOP_LOSS     = 0.0215   # 2.15%
SESSION_START = 9
SESSION_END   = 14
EMA_PERIOD    = 20        # EMA macro bias filter
RSI_PERIOD    = 14        # Periodo RSI
RSI_BUY_MAX   = 45        # BUY solo si RSI < 45
RSI_SELL_MIN  = 55        # SELL solo si RSI > 55
INITIAL_CAP   = 10_000
ARTIFACTS_DIR = Path(__file__).parent.parent / "artifacts"
INTERVAL_5M   = "5m"
INTERVAL_15M  = "15m"
INTERVAL_1H   = "1h"


# ── 1. DESCARGA ─────────────────────────────────────────────────────────────
def download_data(interval: str, period: str = None, start: str = None, end: str = None):
    """Descarga datos de yfinance y los convierte a hora española."""
    if start and end:
        logger.info(f"[DATA] Descargando dates {start} a {end} ({interval}) BTC-USD...")
        df = yf.download(TICKER, start=start, end=end, interval=interval, progress=False)
    else:
        logger.info(f"[DATA] Descargando {period} de datos ({interval}) BTC-USD...")
        df = yf.download(TICKER, period=period, interval=interval, progress=False)
    
    df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]
    df.index = pd.to_datetime(df.index)
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    df.index = df.index.tz_convert(TIMEZONE_ES)
    logger.info(f"[DATA] {len(df)} velas descargadas.")
    return df

def load_binance_csv(file_path: str):
    """Carga datos desde el CSV generado por binance_fetcher.py."""
    logger.info(f"[DATA] Cargando datos locales desde {file_path}...")
    df = pd.read_csv(file_path, index_col=0, parse_dates=True)
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    df.index = df.index.tz_convert(TIMEZONE_ES)
    logger.info(f"[DATA] {len(df)} velas cargadas desde CSV.")
    return df

def download_daily(period: str = "400d"):
    """Descarga datos diarios para EMA macro y detección de tendencia."""
    logger.info(f"[DATA] Descargando datos diarios ({period})...")
    df = yf.download(TICKER, period=period, interval="1d", progress=False)
    df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]
    df.index = pd.to_datetime(df.index)
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    # Calcular EMA20
    df["EMA20"] = df["Close"].ewm(span=EMA_PERIOD, adjust=False).mean()
    logger.info(f"[DATA] {len(df)} velas diarias | EMA{EMA_PERIOD} calculada.")
    return df


# ── 2. RSI ──────────────────────────────────────────────────────────────────
def compute_rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """Calcula el RSI de Wilder sobre una serie de cierres."""
    delta = series.diff()
    gain  = delta.clip(lower=0)
    loss  = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    rs  = avg_gain / avg_loss.replace(0, float('inf'))
    rsi = 100 - (100 / (1 + rs))
    return rsi


# ── 3. SEÑALES ──────────────────────────────────────────────────────────────
def build_signals(df_intra: pd.DataFrame, df_daily: pd.DataFrame, label: str = ""):
    """
    Construye señales BUY/SELL aplicando:
      1. Tendencia diaria (1 cierre confirmando dirección + filtro EMA20)
      2. Gatillo Entrada (1 vela de 1h/15m contraria a la tendencia)
      3. Filtro Momentum (RSI 45/55)
    """
    logger.info(f"[SIGNALS] Construyendo señales {label} | RSI<{RSI_BUY_MAX} para BUY | RSI>{RSI_SELL_MIN} para SELL...")

    # Pre-calcular RSI sobre los cierres intradía
    rsi_series = compute_rsi(df_intra["Close"], RSI_PERIOD)
    rsi_values = rsi_series.values

    # Preparar mapa diario: fecha → {trend, ema20}
    daily_map = {}
    closes = df_daily["Close"].values
    ema20s = df_daily["EMA20"].values
    dates  = df_daily.index

    for i in range(1, len(dates)):
        date = dates[i].date()
        cprev = float(closes[i - 1])
        cthis = float(closes[i])
        ema  = float(ema20s[i])
        if cthis > cprev and cthis > ema:
            trend = 1
        elif cthis < cprev and cthis < ema:
            trend = -1
        else:
            trend = 0
        daily_map[date] = {"trend": trend, "ema20": ema, "close": cthis}

    # Construir arrays de señales
    n = len(df_intra)
    opens   = df_intra["Open"].values
    closes_i = df_intra["Close"].values
    indexes  = df_intra.index

    entries_buy  = np.zeros(n, dtype=bool)
    entries_sell = np.zeros(n, dtype=bool)
    macro_filter_blocked  = 0
    rsi_filter_blocked    = 0

    for i in range(2, n):
        ts   = indexes[i]
        date = ts.date()

        # [BTC] Filtros desactivados: Crypto opera 24/7 (sin sesión, sin descanso)

        day_data = daily_map.get(date)
        if not day_data:
            continue

        trend = day_data["trend"]
        ema20 = day_data["ema20"]
        price = closes_i[i]

        # Filtro 3: Tendencia diaria definida
        if trend == 0:
            continue

        # Filtro 4: Sesgo macro EMA20
        if trend == 1 and price < ema20:
            macro_filter_blocked += 1
            continue
        if trend == -1 and price > ema20:
            macro_filter_blocked += 1
            continue

        # Fase Ignición: Solo 1 vela contraria
        is_candle_bull = closes_i[i - 1] > opens[i - 1]
        current_rsi = rsi_values[i - 1]

        if np.isnan(current_rsi): # Evitar operar sin RSI inicial
            continue

        if trend == 1 and not is_candle_bull:
            if current_rsi < RSI_BUY_MAX:
                entries_buy[i] = True
            else:
                rsi_filter_blocked += 1
        elif trend == -1 and is_candle_bull:
            if current_rsi > RSI_SELL_MIN:
                entries_sell[i] = True
            else:
                rsi_filter_blocked += 1

    buy_n  = entries_buy.sum()
    sell_n = entries_sell.sum()
    logger.info(f"[SIGNALS] BUY: {buy_n} | SELL: {sell_n} | Total: {buy_n + sell_n} | Bloqueadas macro: {macro_filter_blocked} | Bloqueadas RSI: {rsi_filter_blocked}")
    return entries_buy, entries_sell


# ── 3. BACKTESTING ──────────────────────────────────────────────────────────
def run_backtest(df_intra, entries_buy, entries_sell, freq: str):
    price = df_intra["Close"]

    pf_long = vbt.Portfolio.from_signals(
        close=price, entries=entries_buy,
        exits=np.zeros(len(df_intra), dtype=bool),
        sl_stop=STOP_LOSS, tp_stop=TAKE_PROFIT,
        init_cash=INITIAL_CAP, size=1750, size_type="value",
        freq=freq, upon_opposite_entry="ignore"
    )
    pf_short = vbt.Portfolio.from_signals(
        close=price,
        entries=np.zeros(len(df_intra), dtype=bool),
        exits=np.zeros(len(df_intra), dtype=bool),
        short_entries=entries_sell,
        short_exits=np.zeros(len(df_intra), dtype=bool),
        sl_stop=STOP_LOSS, tp_stop=TAKE_PROFIT,
        init_cash=INITIAL_CAP, size=1750, size_type="value",
        freq=freq, upon_opposite_entry="ignore"
    )
    return pf_long, pf_short


# ── 4. MÉTRICAS ─────────────────────────────────────────────────────────────
def extract_metrics(pf_long, pf_short, entries_buy, entries_sell):
    metrics = {}
    for label, pf, entries in [("LONG", pf_long, entries_buy), ("SHORT", pf_short, entries_sell)]:
        trades = pf.trades.records_readable
        n = len(trades)
        if n == 0:
            metrics[label] = {"trades": 0}
            continue
        wins   = trades[trades["PnL"] > 0]
        losses = trades[trades["PnL"] <= 0]
        metrics[label] = {
            "trades":           n,
            "wins":             int(len(wins)),
            "losses":           int(len(losses)),
            "win_rate_pct":     round(len(wins) / n * 100, 2),
            "total_pnl_usd":    round(float(trades["PnL"].sum()), 4),
            "avg_win_usd":      round(float(wins["PnL"].mean()) if len(wins) > 0 else 0, 4),
            "avg_loss_usd":     round(float(losses["PnL"].mean()) if len(losses) > 0 else 0, 4),
            "max_drawdown_pct": round(float(pf.max_drawdown() * 100), 2),
            "final_value_usd":  round(float(pf.final_value()), 2),
            "total_return_pct": round(float(pf.total_return() * 100), 2),
            "sharpe_ratio":     round(float(pf.sharpe_ratio()), 4),
        }
    combined = (
        metrics.get("LONG", {}).get("total_pnl_usd", 0) +
        metrics.get("SHORT", {}).get("total_pnl_usd", 0)
    )
    metrics["COMBINED"] = {
        "total_trades":    (metrics.get("LONG", {}).get("trades", 0) +
                            metrics.get("SHORT", {}).get("trades", 0)),
        "total_pnl_usd":   round(combined, 4),
        "signal_triggers": int(entries_buy.sum() + entries_sell.sum()),
    }
    return metrics


# ── 5. REPORTE ──────────────────────────────────────────────────────────────
def print_report(metrics, title: str):
    print(f"\n{'='*58}")
    print(f"  {title}")
    print(f"  TP={TAKE_PROFIT*100:.2f}% | SL={STOP_LOSS*100:.2f}% | EMA{EMA_PERIOD} + RSI({RSI_PERIOD})<{RSI_BUY_MAX}/>{ RSI_SELL_MIN}")
    print(f"{'='*58}")
    for direction in ["LONG", "SHORT"]:
        m = metrics.get(direction, {})
        if m.get("trades", 0) == 0:
            print(f"\n  [{direction}] Sin operaciones.")
            continue
        sign = "[+]" if m["total_pnl_usd"] >= 0 else "[-]"
        print(f"""
  [{direction}] {'ALCISTA' if direction == 'LONG' else 'BAJISTA'} + retroceso
    Operaciones    : {m['trades']}
    Win Rate       : {m['win_rate_pct']:.1f}%  ({m['wins']}W / {m['losses']}L)
    PnL Total      : {sign} ${m['total_pnl_usd']:+.2f}
    Media Ganadora : ${m['avg_win_usd']:+.4f}
    Media Perdedora: ${m['avg_loss_usd']:+.4f}
    Drawdown Max   : {m['max_drawdown_pct']:.2f}%
    Retorno Total  : {m['total_return_pct']:+.2f}%
    Capital Final  : ${m['final_value_usd']:,.2f}
    Sharpe Ratio   : {m['sharpe_ratio']:.4f}""")
    c = metrics.get("COMBINED", {})
    totalpnl = c.get("total_pnl_usd", 0)
    sign = "[+]" if totalpnl >= 0 else "[-]"
    print(f"""
  [COMBINADO]
    Señales generadas : {c.get('signal_triggers', 0)}
    Operaciones reales: {c.get('total_trades', 0)}
    PnL Total         : {sign} ${totalpnl:+.2f} USD
{'='*58}""")


# ── MAIN ────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("\n[*] Antigravity Backtesting v3 — Filtro Macro EMA20 Activado")
    ARTIFACTS_DIR.mkdir(exist_ok=True)
    all_results = {}

    # Datos diarios compartidos (para EMA20 + tendencia diaria)
    df_daily = download_daily("400d")

    # ── BLOQUE 1: TEST DE ESTRÉS (720 días | 1h) ─────────────────────────────
    print("\n\n[IGNITION PHASE] ULTIMATE STRESS TEST | 720 días | 1h | $500 fixed")
    df_720h = download_data("1h", "720d")
    buy720, sell720 = build_signals(df_720h, download_daily("760d"), "1h/720d")
    if buy720.sum() + sell720.sum() > 0:
        pf_l720, pf_s720 = run_backtest(df_720h, buy720, sell720, "1h")
        m720 = extract_metrics(pf_l720, pf_s720, buy720, sell720)
        print_report(m720, "ESTRÉS 720d | 1h (Fase Ignición)")
        all_results["stress_test_720h"] = m720

    # ── BLOQUE 2: TEST DE FRECUENCIA (60 días | 15m) ──────────────────────────
    print("\n\n[IGNITION PHASE] FREQUENCY TEST | 60 días | 15m | $1,750 fixed")
    df_60m = download_data("15m", "60d")
    buy60, sell60 = build_signals(df_60m, download_daily("400d"), "15m/60d")
    if buy60.sum() + sell60.sum() > 0:
        pf_l60, pf_s60 = run_backtest(df_60m, buy60, sell60, "15T")
        m60 = extract_metrics(pf_l60, pf_s60, buy60, sell60)
        print_report(m60, "FRECUENCIA 60d | 15m ($1,750 fixed)")
        all_results["frequency_test_60m"] = m60

    # ── BLOQUE 4: HIGH FREQUENCY TEST (60 días | 5m | $1,750 fixed) ───────────
    print(f"\n\n[IGNITION PHASE] HIGH FREQUENCY | 60 días | 5m | $1,750 fixed")
    df_hfq = download_data(INTERVAL_5M, "60d") 
    df_daily_hfq = download_daily("400d") 
    buyH, sellH = build_signals(df_hfq, df_daily_hfq, "5m/60d")
    if buyH.sum() + sellH.sum() > 0:
        pf_lH, pf_sH = run_backtest(df_hfq, buyH, sellH, "5T")
        mH = extract_metrics(pf_lH, pf_sH, buyH, sellH)
        print_report(mH, "HIGH FREQUENCY 60d | 5m ($1,750 fixed)")
        all_results["high_frequency_test_5m"] = mH
    else:
        print("\n[SKIP] No se generaron señales para el High Frequency Test.")

    result_payload = {
        "timestamp":   datetime.now().isoformat(),
        "version":     "v4_macro_ema20_rsi",
        "parameters": {
            "take_profit": f"{TAKE_PROFIT*100:.2f}%",
            "stop_loss":   f"{STOP_LOSS*100:.2f}%",
            "ema_filter":  f"EMA{EMA_PERIOD} diaria",
            "session":     f"{SESSION_START}:00-{SESSION_END}:00 CET"
        },
        "results": all_results
    }
    json_path = ARTIFACTS_DIR / "backtest_v3_results.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(result_payload, f, indent=4, ensure_ascii=False)

    logger.info(f"[ARTIFACT] Guardado en: {json_path}")
    print(f"\n[OK] Artefactos en: artifacts/backtest_v3_results.json\n")
