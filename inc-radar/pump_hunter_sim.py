#!/usr/bin/env python3
# pump_hunter_sim.py -- Simulacao 7 dias, 1 SOL por entrada
# Detecta pumps reais e simula trades sem gastar SOL

import subprocess, json, time, os, queue, threading, sys, collections
from concurrent.futures import ThreadPoolExecutor, as_completed
try:
    import websocket
    _HAS_WS = True
except ImportError:
    _HAS_WS = False

SIM_DURATION_MIN = 280  # 4h 40min -- 70 min margem antes do job timeout de 350min
ENTRY_SOL        = 0.3   # 0.3 SOL por entrada (posicao menor, mais trades)
TP_PCT           = 0.40   # +40% take profit
SL_PCT           = 0.12   # -12% stop loss
MAX_HOLD_MIN     = 10     # saida forcada apos 10 min
MAX_POSITIONS    = 3

MIN_WHALE_COUNT  = 2
MIN_SOL_5MIN     = 0.8    # relaxado: era 2.0
MAX_TOKEN_AGE_MIN= 30     # relaxado: era 20min
MAX_BOT_RATIO    = 0.80   # relaxado: era 0.75
MIN_LIQ_USD      = 5000   # relaxado: era 8000
MIN_BUYSELL_RATIO= 1.5    # relaxado: era 2.0
WHALE_SOL_MIN    = 0.3
BOT_SOL_MAX      = 0.005

LOG_FILE    = "/tmp/inc_study/sim_results.txt"
TRADES_FILE = "/tmp/inc_study/sim_trades.jsonl"

os.makedirs("/tmp/inc_study", exist_ok=True)

candidate_q = queue.Queue(maxsize=200)
signal_q    = queue.Queue(maxsize=10)

positions    = {}
trades       = []
sl_blacklist = set()   # tokens que SL'd nesta sessão — não re-entrar
lock         = threading.Lock()
start_ts     = time.time()

# --- MODO ADAPTATIVO TEMPO REAL ---
adaptive = {
    "recent_pnl":    [],          # últimos 10 PnL em SOL
    "min_liq":       MIN_LIQ_USD,
    "min_sol5m":     MIN_SOL_5MIN,
    "min_buysell":   MIN_BUYSELL_RATIO,
    "consec_losses": 0,           # perdas consecutivas
    "consec_wins":   0,           # wins consecutivos
    "pause_until":   0,           # pausa entradas (timestamp)
}

def _update_adaptive(pnl_sol):
    adaptive["recent_pnl"].append(pnl_sol)
    if len(adaptive["recent_pnl"]) > 10:
        adaptive["recent_pnl"].pop(0)

    if pnl_sol > 0:
        adaptive["consec_losses"] = 0
        adaptive["consec_wins"]  += 1
    else:
        adaptive["consec_wins"]   = 0
        adaptive["consec_losses"] += 1

    # 3 perdas consecutivas → pausa 10 min + filtros máximos
    if adaptive["consec_losses"] >= 3:
        adaptive["pause_until"]   = time.time() + 600
        adaptive["min_liq"]       = min(MIN_LIQ_USD * 2.0, 20000)
        adaptive["min_sol5m"]     = min(MIN_SOL_5MIN * 2.0, 6.0)
        adaptive["min_buysell"]   = min(MIN_BUYSELL_RATIO * 1.5, 3.5)
        log(f"[ADAPTIVE] ⚠ 3 PERDAS CONSECUTIVAS → PAUSAR 10min | filtros máximos")
        return

    n  = len(adaptive["recent_pnl"])
    if n < 2: return          # adapta a partir do 2º trade

    wr = sum(1 for x in adaptive["recent_pnl"] if x > 0) / n

    if wr < 0.35:
        adaptive["min_liq"]     = min(MIN_LIQ_USD * 1.5, 15000)
        adaptive["min_sol5m"]   = min(MIN_SOL_5MIN * 1.5, 4.0)
        adaptive["min_buysell"] = min(MIN_BUYSELL_RATIO * 1.3, 3.0)
    elif wr > 0.60:
        adaptive["min_liq"]     = max(MIN_LIQ_USD * 0.85, 4000)
        adaptive["min_sol5m"]   = max(MIN_SOL_5MIN * 0.85, 0.6)
        adaptive["min_buysell"] = max(MIN_BUYSELL_RATIO * 0.85, 1.3)
    else:
        adaptive["min_liq"]     = MIN_LIQ_USD
        adaptive["min_sol5m"]   = MIN_SOL_5MIN
        adaptive["min_buysell"] = MIN_BUYSELL_RATIO

    log(f"[ADAPTIVE] WR={wr:.0%} ({n}tr) cl={adaptive['consec_losses']} cw={adaptive['consec_wins']} → liq=${adaptive['min_liq']:.0f} sol5m={adaptive['min_sol5m']:.1f} B/S={adaptive['min_buysell']:.1f}")

