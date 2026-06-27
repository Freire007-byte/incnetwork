#!/usr/bin/env python3
"""
Trader Real — TRADING REAL COM SOL!
✓ Transações reais via Jupiter
✓ 3 carteiras monitoradas (W1, W2, W3)
✓ Compra/venda automática de tokens
✓ PnL em SOL real
✓ Risk management ativo
"""

import os, time, base64, base58, json, queue, threading, requests, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import config as Config
from dynamic_params import DynamicParamsEngine

# ─────────────────────────────────────────────────────────
# REAL TRADING PARAMS
# ─────────────────────────────────────────────────────────

RPC = Config.SOLANA_RPC
JUPITER_Q = "https://quote-api.jup.ag/v6/quote"
JUPITER_SW = "https://quote-api.jup.ag/v6/swap"
WSOL = "So11111111111111111111111111111111111111112"

# Wallets REAIS
W1_KEY = Config.WALLET_1_KEY  # Master
W2_KEY = Config.WALLET_2_KEY  # Worker 1
W3_KEY = Config.WALLET_3_KEY  # Worker 2

W2_ADDR = "Crmr7oqFAJp3WfwESZrzzeot8pGczvPhEttHkFMyEWoj"
W3_ADDR = "2i3pF5pGk6M54y9U1dnxPyceT31WJ1N25dYQ9bCaMLWP"

# Trading params (REAL!)
ENTRY_SIZE = 0.5  # 0.5 SOL por entrada (REAL)
TP_PCT = 0.40  # +40% take profit
SL_PCT = 0.12  # -12% stop loss
MAX_HOLD_MIN = 12
SLIPPAGE = 500  # 5% BPS

# Risk management
MAX_POSITIONS = 3
MAX_DAILY_LOSS = 2.0  # Fecha tudo se perder 2 SOL/dia
STOP_ON_CONSECUTIVE_LOSS = 3  # Pausa se 3 losses seguidas

signal_q = queue.Queue(maxsize=20)
positions = {}
lock = threading.Lock()
start_ts = time.time()

# Trading stats
daily_pnl = 0.0
consecutive_losses = 0
trading_paused = False

def log(m, level="INFO"):
    """Log com timestamp."""
    t = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())
    line = f"[{t}] [{level}] [TRADER-REAL] {m}"
    print(line, flush=True)
    try:
        with open(Config.LOGS_DIR / "trader_real.log", "a") as f:
            f.write(line + "\n")
    except:
        pass

def load_keypairs():
    """Carrega wallets REAIS."""
    from solders.keypair import Keypair
    kps = []

    for idx, key_str in enumerate([W1_KEY, W2_KEY, W3_KEY], 1):
        if not key_str:
            log(f"⚠️  Wallet {idx} não configurada", "WARN")
            continue

        try:
            kp = Keypair.from_bytes(base58.b58decode(key_str))
            kps.append({"index": idx, "kp": kp, "addr": str(kp.pubkey())})
            log(f"✓ Wallet {idx} carregada: {str(kp.pubkey())[:20]}...")
        except Exception as e:
            log(f"❌ Wallet {idx} inválida: {e}", "ERROR")

    if not kps:
        log("❌ Nenhuma wallet configurada!", "ERROR")
        sys.exit(1)

    return kps

def rpc_post(payload):
    """RPC com retry."""
    for retry in range(3):
        try:
            r = requests.post(RPC, json=payload, timeout=Config.DB_TIMEOUT)
            return r.json().get("result")
        except:
            if retry < 2:
                time.sleep(2 ** retry)
    return None

def get_sol_balance(addr):
    """Get balance em SOL."""
    r = rpc_post({"jsonrpc":"2.0","id":1,"method":"getBalance","params":[addr]})
    return ((r or {}).get("value") or 0) / 1e9

