#!/usr/bin/env python3
"""
Caca Pump Orchestrator — Coordena Scanner, Engine, State Manager
Simplicidade máxima: apenas orquestra os 3 módulos.
"""

import asyncio
import time
import sys
from typing import Optional, Dict, List, Any
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "agents"))
from utils import setup_logger
from types import CandidateToken, TradeStatus

from caca_pump_scanner import PumpFunScanner
from caca_pump_engine import TradingEngine, AdaptiveParams
from caca_pump_state import StateManager

logger = setup_logger("CacaPumpOrchestrator")


class CacaPumpLive:
    """Orquestra Scanner + Engine + State Manager."""

    def __init__(
        self,
        base_dir: str = ".",
        entry_sol: float = 0.5,
        live_trading: bool = False
    ):
        self.base_dir = Path(base_dir)
        self.live_trading = live_trading

        # Inicializa módulos
        self.scanner = PumpFunScanner()
        self.engine = TradingEngine(AdaptiveParams(entry_sol=entry_sol))
        self.state = StateManager(str(self.base_dir))

        # Config
        self.scan_interval = 3  # 3 segundos
        self.watchdog_interval = 10  # 10 segundos
        self.output_interval = 30  # 30 segundos

        logger.info(f"🚀 Caca Pump Live iniciado (live={live_trading})")

    # ─── Main Loop ────────────────────────────────────────────────────────

    async def run(self):
        """Main loop contínuo."""
        tasks = [
            asyncio.create_task(self._scan_loop()),
            asyncio.create_task(self._watchdog_loop()),
            asyncio.create_task(self._output_loop())
        ]

        try:
            await asyncio.gather(*tasks)
        except KeyboardInterrupt:
            logger.info("Shutdown...")
            for task in tasks:
                task.cancel()

    async def _scan_loop(self):
        """Loop de scanning — detecta novos tokens."""
        while True:
            try:
                # TODO: Integrar com pump.fun scanner real
                # Por enquanto placeholder
                await asyncio.sleep(self.scan_interval)

            except Exception as e:
                logger.error(f"Scan error: {e}")
                await asyncio.sleep(self.scan_interval)

    async def _watchdog_loop(self):
        """Loop de watchdog — monitora posições abertas."""
        while True:
            try:
                current_balance = 1.0  # TODO: Get real balance

                # Monitora cada posição
                for mint, position in list(self.engine.positions.items()):
                    # Get preço atual
                    data = self.scanner.monitor_token_price(mint)
                    if not data:
                        continue

                    current_price = data["price_usd"]

                    # Atualiza métricas
                    self.engine.update_position_metrics(position, current_price)

                    # Check break-even
                    self.engine.apply_break_even(position, current_price)

                    # Check exit conditions
                    exit_cond = self.engine.check_exit_conditions(position, current_price)
                    if exit_cond:
                        status, reason = exit_cond
                        self.engine.close_position(mint, current_price, reason)
                        self.state.append_trade_jsonl({
                            "mint": mint,
                            "symbol": position.symbol,
                            "entry_price": position.entry_price,
                            "exit_price": current_price,
                            "reason": reason,
                            "pnl_pct": (current_price - position.entry_price) / position.entry_price * 100,
                            "timestamp": time.time()
                        })

                await asyncio.sleep(self.watchdog_interval)

            except Exception as e:
                logger.error(f"Watchdog error: {e}")
                await asyncio.sleep(self.watchdog_interval)

    async def _output_loop(self):
        """Loop de output — salva dados para painel."""
        while True:
            try:
                stats = self.engine.get_stats()
                positions_data = {
                    mint: {
                        "symbol": pos.symbol,
                        "entry_price": pos.entry_price,
                        "current_price": pos.current_price,
                        "pnl_pct": pos.pnl_pct,
                        "hold_time_min": (time.time() - pos.entry_time) / 60.0
                    }
                    for mint, pos in self.engine.positions.items()
                }

                live_data = {
                    "status": "SCANNING",
                    "positions": positions_data,
                    "n_positions": len(self.engine.positions),
                    "stats": stats,
                    "live_trading": self.live_trading,
                    "timestamp": time.time()
                }

                self.state.save_live_data(live_data)

                await asyncio.sleep(self.output_interval)

            except Exception as e:
                logger.error(f"Output error: {e}")
                await asyncio.sleep(self.output_interval)

    # ─── Manual Operations ─────────────────────────────────────────────────

    def manual_entry(
        self,
        mint: str,
        symbol: str,
        entry_price: float,
        market_cap_usd: float = 5000,
        liquidity_usd: float = 5000
    ) -> bool:
        """Entrada manual para teste."""
        candidate = CandidateToken(
            mint=mint,
            symbol=symbol,
            age_sec=30,
            price_usd=entry_price,
            market_cap_usd=market_cap_usd,
            liquidity_usd=liquidity_usd,
            h1_pct_change=0.0,
            buys_h1=0,
            sells_h1=0,
            buy_sell_ratio_h1=0.0,
            whale_count=1,
            bot_ratio=0.0,
            pattern="MANUAL",
            score=100.0
        )

        return self.engine.enter_position(candidate) is not None

    def manual_exit(self, mint: str, exit_price: float, reason: str = "MANUAL") -> bool:
        """Saída manual para teste."""
        if mint not in self.engine.positions:
            return False

        self.engine.close_position(mint, exit_price, reason)
        return True

    # ─── State Query ───────────────────────────────────────────────────────

    def get_positions(self) -> Dict[str, Any]:
        """Retorna posições abertas."""
        return self.engine.positions

    def get_stats(self) -> Dict[str, float]:
        """Retorna estatísticas."""
        return self.engine.get_stats()

    def get_summary(self) -> Dict[str, Any]:
        """Retorna sumário da sessão."""
        return self.state.get_session_summary()


# Singleton global
_live = None


def get_caca_pump_live(base_dir: str = ".") -> CacaPumpLive:
    """Retorna instância global."""
    global _live
    if not _live:
        _live = CacaPumpLive(base_dir)
    return _live


if __name__ == "__main__":
    # Teste simples
    live = CacaPumpLive(".", entry_sol=0.5, live_trading=False)

    # Run
    try:
        asyncio.run(live.run())
    except KeyboardInterrupt:
        logger.info("Shutdown complete")


__all__ = ['CacaPumpLive', 'get_caca_pump_live']
