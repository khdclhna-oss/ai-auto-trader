-- QuantumTrader V2 Schema
-- =======================
-- Enhanced tables with regime, confluence, ATR tracking, and signal logging.

CREATE TABLE IF NOT EXISTS portfolio (
    id SERIAL PRIMARY KEY,
    capital NUMERIC(14,2) NOT NULL DEFAULT 100000.00,
    invested NUMERIC(14,2) NOT NULL DEFAULT 0.00,
    cash NUMERIC(14,2) NOT NULL DEFAULT 100000.00,
    pnl NUMERIC(14,2) NOT NULL DEFAULT 0.00,
    pnl_pct NUMERIC(8,4) NOT NULL DEFAULT 0.00,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS trades (
    id SERIAL PRIMARY KEY,
    stock VARCHAR(20),
    action VARCHAR(10) CHECK (action IN ('BUY','SELL','HOLD')),
    entry_price NUMERIC(12,2),
    exit_price NUMERIC(12,2),
    quantity INTEGER,
    pnl NUMERIC(12,2),
    reason TEXT,
    entry_time TIMESTAMPTZ,
    exit_time TIMESTAMPTZ,
    status VARCHAR(10) CHECK (status IN ('OPEN','CLOSED','SIGNAL')),
    -- V2 columns
    confluence_score INTEGER,
    regime VARCHAR(20),
    atr_at_entry NUMERIC(12,4),
    trailing_sl NUMERIC(12,2),
    sentiment_score NUMERIC(4,2)
);

CREATE TABLE IF NOT EXISTS open_positions (
    id SERIAL PRIMARY KEY,
    stock VARCHAR(20) UNIQUE,
    quantity INTEGER,
    entry_price NUMERIC(12,2),
    stop_loss NUMERIC(12,2),
    target NUMERIC(12,2),
    entry_time TIMESTAMPTZ,
    reason TEXT
);

CREATE TABLE IF NOT EXISTS equity_snapshots (
    id SERIAL PRIMARY KEY,
    capital NUMERIC(14,2),
    cash NUMERIC(14,2),
    invested NUMERIC(14,2),
    snapshot_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- V2: Signal log for analytics
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

-- V2: Backtest results (for Phase 3)
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

-- Indexes
CREATE INDEX IF NOT EXISTS idx_trades_stock ON trades(stock);
CREATE INDEX IF NOT EXISTS idx_trades_status ON trades(status);
CREATE INDEX IF NOT EXISTS idx_equity_time ON equity_snapshots(snapshot_at);
CREATE INDEX IF NOT EXISTS idx_signal_log_stock ON signal_log(stock);
CREATE INDEX IF NOT EXISTS idx_signal_log_time ON signal_log(logged_at);
