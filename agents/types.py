#!/usr/bin/env python3
"""
Type definitions centralizadas para o sistema de trading.
Melhora IDE autocomplete, static analysis, e self-documentation.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
from enum import Enum
import time


class TradeStatus(str, Enum):
    """Status de uma posição de trade."""
    OPEN = "OPEN"
    TP_HIT = "TP_HIT"
    SL_HIT = "SL_HIT"
    TIMEOUT = "TIMEOUT"
    CLOSED = "CLOSED"
    CANCELLED = "CANCELLED"


class PatternType(str, Enum):
    """Tipos de padrão detectado."""
    PUMP_BALEIA_FORTE = "PUMP_BALEIA_FORTE"
    PUMP_BOT_SWARM = "PUMP_BOT_SWARM"
    PUMP_LENTO_WHALE = "PUMP_LENTO_WHALE"
    PUMP_EXPLOSIVO = "PUMP_EXPLOSIVO"
    ORGANIC_SLOW = "ORGANIC_SLOW"
    RUG_CANDIDATO = "RUG_CANDIDATO"
    PUMP_MISTO = "PUMP_MISTO"


class OracleSignal(str, Enum):
    """Sinal do oracle (Corpo Celeste)."""
    LONG = "LONG"
    SHORT = "SHORT"
    NEUTRAL = "NEUTRAL"


class MarketRegime(str, Enum):
    """Regime de mercado detectado."""
    TRENDING_UP = "TRENDING_UP"
    TRENDING_DOWN = "TRENDING_DOWN"
    RANGING = "RANGING"
    VOLATILE = "VOLATILE"
    COMPRESSING = "COMPRESSING"
    LATERAL = "LATERAL"


@dataclass
class Position:
    """Uma posição de trade aberta."""
    mint: str
    symbol: str
    entry_price: float
    entry_sol: float
    entry_time: float = field(default_factory=time.time)
    status: TradeStatus = TradeStatus.OPEN
    current_price: Optional[float] = None
    tp_price: Optional[float] = None
    sl_price: Optional[float] = None
    trailing_sl_price: Optional[float] = None
    pnl_pct: float = 0.0
    pnl_sol: float = 0.0
    tx_sig: Optional[str] = None

    def is_active(self) -> bool:
        """Verifica se posição ainda está ativa."""
        return self.status == TradeStatus.OPEN

    def get_hold_time_seconds(self) -> float:
        """Retorna tempo de hold em segundos."""
        return time.time() - self.entry_time

    def get_hold_time_minutes(self) -> float:
        """Retorna tempo de hold em minutos."""
        return self.get_hold_time_seconds() / 60.0


@dataclass
class ClosedTrade:
    """Registro de um trade fechado."""
    mint: str
    symbol: str
    entry_price: float
    exit_price: float
    entry_sol: float
    pnl_pct: float
    pnl_sol: float
    entry_time: float
    exit_time: float
    hold_time_sec: float
    status: TradeStatus
    reason: str  # "TP", "SL", "TIMEOUT", etc

    def is_win(self) -> bool:
        """Verifica se foi win."""
        return self.pnl_sol > 0


@dataclass
class CandidateToken:
    """Token candidato para entrada."""
    mint: str
    symbol: str
    age_sec: float
    price_usd: float
    market_cap_usd: float
    liquidity_usd: float
    h1_pct_change: float
    buys_h1: int
    sells_h1: int
    buy_sell_ratio_h1: float
    whale_count: int
    bot_ratio: float
    pattern: PatternType
    score: float
    url: Optional[str] = None
    timestamp: float = field(default_factory=time.time)


@dataclass
class MarketSignal:
    """Sinal de mercado do oracle."""
    signal: OracleSignal
    confidence: int  # 0-100
    regime: MarketRegime
    cvd: float  # Cumulative Volume Delta
    lsr: float  # Long/Short Ratio
    funding_rate: float
    timestamp: float = field(default_factory=time.time)


@dataclass
class AdaptiveState:
    """Estado adaptativo do sistema."""
    regime: str
    tp_pct: float
    sl_pct: float
    entry_size_sol: float
    min_liquidity_usd: float
    min_whale_count: int
    max_bot_ratio: float
    max_age_sec: int
    consecutive_losses: int
    daily_pnl_sol: float
    is_paused: bool
    timestamp: float = field(default_factory=time.time)


@dataclass
class TradingStats:
    """Estatísticas de trading."""
    total_trades: int
    wins: int
    losses: int
    win_rate_pct: float
    total_pnl_sol: float
    avg_pnl_pct: float
    largest_win: float
    largest_loss: float
    daily_pnl_sol: float
    consecutive_losses: int
    balance_sol: float
    timestamp: float = field(default_factory=time.time)

    def get_sharpe_ratio(self, daily_vol: float) -> float:
        """Calcula Sharpe ratio simples."""
        if daily_vol <= 0:
            return 0.0
        return self.daily_pnl_sol / daily_vol


@dataclass
class SystemMetrics:
    """Métricas do sistema."""
    uptime_sec: float
    memory_usage_mb: float
    api_calls_count: int
    cache_hit_rate: float
    avg_fetch_latency_ms: float
    positions_open: int
    positions_closed: int
    timestamp: float = field(default_factory=time.time)


# Type aliases para melhor clareza
TokenMint = str  # Mint address do token
WalletAddress = str  # Endereço da wallet
SolPrice = float  # Preço em SOL
UsdPrice = float  # Preço em USD
Percentage = float  # Percentual (0.0-1.0)

# Dicionários tipados comuns
PositionDict = Dict[str, Position]
TradeHistoryDict = Dict[str, List[ClosedTrade]]
CandidateListDict = Dict[str, List[CandidateToken]]


__all__ = [
    'TradeStatus',
    'PatternType',
    'OracleSignal',
    'MarketRegime',
    'Position',
    'ClosedTrade',
    'CandidateToken',
    'MarketSignal',
    'AdaptiveState',
    'TradingStats',
    'SystemMetrics',
    'TokenMint',
    'WalletAddress',
    'SolPrice',
    'UsdPrice',
    'Percentage',
    'PositionDict',
    'TradeHistoryDict',
    'CandidateListDict',
]
