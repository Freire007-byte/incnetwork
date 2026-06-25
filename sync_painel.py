#!/usr/bin/env python3
"""Sincroniza JSONs do dashboard para GitHub Pages a cada 60s."""
import shutil, subprocess, time, os

SRC = "/mnt/c/Users/Loja/btc-hunter/dashboard"
DST = "/mnt/c/Users/Loja/incnetwork_deploy"
FILES = ["data.json", "celeste_data.json", "inc_radar_data.json",
         "caca_pump_live_data.json"]
REPO = DST

def run(cmd, cwd=REPO):
    return subprocess.run(cmd, shell=True, cwd=cwd, capture_output=True, text=True)

print("[SYNC] iniciado — push a cada 60s", flush=True)

while True:
    changed = False
    for f in FILES:
        src = f"{SRC}/{f}"
        dst = f"{DST}/{f}"
        if os.path.exists(src):
            shutil.copy2(src, dst)
            changed = True

    if changed:
        run("git add data.json celeste_data.json inc_radar_data.json caca_pump_live_data.json 2>/dev/null")
        r = run('git commit -m "sync: painel $(date -u +%H:%M)"')
        if "nothing to commit" not in r.stdout and r.returncode == 0:
            r2 = run("git push origin main")
            ts = time.strftime("%H:%M:%S")
            if r2.returncode == 0:
                print(f"[{ts}] push OK", flush=True)
            else:
                print(f"[{ts}] push ERRO: {r2.stderr[:100]}", flush=True)
        else:
            print(f"[{time.strftime('%H:%M:%S')}] sem alterações", flush=True)

    time.sleep(60)
