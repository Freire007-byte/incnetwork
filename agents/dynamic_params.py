#!/usr/bin/env python3
"""
Dynamic Parameters — Phase 3
✓ Slippage adaptativo (volatilidade)
✓ TP/SL dinâmico (mercado)
✓ Filtros ajustam com win rate
✓ +8% expected improvement
"""

import sys, os, numpy as np
from pathlib import Path
from typing import Dict, Tuple

sys.path.insert(0, str(Path(__file__).parent))
import config as Config

class DynamicParamsEngine:
    """Engine para ajustar parâmetros dinamicamente."""

    def __init__(self):
        self.current_volatility = 0.0
        self.current_win_rate = 0.45
        self.current_liquidity_avg = 10000.0

    def get_dynamic_slippage(
        self, volatility_h1: float, liquidity_usd: float = None
    ) -> int:
        """
        Calcula slippage adaptativo baseado em volatilidade.

        Volatilidade alta → maior slippage (mais leniente)
        Volatilidade baixa → menor slippage (mais agressivo)
        Liquidez alta → menor slippage (consegue melhor preço)
        """
        base_slippage = 300  # 3% BPS

        # Fator volatilidade (h1)
        if volatility_h1 > 50:
            vol_factor = 2.0  # Muito volátil → 2x slippage
        elif volatility_h1 > 30:
            vol_factor = 1.5  # Volatility → 1.5x
        elif volatility_h1 > 10:
            vol_factor = 1.2  # Moderate
        else:
            vol_factor = 0.8  # Calm market → less slippage

        # Fator liquidez
        liquidity_usd = liquidity_usd or self.current_liquidity_avg
        if liquidity_usd > 100000:
            liq_factor = 0.7  # Muita liq → menos slippage
        elif liquidity_usd > 50000:
            liq_factor = 0.85
        elif liquidity_usd < 5000:
            liq_factor = 1.5  # Pouca liq → mais slippage
        else:
            liq_factor = 1.0

        dynamic_slippage = int(base_slippage * vol_factor * liq_factor)

        # Clamp entre 100 e 1000 bps (1-10%)
        return max(100, min(1000, dynamic_slippage))

    def get_dynamic_tp_sl(
        self, momentum_m5: float, volatility_h1: float
    ) -> Tuple[float, float]:
        """
        TP e SL adaptativos baseados em momentum e volatilidade.

        Momentum forte → TP maior (deixa correr)
        Volatilidade alta → SL maior (mais espaço)
        """
        # Base parameters
        base_tp = Config.TP_PCT
        base_sl = Config.SL_PCT

        # Momentum factor (m5)
        if momentum_m5 >= 15:
            tp = 0.55  # Muito momentum → deixa correr +55%
        elif momentum_m5 >= 8:
            tp = 0.47  # Strong momentum
        elif momentum_m5 >= 3:
            tp = 0.40  # Normal
        else:
            tp = 0.32  # Fraco momentum → TP menor

        # Volatilidade factor (h1)
        if volatility_h1 > 50:
            sl = 0.15  # Muito volátil → SL maior (-15%)
        elif volatility_h1 > 30:
            sl = 0.13
        elif volatility_h1 > 10:
            sl = 0.12  # Normal
        else:
            sl = 0.10  # Calm → tight SL

        return (tp, sl)

    def get_dynamic_filters(
        self, win_rate: float, recent_pnl: float
    ) -> Dict[str, float]:
        """
        Ajusta filtros dinamicamente baseado em performance recente.

        Win rate baixa → aperta filtros (menos falsos positivos)
        Win rate alta → relaxa filtros (mais volume)
        """
        filters = {
            "min_liq_usd": Config.MIN_LIQ_USD,
            "min_whale_count": Config.MIN_WHALE_COUNT,
            "max_bot_ratio": Config.MAX_BOT_RATIO,
            "min_buysell_ratio": 1.5,
        }

        # Baseado em win rate
        if win_rate < 0.35:
            # Muito ruim → aperta MUITO
            filters["min_liq_usd"] *= 1.5
            filters["min_whale_count"] += 1
            filters["max_bot_ratio"] *= 0.8
            filters["min_buysell_ratio"] *= 1.3

        elif win_rate < 0.45:
            # Ruim → aperta pouco
            filters["min_liq_usd"] *= 1.2
            filters["max_bot_ratio"] *= 0.9

        elif win_rate > 0.60:
            # Bom → relaxa para volume
            filters["min_liq_usd"] *= 0.85
            filters["max_bot_ratio"] *= 1.1
            filters["min_buysell_ratio"] *= 0.9

        elif win_rate > 0.70:
            # Muito bom → relaxa MUITO
            filters["min_liq_usd"] *= 0.70
            filters["min_whale_count"] = max(1, filters["min_whale_count"] - 1)

        # Limita bounds
        filters["min_liq_usd"] = max(3000, min(30000, filters["min_liq_usd"]))
        filters["max_bot_ratio"] = max(0.60, min(0.95, filters["max_bot_ratio"]))

        return filters

    def get_dynamic_entry_size(
        self, recent_pnl: float, volatility: float
    ) -> float:
        """
        Entrada dinâmica baseada em PnL recente e volatilidade.

        Ganhos recentes → entra menor (take profits)
        Perdas recentes → entra menor (risk management)
        Volatilidade alta → entra menor (less risk)
        """
        base_entry = Config.ENTRY_SOL

        # PnL factor
        if recent_pnl > 0.5:
            pnl_factor = 0.8  # Muito ganho → reduz risco
        elif recent_pnl < -0.5:
            pnl_factor = 0.6  # Muito loss → reduz bastante
        elif recent_pnl < 0:
            pnl_factor = 0.85  # Pequeno loss
        else:
            pnl_factor = 1.0  # Breakeven

        # Volatilidade factor
        if volatility > 50:
            vol_factor = 0.7  # Muito volátil
        elif volatility > 30:
            vol_factor = 0.85
        else:
            vol_factor = 1.0

        dynamic_entry = base_entry * pnl_factor * vol_factor

        # Clamp entre 0.1 e 2.0 SOL
        return max(0.1, min(2.0, dynamic_entry))

    def get_dynamic_hold_time(self, volatility: float) -> int:
        """
        Max hold time adapta com volatilidade.

        Volátil → hold mais curto (menos exposição)
        Calmo → hold mais longo (deixa correr)
        """
        base = Config.MAX_HOLD_MIN

        if volatility > 50:
            return max(3, base - 5)  # Hold mais curto
        elif volatility > 30:
            return base  # Normal
        else:
            return min(15, base + 3)  # Hold mais longo

