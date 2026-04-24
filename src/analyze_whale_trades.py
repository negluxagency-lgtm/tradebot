import requests
import json
import sys
import io
from datetime import datetime, timezone

if sys.stdout.encoding != 'utf-8':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

WALLET = "0xe1d6b51521bd4365769199f392f9818661bd907c"
DATA_API = "https://data-api.polymarket.com"

def fetch_all_activity():
    """Extrae TODA la actividad de la cartera paginando hasta el final."""
    all_trades = []
    offset = 0
    limit = 100
    print(f"Extrayendo actividad de {WALLET}...")

    while True:
        resp = requests.get(
            f"{DATA_API}/activity",
            params={"user": WALLET, "limit": limit, "offset": offset},
            timeout=15
        )
        if resp.status_code != 200:
            print(f"ERROR {resp.status_code}: {resp.text}")
            break
        
        batch = resp.json()
        if not batch or not isinstance(batch, list):
            break
        
        all_trades.extend(batch)
        print(f"  -> Página offset={offset}: {len(batch)} trades (Total: {len(all_trades)})")
        
        if len(batch) < limit:
            break
        offset += limit

    return all_trades

def analyze(trades):
    """Analiza las operaciones y calcula métricas reales."""
    if not trades:
        print("No hay trades que analizar.")
        return

    total_usdc_in = 0.0
    total_usdc_out = 0.0
    trade_sizes = []
    markets_traded = {}
    buy_count = 0
    sell_count = 0

    for t in trades:
        side      = t.get("side", "BUY").upper()
        usdc_size = float(t.get("usdcSize", 0) or 0)
        price     = float(t.get("price", 0) or 0)
        size      = float(t.get("size", 0) or 0)
        market    = t.get("title", "Desconocido")
        ts        = t.get("timestamp", 0)

        if side == "BUY":
            total_usdc_in += usdc_size
            buy_count += 1
        else:
            total_usdc_out += usdc_size
            sell_count += 1

        trade_sizes.append(usdc_size)

        if market not in markets_traded:
            markets_traded[market] = {"buy_usdc": 0, "sell_usdc": 0, "trades": 0}
        markets_traded[market]["trades"] += 1
        if side == "BUY":
            markets_traded[market]["buy_usdc"] += usdc_size
        else:
            markets_traded[market]["sell_usdc"] += usdc_size

    # Ordenar mercados por volumen
    top_markets = sorted(
        markets_traded.items(),
        key=lambda x: x[1]["buy_usdc"] + x[1]["sell_usdc"],
        reverse=True
    )[:20]

    # Stats de tamaño de trade
    if trade_sizes:
        avg_size = sum(trade_sizes) / len(trade_sizes)
        max_size = max(trade_sizes)
        min_size = min(trade_sizes)
        # Distribución por rangos
        under_50    = sum(1 for s in trade_sizes if s < 50)
        range_50_200 = sum(1 for s in trade_sizes if 50 <= s < 200)
        range_200_1k = sum(1 for s in trade_sizes if 200 <= s < 1000)
        over_1k     = sum(1 for s in trade_sizes if s >= 1000)

    print("\n" + "="*65)
    print("  ANALISIS COMPLETO DE CARTERA")
    print(f"  Wallet: {WALLET}")
    print("="*65)
    print(f"\n RESUMEN GLOBAL")
    print(f"  Total de operaciones analizadas : {len(trades)}")
    print(f"  BUYs                            : {buy_count}")
    print(f"  SELLs                           : {sell_count}")
    print(f"  Total USDC entrado (BUYs)       : ${total_usdc_in:,.2f}")
    print(f"  Total USDC salido  (SELLs)      : ${total_usdc_out:,.2f}")
    print(f"  Mercados distintos              : {len(markets_traded)}")

    print(f"\n TAMANO TIPICO POR OPERACION")
    print(f"  Promedio : ${avg_size:,.2f} USDC")
    print(f"  Maximo   : ${max_size:,.2f} USDC")
    print(f"  Minimo   : ${min_size:,.2f} USDC")

    print(f"\n DISTRIBUCION DE TAMANOS")
    print(f"  < $50         : {under_50} trades  ({under_50/len(trades)*100:.1f}%)")
    print(f"  $50 - $200    : {range_50_200} trades  ({range_50_200/len(trades)*100:.1f}%)")
    print(f"  $200 - $1.000 : {range_200_1k} trades  ({range_200_1k/len(trades)*100:.1f}%)")
    print(f"  > $1.000      : {over_1k} trades  ({over_1k/len(trades)*100:.1f}%)")

    print(f"\n TOP 20 MERCADOS POR VOLUMEN")
    print(f"  {'Mercado':<50} {'Trades':>6}  {'BUY ($)':>10}  {'SELL ($)':>10}")
    print(f"  {'-'*50} {'-'*6}  {'-'*10}  {'-'*10}")
    for name, data in top_markets:
        short = name[:50]
        print(f"  {short:<50} {data['trades']:>6}  ${data['buy_usdc']:>9,.0f}  ${data['sell_usdc']:>9,.0f}")

    # Guardar artefacto completo
    output = {
        "wallet": WALLET,
        "total_trades": len(trades),
        "buy_count": buy_count,
        "sell_count": sell_count,
        "total_usdc_in": total_usdc_in,
        "total_usdc_out": total_usdc_out,
        "avg_trade_size": avg_size,
        "max_trade_size": max_size,
        "min_trade_size": min_size,
        "size_distribution": {
            "under_50": under_50,
            "50_to_200": range_50_200,
            "200_to_1k": range_200_1k,
            "over_1k": over_1k
        },
        "top_markets": [{"name": k, **v} for k, v in top_markets],
        "raw_trades": trades
    }

    with open("artifacts/whale_analysis.json", "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    print(f"\n Artefacto materializado: artifacts/whale_analysis.json")
    print("="*65)

if __name__ == "__main__":
    trades = fetch_all_activity()
    analyze(trades)
