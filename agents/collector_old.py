#!/usr/bin/env python3
# Agente 1: Coleta tokens -- DexScreener profiles + boosts + pump.fun
import subprocess, json, time, sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db as DB

def log(m):
    line = f"[{time.strftime('%H:%M:%S', time.gmtime())}] [COLLECTOR] {m}"
    print(line, flush=True)

def curl(url, timeout=12):
    try:
        r = subprocess.run(["curl","-s","--max-time",str(timeout),
            "-A","Mozilla/5.0","-H","Accept: application/json", url],
            capture_output=True)
        return json.loads(r.stdout) if r.stdout else None
    except: return None

def save(conn, mint, sym, name, ts, mc, vol, liq, h1, h6, src):
    try:
        conn.execute("INSERT OR IGNORE INTO tokens VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (mint, sym[:20], name[:50], int(ts or 0), float(mc or 0),
             float(vol or 0), float(liq or 0), float(h1 or 0),
             float(h6 or 0), int(time.time()), src))
        return True
    except: return False

def collect_dexscreener(conn):
    added = 0
    cutoff = time.time() - 7*86400

    for endpoint in [
        "https://api.dexscreener.com/token-profiles/latest/v1",
        "https://api.dexscreener.com/token-boosts/latest/v1",
    ]:
        d = curl(endpoint)
        if not d or not isinstance(d, list): continue
        for t in d[:40]:
            if t.get("chainId") != "solana": continue
            mint = t.get("tokenAddress", "")
            if not mint: continue
            d2 = curl(f"https://api.dexscreener.com/latest/dex/tokens/{mint}", timeout=8)
            if not d2 or not d2.get("pairs"): continue
            sols = [p for p in d2["pairs"] if p.get("chainId") == "solana"]
            if not sols: continue
            p = max(sols, key=lambda x: float((x.get("liquidity") or {}).get("usd") or 0))
            ca = p.get("pairCreatedAt", 0)
            ts = ca/1000 if ca > 1e12 else ca
            if ts and ts < cutoff: continue
            liq = float((p.get("liquidity") or {}).get("usd") or 0)
            h1  = float((p.get("priceChange") or {}).get("h1") or 0)
            h6  = float((p.get("priceChange") or {}).get("h6") or 0)
            vol = float((p.get("volume") or {}).get("h24") or 0)
            mc  = float(p.get("fdv") or p.get("marketCap") or 0)
            sym  = (p.get("baseToken") or {}).get("symbol", "?")
            name = (p.get("baseToken") or {}).get("name", "?")
            if save(conn, mint, sym, name, ts, mc, vol, liq, h1, h6, "dexscreener"): added += 1
            time.sleep(0.3)
        conn.commit()
    return added

def collect_pumpfun(conn):
    added = 0
    cutoff = time.time() - 7*86400
    url = "https://frontend-api.pump.fun/coins?offset=0&limit=50&sort=created_timestamp&order=DESC&includeNsfw=false"
    r = subprocess.run(["curl","-s","--max-time","12",
        "-A","Mozilla/5.0","-H","Origin: https://pump.fun",
        "-H","Referer: https://pump.fun/", url], capture_output=True)
    if r.returncode != 0 or not r.stdout:
        log("pumpfun curl failed, retry next cycle")
        return 0
    try: data = json.loads(r.stdout)
    except: return 0
    if not isinstance(data, list): return 0
    for c in data:
        ts = c.get("created_timestamp", 0)
        if ts and ts > 1e12: ts = ts // 1000
        if ts and ts < cutoff: continue
        mint = c.get("mint", "")
        if not mint: continue
        sym  = c.get("symbol", "?")[:20]
        name = c.get("name", "?")[:50]
        mc   = float(c.get("usd_market_cap") or 0)
        if save(conn, mint, sym, name, ts, mc, 0, 0, 0, 0, "pumpfun"): added += 1
    conn.commit()
    return added

if __name__ == "__main__":
    log("Iniciado -- coletando tokens 7 dias")
    conn = DB.get_conn()
    cycle = 0
    while True:
        cycle += 1
        a1 = collect_dexscreener(conn)
        a2 = collect_pumpfun(conn)
        if a1 + a2 > 0:
            log(f"+{a1} dexscreener +{a2} pumpfun (ciclo {cycle})")
        if cycle % 10 == 0:
            n = conn.execute("SELECT COUNT(*) FROM tokens").fetchone()[0]
            log(f"Total acumulado: {n} tokens")
            conn.execute("PRAGMA wal_checkpoint(PASSIVE)")
        time.sleep(60)
