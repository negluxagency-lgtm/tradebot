"""
🛰️ Wallet Tracker v1.0 — Smart Money Detection
Responsabilidad: Descubrir, trackear y calificar wallets basadas en su performance real.
Materializa rankings en artifacts/top_wallets.json para ser usados como filtro de calidad.
"""
import os
import json
import asyncio
import logging
import aiohttp
from datetime import datetime, timezone
from dotenv import load_dotenv

load_dotenv(".env.local")

# ── Configuración ──────────────────────────────────────────────────────────────
CLOB_API_URL = os.getenv("POLY_HOST", "https://clob.polymarket.com")
STATS_PATH = "artifacts/wallet_stats.json"
RANKINGS_PATH = "artifacts/top_wallets.json"

logger = logging.getLogger("wallet_tracker")

class WalletStats:
    def __init__(self, address: str):
        self.address = address
        self.trades = []
        self.pnl = 0.0
        self.volume = 0.0
        self.wins = 0
        self.losses = 0
        self.score = 0.0

    def to_dict(self):
        return {
            "address": self.address,
            "pnl": self.pnl,
            "volume": self.volume,
            "wins": self.wins,
            "losses": self.losses,
            "score": self.score,
            "trade_count": len(self.trades)
        }

    @classmethod
    def from_dict(cls, data):
        inst = cls(data["address"])
        inst.pnl = data.get("pnl", 0.0)
        inst.volume = data.get("volume", 0.0)
        inst.wins = data.get("wins", 0)
        inst.losses = data.get("losses", 0)
        inst.score = data.get("score", 0.0)
        return inst

