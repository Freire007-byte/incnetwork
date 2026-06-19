#!/usr/bin/env python3
# pump_hunter_sim.py -- Simulacao 7 dias, 1 SOL por entrada
# Detecta pumps reais e simula trades sem gastar SOL

import subprocess, json, time, os, queue, threading, sys

SIM_DURATION_MIN = 10080  # 7 dias em minutos
ENTRY_SOL        = 10.0
TP_PCT           = 0.35   # +35% take profit
SL_PCT           = 0.12   # -12% stop loss
MAX_HOLD_MIN     = 12     # saida forcada apos 12 min
MAX_POSITIONS    = 3

MIN_WHALE_COUNT  = 2
MIN_SOL_5MIN     = 0.5
MAX_TOKEN_AGE_MIN= 60
MAX_BOT_RATIO    = 0.90
MIN_LIQ_USD      = 5000
WHALE_SOL_MIN    = 0.3
BOT_SOL_MAX      = 0.005

HELIUS_KEY = "a6a9f38c-3e3d-46a5-8038-6a3baa6c0298"

LOG_FILE    = "/tmp/inc_study/sim_results.txt"
TRADES_FILE = "/tmp/inc_study/sim_trades.jsonl"

os.makedirs("/tmp/inc_study", exist_ok=True)

candidate_q = queue.Queue(maxsize=200)
signal_q    = queue.Queue(maxsize=10)

positions = {}
trades    = []
lock      = threading.Lock()
start_ts  = time.time()

def log(msg):
    t_min = (time.time() - start_ts) / 60
    line  = f"[{time.strftime('%H:%M:%S')}] [t={t_min:.1f}min] {msg}"
    print(line, flush=True)
    try:
        with open(LOG_FILE, "a") as f: f.write(line + "\n")
    except: pass

