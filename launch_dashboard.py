import http.server
import socketserver
import webbrowser
import os
from pathlib import Path

# Configuración
PORT = 8000
DIRECTORY = Path(__file__).parent / "dashboard"

class MyHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(DIRECTORY), **kwargs)

def start_mission_control():
    os.chdir(Path(__file__).parent)
    
    with socketserver.TCPServer(("", PORT), MyHandler) as httpd:
        print(f"🚀 [ANTIGRAVITY] Mission Control Dashboard activado en http://localhost:{PORT}")
        print("💻 Presiona Ctrl+C para apagar los motores visuales.")
        
        # Abrir el navegador automáticamente
        webbrowser.open(f"http://localhost:{PORT}")
        
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\n🛑 Apagando Centro de Control. Hasta pronto, Comandante.")
            httpd.shutdown()

if __name__ == "__main__":
    start_mission_control()
