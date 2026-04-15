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
import io
import time
import traceback
import psycopg2
from datetime import datetime, timezone, timedelta
from multi_timeframe import fetch_multi_timeframe, get_confluence
from regime import detect_regime
from tenacity import retry, wait_fixed, stop_after_attempt
from risk_manager import (
    calculate_atr, plan_position, check_trailing_stop
)
MAX_POSITIONS = 10  # Scaled for 100 stocks
from calculator import calculate_realistic_charges
from sentiment_llm import get_llm_sentiment
from alerts import send_telegram_alert

IST = timezone(timedelta(hours=5, minutes=30))


class TeeLogger:
    """Writes to both stdout and an in-memory buffer for log capture."""
    def __init__(self):
        self._buf = io.StringIO()
        self._stdout = sys.stdout
    def write(self, msg):
        self._stdout.write(msg)
        self._buf.write(msg)
    def flush(self):
        self._stdout.flush()
    def getvalue(self):
        return self._buf.getvalue()

STOCKS = [
    "ABB.NS", "ACC.NS", "ADANIENT.NS", "ADANIPORTS.NS", "ADANIPOWER.NS", "AMBUJACEM.NS", "APOLLOHOSP.NS", "ASIANPAINT.NS", 
    "AXISBANK.NS", "BAJAJ-AUTO.NS", "BAJFINANCE.NS", "BAJAJFINSV.NS", "BANKBARODA.NS", "BEL.NS", "BHARTIARTL.NS", "BPCL.NS", 
    "BRITANNIA.NS", "CANBK.NS", "CHOLAFIN.NS", "CIPLA.NS", "COALINDIA.NS", "COLPAL.NS", "CONCOR.NS", "DLF.NS", "DABUR.NS", 
    "DIVISLAB.NS", "DRREDDY.NS", "EICHERMOT.NS", "GAIL.NS", "GODREJCP.NS", "GRASIM.NS", "HCLTECH.NS", "HDFCBANK.NS", 
    "HDFCLIFE.NS", "HAVELLS.NS", "HEROMOTOCO.NS", "HINDALCO.NS", "HAL.NS", "HINDUNILVR.NS", "ICICIBANK.NS", "ICICIGI.NS", 
    "ICICIPRULI.NS", "ITC.NS", "INDHOTEL.NS", "IOC.NS", "IRCTC.NS", "INDUSINDBK.NS", "INFY.NS", "INDIGO.NS", "JSWSTEEL.NS", 
    "JINDALSTEL.NS", "KOTAKBANK.NS", "LTIM.NS", "LT.NS", "M&M.NS", "MARICO.NS", "MARUTI.NS", "NTPC.NS", "NESTLEIND.NS", 
    "ONGC.NS", "PIDILITIND.NS", "PFC.NS", "POWERGRID.NS", "PNB.NS", "RECLTD.NS", "RELIANCE.NS", "SBICARD.NS", "SBILIFE.NS", 
    "SBIN.NS", "SRF.NS", "SHREECEM.NS", "SHRIRAMFIN.NS", "SIEMENS.NS", "SUNPHARMA.NS", "TATACONSUM.NS", "TATAMOTORS.NS", 
    "TATAPOWER.NS", "TATASTEEL.NS", "TCS.NS", "TECHM.NS", "TITAN.NS", "TRENT.NS", "TVSMOTOR.NS", "ULTRACEMCO.NS", "UNITDSPR.NS", 
    "VBL.NS", "VEDL.NS", "WIPRO.NS", "ZOMATO.NS", "ZYDUSLIFE.NS", "BHEL.NS", "IDFCFIRSTB.NS", "IRFC.NS", "JIOFIN.NS", 
    "LODHA.NS", "OFSS.NS", "PAGEIND.NS", "TATACOMM.NS", "ADANIENSOL.NS", "ADANIGREEN.NS", "ATGL.NS", "BAJAJHLDNG.NS"
]
DATABASE_URL = os.environ["DATABASE_URL"]
INITIAL_CAPITAL = 100000


