#!/usr/bin/env python3
# Trader real -- Jupiter API, 3 carteiras, compra/vende tokens Solana

import os, time, base64, base58, json, queue, threading, requests

RPC         = "https://api.mainnet-beta.solana.com"
JUPITER_Q   = "https://quote-api.jup.ag/v6/quote"
JUPITER_SW  = "https://quote-api.jup.ag/v6/swap"
WSOL        = "So11111111111111111111111111111111111111112"
SLIPPAGE    = 300    # 3% slippage bps
ENTRY_PCT   = 0.80   # usa 80% do saldo disponivel por entrada
TP_PCT      = 0.25   # +25% take profit
SL_PCT      = 0.10   # -10% stop loss
MAX_HOLD_MIN= 12     # saida forcada apos 12 min
BE_TRIGGER  = 0.12   # move SL ao chegar em +12%
BE_SL       = 0.05   # SL vira +5% no break-even

W1_KEY  = os.environ.get("WALLET_1_KEY", "")
W2_KEY  = os.environ.get("WALLET_2_KEY", "")
W3_KEY  = os.environ.get("WALLET_3_KEY", "")

W2_ADDR = "Crmr7oqFAJp3WfwESZrzzeot8pGczvPhEttHkFMyEWoj"
W3_ADDR = "2i3pF5pGk6M54y9U1dnxPyceT31WJ1N25dYQ9bCaMLWP"

# Filtros de entrada
MIN_WHALE_COUNT  = 2
MIN_SOL_5MIN     = 0.5
MAX_TOKEN_AGE_MIN= 60
MAX_BOT_RATIO    = 0.90
MIN_LIQ_USD      = 5000
WHALE_SOL_MIN    = 0.3
BOT_SOL_MAX      = 0.005

signal_q  = queue.Queue(maxsize=20)
positions = {}  # mint -> {wallet_idx, kp, symbol, entry_price, tp, sl, token_amt, entry_time, be_applied}
lock      = threading.Lock()
start_ts  = time.time()

def log(m):
    print(f"[{time.strftime('%H:%M:%S', time.gmtime())}] [LIVE] {m}", flush=True)

def load_keypairs():
    from solders.keypair import Keypair
    kps = []
    for key_str in [W1_KEY, W2_KEY, W3_KEY]:
        if not key_str:
            continue
        try:
            kp = Keypair.from_bytes(base58.b58decode(key_str))
            kps.append(kp)
            log(f"Wallet carregada: {str(kp.pubkey())[:20]}...")
        except Exception as e:
            log(f"Erro ao carregar wallet: {e}")
    return kps

def rpc_post(payload):
    try:
        r = requests.post(RPC, json=payload, timeout=15)
        return r.json().get("result")
    except:
        return None

def get_sol_balance(addr):
    r = rpc_post({"jsonrpc":"2.0","id":1,"method":"getBalance","params":[str(addr)]})
    return ((r or {}).get("value") or 0) / 1e9

def get_token_balance(wallet_addr, mint):
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
    return 0

def get_token_price_usd(mint):
    try:
        r = requests.get(f"https://api.dexscreener.com/latest/dex/tokens/{mint}", timeout=8)
        pairs = r.json().get("pairs") or []
        sols = [p for p in pairs if p.get("chainId") == "solana"]
        if not sols:
            return None
        p = max(sols, key=lambda x: float((x.get("liquidity") or {}).get("usd") or 0))
        return float(p.get("priceUsd") or 0) or None
    except:
        return None

def get_blockhash():
    r = rpc_post({"jsonrpc":"2.0","id":1,"method":"getLatestBlockhash",
                  "params":[{"commitment":"confirmed"}]})
    return ((r or {}).get("value") or {}).get("blockhash")

def jupiter_quote(input_mint, output_mint, amount_lamports):
    try:
        r = requests.get(JUPITER_Q, params={
            "inputMint": input_mint,
            "outputMint": output_mint,
            "amount": str(amount_lamports),
            "slippageBps": str(SLIPPAGE),
        }, timeout=10)
        return r.json() if r.ok else None
    except:
        return None

def jupiter_swap_tx(quote_resp, user_pubkey):
    try:
        r = requests.post(JUPITER_SW, json={
            "quoteResponse": quote_resp,
            "userPublicKey": str(user_pubkey),
            "wrapAndUnwrapSol": True,
            "dynamicComputeUnitLimit": True,
            "prioritizationFeeLamports": 1000,
        }, timeout=15)
        return (r.json() or {}).get("swapTransaction")
    except:
        return None

def sign_and_send(tx_b64, keypair):
    try:
        from solders.transaction import VersionedTransaction
        tx_bytes  = base64.b64decode(tx_b64)
        tx        = VersionedTransaction.from_bytes(tx_bytes)
        signed    = VersionedTransaction(tx.message, [keypair])
        signed_b64 = base64.b64encode(bytes(signed)).decode()
        r = rpc_post({"jsonrpc":"2.0","id":1,"method":"sendTransaction",
                      "params":[signed_b64, {"encoding":"base64",
                                              "skipPreflight":False,
                                              "maxRetries":3}]})
        return r
    except Exception as e:
        log(f"Erro sign/send: {e}")
        return None