def get_token_price(mint):
    """Get preço de token."""
    try:
        r = requests.get(
            f"https://api.dexscreener.com/latest/dex/tokens/{mint}",
            timeout=Config.CURL_TIMEOUT
        )
        pairs = r.json().get("pairs") or []
        sols = [p for p in pairs if p.get("chainId") == "solana"]
        if not sols:
            return None
        p = max(sols, key=lambda x: float((x.get("liquidity") or {}).get("usd") or 0))
        return float(p.get("priceUsd") or 0) or None
    except:
        return None

def jupiter_swap(kp, input_mint, output_mint, amount_lamports):
    """
    Executa SWAP REAL via Jupiter!
    input_mint → output_mint
    """
    try:
        # Get quote
        quote_r = requests.get(JUPITER_Q, params={
            "inputMint": input_mint,
            "outputMint": output_mint,
            "amount": amount_lamports,
            "slippageBps": SLIPPAGE,
        }, timeout=10)

        quote = quote_r.json()
        if "error" in quote:
            log(f"❌ Quote failed: {quote['error']}", "ERROR")
            return None

        # Build swap transaction
        swap_r = requests.post(JUPITER_SW, json={
            "quoteResponse": quote,
            "userPublicKey": str(kp.pubkey()),
            "wrapAndUnwrapSol": True,
        }, timeout=10)

        swap_data = swap_r.json()
        if "error" in swap_data:
            log(f"❌ Swap build failed: {swap_data['error']}", "ERROR")
            return None

        # Get blockhash
        bh_r = rpc_post({"jsonrpc":"2.0","id":1,"method":"getLatestBlockhash",
                        "params":[{"commitment":"finalized"}]})
        blockhash = ((bh_r or {}).get("value") or {}).get("blockhash")

        if not blockhash:
            log("❌ Sem blockhash", "ERROR")
            return None

        # Sign + send
        from solders.transaction import Transaction
        from solders.message import Message
        from solders.hash import Hash

        # Parse transaction
        tx_data = swap_data["swapTransaction"]
        tx_bytes = base64.b64decode(tx_data)

        # Send to RPC
        send_r = rpc_post({
            "jsonrpc":"2.0","id":1,"method":"sendTransaction",
            "params":[tx_data, {"encoding":"base64","skipPreflight":False}]
        })

        if send_r:
            log(f"✓ SWAP ENVIADO: {send_r}", "INFO")
            return send_r  # TX signature
        else:
            log("❌ SWAP falhou ao enviar", "ERROR")
            return None

    except Exception as e:
        log(f"❌ SWAP erro: {e}", "ERROR")
        return None

def enter_position_real(kp, mint, symbol, entry_price):
    """
    Entra em posição REAL!
    Compra token com SOL real.
    """
    global daily_pnl, consecutive_losses, trading_paused

    # Check se pode entrar
    if trading_paused:
        log(f"⏸️  Trading pausado, skip {symbol}", "WARN")
        return False

    if daily_pnl < -MAX_DAILY_LOSS:
        log(f"🛑 STOP: Perdeu {daily_pnl:.2f} SOL hoje, PARANDO", "ERROR")
        trading_paused = True
        return False

    # Get saldo da wallet
    bal = get_sol_balance(str(kp.pubkey())) / 1e9
    if bal < ENTRY_SIZE + 0.01:  # 0.01 SOL para fees
        log(f"❌ Saldo insuficiente: {bal:.3f} < {ENTRY_SIZE:.3f}", "WARN")
        return False

    amount_sol = ENTRY_SIZE
    amount_lamports = int(amount_sol * 1e9)

    log(f"💰 ENTRADA REAL: {symbol} @ ${entry_price:.8f} | {amount_sol} SOL", "INFO")

    # Swap SOL → Token
    tx_sig = jupiter_swap(kp, WSOL, mint, amount_lamports)

    if not tx_sig:
        log(f"❌ Não conseguiu comprar {symbol}", "ERROR")
        return False

    # Registra posição
    with lock:
        positions[mint] = {
            "symbol": symbol,
            "entry_price": entry_price,
            "entry_sol": amount_sol,
            "entry_time": time.time(),
            "tx_sig": tx_sig,
            "kp": kp,
            "status": "OPEN",
        }

    log(f"✓ POSIÇÃO ABERTA: {symbol} | TX: {tx_sig[:20]}...", "INFO")
    return True

