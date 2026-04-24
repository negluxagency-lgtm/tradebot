"""
Backtest: Piecewise Linear Interpolation Strategy
Simula $5,000 de capital copiando a la cartera objetivo usando
una función de interpolación lineal por tramos para el sizing.
"""
import json
import sys
import io
import os

if sys.stdout.encoding != 'utf-8':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

# ── Puntos de calibración de la curva de sizing ───────────────────────────────
BREAKPOINTS = [
    (1,    1),
    (10,   7),
    (20,   16),
    (50,   30),
    (100,  70),
    (500,  250),
    (1000, 500),
    (2000, 700),
]

def piecewise_copy_size(whale_usdc: float) -> float:
    """
    Calcula el tamaño de nuestra copia usando interpolación lineal por tramos.
    Por encima de $2000 aplicamos un 33% fijo.
    """
    if whale_usdc <= 0:
        return 0.0
    if whale_usdc < BREAKPOINTS[0][0]:
        return whale_usdc  # proporcional 1:1 para importes ínfimos
    if whale_usdc > 2000:
        return round(whale_usdc * 0.33, 2)

    for i in range(len(BREAKPOINTS) - 1):
        x0, y0 = BREAKPOINTS[i]
        x1, y1 = BREAKPOINTS[i + 1]
        if x0 <= whale_usdc <= x1:
            t = (whale_usdc - x0) / (x1 - x0)
            return round(y0 + t * (y1 - y0), 2)

    return BREAKPOINTS[-1][1]