class WalletTracker:
    def __init__(self):
        self.wallets: dict[str, WalletStats] = {}
        self.rankings = []
        self._load_stats()

    def _load_stats(self):
        """Carga estadísticas desde artifacts/ (Proceso idempotente)."""
        if os.path.exists(STATS_PATH):
            try:
                with open(STATS_PATH, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    for addr, stats_data in data.items():
                        self.wallets[addr] = WalletStats.from_dict(stats_data)
                logger.info(f"📂 Cargadas {len(self.wallets)} wallets desde {STATS_PATH}")
            except Exception as e:
                logger.error(f"Error cargando estadísticas: {e}")

    def save_stats(self):
        """Persiste estadísticas y rankings en artifacts/."""
        os.makedirs("artifacts", exist_ok=True)
        try:
            # Stats completas
            with open(STATS_PATH, "w", encoding="utf-8") as f:
                json.dump({addr: s.to_dict() for addr, s in self.wallets.items()}, f, indent=2)
            
            # Solo Top Ranking para consulta rápida
            with open(RANKINGS_PATH, "w", encoding="utf-8") as f:
                json.dump(self.rankings, f, indent=2)
        except Exception as e:
            logger.error(f"Error guardando estadísticas: {e}")

    def register_trade(self, wallet_addr: str, trade: dict):
        """Registra un trade y actualiza el performance de la wallet."""
        if not wallet_addr or wallet_addr == "unknown":
            return

        if wallet_addr not in self.wallets:
            self.wallets[wallet_addr] = WalletStats(wallet_addr)

        stats = self.wallets[wallet_addr]
        size = float(trade.get("size", 0))
        price = float(trade.get("price", 0))
        side = trade.get("side", "").upper()

        stats.volume += size * price
        stats.trades.append({
            "ts": datetime.now(timezone.utc).isoformat(),
            "token_id": trade.get("token_id"),
            "side": side,
            "price": price,
            "size": size
        })

        # Lógica simplificada de PnL: Buys restan, Sells suman (proxy de cashflow)
        if side == "SELL":
            stats.pnl += size * price
            stats.wins += 1
        else:
            stats.pnl -= size * price
            stats.losses += 1
            
        # Limitar historial en memoria para no saturar
        if len(stats.trades) > 100:
            stats.trades = stats.trades[-100:]

        stats.score = self.compute_wallet_score(stats)

    def compute_wallet_score(self, stats: WalletStats) -> float:
        """Calcula el 'Edge Score' (0.0 a 1.0) para una wallet."""
        if stats.volume == 0:
            return 0.0

        # ROI relativo al volumen total operado
        # Usamos abs en el denominador para evitar división por cero o negativa (aunque vol > 0)
        roi = stats.pnl / stats.volume
        winrate = stats.wins / max(1, stats.wins + stats.losses)
        activity_score = min(len(stats.trades) / 100, 1.0)
        volume_norm = min(stats.volume / 10000.0, 1.0)

        # Formula balanceada: ROI es el factor dominante
        score = (
            roi * 0.5 + 
            winrate * 0.2 + 
            activity_score * 0.1 + 
            volume_norm * 0.2
        )
        return round(max(0.0, score), 4)

    def is_copyable(self, stats: WalletStats) -> bool:
        """Determina si la wallet cumple con los criterios de calidad mínima."""
        return (
            stats.volume > 5000 and
            len(stats.trades) >= 10 and
            stats.pnl > 0 and
            (stats.pnl / stats.volume) > 0.02
        )

    def update_rankings(self):
        """Actualiza la lista de top 20 wallets con mejor score."""
        scored = []
        for wallet_addr, stats in self.wallets.items():
            if self.is_copyable(stats):
                scored.append({
                    "address": wallet_addr,
                    "score": stats.score,
                    "pnl": stats.pnl,
                    "volume": stats.volume
                })
        
        self.rankings = sorted(scored, key=lambda x: x["score"], reverse=True)[:20]
        self.save_stats()

    async def discover_wallets_from_trades(self, session: aiohttp.ClientSession, token_id: str):
        """Descubre automáticamente traders recientes desde la API CLOB."""
        url = f"{CLOB_API_URL}/trades?token_id={token_id}"
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    for trade in data:
                        # En la API CLOB, el campo es 'maker_address' o 'taker_address'
                        # o simplemente 'trader' en algunos endpoints históricos
                        addr = trade.get("trader") or trade.get("maker_address") or trade.get("taker_address")
                        if addr:
                            self.register_trade(addr, {
                                "token_id": token_id,
                                "size": trade.get("size"),
                                "price": trade.get("price"),
                                "side": trade.get("side")
                            })
                    logger.info(f"🔍 Discovery: Procesados trades para {token_id} (Wallets: {len(self.wallets)})")
        except Exception as e:
            logger.warning(f"Error procesando discovery para {token_id}: {e}")

    def is_smart_money_present(self, token_id: str) -> bool:
        """Verifica si alguna de las Top 5 wallets ha tradeado este mercado."""
        top_wallets = {w["address"] for w in self.rankings[:5]}
        if not top_wallets:
            return False

        for addr in top_wallets:
            stats = self.wallets.get(addr)
            if not stats: continue
            if any(t["token_id"] == token_id for t in stats.trades):
                return True
        return False

# Singleton para acceso global
tracker = WalletTracker()

if __name__ == "__main__":
    # Modo de inicialización autónoma
    async def standalone_init():
        logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
        logger.info("🚀 Iniciando WalletTracker en modo Standalone (Discovery)...")
        
        async with aiohttp.ClientSession() as session:
            # 1. Obtener mercados activos (aproximación rápida)
            gamma_url = "https://gamma-api.polymarket.com/markets?active=true&closed=false&limit=20&order=volume&ascending=false"
            try:
                async with session.get(gamma_url) as resp:
                    markets = await resp.json()
                    for m in markets:
                        tokens = json.loads(m.get("clobTokenIds", "[]"))
                        if tokens:
                            await tracker.discover_wallets_from_trades(session, tokens[0])
            except Exception as e:
                logger.error(f"Error en discovery inicial: {e}")

            # 2. Actualizar Rankings
            tracker.update_rankings()
            logger.info(f"✅ Discovery completado. Rankings actualizados con {len(tracker.rankings)} top wallets.")
            logger.info("Los bots (arbitrage/whale) ahora utilizarán estos datos como filtro.")

    asyncio.run(standalone_init())
