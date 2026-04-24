import requests
import sys
import io
from supabase_engine import SUPABASE_URL, SUPABASE_KEY, get_supabase_headers

# Fix para codificación Unicode en terminales Windows
if sys.stdout.encoding != 'utf-8':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')


def reset_database():
    print("🧹 Iniciando protocolo de limpieza de base de datos...")
    
    # Truncar tabla vía REST (DELETE sin filtro id=neq.null no es soportado por defecto)
    # Usaremos una condición que abarque todos los registros (id is not null)
    url = f"{SUPABASE_URL}/rest/v1/trades?id=neq.00000000-0000-0000-0000-000000000000"
    
    try:
        resp = requests.delete(url, headers=get_supabase_headers(), timeout=10)
        if resp.status_code in [200, 204]:
            print("✅ Dashboard reseteado. Base de datos vacía y lista para datos reales.")
        else:
            print(f"❌ Error al limpiar: {resp.status_code} | {resp.text}")
    except Exception as e:
        print(f"❌ Excepción en reset: {e}")

if __name__ == "__main__":
    reset_database()