def load_whale_data(path="artifacts/whale_analysis.json"):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def backtest():
    print("=" * 65)
    print("  BACKTEST: Piecewise Linear Sizing — Capital Simulado $5,000")
    print("=" * 65)

    # Verificar el archivo de datos
    if not os.path.exists("artifacts/whale_analysis.json"):
        print("ERROR: Ejecuta primero src/analyze_whale_trades.py")
        return

    data = load_whale_data()
    trades = data.get("raw_trades", [])
    if not trades:
        print("Sin datos de trades del objetivo.")
        return

    # ── Agrupar trades por mercado para calcular resultado ───────────────────
    markets = {}
    for t in trades:
        key  = t.get("conditionId") or t.get("market", "unknown")
        name = t.get("title", "Desconocido")
        side = t.get("side", "BUY").upper()
        usdc = float(t.get("usdcSize", 0) or 0)
        if key not in markets:
            markets[key] = {"name": name, "buy": 0.0, "sell": 0.0, "trades": 0}
        markets[key]["trades"] += 1
        if side == "BUY":
            markets[key]["buy"] += usdc
        else:
            markets[key]["sell"] += usdc

    # ── Simular nuestra posición en cada mercado ─────────────────────────────
    INITIAL_CAPITAL = 5000.0
    CIRCUIT_BREAKER = 150.0   # Detener si bajamos de aquí

    capital = INITIAL_CAPITAL
    total_invested = 0.0
    total_recovered = 0.0
    wins = 0
    losses = 0
    skipped_capital = 0
    skipped_breaker = 0
    trade_log = []

    for mkt_id, mkt in sorted(markets.items(), key=lambda x: x[1]["buy"], reverse=True):
        whale_buy  = mkt["buy"]
        whale_sell = mkt["sell"]

        if whale_buy <= 0:
            continue

        # Nuestra inversión según la curva
        our_size = piecewise_copy_size(whale_buy)

        # Circuit breaker
        if capital <= CIRCUIT_BREAKER:
            skipped_breaker += 1
            continue

        # No invertir más de lo que tenemos
        if our_size > capital:
            our_size = capital
            skipped_capital += 1

        # Calcular resultado del mercado: ratio = sell/buy del objetivo
        # Si no hubo SELL aún → mercado todavía abierto (lo excluimos del PnL)
        if whale_sell == 0:
            # Posición abierta → no sabemos el resultado, omitir del PnL
            continue

        result_ratio = whale_sell / whale_buy  # >1 = ganó, <1 = perdió

        # Nuestro retorno
        our_recovered = round(our_size * result_ratio, 2)
        our_pnl = round(our_recovered - our_size, 2)

        capital += our_pnl
        total_invested  += our_size
        total_recovered += our_recovered

        if our_pnl >= 0:
            wins += 1
        else:
            losses += 1

        trade_log.append({
            "market":        mkt["name"][:50],
            "whale_buy":     whale_buy,
            "whale_sell":    whale_sell,
            "result_ratio":  round(result_ratio, 3),
            "our_size":      our_size,
            "our_recovered": our_recovered,
            "our_pnl":       our_pnl,
            "capital_after": round(capital, 2)
        })

    # ── Resultados Globales ──────────────────────────────────────────────────
    total_markets = len([m for m in markets.values() if m["buy"] > 0])
    final_pnl     = round(capital - INITIAL_CAPITAL, 2)
    roi_pct       = round(final_pnl / INITIAL_CAPITAL * 100, 2)

    print(f"\n  CAPITAL INICIAL       : ${INITIAL_CAPITAL:,.2f}")
    print(f"  CAPITAL FINAL         : ${capital:,.2f}")
    print(f"  PnL NETO SIMULADO     : ${final_pnl:+,.2f}")
    print(f"  ROI                   : {roi_pct:+.2f}%")
    print(f"\n  OPERACIONES SIMULADAS : {wins + losses}")
    print(f"  Ganadas               : {wins}")
    print(f"  Perdidas              : {losses}")
    print(f"  Winrate               : {wins/(wins+losses)*100:.1f}%" if (wins+losses) > 0 else "  Winrate: N/A")
    print(f"  Mercados abiertos (excluidos del PnL): {sum(1 for m in markets.values() if m['buy']>0 and m['sell']==0)}")
    print(f"  Skipped (circuit breaker)            : {skipped_breaker}")
    print(f"  Ajustados por capital insuficiente   : {skipped_capital}")

    # ── Verificación de la curva ─────────────────────────────────────────────
    print(f"\n  TABLA DE CONVERSION (Piecewise Linear)")
    print(f"  {'Ballena pone':>14}  {'Nosotros ponemos':>18}  {'Ratio':>8}")
    print(f"  {'-'*14}  {'-'*18}  {'-'*8}")
    test_values = [1, 5, 10, 15, 20, 35, 50, 75, 100, 200, 500, 750, 1000, 1500, 2000, 3000, 5000]
    for v in test_values:
        our = piecewise_copy_size(v)
        ratio = our / v * 100
        print(f"  ${v:>13,.0f}  ${our:>17,.2f}  {ratio:>7.1f}%")

    # ── Top operaciones ──────────────────────────────────────────────────────
    print(f"\n  TOP 10 OPERACIONES POR IMPACTO")
    top_trades = sorted(trade_log, key=lambda x: abs(x["our_pnl"]), reverse=True)[:10]
    print(f"  {'Mercado':<45} {'Whale':>8}  {'Nuestro':>8}  {'PnL':>8}")
    print(f"  {'-'*45} {'-'*8}  {'-'*8}  {'-'*8}")
    for t in top_trades:
        pnl_str = f"${t['our_pnl']:+,.2f}"
        print(f"  {t['market']:<45} ${t['whale_buy']:>7,.0f}  ${t['our_size']:>7,.2f}  {pnl_str:>8}")

    # ── Guardar artefacto ────────────────────────────────────────────────────
    output = {
        "strategy": "piecewise_linear_interpolation",
        "initial_capital": INITIAL_CAPITAL,
        "final_capital": round(capital, 2),
        "pnl": final_pnl,
        "roi_pct": roi_pct,
        "wins": wins,
        "losses": losses,
        "total_invested": round(total_invested, 2),
        "total_recovered": round(total_recovered, 2),
        "breakpoints": BREAKPOINTS,
        "trade_log": trade_log
    }
    out_path = "artifacts/backtest_piecewise.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    print(f"\n  Artefacto materializado: {out_path}")
    print("=" * 65)


if __name__ == "__main__":
    backtest()