@retry(wait=wait_fixed(5), stop=stop_after_attempt(3))
def get_conn():
    """Warming up the database with a tenacity retry wrapper (Neon sleep handling)."""
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


def fetch_batch_universe(tickers: list) -> dict:
    """Download market data for 100+ stocks in just 3 efficient requests."""
    import yfinance as yf
    print(f"  📥 Batch fetching universe ({len(tickers)} stocks)...")
    
    try:
        # 1. Daily data (2 years)
        df_d_all = yf.download(tickers, period="2y", interval="1d", progress=False, group_by='ticker')
        # 2. Hourly data (1 month)
        df_h_all = yf.download(tickers, period="1mo", interval="1h", progress=False, group_by='ticker')
        # 3. 15-min data (5 days)
        df_15_all = yf.download(tickers, period="5d", interval="15m", progress=False, group_by='ticker')
        
        universe = {}
        for ticker in tickers:
            try:
                # Handle MultiIndex result if yfinance returns one
                d_df = df_d_all[ticker].copy() if ticker in df_d_all else None
                h_df = df_h_all[ticker].copy() if ticker in df_h_all else None
                f_df = df_15_all[ticker].copy() if ticker in df_15_all else None
                
                # Cleanup: lowercase column names
                if d_df is not None: d_df.columns = [c.lower() for c in d_df.columns]
                if h_df is not None: h_df.columns = [c.lower() for c in h_df.columns]
                if f_df is not None: f_df.columns = [c.lower() for c in f_df.columns]
                
                universe[ticker] = {
                    "1d": d_df.dropna() if d_df is not None else None,
                    "1h": h_df.dropna() if h_df is not None else None,
                    "15m": f_df.dropna() if f_df is not None else None
                }
            except Exception:
                universe[ticker] = {"1d": None, "1h": None, "15m": None}
        return universe
    except Exception as e:
        print(f"  ❌ Batch fetch failed: {e}")
        return {}


