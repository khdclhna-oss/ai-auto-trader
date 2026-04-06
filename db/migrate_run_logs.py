"""Run run_logs migration."""
import os, psycopg2

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://neondb_owner:npg_ie0GzmROxE9f@ep-proud-bird-an4ydv35-pooler.c-6.us-east-1.aws.neon.tech/neondb?sslmode=require"
)

SQL = """
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
    log_lines TEXT,
    duration_ms INTEGER
);
CREATE INDEX IF NOT EXISTS idx_run_logs_started ON run_logs(started_at DESC);
"""

conn = psycopg2.connect(DATABASE_URL)
cur = conn.cursor()
cur.execute(SQL)
conn.commit()
cur.close(); conn.close()
print("✅ run_logs table created.")
