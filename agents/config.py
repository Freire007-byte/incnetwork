#!/usr/bin/env python3
"""
Configuração centralizada — Remove hardcodes, Magic numbers, Secrets
Todas as constantes em um único lugar
"""
import os
from pathlib import Path

# Detecta ambiente (GitHub Actions vs Local)
IS_GITHUB = "GITHUB_WORKSPACE" in os.environ
APP_DATA_DIR = Path(os.environ.get("APP_DATA_DIR", "."))
if IS_GITHUB:
    APP_DATA_DIR = Path(os.environ.get("GITHUB_WORKSPACE", "/root/caca-pump"))

# ─────────────────────────────────────────────────────────
# CAMINHOS (Dinâmicos — não hardcoded)
# ─────────────────────────────────────────────────────────

AGENTS_DIR = APP_DATA_DIR / "agents"
DATA_DIR = APP_DATA_DIR / "data"
LOGS_DIR = APP_DATA_DIR / "logs"
EVOLUTION_LOGS_DIR = APP_DATA_DIR / "evolution_logs"
MODELS_DIR = APP_DATA_DIR / "models"
ARTIFACTS_DIR = APP_DATA_DIR / "artifacts"

# Criar diretórios se não existirem
for d in [DATA_DIR, LOGS_DIR, EVOLUTION_LOGS_DIR, MODELS_DIR, ARTIFACTS_DIR]:
    d.mkdir(parents=True, exist_ok=True)

DB_PATH = DATA_DIR / "caca_pump.db"
TRADES_LOG = DATA_DIR / "sim_trades.jsonl"

# ─────────────────────────────────────────────────────────
# CONSTANTES DE TRADING (Não magic numbers!)
# ─────────────────────────────────────────────────────────

# Limites de posição
ENTRY_SOL = 0.3  # SOL por entrada
MAX_POSITIONS = 3  # Máximo trades simultâneos
MAX_POSITION_SIZE = 10.0  # SOL máximo por posição

# Take profit / Stop loss
TP_PCT = 0.40  # +40% take profit
SL_PCT = 0.12  # -12% stop loss
MAX_HOLD_MIN = 10  # 10 minutos máximo hold
BREAK_EVEN_TRIGGER = 0.12  # Ativa break-even em +12%
BREAK_EVEN_SL = 0.08  # SL sobe para entry * 1.08

# Trailing SL levels
TRAILING_SL_LEVELS = [
    (1.50, 1.35, "+50%→SL+35%"),
    (1.35, 1.20, "+35%→SL+20%"),
    (1.20, 1.08, "+20%→SL+8%"),
]

# ─────────────────────────────────────────────────────────
# FILTROS DE ENTRADA (Dinâmicos via evolution_engine)
# ─────────────────────────────────────────────────────────

MIN_LIQ_USD = 5000  # Liquidez mínima USD
MIN_SOL_5MIN = 0.8  # SOL transacionado em 5 min
MIN_WHALE_COUNT = 2  # Baleias mínimas
MAX_BOT_RATIO = 0.80  # Máximo % de bots (0-1)
MAX_TOKEN_AGE_MIN = 30  # Token mais velho que isto é rejeitado
MIN_BUYSELL_RATIO = 1.5  # Buy/Sell minimum

# Detecção de padrão
WHALE_SOL_MIN = 0.3  # O que é considerado whale (SOL)
BOT_SOL_MAX = 0.005  # O que é considerado bot (SOL)

# ─────────────────────────────────────────────────────────
# APIs e ENDPOINTS
# ─────────────────────────────────────────────────────────

DEXSCREENER_BASE = "https://api.dexscreener.com"
DEXSCREENER_ENDPOINTS = {
    "profiles": f"{DEXSCREENER_BASE}/token-profiles/latest/v1",
    "boosts": f"{DEXSCREENER_BASE}/token-boosts/latest/v1",
    "tokens": f"{DEXSCREENER_BASE}/latest/dex/tokens",
}

SOLANA_RPC = "https://api.mainnet-beta.solana.com"

PUMPFUN_API = "https://frontend-api.pump.fun"

COINGECKO_API = "https://api.coingecko.com/api/v3"

HELIUS_API = "https://api.helius.xyz/v0"
HELIUS_KEY = os.environ.get("HELIUS_KEY", "")  # Sempre do env!

# ─────────────────────────────────────────────────────────
# TIMEOUTS e DELAYS
# ─────────────────────────────────────────────────────────

CURL_TIMEOUT = 12  # segundos
RPC_TIMEOUT = 8  # segundos
DB_TIMEOUT = 30  # segundos
SOCKET_TIMEOUT = 10  # segundos

REQ_DELAY_DEXSCREENER = 0.3  # 300ms entre requests
REQ_DELAY_PUMPFUN = 2.5  # 2.5s entre requests (rate limit)
REQ_DELAY_HELIUS = 1.0  # 1s entre requests

PRICE_FRESHNESS_MAX_SEC = 300  # Preço válido por 5 min
PRICE_CACHE_SEC = 60  # Cache SOL price por 60s

