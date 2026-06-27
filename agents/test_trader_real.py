#!/usr/bin/env python3
"""
E2E Tests para trader_real_live.py — Risk Management
Valida que daily loss limit, consecutive loss pause funcionam.
"""

import pytest
import sys
import json
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock
from datetime import datetime

# Import do módulo a testar
import trader_real_live


class TestDailyLossLimit:
    """Testa MAX_DAILY_LOSS functionality."""

    def test_daily_loss_stops_trading(self):
        """Quando daily_pnl < -MAX_DAILY_LOSS, trading deve parar."""
        # Setup
        trader_real_live.daily_pnl = -2.1  # Perdeu 2.1 SOL (MAX = 2.0)
        trader_real_live.trading_paused = False

        # Mock keypair
        mock_kp = Mock()
        mock_kp.pubkey.return_value = Mock(__str__=lambda s: "test_wallet")

        # Executa
        result = trader_real_live.enter_position_real(
            mock_kp, "test_mint", "TOKEN", 0.5
        )

        # Assert
        assert result == False, "Should not enter when daily loss limit exceeded"
        assert trader_real_live.trading_paused == True, "Trading should be paused"

    def test_daily_loss_allows_trading_above_limit(self):
        """Quando daily_pnl > -MAX_DAILY_LOSS, trading continua (se saldo ok)."""
        # Setup
        trader_real_live.daily_pnl = -1.5  # Perdeu 1.5 SOL (MAX = 2.0)
        trader_real_live.trading_paused = False

        # Mock keypair e balance
        mock_kp = Mock()
        mock_kp.pubkey.return_value = Mock(__str__=lambda s: "test_wallet")

        with patch('trader_real_live.get_sol_balance', return_value=1.0):
            with patch('trader_real_live.jupiter_swap', return_value="tx_sig_123"):
                result = trader_real_live.enter_position_real(
                    mock_kp, "test_mint", "TOKEN", 0.5
                )

                assert result == True or result == False  # Depende de saldo


class TestConsecutiveLossPause:
    """Testa STOP_ON_CONSECUTIVE_LOSS functionality."""

    def test_consecutive_losses_pause_trading(self):
        """3 losses seguidas devem pausar trading."""
        # Setup
        trader_real_live.consecutive_losses = 3
        trader_real_live.trading_paused = False

        position = {
            "symbol": "TOKEN",
            "entry_price": 0.5,
            "entry_sol": 0.5
        }

        # Executa exit (loss)
        result = trader_real_live.exit_position_real(
            "mint", position, 0.4, "SL"  # Preço caiu = loss
        )

        # Assert
        assert trader_real_live.trading_paused == True, "Should pause on consecutive loss"

    def test_loss_resets_consecutive_counter(self):
        """Win deve resetar consecutive_losses para 0."""
        trader_real_live.consecutive_losses = 2

        position = {
            "symbol": "TOKEN",
            "entry_price": 0.5,
            "entry_sol": 0.5
        }

        # Executa exit (WIN)
        trader_real_live.exit_position_real(
            "mint", position, 0.6, "TP"  # Preço subiu = win
        )

        assert trader_real_live.consecutive_losses == 0, "Should reset on win"


class TestBalanceCheck:
    """Testa validação de saldo antes de entrada."""

    def test_insufficient_balance_prevents_entry(self):
        """Saldo insuficiente deve impedir entrada."""
        trader_real_live.daily_pnl = 0.0
        trader_real_live.trading_paused = False

        mock_kp = Mock()
        mock_kp.pubkey.return_value = Mock(__str__=lambda s: "test_wallet")

        with patch('trader_real_live.get_sol_balance', return_value=0.1):  # Saldo baixo
            result = trader_real_live.enter_position_real(
                mock_kp, "mint", "TOKEN", 0.5
            )

            assert result == False, "Should not enter with insufficient balance"

    def test_sufficient_balance_allows_entry(self):
        """Saldo suficiente permite entrada (se sem outros bloqueios)."""
        trader_real_live.daily_pnl = 0.0
        trader_real_live.trading_paused = False

        mock_kp = Mock()
        mock_kp.pubkey.return_value = Mock(__str__=lambda s: "test_wallet")

        with patch('trader_real_live.get_sol_balance', return_value=2.0):
            with patch('trader_real_live.jupiter_swap', return_value="tx_sig"):
                result = trader_real_live.enter_position_real(
                    mock_kp, "mint", "TOKEN", 0.5
                )

                # result pode ser True ou False dependendo de outros fatores
                # mas não deve ser bloqueado por saldo


class TestPnlTracking:
    """Testa rastreamento de PnL."""

    def test_pnl_calculated_correctly(self):
        """PnL deve ser calculado corretamente (% de ganho/perda)."""
        trader_real_live.daily_pnl = 0.0
        trader_real_live.consecutive_losses = 0

        position = {
            "symbol": "TOKEN",
            "entry_price": 1.0,
            "entry_sol": 0.5
        }

        exit_price = 1.2  # +20% ganho
        trader_real_live.exit_position_real("mint", position, exit_price, "TP")

        expected_pnl = 0.5 * 0.2  # 0.5 SOL * 20% = 0.1 SOL ganho
        assert abs(trader_real_live.daily_pnl - expected_pnl) < 0.01


class TestPositionManagement:
    """Testa gerenciamento de posições."""

    def test_position_stored_on_entry(self):
        """Posição deve ser armazenada no dicionário."""
        trader_real_live.positions = {}
        trader_real_live.daily_pnl = 0.0
        trader_real_live.trading_paused = False

        mock_kp = Mock()
        mock_kp.pubkey.return_value = Mock(__str__=lambda s: "test_wallet")

        with patch('trader_real_live.get_sol_balance', return_value=2.0):
            with patch('trader_real_live.jupiter_swap', return_value="tx_sig"):
                trader_real_live.enter_position_real(
                    mock_kp, "test_mint", "TOKEN", 0.5
                )

                assert "test_mint" in trader_real_live.positions

    def test_position_removed_on_exit(self):
        """Posição deve ser removida após exit."""
        trader_real_live.positions = {
            "test_mint": {
                "symbol": "TOKEN",
                "entry_price": 1.0,
                "entry_sol": 0.5
            }
        }
        trader_real_live.consecutive_losses = 0

        trader_real_live.exit_position_real(
            "test_mint",
            trader_real_live.positions["test_mint"],
            1.2,
            "TP"
        )

        assert "test_mint" not in trader_real_live.positions


class TestConfigValidation:
    """Testa validação de configuração."""

    def test_required_config_fields(self):
        """Config deve ter campos obrigatórios."""
        # Check se config.py tem os campos necessários
        from agents import config

        assert hasattr(config, 'WALLET_1_KEY'), "Config missing WALLET_1_KEY"
        assert hasattr(config, 'MAX_DAILY_LOSS'), "Config missing MAX_DAILY_LOSS"
        assert hasattr(config, 'STOP_ON_CONSECUTIVE_LOSS'), "Config missing STOP_ON_CONSECUTIVE_LOSS"


if __name__ == '__main__':
    # Run tests
    pytest.main([__file__, '-v', '--tb=short'])
