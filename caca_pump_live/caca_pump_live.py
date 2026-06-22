"""
caca_pump_live.py — Caça Pump local 24/7
Entra em tokens pump.fun nos primeiros 10 segundos do lançamento.
Scanner via API pump.fun a cada 3s. Preço via bonding curve.
"""
import json, time, os, subprocess, threading, queue, collections, urllib.request, ssl, hashlib, struct, base64 as _b64

# ── Real trader (usa wallet quando live_wallet.json tiver live_trading=true) ─
try:
    import real_trader as _rt
    LIVE_TRADING = _rt.is_live()
except Exception:
    _rt = None
    LIVE_TRADING = False

# ── Config ────────────────────────────────────────────────────────────────────
ENTRY_SOL        = 0.5     # tamanho da posição simulada
TP_PCT           = 0.104   # +10.4% — pico típico antes do crash
SL_PCT           = 0.08    # -8%
MAX_HOLD_MIN     = 8       # 8 min máx — tokens dumpam rápido
MAX_POSITIONS    = 3
BREAK_EVEN_TRIGGER = 0.06  # quando +6% → SL move para entry+1%
BREAK_EVEN_SL    = 0.01

MAX_AGE_SEC      = 60      # tokens < 60 segundos do lançamento (1º minuto)
MIN_MC_USD       = 3_000   # mínimo $3k market cap (confirma que alguém comprou)

PRICE_RETRY_MAX  = 4       # retries de preço para tokens sem dados ainda
PRICE_RETRY_WAIT = 20      # segundos entre retries

SCAN_INTERVAL    = 3       # scan pump.fun a cada 3 segundos
WATCHDOG_INTERVAL = 10     # verificação SL/TP a cada 10s
POST_LOSS_POLL   = 60
POST_LOSS_MINS   = 60

_BASE        = os.environ.get("CACA_PUMP_DIR", os.path.dirname(os.path.abspath(__file__)))
TRADES_FILE  = os.path.join(_BASE, "inc_study", "sim_trades.jsonl")
OUT_JSON     = os.path.join(_BASE, "inc_study", "caca_pump_live_data.json")
LOG_FILE     = os.path.join(_BASE, "inc_study", "live_results.txt")
MEMORY_FILE  = os.path.join(_BASE, "inc_study", "memory.json")

os.makedirs(os.path.join(_BASE, "inc_study"), exist_ok=True)


# ── Memória persistente — aprende entre sessões ───────────────────────────────

def load_memory():
    """Carrega memória de manipuladores conhecidos."""
    try:
        with open(MEMORY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"manipulator_wallets": {}, "patterns": [], "stats": {}}

def save_memory(mem):
    tmp = MEMORY_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(mem, f, ensure_ascii=False, indent=2)
    os.replace(tmp, MEMORY_FILE)

# Carrega memória no arranque
MEMORY = load_memory()
log_fn = print  # placeholder — será substituído depois de log() ser definido

# ── Estado global ─────────────────────────────────────────────────────────────
positions         = {}
closed_trades     = []
post_loss_records = []
seen_mints        = set()
lock              = threading.Lock()
candidate_q       = queue.Queue(maxsize=500)
signal_q          = queue.Queue(maxsize=20)
start_ts          = time.time()

_sol_price        = 150.0   # cache SOL/USD, actualizado a cada 60s
_sol_price_ts     = 0.0


def log(msg):
    ts = time.strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


_NO_WIN = 0x08000000 if os.name == 'nt' else 0  # CREATE_NO_WINDOW (Windows only)

def curl(url, timeout=8, method="GET", body=None, headers=None):
    cmd = ["curl", "-s", "--max-time", str(timeout),
           "-A", "Mozilla/5.0",
           "-H", "Accept: application/json",
           "-H", "Origin: https://pump.fun",
           "-H", "Referer: https://pump.fun/"]
    if headers:
        for h in headers:
            cmd += ["-H", h]
    if method == "POST" and body:
        cmd += ["-X", "POST", "-H", "Content-Type: application/json", "-d", body]
    cmd.append(url)
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=timeout + 4,
                           creationflags=_NO_WIN)
        return json.loads(r.stdout) if r.stdout else None
    except Exception:
        return None


# ── Preço SOL/USD ─────────────────────────────────────────────────────────────

def get_sol_price():
    global _sol_price, _sol_price_ts
    if time.time() - _sol_price_ts < 60:
        return _sol_price
    d = curl("https://api.coinbase.com/v2/prices/SOL-USD/spot", timeout=6)
    if d and (d.get("data") or {}).get("amount"):
        try:
            _sol_price = float(d["data"]["amount"])
            _sol_price_ts = time.time()
        except Exception:
            pass
    return _sol_price


# ── Preço via bonding curve pump.fun ──────────────────────────────────────────

