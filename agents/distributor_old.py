#!/usr/bin/env python3
# Distribuidor de SOL -- detecta deposito na W1 e divide igualmente entre as 3 carteiras

import os, time, base64, base58, json, requests

RPC         = "https://api.mainnet-beta.solana.com"
MIN_BAL     = 0.09    # SOL minimo na W1 para disparar distribuicao
GAS_RESERVE = 0.006   # SOL reservado para fees em cada carteira

W1_KEY  = os.environ.get("WALLET_1_KEY", "")
W2_ADDR = "Crmr7oqFAJp3WfwESZrzzeot8pGczvPhEttHkFMyEWoj"
W3_ADDR = "2i3pF5pGk6M54y9U1dnxPyceT31WJ1N25dYQ9bCaMLWP"

def log(m):
    print(f"[{time.strftime('%H:%M:%S', time.gmtime())}] [DIST] {m}", flush=True)

def rpc_post(payload):
    try:
        r = requests.post(RPC, json=payload, timeout=15)
        return r.json().get("result")
    except Exception as e:
        log(f"RPC erro: {e}")
        return None

def get_balance_lamports(addr):
    r = rpc_post({"jsonrpc":"2.0","id":1,"method":"getBalance","params":[addr]})
    return (r or {}).get("value", 0)

def get_blockhash():
    r = rpc_post({"jsonrpc":"2.0","id":1,"method":"getLatestBlockhash",
                  "params":[{"commitment":"finalized"}]})
    return ((r or {}).get("value") or {}).get("blockhash")

def build_transfer_tx(from_kp, to_addr, lamports, blockhash):
    try:
        from solders.keypair import Keypair
        from solders.pubkey import Pubkey
        from solders.system_program import transfer, TransferParams
        from solders.transaction import Transaction
        from solders.message import Message
        from solders.hash import Hash

        ix  = transfer(TransferParams(
            from_pubkey=from_kp.pubkey(),
            to_pubkey=Pubkey.from_string(to_addr),
            lamports=lamports,
        ))
        msg = Message.new_with_blockhash([ix], from_kp.pubkey(), Hash.from_string(blockhash))
        tx  = Transaction([from_kp], msg, Hash.from_string(blockhash))
        return base64.b64encode(bytes(tx)).decode()
    except Exception as e:
        log(f"Erro build tx: {e}")
        return None

def send_sol(from_kp, to_addr, amount_sol):
    lamports = int(amount_sol * 1e9)
    blockhash = get_blockhash()
    if not blockhash:
        log("Sem blockhash")
        return None
    tx_b64 = build_transfer_tx(from_kp, to_addr, lamports, blockhash)
    if not tx_b64:
        return None
    r = rpc_post({"jsonrpc":"2.0","id":1,"method":"sendTransaction",
                  "params":[tx_b64, {"encoding":"base64","skipPreflight":False}]})
    return r

def main():
    if not W1_KEY:
        log("WALLET_1_KEY nao configurado -- abortando")
        return

    try:
        from solders.keypair import Keypair
        key_bytes = base58.b58decode(W1_KEY)
        kp1 = Keypair.from_bytes(key_bytes)
        w1_addr = str(kp1.pubkey())
    except Exception as e:
        log(f"Erro ao carregar chave: {e}")
        return

    log(f"Monitorando W1: {w1_addr}")
    log(f"W2: {W2_ADDR}")
    log(f"W3: {W3_ADDR}")

    distributed = False

    while True:
        bal1 = get_balance_lamports(w1_addr) / 1e9
        bal2 = get_balance_lamports(W2_ADDR) / 1e9
        bal3 = get_balance_lamports(W3_ADDR) / 1e9
        log(f"Saldos -- W1={bal1:.4f} W2={bal2:.4f} W3={bal3:.4f} SOL")

        precisa_distribuir = (bal2 < 0.01 or bal3 < 0.01) and bal1 > MIN_BAL

        if precisa_distribuir and not distributed:
            disponivel = bal1 - GAS_RESERVE
            por_wallet = disponivel / 3
            log(f"Distribuindo {disponivel:.4f} SOL -> {por_wallet:.4f} SOL por carteira")

            if por_wallet < 0.01:
                log(f"Saldo insuficiente para distribuir ({por_wallet:.4f} SOL por wallet)")
            else:
                if bal2 < 0.01:
                    sig = send_sol(kp1, W2_ADDR, por_wallet)
                    log(f"W1 -> W2: {por_wallet:.4f} SOL | sig={sig}")
                    time.sleep(5)

                if bal3 < 0.01:
                    # Recheck balance after first transfer
                    bal1 = get_balance_lamports(w1_addr) / 1e9
                    ajuste = max(0, bal1 - GAS_RESERVE - por_wallet)
                    enviar = min(por_wallet, bal1 - GAS_RESERVE)
                    sig = send_sol(kp1, W3_ADDR, enviar)
                    log(f"W1 -> W3: {enviar:.4f} SOL | sig={sig}")

                distributed = True
                log("Distribuicao concluida!")

        elif distributed:
            log("SOL distribuido. Monitoramento passivo...")

        time.sleep(60)

if __name__ == "__main__":
    main()