def curl_get(url, timeout=12):
    try:
        r = subprocess.run(["curl","-s","--max-time",str(timeout),
            "-A","Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "-H","Accept: application/json", url],
            capture_output=True)
        return json.loads(r.stdout) if r.stdout else None
    except: return None

def get_price(mint):
    d = curl_get(f"https://api.dexscreener.com/latest/dex/tokens/{mint}", timeout=8)
    if not d or not d.get("pairs"): return None
    sols = [p for p in d["pairs"] if p.get("chainId") == "solana"]
    if not sols: return None
    p = max(sols, key=lambda x: float((x.get("liquidity") or {}).get("usd") or 0))
    price = float((p.get("priceUsd") or 0))
    return price if price > 0 else None

def scanner_worker():
    log("[SCANNER] iniciado")
    while True:
        elapsed = (time.time() - start_ts) / 60
        if elapsed >= SIM_DURATION_MIN:
            break

        candidates = []
        now_ms     = time.time() * 1000
        cutoff_ms  = now_ms - MAX_TOKEN_AGE_MIN * 60000

        d = curl_get("https://api.dexscreener.com/token-profiles/latest/v1")
        if d and isinstance(d, list):
            for t in d[:30]:
                if t.get("chainId") != "solana": continue
                mint = t.get("tokenAddress", "")
                if not mint: continue
                candidates.append(mint)

        d2 = curl_get("https://api.dexscreener.com/token-boosts/latest/v1")
        if d2 and isinstance(d2, list):
            for t in d2[:30]:
                if t.get("chainId") != "solana": continue
                mint = t.get("tokenAddress", "")
                if mint and mint not in candidates:
                    candidates.append(mint)

        added = 0
        for mint in candidates:
            try:
                candidate_q.put_nowait(mint)
                added += 1
            except queue.Full:
                continue

        if added > 0:
            log(f"[SCANNER] +{added} candidatos (fila={candidate_q.qsize()})")
        time.sleep(15)

def classifier_worker():
    log("[CLASSIFIER] iniciado")
    seen = {}
    SEEN_EXPIRY = 900  # reavalia token apos 15min
    while True:
        elapsed = (time.time() - start_ts) / 60
        if elapsed >= SIM_DURATION_MIN:
            break
        try:
            mint = candidate_q.get(timeout=5)
        except queue.Empty:
            continue
        now = time.time()
        seen = {m: t for m, t in seen.items() if now - t < SEEN_EXPIRY}
        if mint in seen: continue
        seen[mint] = now

        d = curl_get(f"https://api.dexscreener.com/latest/dex/tokens/{mint}", timeout=8)
        if not d or not d.get("pairs"): continue
        sols = [p for p in d["pairs"] if p.get("chainId") == "solana"]
        if not sols: continue
        p    = max(sols, key=lambda x: float((x.get("liquidity") or {}).get("usd") or 0))
        ca   = p.get("pairCreatedAt", 0)
        age_min = (time.time()*1000 - ca) / 60000 if ca else 999
        if age_min > MAX_TOKEN_AGE_MIN: continue
        liq  = float((p.get("liquidity") or {}).get("usd") or 0)
        h1   = float((p.get("priceChange") or {}).get("h1") or 0)
        m5   = float((p.get("priceChange") or {}).get("m5") or 0)
        sym  = (p.get("baseToken") or {}).get("symbol", "?")
        price = float(p.get("priceUsd") or 0)
        if not price: continue

        # Classifica via DexScreener (sem Helius)
        vol_m5   = float(p.get("volume", {}).get("m5") or 0)
        buys_m5  = int((p.get("txns") or {}).get("m5", {}).get("buys") or 0)
        sells_m5 = int((p.get("txns") or {}).get("m5", {}).get("sells") or 0)
        buys_h1  = int((p.get("txns") or {}).get("h1", {}).get("buys") or 0)
        SOL_PRICE = 175.0
        avg_buy_usd = vol_m5 / max(1, buys_m5)
        whale_c  = max(0, int(vol_m5 / (WHALE_SOL_MIN * SOL_PRICE * 1.5)))
        bot_c    = max(0, int(buys_m5 * max(0, 1 - avg_buy_usd / 40)))
        sol_5min = vol_m5 / SOL_PRICE
        total    = whale_c + bot_c
        bot_ratio = bot_c / max(1, total)
        reasons   = []
        if m5 <= 0:                     reasons.append(f"m5={m5:+.0f}%<=0")
        if liq < MIN_LIQ_USD:           reasons.append(f"liq=${liq:,.0f}<${MIN_LIQ_USD:,.0f}")
        if whale_c < MIN_WHALE_COUNT:   reasons.append(f"whales={whale_c}<{MIN_WHALE_COUNT}")
        if sol_5min < MIN_SOL_5MIN:     reasons.append(f"sol5m={sol_5min:.1f}<{MIN_SOL_5MIN}")
        if bot_ratio > MAX_BOT_RATIO:   reasons.append(f"bots={bot_ratio:.0%}>{MAX_BOT_RATIO:.0%}")

        if reasons:
            log(f"[skip] {sym} h1={h1:+.0f}% m5={m5:+.0f}% liq=${liq:,.0f} wh={whale_c} sol5m={sol_5min:.2f} bot={bot_ratio:.0%} | {reasons}")
            continue

        sig = {"mint":mint,"symbol":sym,"price":price,"liq":liq,
               "h1":h1,"m5":m5,"age_min":age_min,"whale_count":whale_c,
               "sol_5min":round(sol_5min,1),"bot_ratio":round(bot_ratio,2)}
        log(f"[SINAL] >>> {sym} h1={h1:+.0f}% m5={m5:+.0f}% age={age_min:.0f}m "
            f"liq=${liq:,.0f} whales={whale_c} sol5m={sol_5min:.1f} bot={bot_ratio:.0%}")
        try: signal_q.put_nowait(sig)
        except queue.Full: pass

def trader_worker():
    log("[TRADER] iniciado")
    total_pnl = 0.0
    wins = losses = 0

    while True:
        elapsed = (time.time() - start_ts) / 60
        if elapsed >= SIM_DURATION_MIN:
            break

        if elapsed % 2 < 0.05:
            with lock:
                n = len(positions)
                w = sum(1 for t in trades if t.get("pnl_sol",0) > 0)
                l = sum(1 for t in trades if t.get("pnl_sol",0) <= 0)
                p = sum(t.get("pnl_sol",0) for t in trades)
            log(f"[STATUS] posicoes={n} | trades={len(trades)} | wins={w} losses={l} | pnl={p:+.5f}SOL")

        try:
            sig = signal_q.get(timeout=5)
        except queue.Empty:
            time.sleep(1)
            continue

        with lock:
            if len(positions) >= MAX_POSITIONS: continue
            if sig["mint"] in positions: continue

        entry = get_price(sig["mint"]) or sig["price"]
        tp    = entry * (1 + TP_PCT)
        sl    = entry * (1 - SL_PCT)
        log(f"[SIM ENTRADA] {sig['symbol']} @ ${entry:.8f} | TP=${tp:.8f} (+{TP_PCT:.0%}) SL=${sl:.8f} (-{SL_PCT:.0%}) max={MAX_HOLD_MIN}min")
        with lock:
            positions[sig["mint"]] = {
                "symbol": sig["symbol"], "entry": entry,
                "tp": tp, "sl": sl,
                "entry_time": time.time(),
                "whale_count": sig["whale_count"],
                "sol_5min": sig["sol_5min"],
                "be_applied": False,
            }

def watchdog_worker():
    log("[WATCHDOG] iniciado")
    while True:
        elapsed = (time.time() - start_ts) / 60
        if elapsed >= SIM_DURATION_MIN:
            log("[SIM FIM] Duracao atingida")
            with lock:
                for mint, pos in list(positions.items()):
                    price = get_price(mint) or pos["entry"]
                    _close_position(mint, pos, price, "FIM_SIM")
            break

        with lock:
            mints = list(positions.keys())

        for mint in mints:
            with lock:
                if mint not in positions: continue
                pos = positions[mint]

            hold_min = (time.time() - pos["entry_time"]) / 60

            if hold_min >= MAX_HOLD_MIN:
                price = get_price(mint) or pos["entry"]
                with lock:
                    if mint in positions:
                        _close_position(mint, pos, price, "TEMPO")
                continue

            price = get_price(mint)
            if not price: continue

            with lock:
                if mint in positions and not positions[mint].get("be_applied") and price >= pos["entry"] * 1.20:
                    positions[mint]["sl"] = pos["entry"] * 1.01
                    positions[mint]["be_applied"] = True
                    log(f"[BREAK-EVEN] {pos['symbol']} SL → entry+1% @ ${pos['entry']*1.01:.8f}")
                if mint in positions:
                    pos = positions[mint]

            reason = None
            if price >= pos["tp"]:
                reason = "TP"
            elif price <= pos["sl"]:
                reason = "SL"

            if reason:
                with lock:
                    if mint in positions:
                        _close_position(mint, pos, price, reason)
        time.sleep(5)

def _close_position(mint, pos, price, reason):
    pnl_pct = (price - pos["entry"]) / pos["entry"] * 100
    pnl_sol = ENTRY_SOL * (pnl_pct / 100)
    hold    = (time.time() - pos["entry_time"]) / 60
    trade   = {
        "symbol":   pos["symbol"],
        "mint":     mint,
        "entry":    pos["entry"],
        "exit":     price,
        "pnl_pct":  round(pnl_pct, 2),
        "pnl_sol":  round(pnl_sol, 5),
        "exit_reason": reason,
        "hold_min": round(hold, 1),
        "ts":       int(time.time()),
    }
    trades.append(trade)
    with open(TRADES_FILE, "a") as f:
        f.write(json.dumps(trade) + "\n")
    label = "LUCRO" if pnl_sol > 0 else "PERDA"
    log(f"[SIM SAIDA {label}] {pos['symbol']} | {reason} {hold:.0f}min | "
        f"{pnl_pct:+.2f}% | {pnl_sol:+.5f} SOL | motivo: {reason}")
    del positions[mint]

if __name__ == "__main__":
    log("=" * 55)
    log(f"SIMULACAO PUMP HUNTER -- {SIM_DURATION_MIN/60:.0f} horas -- SEM SOL REAL")
    log(f"Config: entry={ENTRY_SOL}SOL tp={TP_PCT:.0%} sl={SL_PCT:.0%} max={MAX_HOLD_MIN}min")
    log("=" * 55)

    threads = [
        threading.Thread(target=scanner_worker,    daemon=True),
        threading.Thread(target=classifier_worker, daemon=True),
        threading.Thread(target=trader_worker,     daemon=True),
        threading.Thread(target=watchdog_worker,   daemon=True),
    ]
    for t in threads: t.start()
    for t in threads: t.join()

    total_pnl = sum(t.get("pnl_sol", 0) for t in trades)
    wins      = sum(1 for t in trades if t.get("pnl_sol", 0) > 0)
    log(f"\nFIM: {len(trades)} trades | {wins} wins | PnL={total_pnl:+.5f} SOL")
