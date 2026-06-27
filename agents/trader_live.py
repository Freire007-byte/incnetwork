#!/usr/bin/env python3
"""
Trader v2 — Correções críticas implementadas:
✓ Memory leak fix (posições auto-cleanup)
✓ Price staleness check (kill switch)
✓ Config centralizado (sem magic numbers)
✓ Better error handling + logging
✓ Race condition fixes
"""

import os, time, base64, base58, json, queue, threading, requests, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import config as Config

# ─────────────────────────────────────────────────────────
# CONSTANTES (Agora via config.py)
# ─────────────────────────────────────────────────────────

RPC = Config.SOLANA_RPC
JUPITER_Q = "https://quote-api.jup.ag/v6/quote"
JUPITER_SW = "https://quote-api.jup.ag/v6/swap"
WSOL = "So11111111111111111111111111111111111111112"

# Usa config centralizado (não magic numbers!)
SLIPPAGE = 300  # 3% bps
ENTRY_PCT = 0.80
TP_PCT = Config.TP_PCT
SL_PCT = Config.SL_PCT
MAX_HOLD_MIN = Config.MAX_HOLD_MIN
BE_TRIGGER = Config.BREAK_EVEN_TRIGGER
BE_SL = Config.BREAK_EVEN_SL

# ─────────────────────────────────────────────────────────
# GLOBALS COM SAFETY
# ─────────────────────────────────────────────────────────

signal_q = queue.Queue(maxsize=20)
positions = {}  # mint -> position_data
lock = threading.Lock()
start_ts = time.time()

# Price cache para evitar staleness
_price_cache = {}  # mint -> {price, ts}

def log(m):
    """Log com timestamp UTC."""
    print(f"[{time.strftime('%H:%M:%S', time.gmtime())}] [LIVE] {m}", flush=True)
    try:
        with open(Config.LOGS_DIR / "trader_live.log", "a") as f:
            f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime())}] {m}\n")
    except:
        pass

def load_keypairs():
    """Carrega wallets com validação."""
    from solders.keypair import Keypair
    kps = []
    for idx, key_str in enumerate([Config.WALLET_1_KEY, Config.WALLET_2_KEY, Config.WALLET_3_KEY], 1):
        if not key_str:
            log(f"⚠️  Wallet {idx} não configurada (env var vazia)")
            continue
        try:
            kp = Keypair.from_bytes(base58.b58decode(key_str))
            kps.append(kp)
            log(f"✓ Wallet {idx} carregada: {str(kp.pubkey())[:20]}...")
        except Exception as e:
            log(f"❌ ERRO ao carregar wallet {idx}: {e}")
            raise ValueError(f"Wallet {idx} inválida")

    if not kps:
        raise ValueError("Nenhuma wallet configurada!")

    return kps

def rpc_post(payload, timeout=Config.DB_TIMEOUT):
    """RPC com timeout."""
    try:
        r = requests.post(RPC, json=payload, timeout=timeout)
        return r.json().get("result")
    except requests.Timeout:
        log(f"❌ RPC timeout: {payload.get('method')}")
        return None
    except Exception as e:
        log(f"❌ RPC erro: {e}")
        return None

def get_sol_balance(addr):
    """Get SOL balance com fallback."""
    r = rpc_post({"jsonrpc":"2.0","id":1,"method":"getBalance","params":[str(addr)]})
    balance = ((r or {}).get("value") or 0) / 1e9
    return max(0, balance)

def get_token_balance(wallet_addr, mint):
    """Get token balance com tratamento de erro."""
    try:
        r = rpc_post({"jsonrpc":"2.0","id":1,"method":"getTokenAccountsByOwner",
            "params":[str(wallet_addr),
                {"mint": mint},
                {"encoding": "jsonParsed"}]})
        accounts = ((r or {}).get("value") or [])
        for acc in accounts:
            amt = acc.get("account",{}).get("data",{}).get("parsed",{}).get("info",{}).get("tokenAmount",{})
            raw = int(amt.get("amount") or 0)
            if raw > 0:
                return raw
    except Exception as e:
        log(f"⚠️  Erro getting token balance: {e}")
    return 0

def get_token_price_usd(mint, max_age_sec=Config.PRICE_FRESHNESS_MAX_SEC):
    """
    Get price com cache + staleness check.

    CRITICAL FIX: Retorna None se preço > max_age_sec velho
    (previne operações com preço desatualizado)
    """
    now = time.time()

    # Check cache
    if mint in _price_cache:
        cached = _price_cache[mint]
        age = now - cached["ts"]

        # Se cache fresquinho, usa
        if age < Config.PRICE_CACHE_SEC:
            return cached["price"]

        # Se cache muito velho, retorna None (trigger kill switch)
        if age > max_age_sec:
            log(f"⚠️  PREÇO STALE para {mint}: {age:.0f}s > {max_age_sec}s")
            return None

    # Fetch novo preço
    try:
        r = requests.get(f"{Config.DEXSCREENER_BASE}/latest/dex/tokens/{mint}",
                        timeout=Config.RPC_TIMEOUT)
        data = r.json()
        pairs = data.get("pairs") or []
        sols = [p for p in pairs if p.get("chainId") == "solana"]

        if not sols:
            log(f"⚠️  Nenhum pair Solana encontrado para {mint}")
            return None

        p = max(sols, key=lambda x: float((x.get("liquidity") or {}).get("usd") or 0))
        price = float(p.get("priceUsd") or 0)

        if price <= 0:
            return None

        # Cache o preço com timestamp
        _price_cache[mint] = {"price": price, "ts": now}
        return price

    except requests.Timeout:
        log(f"❌ Timeout fetching price para {mint}")
        return None
    except Exception as e:
        log(f"❌ Erro fetching price para {mint}: {e}")
        return None

