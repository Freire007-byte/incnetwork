#!/usr/bin/env python3
"""
Caching Layer — Phase 3
✓ Smart token cache (5min TTL)
✓ Price cache (60s TTL)
✓ Pattern cache (30min TTL)
✓ -70% API calls expected
"""

import time
import json
import sys, os
from pathlib import Path
from typing import Optional, Dict, Any

sys.path.insert(0, str(Path(__file__).parent))
import config as Config

class Cache:
    """Simple in-memory cache com TTL."""

    def __init__(self, name: str, default_ttl: int = 300):
        self.name = name
        self.default_ttl = default_ttl
        self.data = {}  # key -> (value, timestamp)

    def get(self, key: str, ttl: Optional[int] = None) -> Optional[Any]:
        """Get value if not expired."""
        ttl = ttl or self.default_ttl

        if key not in self.data:
            return None

        value, ts = self.data[key]
        age = time.time() - ts

        if age > ttl:
            # Expired, remove
            del self.data[key]
            return None

        return value

    def set(self, key: str, value: Any):
        """Set value with current timestamp."""
        self.data[key] = (value, time.time())

    def clear(self):
        """Clear all cache."""
        self.data.clear()

    def cleanup_expired(self, ttl: int = 300):
        """Remove expired entries."""
        now = time.time()
        expired = [k for k, (_, ts) in self.data.items() if now - ts > ttl]
        for k in expired:
            del self.data[k]
        return len(expired)

    def stats(self) -> Dict[str, int]:
        """Cache statistics."""
        return {
            "name": self.name,
            "entries": len(self.data),
            "memory_kb": len(json.dumps(self.data)) / 1024,
        }

# ─────────────────────────────────────────────────────────
# CACHES GLOBAIS (Um por tipo de dado)
# ─────────────────────────────────────────────────────────

# Tokens (mint → token data) — 5 min
TOKEN_CACHE = Cache("tokens", ttl=300)

# Preços (mint → price) — 60s
PRICE_CACHE = Cache("prices", ttl=60)

# Padrões (mint → pattern) — 30min
PATTERN_CACHE = Cache("patterns", ttl=1800)

# Saldos (wallet → balance) — 30s
BALANCE_CACHE = Cache("balances", ttl=30)

# Carteiras (wallet → addresses) — 2h
WALLET_CACHE = Cache("wallets", ttl=7200)

# SOL price (singletons) — 60s
SOL_PRICE_CACHE = Cache("sol_price", ttl=60)

# ─────────────────────────────────────────────────────────
# UTILITY FUNCTIONS
# ─────────────────────────────────────────────────────────

def get_token_cached(mint: str, fetch_func=None) -> Optional[Dict]:
    """
    Get token data com cache.

    fetch_func: função para buscar se não em cache
    """
    # Try cache primeiro
    cached = TOKEN_CACHE.get(mint)
    if cached:
        return cached

    # Fetch se não em cache
    if fetch_func:
        try:
            data = fetch_func(mint)
            if data:
                TOKEN_CACHE.set(mint, data)
            return data
        except Exception as e:
            print(f"⚠️  Erro fetching token {mint}: {e}")
            return None

    return None

def get_price_cached(mint: str, fetch_func=None) -> Optional[float]:
    """Get price com cache (60s)."""
    cached = PRICE_CACHE.get(mint, ttl=60)
    if cached is not None:
        return cached

    if fetch_func:
        try:
            price = fetch_func(mint)
            if price and price > 0:
                PRICE_CACHE.set(mint, price)
            return price
        except Exception as e:
            print(f"⚠️  Erro fetching price {mint}: {e}")
            return None

    return None

def get_sol_price_cached(fetch_func=None) -> float:
    """Get SOL price com cache (60s)."""
    cached = SOL_PRICE_CACHE.get("SOL", ttl=60)
    if cached is not None:
        return cached

    if fetch_func:
        try:
            price = fetch_func()
            if price and price > 0:
                SOL_PRICE_CACHE.set("SOL", price)
                return price
        except Exception as e:
            print(f"⚠️  Erro fetching SOL price: {e}")

    # Fallback
    return 175.0

def get_pattern_cached(mint: str, fetch_func=None) -> Optional[Dict]:
    """Get pattern com cache (30min)."""
    cached = PATTERN_CACHE.get(mint, ttl=1800)
    if cached:
        return cached

    if fetch_func:
        try:
            pattern = fetch_func(mint)
            if pattern:
                PATTERN_CACHE.set(mint, pattern)
            return pattern
        except Exception as e:
            print(f"⚠️  Erro fetching pattern {mint}: {e}")
            return None

    return None

def invalidate_cache(key: str, cache: Cache = None):
    """Invalida entrada de cache manualmente."""
    if cache:
        try:
            del cache.data[key]
        except:
            pass

def cleanup_all_caches():
    """Limpa todos os caches expirados."""
    caches = [TOKEN_CACHE, PRICE_CACHE, PATTERN_CACHE, BALANCE_CACHE, WALLET_CACHE, SOL_PRICE_CACHE]
    total_removed = 0

    for cache in caches:
        removed = cache.cleanup_expired(cache.default_ttl)
        total_removed += removed

    return total_removed

def get_all_stats() -> Dict:
    """Retorna stats de todos os caches."""
    caches = [TOKEN_CACHE, PRICE_CACHE, PATTERN_CACHE, BALANCE_CACHE, WALLET_CACHE, SOL_PRICE_CACHE]
    stats = {
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "caches": []
    }

    for cache in caches:
        stats["caches"].append(cache.stats())

    return stats

# ─────────────────────────────────────────────────────────
# MONITORING
# ─────────────────────────────────────────────────────────

def log_cache_stats():
    """Log cache statistics."""
    stats = get_all_stats()
    print("\n📊 Cache Statistics:")
    print("="*50)
    for cache_stat in stats["caches"]:
        print(f"  {cache_stat['name']:15} | Entries: {cache_stat['entries']:4} | {cache_stat['memory_kb']:.1f}KB")
    print("="*50)

if __name__ == "__main__":
    print("💾 Caching Layer — Phase 3")
    print("="*60)

    # Teste básico
    TOKEN_CACHE.set("mint1", {"symbol": "TEST", "price": 0.001})
    TOKEN_CACHE.set("mint2", {"symbol": "TEST2", "price": 0.002})

    print("✓ Stored 2 tokens in cache")
    print(f"✓ Retrieved: {TOKEN_CACHE.get('mint1')}")

    # Stats
    log_cache_stats()

    print("\n✅ Caching layer ready!")
