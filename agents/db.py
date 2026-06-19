import os, sqlite3

DB_PATH = os.environ.get("DB_PATH", "data/caca_pump.db")

def get_conn():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_conn()
    conn.execute("""CREATE TABLE IF NOT EXISTS tokens (
        mint TEXT PRIMARY KEY, symbol TEXT, name TEXT,
        created_at INTEGER, market_cap REAL, vol_24h REAL,
        liq_usd REAL, peak_h1 REAL, peak_h6 REAL,
        collected_at INTEGER, source TEXT DEFAULT 'dexscreener'
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS token_txs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        mint TEXT, wallet TEXT, sol_amount REAL,
        tx_type TEXT, role TEXT, ts INTEGER
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS wallet_appearances (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        wallet TEXT, mint TEXT, role TEXT,
        sol_amount REAL, ts INTEGER
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS token_patterns (
        mint TEXT PRIMARY KEY, pattern_id INTEGER DEFAULT -1,
        whale_count INTEGER DEFAULT 0, bot_count INTEGER DEFAULT 0,
        retail_count INTEGER DEFAULT 0, sol_5min REAL DEFAULT 0,
        bot_ratio REAL DEFAULT 0, duration_min REAL DEFAULT 0,
        analyzed_at INTEGER DEFAULT 0
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS wallet_groups (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        wallet TEXT, group_id INTEGER, role TEXT,
        token_count INTEGER, total_sol REAL
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_wa_wallet ON wallet_appearances(wallet)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_wa_mint   ON wallet_appearances(mint)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tp_pid    ON token_patterns(pattern_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tx_mint   ON token_txs(mint)")
    conn.commit()
    conn.close()
    print("DB OK:", DB_PATH)

if __name__ == "__main__":
    init_db()
