import subprocess
import os
import sys

def check_and_install(package):
    """Verifica si un paquete está instalado e intenta instalarlo si falta."""
    try:
        __import__(package.replace("-", "_"))
        print(f"[OK] {package} está listo.")
    except ImportError:
        print(f"[*] Instalando {package}...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", package])

def create_env_file():
    """Crea un archivo .env.local con los marcadores necesarios."""
    env_content = """# 🛰️ Configuración del Bot de Trading Polymarket

# 1. Configuración de Red (Polygon Mainnet)
POLY_CHAIN_ID=137
POLY_HOST=https://clob.polymarket.com

# 2. Credenciales de Nivel 1 (Firma de Wallet)
# IMPORTANTE: Nunca compartas tu Clave Privada.
POLYMARKET_PRIVATE_KEY=your_private_key_here
# Funder: Si usas Magic Link (email), pon tu Proxy Address. Si usas EOA, puedes omitirlo o dejarlo vacío.
POLYMARKET_PROXY_ADDRESS=your_proxy_address_here

# 3. Credenciales de Nivel 2 (CLOB API - Generadas tras la primera autenticación)
POLY_API_KEY=
POLY_API_SECRET=
POLY_API_PASSPHRASE=

# 4. Firma de Tipo (0: EOA, 1: Magic/Proxy, 2: Gnosis Safe)
POLY_SIGNATURE_TYPE=1
"""
    env_path = ".env.local"
    if not os.path.exists(env_path):
        with open(env_path, "w", encoding="utf-8") as f:
            f.write(env_content)
        print(f"[OK] {env_path} creado exitosamente.")
    else:
        print(f"[!] {env_path} ya existe. Saltando creación.")

if __name__ == "__main__":
    print("--- INICIALIZANDO ENTORNO ANTIGRAVITY ---")
    
    # Lista de dependencias críticas
    dependencies = ["py-clob-client", "python-dotenv", "web3", "requests"]
    
    for dep in dependencies:
        # Algunos paquetes tienen nombres de importación distintos
        import_name = dep
        if dep == "py-clob-client":
            import_name = "py_clob_client"
        
        try:
            __import__(import_name)
            print(f"[OK] {dep} ya está disponible.")
        except ImportError:
            check_and_install(dep)

    create_env_file()
    print("--- MOTOR LISTO PARA EL DESPEGUE ---")
