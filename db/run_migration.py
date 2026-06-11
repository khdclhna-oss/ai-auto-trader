"""Run the checked-in database migration against PostgreSQL."""
import os
import psycopg2

DATABASE_URL = os.environ["DATABASE_URL"]

MIGRATION = """
-- Add new columns to trades
ALTER TABLE trades ADD COLUMN IF NOT EXISTS confluence_score INTEGER;
ALTER TABLE trades ADD COLUMN IF NOT EXISTS regime VARCHAR(20);
ALTER TABLE trades ADD COLUMN IF NOT EXISTS atr_at_entry NUMERIC(12,4);
ALTER TABLE trades ADD COLUMN IF NOT EXISTS trailing_sl NUMERIC(12,2);
ALTER TABLE trades ADD COLUMN IF NOT EXISTS sentiment_score NUMERIC(4,2);
ALTER TABLE trades ADD COLUMN IF NOT EXISTS charges NUMERIC(12,4) DEFAULT 0;  -- [P0 FIX] was missing, written on every close
ALTER TABLE trades ADD COLUMN IF NOT EXISTS original_quantity INTEGER;
ALTER TABLE trades ADD COLUMN IF NOT EXISTS exit_type VARCHAR(30);

-- Add V4 tranche columns to open positions
ALTER TABLE open_positions ADD COLUMN IF NOT EXISTS original_quantity INTEGER;
ALTER TABLE open_positions ADD COLUMN IF NOT EXISTS target_1 NUMERIC(12,2);
ALTER TABLE open_positions ADD COLUMN IF NOT EXISTS target_2 NUMERIC(12,2);
ALTER TABLE open_positions ADD COLUMN IF NOT EXISTS target_3 NUMERIC(12,2);
ALTER TABLE open_positions ADD COLUMN IF NOT EXISTS tranches_exited INTEGER NOT NULL DEFAULT 0;

-- Allow 'SIGNAL' status
ALTER TABLE trades DROP CONSTRAINT IF EXISTS trades_status_check;
ALTER TABLE trades ADD CONSTRAINT trades_status_check CHECK (status IN ('OPEN','CLOSED','SIGNAL'));

-- Add pnl/pnl_pct to portfolio if missing
ALTER TABLE portfolio ADD COLUMN IF NOT EXISTS pnl NUMERIC(14,2) NOT NULL DEFAULT 0.00;
ALTER TABLE portfolio ADD COLUMN IF NOT EXISTS pnl_pct NUMERIC(8,4) NOT NULL DEFAULT 0.00;

-- Update existing portfolio row
UPDATE portfolio SET pnl = capital - 100000.00, pnl_pct = ((capital - 100000.00) / 100000.00) * 100
WHERE id = (SELECT id FROM portfolio ORDER BY updated_at DESC LIMIT 1);

-- Signal log (DROP and RECREATE to enforce new schema)
DROP TABLE IF EXISTS signal_log;
CREATE TABLE IF NOT EXISTS signal_log (
    id SERIAL PRIMARY KEY,
    stock VARCHAR(20),
    action VARCHAR(10),
    price NUMERIC(12,2),
    reason TEXT,
    confluence_score INTEGER,
    regime VARCHAR(20),
    atr NUMERIC(12,4),
    sentiment_score NUMERIC(4,2),
    logged_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Backtest results
CREATE TABLE IF NOT EXISTS backtest_results (
    id SERIAL PRIMARY KEY,
    strategy_name VARCHAR(100),
    period_start DATE, period_end DATE,
    total_trades INTEGER, win_rate NUMERIC(5,2),
    profit_factor NUMERIC(8,2), sharpe_ratio NUMERIC(6,3),
    sortino_ratio NUMERIC(6,3), max_drawdown_pct NUMERIC(5,2),
    expectancy NUMERIC(12,2), total_pnl NUMERIC(14,2),
    run_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_signal_log_stock ON signal_log(stock);
CREATE INDEX IF NOT EXISTS idx_signal_log_time ON signal_log(logged_at);
"""

if __name__ == "__main__":
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()
    for stmt in MIGRATION.split(";"):
        stmt = stmt.strip()
        if stmt:
            try:
                cur.execute(stmt)
                print(f"[OK] {stmt[:60]}...")
            except Exception as e:
                print(f"[WARN] {stmt[:60]}... -> {e}")
                conn.rollback()
                continue
    conn.commit()
    cur.close()
    conn.close()
    print("\n[SUCCESS] V2 migration complete!")
