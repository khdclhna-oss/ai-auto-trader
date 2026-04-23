"""
QuantumTrader V3.1 — Institutional Hardened Orchestrator
=====================================================
The main trading engine. Coordinates:
  1. Multi-timeframe analysis (daily + hourly + 15m)
  2. Market regime detection (trending / ranging / volatile)
  3. Confluence-based signal generation (unified signals.py)
  4. ATR-based risk management & position sizing (risk_manager.py)
  5. Intrabar SL/TP execution (Stop-First, Gap-Slippage parity)
  6. High-fidelity signal logging
"""

import os
import sys
import io
import time
import traceback
import psycopg2
from datetime import datetime, timezone, timedelta
from regime import detect_regime
from tenacity import retry, wait_fixed, stop_after_attempt
from risk_manager import (
    calculate_atr, plan_position, check_trailing_stop, MAX_POSITIONS
)
from calculator import calculate_realistic_charges
from alerts import send_telegram_alert
from signals import evaluate_signal, _default_sentiment, apply_intrabar_exit

IST = timezone(timedelta(hours=5, minutes=30))

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


class TeeLogger:
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


@retry(wait=wait_fixed(5), stop=stop_after_attempt(3))
def get_conn():
    return psycopg2.connect(DATABASE_URL)


def is_market_open() -> bool:
    now_ist = datetime.now(IST)
    if now_ist.weekday() >= 5: return False
    market_open = now_ist.replace(hour=9, minute=15, second=0, microsecond=0)
    market_close = now_ist.replace(hour=15, minute=30, second=0, microsecond=0)
    return market_open <= now_ist <= market_close


