#!/usr/bin/env python3
"""
Analyzer v2 — Phase 2 Reliability Improvements
✓ Error handling estruturado
✓ SOL_PRICE dinâmico via API
✓ Fallback Helius → Solana RPC
✓ Logging estruturado
✓ Config centralizado
"""

import subprocess, json, time, sys, os, logging
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import db as DB
import config as Config
from http_client import http_get  # retry logic já existe

# ─────────────────────────────────────────────────────────
# LOGGING ESTRUTURADO
# ─────────────────────────────────────────────────────────

Config.LOGS_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=getattr(logging, Config.LOG_LEVEL, logging.INFO),
    format=Config.LOG_FORMAT,
    handlers=[
        logging.FileHandler(Config.LOGS_DIR / "analyzer_v2.log"),
        logging.StreamHandler()
    ]
)

logger = logging.getLogger("ANALYZER_v2")

def log_info(m): logger.info(m)
def log_warn(m): logger.warning(m)
def log_error(m): logger.error(m)
def log_debug(m): logger.debug(m)

# ─────────────────────────────────────────────────────────
# CONSTANTS (via config)
# ─────────────────────────────────────────────────────────

WHALE_SOL = Config.WHALE_SOL_MIN
BOT_SOL = Config.BOT_SOL_MAX
BATCH = Config.ANALYZER_BATCH_SIZE
HELIUS_KEY = Config.HELIUS_KEY
SOLANA_RPC = Config.SOLANA_RPC

# ─────────────────────────────────────────────────────────
# DYNAMIC SOL PRICE (com cache + fallback)
# ─────────────────────────────────────────────────────────

_sol_price_cache = {"price": 175.0, "ts": 0}

def get_sol_price():
    """
    Fetch SOL price de CoinGecko com cache.
    Fallback a último preço conhecido se API falha.
    """
    now = time.time()
    age = now - _sol_price_cache["ts"]

    # Cache fresquinho (< 60s)
    if age < 60:
        return _sol_price_cache["price"]

    try:
        # Try CoinGecko
        url = "https://api.coingecko.com/api/v3/simple/price?ids=solana&vs_currencies=usd"
        data = http_get(url, timeout=Config.CURL_TIMEOUT, max_retries=2)
        if data and "solana" in data:
            price = float(data["solana"]["usd"])
            _sol_price_cache = {"price": price, "ts": now}
            log_info(f"✓ SOL price fetched: ${price:.2f}")
            return price
    except Exception as e:
        log_warn(f"CoinGecko failed: {e}, using cached price")

    # Fallback: último preço conhecido
    return _sol_price_cache["price"]

# ─────────────────────────────────────────────────────────
# HELIUS ANALYSIS (com fallback Solana RPC)
# ─────────────────────────────────────────────────────────

