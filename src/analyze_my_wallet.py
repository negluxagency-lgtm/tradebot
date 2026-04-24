import requests
import json
import sys
import io

if sys.stdout.encoding != 'utf-8':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

WALLET = "0xb50c894b723f68af1eb305b7096d74a5b92811aa"
DATA_API = "https://data-api.polymarket.com"

def fetch_all(wallet):
    all_trades = []
    offset = 0
    limit = 100
    print(f"Extrayendo actividad de {wallet}...")
    while True:
        resp = requests.get(
            f"{DATA_API}/activity",
            params={"user": wallet, "limit": limit, "offset": offset},
            timeout=15
        )
        if resp.status_code != 200:
            print(f"  Fin paginacion: HTTP {resp.status_code}")
            break
        batch = resp.json()
        if not batch or not isinstance(batch, list):
            break
        all_trades.extend(batch)
        print(f"  offset={offset}: {len(batch)} trades (Total: {len(all_trades)})")
        if len(batch) < limit:
            break
        offset += limit
    return all_trades

def analyze(all_trades, wallet):
    if not all_trades:
        print("Sin trades.")
        return

    buy_usdc  = sum(float(t.get("usdcSize", 0) or 0) for t in all_trades if t.get("side","").upper() == "BUY")
    sell_usdc = sum(float(t.get("usdcSize", 0) or 0) for t in all_trades if t.get("side","").upper() == "SELL")
    buy_count  = sum(1 for t in all_trades if t.get("side","").upper() == "BUY")
    sell_count = sum(1 for t in all_trades if t.get("side","").upper() == "SELL")
    sizes = [float(t.get("usdcSize", 0) or 0) for t in all_trades]
    avg = sum(sizes) / len(sizes) if sizes else 0

    under_5  = sum(1 for s in sizes if s < 5)
    r5_20    = sum(1 for s in sizes if 5  <= s < 20)
    r20_50   = sum(1 for s in sizes if 20 <= s < 50)
    over_50  = sum(1 for s in sizes if s >= 50)

    markets = {}
    for t in all_trades:
        k = t.get("title", "Desconocido")
        side = t.get("side", "BUY").upper()
        usdc = float(t.get("usdcSize", 0) or 0)
        if k not in markets:
            markets[k] = {"buy": 0.0, "sell": 0.0, "trades": 0}
        markets[k]["trades"] += 1
        if side == "BUY":
            markets[k]["buy"] += usdc
        else:
            markets[k]["sell"] += usdc

    top = sorted(markets.items(), key=lambda x: x[1]["buy"] + x[1]["sell"], reverse=True)[:15]

    sep = "=" * 65
    print()
    print(sep)
    print("  TU CARTERA - ANALISIS REAL")
    print(f"  Wallet: {wallet}")
    print(sep)
    print(f"  Total operaciones     : {len(all_trades)}")
    print(f"  BUYs                  : {buy_count}")
    print(f"  SELLs                 : {sell_count}")
    print(f"  Total USDC invertido  : ${buy_usdc:,.2f}")
    print(f"  Total USDC recuperado : ${sell_usdc:,.2f}")
    print(f"  PnL neto estimado     : ${sell_usdc - buy_usdc:+,.2f}")
    print(f"  Mercados distintos    : {len(markets)}")
    print()
    print("  TAMANO POR OPERACION")
    print(f"  Promedio : ${avg:,.2f} USDC")
    print(f"  Maximo   : ${max(sizes):,.2f} USDC")
    print(f"  Minimo   : ${min(sizes):,.2f} USDC")
    print()
    print("  DISTRIBUCION DE TAMANOS")
    n = len(all_trades)
    print(f"  < $5       : {under_5:>4} trades  ({under_5/n*100:.1f}%)")
    print(f"  $5 - $20   : {r5_20:>4} trades  ({r5_20/n*100:.1f}%)")
    print(f"  $20 - $50  : {r20_50:>4} trades  ({r20_50/n*100:.1f}%)")
    print(f"  > $50      : {over_50:>4} trades  ({over_50/n*100:.1f}%)")
    print()
    print("  TOP 15 MERCADOS POR VOLUMEN")
    print(f"  {'Mercado':<45} {'Trades':>6}  {'BUY':>9}  {'SELL':>9}  {'PnL':>9}")
    print(f"  {'-'*45} {'-'*6}  {'-'*9}  {'-'*9}  {'-'*9}")
    for name, d in top:
        pnl = d["sell"] - d["buy"]
        pnl_str = f"${pnl:+,.0f}"
        print(f"  {name[:45]:<45} {d['trades']:>6}  ${d['buy']:>8,.0f}  ${d['sell']:>8,.0f}  {pnl_str:>9}")
    print(sep)

    out = {
        "wallet": wallet,
        "total_trades": len(all_trades),
        "buy_count": buy_count,
        "sell_count": sell_count,
        "buy_usdc": buy_usdc,
        "sell_usdc": sell_usdc,
        "pnl": sell_usdc - buy_usdc,
        "avg_size": avg,
        "max_size": max(sizes),
        "min_size": min(sizes),
        "size_distribution": {
            "under_5": under_5,
            "5_to_20": r5_20,
            "20_to_50": r20_50,
            "over_50": over_50
        },
        "top_markets": [{"name": k, **v} for k, v in top],
        "raw_trades": all_trades
    }
    with open("artifacts/my_wallet_analysis.json", "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)
    print("  Artefacto: artifacts/my_wallet_analysis.json")

if __name__ == "__main__":
    trades = fetch_all(WALLET)
    analyze(trades, WALLET)
