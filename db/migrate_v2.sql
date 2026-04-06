-- V2 Migration: Run this on the existing Neon database
-- Adds new columns and tables without destroying existing data.

-- Add new columns to trades
ALTER TABLE trades ADD COLUMN IF NOT EXISTS confluence_score INTEGER;
ALTER TABLE trades ADD COLUMN IF NOT EXISTS regime VARCHAR(20);
ALTER TABLE trades ADD COLUMN IF NOT EXISTS atr_at_entry NUMERIC(12,4);
ALTER TABLE trades ADD COLUMN IF NOT EXISTS trailing_sl NUMERIC(12,2);
ALTER TABLE trades ADD COLUMN IF NOT EXISTS sentiment_score NUMERIC(4,2);

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
