"""
QuantumTrader V2 — Orchestrator
=================================
The main trading engine. Coordinates:
  1. Multi-timeframe analysis (daily + hourly + 15m)
  2. Market regime detection (trending / ranging / volatile)
  3. Confluence-based signal generation
  4. ATR-based risk management & position sizing
  5. Trailing stop management on open positions
  6. Signal logging for every scan
"""

import os
import sys
import psycopg2
from datetime import datetime
from multi_timeframe import fetch_multi_timeframe, get_confluence
from regime import detect_regime
from risk_manager import (
    calculate_atr, plan_position, check_trailing_stop, MAX_POSITIONS
)
from news import get_news_sentiment

STOCKS = [
    # Financials
    "HDFCBANK.NS", "ICICIBANK.NS", "KOTAKBANK.NS", "AXISBANK.NS", "SBILIFE.NS",
    # IT
    "TCS.NS", "INFY.NS", "WIPRO.NS", "HCLTECH.NS",
    # Energy & Industrials
    "RELIANCE.NS", "NTPC.NS", "POWERGRID.NS",
    # FMCG
    "HINDUNILVR.NS", "NESTLEIND.NS",
    # Auto
    "MARUTI.NS", "TATAMOTORS.NS",
    # Pharma
    "SUNPHARMA.NS", "DRREDDY.NS",
    # Metals & Telecom
    "TATASTEEL.NS", "BHARTIARTL.NS",
]
DATABASE_URL = os.environ["DATABASE_URL"]
INITIAL_CAPITAL = 100000


def get_conn():
    return psycopg2.connect(DATABASE_URL)


def is_market_open() -> bool:
    """Check if NSE market is currently open (9:15 AM - 3:30 PM IST, Mon-Fri)."""
    from datetime import timezone, timedelta
    ist = timezone(timedelta(hours=5, minutes=30))
    now_ist = datetime.now(ist)
    
    # Weekend check
    if now_ist.weekday() >= 5:  # Saturday=5, Sunday=6
        return False
    
    # Market hours: 9:15 AM to 3:30 PM IST
    market_open = now_ist.replace(hour=9, minute=15, second=0, microsecond=0)
    market_close = now_ist.replace(hour=15, minute=30, second=0, microsecond=0)
    
    return market_open <= now_ist <= market_close


