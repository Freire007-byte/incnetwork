#!/usr/bin/env python3
"""
Caca Pump Scanner — Responsável apenas por coleta de dados do pump.fun
Separa concerns: scanner (http) vs engine (lógica) vs state (persistência)
"""

import subprocess
import json
import time
import urllib.request
import ssl
from typing import Optional, Dict, List, Any
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent / "agents"))
from utils import setup_logger, get_client
from types import SolPrice, UsdPrice, TokenMint

logger = setup_logger("CacaPumpScanner")


class PumpFunScanner:
    """Scanner para pump.fun — coleta dados de tokens."""

    def __init__(self):
        self.client = get_client("pumpfun", rate_limit_delay=3.0, timeout=8)
        self._sol_price_cache = 150.0
        self._sol_price_ts = 0.0
        self._NO_WIN = 0x08000000 if sys.platform == 'win32' else 0

    # ─── SOL Price ───────────────────────────────────────────────────────

    def get_sol_price(self) -> SolPrice:
        """Retorna preço SOL/USD com cache de 60s."""
        if time.time() - self._sol_price_ts < 60:
            return self._sol_price

        try:
            data = self.client.get_json(
                "https://api.coinbase.com/v2/prices/SOL-USD/spot",
                default=None
            )
            if data and data.get("data", {}).get("amount"):
                price = float(data["data"]["amount"])
                self._sol_price_cache = price
                self._sol_price_ts = time.time()
                return price
        except Exception as e:
            logger.warning(f"Failed to fetch SOL price: {e}")

        return self._sol_price_cache

    # ─── Pump.fun API ────────────────────────────────────────────────────

    def _curl(
        self,
        url: str,
        timeout: int = 8,
        method: str = "GET",
        body: Optional[str] = None,
        headers: Optional[List[str]] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Curl helper com curl binary (contorna Cloudflare blocks).
        """
        cmd = [
            "curl", "-s", "--max-time", str(timeout),
            "-A", "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
            "-H", "Accept: application/json",
            "-H", "Origin: https://pump.fun",
            "-H", "Referer: https://pump.fun/"
        ]

        if headers:
            for h in headers:
                cmd += ["-H", h]

        if method == "POST" and body:
            cmd += ["-X", "POST", "-H", "Content-Type: application/json", "-d", body]

        cmd.append(url)

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                timeout=timeout + 4,
                creationflags=self._NO_WIN
            )
            return json.loads(result.stdout) if result.stdout else None
        except Exception as e:
            logger.debug(f"Curl error: {e}")
            return None

    def get_token_detail(self, mint: TokenMint) -> Optional[Dict[str, Any]]:
        """
        Retorna detalhes do token via pump.fun API.
        Tenta curl primeiro, depois urllib se falhar.
        """
        # Tenta curl (mais rápido)
        detail = self._curl(
            f"https://frontend-api.pump.fun/coins/{mint}",
            timeout=6
        )
        if detail:
            return detail

        # Fallback: urllib (mais lento mas contorna alguns bloqueios)
        url = f"https://frontend-api.pump.fun/coins/{mint}"
        try:
            req = urllib.request.Request(
                url,
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
                    "Accept": "application/json",
                    "Origin": "https://pump.fun",
                    "Referer": "https://pump.fun/"
                }
            )
            ctx = ssl.create_default_context()
            with urllib.request.urlopen(req, timeout=8, context=ctx) as resp:
                return json.loads(resp.read().decode())
        except Exception as e:
            logger.debug(f"urllib error for {mint}: {e}")
            return None

    # ─── Price Calculation ───────────────────────────────────────────────

    def get_token_price_usd(
        self,
        mint: TokenMint,
        detail: Optional[Dict] = None
    ) -> Optional[UsdPrice]:
        """
        Calcula preço USD de um token via bonding curve.
        """
        if not detail:
            detail = self.get_token_detail(mint)

        if not detail:
            return None

        try:
            v_sol = detail.get("virtual_sol_reserves", 0)
            v_tok = detail.get("virtual_token_reserves", 0)

            if not v_sol or not v_tok:
                return None

            # Bonding curve: price_sol = v_sol / v_tok
            price_sol = v_sol / v_tok  # em lamports/microtokens
            # Ajuste de escala pump.fun (empírico)
            price_sol_real = price_sol / 1e3
            # Converter para USD
            sol_price = self.get_sol_price()
            return price_sol_real * sol_price

        except Exception as e:
            logger.debug(f"Price calc error for {mint}: {e}")
            return None

    def get_token_market_cap_usd(self, detail: Optional[Dict]) -> float:
        """Extrai market cap USD do detalhe do token."""
        if not detail:
            return 0.0
        return float(detail.get("usd_market_cap") or 0)

    def get_token_liquidity_usd(self, detail: Optional[Dict]) -> float:
        """Estima liquidez USD do token."""
        if not detail:
            return 0.0
        # pump.fun: liquidez = virtual_sol_reserves * sol_price
        sol_reserves = detail.get("virtual_sol_reserves", 0)
        if not sol_reserves:
            return 0.0
        return (sol_reserves / 1e9) * self.get_sol_price()

    # ─── Token Monitoring ────────────────────────────────────────────────

    def monitor_token_price(
        self,
        mint: TokenMint,
        retry_count: int = 3,
        retry_delay: int = 20
    ) -> Optional[Dict[str, Any]]:
        """
        Monitora preço de um token com retries.
        """
        for attempt in range(retry_count):
            try:
                detail = self.get_token_detail(mint)
                if not detail:
                    if attempt < retry_count - 1:
                        logger.debug(f"Retry {attempt + 1}/{retry_count} for {mint}")
                        time.sleep(retry_delay)
                    continue

                price_usd = self.get_token_price_usd(mint, detail)
                market_cap = self.get_token_market_cap_usd(detail)
                liquidity = self.get_token_liquidity_usd(detail)

                return {
                    "mint": mint,
                    "price_usd": price_usd,
                    "market_cap_usd": market_cap,
                    "liquidity_usd": liquidity,
                    "detail": detail,
                    "timestamp": time.time()
                }

            except Exception as e:
                logger.debug(f"Error monitoring {mint}: {e}")
                if attempt < retry_count - 1:
                    time.sleep(retry_delay)

        return None

    # ─── Token Analysis ─────────────────────────────────────────────────

    def analyze_token_movement(
        self,
        current_price: float,
        entry_price: float,
        entry_time: float
    ) -> Dict[str, Any]:
        """
        Analisa movimento do token desde entrada.
        """
        pnl_pct = (current_price - entry_price) / entry_price * 100
        hold_time_min = (time.time() - entry_time) / 60.0

        return {
            "current_price": current_price,
            "entry_price": entry_price,
            "pnl_pct": pnl_pct,
            "hold_time_min": hold_time_min,
            "is_profit": pnl_pct > 0,
            "price_change_pct": pnl_pct
        }


# Singleton global
_scanner = None


def get_scanner() -> PumpFunScanner:
    """Retorna instância global do scanner."""
    global _scanner
    if not _scanner:
        _scanner = PumpFunScanner()
    return _scanner


__all__ = ['PumpFunScanner', 'get_scanner']
