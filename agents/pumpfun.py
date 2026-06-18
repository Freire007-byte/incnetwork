#!/usr/bin/env python3
# Agente 5: pump.fun deep scanner v2 -- throttle seguro
import subprocess, json, time, sys
sys.path.insert(0, "/root/caca-pump/agents")
import db as DB

LOG       = "/root/caca-pump/logs/pumpfun.log"
SCAN_DAYS = 2       # scan inicial 2 dias (expande ate 7 ao longo da semana)
MAX_DAYS  = 7
PAGE_SIZE = 25      # menor pagina = menos carga
REQ_DELAY = 2.5     # segundos entre requests
MAX_PAGES = 120

BASE = "https://frontend-api.pump.fun"

def log(m):
    t = time.strftime("%H:%M:%S")
    line = f"[{t}] [PUMPFUN] {m}"
    print(line, flush=True)
    try:
        with open(LOG, "a") as f: f.write(line + "\n")
    except: pass

def curl(url, timeout=15):
    try:
        r = subprocess.run(["curl","-s","--max-time",str(timeout),
            "-A","Mozilla/5.0","-H","Accept: application/json",
            "-H","Origin: https://pump.fun",
            "-H","Referer: https://pump.fun/", url],
            capture_output=True)
        return json.loads(r.stdout) if r.stdout else None
    except: return None

def save_coin(conn, coin):
    mint = coin.get("mint", "")
    if not mint: return False
    sym  = coin.get("symbol", "?")[:20]
    name = coin.get("name", "?")[:50]
    ts   = coin.get("created_timestamp", 0)
    if ts and ts > 1e12: ts = ts // 1000
    mc   = float(coin.get("usd_market_cap") or 0)
    try:
        conn.execute("INSERT OR IGNORE INTO tokens VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (mint, sym, name, int(ts or 0), mc, 0, 0, 0, 0,
             int(time.time()), "pumpfun"))
        conn.commit()
        return True
    except: return False

def deep_scan(days):
    log(f"Deep scan pump.fun: {days} dias")
    conn   = DB.get_conn()
    cutoff = time.time() - days * 86400
    total  = 0

    for ep_name, url_tpl in [
        ("recentes", BASE + "/coins?offset={}&limit={}&sort=created_timestamp&order=DESC&includeNsfw=false"),
        ("graduados", BASE + "/coins?offset={}&limit={}&sort=last_trade_timestamp&order=DESC&includeNsfw=false"),
    ]:
        offset = 0
        pages  = 0
        log(f"  Varrendo: {ep_name}")
        while pages < MAX_PAGES:
            data = curl(url_tpl.format(offset, PAGE_SIZE))
            if not data or not isinstance(data, list) or len(data) == 0: break
            stop = False
            added = 0
            for coin in data:
                ts = coin.get("created_timestamp", 0)
                if ts and ts > 1e12: ts = ts // 1000
                if ts and ts < cutoff: stop = True; break
                if save_coin(conn, coin): added += 1
            total  += added
            pages  += 1
            offset += PAGE_SIZE
            if pages % 20 == 0:
                log(f"    pag={pages} +{added} total={total}")
            time.sleep(REQ_DELAY)
            if stop: break

    king = curl(BASE + "/coins/king-of-the-hill?includeNsfw=false")
    if king and isinstance(king, list):
        for c in king:
            if save_coin(conn, c): total += 1

    conn.close()
    log(f"Deep scan: +{total} tokens")
    return total

def monitor_novos():
    conn  = DB.get_conn()
    added = 0
    cutoff = time.time() - 600
    data = curl(BASE + "/coins?offset=0&limit=20&sort=created_timestamp&order=DESC&includeNsfw=false")
    if data and isinstance(data, list):
        for coin in data:
            ts = coin.get("created_timestamp", 0)
            if ts and ts > 1e12: ts = ts // 1000
            if ts and ts < cutoff: continue
            if save_coin(conn, coin): added += 1
    conn.close()
    return added

def enrich_dexscreener(batch=8):
    conn = DB.get_conn()
    rows = conn.execute(
        "SELECT mint FROM tokens WHERE source='pumpfun' AND liq_usd=0 LIMIT ?",
        (batch,)).fetchall()
    enriched = 0
    for (mint,) in rows:
        dex = curl(f"https://api.dexscreener.com/latest/dex/tokens/{mint}", timeout=10)
        if not dex or not dex.get("pairs"): continue
        sols = [p for p in dex["pairs"] if p.get("chainId") == "solana"]
        if not sols: continue
        p   = max(sols, key=lambda x: float((x.get("liquidity") or {}).get("usd") or 0))
        liq = float((p.get("liquidity") or {}).get("usd") or 0)
        h1  = float((p.get("priceChange") or {}).get("h1") or 0)
        h6  = float((p.get("priceChange") or {}).get("h6") or 0)
        vol = float((p.get("volume") or {}).get("h24") or 0)
        if liq > 0:
            conn.execute("UPDATE tokens SET liq_usd=?,vol_24h=?,peak_h1=?,peak_h6=? WHERE mint=?",
                (liq, vol, h1, h6, mint))
            enriched += 1
        time.sleep(1.5)
    conn.commit()
    conn.close()
    if enriched > 0: log(f"Enriched {enriched} tokens")

if __name__ == "__main__":
    log("=" * 50)
    log(f"PUMPFUN SCANNER v2 | scan={SCAN_DAYS}d delay={REQ_DELAY}s")
    log("=" * 50)

    deep_scan(SCAN_DAYS)
    enrich_dexscreener(20)

    last_deep = time.time()
    deep_days = SCAN_DAYS
    cycle     = 0

    while True:
        cycle += 1
        added = monitor_novos()
        if added > 0: log(f"+{added} novos pump.fun")

        if cycle % 5 == 0:
            enrich_dexscreener(8)

        if cycle % 30 == 0:
            conn  = DB.get_conn()
            n_pf  = conn.execute("SELECT COUNT(*) FROM tokens WHERE source='pumpfun'").fetchone()[0]
            n_tot = conn.execute("SELECT COUNT(*) FROM tokens").fetchone()[0]
            conn.close()
            log(f"STATUS: {n_pf} pumpfun | {n_tot} total")

        if time.time() - last_deep > 14400:
            deep_days = min(deep_days + 1, MAX_DAYS)
            deep_scan(deep_days)
            last_deep = time.time()

        time.sleep(120)