def run():
    conn = get_conn()
    cur = conn.cursor()
    
    from datetime import timezone, timedelta
    ist = timezone(timedelta(hours=5, minutes=30))
    now = datetime.now(ist)
    
    print(f"\n{'='*60}")
    print(f"  QuantumTrader V2 — Market Scan @ {now.strftime('%Y-%m-%d %H:%M:%S')} IST")
    print(f"{'='*60}\n")

    # ─── 0. Market hours guard ────────────────────────────────
    if not is_market_open():
        print(f"  ⏸️  Market is CLOSED. NSE trading hours: 9:15 AM - 3:30 PM IST (Mon-Fri).")
        print(f"  Current time: {now.strftime('%A, %I:%M %p IST')}")
        print(f"  Skipping trade execution. Only logging equity snapshot.\n")
        
        # Still snapshot equity so the chart stays updated
        cur.execute("SELECT cash, invested FROM portfolio ORDER BY updated_at DESC LIMIT 1")
        row = cur.fetchone()
        if row:
            c, i = float(row[0]), float(row[1])
            cur.execute("INSERT INTO equity_snapshots (capital, cash, invested) VALUES (%s, %s, %s)", (c + i, c, i))
        conn.commit()
        cur.close()
        conn.close()
        return

    # ─── 1. Get portfolio state ───────────────────────────────
    cur.execute("SELECT capital, cash, invested FROM portfolio ORDER BY updated_at DESC LIMIT 1")
    row = cur.fetchone()
    capital = float(row[0]) if row else INITIAL_CAPITAL
    cash = float(row[1]) if row else INITIAL_CAPITAL
    invested = float(row[2]) if row else 0

    cur.execute("SELECT COUNT(*) FROM open_positions")
    open_count = cur.fetchone()[0]

    print(f"  Portfolio: ₹{capital:,.0f} | Cash: ₹{cash:,.0f} | Invested: ₹{invested:,.0f}")
    print(f"  Open positions: {open_count}/{MAX_POSITIONS}\n")

    # ─── 2. Process each stock ────────────────────────────────
    for symbol in STOCKS:
        short_name = symbol.replace(".NS", "")
        print(f"  ┌─ Analyzing {short_name} {'─' * (40 - len(short_name))}")

        try:
            # Fetch all timeframes
            frames = fetch_multi_timeframe(symbol)
            df_15 = frames.get("15m")
            df_daily = frames.get("1d")

            if df_15 is None or len(df_15) < 30:
                print(f"  │  ⚠ Insufficient data, skipping")
                print(f"  └{'─' * 50}\n")
                continue

            # Detect market regime from daily data
            regime_result = detect_regime(df_daily if df_daily is not None and len(df_daily) > 50 else df_15)
            print(f"  │  Regime: {regime_result.regime} (ADX: {regime_result.adx:.1f})")

            # Get multi-timeframe confluence
            confluence = get_confluence(symbol, frames, regime_result.regime)
            print(f"  │  Confluence: {confluence.confluence_score:+d} → {confluence.action}")
            for r in confluence.reasons:
                print(f"  │    • {r}")

            # News sentiment
            sentiment = get_news_sentiment(short_name)
            sentiment_str = {1: "Positive", -1: "Negative", 0: "Neutral"}[sentiment]
            print(f"  │  News: {sentiment_str}")

            # Adjust confluence with sentiment
            effective_score = confluence.confluence_score
            if sentiment != 0:
                effective_score += sentiment  # ±1 from news

            # Current price and ATR
            price = float(df_15["close"].iloc[-1])
            atr = calculate_atr(df_15)
            if atr is None:
                atr = price * 0.01  # fallback: 1% of price

            # Build reason string
            reason_parts = confluence.reasons.copy()
            if sentiment > 0:
                reason_parts.append("Positive news +1")
            elif sentiment < 0:
                reason_parts.append("Negative news -1")
            reason_str = " | ".join(reason_parts) + f" → score {effective_score:+d} → {confluence.action}"

            # ─── 3. Execute trade decisions ───────────────
            if confluence.action == "BUY" and open_count < MAX_POSITIONS and cash > 0:
                plan = plan_position(
                    stock=symbol,
                    entry_price=price,
                    atr=atr,
                    capital=capital,
                    regime=regime_result.regime,
                )

                if plan and plan.quantity > 0 and price * plan.quantity <= cash:
                    print(f"  │  🟢 EXECUTING BUY: {plan.quantity} shares @ ₹{price:.2f}")
                    print(f"  │     SL: ₹{plan.stop_loss:.2f} | TP: ₹{plan.target:.2f} | RR: {plan.reward_risk_ratio:.1f}")

                    cur.execute("""
                        INSERT INTO open_positions (stock, quantity, entry_price, stop_loss, target, entry_time, reason)
                        VALUES (%s, %s, %s, %s, %s, NOW(), %s)
                        ON CONFLICT (stock) DO NOTHING
                    """, (symbol, plan.quantity, price, plan.stop_loss, plan.target, reason_str))

                    cur.execute("""
                        INSERT INTO trades (stock, action, entry_price, quantity, reason, entry_time, status,
                                            confluence_score, regime, atr_at_entry, sentiment_score)
                        VALUES (%s, 'BUY', %s, %s, %s, NOW(), 'OPEN', %s, %s, %s, %s)
                    """, (symbol, price, plan.quantity, reason_str,
                          effective_score, regime_result.regime, atr, sentiment))

                    cost = price * plan.quantity
                    cur.execute("""
                        UPDATE portfolio SET cash = cash - %s, invested = invested + %s, updated_at = NOW()
                    """, (cost, cost))
                    open_count += 1
                    cash -= cost
                else:
                    print(f"  │  ⚠ BUY signal but position plan rejected (insufficient funds or bad RR)")

            elif confluence.action == "SELL":
                cur.execute("SELECT quantity, entry_price, stop_loss FROM open_positions WHERE stock = %s", (symbol,))
                pos = cur.fetchone()
                if pos:
                    qty, entry, sl = int(pos[0]), float(pos[1]), float(pos[2])
                    pnl = (price - entry) * qty
                    print(f"  │  🔴 EXECUTING SELL: {qty} shares @ ₹{price:.2f} | P&L: ₹{pnl:+.2f}")

                    cur.execute("""
                        UPDATE trades SET exit_price=%s, exit_time=NOW(), pnl=%s, status='CLOSED'
                        WHERE stock=%s AND status='OPEN'
                    """, (price, pnl, symbol))
                    cur.execute("DELETE FROM open_positions WHERE stock = %s", (symbol,))
                    proceeds = price * qty
                    cur.execute("""
                        UPDATE portfolio SET cash = cash + %s, invested = invested - %s,
                                             capital = capital + %s, pnl = pnl + %s, updated_at = NOW()
                    """, (proceeds, entry * qty, pnl, pnl))
                    open_count -= 1
                    cash += proceeds

            # Always log the signal
            cur.execute("""
                INSERT INTO trades (stock, action, entry_price, quantity, reason, entry_time, status,
                                    confluence_score, regime, atr_at_entry, sentiment_score)
                VALUES (%s, %s, %s, 0, %s, NOW(), 'SIGNAL', %s, %s, %s, %s)
            """, (symbol, confluence.action, price, reason_str,
                  effective_score, regime_result.regime, atr, sentiment))

            # Log to signal_log table for analytics
            daily_indicators = confluence.daily.indicators
            hourly_indicators = confluence.hourly.indicators
            cur.execute("""
                INSERT INTO signal_log (stock, regime, adx, atr, confluence_score, action,
                                        rsi, ema_trend, news_sentiment)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (symbol, regime_result.regime, regime_result.adx, atr,
                  effective_score, confluence.action,
                  hourly_indicators.get("rsi"),
                  "UP" if confluence.daily.direction > 0 else "DOWN",
                  sentiment))

        except Exception as e:
            print(f"  │  ❌ Error: {e}")
            import traceback
            traceback.print_exc()

        print(f"  └{'─' * 50}\n")

    # ─── 4. Manage trailing stops on open positions ───────────
    print(f"  {'─' * 50}")
    print(f"  Managing trailing stops...")
    cur.execute("SELECT stock, quantity, entry_price, stop_loss, target FROM open_positions")
    positions = cur.fetchall()

    for pos in positions:
        stock, qty, entry, sl, target = pos[0], int(pos[1]), float(pos[2]), float(pos[3]), float(pos[4])
        short = stock.replace(".NS", "")

        try:
            frames = fetch_multi_timeframe(stock)
            df_15 = frames.get("15m")
            if df_15 is None or len(df_15) < 2:
                continue

            current_price = float(df_15["close"].iloc[-1])
            atr = calculate_atr(df_15) or (current_price * 0.01)

            # Check if target was hit
            if current_price >= target:
                pnl = (current_price - entry) * qty
                print(f"  🎯 {short}: Target hit @ ₹{current_price:.2f} | P&L: +₹{pnl:.2f}")
                cur.execute("""
                    UPDATE trades SET exit_price=%s, exit_time=NOW(), pnl=%s, status='CLOSED'
                    WHERE stock=%s AND status='OPEN'
                """, (current_price, pnl, stock))
                cur.execute("DELETE FROM open_positions WHERE stock = %s", (stock,))
                proceeds = current_price * qty
                cur.execute("""
                    UPDATE portfolio SET cash = cash + %s, invested = invested - %s,
                                         capital = capital + %s, pnl = pnl + %s, updated_at = NOW()
                """, (proceeds, entry * qty, pnl, pnl))
                continue

            # Check trailing stop
            trail = check_trailing_stop(stock, entry, current_price, sl, atr)

            if trail.should_close:
                pnl = (current_price - entry) * qty
                print(f"  🛑 {short}: Stop hit @ ₹{current_price:.2f} | P&L: ₹{pnl:+.2f}")
                cur.execute("""
                    UPDATE trades SET exit_price=%s, exit_time=NOW(), pnl=%s, status='CLOSED'
                    WHERE stock=%s AND status='OPEN'
                """, (current_price, pnl, stock))
                cur.execute("DELETE FROM open_positions WHERE stock = %s", (stock,))
                proceeds = current_price * qty
                cur.execute("""
                    UPDATE portfolio SET cash = cash + %s, invested = invested - %s,
                                         capital = capital + %s, pnl = pnl + %s, updated_at = NOW()
                """, (proceeds, entry * qty, pnl, pnl))
            elif trail.should_update:
                print(f"  📈 {short}: Trailing stop moved ₹{sl:.2f} → ₹{trail.new_stop:.2f}")
                cur.execute("""
                    UPDATE open_positions SET stop_loss = %s WHERE stock = %s
                """, (trail.new_stop, stock))
            else:
                print(f"  ⏳ {short}: Holding @ ₹{current_price:.2f} (P&L: ₹{trail.unrealized_pnl:+.2f})")

        except Exception as e:
            print(f"  ❌ Error managing {short}: {e}")

    # ─── 5. Snapshot equity ───────────────────────────────────
    cur.execute("SELECT cash, invested FROM portfolio ORDER BY updated_at DESC LIMIT 1")
    row = cur.fetchone()
    if row:
        c, i = float(row[0]), float(row[1])
        cur.execute("""
            INSERT INTO equity_snapshots (capital, cash, invested) VALUES (%s, %s, %s)
        """, (c + i, c, i))

    # ─── 6. Update P&L percentage ─────────────────────────────
    cur.execute("SELECT capital, pnl FROM portfolio ORDER BY updated_at DESC LIMIT 1")
    row = cur.fetchone()
    if row:
        cap, pnl = float(row[0]), float(row[1])
        pnl_pct = (pnl / INITIAL_CAPITAL) * 100 if INITIAL_CAPITAL > 0 else 0
        cur.execute("UPDATE portfolio SET pnl_pct = %s WHERE id = (SELECT id FROM portfolio ORDER BY updated_at DESC LIMIT 1)", (pnl_pct,))

    conn.commit()
    cur.close()
    conn.close()
    print(f"\n  ✅ Scan complete @ {datetime.now().strftime('%H:%M:%S')}\n")


if __name__ == "__main__":
    run()
