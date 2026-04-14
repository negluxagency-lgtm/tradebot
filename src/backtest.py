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
TICKER        = "EURUSD=X"
TIMEZONE_ES   = pytz.timezone("Europe/Madrid")
TAKE_PROFIT   = 0.0076   # 0.76% (2:1 Ratio)
STOP_LOSS     = 0.0038   # 0.38%
SESSION_START = 9
SESSION_END   = 14
EMA_PERIOD    = 20        # EMA macro bias filter
RSI_PERIOD    = 14        # Periodo RSI
RSI_BUY_MAX   = 40        # BUY solo si RSI < 40 (sobreventa)
RSI_SELL_MIN  = 60        # SELL solo si RSI > 60 (sobrecompra)
INITIAL_CAP   = 10_000
ARTIFACTS_DIR = Path(__file__).parent.parent / "artifacts"


# ── 1. DESCARGA ─────────────────────────────────────────────────────────────
def download_data(interval: str, period: str):
    """Descarga datos de yfinance y los convierte a hora española."""
    logger.info(f"[DATA] Descargando {period} de datos ({interval}) EUR/USD...")
    df = yf.download(TICKER, period=period, interval=interval, progress=False)
    df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]
    df.index = pd.to_datetime(df.index)
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    df.index = df.index.tz_convert(TIMEZONE_ES)
    logger.info(f"[DATA] {len(df)} velas descargadas.")
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
      1. Filtro sesión Londres (09-14h CET)
      2. Tendencia diaria (2 cierres consecutivos del mismo signo)
      3. Filtro macro EMA20 (solo LONG si precio > EMA20, solo SHORT si < EMA20)
      4. Gatillo: 2 velas intradía consecutivas contrarias a la tendencia
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

    for i in range(2, len(dates)):
        date = dates[i].date()
        c0   = float(closes[i - 2])
        c1   = float(closes[i - 1])
        c2   = float(closes[i])
        ema  = float(ema20s[i])
        bull1 = c2 > c1
        bull2 = c1 > c0
        if bull1 and bull2:
            trend = 1
        elif not bull1 and not bull2:
            trend = -1
        else:
            trend = 0
        daily_map[date] = {"trend": trend, "ema20": ema, "close": c2}

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
        hour = ts.hour
        date = ts.date()

        # Filtro 1: Solo sesión de Londres
        if not (SESSION_START <= hour < SESSION_END):
            continue

        # Filtro 2: Sin fines de semana
        if ts.weekday() >= 5:
            continue

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

        # Filtro 5: 2 velas consecutivas contrarias a la tendencia
        a_bull = closes_i[i - 2] > opens[i - 2]
        b_bull = closes_i[i - 1] > opens[i - 1]

        # Filtro 6 NUEVO (Opcion C): Confirmacion RSI
        # BUY  solo si RSI < RSI_BUY_MAX  (zona de sobreventa)
        # SELL solo si RSI > RSI_SELL_MIN (zona de sobrecompra)
        current_rsi = rsi_values[i - 1]  # RSI de la ultima vela cerrada
        if np.isnan(current_rsi):
            continue

        if trend == 1 and not a_bull and not b_bull:
            if current_rsi < RSI_BUY_MAX:
                entries_buy[i] = True
            else:
                rsi_filter_blocked += 1
        elif trend == -1 and a_bull and b_bull:
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
        init_cash=INITIAL_CAP, size=0.95, size_type="percent",
        freq=freq, upon_opposite_entry="ignore"
    )
    pf_short = vbt.Portfolio.from_signals(
        close=price,
        entries=np.zeros(len(df_intra), dtype=bool),
        exits=np.zeros(len(df_intra), dtype=bool),
        short_entries=entries_sell,
        short_exits=np.zeros(len(df_intra), dtype=bool),
        sl_stop=STOP_LOSS, tp_stop=TAKE_PROFIT,
        init_cash=INITIAL_CAP, size=0.95, size_type="percent",
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

    # ── TEST 1: 15m / 60 días (exacto) ──────────────────────────────────────
    print("\n\n[PERIODO 1] 15m | Ultimos 60 dias")
    df_15m = download_data("15m", "60d")
    buy15, sell15 = build_signals(df_15m, df_daily, "15m/60d")
    if buy15.sum() + sell15.sum() > 0:
        pf_l15, pf_s15 = run_backtest(df_15m, buy15, sell15, "15T")
        m15 = extract_metrics(pf_l15, pf_s15, buy15, sell15)
        print_report(m15, "BACKTESTING 15m | Ultimos 60 dias")
        all_results["15m_60d"] = m15
    else:
        print("  Sin señales en el período.")

    # ── TEST 2: 1h / 1 año (concepto extendido) ─────────────────────────────
    print("\n\n[PERIODO 2] 1h | Ultimo 1 ano (concepto extendido)")
    df_1h = download_data("1h", "365d")
    buy1h, sell1h = build_signals(df_1h, df_daily, "1h/1y")
    if buy1h.sum() + sell1h.sum() > 0:
        pf_l1h, pf_s1h = run_backtest(df_1h, buy1h, sell1h, "1h")
        m1h = extract_metrics(pf_l1h, pf_s1h, buy1h, sell1h)
        print_report(m1h, "BACKTESTING 1h | Ultimo 1 ano (~sesion Londres)")
        all_results["1h_1y"] = m1h
    else:
        print("  Sin señales en el período.")

    # ── TEST 3: 1h / 2 años ─────────────────────────────────────────────────
    print("\n\n[PERIODO 3] 1h | Ultimos 2 anos")
    df_2y = download_data("1h", "730d")
    buy2y, sell2y = build_signals(df_2y, download_daily("760d"), "1h/2y")
    if buy2y.sum() + sell2y.sum() > 0:
        pf_l2y, pf_s2y = run_backtest(df_2y, buy2y, sell2y, "1h")
        m2y = extract_metrics(pf_l2y, pf_s2y, buy2y, sell2y)
        print_report(m2y, "BACKTESTING 1h | Ultimos 2 anos")
        all_results["1h_2y"] = m2y
    else:
        print("  Sin señales en el período.")

    # ── Materializar artefacto JSON ─────────────────────────────────────────
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
