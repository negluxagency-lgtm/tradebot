import json
import os
import sys
import io

# Fix para codificación Unicode en terminales Windows
if sys.stdout.encoding != 'utf-8':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

def clear_local_history():
    print("🧹 Iniciando limpieza de artefactos locales...")
    
    files_to_clear = [
        "artifacts/copy_trade_pnl.json",
        "artifacts/whale_signals.json",
        "artifacts/whale_tracker.log"
    ]
    
    for file_path in files_to_clear:
        if os.path.exists(file_path):
            try:
                if file_path.endswith(".json"):
                    with open(file_path, 'w', encoding='utf-8') as f:
                        json.dump([], f)
                else:
                    with open(file_path, 'w', encoding='utf-8') as f:
                        f.write("")
                print(f"✅ Vaciado: {file_path}")
            except Exception as e:
                print(f"❌ Error al vaciar {file_path}: {e}")
        else:
            print(f"➖ No existe: {file_path}")

if __name__ == "__main__":
    clear_local_history()