def pumpfun_detail(mint):
    """Retorna dados do token via API pump.fun (curl)."""
    return curl(f"https://frontend-api.pump.fun/coins/{mint}", timeout=6)


def pumpfun_detail_urllib(mint):
    """Tenta pump.fun via urllib — contorna bloqueio Cloudflare do curl."""
    url = f"https://frontend-api.pump.fun/coins/{mint}"
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "application/json,*/*",
        "Accept-Language": "en-US,en;q=0.9",
        "Origin": "https://pump.fun",
        "Referer": "https://pump.fun/",
    })
    try:
        ctx = ssl.create_default_context()
        with urllib.request.urlopen(req, timeout=8) as resp:
            return json.loads(resp.read().decode())
    except Exception:
        return None


def pumpfun_price(detail, sol_price=None):
    """Calcula preço USD a partir da bonding curve."""
    if not detail:
        return None
    v_sol = detail.get("virtual_sol_reserves", 0)
    v_tok = detail.get("virtual_token_reserves", 0)
    if not v_sol or not v_tok:
        return None
    price_sol = v_sol / v_tok  # SOL por token (em lamports/microtokens)
    # lamports → SOL: /1e9; microtokens → tokens: /1e6  → ratio já em SOL/token se dividido por 1e3
    price_sol_real = price_sol / 1e3  # ajuste de escala pump.fun (empirico)
    return price_sol_real * (sol_price or get_sol_price())


def pumpfun_mc(detail):
    return float(detail.get("usd_market_cap") or 0)


# ── Bonding curve on-chain (preço sem precisar de pump.fun HTTP) ─────────────
# Ed25519 prime e constante d para verificação de ponto
_P   = 2**255 - 19
_D   = (-121665 * pow(121666, _P-2, _P)) % _P
_B58 = '123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz'

def _b58dec(s):
    n = 0
    for c in s:
        n = n * 58 + _B58.index(c)
    b = []
    while n:
        b.append(n & 255); n >>= 8
    return bytes([0] * (len(s) - len(s.lstrip('1'))) + b[::-1])

def _b58enc(b):
    n = int.from_bytes(b, 'big')
    r = []
    while n:
        n, rem = divmod(n, 58); r.append(_B58[rem])
    return '1' * sum(1 for x in b if x == 0) + ''.join(reversed(r))

