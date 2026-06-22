"""
real_trader.py — Executa compras/vendas reais na pump.fun via Solana
Requer: live_wallet.json com private_key_b58 e live_trading: true
Usa: solders (ja instalado), pumpportal.fun/api/trade-local para montar tx
"""
import json, os, ssl, time, urllib.request

_BASE       = os.environ.get("CACA_PUMP_DIR", os.path.dirname(os.path.abspath(__file__)))
WALLET_CFG  = os.path.join(_BASE, "live_wallet.json")
_HELIUS_KEY = "59ba4837-5cbe-473d-9a25-45df57a9be29"
SOLANA_RPC  = f"https://mainnet.helius-rpc.com/?api-key={_HELIUS_KEY}"
PORTAL_URL  = "https://pumpportal.fun/api/trade-local"

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


def _https(url, data=None, headers=None, timeout=12):
    ctx = ssl.create_default_context()
    h   = {"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"}
    if headers:
        h.update(headers)
    method = "POST" if data else "GET"
    req = urllib.request.Request(url, data=data, headers=h, method=method)
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
        body = r.read()
        return r.status, body


def _rpc(method, params):
    payload = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    _, body = _https(SOLANA_RPC, data=payload)
    return json.loads(body)


# ── Carrega wallet ────────────────────────────────────────────────────────────

_wallet_cache = None

def _load_wallet():
    global _wallet_cache
    if not os.path.exists(WALLET_CFG):
        return None
    try:
        with open(WALLET_CFG, encoding="utf-8") as f:
            cfg = json.load(f)
        if not cfg.get("live_trading"):
            return None
        pk_b58 = cfg.get("private_key_b58", "")
        if not pk_b58 or "SUA_PRIVATE_KEY" in pk_b58:
            return None
        return cfg, pk_b58
    except Exception:
        return None


def _get_keypair(pk_b58: str):
    """Carrega Keypair solders a partir da private key base58 do Phantom."""
    from solders.keypair import Keypair
    pk_bytes = _b58dec(pk_b58)
    if len(pk_bytes) == 64:
        return Keypair.from_bytes(pk_bytes)
    elif len(pk_bytes) == 32:
        return Keypair.from_seed(pk_bytes)
    raise ValueError(f"Private key invalida: {len(pk_bytes)} bytes (esperado 32 ou 64)")


# ── Funções publicas ──────────────────────────────────────────────────────────

def is_live() -> bool:
    """True se live_trading=true e wallet configurada."""
    r = _load_wallet()
    return r is not None


def wallet_info() -> dict:
    """Retorna info da wallet (saldo SOL, pubkey, config)."""
    r = _load_wallet()
    if not r:
        if not os.path.exists(WALLET_CFG):
            return {"configured": False, "reason": "live_wallet.json nao existe"}
        try:
            cfg = json.load(open(WALLET_CFG))
            if not cfg.get("live_trading"):
                return {"configured": False, "reason": "live_trading=false no live_wallet.json"}
            if "SUA_PRIVATE_KEY" in cfg.get("private_key_b58", ""):
                return {"configured": False, "reason": "private_key_b58 nao preenchida"}
        except Exception as e:
            return {"configured": False, "reason": str(e)}
        return {"configured": False, "reason": "erro desconhecido"}

    cfg, pk_b58 = r
    try:
        kp     = _get_keypair(pk_b58)
        pub_b58 = str(kp.pubkey())
        sol    = _rpc("getBalance", [pub_b58])["result"]["value"] / 1e9
        return {
            "configured":   True,
            "public_key":   pub_b58,
            "sol_balance":  round(sol, 6),
            "entry_sol":    cfg.get("entry_sol", 0.02),
            "max_positions": cfg.get("max_positions", 2),
            "slippage":     cfg.get("slippage_pct", 15),
            "live_trading": True,
        }
    except Exception as e:
        return {"configured": True, "error": str(e)}