def fetch_batch_universe(tickers: list) -> dict:
    import yfinance as yf
    print(f"  📥 Batch fetching universe ({len(tickers)} stocks)...")
    try:
        df_d_all = yf.download(tickers, period="2y", interval="1d", progress=False, group_by='ticker')
        df_h_all = yf.download(tickers, period="1mo", interval="1h", progress=False, group_by='ticker')
        df_15_all = yf.download(tickers, period="5d", interval="15m", progress=False, group_by='ticker')
        
        universe = {}
        for ticker in tickers:
            try:
                d_df = df_d_all[ticker].copy() if ticker in df_d_all else None
                h_df = df_h_all[ticker].copy() if ticker in df_h_all else None
                f_df = df_15_all[ticker].copy() if ticker in df_15_all else None
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
        log_cur.execute("INSERT INTO run_logs (started_at, status) VALUES (NOW(), 'RUNNING') RETURNING id")
        run_id = log_cur.fetchone()[0]
        log_conn.commit()
        log_cur.close()
    except Exception as e:
        sys.stdout = tee._stdout
        print(f"[run_logs] Could not create run record: {e}")
        sys.stdout = tee

    def finish_log(status, stocks_scanned=0, signals_fired=0, trades_executed=0, error_msg=None):
        sys.stdout = tee._stdout
        if run_id is None: return
        try:
            duration_ms = int((time.time() - start_ts) * 1000)
            lc = log_conn.cursor()
            lc.execute("""
                UPDATE run_logs SET finished_at = NOW(), status = %s, market_open = %s, 
                stocks_scanned = %s, signals_fired = %s, trades_executed = %s,
                error_message = %s, log_lines = %s, duration_ms = %s WHERE id = %s
            """, (status, status != 'MARKET_CLOSED', stocks_scanned, signals_fired, trades_executed,
                  error_msg, tee.getvalue()[:10000], duration_ms, run_id))
            log_conn.commit(); lc.close(); log_conn.close()
        except Exception: pass

    conn = get_conn()
    cur = conn.cursor()
    now = datetime.now(IST)
    
    print(f"\n{'='*60}")
    print(f"  QuantumTrader V3.1 — scan @ {now.strftime('%Y-%m-%d %H:%M:%S')} IST")
    print(f"{'='*60}\n")

    if not is_market_open():
        print(f"  Wait: Market closed.")
        finish_log('MARKET_CLOSED')
        return

    cur.execute("SELECT capital, cash, invested FROM portfolio ORDER BY updated_at DESC LIMIT 1")
    row = cur.fetchone()
    capital = float(row[0]) if row else INITIAL_CAPITAL
    cash = float(row[1]) if row else INITIAL_CAPITAL
    invested = float(row[2]) if row else 0

    cur.execute("SELECT COUNT(*) FROM open_positions")
    open_count = cur.fetchone()[0]
    cur.execute("SELECT stock FROM open_positions")
    held_stocks = {row[0] for row in cur.fetchall()}

    signals_total = 0; trades_total = 0
    print(f"  Portfolio: ₹{capital:,.0f} | Cash: ₹{cash:,.0f} | Open: {open_count}/{MAX_POSITIONS}\n")
    
    universe_data = fetch_batch_universe(STOCKS)
    if not universe_data:
        finish_log('ERROR', error_msg="Batch fetch failed")
        return

    # ─── 1. Analyze Universe ──────────────────────────
    for symbol in STOCKS:
        short_name = symbol.replace(".NS", "")
        try:
            frames = universe_data.get(symbol, {})
            df_15 = frames.get("15m")
            if df_15 is None or len(df_15) < 30: continue

            sig = evaluate_signal(symbol=symbol, frames=frames, capital=capital, cash=cash,
                                  held_stocks=held_stocks, sentiment_fn=_default_sentiment,
                                  open_count=open_count, max_positions=MAX_POSITIONS)
            if sig.skipped: continue

            if sig.final_action == "HOLD": continue
            signals_total += 1

            # High-fidelity signal logging (only for actionable BUY/SELL signals)
            cur.execute("""
                INSERT INTO signal_log (stock, action, price, reason, confluence_score, regime, atr, sentiment_score, logged_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW())
            """, (symbol, sig.final_action, sig.price, sig.reason_str,
                  sig.confluence_score, sig.regime, sig.atr, sig.sentiment_score))

            # Execution
            if sig.final_action == "BUY" and symbol not in held_stocks and open_count < MAX_POSITIONS:
                plan = sig.plan
                if plan and plan.quantity > 0:
                    cost = sig.price * plan.quantity
                    if cost <= cash:
                        print(f"  🟢 BUY: {short_name} @ ₹{sig.price:.2f}")
                        send_telegram_alert(f"🟢 BUY: {short_name} @ ₹{sig.price:.2f}")

                        cur.execute("""
                            INSERT INTO open_positions (stock, quantity, entry_price, stop_loss, target, entry_time, reason)
                            VALUES (%s, %s, %s, %s, %s, NOW(), %s)
                        """, (symbol, plan.quantity, sig.price, plan.stop_loss, plan.target, sig.reason_str))

                        cur.execute("""
                            INSERT INTO trades (stock, action, entry_price, quantity, reason, entry_time, status,
                                                confluence_score, regime, atr_at_entry, sentiment_score)
                            VALUES (%s, 'BUY', %s, %s, %s, NOW(), 'OPEN', %s, %s, %s, %s)
                        """, (symbol, sig.price, plan.quantity, sig.reason_str,
                              sig.confluence_score, sig.regime, sig.atr, sig.sentiment_score))

                        cur.execute("UPDATE portfolio SET cash = cash - %s, invested = invested + %s, updated_at = NOW()", (cost, cost))
                        open_count += 1; cash -= cost; held_stocks.add(symbol)

            elif sig.final_action == "SELL":
                cur.execute("SELECT quantity, entry_price FROM open_positions WHERE stock = %s", (symbol,))
                pos = cur.fetchone()
                if pos:
                    qty, ent = int(pos[0]), float(pos[1])
                    c = calculate_realistic_charges(ent, sig.price, qty, False)
                    print(f"  🔴 SELL: {short_name} @ ₹{sig.price:.2f} | PnL: ₹{c.net_pnl:+.2f}")
                    send_telegram_alert(f"🔴 SELL: {short_name} @ ₹{sig.price:.2f} | PnL: ₹{c.net_pnl:+.2f}")

                    cur.execute("""
                        UPDATE trades SET exit_price=%s, exit_time=NOW(), pnl=%s, status='CLOSED', charges=%s
                        WHERE stock=%s AND status='OPEN'
                    """, (sig.price, c.net_pnl, c.total, symbol))
                    cur.execute("DELETE FROM open_positions WHERE stock = %s", (symbol,))
                    proceeds = sig.price * qty - c.total
                    cur.execute("UPDATE portfolio SET cash = cash + %s, invested = invested - %s, capital = capital + %s, pnl = pnl + %s, updated_at = NOW()",
                                (proceeds, ent * qty, c.net_pnl, c.net_pnl))
                    open_count -= 1; cash += proceeds; held_stocks.discard(symbol); trades_total += 1
        except Exception as e:
            print(f"  ❌ Error {short_name}: {e}")

    # ─── 2. Manage Open Positions (Intrabar + Trailing) ──────────
    print(f"  Checking open positions...")
    cur.execute("SELECT stock, quantity, entry_price, stop_loss, target FROM open_positions")
    open_pos_snapshot = cur.fetchall()  # snapshot to avoid mutating cursor mid-iteration
    for pos in open_pos_snapshot:
        stock, qty, entry, sl, target = pos[0], int(pos[1]), float(pos[2]), float(pos[3]), float(pos[4])
        try:
            frames = universe_data.get(stock, {})
            df=frames.get("15m")
            if df is None or len(df) < 2: continue
            curr = df.iloc[-1]
            price = float(curr["close"])
            
            # SL/TP Exit
            ext = apply_intrabar_exit(curr, entry, sl, target, qty, None, now)
            if ext:
                fp, etype = ext["fill_price"], ext["type"]
                c = calculate_realistic_charges(entry, fp, qty, False)
                print(f"  🛑 {etype} hit for {stock} at ₹{fp:.2f}")
                send_telegram_alert(f"🛑 {etype} hit for {stock} at ₹{fp:.2f}")

                cur.execute("UPDATE trades SET exit_price=%s, exit_time=NOW(), pnl=%s, status='CLOSED', charges=%s WHERE stock=%s AND status='OPEN'",
                            (fp, c.net_pnl, c.total, stock))
                cur.execute("DELETE FROM open_positions WHERE stock = %s", (stock,))
                proceeds = fp * qty - c.total
                cur.execute("UPDATE portfolio SET cash = cash + %s, invested = invested - %s, capital = capital + %s, pnl = pnl + %s, updated_at = NOW()",
                            (proceeds, entry * qty, c.net_pnl, c.net_pnl))
                open_count -= 1; cash += proceeds; held_stocks.discard(stock); trades_total += 1
                continue

            # Trailing
            atr = calculate_atr(df) or (price * 0.01)
            adx = detect_regime(frames.get("1d") or df).adx
            upd = check_trailing_stop(stock, entry, price, sl, atr, adx)
            if upd.should_update:
                cur.execute("UPDATE open_positions SET stop_loss = %s WHERE stock = %s", (upd.new_stop, stock))
                cur.execute("UPDATE trades SET trailing_sl = %s WHERE stock = %s AND status = 'OPEN'", (upd.new_stop, stock))
        except Exception as e: print(f"  ❌ Exit err {stock}: {e}")

    # ─── 3. Finalize ──────────────────────────
    cur.execute("INSERT INTO equity_snapshots (capital, cash, invested) SELECT capital, cash, invested FROM portfolio ORDER BY updated_at DESC LIMIT 1")
    conn.commit(); cur.close(); conn.close()
    finish_log('SUCCESS', stocks_scanned=len(STOCKS), signals_fired=signals_total, trades_executed=trades_total)


def _safe_run():
    try: run()
    except Exception as e:
        sys.stdout = sys.__stdout__
        err = traceback.format_exc()
        print(f"FATAL: {e}\n{err}")
        send_telegram_alert(f"FATAL ERROR: {e}")
        try:
            conn = get_conn(); cur = conn.cursor()
            cur.execute("UPDATE run_logs SET status='ERROR', finished_at=NOW(), error_message=%s, log_lines=%s WHERE id = (SELECT id FROM run_logs ORDER BY started_at DESC LIMIT 1)", (str(e)[:500], err[:10000]))
            conn.commit(); cur.close(); conn.close()
        except Exception: pass
        sys.exit(1)

if __name__ == "__main__":
    _safe_run()
