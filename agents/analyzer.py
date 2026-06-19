#!/usr/bin/env python3
# Agente 2: Analisa transacoes -- classifica whales/bots/retail via Solana RPC publico
import subprocess, json, time, sys
sys.path.insert(0, "C:/Users/Loja/caca_pump_local/agents")
import db as DB

WHALE_SOL = 0.3
BOT_SOL   = 0.006
BATCH     = 4
RPC_URL   = "https://api.mainnet-beta.solana.com"

def log(m):
    t = time.strftime("%H:%M:%S")
    print(f"[{t}] [ANALYZER] {m}", flush=True)

def rpc(payload, timeout=20):
    try:
        r = subprocess.run(["curl","-s","--max-time",str(timeout),
            "-X","POST", RPC_URL,
            "-H","Content-Type: application/json",
            "-d", json.dumps(payload)],
            capture_output=True)
        return json.loads(r.stdout).get("result") if r.stdout else None
    except: return None

def dex_get(url, timeout=10):
    try:
        r = subprocess.run(["curl","-s","--max-time",str(timeout),
            "-A","Mozilla/5.0","-H","Accept: application/json", url],
            capture_output=True)
        return json.loads(r.stdout) if r.stdout else None
    except: return None

def analyze_token(conn, mint, sym):
    # Busca dados DexScreener primeiro
    dex = dex_get(f"https://api.dexscreener.com/latest/dex/tokens/{mint}")
    pairs = (dex or {}).get("pairs", [])
    sol_pairs = [p for p in pairs if p.get("chainId") == "solana"]

    whale_c = bot_c = retail_c = 0
    sol_early = 0.0

    if sol_pairs:
        p = max(sol_pairs, key=lambda x: float((x.get("liquidity") or {}).get("usd") or 0))
        vol_m5   = float((p.get("volume") or {}).get("m5") or 0)
        vol_h1   = float((p.get("volume") or {}).get("h1") or 0)
        liq_usd  = float((p.get("liquidity") or {}).get("usd") or 0)
        buys_m5  = int((p.get("txns") or {}).get("m5", {}).get("buys") or 0)
        sells_m5 = int((p.get("txns") or {}).get("m5", {}).get("sells") or 0)
        buys_h1  = int((p.get("txns") or {}).get("h1", {}).get("buys") or 0)

        SOL_PRICE = 175.0
        avg_buy_usd = vol_m5 / max(1, buys_m5)
        # Whale: compra >= 0.3 SOL (~$52.5). Estima qtd de whales pelo volume
        whale_c   = max(0, int(vol_m5 / (WHALE_SOL * SOL_PRICE * 2)))
        bot_c     = max(0, buys_m5 - int(buys_m5 * min(1.0, avg_buy_usd / 50)))
        retail_c  = max(0, buys_h1 - whale_c - bot_c)
        sol_early = vol_m5 / SOL_PRICE  # SOL movimentado nos ultimos 5min
        time.sleep(1)

    # Tenta Solana RPC para assinaturas reais
    sigs = rpc({
        "jsonrpc":"2.0","id":1,
        "method":"getSignaturesForAddress",
        "params":[mint, {"limit": 15}]
    }, timeout=15)

    if sigs and isinstance(sigs, list):
        rpc_whale = rpc_bot = rpc_retail = 0
        for sig_info in sigs[:8]:
            sig = sig_info.get("signature","")
            bt  = sig_info.get("blockTime", 0)
            tx  = rpc({
                "jsonrpc":"2.0","id":1,
                "method":"getTransaction",
                "params":[sig, {"encoding":"json","maxSupportedTransactionVersion":0}]
            }, timeout=15)
            if not tx: continue
            meta = tx.get("meta") or {}
            pre  = meta.get("preBalances", [])
            post = meta.get("postBalances", [])
            for i in range(min(len(pre), len(post))):
                diff = abs(post[i] - pre[i]) / 1e9
                if diff < 0.001: continue
                if diff >= WHALE_SOL:
                    rpc_whale += 1
                    sol_early += diff
                elif diff <= BOT_SOL:
                    rpc_bot += 1
                else:
                    rpc_retail += 1
            time.sleep(0.4)
        # Usa max entre estimativa DexScreener e RPC real
        whale_c   = max(whale_c, rpc_whale)
        bot_c     = max(bot_c, rpc_bot)
        retail_c  = max(retail_c, rpc_retail)

    total     = whale_c + bot_c + retail_c
    bot_ratio = bot_c / max(1, total)

    if whale_c >= 5 and sol_early >= 3 and bot_ratio < 0.6:
        pid = 0  # BALEIA_FORTE
    elif bot_ratio > 0.8:
        pid = 1  # BOT_SWARM
    elif whale_c >= 3:
        pid = 2  # LENTO_WHALE
    elif sol_early > 50:
        pid = 3  # EXPLOSIVO
    elif whale_c < 2 and bot_ratio < 0.3:
        pid = 4  # ORGANIC
    elif whale_c < 2 and bot_ratio > 0.6:
        pid = 5  # RUG_CAND
    else:
        pid = 6  # MISTO

    conn.execute("INSERT OR REPLACE INTO token_patterns VALUES (?,?,?,?,?,?,?,?,?)",
        (mint, pid, whale_c, bot_c, retail_c,
         round(sol_early, 2), round(bot_ratio, 3), 0.0, int(time.time())))

    if sol_pairs:
        p = max(sol_pairs, key=lambda x: float((x.get("liquidity") or {}).get("usd") or 0))
        liq = float((p.get("liquidity") or {}).get("usd") or 0)
        role_map = []
        if whale_c > 0:
            role_map.append(("WHALE_EST", "whale", round(sol_early/max(1,whale_c), 2)))
        if bot_c > 0:
            role_map.append(("BOT_EST", "bot", 0.003))
        for wallet, role, sol_amt in role_map[:10]:
            conn.execute(
                "INSERT INTO wallet_appearances (wallet,mint,role,sol_amount,ts) VALUES (?,?,?,?,?)",
                (wallet + f"_{mint[:8]}", mint, role, sol_amt, int(time.time())))

    conn.commit()
    log(f"[{sym}] pid={pid} whales={whale_c} bots={bot_c} retail={retail_c} sol5m={sol_early:.1f}")
    return True

if __name__ == "__main__":
    log("Iniciado -- Solana RPC + DexScreener (sem Helius)")
    while True:
        conn = DB.get_conn()
        # Re-analisa tambem tokens com pattern=-1 (falha anterior)
        rows = conn.execute("""
            SELECT t.mint, t.symbol FROM tokens t
            LEFT JOIN token_patterns p ON p.mint = t.mint
            WHERE p.mint IS NULL OR p.pattern_id = -1
            LIMIT ?
        """, (BATCH,)).fetchall()

        if rows:
            ok = 0
            for r in rows:
                try:
                    if analyze_token(conn, r[0], r[1]): ok += 1
                except Exception as e:
                    log(f"Erro {r[1]}: {e}")
                time.sleep(2)
            log(f"Analisados {ok}/{len(rows)} tokens | Total DB: {conn.execute('SELECT COUNT(*) FROM wallet_appearances').fetchone()[0]} carteiras")
        else:
            log(f"Aguardando tokens... DB: {conn.execute('SELECT COUNT(*) FROM tokens').fetchone()[0]} tokens")
        conn.close()
        time.sleep(25)