def buy(mint: str, sol_amount: float = None, slippage_pct: int = None) -> dict:
    """
    Compra tokens pump.fun com SOL real.
    Retorna: {"ok": True/False, "sig": "...", "error": "..."}
    """
    r = _load_wallet()
    if not r:
        return {"ok": False, "error": "wallet nao configurada ou live_trading=false"}
    cfg, pk_b58 = r

    entry_sol  = sol_amount or cfg.get("entry_sol", 0.02)
    slippage   = slippage_pct or cfg.get("slippage_pct", 15)
    prio_fee   = cfg.get("priority_fee_sol", 0.00005)

    try:
        kp      = _get_keypair(pk_b58)
        pub_b58 = str(kp.pubkey())

        # 1. Obter tx nao-assinada do pumpportal.fun
        payload = json.dumps({
            "publicKey":   pub_b58,
            "action":      "buy",
            "mint":        mint,
            "amount":      entry_sol,
            "slippage":    slippage,
            "priorityFee": prio_fee,
            "pool":        "pump"
        }).encode()
        status, tx_bytes = _https(PORTAL_URL, data=payload, timeout=12)
        if status != 200:
            return {"ok": False, "error": f"pumpportal HTTP {status}"}
        if not tx_bytes:
            return {"ok": False, "error": "pumpportal retornou resposta vazia"}

        # 2. Assinar com solders
        from solders.transaction import VersionedTransaction
        tx     = VersionedTransaction.from_bytes(tx_bytes)
        signed = VersionedTransaction(tx.message, [kp])

        # 3. Submeter via RPC
        import base64
        tx_b64 = base64.b64encode(bytes(signed)).decode()
        resp = _rpc("sendTransaction", [tx_b64, {
            "encoding":            "base64",
            "skipPreflight":       True,
            "preflightCommitment": "processed",
            "maxRetries":          3,
        }])
        if "error" in resp:
            return {"ok": False, "error": str(resp["error"])}
        sig = resp.get("result", "")
        return {"ok": bool(sig), "sig": sig, "entry_sol": entry_sol, "mint": mint}

    except Exception as e:
        return {"ok": False, "error": str(e)}


def sell(mint: str, sell_pct: float = 100.0, slippage_pct: int = None) -> dict:
    """
    Vende X% dos tokens pump.fun.
    sell_pct: 100 = vende tudo, 50 = vende metade
    Retorna: {"ok": True/False, "sig": "...", "error": "..."}
    """
    r = _load_wallet()
    if not r:
        return {"ok": False, "error": "wallet nao configurada"}
    cfg, pk_b58 = r

    slippage = slippage_pct or cfg.get("slippage_pct", 15)
    prio_fee = cfg.get("priority_fee_sol", 0.00005)

    try:
        kp      = _get_keypair(pk_b58)
        pub_b58 = str(kp.pubkey())

        payload = json.dumps({
            "publicKey":   pub_b58,
            "action":      "sell",
            "mint":        mint,
            "amount":      f"{sell_pct}%",
            "slippage":    slippage,
            "priorityFee": prio_fee,
            "pool":        "pump"
        }).encode()
        status, tx_bytes = _https(PORTAL_URL, data=payload, timeout=12)
        if status != 200:
            return {"ok": False, "error": f"pumpportal HTTP {status}"}
        if not tx_bytes:
            return {"ok": False, "error": "pumpportal retornou resposta vazia"}

        from solders.transaction import VersionedTransaction
        import base64
        tx     = VersionedTransaction.from_bytes(tx_bytes)
        signed = VersionedTransaction(tx.message, [kp])
        tx_b64 = base64.b64encode(bytes(signed)).decode()
        resp = _rpc("sendTransaction", [tx_b64, {
            "encoding":            "base64",
            "skipPreflight":       True,
            "preflightCommitment": "processed",
            "maxRetries":          3,
        }])
        if "error" in resp:
            return {"ok": False, "error": str(resp["error"])}
        sig = resp.get("result", "")
        return {"ok": bool(sig), "sig": sig, "mint": mint, "sell_pct": sell_pct}

    except Exception as e:
        return {"ok": False, "error": str(e)}


# ── CLI rápido para testar ────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    print("=== Caca Pump --- Verificacao da Wallet ===")
    info = wallet_info()
    if info.get("configured"):
        print(f"[OK] Wallet configurada!")
        print(f"  Pubkey:   {info.get('public_key', '?')}")
        print(f"  Saldo:    {info.get('sol_balance', 0):.6f} SOL")
        print(f"  Entrada:  {info.get('entry_sol', '?')} SOL por trade")
        print(f"  Max pos:  {info.get('max_positions', '?')}")
        print(f"  Slippage: {info.get('slippage', '?')}%")
        sol   = info.get("sol_balance", 0)
        entry = info.get("entry_sol", 0.02)
        fee_reserve = 0.005  # ~0.003 SOL por tx (fee + ATA)
        if sol < entry + fee_reserve:
            print(f"\n[AVISO] SALDO INSUFICIENTE: {sol:.4f} SOL < {entry+fee_reserve:.4f} SOL minimo")
            print("  Transfere SOL para esta wallet no Phantom antes de operar")
        else:
            n_trades = int(sol / (entry + fee_reserve))
            print(f"\n  Saldo suficiente para ~{n_trades} operacoes")
    else:
        print(f"[ERRO] Wallet NAO configurada: {info.get('reason', info.get('error', '?'))}")
        print("\nPassos para ativar:")
        print("  1. Abre live_wallet.json nesta pasta")
        print("  2. Cole sua private key do Phantom em private_key_b58")
        print("  3. Muda live_trading para true")
        print("  4. Ajusta entry_sol para o valor que quer arriscar por trade")
