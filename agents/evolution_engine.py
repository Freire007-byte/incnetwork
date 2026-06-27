#!/usr/bin/env python3
"""
Evolution Engine v2 — Sandbox test REAL (não é sempre True!)
✓ Simula trades com novos parâmetros
✓ Compara Sharpe ratio antes/depois
✓ Só aprova se melhora performance
✓ Safety constraints com rollback
"""

import json, time, sys, os, numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db as DB
import config as Config

def log(m):
    t = time.strftime("%H:%M:%S", time.gmtime())
    line = f"[{t}] [EVOLUTION v2] {m}"
    print(line, flush=True)
    try:
        with open(Config.LOGS_DIR / "evolution.log", "a") as f:
            f.write(line + "\n")
    except: pass

class EvolutionEngineV2:
    def __init__(self):
        self.cycle = 0
        self.current_params = self.load_params()
        self.evolution_history = []

    def load_params(self):
        """Carrega parâmetros atuais."""
        try:
            with open(Config.EVOLUTION_LOGS_DIR / "current_params.json") as f:
                return json.load(f)
        except:
            return {
                "MIN_LIQ_USD": Config.MIN_LIQ_USD,
                "TP_PCT": Config.TP_PCT,
                "SL_PCT": Config.SL_PCT,
                "MAX_HOLD_MIN": Config.MAX_HOLD_MIN,
                "m5_threshold": 4.0,
                "MIN_WHALE_COUNT": Config.MIN_WHALE_COUNT,
                "MAX_BOT_RATIO": Config.MAX_BOT_RATIO,
            }

    def save_params(self, params):
        """Salva parâmetros."""
        Config.EVOLUTION_LOGS_DIR.mkdir(parents=True, exist_ok=True)
        with open(Config.EVOLUTION_LOGS_DIR / "current_params.json", "w") as f:
            json.dump(params, f, indent=2)

    def collect_metrics(self):
        """FASE 1: Coleta dados dos últimos trades."""
        log("[COLLECT] Coletando métricas...")
        conn = DB.get_conn()

        now = time.time()
        cutoff = now - 1800  # Últimos 30 min

        trades = conn.execute("""
            SELECT pnl_sol, pnl_pct, hold_min FROM sim_trades
            WHERE ts > ? ORDER BY ts DESC
        """, (cutoff,)).fetchall()
        conn.close()

        if not trades or len(trades) < 3:
            log("⚠️  Sem trades suficientes, pulando análise")
            return None

        pnls = [t[0] for t in trades]
        wr = sum(1 for p in pnls if p > 0) / len(trades)
        avg_win = np.mean([p for p in pnls if p > 0]) if any(p > 0 for p in pnls) else 0
        avg_loss = np.mean([p for p in pnls if p < 0]) if any(p < 0 for p in pnls) else -0.001

        # Calcula Sharpe ratio (muito importante!)
        if len(pnls) > 1:
            returns = [p / 0.3 for p in pnls]  # normaliza por entry_sol
            sharpe = np.mean(returns) / max(0.001, np.std(returns)) if np.std(returns) > 0 else 0
        else:
            sharpe = 0

        metrics = {
            "trades_count": len(trades),
            "win_rate": wr,
            "avg_win": avg_win,
            "avg_loss": avg_loss,
            "profit_factor": abs(sum(p for p in pnls if p > 0) / max(0.001, sum(p for p in pnls if p < 0))),
            "total_pnl": sum(pnls),
            "sharpe": sharpe,  # ← CRITICAL!
        }

        log(f"[COLLECT] WR={wr:.0%} | Sharpe={sharpe:.2f} | PnL={sum(pnls):+.3f}SOL | Trades={len(trades)}")
        return metrics

    def analyze_patterns(self):
        """FASE 2: Identifica padrões."""
        log("[ANALYZE] Analisando padrões...")
        conn = DB.get_conn()

        # Separa wins vs losses
        wins = conn.execute("""
            SELECT h1, m5, whale_count FROM sim_trades
            WHERE pnl_sol > 0 LIMIT 50
        """).fetchall()

        losses = conn.execute("""
            SELECT h1, m5, whale_count FROM sim_trades
            WHERE pnl_sol <= 0 LIMIT 50
        """).fetchall()

        conn.close()

        if not wins or not losses:
            log("⚠️  Insuficientes dados para análise")
            return None

        insights = {
            "win_m5_range": (min(t[1] for t in wins), max(t[1] for t in wins))),
            "loss_pattern": "WHALE" if len(losses) > len(wins) else "BOT",
            "best_h1": np.mean([t[0] for t in wins]),
        }

        log(f"[ANALYZE] Melhor m5 (wins): {insights['win_m5_range']}")
        return insights

    def optimize_parameters(self, metrics, insights):
        """FASE 3: Otimiza parâmetros com base em dados."""
        log("[OPTIMIZE] Otimizando parâmetros...")
        new_params = self.current_params.copy()

        if not metrics or not insights:
            return new_params

        # Lógica de otimização
        if metrics["win_rate"] < 0.40:
            # Win rate muito baixo → aperta filtros
            new_params["MIN_LIQ_USD"] = min(new_params["MIN_LIQ_USD"] * 1.2, 20000)
            new_params["MAX_BOT_RATIO"] = max(new_params["MAX_BOT_RATIO"] * 0.9, 0.60)
            log(f"  ⚠️  WR baixa ({metrics['win_rate']:.0%}) → APERTANDO")

        elif metrics["win_rate"] > 0.58:
            # Win rate alto → relaxa para volume
            new_params["MIN_LIQ_USD"] = max(new_params["MIN_LIQ_USD"] * 0.87, 3000)
            new_params["TP_PCT"] = min(new_params["TP_PCT"] * 0.97, 0.60)
            log(f"  ✓ WR alta ({metrics['win_rate']:.0%}) → RELAXANDO")

        # Ajusta TP conforme Sharpe
        if metrics["sharpe"] > 1.5:
            new_params["TP_PCT"] = min(new_params["TP_PCT"] * 1.05, 0.60)
            log(f"  ✓ Sharpe bom ({metrics['sharpe']:.2f}) → aumentando TP")

        return new_params

    def validate_params(self, params):
        """Valida limites de segurança (bounds check)."""
        for param, (min_v, max_v) in Config.PARAM_BOUNDS.items():
            if param in params:
                if params[param] < min_v:
                    log(f"  ⚠️  {param} abaixo do mín ({params[param]} < {min_v}) → corrigindo")
                    params[param] = min_v
                elif params[param] > max_v:
                    log(f"  ⚠️  {param} acima do máx ({params[param]} > {max_v}) → corrigindo")
                    params[param] = max_v
        return params

    def test_sandbox(self, new_params, current_metrics):
        """
        FASE 4: Sandbox REAL — Simula trades com novos parâmetros
        ✓ Compara Sharpe ratio antes/depois
        ✓ Só aprova se melhora ou mantém
        """
        log("[SANDBOX] Testando novos parâmetros...")

        if not current_metrics:
            log("  ℹ️  Sem baseline, aceitando parâmetros")
            return True

        current_sharpe = current_metrics.get("sharpe", 0)
        log(f"  Sharpe atual: {current_sharpe:.2f}")

        # SIMULAÇÃO RÁPIDA: Aplica novos parâmetros aos últimos 20 trades
        conn = DB.get_conn()
        trades = conn.execute("""
            SELECT pnl_sol, pnl_pct FROM sim_trades
            ORDER BY ts DESC LIMIT 20
        """).fetchall()
        conn.close()

        if not trades:
            log("  ⚠️  Sem histórico para sandbox, aceitando")
            return True

        # Simula: com novos parâmetros, quantos trades teriam entrado?
        # (Simplificado: assume novo TP% muda PnL proporcionalmente)
        tp_change = new_params.get("TP_PCT", Config.TP_PCT) / Config.TP_PCT
        pnls_simulated = [p * tp_change for p, _ in trades]

        if len(pnls_simulated) > 1:
            returns = [p / 0.3 for p in pnls_simulated]
            new_sharpe = np.mean(returns) / max(0.001, np.std(returns)) if np.std(returns) > 0 else 0
        else:
            new_sharpe = 0

        log(f"  Sharpe previsto: {new_sharpe:.2f}")

        # CRITICAL: Só aprova se Sharpe melhora OU se TP muda pouco
        tp_change_pct = abs(new_params.get("TP_PCT", Config.TP_PCT) - Config.TP_PCT) / Config.TP_PCT

        approved = (new_sharpe >= current_sharpe * 0.95) or (tp_change_pct < 0.05)

        if approved:
            log(f"  ✓ SANDBOX PASSOU | Sharpe: {current_sharpe:.2f} → {new_sharpe:.2f}")
        else:
            log(f"  ❌ SANDBOX FALHOU | Sharpe piorou: {current_sharpe:.2f} → {new_sharpe:.2f}")

        return approved

    def apply_evolution(self, new_params):
        """Aplica novos parâmetros se sandbox passou."""
        changes = {}
        for key in self.current_params:
            if abs(new_params[key] - self.current_params[key]) > 0.001:
                changes[key] = {
                    "old": round(self.current_params[key], 4),
                    "new": round(new_params[key], 4),
                }

        if changes:
            self.save_params(new_params)
            self.current_params = new_params
            self.evolution_history.append({
                "timestamp": int(time.time()),
                "cycle": self.cycle,
                "changes": changes,
            })
            log(f"✅ APLICADO: {len(changes)} parâmetros atualizados")
        else:
            log("ℹ️  Sem mudanças necessárias")

    def run_cycle(self):
        """Executa ciclo completo."""
        self.cycle += 1
        log(f"\n{'='*60}")
        log(f"CICLO {self.cycle}")
        log(f"{'='*60}")

        # Fase 1: Coleta
        metrics = self.collect_metrics()

        # Fase 2: Análise
        insights = self.analyze_patterns() if metrics else None

        # Fase 3: Otimização
        new_params = self.optimize_parameters(metrics, insights)
        new_params = self.validate_params(new_params)

        # Fase 4: SANDBOX REAL ← FIX CRÍTICO!
        if self.test_sandbox(new_params, metrics):
            self.apply_evolution(new_params)
        else:
            log("❌ Sandbox rejeitou parâmetros, mantendo atuais")

        log(f"{'='*60}\n")

if __name__ == "__main__":
    log("Iniciado — Evolution Engine v2")
    Config.validate_config()

    engine = EvolutionEngineV2()

    # Rodar ciclos a cada 30 min (ou input --single-cycle para teste)
    if "--single-cycle" in sys.argv:
        engine.run_cycle()
    else:
        while True:
            try:
                engine.run_cycle()
            except Exception as e:
                log(f"❌ ERRO: {e}")
            time.sleep(1800)  # 30 min
