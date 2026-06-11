-- V2 Migration: Run this on the existing Neon database
-- Adds new columns and tables without destroying existing data.

-- Add new columns to trades
ALTER TABLE trades ADD COLUMN IF NOT EXISTS confluence_score INTEGER;
ALTER TABLE trades ADD COLUMN IF NOT EXISTS regime VARCHAR(20);
ALTER TABLE trades ADD COLUMN IF NOT EXISTS atr_at_entry NUMERIC(12,4);
ALTER TABLE trades ADD COLUMN IF NOT EXISTS trailing_sl NUMERIC(12,2);
ALTER TABLE trades ADD COLUMN IF NOT EXISTS sentiment_score NUMERIC(4,2);
ALTER TABLE trades ADD COLUMN IF NOT EXISTS charges NUMERIC(12,4) DEFAULT 0;
ALTER TABLE trades ADD COLUMN IF NOT EXISTS original_quantity INTEGER;
ALTER TABLE trades ADD COLUMN IF NOT EXISTS exit_type VARCHAR(30);

-- Add V4 tranche columns to open positions
ALTER TABLE open_positions ADD COLUMN IF NOT EXISTS original_quantity INTEGER;
ALTER TABLE open_positions ADD COLUMN IF NOT EXISTS target_1 NUMERIC(12,2);
ALTER TABLE open_positions ADD COLUMN IF NOT EXISTS target_2 NUMERIC(12,2);
ALTER TABLE open_positions ADD COLUMN IF NOT EXISTS target_3 NUMERIC(12,2);
ALTER TABLE open_positions ADD COLUMN IF NOT EXISTS tranches_exited INTEGER NOT NULL DEFAULT 0;

-- Allow 'SIGNAL' status for logged scans
ALTER TABLE trades DROP CONSTRAINT IF EXISTS trades_status_check;
ALTER TABLE trades ADD CONSTRAINT trades_status_check CHECK (status IN ('OPEN','CLOSED','SIGNAL'));

-- Add pnl/pnl_pct to portfolio if missing
ALTER TABLE portfolio ADD COLUMN IF NOT EXISTS pnl NUMERIC(14,2) NOT NULL DEFAULT 0.00;
ALTER TABLE portfolio ADD COLUMN IF NOT EXISTS pnl_pct NUMERIC(8,4) NOT NULL DEFAULT 0.00;

-- Update existing portfolio row with current pnl
UPDATE portfolio SET pnl = capital - 100000.00, pnl_pct = ((capital - 100000.00) / 100000.00) * 100
WHERE id = (SELECT id FROM portfolio ORDER BY updated_at DESC LIMIT 1);

-- Create signal_log table
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

ALTER TABLE signal_log ADD COLUMN IF NOT EXISTS action VARCHAR(10);
ALTER TABLE signal_log ADD COLUMN IF NOT EXISTS price NUMERIC(12,2);
ALTER TABLE signal_log ADD COLUMN IF NOT EXISTS reason TEXT;
ALTER TABLE signal_log ADD COLUMN IF NOT EXISTS confluence_score INTEGER;
ALTER TABLE signal_log ADD COLUMN IF NOT EXISTS regime VARCHAR(20);
ALTER TABLE signal_log ADD COLUMN IF NOT EXISTS atr NUMERIC(12,4);
ALTER TABLE signal_log ADD COLUMN IF NOT EXISTS sentiment_score NUMERIC(4,2);

-- Create backtest_results table
CREATE TABLE IF NOT EXISTS backtest_results (
    id SERIAL PRIMARY KEY,
    strategy_name VARCHAR(100),
    period_start DATE,
    period_end DATE,
    total_trades INTEGER,
    win_rate NUMERIC(5,2),
    profit_factor NUMERIC(8,2),
    sharpe_ratio NUMERIC(6,3),
    sortino_ratio NUMERIC(6,3),
    max_drawdown_pct NUMERIC(5,2),
    expectancy NUMERIC(12,2),
    total_pnl NUMERIC(14,2),
    run_at TIMESTAMPTZ DEFAULT NOW()
);

-- New indexes
CREATE INDEX IF NOT EXISTS idx_signal_log_stock ON signal_log(stock);
CREATE INDEX IF NOT EXISTS idx_signal_log_time ON signal_log(logged_at);
