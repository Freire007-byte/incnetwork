#!/usr/bin/env python3
# Agente 4: Classifica padroes dos pumps -- roda a cada 3h
import json, time, sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db as DB

PATTERN_NAMES = {
    0: "PUMP_BALEIA_FORTE",
    1: "PUMP_BOT_SWARM",
    2: "PUMP_LENTO_WHALE",
    3: "PUMP_EXPLOSIVO",
    4: "ORGANIC_SLOW",
    5: "RUG_CANDIDATO",
    6: "PUMP_MISTO",
}

def log(m):
    print(f"[{time.strftime('%H:%M:%S', time.gmtime())}] [PATTERNS] {m}", flush=True)

def run_analysis():
    conn = DB.get_conn()
    n = conn.execute(
        "SELECT COUNT(*) FROM token_patterns WHERE pattern_id >= 0"
    ).fetchone()[0]

    if n < 5:
        log(f"Poucos tokens ({n}), aguardando mais dados...")
        conn.close()
        return

    total = conn.execute("SELECT COUNT(*) FROM tokens").fetchone()[0]
    report = {"total_analyzed": n, "total_tokens": total,
              "patterns": {}, "ts": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())}

    for pid, pname in PATTERN_NAMES.items():
        row = conn.execute("""
            SELECT COUNT(*), AVG(sol_5min), AVG(whale_count),
                   AVG(duration_min), AVG(bot_ratio)
            FROM token_patterns WHERE pattern_id = ?
        """, (pid,)).fetchone()
        cnt = row[0] or 0
        if cnt == 0: continue
        report["patterns"][str(pid)] = {
            "name": pname,
            "token_count": cnt,
            "pct_of_total": round(cnt / max(1, n) * 100, 1),
            "avg_sol_5min": round(row[1] or 0, 1),
            "avg_whales":   round(row[2] or 0, 1),
            "avg_duration": round(row[3] or 0, 1),
            "avg_bot_ratio": round(row[4] or 0, 3),
        }

    log(f"{n} tokens | {len(report['patterns'])} padroes identificados")
    conn.close()

if __name__ == "__main__":
    log("Iniciado -- classificando padroes de pump")
    while True:
        run_analysis()
        time.sleep(10800)
