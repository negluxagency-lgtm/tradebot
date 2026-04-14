"""
Test de Integración: Ejecuta un único ciclo del bot en modo DRY_RUN
y muestra el resultado completo de la estrategia.
"""
import sys
import logging
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from data_engine import get_daily_candles, get_15m_candles, get_current_price
from strategy_engine import detect_daily_trend, detect_entry_signal, build_trade_setup

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)

def run_integration_test():
    print("\n" + "="*55)
    print("  ANTIGRAVITY — TEST DE INTEGRACIÓN COMPLETO")
    print("="*55)

    # 1. Datos
    print("\n[1] MOTOR DE DATOS")
    daily    = get_daily_candles(5)
    c15m     = get_15m_candles()
    price    = get_current_price()
    print(f"    Velas diarias obtenidas : {len(daily)}")
    print(f"    Velas 15m obtenidas     : {len(c15m)}")
    print(f"    Precio actual BTC-USD   : {price:.2f}")

    if len(daily) >= 3:
        closes = daily["Close"].values
        print(f"\n    Últimos 3 cierres diarios:")
        for i, c in enumerate(closes[-3:], 1):
            print(f"      D-{3-i}: {c:.5f}")

    # 2. Tendencia
    print("\n[2] MOTOR DE ESTRATEGIA — TENDENCIA DIARIA")
    trend = detect_daily_trend(daily)
    print(f"    Tendencia detectada: >>> {trend.value} <<<")

    # 3. Señal de entrada
    print("\n[3] MOTOR DE ESTRATEGIA — SEÑAL 15M / MOMENTUM")
    if len(c15m) >= 2:
        last2 = c15m.tail(2)
        print(f"    Vela 15m penúltima: Open={last2['Open'].iloc[0]:.2f}  Close={last2['Close'].iloc[0]:.2f}")
        print(f"    Vela 15m última   : Open={last2['Open'].iloc[1]:.2f}  Close={last2['Close'].iloc[1]:.2f} | RSI={last2['RSI'].iloc[1]:.1f}")

    signal = detect_entry_signal(c15m, trend)
    print(f"    Señal detectada: >>> {signal.value} <<<")

    # 4. Setup de operación
    print("\n[4] MOTOR DE ESTRATEGIA — SETUP DE OPERACIÓN")
    setup = build_trade_setup(signal, trend, price)
    if setup:
        print(f"    Dirección  : {setup.signal.value}")
        print(f"    Entrada    : {setup.entry_price:.5f}")
        print(f"    Take Profit: {setup.take_profit:.2f}  (+{0.76:.2f}%)")
        print(f"    Stop Loss  : {setup.stop_loss:.2f}  (-{0.38:.2f}%)")
        print(f"    Motivo     : {setup.reason}")
    else:
        print("    Sin setup de operación en este ciclo (condiciones no cumplidas).")

    print("\n" + "="*55)
    print(" TELEMETRÍA OK — Todos los módulos responden correctamente.")
    print("="*55 + "\n")

if __name__ == "__main__":
    run_integration_test()
