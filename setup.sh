#!/bin/bash
# CACA PUMP -- Setup via Netlify
# Rodar na VPS: curl -s https://incnetwork.netlify.app/setup.sh | bash

BASE="https://raw.githubusercontent.com/Freire007-byte/incnetwork/main"
PYTHON="/root/inc-radar/.venv/bin/python"
if [ ! -f "$PYTHON" ]; then PYTHON=$(which python3); fi

echo "=== CACA PUMP SETUP ==="
echo "Python: $PYTHON"

# Diretorios
mkdir -p /root/caca-pump/{agents,data,logs}
mkdir -p /tmp/inc_study
echo "[OK] Diretorios criados"

# Download agentes
for f in db.py collector.py analyzer.py network.py patterns.py pumpfun.py dashboard_server.py; do
  curl -s "$BASE/agents/$f" -o "/root/caca-pump/agents/$f"
  echo "[OK] $f"
done

curl -s "$BASE/inc-radar/pump_hunter_sim.py" -o "/root/inc-radar/pump_hunter_sim.py"
echo "[OK] pump_hunter_sim.py"

# Inicializa DB
$PYTHON /root/caca-pump/agents/db.py
echo "[OK] Banco de dados"

# Para agentes antigos
pm2 delete cp-collector cp-analyzer cp-network cp-patterns cp-dashboard cp-pumpfun pump-sim 2>/dev/null || true

# Inicia agentes
pm2 start /root/caca-pump/agents/collector.py        --name cp-collector  --interpreter $PYTHON --restart-delay=30000
pm2 start /root/caca-pump/agents/analyzer.py         --name cp-analyzer   --interpreter $PYTHON --restart-delay=30000
pm2 start /root/caca-pump/agents/network.py          --name cp-network    --interpreter $PYTHON --restart-delay=30000
pm2 start /root/caca-pump/agents/patterns.py         --name cp-patterns   --interpreter $PYTHON --restart-delay=30000
pm2 start /root/caca-pump/agents/pumpfun.py          --name cp-pumpfun    --interpreter $PYTHON --restart-delay=60000
pm2 start /root/caca-pump/agents/dashboard_server.py --name cp-dashboard  --interpreter $PYTHON --restart-delay=10000
pm2 start /root/inc-radar/pump_hunter_sim.py         --name pump-sim      --interpreter $PYTHON --restart-delay=60000
pm2 save

echo ""
echo "=== AGENTES ATIVOS ==="
pm2 list --no-color | grep -E "cp-|pump-sim"
echo ""
echo "Dashboard: http://153.75.224.178:8099"
echo "=== PRONTO ==="
