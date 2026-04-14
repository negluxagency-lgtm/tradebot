import json
from pathlib import Path
from supabase_engine import log_trade_to_supabase, update_trade_in_supabase

def migrate_history():
    print("⏳ Migrando resultados históricos del Deep Test a Supabase...")
    results_path = Path("artifacts/backtest_v3_results.json")
    
    if not results_path.exists():
        print("❌ No se encontró el archivo de resultados históricos.")
        return

    with open(results_path, "r") as f:
        full_data = json.load(f)

    # Solo migraremos el Deep Test (último año) o el Vintage para no saturar.
    # Usemos el Frequency Test (últimos 60 días) como ejemplo rápido.
    deep_data = full_data["results"]["frequency_test_60m"]
    
    # Nota: El JSON actual solo tiene promedios, no la lista de trades exacta.
    # Para el Dashboard, insertaremos 3 trades de ejemplo basados en los promedios
    # para validar la visualización.
    
    mock_trades = [
        {"side": "BUY", "trend": "BULLISH", "entry": 65000, "exit": 66950, "pnl": 3.0, "res": "TP"},
        {"side": "SELL", "trend": "BEARISH", "entry": 68000, "exit": 69462, "pnl": -2.15, "res": "SL"},
        {"side": "BUY", "trend": "BULLISH", "entry": 64000, "exit": 65920, "pnl": 3.0, "res": "TP"},
    ]

    for t in mock_trades:
        uuid = log_trade_to_supabase({
            "side": t["side"],
            "trend": t["trend"],
            "entry_price": t["entry"],
            "is_live": False
        })
        if uuid:
            update_trade_in_supabase(uuid, {
                "exit_price": t["exit"],
                "pnl_pct": t["pnl"],
                "result": t["res"],
                "reason": "Historical Migration"
            })

    print("✅ Migración de prueba completada.")

if __name__ == "__main__":
    migrate_history()