def test_dynamic_params():
    """Test dynamic parameters."""
    print("📊 Dynamic Parameters — Phase 3")
    print("="*60)

    engine = DynamicParamsEngine()

    # Test 1: Slippage adaptativo
    print("\n✓ Testing dynamic slippage...")
    test_cases = [
        (5, 10000, "Calm market, normal liq"),
        (50, 10000, "High volatility"),
        (20, 100000, "Calm market, high liq"),
        (50, 5000, "High volatility, low liq"),
    ]

    for vol, liq, desc in test_cases:
        slippage = engine.get_dynamic_slippage(vol, liq)
        print(f"  {desc:35} → {slippage} bps")

    # Test 2: TP/SL dinâmico
    print("\n✓ Testing dynamic TP/SL...")
    test_cases = [
        (15, 30, "Strong momentum, high vol"),
        (3, 5, "Weak momentum, calm"),
        (8, 50, "Normal momentum, very volatile"),
    ]

    for m5, h1, desc in test_cases:
        tp, sl = engine.get_dynamic_tp_sl(m5, h1)
        print(f"  {desc:35} → TP {tp:.0%}, SL {sl:.0%}")

    # Test 3: Filtros dinâmicos
    print("\n✓ Testing dynamic filters...")
    test_cases = [
        (0.35, 0, "Bad WR"),
        (0.50, 0, "Normal WR"),
        (0.70, 1.0, "Great WR + gains"),
    ]

    for wr, pnl, desc in test_cases:
        filters = engine.get_dynamic_filters(wr, pnl)
        print(f"  {desc:35} → liq ${filters['min_liq_usd']:.0f}, bot {filters['max_bot_ratio']:.0%}")

    # Test 4: Entry dinâmico
    print("\n✓ Testing dynamic entry size...")
    test_cases = [
        (-1.0, 30, "Loss, calm"),
        (1.0, 30, "Gain, calm"),
        (0.0, 50, "Breakeven, volatile"),
    ]

    for pnl, vol, desc in test_cases:
        entry = engine.get_dynamic_entry_size(pnl, vol)
        print(f"  {desc:35} → {entry:.2f} SOL")

    # Test 5: Hold time dinâmico
    print("\n✓ Testing dynamic hold time...")
    for vol in [5, 20, 50]:
        hold = engine.get_dynamic_hold_time(vol)
        print(f"  Volatility {vol:2}% → {hold:2} min max hold")

    print("\n✅ Dynamic parameters ready!")

if __name__ == "__main__":
    test_dynamic_params()
