#!/usr/bin/env python3
"""
Caca Pump State Manager — Responsável apenas por persistência e estado
Salva/carrega memória, registra trades, gerencia histórico.
"""

import json
import time
import sys
from typing import Dict, List, Optional, Any
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "agents"))
from utils import setup_logger, safe_json_dump, safe_json_load

logger = setup_logger("CacaPumpState")


class StateManager:
    """Gerencia estado persistente do sistema."""

    def __init__(self, base_dir: str = "."):
        self.base_dir = Path(base_dir)
        self.inc_study_dir = self.base_dir / "inc_study"
        self.inc_study_dir.mkdir(parents=True, exist_ok=True)

        # Paths dos arquivos de estado
        self.memory_file = self.inc_study_dir / "memory.json"
        self.trades_file = self.inc_study_dir / "sim_trades.jsonl"
        self.live_data_file = self.inc_study_dir / "caca_pump_live_data.json"
        self.log_file = self.inc_study_dir / "live_results.txt"

        # Carrega memória existente
        self.memory = self._load_memory()

    # ─── Memory Management ─────────────────────────────────────────────────

    def _load_memory(self) -> Dict[str, Any]:
        """Carrega memória persistente."""
        try:
            data = safe_json_load(self.memory_file)
            if data:
                logger.info(f"✓ Memória carregada: {len(data)} fields")
                return data
        except Exception as e:
            logger.warning(f"Memory load error: {e}")

        # Padrão vazio
        return {
            "manipulator_wallets": {},
            "patterns": [],
            "stats": {
                "total_trades": 0,
                "wins": 0,
                "losses": 0,
                "total_pnl_sol": 0.0
            }
        }

    def save_memory(self) -> bool:
        """Salva memória persistente de forma atômica."""
        try:
            success = safe_json_dump(self.memory, self.memory_file, indent=2)
            if success:
                logger.debug("Memory saved")
                return True
        except Exception as e:
            logger.error(f"Memory save error: {e}")

        return False

    def get_memory(self) -> Dict[str, Any]:
        """Retorna memória atual."""
        return self.memory

    # ─── Manipulator Wallets ──────────────────────────────────────────────

    def add_manipulator_wallet(
        self,
        wallet_addr: str,
        pattern_type: str,
        confidence: float
    ) -> None:
        """Registra carteira manipuladora."""
        if wallet_addr not in self.memory["manipulator_wallets"]:
            self.memory["manipulator_wallets"][wallet_addr] = {
                "pattern": pattern_type,
                "confidence": confidence,
                "first_seen": time.time(),
                "appearances": 0
            }
            logger.info(f"Added manipulator: {wallet_addr} ({pattern_type}, {confidence:.0%})")

        self.memory["manipulator_wallets"][wallet_addr]["appearances"] += 1

    def is_known_manipulator(
        self,
        wallet_addr: str,
        min_confidence: float = 0.7
    ) -> bool:
        """Verifica se é carteira conhecida de manipulação."""
        if wallet_addr not in self.memory["manipulator_wallets"]:
            return False

        conf = self.memory["manipulator_wallets"][wallet_addr].get("confidence", 0)
        return conf >= min_confidence

    # ─── Trade Recording ──────────────────────────────────────────────────

    def append_trade_jsonl(self, trade: Dict[str, Any]) -> bool:
        """Anexa trade ao arquivo JSONL (append-only)."""
        try:
            with open(self.trades_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(trade, ensure_ascii=False) + "\n")
            return True
        except Exception as e:
            logger.error(f"JSONL write error: {e}")
            return False

    def get_trade_history(self, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """Carrega histórico de trades do JSONL."""
        trades = []
        try:
            if not self.trades_file.exists():
                return trades

            with open(self.trades_file, "r", encoding="utf-8") as f:
                for i, line in enumerate(f):
                    if limit and i >= limit:
                        break
                    try:
                        trade = json.loads(line.strip())
                        trades.append(trade)
                    except json.JSONDecodeError:
                        logger.warning(f"Invalid JSON line {i}")

        except Exception as e:
            logger.error(f"Trade history read error: {e}")

        return trades

    # ─── Live Data Output ─────────────────────────────────────────────────

    def save_live_data(self, data: Dict[str, Any]) -> bool:
        """Salva dados atuais para exibição no painel."""
        try:
            data["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())
            success = safe_json_dump(data, self.live_data_file, indent=2)
            return success
        except Exception as e:
            logger.error(f"Live data save error: {e}")

        return False

    def get_live_data(self) -> Dict[str, Any]:
        """Carrega dados atuais."""
        try:
            data = safe_json_load(self.live_data_file)
            if data:
                return data
        except Exception as e:
            logger.warning(f"Live data load error: {e}")

        return {
            "status": "INITIALIZING",
            "positions": {},
            "closed_trades": [],
            "stats": {}
        }

    # ─── Logging ──────────────────────────────────────────────────────────

    def log_message(self, message: str) -> None:
        """Registra mensagem em log file."""
        try:
            ts = time.strftime("%H:%M:%S")
            line = f"[{ts}] {message}\n"

            with open(self.log_file, "a", encoding="utf-8") as f:
                f.write(line)

            # Também imprime no console
            print(line.strip())

        except Exception as e:
            logger.error(f"Log write error: {e}")

    # ─── Statistics Update ────────────────────────────────────────────────

    def update_stats(self, stats: Dict[str, float]) -> None:
        """Atualiza estatísticas na memória."""
        self.memory["stats"].update(stats)
        self.save_memory()

    def get_stats(self) -> Dict[str, float]:
        """Retorna estatísticas atuais."""
        return self.memory.get("stats", {})

    def increment_stat(self, key: str, amount: float = 1.0) -> None:
        """Incrementa estatística."""
        current = self.memory["stats"].get(key, 0)
        self.memory["stats"][key] = current + amount
        self.save_memory()

    # ─── Pattern Learning ─────────────────────────────────────────────────

    def add_pattern(
        self,
        pattern_type: str,
        data: Dict[str, Any]
    ) -> None:
        """Registra padrão aprendido."""
        pattern = {
            "type": pattern_type,
            "data": data,
            "timestamp": time.time()
        }
        self.memory["patterns"].append(pattern)
        logger.debug(f"Added pattern: {pattern_type}")

    def get_patterns(self, pattern_type: Optional[str] = None) -> List[Dict]:
        """Retorna padrões registrados."""
        patterns = self.memory.get("patterns", [])

        if pattern_type:
            return [p for p in patterns if p.get("type") == pattern_type]

        return patterns

    # ─── Summary ──────────────────────────────────────────────────────────

    def get_session_summary(self) -> Dict[str, Any]:
        """Retorna resumo da sessão."""
        trades = self.get_trade_history()
        stats = self.get_stats()

        return {
            "session_start": time.strftime("%Y-%m-%d %H:%M:%S"),
            "total_trades": len(trades),
            "stats": stats,
            "manipulators_detected": len(self.memory["manipulator_wallets"]),
            "patterns_learned": len(self.memory["patterns"]),
            "memory_file_size_kb": self.memory_file.stat().st_size / 1024 if self.memory_file.exists() else 0
        }


# Singleton global
_state_manager = None


def get_state_manager(base_dir: str = ".") -> StateManager:
    """Retorna instância global do StateManager."""
    global _state_manager
    if not _state_manager:
        _state_manager = StateManager(base_dir)
    return _state_manager


__all__ = ['StateManager', 'get_state_manager']
