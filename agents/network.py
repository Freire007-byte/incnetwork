#!/usr/bin/env python3
# Agente 3: Mapeia rede de carteiras coordenadas -- roda a cada 2h
import json, time, sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db as DB

def log(m):
    print(f"[{time.strftime('%H:%M:%S', time.gmtime())}] [NETWORK] {m}", flush=True)

def run_analysis():
    conn = DB.get_conn()
    rows = conn.execute("""
        SELECT wallet, COUNT(DISTINCT mint) tc,
               SUM(sol_amount) total_sol, role
        FROM wallet_appearances
        GROUP BY wallet
        HAVING tc >= 2
        ORDER BY total_sol DESC
    """).fetchall()

    conn.execute("DELETE FROM wallet_groups")
    gid = 0
    for r in rows:
        gid += 1
        role = r[3] if r[3] else "retail"
        conn.execute(
            "INSERT INTO wallet_groups (wallet,group_id,role,token_count,total_sol) VALUES (?,?,?,?,?)",
            (r[0], gid, role, r[1], round(r[2] or 0, 3)))
    conn.commit()
    log(f"{gid} grupos de carteiras coordenadas mapeados")
    conn.close()

if __name__ == "__main__":
    log("Iniciado -- mapeando rede de carteiras")
    while True:
        run_analysis()
        time.sleep(7200)