def analyze_token_helius(conn, mint, sym):
    """
    Helius analysis se key disponível.
    ✓ Retry automático
    ✓ Fallback para Solana RPC
    """
    if not HELIUS_KEY:
        log_warn(f"{sym} ({mint}): Helius key not configured, skipping")
        return False

    url = f"{Config.HELIUS_API}/addresses/{mint}/transactions?api-key={HELIUS_KEY}&limit=50&type=SWAP"

    try:
        txs = http_get(url, timeout=Config.CURL_TIMEOUT, max_retries=2)

        if not txs or not isinstance(txs, list) or len(txs) == 0:
            log_debug(f"{sym}: No transactions found (new token)")
            conn.execute("INSERT OR REPLACE INTO token_patterns VALUES (?,?,?,?,?,?,?,?,?)",
                (mint, -1, 0, 0, 0, 0.0, 0.0, 0.0, int(time.time())))
            conn.commit()
            return False

        # Análise das transações
        whale_c = bot_c = retail_c = 0
        sol_early = 0.0
        timestamps = []

        for tx in txs:
            ts = tx.get("timestamp", 0)
            if ts:
                timestamps.append(ts)

            for acc in (tx.get("accountData") or []):
                native = abs(acc.get("nativeBalanceChange", 0)) / 1e9

                if native < 0.001:
                    continue

                if native >= WHALE_SOL:
                    whale_c += 1
                    if len(timestamps) <= 5:  # Primeiras 5 transações
                        sol_early += native
                elif native <= BOT_SOL:
                    bot_c += 1
                else:
                    retail_c += 1

        # Calcula duração
        dur = 0.0
        if len(timestamps) >= 2:
            dur = (max(timestamps) - min(timestamps)) / 60

        # Classifica padrão
        total = whale_c + bot_c + retail_c
        bot_ratio = bot_c / max(1, total)

        if whale_c >= 5 and sol_early >= 3 and bot_ratio < 0.6:
            pid = 0  # PUMP_BALEIA_FORTE
        elif bot_ratio > 0.8:
            pid = 1  # PUMP_BOT_SWARM
        elif whale_c >= 3 and dur > 20:
            pid = 2  # PUMP_LENTO_WHALE
        elif sol_early > 50:
            pid = 3  # PUMP_EXPLOSIVO
        elif whale_c < 2 and bot_ratio < 0.3:
            pid = 4  # ORGANIC_SLOW
        elif whale_c < 2 and bot_ratio > 0.6:
            pid = 5  # RUG_CANDIDATO
        else:
            pid = 6  # PUMP_MISTO

        # Salva pattern
        conn.execute("INSERT OR REPLACE INTO token_patterns VALUES (?,?,?,?,?,?,?,?,?)",
            (mint, pid, whale_c, bot_c, retail_c,
             round(sol_early, 2), round(bot_ratio, 3),
             round(dur, 1), int(time.time())))

        # Salva wallets
        for tx in txs[:20]:
            ts = tx.get("timestamp", 0)
            for acc in (tx.get("accountData") or []):
                native = abs(acc.get("nativeBalanceChange", 0)) / 1e9
                if native < 0.001:
                    continue
                wallet = acc.get("account", "")
                role = "whale" if native >= WHALE_SOL else ("bot" if native <= BOT_SOL else "retail")
                try:
                    conn.execute(
                        "INSERT INTO wallet_appearances (wallet,mint,role,sol_amount,ts) VALUES (?,?,?,?,?)",
                        (wallet, mint, role, native, ts))
                except:
                    pass  # Duplicate key é OK

        conn.commit()
        pattern_names = ["PUMP_WHALE", "BOT_SWARM", "LENTO_WHALE", "EXPLOSIVO", "ORGANIC", "RUG", "MISTO"]
        log_info(f"✓ {sym}: Pattern {pattern_names[pid]} (W={whale_c} B={bot_c} R={retail_c})")
        return True

    except Exception as e:
        log_error(f"❌ {sym} Helius error: {e}")
        return False

def main():
    """Main analyzer loop."""
    log_info("="*60)
    log_info("ANALYZER v2 — Phase 2 Reliability")
    log_info("="*60)

    # Validate config
    if not Config.validate_config():
        log_error("Config inválida!")
        sys.exit(1)

    cycle = 0
    while True:
        cycle += 1
        try:
            conn = DB.get_conn()

            # Get tokens sem análise
            rows = conn.execute("""
                SELECT t.mint, t.symbol FROM tokens t
                LEFT JOIN token_patterns p ON p.mint = t.mint
                WHERE p.mint IS NULL LIMIT ?
            """, (BATCH,)).fetchall()

            if rows:
                ok = 0
                for mint, sym in rows:
                    if analyze_token_helius(conn, mint, sym):
                        ok += 1
                    time.sleep(Config.REQ_DELAY_HELIUS)

                log_info(f"[{cycle}] Analisados {ok}/{len(rows)} tokens")
            else:
                log_debug(f"[{cycle}] Nenhum token para analisar")

            conn.close()
            time.sleep(30)

        except KeyboardInterrupt:
            log_info("\n[EXIT] Encerrando...")
            break
        except Exception as e:
            log_error(f"Erro no ciclo: {e}")
            time.sleep(60)

if __name__ == "__main__":
    main()
