import os
import psycopg2
import psycopg2.extras
from typing import List, Dict, Optional, Any

def get_connection():
    """Get a database connection, failing loudly if not configured."""
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        raise ValueError("DATABASE_URL environment variable is not set. Cannot connect to database.")
    return psycopg2.connect(db_url)

class Database:
    """Repository layer for QuantumTrader. Encapsulates all SQL."""

    def __init__(self, conn=None):
        self.conn = conn or get_connection()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.conn.close()

    def get_portfolio(self) -> Dict[str, Any]:
        """Fetch current portfolio state."""
        with self.conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
            cur.execute("SELECT capital, cash, invested, pnl, pnl_pct FROM portfolio ORDER BY updated_at DESC LIMIT 1")
            row = cur.fetchone()
            if not row:
                raise ValueError("Portfolio table is empty. Did you run the migration?")
            return dict(row)

    def log_signal(
        self,
        stock: str,
        action: str,
        price: float,
        reason: str,
        confluence_score: int,
        regime: str,
        atr: float,
        sentiment_score: float,
        effective_score: Optional[int] = None,
        setup_type: Optional[str] = None,
        volume_ratio: Optional[float] = None,
        skipped: Optional[bool] = False,
    ):
        """Log an actionable or non-actionable signal for full signal auditing."""
        try:
            with self.conn.cursor() as cur:
                cur.execute("""
                    ALTER TABLE signal_log 
                    ADD COLUMN IF NOT EXISTS effective_score INT,
                    ADD COLUMN IF NOT EXISTS setup_type TEXT,
                    ADD COLUMN IF NOT EXISTS volume_ratio NUMERIC,
                    ADD COLUMN IF NOT EXISTS skipped BOOLEAN;
                """)
                cur.execute("""
                    INSERT INTO signal_log (
                        stock, action, price, reason, confluence_score, regime, atr, sentiment_score,
                        effective_score, setup_type, volume_ratio, skipped, logged_at
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
                """, (
                    stock, action, price, reason, confluence_score, regime, atr, sentiment_score,
                    effective_score, setup_type, volume_ratio, skipped
                ))
            self.conn.commit()
        except Exception as e:
            self.conn.rollback()
            raise RuntimeError(f"Failed to log signal for {stock}: {e}")

    def get_open_positions(self) -> List[Dict[str, Any]]:
        """Fetch all open positions with V4.1 multi-target support."""
        with self.conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
            cur.execute("""
                SELECT stock, quantity, original_quantity, entry_price, stop_loss, 
                       target_1, target_2, target_3, tranches_exited 
                FROM open_positions
            """)
            return [dict(r) for r in cur.fetchall()]

    def execute_buy(self, stock: str, quantity: int, price: float, stop_loss: float, 
                    t1: float, t2: float, t3: float,
                    reason: str, confluence_score: int, regime: str, atr: float, sentiment_score: float):
        """Atomically execute a V4.1 BUY: initialize tranches and targets."""
        cost = price * quantity
        try:
            with self.conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO open_positions (stock, quantity, original_quantity, entry_price, stop_loss, target_1, target_2, target_3, entry_time, reason)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW(), %s)
                """, (stock, quantity, quantity, price, stop_loss, t1, t2, t3, reason))

                cur.execute("""
                    INSERT INTO trades (stock, action, entry_price, quantity, original_quantity, reason, entry_time, status, confluence_score, regime, atr_at_entry, sentiment_score)
                    VALUES (%s, 'BUY', %s, %s, %s, %s, NOW(), 'OPEN', %s, %s, %s, %s)
                """, (stock, price, quantity, quantity, reason, confluence_score, regime, atr, sentiment_score))

                cur.execute("UPDATE portfolio SET cash = cash - %s, invested = invested + %s, updated_at = NOW()", (cost, cost))
            self.conn.commit()
        except Exception as e:
            self.conn.rollback()
            raise RuntimeError(f"Failed to execute BUY for {stock}: {e}")

    def execute_partial_sell(self, stock: str, qty_to_sell: int, entry_price: float, exit_price: float, net_pnl: float, charges: float, tranche_num: int):
        """Atomically execute a PARTIAL SELL (Tranche exit)."""
        proceeds = (exit_price * qty_to_sell) - charges
        try:
            with self.conn.cursor() as cur:
                # 1. Update the open position: reduce quantity, increment tranches_exited
                cur.execute("""
                    UPDATE open_positions 
                    SET quantity = quantity - %s, tranches_exited = %s
                    WHERE stock = %s
                """, (qty_to_sell, tranche_num, stock))

                # 2. Log the partial exit in a new 'tranche_exits' table (or just update the trade log)
                # For simplicity in V4.1, we update the master trade's current quantity and realized PnL
                cur.execute("""
                    UPDATE trades 
                    SET quantity = quantity - %s, 
                        pnl = COALESCE(pnl, 0) + %s, 
                        charges = COALESCE(charges, 0) + %s
                    WHERE stock = %s AND status = 'OPEN'
                """, (qty_to_sell, net_pnl, charges, stock))

                # 3. Update portfolio
                cur.execute("""
                    UPDATE portfolio 
                    SET cash = cash + %s, invested = invested - %s, capital = capital + %s, pnl = pnl + %s, updated_at = NOW()
                """, (proceeds, entry_price * qty_to_sell, net_pnl, net_pnl))
            self.conn.commit()
        except Exception as e:
            self.conn.rollback()
            raise RuntimeError(f"Failed to execute PARTIAL SELL for {stock}: {e}")

    def execute_sell(self, stock: str, qty: int, entry_price: float, exit_price: float, net_pnl: float, charges: float):
        """Atomically execute a FULL SELL: delete position, close trade, update portfolio."""
        proceeds = (exit_price * qty) - charges
        try:
            with self.conn.cursor() as cur:
                cur.execute("""
                    UPDATE trades SET exit_price=%s, exit_time=NOW(), pnl=COALESCE(pnl, 0)+%s, status='CLOSED', charges=COALESCE(charges, 0)+%s
                    WHERE stock=%s AND status='OPEN'
                """, (exit_price, net_pnl, charges, stock))

                cur.execute("DELETE FROM open_positions WHERE stock = %s", (stock,))

                cur.execute("""
                    UPDATE portfolio 
                    SET cash = cash + %s, invested = invested - %s, capital = capital + %s, pnl = pnl + %s, updated_at = NOW()
                """, (proceeds, entry_price * qty, net_pnl, net_pnl))
            self.conn.commit()
        except Exception as e:
            self.conn.rollback()
            raise RuntimeError(f"Failed to execute SELL for {stock}: {e}")

    def update_trailing_stop(self, stock: str, new_stop: float):
        """Update the stop loss for an open position."""
        try:
            with self.conn.cursor() as cur:
                cur.execute("UPDATE open_positions SET stop_loss = %s WHERE stock = %s", (new_stop, stock))
                cur.execute("UPDATE trades SET trailing_sl = %s WHERE stock = %s AND status = 'OPEN'", (new_stop, stock))
            self.conn.commit()
        except Exception as e:
            self.conn.rollback()
            raise RuntimeError(f"Failed to update trailing stop for {stock}: {e}")

    def snapshot_equity(self):
        """Record the current portfolio value into equity_snapshots."""
        try:
            with self.conn.cursor() as cur:
                cur.execute("INSERT INTO equity_snapshots (capital, cash, invested) SELECT capital, cash, invested FROM portfolio ORDER BY updated_at DESC LIMIT 1")
            self.conn.commit()
        except Exception as e:
            self.conn.rollback()
            print(f"Failed to snapshot equity: {e}")

    def get_last_loss_time(self, stock: str):
        """Returns the exit_time of the most recent losing trade for this stock, or None."""
        with self.conn.cursor() as cur:
            cur.execute("""
                SELECT exit_time FROM trades
                WHERE stock = %s AND status = 'CLOSED' AND pnl < 0
                ORDER BY exit_time DESC LIMIT 1
            """, (stock,))
            row = cur.fetchone()
            return row[0] if row else None

    def get_held_stocks(self) -> set:
        """Returns a set of symbols currently held."""
        with self.conn.cursor() as cur:
            cur.execute("SELECT stock FROM open_positions")
            return {r[0] for r in cur.fetchall()}

    def get_today_realized_pnl(self) -> float:
        """
        Returns the sum of PnL for all trades closed today (IST).
        Used by the -2R daily loss circuit breaker in trader.py.
        Negative value = net loss today.
        """
        with self.conn.cursor() as cur:
            cur.execute("""
                SELECT COALESCE(SUM(pnl), 0) FROM trades
                WHERE status = 'CLOSED'
                  AND exit_time >= (NOW() AT TIME ZONE 'Asia/Kolkata')::date
            """)
            row = cur.fetchone()
            return float(row[0]) if row else 0.0

    def get_week_realized_pnl(self) -> float:
        """Returns net realized PnL for the current IST trading week."""
        with self.conn.cursor() as cur:
            cur.execute("""
                SELECT COALESCE(SUM(pnl), 0) FROM trades
                WHERE status = 'CLOSED'
                  AND exit_time >= (
                      date_trunc('week', NOW() AT TIME ZONE 'Asia/Kolkata')
                      AT TIME ZONE 'Asia/Kolkata'
                  )
            """)
            row = cur.fetchone()
            return float(row[0]) if row else 0.0

    def count_recent_losses(self, stock: str, days: int = 5) -> int:
        """Count losing exits for a symbol in the last N calendar days."""
        with self.conn.cursor() as cur:
            cur.execute("""
                SELECT COUNT(*) FROM trades
                WHERE stock = %s
                  AND status = 'CLOSED'
                  AND pnl < 0
                  AND exit_time >= NOW() - (%s || ' days')::interval
            """, (stock, days))
            row = cur.fetchone()
            return int(row[0]) if row else 0

    def get_recent_system_stats(self, limit: int = 20, risk_amount: Optional[float] = None) -> Dict[str, Any]:
        """Return compact performance stats for the most recent closed trades."""
        with self.conn.cursor() as cur:
            cur.execute("""
                SELECT pnl FROM (
                    SELECT pnl, exit_time FROM trades
                    WHERE status = 'CLOSED' AND pnl IS NOT NULL
                    ORDER BY exit_time DESC
                    LIMIT %s
                ) recent
                ORDER BY exit_time ASC
            """, (limit,))
            pnls = [float(r[0]) for r in cur.fetchall()]

        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]
        gross_profit = sum(wins)
        gross_loss = abs(sum(losses))
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else (float("inf") if gross_profit > 0 else 0.0)
        expectancy = sum(pnls) / len(pnls) if pnls else 0.0

        max_loss_streak = 0
        streak = 0
        for pnl in pnls:
            if pnl <= 0:
                streak += 1
                max_loss_streak = max(max_loss_streak, streak)
            else:
                streak = 0

        expectancy_r = expectancy / risk_amount if risk_amount and risk_amount > 0 else 0.0
        return {
            "sample_size": len(pnls),
            "profit_factor": profit_factor,
            "expectancy": expectancy,
            "expectancy_r": expectancy_r,
            "max_loss_streak": max_loss_streak,
        }

    def had_entry_today(self, stock: str) -> bool:
        """
        Returns True if there was any entry (OPEN or CLOSED) for this stock today (IST).
        Used to enforce max-1-new-entry-per-symbol-per-day rule.
        """
        with self.conn.cursor() as cur:
            cur.execute("""
                SELECT 1 FROM trades
                WHERE stock = %s
                  AND entry_time >= (NOW() AT TIME ZONE 'Asia/Kolkata')::date
                LIMIT 1
            """, (stock,))
            return cur.fetchone() is not None

    def save_macro_state(self, state):
        """Save the latest macro state to the database."""
        try:
            with self.conn.cursor() as cur:
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS macro_state (
                        id SERIAL PRIMARY KEY,
                        tradeable BOOLEAN,
                        nifty_above_200ema BOOLEAN,
                        nifty_50ema_slope_up BOOLEAN,
                        vix NUMERIC(6,2),
                        vix_ok BOOLEAN,
                        breadth_pct NUMERIC(5,2),
                        breadth_ok BOOLEAN,
                        reason TEXT,
                        updated_at TIMESTAMPTZ DEFAULT NOW()
                    )
                """)
                cur.execute("DELETE FROM macro_state")
                cur.execute("""
                    INSERT INTO macro_state (tradeable, nifty_above_200ema, nifty_50ema_slope_up, vix, vix_ok, breadth_pct, breadth_ok, reason, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW())
                """, (state.tradeable, state.nifty_above_200ema, state.nifty_50ema_slope_up, state.vix, state.vix_ok, state.breadth_pct, state.breadth_ok, state.reason))
            self.conn.commit()
        except Exception as e:
            self.conn.rollback()
            print(f"Failed to save macro state: {e}")
