#!/usr/bin/env python3
"""
Distributor v2 — Fixes críticos:
✓ Race condition fix (timestamp-based idempotency vs memory flag)
✓ Retry logic com exponential backoff
✓ Balance validation antes de enviar
✓ Logging estruturado
✓ Config centralizado
"""

import os, time, base64, base58, json, requests, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import config as Config

# ─────────────────────────────────────────────────────────
# CONSTANTES
# ─────────────────────────────────────────────────────────

RPC = Config.SOLANA_RPC
MIN_BAL = 0.09  # SOL mínimo na W1 para disparar
GAS_RESERVE = 0.006  # Reserve para fees
MAX_RETRIES = 3
RETRY_BACKOFF = [1, 2, 4]  # segundos entre retries

# Addresses (hardcoded porque são públicas)
W2_ADDR = "Crmr7oqFAJp3WfwESZrzzeot8pGczvPhEttHkFMyEWoj"
W3_ADDR = "2i3pF5pGk6M54y9U1dnxPyceT31WJ1N25dYQ9bCaMLWP"

W1_KEY = Config.WALLET_1_KEY

# CRITICAL FIX: Idempotency file em vez de memory flag
IDEMPOTENCY_FILE = Config.DATA_DIR / "distributor_last_tx.json"

# ─────────────────────────────────────────────────────────
# FUNÇÕES
# ─────────────────────────────────────────────────────────

def log(m, level="INFO"):
    """Log com timestamp UTC."""
    t = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())
    line = f"[{t}] [{level}] [DIST] {m}"
    print(line, flush=True)
    try:
        with open(Config.LOGS_DIR / "distributor.log", "a") as f:
            f.write(line + "\n")
    except:
        pass

def load_last_distribution():
    """Carrega último timestamp de distribuição (idempotency)."""
    try:
        if IDEMPOTENCY_FILE.exists():
            with open(IDEMPOTENCY_FILE) as f:
                data = json.load(f)
                return data.get("last_tx_time", 0), data.get("last_sigs", [])
    except:
        pass
    return 0, []

def save_distribution(sigs):
    """Salva timestamp + signatures da distribuição."""
    data = {
        "last_tx_time": time.time(),
        "last_sigs": sigs,
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())
    }
    with open(IDEMPOTENCY_FILE, "w") as f:
        json.dump(data, f, indent=2)
    log(f"✓ Distribuição salva: {sigs}")

def is_distribution_fresh():
    """Verifica se já distribuiu nos últimos 10 minutos."""
    last_time, _ = load_last_distribution()
    age = time.time() - last_time
    return age < 600  # 10 minutos

def rpc_post(payload, retry=0):
    """RPC com retry automático."""
    try:
        r = requests.post(RPC, json=payload, timeout=Config.DB_TIMEOUT)
        result = r.json().get("result")
        if result is None and retry < MAX_RETRIES:
            wait = RETRY_BACKOFF[retry]
            log(f"⚠️  RPC falhou, retry {retry+1}/{MAX_RETRIES} em {wait}s", "WARN")
            time.sleep(wait)
            return rpc_post(payload, retry+1)
        return result
    except requests.Timeout:
        if retry < MAX_RETRIES:
            wait = RETRY_BACKOFF[retry]
            log(f"⚠️  RPC timeout, retry {retry+1}/{MAX_RETRIES} em {wait}s", "WARN")
            time.sleep(wait)
            return rpc_post(payload, retry+1)
        log(f"❌ RPC falhou após {MAX_RETRIES} retries", "ERROR")
        return None
    except Exception as e:
        log(f"❌ RPC erro: {e}", "ERROR")
        return None

def get_balance_lamports(addr):
    """Get balance com retry."""
    r = rpc_post({"jsonrpc":"2.0","id":1,"method":"getBalance","params":[addr]})
    return (r or {}).get("value", 0)

def validate_address(addr):
    """Valida formato de endereço Solana."""
    if not addr or len(addr) < 40:
        return False
    try:
        base58.b58decode(addr)
        return True
    except:
        return False

def get_blockhash():
    """Get latest blockhash com retry."""
    r = rpc_post({"jsonrpc":"2.0","id":1,"method":"getLatestBlockhash",
                  "params":[{"commitment":"finalized"}]})
    return ((r or {}).get("value") or {}).get("blockhash")

def build_transfer_tx(from_kp, to_addr, lamports, blockhash):
    """Build transaction com validação."""
    try:
        from solders.keypair import Keypair
        from solders.pubkey import Pubkey
        from solders.system_program import transfer, TransferParams
        from solders.transaction import Transaction
        from solders.message import Message
        from solders.hash import Hash

        # Validar endereço
        if not validate_address(to_addr):
            log(f"❌ Endereço inválido: {to_addr}", "ERROR")
            return None

        ix = transfer(TransferParams(
            from_pubkey=from_kp.pubkey(),
            to_pubkey=Pubkey.from_string(to_addr),
            lamports=lamports,
        ))
        msg = Message.new_with_blockhash([ix], from_kp.pubkey(), Hash.from_string(blockhash))
        tx = Transaction([from_kp], msg, Hash.from_string(blockhash))
        return base64.b64encode(bytes(tx)).decode()
    except Exception as e:
        log(f"❌ Erro ao build tx: {e}", "ERROR")
        return None

