"""
Motor de Estrategia - Antigravity Trading Bot
Implementa las reglas de:
  - Detección de tendencia diaria (2 velas cerradas del mismo signo)
  - Señal de entrada 15m (2 velas contrarias consecutivas)
  - Cálculo de TP y SL
"""
import pandas as pd
import logging
from dataclasses import dataclass
from enum import Enum
from typing import Optional

logger = logging.getLogger(__name__)

# ── Constantes de la estrategia (Fase de Ignición - Escudo de Oro) ──
TAKE_PROFIT_PCT = 0.03     # 3.0%
STOP_LOSS_PCT   = 0.0215   # 2.15%


class Trend(Enum):
    BULLISH = "ALCISTA"
    BEARISH = "BAJISTA"
    NEUTRAL = "NEUTRAL"


class Signal(Enum):
    BUY  = "COMPRAR"
    SELL = "VENDER"
    NONE = "NINGUNA"


@dataclass
class TradeSetup:
    signal:       Signal
    trend:        Trend
    entry_price:  float
    take_profit:  float
    stop_loss:    float
    reason:       str

    def __str__(self):
        return (
            f"[SETUP] Señal={self.signal.value} | Tendencia={self.trend.value} | "
            f"Entrada={self.entry_price:.5f} | TP={self.take_profit:.5f} | "
            f"SL={self.stop_loss:.5f} | {self.reason}"
        )


# ────────────────────────────────────────────────────────────────────────────
def detect_daily_trend(daily_df: pd.DataFrame) -> Trend:
    """
    Analiza la última vela cerrada y el sesgo macro EMA20 (Fase Ignición).
    Alcista  → Última vela al alza + Precio > EMA20.
    Bajista  → Última vela a la baja + Precio < EMA20.
    Neutral  → Bloqueado por EMA20.
    """
    if daily_df is None or len(daily_df) < 2:
        logger.warning("[STRATEGY] Datos diarios insuficientes para detectar tendencia.")
        return Trend.NEUTRAL

    closes = daily_df["Close"].values
    emas   = daily_df["EMA20"].values

    # Fase Ignición: Solo 1 vela de confirmación (la más reciente)
    vela_bull = closes[-1] > closes[-2]

    precio_actual = closes[-1]
    ema_actual    = emas[-1]

    if vela_bull:
        if precio_actual > ema_actual:
            trend = Trend.BULLISH
        else:
            trend = Trend.NEUTRAL
            logger.info("[STRATEGY] Tendencia ALCISTA cancelada por filtro macro (Precio < EMA20).")
    elif not vela_bull:
        if precio_actual < ema_actual:
            trend = Trend.BEARISH
        else:
            trend = Trend.NEUTRAL
            logger.info("[STRATEGY] Tendencia BAJISTA cancelada por filtro macro (Precio > EMA20).")
    else:
        trend = Trend.NEUTRAL

    logger.info(
        f"[STRATEGY] Tendencia diaria (1 vela): {trend.value} | "
        f"Cierres: {closes[-2]:.2f} → {closes[-1]:.2f} | EMA20: {ema_actual:.2f}"
    )
    return trend


def detect_entry_signal(candles_15m: pd.DataFrame, trend: Trend) -> Signal:
    """
    Analiza la última vela de 15m cerrada (Fase Ignición).
    BUY  → Tendencia ALCISTA + última vela bajista + RSI < 45 (mejor retroceso).
    SELL → Tendencia BAJISTA + última vela alcista + RSI > 55 (mejor rebote).
    """
    if trend == Trend.NEUTRAL:
        logger.info("[STRATEGY] Tendencia NEUTRAL — sin señal de entrada.")
        return Signal.NONE

    if candles_15m is None or len(candles_15m) < 1 or "RSI" not in candles_15m.columns:
        logger.warning("[STRATEGY] Datos 15m insuficientes o falta RSI.")
        return Signal.NONE

    last_candle = candles_15m.iloc[-1]
    open_p  = last_candle["Open"]
    close_p = last_candle["Close"]
    rsi_p   = last_candle["RSI"]

    is_vela_bull = close_p > open_p
    current_rsi = rsi_p

    logger.info(
        f"[STRATEGY] Última vela 15m → "
        f"{'▲' if is_vela_bull else '▼'} ({open_p:.2f}→{close_p:.2f}) | "
        f"RSI: {current_rsi:.1f}"
    )

    # Fase Ignición: Solo 1 vela contraria para entrar
    if trend == Trend.BULLISH and not is_vela_bull:
        if current_rsi < 45:
            logger.info("[STRATEGY] Señal: COMPRAR (retroceso 1x15m + RSI<45 confirmado)")
            return Signal.BUY
        else:
            logger.info(f"[STRATEGY] Señal COMPRAR bloqueada por RSI ({current_rsi:.1f} >= 45)")
            return Signal.NONE

    if trend == Trend.BEARISH and is_vela_bull:
        if current_rsi > 55:
            logger.info("[STRATEGY] Señal: VENDER (rebote 1x15m + RSI>55 confirmado)")
            return Signal.SELL
        else:
            logger.info(f"[STRATEGY] Señal VENDER bloqueada por RSI ({current_rsi:.1f} <= 55)")
            return Signal.NONE

    logger.info("[STRATEGY] Sin patrón de entrada activado en este ciclo.")
    return Signal.NONE


def build_trade_setup(signal: Signal, trend: Trend, entry_price: float) -> Optional[TradeSetup]:
    """
    Construye el objeto TradeSetup con los niveles de TP y SL.
    """
    if signal == Signal.NONE or entry_price <= 0:
        return None

    if signal == Signal.BUY:
        tp = entry_price * (1 + TAKE_PROFIT_PCT)
        sl = entry_price * (1 - STOP_LOSS_PCT)
    else:  # SELL
        tp = entry_price * (1 - TAKE_PROFIT_PCT)
        sl = entry_price * (1 + STOP_LOSS_PCT)

    setup = TradeSetup(
        signal=signal,
        trend=trend,
        entry_price=entry_price,
        take_profit=round(tp, 5),
        stop_loss=round(sl, 5),
        reason=f"Retroceso 1x15m contra tendencia {trend.value}"
    )
    logger.info(str(setup))
    return setup


def check_exit(setup: TradeSetup, current_price: float) -> Optional[str]:
    """
    Verifica si el precio actual ha alcanzado el TP o el SL.
    Retorna: 'TP', 'SL', o None.
    """
    if setup.signal == Signal.BUY:
        if current_price >= setup.take_profit:
            return "TP"
        if current_price <= setup.stop_loss:
            return "SL"
    elif setup.signal == Signal.SELL:
        if current_price <= setup.take_profit:
            return "TP"
        if current_price >= setup.stop_loss:
            return "SL"
    return None
