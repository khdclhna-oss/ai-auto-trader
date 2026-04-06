"""Run V2 database migration against Neon PostgreSQL."""
import os
import psycopg2

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://neondb_owner:npg_ie0GzmROxE9f@ep-proud-bird-an4ydv35-pooler.c-6.us-east-1.aws.neon.tech/neondb?sslmode=require"
)

MIGRATION = """
-- Add new columns to trades
ALTER TABLE trades ADD COLUMN IF NOT EXISTS confluence_score INTEGER;
ALTER TABLE trades ADD COLUMN IF NOT EXISTS regime VARCHAR(20);
ALTER TABLE trades ADD COLUMN IF NOT EXISTS atr_at_entry NUMERIC(12,4);
ALTER TABLE trades ADD COLUMN IF NOT EXISTS trailing_sl NUMERIC(12,2);
ALTER TABLE trades ADD COLUMN IF NOT EXISTS sentiment_score NUMERIC(4,2);

-- Allow 'SIGNAL' status
ALTER TABLE trades DROP CONSTRAINT IF EXISTS trades_status_check;
ALTER TABLE trades ADD CONSTRAINT trades_status_check CHECK (status IN ('OPEN','CLOSED','SIGNAL'));

-- Add pnl/pnl_pct to portfolio if missing
ALTER TABLE portfolio ADD COLUMN IF NOT EXISTS pnl NUMERIC(14,2) NOT NULL DEFAULT 0.00;
ALTER TABLE portfolio ADD COLUMN IF NOT EXISTS pnl_pct NUMERIC(8,4) NOT NULL DEFAULT 0.00;

-- Update existing portfolio row
UPDATE portfolio SET pnl = capital - 100000.00, pnl_pct = ((capital - 100000.00) / 100000.00) * 100
WHERE id = (SELECT id FROM portfolio ORDER BY updated_at DESC LIMIT 1);

-- Signal log
CREATE TABLE IF NOT EXISTS signal_log (
    id SERIAL PRIMARY KEY,
    stock VARCHAR(20),
    regime VARCHAR(20),
    adx NUMERIC(6,2),
    atr NUMERIC(12,4),
    confluence_score INTEGER,
    action VARCHAR(10),
    rsi NUMERIC(6,2),
    ema_trend VARCHAR(10),
    news_sentiment NUMERIC(4,2),
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
                print(f"✓ {stmt[:60]}...")
            except Exception as e:
                print(f"⚠ {stmt[:60]}... → {e}")
                conn.rollback()
                continue
    conn.commit()
    cur.close()
    conn.close()
    print("\n✅ V2 migration complete!")
