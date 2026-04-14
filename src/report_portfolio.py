import json, os, sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

pnl_path = 'artifacts/copy_trade_pnl.json'
if not os.path.exists(pnl_path):
    print('Sin posiciones registradas.')
    exit()

with open(pnl_path, 'r', encoding='utf-8') as f:
    data = json.load(f)

if not data:
    print('El archivo esta vacio. El bot no ha ejecutado trades todavia.')
    exit()

abiertas = [p for p in data if p.get('status') == 'open']
cerradas = [p for p in data if p.get('status') == 'closed']
fallidas = [p for p in data if p.get('status') == 'failed']

capital_total   = sum(p.get('copy_trade_usdc', 0) for p in data)
capital_abierto = sum(p.get('copy_trade_usdc', 0) for p in abiertas)
pnl_realizado   = sum(p.get('pnl_usdc', 0) or 0 for p in cerradas)

print("=" * 55)
print("  REPORTE SHADOW TRACKER (DRY RUN)")
print("=" * 55)
print(f"  Total trades         : {len(data)}")
print(f"  Posiciones abiertas  : {len(abiertas)}")
print(f"  Posiciones cerradas  : {len(cerradas)}")
print(f"  Fallidas             : {len(fallidas)}")
print("-" * 55)
print(f"  Capital total usado  : ${capital_total:.2f} USDC")
print(f"  Capital en juego     : ${capital_abierto:.2f} USDC")
print(f"  P/L realizado        : ${pnl_realizado:+.2f} USDC")
print("=" * 55)

if abiertas:
    print("\n  POSICIONES ABIERTAS:")
    print("-" * 55)
    for p in abiertas:
        name = p.get('market_name', 'N/A')[:42]
        outcome = p.get('outcome', 'N/A')[:10]
        cap = p.get('copy_trade_usdc', 0)
        price = p.get('entry_price', 0)
        print(f"  {name:<42} | {outcome:<10} | ${cap:.2f} @ {price:.3f}")