def buy_token(kp, mint, sol_amount):
    lamports = int(sol_amount * 1e9)
    log(f"Comprando {sol_amount:.4f} SOL de {mint[:12]}...")
    quote = jupiter_quote(WSOL, mint, lamports)
    if not quote or quote.get("error"):
        log(f"Quote erro: {(quote or {}).get('error')}")
        return None, 0
    out_amount = int(quote.get("outAmount") or 0)
    tx_b64 = jupiter_swap_tx(quote, kp.pubkey())
    if not tx_b64:
        log("Swap TX nao retornou")
        return None, 0
    sig = sign_and_send(tx_b64, kp)
    if sig:
        log(f"[COMPRA OK] sig={str(sig)[:20]}... tokens_est={out_amount}")
        time.sleep(4)
        token_amt = get_token_balance(kp.pubkey(), mint)
        if token_amt == 0:
            token_amt = out_amount
        return sig, token_amt
    log("Falha ao enviar tx de compra")
    return None, 0

def sell_token(kp, mint, token_amount, reason):
    log(f"Vendendo {token_amount} tokens de {mint[:12]}... motivo={reason}")
    quote = jupiter_quote(mint, WSOL, token_amount)
    if not quote or quote.get("error"):
        log(f"Quote venda erro: {(quote or {}).get('error')}")
        return None
    tx_b64 = jupiter_swap_tx(quote, kp.pubkey())
    if not tx_b64:
        return None
    sig = sign_and_send(tx_b64, kp)
    if sig:
        log(f"[VENDA OK] sig={str(sig)[:20]}... motivo={reason}")
    return sig

def scanner_worker(keypairs):
    log("[SCANNER] iniciado")
    while True:
        candidates = []
        for ep in ["https://api.dexscreener.com/token-profiles/latest/v1",
                   "https://api.dexscreener.com/token-boosts/latest/v1"]:
            try:
                r = requests.get(ep, timeout=12)
                data = r.json() if r.ok else []
                for t in (data if isinstance(data, list) else [])[:30]:
                    if t.get("chainId") != "solana": continue
                    mint = t.get("tokenAddress","")
                    if mint and mint not in candidates:
                        candidates.append(mint)
            except:
                pass
        for mint in candidates:
            classify_and_signal(mint)
        time.sleep(20)

def classify_and_signal(mint):
    try:
        r = requests.get(f"https://api.dexscreener.com/latest/dex/tokens/{mint}", timeout=8)
        data = r.json()
        pairs = data.get("pairs") or []
        sols  = [p for p in pairs if p.get("chainId") == "solana"]
        if not sols: return
        p = max(sols, key=lambda x: float((x.get("liquidity") or {}).get("usd") or 0))

        ca  = p.get("pairCreatedAt", 0)
        age = (time.time()*1000 - ca) / 60000 if ca else 999
        if age > MAX_TOKEN_AGE_MIN: return

        liq  = float((p.get("liquidity") or {}).get("usd") or 0)
        m5   = float((p.get("priceChange") or {}).get("m5") or 0)
        sym  = (p.get("baseToken") or {}).get("symbol","?")
        price = float(p.get("priceUsd") or 0)
        if not price or m5 <= 0: return

        vol_m5  = float((p.get("volume") or {}).get("m5") or 0)
        buys_m5 = int((p.get("txns") or {}).get("m5",{}).get("buys") or 0)
        SOL_PX  = 175.0
        avg_buy = vol_m5 / max(1, buys_m5)
        whale_c = max(0, int(vol_m5 / (WHALE_SOL_MIN * SOL_PX * 1.5)))
        bot_c   = max(0, int(buys_m5 * max(0, 1 - avg_buy / 40)))
        sol_5m  = vol_m5 / SOL_PX
        total   = whale_c + bot_c
        bot_r   = bot_c / max(1, total)

        if liq < MIN_LIQ_USD: return
        if whale_c < MIN_WHALE_COUNT: return
        if sol_5m < MIN_SOL_5MIN: return
        if bot_r > MAX_BOT_RATIO: return

        with lock:
            if mint in positions: return

        sig = {"mint":mint,"symbol":sym,"price":price,"liq":liq,
               "m5":m5,"age_min":age,"whale_count":whale_c,
               "sol_5min":round(sol_5m,1),"bot_ratio":round(bot_r,2)}
        log(f"[SINAL] {sym} m5={m5:+.0f}% age={age:.0f}m liq=${liq:,.0f} wh={whale_c} sol5m={sol_5m:.1f}")
        try: signal_q.put_nowait(sig)
        except queue.Full: pass
    except:
        pass

