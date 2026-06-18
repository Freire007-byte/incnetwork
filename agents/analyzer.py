#!/usr/bin/env python3
# Agente 2: Analisa transacoes -- classifica whales/bots/retail
import subprocess, json, time, sys
sys.path.insert(0, "/root/caca-pump/agents")
import db as DB

WHALE_SOL = 0.3
BOT_SOL   = 0.006
BATCH     = 6
HELIUS_KEY = "a6a9f38c-3e3d-46a5-8038-6a3baa6c0298"

def log(m):
    t = time.strftime("%H:%M:%S")
    print(f"[{t}] [ANALYZER] {m}", flush=True)

def curl(url, timeout=15):
    try:
        r = subprocess.run(["curl","-s","--max-time",str(timeout),
            "-A","Mozilla/5.0","-H","Accept: application/json", url],
            capture_output=True)
        return json.loads(r.stdout) if r.stdout else None
    except: return None

def analyze_token(conn, mint, sym):
    url = (f"https://api.helius.xyz/v0/addresses/{mint}/transactions"
           f"?api-key={HELIUS_KEY}&limit=50&type=SWAP")
    txs = curl(url, timeout=15)
    if not txs or not isinstance(txs, list):
        conn.execute("INSERT OR REPLACE INTO token_patterns VALUES (?,?,?,?,?,?,?,?,?)",
            (mint, -1, 0, 0, 0, 0.0, 0.0, 0.0, int(time.time())))
        conn.commit()
        return False

    whale_c = bot_c = retail_c = 0
    sol_early = 0.0
    t0 = None
    for tx in txs:
        ts = tx.get("timestamp", 0)
        if t0 is None or (ts and ts < t0): t0 = ts

    for tx in txs:
        ts  = tx.get("timestamp", 0)
        age = (ts - t0)/60 if t0 and ts else 999
        for acc in (tx.get("accountData") or []):
            native = abs(acc.get("nativeBalanceChange", 0)) / 1e9
            if native < 0.001: continue
            if native >= WHALE_SOL:
                whale_c += 1
                if age <= 5: sol_early += native
            elif native <= BOT_SOL:
                bot_c += 1
            else:
                retail_c += 1

    total     = whale_c + bot_c + retail_c
    bot_ratio = bot_c / max(1, total)
    dur = 0.0
    if len(txs) > 1:
        times = [tx.get("timestamp", 0) for tx in txs if tx.get("timestamp")]
        if times: dur = (max(times) - min(times)) / 60

    # Classificacao por padrao
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

    conn.execute("INSERT OR REPLACE INTO token_patterns VALUES (?,?,?,?,?,?,?,?,?)",
        (mint, pid, whale_c, bot_c, retail_c,
         round(sol_early, 2), round(bot_ratio, 3),
         round(dur, 1), int(time.time())))

    for tx in txs[:20]:
        ts = tx.get("timestamp", 0)
        for acc in (tx.get("accountData") or []):
            native = abs(acc.get("nativeBalanceChange", 0)) / 1e9
            if native < 0.001: continue
            wallet = acc.get("account", "")
            role   = "whale" if native >= WHALE_SOL else ("bot" if native <= BOT_SOL else "retail")
            conn.execute(
                "INSERT INTO wallet_appearances (wallet,mint,role,sol_amount,ts) VALUES (?,?,?,?,?)",
                (wallet, mint, role, native, ts))
    conn.commit()
    return True

if __name__ == "__main__":
    log("Iniciado -- analisando tokens pendentes")
    while True:
        conn = DB.get_conn()
        rows = conn.execute("""
            SELECT t.mint, t.symbol FROM tokens t
            LEFT JOIN token_patterns p ON p.mint = t.mint
            WHERE p.mint IS NULL LIMIT ?
        """, (BATCH,)).fetchall()

        if rows:
            ok = 0
            for r in rows:
                if analyze_token(conn, r[0], r[1]): ok += 1
                time.sleep(1.5)
            log(f"Analisados {ok}/{len(rows)} tokens")
        conn.close()
        time.sleep(30)