# ─────────────────────────────────────────────────────────
# COLETA DE DADOS
# ─────────────────────────────────────────────────────────

SCAN_DAYS = 7  # Coleta tokens dos últimos 7 dias
COLLECTOR_BATCH_SIZE = 40  # Tokens por ciclo de coleta
ANALYZER_BATCH_SIZE = 6  # Análises por ciclo
PUMPFUN_PAGES = 100  # Máximo páginas pump.fun
PUMPFUN_LIMIT = 100  # Tokens por request

# ─────────────────────────────────────────────────────────
# SIMULAÇÃO
# ─────────────────────────────────────────────────────────

SIM_DURATION_MIN = 300  # 5 horas simulação
CANDIDATE_QUEUE_MAX = 200  # Tokens na fila
SIGNAL_QUEUE_MAX = 10  # Sinais processando

# ─────────────────────────────────────────────────────────
# EVOLUTION (Auto-optimization)
# ─────────────────────────────────────────────────────────

EVOLUTION_CYCLE_SEC = 1800  # 30 min entre ciclos
PARAM_BOUNDS = {
    "MIN_LIQ_USD": (3000, 30000),
    "TP_PCT": (0.20, 0.80),
    "SL_PCT": (0.05, 0.20),
    "MAX_HOLD_MIN": (3, 30),
    "MAX_BOT_RATIO": (0.60, 0.95),
    "MIN_WHALE_COUNT": (1, 5),
}

# ─────────────────────────────────────────────────────────
# SAFETY / GUARDRAILS
# ─────────────────────────────────────────────────────────

MAX_PARAM_CHANGE_PCT = 0.30  # Mudança max 30% por ciclo
ADAPTIVE_MODE_ENABLED = True  # Ajusta filtros com WR
CONSECUTIVE_LOSS_THRESHOLD = 3  # Pausa depois de 3 perdas
PAUSE_AFTER_LOSSES_SEC = 600  # Pausa por 10 min

# Price staleness kill switch
PRICE_STALENESS_KILL_SEC = 300  # Kill trade se preço > 5 min velho

# ─────────────────────────────────────────────────────────
# LOGGING
# ─────────────────────────────────────────────────────────

LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")
LOG_FORMAT = "[%(asctime)s] [%(name)s] [%(levelname)s] %(message)s"
LOG_RETENTION_DAYS = 7

# ─────────────────────────────────────────────────────────
# SECRETS (SEMPRE do environment!)
# ─────────────────────────────────────────────────────────

class SecretsValidator:
    """Valida que secrets vêm do environment, não do código."""

    @staticmethod
    def get_or_fail(env_var, description):
        """Pega env var ou falha com mensagem clara."""
        value = os.environ.get(env_var, "")
        if not value:
            raise ValueError(
                f"❌ ERRO: {description} não configurada.\n"
                f"   Configure com: export {env_var}=seu-valor"
            )
        return value

    @staticmethod
    def get_optional(env_var, description, default=""):
        """Pega env var com default."""
        return os.environ.get(env_var, default)

# Secrets (use SecretsValidator para validar)
WALLET_1_KEY = os.environ.get("WALLET_1_KEY", "")  # Master wallet
WALLET_2_KEY = os.environ.get("WALLET_2_KEY", "")  # Worker 1
WALLET_3_KEY = os.environ.get("WALLET_3_KEY", "")  # Worker 2
HELIUS_KEY = os.environ.get("HELIUS_KEY", "")
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")

# ─────────────────────────────────────────────────────────
# VALIDATION (Não deixa iniciar com config quebrada)
# ─────────────────────────────────────────────────────────

def validate_config():
    """Valida configuração antes de rodar."""
    errors = []

    # Validar diretórios
    if not DATA_DIR.exists():
        errors.append(f"DATA_DIR não existe: {DATA_DIR}")

    # Validar ranges
    if TP_PCT < 0.10 or TP_PCT > 0.90:
        errors.append(f"TP_PCT inválido: {TP_PCT} (deve ser 0.10-0.90)")

    if SL_PCT < 0.02 or SL_PCT > 0.30:
        errors.append(f"SL_PCT inválido: {SL_PCT} (deve ser 0.02-0.30)")

    if MAX_HOLD_MIN < 1 or MAX_HOLD_MIN > 60:
        errors.append(f"MAX_HOLD_MIN inválido: {MAX_HOLD_MIN} (deve ser 1-60)")

    if errors:
        for err in errors:
            print(f"⚠️  {err}")
        return False

    return True

# ─────────────────────────────────────────────────────────
# DEBUG
# ─────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("✓ Config carregado com sucesso")
    print(f"  Data dir: {DATA_DIR}")
    print(f"  DB path: {DB_PATH}")
    print(f"  Logs dir: {LOGS_DIR}")
    print(f"  Entry SOL: {ENTRY_SOL}")
    print(f"  TP: {TP_PCT:.0%}, SL: {SL_PCT:.0%}, Hold: {MAX_HOLD_MIN}min")
    validate_config()