def trader_worker(keypairs):
    log(f"[TRADER] iniciado com {len(keypairs)} carteiras")
    last_status = 0.0
    wallet_busy = [False] * len(keypairs)

    while True:
        elapsed = (time.time() - start_ts) / 60
        if elapsed - last_status >= 5.0:
            last_status = elapsed
            with lock:
                n  = len(positions)
                ps = [(v["symbol"], v["kp_idx"]) for v in positions.values()]
            bals = [f"W{i+1}={get_sol_balance(kp.pubkey()):.3f}" for i, kp in enumerate(keypairs)]
            log(f"[STATUS] posicoes={n} {ps} | {' '.join(bals)} SOL")

        try:
            sig = signal_q.get(timeout=5)
        except queue.Empty:
            time.sleep(1)
            continue

        with lock:
            if sig["mint"] in positions: continue
            busy_count = sum(1 for v in positions.values())
            if busy_count >= len(keypairs): continue

        # Acha carteira livre
        free_idx = None
        with lock:
            used_wallets = {v["kp_idx"] for v in positions.values()}
            for i in range(len(keypairs)):
                if i not in used_wallets:
                    free_idx = i
                    break

        if free_idx is None: continue

        kp       = keypairs[free_idx]
        sol_bal  = get_sol_balance(kp.pubkey())
        sol_use  = sol_bal * ENTRY_PCT

        if sol_use < 0.01:
            log(f"W{free_idx+1} saldo baixo: {sol_bal:.4f} SOL -- pulando")
            continue

        entry_price = get_token_price_usd(sig["mint"]) or sig["price"]
        sig_sig, token_amt = buy_token(kp, sig["mint"], sol_use)

        if not sig_sig or token_amt == 0:
            log(f"Compra falhou: {sig['symbol']}")
            continue

        with lock:
            positions[sig["mint"]] = {
                "kp_idx":     free_idx,
                "kp":         kp,
                "symbol":     sig["symbol"],
                "entry_price": entry_price,
                "sol_used":   sol_use,
                "token_amt":  token_amt,
                "tp":         entry_price * (1 + TP_PCT),
                "sl":         entry_price * (1 - SL_PCT),
                "entry_time": time.time(),
                "be_applied": False,
            }

def watchdog_worker(keypairs):
    log("[WATCHDOG] iniciado")
    while True:
        with lock:
            mints = list(positions.keys())

        for mint in mints:
            with lock:
                if mint not in positions: continue
                pos = dict(positions[mint])

            hold_min  = (time.time() - pos["entry_time"]) / 60
            cur_price = get_token_price_usd(mint)
            if not cur_price: continue

            with lock:
                if mint not in positions: continue
                if not positions[mint]["be_applied"] and cur_price >= pos["entry_price"] * (1 + BE_TRIGGER):
                    positions[mint]["sl"] = pos["entry_price"] * (1 + BE_SL)
                    positions[mint]["be_applied"] = True
                    log(f"[BE] {pos['symbol']} SL -> +{BE_SL:.0%} @ ${positions[mint]['sl']:.8f}")
                pos = dict(positions[mint])

            reason = None
            if hold_min >= MAX_HOLD_MIN: reason = "TEMPO"
            elif cur_price >= pos["tp"]: reason = "TP"
            elif cur_price <= pos["sl"]: reason = "SL"

            if reason:
                pnl_pct = (cur_price - pos["entry_price"]) / pos["entry_price"] * 100
                sig = sell_token(pos["kp"], mint, pos["token_amt"], reason)
                label = "LUCRO" if pnl_pct > 0 else "PERDA"
                log(f"[SAIDA {label}] {pos['symbol']} | {reason} {hold_min:.0f}min | "
                    f"{pnl_pct:+.2f}% | W{pos['kp_idx']+1}")
                with lock:
                    if mint in positions:
                        del positions[mint]

        time.sleep(8)

if __name__ == "__main__":
    log("=" * 55)
    log("TRADER LIVE -- Jupiter API -- 3 Carteiras Reais")
    log(f"Config: TP={TP_PCT:.0%} SL={SL_PCT:.0%} BE@{BE_TRIGGER:.0%}->{BE_SL:.0%} max={MAX_HOLD_MIN}min")
    log("=" * 55)

    try:
        from solders.keypair import Keypair
        from solders.transaction import VersionedTransaction
    except ImportError:
        log("ERRO: pip install solders base58")
        exit(1)

    keypairs = load_keypairs()
    if not keypairs:
        log("ERRO: nenhuma chave configurada (WALLET_1_KEY, WALLET_2_KEY, WALLET_3_KEY)")
        exit(1)

    log(f"{len(keypairs)} carteiras ativas")
    for i, kp in enumerate(keypairs):
        bal = get_sol_balance(kp.pubkey())
        log(f"  W{i+1}: {str(kp.pubkey())} | {bal:.4f} SOL")

    threads = [
        threading.Thread(target=scanner_worker,  args=(keypairs,), daemon=True),
        threading.Thread(target=trader_worker,   args=(keypairs,), daemon=True),
        threading.Thread(target=watchdog_worker, args=(keypairs,), daemon=True),
    ]
    for t in threads: t.start()
    for t in threads: t.join()