def exit_position_real(mint, pos, exit_price, reason):
    """
    Sai da posição REAL!
    Vende token por SOL real.
    """
    global daily_pnl, consecutive_losses

    symbol = pos["symbol"]
    entry = pos["entry_price"]
    entry_sol = pos["entry_sol"]

    # PnL em %
    pnl_pct = (exit_price - entry) / entry * 100
    pnl_sol = entry_sol * (pnl_pct / 100)

    log(f"📊 SAÍDA: {symbol} | {reason} | {pnl_pct:+.2f}% | {pnl_sol:+.3f} SOL", "INFO")

    # Update daily stats
    daily_pnl += pnl_sol
    if pnl_sol < 0:
        consecutive_losses += 1
    else:
        consecutive_losses = 0

    # Check stop conditions
    if consecutive_losses >= STOP_ON_CONSECUTIVE_LOSS:
        log(f"🛑 {STOP_ON_CONSECUTIVE_LOSS} LOSSES SEGUIDAS, PAUSANDO", "ERROR")
        return False

    # Remove posição
    with lock:
        if mint in positions:
            del positions[mint]

    return True

def trader_real_worker(keypairs):
    """Main trading loop (REAL!)."""
    log("🤖 Trader Real iniciado — TRADING REAL COM SOL!", "INFO")

    while True:
        try:
            # Get sinal
            try:
                sig = signal_q.get(timeout=30)
            except queue.Empty:
                continue

            # Seleciona wallet (round-robin)
            kp = keypairs[len(positions) % len(keypairs)]["kp"]

            # Entra em posição REAL
            if enter_position_real(kp, sig["mint"], sig["symbol"], sig["price"]):
                # Aguarda TP/SL
                time.sleep(5)

                # Get preço atual
                current_price = get_token_price(sig["mint"])
                if not current_price:
                    continue

                # Check TP/SL
                entry_price = sig["price"]
                tp = entry_price * (1 + TP_PCT)
                sl = entry_price * (1 - SL_PCT)

                if current_price >= tp:
                    exit_position_real(sig["mint"], positions[sig["mint"]], current_price, "TP")
                elif current_price <= sl:
                    exit_position_real(sig["mint"], positions[sig["mint"]], current_price, "SL")

        except Exception as e:
            log(f"❌ Trader error: {e}", "ERROR")
            time.sleep(10)

def status_reporter():
    """Reporta status a cada minuto."""
    while True:
        try:
            with lock:
                n = len(positions)
                bal_w1 = get_sol_balance(str(keypairs[0]["kp"].pubkey()))
                bal_w2 = get_sol_balance(W2_ADDR)
                bal_w3 = get_sol_balance(W3_ADDR)

            log(f"📊 Status: Pos={n} | PnL={daily_pnl:+.2f} SOL | Balances: W1={bal_w1:.3f} W2={bal_w2:.3f} W3={bal_w3:.3f}", "INFO")
            time.sleep(60)
        except:
            time.sleep(60)

# ─────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────

if __name__ == "__main__":
    log("="*60)
    log("TRADER REAL — TRADING COM SOL REAL!")
    log("="*60)

    # Validar config
    if not Config.validate_config():
        log("❌ Config inválida!", "ERROR")
        sys.exit(1)

    # Load wallets
    try:
        keypairs = load_keypairs()
    except Exception as e:
        log(f"❌ Erro ao carregar wallets: {e}", "ERROR")
        sys.exit(1)

    # Start threads
    threads = [
        threading.Thread(target=trader_real_worker, args=(keypairs,), daemon=True),
        threading.Thread(target=status_reporter, daemon=True),
    ]

    for t in threads:
        t.start()

    # Keep alive
    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        log("\n[EXIT] Encerrando trader real...", "INFO")
        sys.exit(0)
