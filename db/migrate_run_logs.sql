-- V2.1 Migration: Run log table for full transparency
-- Tracks every single engine execution with outcome and errors.

CREATE TABLE IF NOT EXISTS run_logs (
    id SERIAL PRIMARY KEY,
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at TIMESTAMPTZ,
    status VARCHAR(20) NOT NULL DEFAULT 'RUNNING'
        CHECK (status IN ('RUNNING', 'SUCCESS', 'MARKET_CLOSED', 'ERROR')),
    market_open BOOLEAN DEFAULT FALSE,
    stocks_scanned INTEGER DEFAULT 0,
    signals_fired INTEGER DEFAULT 0,
    trades_executed INTEGER DEFAULT 0,
    error_message TEXT,
    log_lines TEXT,   -- full stdout captured for debugging
    duration_ms INTEGER
);

CREATE INDEX IF NOT EXISTS idx_run_logs_started ON run_logs(started_at DESC);