def send_sol(from_kp, to_addr, amount_sol, retry=0):
    """Envia SOL com retry e validação."""
    # Validar saldo
    bal = get_balance_lamports(from_kp.pubkey()) / 1e9
    if bal < amount_sol + GAS_RESERVE:
        log(f"❌ Saldo insuficiente: {bal:.4f} < {amount_sol:.4f} + reserve", "ERROR")
        return None

    lamports = int(amount_sol * 1e9)
    blockhash = get_blockhash()
    if not blockhash:
        log("❌ Sem blockhash", "ERROR")
        return None

    tx_b64 = build_transfer_tx(from_kp, to_addr, lamports, blockhash)
    if not tx_b64:
        return None

    try:
        r = rpc_post({"jsonrpc":"2.0","id":1,"method":"sendTransaction",
                      "params":[tx_b64, {"encoding":"base64","skipPreflight":False}]})
        if r:
            log(f"✓ TX enviada: {r}", "INFO")
            return r

        # Retry se falhou
        if retry < MAX_RETRIES:
            wait = RETRY_BACKOFF[retry]
            log(f"⚠️  TX falhou, retry {retry+1}/{MAX_RETRIES} em {wait}s", "WARN")
            time.sleep(wait)
            return send_sol(from_kp, to_addr, amount_sol, retry+1)

        log(f"❌ TX falhou após {MAX_RETRIES} retries", "ERROR")
        return None

    except Exception as e:
        log(f"❌ Erro ao enviar: {e}", "ERROR")
        return None

def main():
    """Main distributor loop."""
    log("="*60)
    log("DISTRIBUTOR v2 — Com idempotency timestamp", "INFO")
    log("="*60)

    # Validar wallet
    if not W1_KEY:
        log("❌ WALLET_1_KEY não configurada", "ERROR")
        sys.exit(1)

    try:
        from solders.keypair import Keypair
        key_bytes = base58.b58decode(W1_KEY)
        kp1 = Keypair.from_bytes(key_bytes)
        w1_addr = str(kp1.pubkey())
    except Exception as e:
        log(f"❌ Erro ao carregar chave: {e}", "ERROR")
        sys.exit(1)

    log(f"W1: {w1_addr[:20]}...", "INFO")
    log(f"W2: {W2_ADDR[:20]}...", "INFO")
    log(f"W3: {W3_ADDR[:20]}...", "INFO")

    cycle = 0
    while True:
        cycle += 1
        try:
            # Check saldos
            bal1 = get_balance_lamports(w1_addr) / 1e9
            bal2 = get_balance_lamports(W2_ADDR) / 1e9
            bal3 = get_balance_lamports(W3_ADDR) / 1e9

            log(f"[{cycle}] Saldos: W1={bal1:.4f} W2={bal2:.4f} W3={bal3:.4f} SOL", "INFO")

            # Decide se precisa distribuir
            precisa_distribuir = (bal2 < 0.01 or bal3 < 0.01) and bal1 > MIN_BAL

            # CRITICAL FIX: Usa timestamp, não memory flag
            if precisa_distribuir and not is_distribution_fresh():
                disponivel = bal1 - GAS_RESERVE
                por_wallet = disponivel / 3

                log(f"💰 Distribuindo {disponivel:.4f} SOL -> {por_wallet:.4f} por carteira", "INFO")

                if por_wallet < 0.01:
                    log(f"⚠️  Saldo insuficiente ({por_wallet:.4f} SOL por wallet)", "WARN")
                else:
                    sigs = []

                    # Enviar para W2 se necessário
                    if bal2 < 0.01:
                        sig = send_sol(kp1, W2_ADDR, por_wallet)
                        if sig:
                            sigs.append({"wallet": "W2", "amount": por_wallet, "sig": sig})
                            time.sleep(5)

                    # Revalidar saldo antes de W3
                    bal1 = get_balance_lamports(w1_addr) / 1e9

                    # Enviar para W3 se necessário
                    if bal3 < 0.01:
                        enviar = min(por_wallet, bal1 - GAS_RESERVE)
                        if enviar > 0.001:
                            sig = send_sol(kp1, W3_ADDR, enviar)
                            if sig:
                                sigs.append({"wallet": "W3", "amount": enviar, "sig": sig})

                    # Salva idempotency (timestamp + signatures)
                    if sigs:
                        save_distribution(sigs)
                        log(f"✓ Distribuição completa: {len(sigs)} transfers", "INFO")
                    else:
                        log("⚠️  Nenhuma distribuição realizada", "WARN")

            else:
                if is_distribution_fresh():
                    log("ℹ️  Distribuição recente, em monitoramento passivo", "INFO")

            time.sleep(60)

        except KeyboardInterrupt:
            log("\n[EXIT] Encerrando...", "INFO")
            break
        except Exception as e:
            log(f"❌ Erro no ciclo: {e}", "ERROR")
            time.sleep(60)

if __name__ == "__main__":
    main()
