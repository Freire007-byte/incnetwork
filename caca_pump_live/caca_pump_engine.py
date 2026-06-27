#!/usr/bin/env python3
"""
Caca Pump Trading Engine — Responsável apenas por lógica de trades
Verifica TP/SL, entrada, saída, break-even, etc.
"""

import time
import sys
from typing import Optional, Dict, List, Tuple
from pathlib import Path
from dataclasses import dataclass, field

sys.path.insert(0, str(Path(__file__).parent.parent / "agents"))
from utils import setup_logger
from types import Position, TradeStatus, CandidateToken, ClosedTrade

logger = setup_logger("CacaPumpEngine")


@dataclass
class AdaptiveParams:
    """Parâmetros adaptativos do engine."""
    entry_sol: float = 0.5
    tp_pct: float = 0.104  # +10.4%
    sl_pct: float = 0.08  # -8%
    max_hold_min: int = 8
    break_even_trigger: float = 0.06  # +6%
    break_even_sl: float = 0.01  # Move SL para entry+1%
    max_age_sec: int = 60
    min_mc_usd: float = 3000
    max_mc_usd: float = 8000
    min_whale_count: int = 1


class TradingEngine:
    """Engine para gerenciar trades — entrada, saída, TP/SL."""

    def __init__(self, params: Optional[AdaptiveParams] = None):
        self.params = params or AdaptiveParams()
        self.positions: Dict[str, Position] = {}
        self.closed_trades: List[ClosedTrade] = []

    # ─── Entry Logic ──────────────────────────────────────────────────────

    def should_enter(
        self,
        candidate: CandidateToken,
        current_balance_sol: float
    ) -> bool:
        """
        Verifica se deve entrar em posição.

        Critérios:
        - Tem saldo suficiente
        - Token não muito velho (< 60s)
        - Market cap no range (3k-8k)
        - Tem liquidez
        - Não há posições demais abertas
        """
        # Saldo check
        if current_balance_sol < self.params.entry_sol + 0.01:
            logger.debug(f"Insufficient balance: {current_balance_sol:.3f} < {self.params.entry_sol}")
            return False

        # Idade check
        if candidate.age_sec > self.params.max_age_sec:
            logger.debug(f"Token too old: {candidate.age_sec:.1f}s > {self.params.max_age_sec}s")
            return False

        # Market cap check
        if candidate.market_cap_usd < self.params.min_mc_usd:
            logger.debug(f"Market cap too low: ${candidate.market_cap_usd:.0f} < ${self.params.min_mc_usd}")
            return False

        if candidate.market_cap_usd > self.params.max_mc_usd:
            logger.debug(f"Market cap too high: ${candidate.market_cap_usd:.0f} > ${self.params.max_mc_usd}")
            return False

        # Liquidez check
        if candidate.liquidity_usd < 1000:  # Min $1k liquidity
            logger.debug(f"Low liquidity: ${candidate.liquidity_usd:.0f}")
            return False

        # Posições check
        if len(self.positions) >= 3:
            logger.debug(f"Too many open positions: {len(self.positions)} >= 3")
            return False

        return True

    def enter_position(
        self,
        candidate: CandidateToken,
        entry_sol: Optional[float] = None
    ) -> Optional[Position]:
        """
        Cria nova posição.

        Returns:
            Position object ou None se falhou
        """
        entry_amount = entry_sol or self.params.entry_sol

        try:
            pos = Position(
                mint=candidate.mint,
                symbol=candidate.symbol,
                entry_price=candidate.price_usd,
                entry_sol=entry_amount,
                entry_time=time.time(),
                status=TradeStatus.OPEN,
                tp_price=candidate.price_usd * (1 + self.params.tp_pct),
                sl_price=candidate.price_usd * (1 - self.params.sl_pct),
                trailing_sl_price=candidate.price_usd * (1 - self.params.sl_pct)
            )

            self.positions[candidate.mint] = pos
            logger.info(
                f"💰 ENTRADA: {candidate.symbol} @ ${candidate.price_usd:.8f} | "
                f"{entry_amount} SOL | Score: {candidate.score:.0f}"
            )
            return pos

        except Exception as e:
            logger.error(f"Error creating position: {e}")
            return None

    # ─── Exit Logic ───────────────────────────────────────────────────────

    def check_exit_conditions(
        self,
        position: Position,
        current_price: float
    ) -> Optional[Tuple[TradeStatus, str]]:
        """
        Verifica se deve sair da posição.

        Returns:
            (TradeStatus, reason) ou None
        """
        if not position.is_active():
            return None

        current_time = time.time()
        hold_time_min = (current_time - position.entry_time) / 60.0

        # TP check
        if position.tp_price and current_price >= position.tp_price:
            return (TradeStatus.TP_HIT, "TP")

        # SL check (trailing)
        trailing_sl = position.trailing_sl_price or position.sl_price
        if trailing_sl and current_price <= trailing_sl:
            return (TradeStatus.SL_HIT, "SL")

        # Timeout check
        if hold_time_min >= self.params.max_hold_min:
            return (TradeStatus.TIMEOUT, "TIMEOUT")

        return None

    def apply_break_even(
        self,
        position: Position,
        current_price: float
    ) -> bool:
        """
        Ativa break-even: se +6%, move SL para entry+1%.

        Returns:
            True se ativado
        """
        if not position.is_active():
            return False

        pnl_pct = (current_price - position.entry_price) / position.entry_price

        # Check break-even trigger
        if pnl_pct >= self.params.break_even_trigger:
            # Move SL para entry + 1%
            new_sl = position.entry_price * (1 + self.params.break_even_sl)

            if not position.trailing_sl_price or new_sl > position.trailing_sl_price:
                position.trailing_sl_price = new_sl
                logger.info(
                    f"🎯 BREAK-EVEN: {position.symbol} | "
                    f"SL movido para ${new_sl:.8f}"
                )
                return True

        return False

    def update_position_metrics(
        self,
        position: Position,
        current_price: float
    ) -> None:
        """
        Atualiza métricas de posição (PnL, etc).
        """
        position.current_price = current_price
        position.pnl_pct = (current_price - position.entry_price) / position.entry_price * 100
        position.pnl_sol = position.entry_sol * position.pnl_pct / 100

    def close_position(
        self,
        mint: str,
        exit_price: float,
        reason: str
    ) -> Optional[ClosedTrade]:
        """
        Fecha posição e cria registro ClosedTrade.

        Returns:
            ClosedTrade object
        """
        if mint not in self.positions:
            return None

        position = self.positions[mint]
        pnl_pct = (exit_price - position.entry_price) / position.entry_price * 100
        pnl_sol = position.entry_sol * pnl_pct / 100
        exit_time = time.time()
        hold_time_sec = exit_time - position.entry_time

        # Determina status
        if reason == "TP":
            status = TradeStatus.TP_HIT
        elif reason == "SL":
            status = TradeStatus.SL_HIT
        elif reason == "TIMEOUT":
            status = TradeStatus.TIMEOUT
        else:
            status = TradeStatus.CLOSED

        # Cria registro
        closed_trade = ClosedTrade(
            mint=position.mint,
            symbol=position.symbol,
            entry_price=position.entry_price,
            exit_price=exit_price,
            entry_sol=position.entry_sol,
            pnl_pct=pnl_pct,
            pnl_sol=pnl_sol,
            entry_time=position.entry_time,
            exit_time=exit_time,
            hold_time_sec=hold_time_sec,
            status=status,
            reason=reason
        )

        # Remove posição e registra
        del self.positions[mint]
        self.closed_trades.append(closed_trade)

        # Log
        win_str = "✅" if closed_trade.is_win() else "❌"
        logger.info(
            f"📊 SAÍDA: {closed_trade.symbol} | {reason} | "
            f"{pnl_pct:+.1f}% | {pnl_sol:+.3f} SOL {win_str}"
        )

        return closed_trade

    # ─── Statistics ───────────────────────────────────────────────────────

    def get_stats(self) -> Dict[str, float]:
        """Retorna estatísticas de trading."""
        total_trades = len(self.closed_trades)
        wins = sum(1 for t in self.closed_trades if t.is_win())
        losses = total_trades - wins
        win_rate = (wins / total_trades * 100) if total_trades > 0 else 0
        total_pnl = sum(t.pnl_sol for t in self.closed_trades)
        avg_pnl_pct = (sum(t.pnl_pct for t in self.closed_trades) / total_trades) if total_trades > 0 else 0

        return {
            "total_trades": total_trades,
            "wins": wins,
            "losses": losses,
            "win_rate_pct": win_rate,
            "total_pnl_sol": total_pnl,
            "avg_pnl_pct": avg_pnl_pct,
            "open_positions": len(self.positions)
        }


__all__ = ['TradingEngine', 'AdaptiveParams']
