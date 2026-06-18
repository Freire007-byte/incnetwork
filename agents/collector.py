#!/usr/bin/env python3
# Agente 1: Coleta tokens das ultimas 7 dias -- DexScreener + pump.fun
import subprocess, json, time, sys
sys.path.insert(0, "/root/caca-pump/agents")
import db as DB

LOG = "/root/caca-pump/logs/collector.log"

def log(m):
    t = time.strftime("%H:%M:%S")
    line = f"[{t}] [COLLECTOR] {m}"
    print(line, flush=True)
    try:
        with open(LOG, "a") as f: f.write(line + "\n")
    except: pass

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
        conn.commit()
        return True
    except: return False

def collect_dexscreener(conn):
    added = 0
    cutoff = time.time() - 7*86400

    d = curl("https://api.dexscreener.com/token-profiles/latest/v1")
    if d and isinstance(d, list):
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
            sym  = (p.get("baseToken") or {}).get("symbol", "?")
            name = (p.get("baseToken") or {}).get("name", "?")
            if save(conn, mint, sym, name, ts, 0, vol, liq, h1, h6, "dexscreener"): added += 1
            time.sleep(0.3)

    d = curl("https://api.dexscreener.com/latest/dex/search?q=SOL")
    if d and d.get("pairs"):
        for p in d["pairs"][:30]:
            if p.get("chainId") != "solana": continue
            ca = p.get("pairCreatedAt", 0)
            ts = ca/1000 if ca > 1e12 else ca
            if ts and ts < cutoff: continue
            mint = (p.get("baseToken") or {}).get("address", "")
            if not mint: continue
            liq  = float((p.get("liquidity") or {}).get("usd") or 0)
            h1   = float((p.get("priceChange") or {}).get("h1") or 0)
            h6   = float((p.get("priceChange") or {}).get("h6") or 0)
            vol  = float((p.get("volume") or {}).get("h24") or 0)
            sym  = (p.get("baseToken") or {}).get("symbol", "?")
            name = (p.get("baseToken") or {}).get("name", "?")
            if save(conn, mint, sym, name, ts, 0, vol, liq, h1, h6, "dexscreener"): added += 1
    return added

def collect_pumpfun(conn):
    added = 0
    cutoff = time.time() - 7*86400
    url = "https://frontend-api.pump.fun/coins?offset=0&limit=50&sort=created_timestamp&order=DESC&includeNsfw=false"
    r = subprocess.run(["curl","-s","--max-time","12",
        "-A","Mozilla/5.0","-H","Origin: https://pump.fun",
        "-H","Referer: https://pump.fun/", url], capture_output=True)
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
        time.sleep(60)
