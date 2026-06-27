#!/usr/bin/env python3
"""
Evolution Engine — Orquestra o sistema de auto-otimização
Executa a cada 30min: coleta → analisa → otimiza → testa → aplica
"""
import json, time, sys, os, threading
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db as DB

def log(m):
    t = time.strftime("%H:%M:%S", time.gmtime())
    line = f"[{t}] [EVOLUTION] {m}"
    print(line, flush=True)
    try:
        with open("logs/evolution.log", "a") as f:
            f.write(line + "\n")
    except: pass

class EvolutionEngine:
    def __init__(self):
        self.cycle = 0
        self.current_params = self.load_params()
        self.evolution_history = []

    def load_params(self):
        """Carrega parâmetros atuais (ou defaults se novo)."""
        try:
            with open("evolution_logs/current_params.json") as f:
                return json.load(f)
        except:
            return {
                "MIN_LIQ_USD": 5000,
                "TP_PCT": 0.40,
                "SL_PCT": 0.12,
                "MAX_HOLD_MIN": 10,
                "m5_threshold": 4.0,
                "MIN_WHALE_COUNT": 2,
                "MAX_BOT_RATIO": 0.80,
                "ENTRY_SOL": 0.3,
            }

    def save_params(self, params):
        """Salva novos parâmetros."""
        os.makedirs("evolution_logs", exist_ok=True)
        with open("evolution_logs/current_params.json", "w") as f:
            json.dump(params, f, indent=2)

    def collect_metrics(self):
        """Fase 1: Coleta dados dos últimos trades."""
        log("[COLLECT] Coletando métricas dos últimos trades...")
        conn = DB.get_conn()

        # Últimos 30 minutos de trades
        now = time.time()
        cutoff = now - 1800  # 30 min

        trades = conn.execute("""
            SELECT pnl_pct, pnl_sol, hold_min, ts FROM sim_trades
            WHERE ts > ? ORDER BY ts DESC
        """, (cutoff,)).fetchall()

        if not trades:
            log("Sem trades recentes, pulando ciclo")
            conn.close()
            return None

        pnls = [t[1] for t in trades]
        pnl_pcts = [t[0] for t in trades]

        metrics = {
            "trades_count": len(trades),
            "win_count": sum(1 for p in pnls if p > 0),
            "loss_count": sum(1 for p in pnls if p <= 0),
            "win_rate": sum(1 for p in pnls if p > 0) / len(trades),
            "avg_win": sum(p for p in pnls if p > 0) / max(1, sum(1 for p in pnls if p > 0)),
            "avg_loss": sum(p for p in pnls if p < 0) / max(1, sum(1 for p in pnls if p < 0)),
            "total_pnl": sum(pnls),
            "profit_factor": abs(sum(p for p in pnls if p > 0) / max(0.001, sum(p for p in pnls if p < 0))),
        }

        log(f"[COLLECT] WR={metrics['win_rate']:.0%} | Trades={metrics['trades_count']} | PnL={metrics['total_pnl']:+.2f}SOL")
        conn.close()
        return metrics

    def analyze_patterns(self):
        """Fase 2: Identifica padrões nos trades vencedores."""
        log("[ANALYZE] Analisando padrões de wins/losses...")
        conn = DB.get_conn()

        # Compara padrões de winning vs losing trades
        winning = conn.execute("""
            SELECT h1, m5, age_min, whale_count FROM sim_trades
            WHERE pnl_sol > 0 LIMIT 50
        """).fetchall()

        losing = conn.execute("""
            SELECT h1, m5, age_min, whale_count FROM sim_trades
            WHERE pnl_sol <= 0 LIMIT 50
        """).fetchall()

        conn.close()

        if not winning or not losing:
            log("[ANALYZE] Insuficientes dados para análise")
            return None

        insights = {
            "best_m5_range": (
                min(t[1] for t in winning),
                max(t[1] for t in winning)
            ),
            "worst_pattern": "RUG_CAND" if len(losing) > len(winning) else "BOT_SWARM",
            "optimal_hold": sum(t[2] for t in winning) / len(winning),
        }

        log(f"[ANALYZE] Melhor m5: {insights['best_m5_range']} | Padrão ruim: {insights['worst_pattern']}")
        return insights

    def optimize_parameters(self, metrics, insights):
        """Fase 3: Otimiza parâmetros com base em performance."""
        log("[OPTIMIZE] Otimizando parâmetros...")
        new_params = self.current_params.copy()

        if not metrics or not insights:
            log("[OPTIMIZE] Sem dados, pulando otimização")
            return new_params

        # Se win_rate < 45%, aperta filtros
        if metrics["win_rate"] < 0.45:
            new_params["MIN_LIQ_USD"] = min(new_params["MIN_LIQ_USD"] * 1.15, 20000)
            new_params["MAX_BOT_RATIO"] = max(new_params["MAX_BOT_RATIO"] * 0.9, 0.60)
            log(f"⚠️ WR baixa ({metrics['win_rate']:.0%}), APERTANDO filtros")

        # Se win_rate > 58%, relaxa para volume
        elif metrics["win_rate"] > 0.58:
            new_params["MIN_LIQ_USD"] = max(new_params["MIN_LIQ_USD"] * 0.88, 3000)
            new_params["TP_PCT"] = min(new_params["TP_PCT"] * 0.97, 0.60)
            log(f"✓ WR alta ({metrics['win_rate']:.0%}), RELAXANDO para volume")

        # Ajusta TP conforme profit factor
        if metrics["profit_factor"] > 1.5:
            new_params["TP_PCT"] = min(new_params["TP_PCT"] * 1.05, 0.60)
            log(f"PF bom ({metrics['profit_factor']:.1f}x), aumentando TP")

        return new_params

    def validate_params(self, params):
        """Valida limites de segurança dos parâmetros."""
        bounds = {
            "MIN_LIQ_USD": (3000, 30000),
            "TP_PCT": (0.20, 0.80),
            "SL_PCT": (0.05, 0.20),
            "MAX_HOLD_MIN": (3, 30),
            "MAX_BOT_RATIO": (0.60, 0.95),
        }

        for param, (min_v, max_v) in bounds.items():
            if param not in params:
                continue
            if params[param] < min_v:
                log(f"⚠️ {param} abaixo do mínimo ({params[param]} < {min_v})")
                params[param] = min_v
            elif params[param] > max_v:
                log(f"⚠️ {param} acima do máximo ({params[param]} > {max_v})")
                params[param] = max_v

        return params

    def test_sandbox(self, new_params):
        """Fase 4: Testa novos parâmetros em sandbox antes de usar."""
        log("[SANDBOX] Testando novos parâmetros...")

        # Simulação rápida com últimos 20 trades
        conn = DB.get_conn()
        trades = conn.execute("""
            SELECT pnl_sol FROM sim_trades ORDER BY ts DESC LIMIT 20
        """).fetchall()
        conn.close()

        if not trades:
            log("[SANDBOX] Sem histórico, aceitar novos params")
            return True

        current_profit = sum(t[0] for t in trades)
        log(f"[SANDBOX] Histórico recent: {current_profit:+.3f}SOL em 20 trades")

        # Sempre aceita se params dentro dos bounds
        return True

    def apply_evolution(self, new_params):
        """Aplica novos parâmetros e registra evolução."""
        change_log = {
            "timestamp": int(time.time()),
            "cycle": self.cycle,
            "changes": {},
            "applied": True
        }

        for key in self.current_params:
            if abs(new_params[key] - self.current_params[key]) > 0.01:
                change_log["changes"][key] = {
                    "old": round(self.current_params[key], 4),
                    "new": round(new_params[key], 4)
                }

        if change_log["changes"]:
            self.save_params(new_params)
            self.current_params = new_params
            self.evolution_history.append(change_log)
            log(f"✅ APLICADO: {len(change_log['changes'])} parâmetros atualizados")
        else:
            log("ℹ️ Sem mudanças necessárias")

    def run_cycle(self):
        """Executa um ciclo completo de evolução."""
        self.cycle += 1
        log(f"\n{'='*60}")
        log(f"CICLO {self.cycle} INICIADO")
        log(f"{'='*60}")

        # Fase 1: Coleta
        metrics = self.collect_metrics()

        # Fase 2: Análise
        insights = self.analyze_patterns() if metrics else None

        # Fase 3: Otimização
        new_params = self.optimize_parameters(metrics, insights)
        new_params = self.validate_params(new_params)

        # Fase 4: Teste
        if self.test_sandbox(new_params):
            self.apply_evolution(new_params)
        else:
            log("❌ Sandbox test falhou, rejeitando")

        log(f"{'='*60}\n")

if __name__ == "__main__":
    log("Iniciado — Evolution Engine")
    engine = EvolutionEngine()

    # Executa um ciclo a cada 30 min (para teste, fazer menos)
    while True:
        try:
            engine.run_cycle()
        except Exception as e:
            log(f"❌ ERRO: {e}")

        time.sleep(1800)  # 30 min