def run():
    tee = TeeLogger()
    sys.stdout = tee
    start_ts = time.time()
    run_id = None
    log_conn = psycopg2.connect(DATABASE_URL)

    try:
        log_cur = log_conn.cursor()
        log_cur.execute(
            "INSERT INTO run_logs (started_at, status) VALUES (NOW(), 'RUNNING') RETURNING id"
        )
        run_id = log_cur.fetchone()[0]
        log_conn.commit()
        log_cur.close()
    except Exception as e:
        sys.stdout = tee._stdout
        print(f"[run_logs] Could not create run record: {e}")
        sys.stdout = tee

    def finish_log(status, stocks_scanned=0, signals_fired=0, trades_executed=0, error_msg=None):
        """Finalize the run_logs row."""
        sys.stdout = tee._stdout  # restore before any potential errors here
        if run_id is None:
            return
        try:
            duration_ms = int((time.time() - start_ts) * 1000)
            lc = log_conn.cursor()
            lc.execute("""
                UPDATE run_logs SET
                    finished_at = NOW(), status = %s,
                    market_open = %s, stocks_scanned = %s,
                    signals_fired = %s, trades_executed = %s,
                    error_message = %s, log_lines = %s, duration_ms = %s
                WHERE id = %s
            """, (
                status, status != 'MARKET_CLOSED',
                stocks_scanned, signals_fired, trades_executed,
                error_msg, tee.getvalue()[:10000], duration_ms, run_id
            ))
            log_conn.commit()
            lc.close()
            log_conn.close()
        except Exception as le:
            print(f"[run_logs] Could not finalize run record: {le}")

    conn = get_conn()
    cur = conn.cursor()
    now = datetime.now(IST)
    
    print(f"\n{'='*60}")
    print(f"  QuantumTrader V2.1 — Market Scan @ {now.strftime('%Y-%m-%d %H:%M:%S')} IST")
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
        finish_log('MARKET_CLOSED')
        return

    # ─── 1. Get portfolio state ───────────────────────────────
    cur.execute("SELECT capital, cash, invested FROM portfolio ORDER BY updated_at DESC LIMIT 1")
    row = cur.fetchone()
    capital = float(row[0]) if row else INITIAL_CAPITAL
    cash = float(row[1]) if row else INITIAL_CAPITAL
    invested = float(row[2]) if row else 0

    cur.execute("SELECT COUNT(*) FROM open_positions")
    open_count = cur.fetchone()[0]
    
    cur.execute("SELECT stock FROM open_positions")
    held_stocks = {row[0] for row in cur.fetchall()}

    signals_total = 0   # tracks non-HOLD signals this run
    trades_total = 0    # tracks actual BUY/SELL executions this run

    print(f"  Portfolio: ₹{capital:,.0f} | Cash: ₹{cash:,.0f} | Invested: ₹{invested:,.0f}")
    print(f"  Open positions: {open_count}/{MAX_POSITIONS}\n")
    
    # Clamp cash to 0 if negative (DB corruption guard)
    if cash < 0:
        print(f"  ⚠️ CASH GUARD: Resetting negative cash to Rs 0 (was ₹{cash:,.0f})")
        cash = 0
        cur.execute("UPDATE portfolio SET cash = 0 WHERE cash < 0")

    # ─── 2. Batch Fetch Universe Data ──────────────────────────
    universe_data = fetch_batch_universe(STOCKS)
    if not universe_data:
        print("  ❌ No market data received. Critical failure.")
        finish_log('ERROR', error_msg="Batch fetch failed")
        return

    # ─── 3. Process each stock ────────────────────────────────
    for symbol in STOCKS:
        short_name = symbol.replace(".NS", "")
        print(f"  ┌─ Analyzing {short_name} {'─' * (40 - len(short_name))}")

        try:
            # Fetch all timeframes from the pre-downloaded universe
            frames = universe_data.get(symbol, {})
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

            # Get qualitative sentiment (LLM 2.0)
            sentiment_score = get_llm_sentiment(symbol)
            # Normalize: if LLM says > 0.3, add +1 to score; if < -0.3, sub 1
            sentiment = 0
            if sentiment_score > 0.3: sentiment = 1
            if sentiment_score < -0.3: sentiment = -1
            
            sentiment_str = {1: "Positive", -1: "Negative", 0: "Neutral"}[sentiment]
            print(f"  │  LLM News: {sentiment_str} ({sentiment_score:+.2f})")

            # Adjust confluence with sentiment
            effective_score = confluence.confluence_score
            if sentiment != 0:
                effective_score += sentiment  # ±1 from news

            # Current price and ATR
            price = float(df_15["close"].iloc[-1])
            prev_close = float(df_15["close"].iloc[-2]) if len(df_15) >= 2 else price
            
            # 🚨 PRICE SANITY CHECK: Reject if price moved >20% from previous bar
            # This prevents acting on bad/stale/split-adjusted data (e.g. HDFC anomaly)
            price_chg_pct = abs((price - prev_close) / prev_close) * 100 if prev_close > 0 else 0
            if price_chg_pct > 20:
                print(f"  │  🚨 PRICE ANOMALY DETECTED: {short_name} moved {price_chg_pct:.1f}% in one bar (₹{prev_close:.2f} → ₹{price:.2f}) — SKIPPING")
                print(f"  └{'─' * 50}\n")
                continue
            
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
            # LIQUIDITY CHECK: Ensure we aren't trading more than 10% of interval volume
            last_volume = float(df_15["volume"].iloc[-1])
            
            if confluence.action == "BUY" and symbol not in held_stocks and open_count < MAX_POSITIONS and cash > 0:
                plan = plan_position(
                    stock=symbol,
                    entry_price=price,
                    atr=atr,
                    capital=capital,
                    regime=regime_result.regime,
                )

                if plan and plan.quantity > 0:
                    # Realistic Penalty: Reject if quantity is too high for the interval
                    if plan.quantity > last_volume * 0.1:
                        print(f"  │  ⚠ BUY REJECTED: Low Liquidity ({plan.quantity} qty vs {last_volume:.0f} vol)")
                    else:
                        cost = price * plan.quantity
                        # 🛡️ HARD CASH GUARD: Never go into negative cash
                        if cost > cash:
                            print(f"  │  ⚠ BUY REJECTED: Insufficient cash (₹{cost:,.0f} needed, only ₹{cash:,.0f} available)")
                        else:
                            print(f"  │  🟢 EXECUTING BUY: {plan.quantity} shares @ ₹{price:.2f}")
                            print(f"  │     SL: ₹{plan.stop_loss:.2f} | TP: ₹{plan.target:.2f} | RR: {plan.reward_risk_ratio:.1f}")

                            send_telegram_alert(
                                f"🚨 <b>BUY EXECUTED: {short_name}</b>\n\n"
                                f"Quantity: {plan.quantity}\n"
                                f"Entry: ₹{price:.2f}\n"
                                f"Stop Loss: ₹{plan.stop_loss:.2f}\n"
                                f"Target: ₹{plan.target:.2f}\n"
                                f"Confluence: +{effective_score}\n"
                                f"Reason: {reason_str}"
                            )

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

                            cur.execute("""
                                UPDATE portfolio SET cash = cash - %s, invested = invested + %s, updated_at = NOW()
                            """, (cost, cost))
                            open_count += 1
                            cash -= cost
                            held_stocks.add(symbol)

            elif confluence.action == "SELL":
                cur.execute("SELECT quantity, entry_price, stop_loss FROM open_positions WHERE stock = %s", (symbol,))
                pos = cur.fetchone()
                if pos:
                    qty, entry, sl = int(pos[0]), float(pos[1]), float(pos[2])
                    # Fix: Using Institutional Hardened Taxes & Slippage
                    c = calculate_realistic_charges(entry, price, qty, is_intraday=False)
                    pnl = c.net_pnl
                    print(f"  │  🔴 EXECUTING SELL: {qty} shares @ ₹{price:.2f} | Net P&L: ₹{pnl:+.2f}")

                    send_telegram_alert(
                        f"🔴 <b>SELL EXECUTED: {short_name}</b>\n\n"
                        f"Quantity: {qty}\n"
                        f"Entry: ₹{entry:.2f}\n"
                        f"Exit: ₹{price:.2f}\n"
                        f"Taxes & Slippage: ₹{c.total:.2f}\n"
                        f"Net P&L: ₹{pnl:+.2f} ({c.net_pnl_pct:+.2f}%)"
                    )

                    cur.execute("""
                        UPDATE trades SET exit_price=%s, exit_time=NOW(), pnl=%s, status='CLOSED', charges=%s
                        WHERE stock=%s AND status='OPEN'
                    """, (price, pnl, c.total, symbol))
                    cur.execute("DELETE FROM open_positions WHERE stock = %s", (symbol,))
                    proceeds = price * qty - c.total
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
            frames = universe_data.get(stock, {})
            df_15 = frames.get("15m")
            if df_15 is None or len(df_15) < 2:
                continue

            current_price = float(df_15["close"].iloc[-1])
            atr = calculate_atr(df_15) or (current_price * 0.01)

            # Get ADX for trailing stop decay
            df_daily = frames.get("1d")
            regime_result = detect_regime(df_daily if df_daily is not None and len(df_daily) > 50 else df_15)
            adx_val = regime_result.adx

            # Check if target was hit
            if current_price >= target:
                # Fix: Use Delivery Hardened charges
                c = calculate_realistic_charges(entry, current_price, qty, is_intraday=False)
                pnl = c.net_pnl
                print(f"  🎯 {short}: Target hit @ ₹{current_price:.2f} | Net P&L: +₹{pnl:.2f}")
                send_telegram_alert(
                    f"🎯 <b>TARGET HIT: {short}</b>\n\n"
                    f"Quantity: {qty}\n"
                    f"Entry: ₹{entry:.2f}\n"
                    f"Exit: ₹{current_price:.2f}\n"
                    f"Taxes & Slippage: ₹{c.total:.2f}\n"
                    f"Net P&L: ₹{pnl:+.2f} ({c.net_pnl_pct:+.2f}%)"
                )
                cur.execute("""
                    UPDATE trades SET exit_price=%s, exit_time=NOW(), pnl=%s, status='CLOSED', charges=%s
                    WHERE stock=%s AND status='OPEN'
                """, (current_price, pnl, c.total, stock))
                cur.execute("DELETE FROM open_positions WHERE stock = %s", (stock,))
                proceeds = current_price * qty - c.total
                cur.execute("""
                    UPDATE portfolio SET cash = cash + %s, invested = invested - %s,
                                         capital = capital + %s, pnl = pnl + %s, updated_at = NOW()
                """, (proceeds, entry * qty, pnl, pnl))
                continue

            # STRATEGY: 📈 BREAK-EVEN TRIGGER
            # If price moves +1.5% in favor, move SL to Entry Price
            unrealized_pct = ((current_price - entry) / entry) * 100
            if unrealized_pct >= 1.5 and sl < entry:
                print(f"  🛡️ {short}: BREAK-EVEN TRIGGER! Moving SL: ₹{sl:.2f} → ₹{entry:.2f}")
                sl = entry
                cur.execute("UPDATE open_positions SET stop_loss = %s WHERE stock = %s", (entry, stock))

            # Check trailing stop
            trail = check_trailing_stop(stock, entry, current_price, sl, atr, adx=adx_val)

            if trail.should_close:
                # Fix: Use Delivery Hardened charges
                c = calculate_realistic_charges(entry, current_price, qty, is_intraday=False)
                pnl = c.net_pnl
                print(f"  🛑 {short}: Stop hit @ ₹{current_price:.2f} | Net P&L: ₹{pnl:+.2f}")
                send_telegram_alert(
                    f"🛑 <b>STOP HIT: {short}</b>\n\n"
                    f"Quantity: {qty}\n"
                    f"Entry: ₹{entry:.2f}\n"
                    f"Exit: ₹{current_price:.2f}\n"
                    f"Taxes & Slippage: ₹{c.total:.2f}\n"
                    f"Net P&L: ₹{pnl:+.2f} ({c.net_pnl_pct:+.2f}%)\n"
                    f"Stop Trailed From: ₹{trail.old_sl if hasattr(trail, 'old_sl') else sl:.2f}"
                )
                cur.execute("""
                    UPDATE trades SET exit_price=%s, exit_time=NOW(), pnl=%s, status='CLOSED', charges=%s
                    WHERE stock=%s AND status='OPEN'
                """, (current_price, pnl, c.total, stock))
                cur.execute("DELETE FROM open_positions WHERE stock = %s", (stock,))
                proceeds = current_price * qty - c.total
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
    print(f"\n  ✅ Scan complete @ {now.strftime('%H:%M:%S')} IST\n")
    finish_log('SUCCESS', stocks_scanned=len(STOCKS), signals_fired=signals_total, trades_executed=trades_total)


def _safe_run():
    """Entry point that catches top-level crashes and logs them to run_logs."""
    try:
        run()
    except Exception as e:
        sys.stdout = sys.__stdout__  # emergency restore
        err = traceback.format_exc()
        print(f"\n💥 FATAL ERROR: {e}\n{err}")
        send_telegram_alert(f"💥 <b>FATAL ERROR IN QUANTUMTRADER</b>\n\n<pre>{str(e)}</pre>")
        # Try to mark the run as errored
        try:
            conn = psycopg2.connect(DATABASE_URL)
            cur = conn.cursor()
            cur.execute("""
                UPDATE run_logs SET status='ERROR', finished_at=NOW(),
                    error_message=%s, log_lines=%s
                WHERE id = (SELECT id FROM run_logs ORDER BY started_at DESC LIMIT 1)
            """, (str(e)[:500], err[:10000]))
            conn.commit(); cur.close(); conn.close()
        except Exception:
            pass
        sys.exit(1)


if __name__ == "__main__":
    _safe_run()
