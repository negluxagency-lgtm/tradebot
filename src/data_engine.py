"""
Motor de Datos - Antigravity Trading Bot
Fuente: yfinance (EURUSD=X)
Provee velas diarias y de 15m para el motor de estrategia.
"""
import yfinance as yf
import pandas as pd
from datetime import datetime, timedelta
import pytz
import logging

logger = logging.getLogger(__name__)

TICKER = "BTC-USD"
TIMEZONE_ES = pytz.timezone("Europe/Madrid")


def compute_rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """Calcula el RSI de Wilder sobre una serie continua."""
    delta = series.diff()
    gain  = delta.clip(lower=0)
    loss  = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, float('inf'))
    return 100 - (100 / (1 + rs))

def get_daily_candles(n: int = 5) -> pd.DataFrame:
    """
    Obtiene las últimas N velas diarias cerradas de EUR/USD.
    Retorna un DataFrame con columnas: Open, High, Low, Close, Volume
    """
    try:
        df = yf.download(TICKER, period="10d", interval="1d", progress=False)
        if df.empty:
            logger.error("[DATA] No se obtuvieron velas diarias.")
            return pd.DataFrame()

        # Eliminar la vela del día actual (puede estar incompleta)
        now_utc = datetime.now(pytz.utc)
        df.index = pd.to_datetime(df.index)
        if df.index.tz is None:
            df.index = df.index.tz_localize("UTC")

        # Solo velas completamente cerradas (anteriores a hoy)
        today = now_utc.date()
        df = df[df.index.date < today]

        df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]

        # Calcular EMA20
        df["EMA20"] = df["Close"].ewm(span=20, adjust=False).mean()

        result = df.tail(n)
        logger.info(f"[DATA] {len(result)} velas diarias | EMA20 calculada.")
        return result

    except Exception as e:
        logger.error(f"[DATA] Error obteniendo velas diarias: {e}")
        return pd.DataFrame()


def get_15m_candles(lookback_hours: int = 6) -> pd.DataFrame:
    """
    Obtiene las velas de 15 minutos de las últimas N horas.
    Solo retorna velas completamente cerradas.
    """
    try:
        df = yf.download(TICKER, period="5d", interval="15m", progress=False)
        if df.empty:
            logger.error("[DATA] No se obtuvieron velas de 15m.")
            return pd.DataFrame()

        df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]
        df.index = pd.to_datetime(df.index)

        if df.index.tz is None:
            df.index = df.index.tz_localize("UTC")

        # Convertir a hora española
        df.index = df.index.tz_convert(TIMEZONE_ES)

        # Calcular RSI(14) sobre todo el histórico reciente para evitar saltos
        df["RSI"] = compute_rsi(df["Close"], 14)

        # Eliminar la vela actual (incompleta)
        now_es = datetime.now(TIMEZONE_ES)
        df = df[df.index < now_es.replace(second=0, microsecond=0)]

        logger.info(f"[DATA] {len(df)} velas 15m obtenidas | Último RSI: {df['RSI'].iloc[-1]:.1f}")
        return df

    except Exception as e:
        logger.error(f"[DATA] Error obteniendo velas 15m: {e}")
        return pd.DataFrame()


def get_5m_candles(lookback_hours: int = 6) -> pd.DataFrame:
    """
    Obtiene las velas de 5 minutos de las últimas N horas.
    Solo retorna velas completamente cerradas.
    """
    try:
        df = yf.download(TICKER, period="5d", interval="5m", progress=False)
        if df.empty:
            logger.error("[DATA] No se obtuvieron velas de 5m.")
            return pd.DataFrame()

        df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]
        df.index = pd.to_datetime(df.index)

        if df.index.tz is None:
            df.index = df.index.tz_localize("UTC")

        # Convertir a hora española
        df.index = df.index.tz_convert(TIMEZONE_ES)

        # Calcular RSI(14) sobre todo el histórico reciente para evitar saltos
        df["RSI"] = compute_rsi(df["Close"], 14)

        # Eliminar la vela actual (incompleta)
        now_es = datetime.now(TIMEZONE_ES)
        df = df[df.index < now_es.replace(second=0, microsecond=0)]

        logger.info(f"[DATA] {len(df)} velas 5m obtenidas | Último RSI: {df['RSI'].iloc[-1]:.1f}")
        return df

    except Exception as e:
        logger.error(f"[DATA] Error obteniendo velas 5m: {e}")
        return pd.DataFrame()


def get_current_price() -> float:
    """Obtiene el precio actual del par EUR/USD."""
    try:
        ticker = yf.Ticker(TICKER)
        data = ticker.history(period="1d", interval="1m")
        if data.empty:
            return 0.0
        price = float(data["Close"].iloc[-1])
        logger.info(f"[DATA] Precio actual BTC-USD: {price:.5f}")
        return price
    except Exception as e:
        logger.error(f"[DATA] Error obteniendo precio actual: {e}")
        return 0.0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    print("=== TEST DATA ENGINE (BTC 24/7) ===")
    daily = get_daily_candles(5)
    print(f"Velas diarias:\n{daily[['Open', 'Close', 'EMA20']].tail()}\n")
    candles_15m = get_15m_candles()
    print(f"Últimas 4 velas 15m:\n{candles_15m[['Open', 'Close', 'RSI']].tail(4)}\n")
    candles_5m = get_5m_candles()
    print(f"Últimas 4 velas 5m:\n{candles_5m[['Open', 'Close', 'RSI']].tail(4)}\n")
    print(f"Precio actual BTC-USD: {get_current_price():.2f}")