def _off_curve(h):
    """True se h (32 bytes) NÃO é ponto Ed25519 válido → PDA válida para Solana."""
    y = int.from_bytes(h, 'little') & ((1 << 255) - 1)
    if y >= _P: return True
    y2 = y * y % _P
    v  = (_D * y2 + 1) % _P
    if v == 0: return True
    x2 = (y2 - 1) * pow(v, _P - 2, _P) % _P
    if x2 == 0: return False  # ponto (0,1) está na curva
    return pow(x2, (_P - 1) // 2, _P) != 1

def _find_pda(seeds_bytes, program_b58):
    """Deriva PDA Solana: seeds como bytes concatenados, prog como base58."""
    pb = _b58dec(program_b58)
    for n in range(255, -1, -1):
        h = hashlib.sha256(seeds_bytes + bytes([n]) + pb + b"ProgramDerivedAddress").digest()
        if _off_curve(h):
            return _b58enc(h)
    return None

def get_bonding_curve_price_rpc(mint, sol_price):
    """
    Obtém preço direto da bonding curve pump.fun via Solana RPC getAccountInfo.
    Funciona mesmo com pump.fun HTTP bloqueado. Retorna (price_usd, mc_usd).
    """
    try:
        pda = _find_pda(b"bonding-curve" + _b58dec(mint), PUMP_FUN_PROGRAM)
        if not pda:
            return None, 0
        info = rpc("getAccountInfo", [pda, {"encoding": "base64"}])
        if not info or not info.get("value"):
            return None, 0
        raw = (info["value"].get("data") or [None])[0]
        if not raw:
            return None, 0
        data = _b64.b64decode(raw)
        if len(data) < 49:
            return None, 0
        # Layout: discriminator(8) + virtual_token_reserves(8) + virtual_sol_reserves(8)
        #         + real_token_reserves(8) + real_sol_reserves(8) + token_total_supply(8) + complete(1)
        v_tok  = struct.unpack_from('<Q', data,  8)[0]  # microtokens
        v_sol  = struct.unpack_from('<Q', data, 16)[0]  # lamports
        supply = struct.unpack_from('<Q', data, 40)[0]  # microtokens (total supply)
        if not v_tok or not v_sol:
            return None, 0
        # Sanity: pump.fun inicia com 30 SOL virtual e ~1.073e15 tokens virtuais.
        # v_sol < 28 SOL ou v_tok > inicial = conta errada (não é bonding curve).
        if v_sol < 28_000_000_000 or v_tok > 1_074_000_000_000_000:
            return None, 0
        # Mesma fórmula que pumpfun_price: (v_sol/v_tok)/1e3 * sol_price
        price_usd = (v_sol / v_tok / 1e3) * sol_price
        mc_usd    = price_usd * (supply / 1e6)  # supply microtokens → tokens
        return price_usd, mc_usd
    except Exception:
        return None, 0


# ── Solana RPC — rastreamento de carteiras ───────────────────────────────────

_HELIUS_KEY = "59ba4837-5cbe-473d-9a25-45df57a9be29"
SOLANA_RPC  = f"https://mainnet.helius-rpc.com/?api-key={_HELIUS_KEY}"

def rpc(method, params):
    payload = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
    try:
        r = subprocess.run(
            ["curl", "-s", "--max-time", "12", "-X", "POST", SOLANA_RPC,
             "-H", "Content-Type: application/json", "-d", payload],
            capture_output=True, timeout=16, creationflags=_NO_WIN
        )
        return json.loads(r.stdout).get("result") if r.stdout else None
    except Exception:
        return None


def fetch_wallets(pool_address, limit=30):
    if not pool_address:
        return []
    sigs = rpc("getSignaturesForAddress", [pool_address, {"limit": limit}])
    if not sigs:
        return []
    wallets = []
    for sig_info in sigs[:15]:
        sig = sig_info.get("signature")
        if not sig:
            continue
        tx = rpc("getTransaction", [sig, {"encoding": "jsonParsed",
                                           "maxSupportedTransactionVersion": 0}])
        if not tx:
            continue
        ts = tx.get("blockTime", 0)
        meta = tx.get("meta") or {}
        pre  = meta.get("preBalances", [])
        post = meta.get("postBalances", [])
        accounts = ((tx.get("transaction") or {}).get("message") or {}).get("accountKeys", [])
        for i, acc in enumerate(accounts):
            addr = acc.get("pubkey", "") if isinstance(acc, dict) else str(acc)
            if not addr or len(addr) < 32:
                continue
            if addr in (pool_address, "11111111111111111111111111111111",
                        "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"):
                continue
            pre_b  = pre[i]  / 1e9 if i < len(pre)  else 0
            post_b = post[i] / 1e9 if i < len(post) else 0
            delta  = post_b - pre_b
            if abs(delta) < 0.01:
                continue
            wallets.append({
                "wallet":      addr[:8] + "…" + addr[-4:],
                "wallet_full": addr,
                "sol_amount":  round(abs(delta), 4),
                "type":        "buy" if delta > 0 else "sell",
                "ts":          ts,
            })
        time.sleep(0.08)
    return wallets


def analyze_wallets(wallets):
    by_wallet = collections.defaultdict(lambda: {"sol_total": 0, "n_txns": 0, "types": []})
    for w in wallets:
        k = w["wallet_full"]
        by_wallet[k]["sol_total"] += w["sol_amount"]
        by_wallet[k]["n_txns"]    += 1
        by_wallet[k]["types"].append(w["type"])
        by_wallet[k]["wallet"]     = w["wallet"]
    result = []
    for addr, d in by_wallet.items():
        sol = d["sol_total"]
        role = "BALEIA" if sol >= 5 else "TRADER" if sol >= 1 else "BOT" if d["n_txns"] >= 10 else "RETAIL"
        buys  = d["types"].count("buy")
        sells = d["types"].count("sell")
        result.append({
            "wallet":    d["wallet"],
            "role":      role,
            "sol_total": round(sol, 4),
            "n_txns":    d["n_txns"],
            "buys":      buys,
            "sells":     sells,
        })
    result.sort(key=lambda x: x["sol_total"], reverse=True)
    return result[:10]


# ── JSON output ───────────────────────────────────────────────────────────────

def write_json():
    with lock:
        sim_trades = list(closed_trades[-20:])
        pos_snap   = {m: dict(p) for m, p in positions.items()}
        pl_snap    = list(post_loss_records[-10:])
    wins  = sum(1 for t in closed_trades if t.get("pnl_sol", 0) > 0)
    pnl   = sum(t.get("pnl_sol", 0) for t in closed_trades)
    wr    = round(wins / max(1, len(closed_trades)) * 100, 1)
    n_manip_wallets = len(MEMORY.get("manipulator_wallets", {}))
    n_patterns      = len(MEMORY.get("patterns", []))
    data = {
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "status":     "SIGNAL" if signal_q.qsize() > 0 else "SCANNING",
        "n_positions": len(pos_snap),
        "positions":   pos_snap,
        "sim": {
            "total":   len(closed_trades),
            "wins":    wins,
            "losses":  len(closed_trades) - wins,
            "pnl_sol": round(pnl, 5),
            "wr_pct":  wr,
            "last_trades": sim_trades,
        },
        "post_loss": pl_snap,
        "uptime_min": round((time.time() - start_ts) / 60, 1),
        "memory": {
            "n_manipulator_wallets": n_manip_wallets,
            "n_patterns":            n_patterns,
            "recent_patterns":       MEMORY.get("patterns", [])[-5:],
        },
    }
    tmp = OUT_JSON + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(tmp, OUT_JSON)


# ── Post-loss monitor ─────────────────────────────────────────────────────────

def post_loss_monitor(trade):
    mint     = trade["mint"]
    sym      = trade["symbol"]
    sl_price = trade["exit_price"]
    history  = []
    max_recov = 0.0
    log(f"[POST-LOSS] {sym} monitorização {POST_LOSS_MINS}min após SL @ ${sl_price:.8f}")

    for i in range(POST_LOSS_MINS):
        time.sleep(POST_LOSS_POLL)
        sol_p = get_sol_price()
        detail = pumpfun_detail(mint)
        if not detail:
            continue
        cur = pumpfun_price(detail, sol_p)
        if not cur:
            continue
        mc = pumpfun_mc(detail)
        recov_pct = (cur - sl_price) / sl_price * 100 if sl_price > 0 else 0
        max_recov = max(max_recov, recov_pct)
        history.append({
            "min": i + 1, "ts": int(time.time()),
            "price": cur, "mc_usd": round(mc, 0),
            "recovery_pct": round(recov_pct, 2),
        })
        if (i + 1) % 10 == 0:
            log(f"[POST-LOSS] {sym} min={i+1} recov={recov_pct:+.1f}% max={max_recov:+.1f}%")

    # Verificar re-entradas de carteiras conhecidas
    known_wallets = [w["wallet_full"] for w in trade.get("wallets", [])
                     if w.get("wallet_full")]
    wallets_reentry = []
    if trade.get("pool_address") and known_wallets:
        new_raw = fetch_wallets(trade["pool_address"], limit=20)
        sl_ts = trade.get("ts", 0)
        for w in new_raw:
            if w["wallet_full"] in known_wallets and w["ts"] > sl_ts:
                wallets_reentry.append({
                    "wallet":      w["wallet"],
                    "sol_amount":  w["sol_amount"],
                    "type":        w["type"],
                    "min_after_sl": round((w["ts"] - sl_ts) / 60, 1),
                })

    manipulated = max_recov >= 10.0 or len(wallets_reentry) >= 2
    verdict = "MANIPULADO" if manipulated else "NATURAL"
    log(f"[POST-LOSS FIM] {sym} | max_recov={max_recov:+.1f}% | re-entradas={len(wallets_reentry)} | {verdict}")

    # ── Aprendizagem: guardar wallets manipuladoras na memória ───────────────
    if manipulated:
        all_wallets = [w.get("wallet_full", "") for w in trade.get("wallets", [])]
        all_wallets += [w.get("wallet_full", "") for w in wallets_reentry]
        for w in set(all_wallets):
            if not w:
                continue
            if w not in MEMORY["manipulator_wallets"]:
                MEMORY["manipulator_wallets"][w] = {"count": 0, "tokens": []}
            MEMORY["manipulator_wallets"][w]["count"] += 1
            MEMORY["manipulator_wallets"][w]["tokens"].append(sym)
        MEMORY["patterns"].append({
            "symbol":       sym,
            "max_recovery": round(max_recov, 2),
            "n_reentry":    len(wallets_reentry),
            "ts":           int(time.time()),
        })
        if len(MEMORY["patterns"]) > 200:
            MEMORY["patterns"] = MEMORY["patterns"][-200:]
        n_manip = len(MEMORY["manipulator_wallets"])
        log(f"[APRENDIZAGEM] {sym} MANIPULADO registado | {n_manip} carteiras manipuladoras conhecidas")
        save_memory(MEMORY)

    record = {
        "symbol":          sym, "mint": mint,
        "sl_price":        sl_price,
        "sl_time":         trade.get("close_dt", ""),
        "max_recovery_pct": round(max_recov, 2),
        "manipulated":     manipulated,
        "verdict":         verdict,
        "mins_monitored":  len(history),
        "wallets_reentry": wallets_reentry,
        "n_reentry":       len(wallets_reentry),
        "history":         history[-30:],
    }
    with lock:
        post_loss_records.append(record)
        if len(post_loss_records) > 20:
            post_loss_records.pop(0)
    write_json()


# ── Workers ───────────────────────────────────────────────────────────────────

PUMP_FUN_PROGRAM = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
SOL_RPC_WS       = f"wss://mainnet.helius-rpc.com/?api-key={_HELIUS_KEY}"

_ws_active = False

def _ws_on_message(ws, message):
    """Recebe logs do programa pump.fun em tempo real via Solana RPC WebSocket."""
    global _ws_active
    try:
        data = json.loads(message)
        # Resposta de subscrição
        if data.get("id") == 1:
            sub_id = data.get("result")
            log(f"[SCANNER-WS] subscrito logs pump.fun | sub_id={sub_id}")
            _ws_active = True
            return
        # Notificação de log
        method = data.get("method")
        if method != "logsNotification":
            return
        result = data["params"]["result"]["value"]
        logs   = result.get("logs", [])
        sig    = result.get("signature", "")
        err    = result.get("err")
        if err:
            return  # transacção falhou
        # Detectar criação de novo token (instrução "Create" na pump.fun)
        is_create = any("Instruction: Create" in l for l in logs)
        if not is_create:
            return
        # Extrair mint da transacção (via getTransaction)
        tx = rpc("getTransaction", [sig, {"encoding": "jsonParsed",
                                           "maxSupportedTransactionVersion": 0}])
        if not tx:
            return
        accounts = ((tx.get("transaction") or {}).get("message") or {}).get("accountKeys", [])
        # No pump.fun, o mint é o 1º token account criado
        for acc in accounts:
            addr = acc.get("pubkey", "") if isinstance(acc, dict) else str(acc)
            if len(addr) == 44 and "pump" in addr.lower():
                mint = addr
                break
        else:
            # fallback: pegar 1º account que não seja system/pump program
            for acc in accounts:
                addr = acc.get("pubkey", "") if isinstance(acc, dict) else str(acc)
                if len(addr) == 44 and addr != PUMP_FUN_PROGRAM:
                    mint = addr
                    break
            else:
                return

        with lock:
            if mint in seen_mints:
                return
            seen_mints.add(mint)

        log(f"[SCANNER-WS] NOVO TOKEN detectado | mint={mint[:16]}... | sig={sig[:16]}...")
        try:
            candidate_q.put_nowait({"mint": mint, "coin": {"mint": mint, "created_timestamp": int(time.time()*1000)}, "age_sec": 0.0})
        except queue.Full:
            pass

    except Exception as e:
        pass  # WS silencioso em caso de erro de parse

def _ws_on_open(ws):
    log("[SCANNER-WS] ligado ao Solana RPC WebSocket")
    sub = json.dumps({
        "jsonrpc": "2.0", "id": 1, "method": "logsSubscribe",
        "params": [{"mentions": [PUMP_FUN_PROGRAM]}, {"commitment": "processed"}]
    })
    ws.send(sub)

def _ws_on_error(ws, error):
    log(f"[SCANNER-WS] erro: {error}")

def _ws_on_close(ws, code, msg):
    global _ws_active
    _ws_active = False
    log(f"[SCANNER-WS] desligado (code={code}) — a reconectar em 5s")


def scanner_worker():
    """Scanner WebSocket tempo real + fallback HTTP DexScreener."""
    import websocket as _ws_lib
    log(f"[SCANNER] iniciado — WebSocket Solana RPC | janela < {MAX_AGE_SEC}s")

    def run_ws():
        while True:
            try:
                ws = _ws_lib.WebSocketApp(
                    SOL_RPC_WS,
                    on_open=_ws_on_open,
                    on_message=_ws_on_message,
                    on_error=_ws_on_error,
                    on_close=_ws_on_close,
                )
                ws.run_forever(ping_interval=30, ping_timeout=10)
            except Exception as e:
                log(f"[SCANNER-WS] exception: {e}")
            time.sleep(5)  # reconectar

    # WebSocket em thread separada
    wst = threading.Thread(target=run_ws, daemon=True, name="ws_scanner")
    wst.start()

    # Fallback HTTP: tenta pump.fun a cada 15s (quando WS inativo ou sem sinais)
    tick = 0
    while True:
        time.sleep(SCAN_INTERVAL)
        tick += 1
        if tick % 5 == 0:
            url = ("https://frontend-api.pump.fun/coins"
                   "?offset=0&limit=20&sort=created_timestamp&order=DESC&includeNsfw=false")
            coins = curl(url, timeout=6)
            if coins and isinstance(coins, list):
                now_ms = time.time() * 1000
                added  = 0
                for c in coins:
                    mint    = c.get("mint", "")
                    created = c.get("created_timestamp", 0)
                    if not mint or not created:
                        continue
                    age_sec = (now_ms - created) / 1000
                    with lock:
                        if mint in seen_mints or age_sec > MAX_AGE_SEC:
                            continue
                        seen_mints.add(mint)
                    try:
                        candidate_q.put_nowait({"mint": mint, "coin": c, "age_sec": round(age_sec, 1)})
                        added += 1
                    except queue.Full:
                        break
                if added:
                    log(f"[SCANNER-HTTP] +{added} novo(s) < {MAX_AGE_SEC}s | pump.fun")
        # Diagnóstico a cada ~60s
        if tick % 20 == 0:
            ws_state = "ATIVO" if _ws_active else "INATIVO"
            log(f"[SCANNER] WS={ws_state} | fila={candidate_q.qsize()} | seen={len(seen_mints)}")


def classifier_worker():
    """Filtra por market cap mínimo e emite sinal de entrada."""
    log("[CLASSIFIER] iniciado — mínimo $3k MC, entra IMEDIATO")
    while True:
        try:
            item = candidate_q.get(timeout=5)
        except queue.Empty:
            continue

        mint    = item["mint"]
        coin    = item["coin"]
        age_sec = item["age_sec"]
        sym     = coin.get("symbol", "?")
        name    = (coin.get("name") or "")[:30]

        # Verifica se já passou da janela de entrada
        created = coin.get("created_timestamp", 0)
        current_age = (time.time() * 1000 - created) / 1000
        if current_age > MAX_AGE_SEC * 3:  # tolerância 3x (processo pode atrasar)
            log(f"[SKIP] {sym} — tarde demais ({current_age:.0f}s)")
            continue

        # 1) Bonding curve on-chain via Solana RPC (instantâneo, sem HTTP externo)
        sol_p     = get_sol_price()
        price, mc = get_bonding_curve_price_rpc(mint, sol_p)
        detail    = None
        from_rpc  = price and price > 0

        if from_rpc:
            # Tenta symbol/name via urllib (best-effort, não bloqueia)
            detail = pumpfun_detail_urllib(mint)
            if detail:
                sym  = detail.get("symbol", sym)
                name = (detail.get("name") or name)[:30]
                mc2  = pumpfun_mc(detail)
                if mc2 > 0:
                    mc = mc2
            log(f"[PREÇO-RPC] {sym} @ ${price:.8f} MC=${mc:,.0f} | bonding curve on-chain")

        # 2) Fallback HTTP: pump.fun curl → urllib → DexScreener, com retry
        if not from_rpc:
            for attempt in range(PRICE_RETRY_MAX + 1):
                detail = pumpfun_detail(mint) or pumpfun_detail_urllib(mint)
                if detail:
                    mc    = pumpfun_mc(detail)
                    price = pumpfun_price(detail, sol_p)
                    sym   = detail.get("symbol", sym)
                    name  = (detail.get("name") or name)[:30]

                if not price or price <= 0 or mc == 0:
                    pair = curl(f"https://api.dexscreener.com/latest/dex/tokens/{mint}", timeout=8)
                    if pair and pair.get("pairs"):
                        sols = [p for p in pair["pairs"] if p.get("chainId") == "solana"]
                        best = max(sols, key=lambda x: float((x.get("liquidity") or {}).get("usd") or 0), default=None)
                        if best:
                            price = float(best.get("priceUsd") or 0)
                            mc    = float(best.get("fdv") or 0) or float((best.get("liquidity") or {}).get("usd") or 0) * 2
                            sym   = (best.get("baseToken") or {}).get("symbol", sym)

                if price and price > 0:
                    break

                created   = coin.get("created_timestamp", 0)
                token_age = (time.time() * 1000 - created) / 1000 if created else 999
                if attempt < PRICE_RETRY_MAX and token_age < MAX_AGE_SEC * 3:
                    log(f"[RETRY {attempt+1}/{PRICE_RETRY_MAX}] {sym} sem preço HTTP — aguarda {PRICE_RETRY_WAIT}s (idade={token_age:.0f}s)")
                    time.sleep(PRICE_RETRY_WAIT)
                    sol_p = get_sol_price()
                else:
                    break

            if not price or price <= 0:
                log(f"[SKIP] {sym} — sem preço após {PRICE_RETRY_MAX} tentativas HTTP")
                continue
            # Filtro MC apenas para preços HTTP (tokens via DexScreener já tiveram actividade)
            if mc < MIN_MC_USD:
                log(f"[SKIP] {sym} — MC=${mc:.0f} < ${MIN_MC_USD} (HTTP)")
                continue

        # Wallet info — pular para tokens RPC frescos (< 15s): somos os primeiros compradores
        wallet_info = []
        whale_count = 0
        if not from_rpc or current_age > 15:
            try:
                pool_addr = mint
                wraw = fetch_wallets(pool_addr, limit=20)
                wallet_info = analyze_wallets(wraw)
                whale_count = sum(1 for w in wallet_info if w["role"] in ("BALEIA", "TRADER"))
            except Exception:
                pass

        # ── Verificar memória: evitar carteiras manipuladoras conhecidas ─────
        known_manip = MEMORY.get("manipulator_wallets", {})
        manip_found = [w["wallet"] for w in wallet_info
                       if w.get("wallet_full", "") in known_manip]
        if manip_found:
            counts = [known_manip[w.get("wallet_full","")]["count"] for w in wallet_info
                      if w.get("wallet_full","") in known_manip]
            max_count = max(counts) if counts else 0
            if max_count >= 2:  # carteira manipulou pelo menos 2x antes
                log(f"[SKIP MANIP] {sym} — {len(manip_found)} carteira(s) manipuladoras conhecidas ({max_count}x)")
                continue

        sig = {
            "mint":         mint,
            "symbol":       sym,
            "name":         name,
            "price":        price,
            "market_cap":   round(mc, 0),
            "age_sec":      round(current_age, 1),
            "sol_price":    sol_p,
            "wallets":      wallet_info[:5],
            "whale_count":  whale_count,
            "pool_address": mint,
            "manip_alert":  len(manip_found) > 0,
        }
        log(f"[SINAL] {sym} ${mc:,.0f} MC | {current_age:.1f}s | preço=${price:.8f} | "
            f"baleias={whale_count} | manip_alert={'SIM' if manip_found else 'NAO'}")
        try:
            signal_q.put_nowait(sig)
        except queue.Full:
            pass


def trader_worker():
    log("[TRADER] iniciado")
    while True:
        try:
            sig = signal_q.get(timeout=5)
        except queue.Empty:
            continue

        with lock:
            if len(positions) >= MAX_POSITIONS:
                continue
            if sig["mint"] in positions:
                continue

        entry  = sig["price"]
        tp     = entry * (1 + TP_PCT)
        sl     = entry * (1 - SL_PCT)
        now_dt = time.strftime("%d/%m %H:%M:%S")
        with lock:
            positions[sig["mint"]] = {
                "symbol":       sig["symbol"],
                "name":         sig.get("name", ""),
                "mint":         sig["mint"],
                "entry_price":  entry,
                "tp_price":     tp,
                "sl_price":     sl,
                "entry_time":   time.time(),
                "open_dt":      now_dt,
                "market_cap":   sig.get("market_cap", 0),
                "age_sec":      sig.get("age_sec", 0),
                "sol_price":    sig.get("sol_price", 150.0),
                "wallets":      sig.get("wallets", []),
                "whale_count":  sig.get("whale_count", 0),
                "pool_address": sig.get("pool_address", ""),
                "break_even":   False,
                "current_price":   entry,
                "current_pnl_pct": 0.0,
                "hold_min":     0.0,
            }
        modo = "PAPER"
        tx_sig = ""
        if LIVE_TRADING and _rt:
            cfg = json.load(open(r"C:\Users\Loja\caca_pump_local\live_wallet.json"))
            # Respeita max_positions do wallet config
            max_live = cfg.get("max_positions", 1)
            with lock:
                if len(positions) >= max_live:
                    continue
            entry_sol_real = cfg.get("entry_sol", 0.02)
            res = _rt.buy(sig["mint"], sol_amount=entry_sol_real)
            if res.get("ok"):
                modo   = "REAL"
                tx_sig = res.get("sig", "")
                with lock:
                    if sig["mint"] in positions:
                        positions[sig["mint"]]["tx_buy"] = tx_sig
                        positions[sig["mint"]]["entry_sol_real"] = entry_sol_real
            else:
                log(f"[REAL-ERRO] buy falhou: {res.get('error')} — modo PAPER")
        log(f"[{modo}] ENTRADA {sig['symbol']} @ ${entry:.8f} | MC=${sig.get('market_cap',0):,.0f} | "
            f"TP +{TP_PCT:.0%} SL -{SL_PCT:.0%} | {sig.get('age_sec',0):.1f}s do launch"
            + (f" | TX={tx_sig[:20]}..." if tx_sig else ""))
        write_json()


def watchdog_worker():
    log("[WATCHDOG] iniciado — verifica SL/TP a cada 10s via pump.fun")
    while True:
        time.sleep(WATCHDOG_INTERVAL)
        with lock:
            mints = list(positions.keys())

        sol_p = get_sol_price()
        for mint in mints:
            detail = pumpfun_detail(mint)
            if not detail:
                continue
            cur = pumpfun_price(detail, sol_p)
            if not cur:
                continue

            with lock:
                if mint not in positions:
                    continue
                pos = positions[mint]

            hold_min = (time.time() - pos["entry_time"]) / 60
            entry    = pos["entry_price"]
            pnl_pct  = (cur - entry) / entry * 100

            # Actualizar estado live
            with lock:
                if mint in positions:
                    positions[mint]["current_price"]   = cur
                    positions[mint]["current_pnl_pct"] = round(pnl_pct, 2)
                    positions[mint]["hold_min"]         = round(hold_min, 1)

            # Break-even trigger
            if not pos["break_even"] and pnl_pct >= BREAK_EVEN_TRIGGER * 100:
                new_sl = entry * (1 + BREAK_EVEN_SL)
                with lock:
                    if mint in positions:
                        positions[mint]["sl_price"]   = new_sl
                        positions[mint]["break_even"] = True
                log(f"[BREAK-EVEN] {pos['symbol']} +{pnl_pct:.1f}% → SL={new_sl:.8f}")

            reason = None
            with lock:
                if mint in positions:
                    p2 = positions[mint]
                    if cur >= p2["tp_price"]:         reason = "TP"
                    elif cur <= p2["sl_price"]:       reason = "SL"
                    elif hold_min >= MAX_HOLD_MIN:    reason = "TEMPO"

            if reason:
                pnl_sol = ENTRY_SOL * (pnl_pct / 100)
                trade = {
                    "symbol":       pos["symbol"],
                    "name":         pos.get("name", ""),
                    "mint":         mint,
                    "entry_price":  entry,
                    "exit_price":   cur,
                    "pnl_pct":      round(pnl_pct, 2),
                    "pnl_sol":      round(pnl_sol, 5),
                    "exit_reason":  reason,
                    "hold_min":     round(hold_min, 1),
                    "open_dt":      pos["open_dt"],
                    "close_dt":     time.strftime("%d/%m %H:%M:%S"),
                    "ts":           int(time.time()),
                    "market_cap":   pos.get("market_cap", 0),
                    "age_sec":      pos.get("age_sec", 0),
                    "wallets":      pos.get("wallets", []),
                    "whale_count":  pos.get("whale_count", 0),
                    "pool_address": pos.get("pool_address", ""),
                }
                with lock:
                    if mint in positions:
                        del positions[mint]
                        closed_trades.append(trade)
                        if len(closed_trades) > 200:
                            closed_trades.pop(0)
                try:
                    with open(TRADES_FILE, "a", encoding="utf-8") as f:
                        f.write(json.dumps(trade) + "\n")
                except Exception:
                    pass

                label = "LUCRO" if pnl_sol > 0 else "PERDA"
                tx_sell = ""
                if LIVE_TRADING and _rt:
                    res_sell = _rt.sell(mint, sell_pct=100.0)
                    if res_sell.get("ok"):
                        tx_sell = res_sell.get("sig", "")
                    else:
                        log(f"[REAL-ERRO] sell falhou: {res_sell.get('error')}")
                modo_s = "REAL" if (LIVE_TRADING and tx_sell) else "PAPER"
                log(f"[{modo_s}] SAIDA {label} {trade['symbol']} {reason} {hold_min:.0f}min | "
                    f"{pnl_pct:+.2f}% | {pnl_sol:+.5f} SOL"
                    + (f" | TX={tx_sell[:20]}..." if tx_sell else ""))

                if reason == "SL":
                    t = threading.Thread(target=post_loss_monitor, args=(trade,), daemon=True)
                    t.start()

                write_json()

        # Status periódico
        if int(time.time()) % 300 < WATCHDOG_INTERVAL:
            with lock:
                n   = len(positions)
                w   = sum(1 for t in closed_trades if t.get("pnl_sol", 0) > 0)
                pnl = sum(t.get("pnl_sol", 0) for t in closed_trades)
            log(f"[STATUS] posições={n} | trades={len(closed_trades)} wins={w} | pnl={pnl:+.5f}SOL")
            write_json()


# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    log("=" * 60)
    log(f"CAÇA PUMP LIVE — {time.strftime('%Y-%m-%d %H:%M:%S')}")
    log(f"Estratégia: entra < {MAX_AGE_SEC}s do lançamento pump.fun")
    log(f"entry={ENTRY_SOL}SOL | TP={TP_PCT:.0%} | SL={SL_PCT:.0%} | max={MAX_HOLD_MIN}min | MC>=${MIN_MC_USD:,}")
    log(f"Scan a cada {SCAN_INTERVAL}s | SOL=${get_sol_price():.0f}")
    if LIVE_TRADING and _rt:
        wi = _rt.wallet_info()
        log(f"MODO: REAL | wallet={wi.get('public_key','?')[:12]}... | saldo={wi.get('sol_balance',0):.4f} SOL | entrada={wi.get('entry_sol',0)} SOL")
    else:
        log("MODO: PAPER (configure live_wallet.json para ativar real)")
    log("=" * 60)
    write_json()

    threads = [
        threading.Thread(target=scanner_worker,    daemon=True, name="scanner"),
        threading.Thread(target=classifier_worker, daemon=True, name="classifier"),
        threading.Thread(target=trader_worker,     daemon=True, name="trader"),
        threading.Thread(target=watchdog_worker,   daemon=True, name="watchdog"),
    ]
    for t in threads:
        t.start()
    try:
        while True:
            time.sleep(30)
            write_json()
    except KeyboardInterrupt:
        log("Parado.")