def _dynamic_tp(m5, h1):
    """TP maior quando momentum é mais forte."""
    if m5 >= 15 or h1 >= 50:
        return 0.55
    elif m5 >= 8 or h1 >= 25:
        return 0.47
    return TP_PCT

def log(msg):
    t_min = (time.time() - start_ts) / 60
    line  = f"[{time.strftime('%H:%M:%S', time.gmtime())}] [t={t_min:.1f}min] {msg}"
    print(line, flush=True)
    try:
        with open(LOG_FILE, "a") as f: f.write(line + "\n")
    except: pass

def curl_get(url, timeout=12, retries=2):
    for attempt in range(retries):
        try:
            r = subprocess.run(["curl","-s","--max-time",str(timeout),
                "-A","Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "-H","Accept: application/json", url],
                capture_output=True)
            if r.returncode != 0:
                if attempt < retries - 1:
                    time.sleep(1 + attempt)
                    continue
                return None
            return json.loads(r.stdout) if r.stdout else None
        except:
            if attempt < retries - 1:
                time.sleep(1 + attempt)
                continue
            return None
    return None

_sol_price_cache = {"price": 175.0, "ts": 0}
def get_sol_price():
    global _sol_price_cache
    now = time.time()
    if now - _sol_price_cache["ts"] < 60:  # cache 60s
        return _sol_price_cache["price"]
    try:
        d = curl_get("https://api.coingecko.com/api/v3/simple/price?ids=solana&vs_currencies=usd", timeout=8)
        if d and "solana" in d and "usd" in d["solana"]:
            price = float(d["solana"]["usd"])
            _sol_price_cache = {"price": price, "ts": now}
            return price
    except: pass
    return _sol_price_cache["price"]  # fallback último valor

def get_price(mint):
    d = curl_get(f"https://api.dexscreener.com/latest/dex/tokens/{mint}", timeout=8)
    if not d or not d.get("pairs"): return None
    sols = [p for p in d["pairs"] if p.get("chainId") == "solana"]
    if not sols: return None
    p = max(sols, key=lambda x: float((x.get("liquidity") or {}).get("usd") or 0))
    price = float((p.get("priceUsd") or 0))
    return price if price > 0 else None

def pumpfun_scanner_worker():
    """WebSocket pump.fun — tokens novos em tempo real, adiciona após 60s de maturação."""
    if not _HAS_WS:
        log("[PUMPFUN] websocket-client não instalado — ignorando")
        return
    pending = collections.deque()  # (mint, ts_add)
    MATURAR_S = 60  # espera 60s para DexScreener indexar

    def on_message(ws, msg):
        try:
            d = json.loads(msg)
            mint = d.get("mint") or d.get("tokenAddress") or d.get("address")
            if mint:
                pending.append((mint, time.time()))
        except: pass

    def on_error(ws, err):
        log(f"[PUMPFUN] WS erro: {err}")

    def on_close(ws, *a):
        log("[PUMPFUN] WS fechado — reconectar em 10s")

    def on_open(ws):
        log("[PUMPFUN] conectado — subscribeNewToken")
        ws.send(json.dumps({"method": "subscribeNewToken"}))

    log("[PUMPFUN] iniciado")
    while True:
        elapsed = (time.time() - start_ts) / 60
        if elapsed >= SIM_DURATION_MIN:
            break
        try:
            ws = websocket.WebSocketApp(
                "wss://pumpportal.fun/api/data",
                on_open=on_open, on_message=on_message,
                on_error=on_error, on_close=on_close,
            )
            t = threading.Thread(target=ws.run_forever, kwargs={"ping_interval": 30}, daemon=True)
            t.start()
            while t.is_alive():
                now = time.time()
                # Liberta tokens maduros para classificação
                while pending and now - pending[0][1] >= MATURAR_S:
                    mint, _ = pending.popleft()
                    try:
                        candidate_q.put_nowait(mint)
                    except queue.Full:
                        pass
                time.sleep(2)
        except Exception as e:
            log(f"[PUMPFUN] erro: {e}")
        time.sleep(10)

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
    SEEN_EXPIRY = 90   # reavalia token apos 90s (pump pode acelerar rapidamente)
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
        SOL_PRICE = get_sol_price()
        avg_buy_usd = vol_m5 / max(1, buys_m5)
        whale_c  = max(0, int(vol_m5 / (WHALE_SOL_MIN * SOL_PRICE * 1.5)))
        bot_c    = max(0, int(buys_m5 * max(0, 1 - avg_buy_usd / 40)))
        sol_5min = vol_m5 / SOL_PRICE
        total    = whale_c + bot_c
        bot_ratio = bot_c / max(1, total)
        buy_sell = buys_m5 / max(1, sells_m5)
        reasons   = []
        cur_liq     = adaptive["min_liq"]
        cur_sol5m   = adaptive["min_sol5m"]
        cur_buysell = adaptive["min_buysell"]
        if mint in sl_blacklist:      reasons.append(f"SL_BLACKLIST")
        if m5 < 5:                    reasons.append(f"m5={m5:+.0f}%<5%")
        if liq < cur_liq:             reasons.append(f"liq=${liq:,.0f}<${cur_liq:,.0f}")
        if whale_c < MIN_WHALE_COUNT: reasons.append(f"whales={whale_c}<{MIN_WHALE_COUNT}")
        if sol_5min < cur_sol5m:      reasons.append(f"sol5m={sol_5min:.1f}<{cur_sol5m:.1f}")
        if bot_ratio > MAX_BOT_RATIO: reasons.append(f"bots={bot_ratio:.0%}>{MAX_BOT_RATIO:.0%}")
        if buy_sell < cur_buysell:    reasons.append(f"B/S={buy_sell:.1f}<{cur_buysell:.1f}")

        if reasons:
            log(f"[skip] {sym} h1={h1:+.0f}% m5={m5:+.0f}% liq=${liq:,.0f} wh={whale_c} sol5m={sol_5min:.2f} bot={bot_ratio:.0%} | {reasons}")
            continue

        sig = {"mint":mint,"symbol":sym,"price":price,"liq":liq,
               "h1":h1,"m5":m5,"age_min":age_min,"whale_count":whale_c,
               "sol_5min":round(sol_5min,1),"bot_ratio":round(bot_ratio,2)}
        log(f"[SINAL] >>> {sym} m5={m5:+.0f}% age={age_min:.0f}m "
            f"liq=${liq:,.0f} wh={whale_c} sol5m={sol_5min:.1f} B/S={buy_sell:.1f} bot={bot_ratio:.0%}")
        try: signal_q.put_nowait(sig)
        except queue.Full: pass

def trader_worker():
    log("[TRADER] iniciado")
    last_status = 0.0

    while True:
        elapsed = (time.time() - start_ts) / 60
        if elapsed >= SIM_DURATION_MIN:
            break

        if elapsed - last_status >= 2.0:
            last_status = elapsed
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

        # Verifica pausa por perdas consecutivas
        if time.time() < adaptive["pause_until"]:
            rem = (adaptive["pause_until"] - time.time()) / 60
            log(f"[ADAPTIVE] ENTRADA BLOQUEADA — pausa ativa ({rem:.1f}min restantes)")
            time.sleep(30)
            continue

        with lock:
            if len(positions) >= MAX_POSITIONS: continue
            if sig["mint"] in positions: continue

        entry  = get_price(sig["mint"]) or sig["price"]
        dyn_tp = _dynamic_tp(sig["m5"], sig["h1"])
        tp     = entry * (1 + dyn_tp)
        sl     = entry * (1 - SL_PCT)
        log(f"[SIM ENTRADA] {sig['symbol']} @ ${entry:.8f} | TP=${tp:.8f} (+{dyn_tp:.0%}) SL=${sl:.8f} (-{SL_PCT:.0%}) max={MAX_HOLD_MIN}min")
        with lock:
            positions[sig["mint"]] = {
                "symbol": sig["symbol"], "entry": entry,
                "tp": tp, "sl": sl, "dyn_tp": dyn_tp,
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

        if mints:
            with ThreadPoolExecutor(max_workers=len(mints)) as exe:
                price_map = {m: None for m in mints}
                futures = {exe.submit(get_price, m): m for m in mints}
                for fut in as_completed(futures, timeout=10):
                    price_map[futures[fut]] = fut.result()

            for mint in mints:
                with lock:
                    if mint not in positions: continue
                    pos = positions[mint]

                price    = price_map.get(mint)
                hold_min = (time.time() - pos["entry_time"]) / 60

                if hold_min >= MAX_HOLD_MIN:
                    price = price or get_price(mint) or pos["entry"]
                    # Extende 5min se posição lucrativa acima do BE (+8%)
                    if price and price > pos["entry"] * 1.08 and hold_min < MAX_HOLD_MIN + 5:
                        continue
                    with lock:
                        if mint in positions:
                            _close_position(mint, pos, price, "TEMPO")
                    continue

                if not price: continue

                # Trailing SL multi-nível (tempo real)
                with lock:
                    if mint in positions:
                        cur_sl  = positions[mint]["sl"]
                        ep      = pos["entry"]
                        sym     = pos["symbol"]
                        trail_levels = [
                            (1.50, 1.35, "+50%→SL+35%"),
                            (1.35, 1.20, "+35%→SL+20%"),
                            (1.20, 1.08, "+20%→SL+8%"),
                        ]
                        for trig, sl_mult, label in trail_levels:
                            if price >= ep * trig:
                                new_sl = ep * sl_mult
                                if new_sl > cur_sl:
                                    positions[mint]["sl"] = new_sl
                                    log(f"[TRAIL] {sym} {label} @ ${new_sl:.8f}")
                                break
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

        time.sleep(1)

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
    _update_adaptive(pnl_sol)
    with open(TRADES_FILE, "a") as f:
        f.write(json.dumps(trade) + "\n")
    if reason == "SL":
        sl_blacklist.add(mint)
        log(f"[BLACKLIST] {pos['symbol']} adicionado — não re-entra esta sessão")
    label = "LUCRO" if pnl_sol > 0 else "PERDA"
    log(f"[SIM SAIDA {label}] {pos['symbol']} | {reason} {hold:.0f}min | "
        f"{pnl_pct:+.2f}% | {pnl_sol:+.5f} SOL | motivo: {reason}")
    del positions[mint]

if __name__ == "__main__":
    log("=" * 60)
    log(f"SIMULACAO PUMP HUNTER -- {SIM_DURATION_MIN/60:.0f} horas -- SEM SOL REAL")
    log(f"Config: entry={ENTRY_SOL}SOL tp={TP_PCT:.0%}(dyn) sl={SL_PCT:.0%} max={MAX_HOLD_MIN}min")
    log(f"MODO ADAPTATIVO: filtros ajustam com WR dos ultimos 10 trades")
    log(f"  TP dinamico: m5>=15%→55% | m5>=8%→47% | base→{TP_PCT:.0%}")
    log(f"  Filtros: liq WR<35%→apertar, WR>60%→relaxar (base cada 5 trades)")
    log("=" * 60)

    threads = [
        threading.Thread(target=pumpfun_scanner_worker, daemon=True),
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