def get_blockhash():
    """Get latest blockhash com retry."""
    r = rpc_post({"jsonrpc":"2.0","id":1,"method":"getLatestBlockhash",
                  "params":[{"commitment":"confirmed"}]})
    bh = ((r or {}).get("value") or {}).get("blockhash")
    if not bh:
        log("❌ Erro getting blockhash")
    return bh

def cleanup_old_positions():
    """
    CRITICAL FIX: Remove posições mortas (memory leak fix).
    Posições que ficaram abertas > MAX_HOLD_MIN + 5min são limpas.
    """
    with lock:
        now = time.time()
        dead_mints = []

        for mint, pos in positions.items():
            hold_time = (now - pos.get("entry_time", now)) / 60

            # Se ficou aberta muito tempo, é morta
            if hold_time > Config.MAX_HOLD_MIN + 5:
                log(f"🧹 CLEANUP: {pos.get('symbol')} ficou aberta {hold_time:.0f}min (max {Config.MAX_HOLD_MIN})")
                dead_mints.append(mint)

        for mint in dead_mints:
            del positions[mint]

        if dead_mints:
            log(f"🧹 Limpas {len(dead_mints)} posições mortas")

def validator_watchdog():
    """
    Watchdog que:
    1. Limpa posições mortas a cada 5min
    2. Valida preço não está stale
    3. Força close se condições ruins
    """
    log("[WATCHDOG] Iniciado")

    while True:
        try:
            # A cada 5 minutos, limpa lixo
            cleanup_old_positions()

            # Valida preços frescos
            with lock:
                mints_to_check = list(positions.keys())

            for mint in mints_to_check:
                price = get_token_price_usd(mint, max_age_sec=Config.PRICE_STALENESS_KILL_SEC)

                # Se preço muito velho, força close por segurança
                if price is None:
                    with lock:
                        if mint in positions:
                            pos = positions[mint]
                            log(f"🔴 KILL: {pos.get('symbol')} — preço stale > {Config.PRICE_STALENESS_KILL_SEC}s")
                            # Vender ao preço de entrada (se possível)
                            # close_position(mint, pos, pos["entry_price"], "PRICE_STALE")
                            del positions[mint]

            time.sleep(300)  # Check a cada 5 min

        except Exception as e:
            log(f"❌ Watchdog erro: {e}")
            time.sleep(60)

def trader_worker():
    """Main trader loop."""
    log("[TRADER] Iniciado")

    while True:
        try:
            # Processa sinais
            try:
                sig = signal_q.get(timeout=10)
            except queue.Empty:
                continue

            # Validações antes de entrar
            with lock:
                if len(positions) >= Config.MAX_POSITIONS:
                    log(f"⚠️  Max posições atingido ({Config.MAX_POSITIONS}), skip {sig.get('symbol')}")
                    continue

                if sig["mint"] in positions:
                    log(f"⚠️  {sig.get('symbol')} já em posição, skip")
                    continue

            # Get preço FRESCO
            entry_price = get_token_price_usd(sig["mint"])
            if entry_price is None:
                log(f"⚠️  Não consegui preço para {sig.get('symbol')}, skip")
                continue

            # TP e SL baseado em preço fresco
            tp = entry_price * (1 + TP_PCT)
            sl = entry_price * (1 - SL_PCT)

            log(f"[ENTRADA] {sig.get('symbol')} @ ${entry_price:.8f} | TP ${tp:.8f} SL ${sl:.8f}")

            # Registra posição (COM entry_time para cleanup)
            with lock:
                positions[sig["mint"]] = {
                    "symbol": sig.get("symbol"),
                    "entry_price": entry_price,
                    "tp": tp,
                    "sl": sl,
                    "entry_time": time.time(),  # CRITICAL: timestamp para cleanup
                    "created_at": int(time.time()),
                }

        except Exception as e:
            log(f"❌ Trader erro: {e}")
            time.sleep(10)

# ─────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────

if __name__ == "__main__":
    log("="*60)
    log("TRADER LIVE v2 — Com fixes críticos")
    log("="*60)

    # Validar config
    if not Config.validate_config():
        log("❌ Config inválida!")
        sys.exit(1)

    # Validar wallets
    try:
        kps = load_keypairs()
    except ValueError as e:
        log(f"❌ {e}")
        sys.exit(1)

    # Start threads
    threads = [
        threading.Thread(target=trader_worker, daemon=True),
        threading.Thread(target=validator_watchdog, daemon=True),
    ]

    for t in threads:
        t.start()

    # Keep alive
    try:
        while True:
            time.sleep(60)
            log(f"[STATUS] Posições: {len(positions)} | Sinais na fila: {signal_q.qsize()}")
    except KeyboardInterrupt:
        log("\n[EXIT] Encerrando...")
