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
from datetime import datetime, timezone, timedelta
from db import Database
from tenacity import retry, wait_fixed, stop_after_attempt
from regime import detect_regime
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
# DATABASE_URL is read lazily inside db.py to avoid crashing at import time.
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

    # Separate connection just for run_logs (kept alive across the whole run)
    try:
        import psycopg2
        _db_url = os.environ.get("DATABASE_URL")
        if not _db_url:
            raise ValueError("DATABASE_URL not set")
        log_conn = psycopg2.connect(_db_url)
    except Exception as e:
        sys.stdout = tee._stdout
        print(f"[run_logs] DB connection failed: {e}")
        sys.stdout = tee
        log_conn = None

    try:
        if log_conn:
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
        if run_id is None or log_conn is None: return
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

    now = datetime.now(IST)
    print(f"[{now.strftime('%Y-%m-%d %H:%M:%S')}] QuantumTrader Engine V3")

    try:
        db = Database()
    except Exception as e:
        print(f"❌ Database connection failed: {e}")
        finish_log('ERROR', error_msg=f"Database connection failed: {e}")
        return

    if not is_market_open():
        print(f"  Wait: Market closed.")
        finish_log('MARKET_CLOSED')
        return

    try:
        portfolio = db.get_portfolio()
        capital, cash, invested = float(portfolio["capital"]), float(portfolio["cash"]), float(portfolio["invested"])
        print(f"  Portfolio: Capital ₹{capital:,.2f} | Cash ₹{cash:,.2f} | Invested ₹{invested:,.2f}")
    except Exception as e:
        print(f"❌ Failed to load portfolio: {e}")
        finish_log('ERROR', error_msg=f"Portfolio error: {e}")
        return

    held_stocks = db.get_held_stocks()
    open_count = len(held_stocks)

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
            if df_15 is None or len(df_15) < 30:
                continue

            sig = evaluate_signal(symbol=symbol, frames=frames, capital=capital, cash=cash,
                                  held_stocks=held_stocks, sentiment_fn=_default_sentiment,
                                  open_count=open_count, max_positions=MAX_POSITIONS)
            if sig.skipped:
                continue

            if sig.final_action == "HOLD":
                continue
            signals_total += 1

            try:
                db.log_signal(symbol, sig.final_action, sig.price, sig.reason_str, sig.confluence_score, sig.regime, sig.atr, sig.sentiment_score)
            except Exception as e:
                print(f"  ⚠ Signal logging failed for {symbol}: {e}")

            # Execution
            if sig.final_action == "BUY" and symbol not in held_stocks and open_count < MAX_POSITIONS:
                plan = sig.plan
                if plan and plan.quantity > 0:
                    cost = sig.price * plan.quantity
                    if cost <= cash:
                        print(f"  🟢 BUY: {short_name} @ ₹{sig.price:.2f}")
                        send_telegram_alert(f"🟢 BUY: {short_name} @ ₹{sig.price:.2f}")

                        try:
                            db.execute_buy(symbol, plan.quantity, sig.price, plan.stop_loss, plan.target, sig.reason_str, sig.confluence_score, sig.regime, sig.atr, sig.sentiment_score)
                            open_count += 1
                            cash -= cost
                            held_stocks.add(symbol)
                            trades_total += 1
                        except Exception as e:
                            print(f"  ❌ BUY execution failed for {symbol}: {e}")

            elif sig.final_action == "SELL":
                open_pos = db.get_open_positions()
                pos = next((p for p in open_pos if p["stock"] == symbol), None)
                if pos:
                    qty, ent = int(pos["quantity"]), float(pos["entry_price"])
                    c = calculate_realistic_charges(ent, sig.price, qty, False)
                    print(f"  🔴 SELL: {short_name} @ ₹{sig.price:.2f} | PnL: ₹{c.net_pnl:+.2f}")
                    send_telegram_alert(f"🔴 SELL: {short_name} @ ₹{sig.price:.2f} | PnL: ₹{c.net_pnl:+.2f}")

                    try:
                        db.execute_sell(symbol, qty, ent, sig.price, c.net_pnl, c.total)
                        proceeds = sig.price * qty - c.total
                        open_count -= 1
                        cash += proceeds
                        held_stocks.discard(symbol)
                        trades_total += 1
                    except Exception as e:
                        print(f"  ❌ SELL execution failed for {symbol}: {e}")

        except Exception as e:
            print(f"  ❌ Error {short_name}: {e}")

    # ─── 2. Manage Open Positions (Intrabar + Trailing) ──────────
    print(f"  Checking open positions...")
    open_pos_snapshot = db.get_open_positions()
    for pos in open_pos_snapshot:
        stock = pos["stock"]
        qty = int(pos["quantity"])
        entry = float(pos["entry_price"])
        sl = float(pos["stop_loss"])
        target = float(pos["target"])

        try:
            frames = universe_data.get(stock, {})
            df = frames.get("15m")
            if df is None or df.empty or len(df) < 2:
                continue
            curr = df.iloc[-1]
            price = float(curr["close"])
            
            # SL/TP Exit
            ext = apply_intrabar_exit(curr, entry, sl, target, qty, None, now)
            if ext:
                fp, etype = ext["fill_price"], ext["type"]
                c = calculate_realistic_charges(entry, fp, qty, False)
                print(f"  🛑 {etype} hit for {stock} at ₹{fp:.2f}")
                send_telegram_alert(f"🛑 {etype} hit for {stock} at ₹{fp:.2f}")

                db.execute_sell(stock, qty, entry, fp, c.net_pnl, c.total)
                proceeds = fp * qty - c.total
                open_count -= 1
                cash += proceeds
                held_stocks.discard(stock)
                trades_total += 1
                continue

            # Trailing
            atr = calculate_atr(df) or (price * 0.01)
            regime_src = frames.get("1d")
            if regime_src is None or regime_src.empty:
                regime_src = df
            adx = detect_regime(regime_src).adx
            upd = check_trailing_stop(stock, entry, price, sl, atr, adx)
            if upd.should_update:
                db.update_trailing_stop(stock, upd.new_stop)

        except Exception as e:
            print(f"  ❌ Exit err {stock}: {e}")

    # ─── 3. Finalize ──────────────────────────
    db.snapshot_equity()
    finish_log('SUCCESS', stocks_scanned=len(STOCKS), signals_fired=signals_total, trades_executed=trades_total)


def _safe_run():
    try: run()
    except Exception as e:
        sys.stdout = sys.__stdout__
        err = traceback.format_exc()
        print(f"FATAL: {e}\n{err}")
        send_telegram_alert(f"FATAL ERROR: {e}")
        try:
            import psycopg2
            import os
            conn = psycopg2.connect(os.environ.get("DATABASE_URL"))
            cur = conn.cursor()
            cur.execute("UPDATE run_logs SET status='ERROR', finished_at=NOW(), error_message=%s, log_lines=%s WHERE id = (SELECT id FROM run_logs ORDER BY started_at DESC LIMIT 1)", (str(e)[:500], err[:10000]))
            conn.commit(); cur.close(); conn.close()
        except Exception: pass
        sys.exit(1)

if __name__ == "__main__":
    _safe_run()
