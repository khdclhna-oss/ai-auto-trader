CREATE TABLE IF NOT EXISTS portfolio (
    id SERIAL PRIMARY KEY,
    capital NUMERIC(14,2) NOT NULL DEFAULT 100000.00,
    invested NUMERIC(14,2) NOT NULL DEFAULT 0.00,
    cash NUMERIC(14,2) NOT NULL DEFAULT 100000.00,
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
    status VARCHAR(10) CHECK (status IN ('OPEN','CLOSED'))
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

CREATE INDEX IF NOT EXISTS idx_trades_stock ON trades(stock);
CREATE INDEX IF NOT EXISTS idx_trades_status ON trades(status);
CREATE INDEX IF NOT EXISTS idx_equity_time ON equity_snapshots(snapshot_at);
