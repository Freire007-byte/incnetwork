#!/usr/bin/env python3
# Agente 3: Mapeia rede de carteiras coordenadas -- roda a cada 2h
import json, time, sys
sys.path.insert(0, "/root/caca-pump/agents")
import db as DB

def log(m):
    t = time.strftime("%H:%M:%S")
    print(f"[{t}] [NETWORK] {m}", flush=True)

def run_analysis():
    conn = DB.get_conn()
    rows = conn.execute("""
        SELECT wallet, COUNT(DISTINCT mint) tc,
               AVG(sol_amount) avg_sol, role
        FROM wallet_appearances
        GROUP BY wallet
        HAVING tc >= 2
        ORDER BY tc DESC
    """).fetchall()

    conn.execute("DELETE FROM wallet_groups")
    gid = 0
    for r in rows:
        gid += 1
        role = r[3] if r[3] else "retail"
        conn.execute(
            "INSERT INTO wallet_groups (wallet,group_id,role,token_count,total_sol) VALUES (?,?,?,?,?)",
            (r[0], gid, role, r[1], round(r[2]*r[1], 3)))
    conn.commit()

    summary = {
        "groups": gid,
        "ts": int(time.time()),
        "ts_str": time.strftime("%Y-%m-%d %H:%M:%S")
    }
    with open("/root/caca-pump/data/network_summary.json", "w") as f:
        json.dump(summary, f, indent=2)

    log(f"{gid} grupos de carteiras coordenadas mapeados")
    conn.close()

if __name__ == "__main__":
    log("Iniciado -- mapeando rede de carteiras")
    while True:
        run_analysis()
        time.sleep(7200)  # a cada 2 horas
