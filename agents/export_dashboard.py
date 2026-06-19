#!/usr/bin/env python3
"""Exporta dados do banco para JSON -- usado pelo dashboard estatico no GitHub Pages"""
import sqlite3, json, time, os

DB_PATH = os.environ.get("DB_PATH", "data/caca_pump.db")
OUT = "dashboard_data.json"
SIM_TRADES = os.environ.get("SIM_TRADES", "/tmp/inc_study/sim_trades.jsonl")
SIM_LOG    = os.environ.get("SIM_LOG", "/tmp/inc_study/sim_results.txt")

def export():
    data = {"updated_at": time.strftime("%Y-%m-%d %H:%M:%S UTC"), "tokens": [], "patterns": {}, "wallets": [], "sim": {}}

    try:
        conn = sqlite3.connect(DB_PATH, timeout=5)
        conn.row_factory = sqlite3.Row

        # Tokens
        rows = conn.execute("""
            SELECT t.mint, t.symbol, t.name, t.market_cap, t.vol_24h, t.liq_usd,
                   p.pattern_id, p.whale_count, p.bot_count, p.retail_count, p.sol_5min, p.bot_ratio
            FROM tokens t
            LEFT JOIN token_patterns p ON p.mint = t.mint
            WHERE p.pattern_id >= 0
            ORDER BY t.collected_at DESC LIMIT 100
        """).fetchall()
        data["tokens"] = [dict(r) for r in rows]

        # Padroes
        pats = conn.execute("SELECT pattern_id, COUNT(*) as cnt FROM token_patterns WHERE pattern_id >= 0 GROUP BY pattern_id").fetchall()
        names = {0:"BALEIA_FORTE",1:"BOT_SWARM",2:"LENTO_WHALE",3:"EXPLOSIVO",4:"ORGANIC",5:"RUG_CAND",6:"MISTO"}
        data["patterns"] = {names.get(r["pattern_id"], str(r["pattern_id"])): r["cnt"] for r in pats}

        # Carteiras top
        wallets = conn.execute("""
            SELECT wallet, role, COUNT(DISTINCT mint) as tokens, SUM(sol_amount) as total_sol
            FROM wallet_appearances GROUP BY wallet, role
            ORDER BY total_sol DESC LIMIT 50
        """).fetchall()
        data["wallets"] = [dict(r) for r in wallets]
        data["total_tokens"] = conn.execute("SELECT COUNT(*) FROM tokens").fetchone()[0]
        data["total_wallets"] = conn.execute("SELECT COUNT(DISTINCT wallet) FROM wallet_appearances").fetchone()[0]
        conn.close()
    except Exception as e:
        data["db_error"] = str(e)

    # Simulacao
    trades = []
    try:
        if os.path.exists(SIM_TRADES):
            with open(SIM_TRADES) as f:
                for line in f:
                    try: trades.append(json.loads(line.strip()))
                    except: pass
    except: pass

    wins   = sum(1 for t in trades if t.get("pnl_sol", 0) > 0)
    losses = sum(1 for t in trades if t.get("pnl_sol", 0) <= 0)
    pnl    = sum(t.get("pnl_sol", 0) for t in trades)
    data["sim"] = {"trades": len(trades), "wins": wins, "losses": losses,
                   "pnl_sol": round(pnl, 5), "entry_sol": 10.0,
                   "last_trades": trades[-10:]}

    # Log da sim
    try:
        if os.path.exists(SIM_LOG):
            with open(SIM_LOG) as f:
                lines = f.readlines()
            data["sim"]["log_tail"] = lines[-20:]
    except: pass

    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, default=str)
    print(f"[OK] dashboard_data.json exportado: {len(data['tokens'])} tokens, {data['total_wallets']} carteiras")

if __name__ == "__main__":
    export()
