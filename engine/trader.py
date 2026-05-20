"""
QuantumTrader V4.0 — Institutional-Grade Swing Trading Engine
=============================================================
5-Layer Architecture:
  Layer 1 | Macro Filter      — Nifty 50 EMA structure + VIX + breadth gate
  Layer 2 | Sector Rotation   — Top-5 NSE sectors by 20d/60d momentum
  Layer 3 | Opportunity Rank  — Composite score (RS, volume, breakout, trend, RRR)
                                 Only the TOP 5 ranked stocks get full evaluation
  Layer 4 | Signal Evaluation — Multi-TF confluence (signals.py + multi_timeframe.py)
  Layer 5 | Trade Management  — ATR-based trailing stops + position management

Key V4 principles vs V3:
  - SELECT the best, don't FILTER the worst
  - Macro regime blocks all new longs (index alignment is free tailwind)
  - Sector rotation adds another free tailwind
  - Kelly criterion tracks live edge — alerts when system is unprofitable
  - 24h cooldown + daily -2R guard + max-1-entry/symbol/day guard all active
"""

import os
import sys
import io
import time
import traceback
from datetime import datetime, timezone, timedelta
import psycopg2
from db import Database
from tenacity import retry, wait_fixed, stop_after_attempt
from regime import detect_regime
from risk_manager import (
    calculate_atr, plan_position, check_trailing_stop, MAX_POSITIONS, RISK_PER_TRADE
)
from calculator import calculate_realistic_charges
from alerts import send_telegram_alert
from signals import evaluate_signal, _default_sentiment, apply_intrabar_exit
# V4 Architecture Layers
from macro_filter import get_macro_state
from sector_rotation import get_allowed_sectors, get_sector_for_stock
from ranker import rank_universe
from kelly import get_kelly_from_db

IST = timezone(timedelta(hours=5, minutes=30))

# TATAMOTORS.NS, LTIM.NS, ZOMATO.NS removed — Yahoo Finance returns 404 (no data) for these tickers.
# Use TATAMOTORS-DVR.NS is no longer traded; ZOMATO trades as ETERNAL.NS from Apr 2025.
STOCKS = [
    "ABB.NS", "ACC.NS", "ADANIENT.NS", "ADANIPORTS.NS", "ADANIPOWER.NS", "AMBUJACEM.NS", "APOLLOHOSP.NS", "ASIANPAINT.NS",
    "AXISBANK.NS", "BAJAJ-AUTO.NS", "BAJFINANCE.NS", "BAJAJFINSV.NS", "BANKBARODA.NS", "BEL.NS", "BHARTIARTL.NS", "BPCL.NS",
    "BRITANNIA.NS", "CANBK.NS", "CHOLAFIN.NS", "CIPLA.NS", "COALINDIA.NS", "COLPAL.NS", "CONCOR.NS", "DLF.NS", "DABUR.NS",
    "DIVISLAB.NS", "DRREDDY.NS", "EICHERMOT.NS", "GAIL.NS", "GODREJCP.NS", "GRASIM.NS", "HCLTECH.NS", "HDFCBANK.NS",
    "HDFCLIFE.NS", "HAVELLS.NS", "HEROMOTOCO.NS", "HINDALCO.NS", "HAL.NS", "HINDUNILVR.NS", "ICICIBANK.NS", "ICICIGI.NS",
    "ICICIPRULI.NS", "ITC.NS", "INDHOTEL.NS", "IOC.NS", "IRCTC.NS", "INDUSINDBK.NS", "INFY.NS", "INDIGO.NS", "JSWSTEEL.NS",
    "JINDALSTEL.NS", "KOTAKBANK.NS", "LT.NS", "M&M.NS", "MARICO.NS", "MARUTI.NS", "NTPC.NS", "NESTLEIND.NS",
    "ONGC.NS", "PIDILITIND.NS", "PFC.NS", "POWERGRID.NS", "PNB.NS", "RECLTD.NS", "RELIANCE.NS", "SBICARD.NS", "SBILIFE.NS",
    "SBIN.NS", "SRF.NS", "SHREECEM.NS", "SHRIRAMFIN.NS", "SIEMENS.NS", "SUNPHARMA.NS", "TATACONSUM.NS",
    "TATAPOWER.NS", "TATASTEEL.NS", "TCS.NS", "TECHM.NS", "TITAN.NS", "TRENT.NS", "TVSMOTOR.NS", "ULTRACEMCO.NS", "UNITDSPR.NS",
    "VBL.NS", "VEDL.NS", "WIPRO.NS", "ETERNAL.NS", "ZYDUSLIFE.NS", "BHEL.NS", "IDFCFIRSTB.NS", "IRFC.NS", "JIOFIN.NS",
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
    return psycopg2.connect(os.environ["DATABASE_URL"])


def is_market_open() -> bool:
    """Checks if today is a weekday and not an NSE holiday, and within market hours."""
    now_ist = datetime.now(IST)
    
    # 1. Weekend check
    if now_ist.weekday() >= 5: 
        return False
        
    # 2. NSE Holiday Check 2026
    # Source: NSE India official holiday list
    holidays_2026 = [
        "2026-01-26", # Republic Day
        "2026-03-06", # Holi
        "2026-03-27", # Ramzan Id
        "2026-04-02", # Mahavir Jayanti
        "2026-04-03", # Good Friday
        "2026-04-10", # Ambedkar Jayanti
        "2026-05-01", # Maharashtra Day / May Day
        "2026-10-02", # Mahatma Gandhi Jayanti
        "2026-10-21", # Dussehra
        "2026-11-05", # Diwali-Laxmi Pujan
        "2026-11-25", # Guru Nanak Jayanti
        "2026-12-25", # Christmas
    ]
    
    today_str = now_ist.strftime("%Y-%m-%d")
    if today_str in holidays_2026:
        return False

    # 3. Time check (09:15 - 15:30)
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
    print(f"[{now.strftime('%Y-%m-%d %H:%M:%S')}] QuantumTrader Engine V4")

    try:
        db = Database()
    except Exception as e:
        print(f"❌ Database connection failed: {e}")
        finish_log('ERROR', error_msg=f"Database connection failed: {e}")
        return

    # ─── Determine trading mode ────────────────────────────────────────────────
    # EXIT MANAGEMENT always runs (any weekday, 9:00-17:30 IST) to handle SL/targets.
    # NEW ENTRIES only fire when market is open AND macro is clear.
    # This is the critical fix: the old code did an early `return` on market-closed
    # or macro-blocked, which completely bypassed stop-loss and target management.
    now_ist = datetime.now(IST)
    is_weekday = now_ist.weekday() < 5
    exit_window_open = is_weekday and (
        now_ist.hour >= 9 and (now_ist.hour < 17 or (now_ist.hour == 17 and now_ist.minute <= 30))
    )
    market_open = is_market_open()
    allow_new_entries = market_open  # will be further gated by macro below

    if not exit_window_open:
        # Truly outside hours — nothing to do
        print(f"  Outside active window. Exiting.")
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

    # ─── V4 Layer 1: Macro Filter (gates NEW entries only) ────────────────────
    macro_blocked = False
    allowed_sectors = None
    if allow_new_entries:
        try:
            macro = get_macro_state(use_cache=True)
            if not macro.tradeable:
                macro_blocked = True
                allow_new_entries = False
                print(f"  🚫 MACRO FILTER BLOCKED: {macro.reason}")
                print(f"  ℹ Exit management will still run for {open_count} open position(s).")
            else:
                print(f"  ✅ Macro clear | Nifty200EMA={'✅' if macro.nifty_above_200ema else '❌'} "
                      f"| 50EMA slope={'✅' if macro.nifty_50ema_slope_up else '❌'} "
                      f"| VIX={macro.vix:.1f} | Breadth={macro.breadth_pct:.0f}%")
        except Exception as e:
            print(f"  ⚠ Macro filter failed (fail-open): {e}")

        # ─── V4 Layer 2: Sector Rotation ──────────────────────────────────────
        if not macro_blocked:
            try:
                allowed_sectors = get_allowed_sectors(top_n_fraction=0.5, use_cache=True)
            except Exception as e:
                print(f"  ⚠ Sector rotation failed (fail-open, all sectors allowed): {e}")

        # ─── V4 Kelly Monitor ─────────────────────────────────────────────────
        try:
            kelly = get_kelly_from_db(db)
            if not kelly.has_edge:
                print(f"  ⚠ [kelly] System has NO positive edge yet ({kelly.sample_size} trades). "
                      f"WR={kelly.win_rate:.0%}, Payoff={kelly.payoff_ratio:.2f}x — TUNING MODE")
        except Exception as e:
            print(f"  ⚠ Kelly computation failed: {e}")

        # ─── V4.2: Loss Circuit Breakers (gate NEW entries only) ──────────────
        DAILY_LOSS_LIMIT = -(capital * RISK_PER_TRADE * 1.5)
        WEEKLY_LOSS_LIMIT = -(capital * 0.03)
        daily_loss_limit_hit = False
        weekly_loss_limit_hit = False
        performance_guard_hit = False
        try:
            daily_pnl = db.get_today_realized_pnl()
            if daily_pnl <= DAILY_LOSS_LIMIT:
                daily_loss_limit_hit = True
                allow_new_entries = False
                print(f"  ⛔ DAILY LOSS GUARD: Today P&L ₹{daily_pnl:+.2f} <= -1.5R limit ₹{DAILY_LOSS_LIMIT:.2f}. No new BUYs today.")
                send_telegram_alert(f"⛔ Daily loss guard hit: ₹{daily_pnl:+.2f}. Halting new entries.")
        except Exception as e:
            print(f"  ⚠ Could not check daily P&L: {e}")
            daily_loss_limit_hit = False

        try:
            weekly_pnl = db.get_week_realized_pnl()
            if weekly_pnl <= WEEKLY_LOSS_LIMIT:
                weekly_loss_limit_hit = True
                allow_new_entries = False
                print(f"  ⛔ WEEKLY LOSS GUARD: Week P&L ₹{weekly_pnl:+.2f} <= ₹{WEEKLY_LOSS_LIMIT:.2f}. No new BUYs this week.")
                send_telegram_alert(f"⛔ Weekly loss guard hit: ₹{weekly_pnl:+.2f}. Halting new entries.")
        except Exception as e:
            print(f"  ⚠ Could not check weekly P&L: {e}")
            weekly_loss_limit_hit = False

        try:
            recent = db.get_recent_system_stats(limit=20, risk_amount=capital * RISK_PER_TRADE)
            if recent["sample_size"] >= 20 and (
                recent["profit_factor"] < 0.8
                or recent["expectancy_r"] < -0.25
                or recent["max_loss_streak"] >= 6
            ):
                performance_guard_hit = True
                allow_new_entries = False
                print(
                    f"  ⛔ PERFORMANCE GUARD: last {recent['sample_size']} trades "
                    f"PF={recent['profit_factor']:.2f}, E={recent['expectancy_r']:+.2f}R, "
                    f"loss streak={recent['max_loss_streak']}. New BUYs halted."
                )
        except Exception as e:
            print(f"  ⚠ Could not check recent system stats: {e}")
            performance_guard_hit = False
    else:
        # Market not open yet / after hours — still need to manage exits
        print(f"  ℹ Market closed. Running exit management only for {open_count} open position(s).")
        daily_loss_limit_hit = weekly_loss_limit_hit = performance_guard_hit = False

    signals_total = 0; trades_total = 0
    print(f"  Portfolio: ₹{capital:,.0f} | Cash: ₹{cash:,.0f} | Open: {open_count}/{MAX_POSITIONS}\n")

    # ─── Smart data fetch: full universe OR held stocks only ──────────────────
    # If new entries are blocked, skip the expensive 100-stock download.
    # We only need candle data for the stocks we actually hold.
    if allow_new_entries:
        universe_data = fetch_batch_universe(STOCKS)
        if not universe_data:
            finish_log('ERROR', error_msg="Batch fetch failed")
            return
    else:
        # Lightweight fetch: only held positions need exit checks
        if held_stocks:
            print(f"  📥 Entries blocked — fetching exit data for {len(held_stocks)} held stock(s) only...")
            universe_data = fetch_batch_universe(list(held_stocks))
        else:
            print(f"  ℹ No open positions and new entries blocked. Nothing to do.")
            finish_log('MARKET_CLOSED')
            return

    # ─── V4 Layer 3: Opportunity Ranking (only when entries allowed) ──────────
    symbols_to_evaluate: list
    if allow_new_entries:
        try:
            ranked_candidates = rank_universe(
                universe_data=universe_data,
                top_n=5,
                allowed_sectors=allowed_sectors,
            )
            ranked_symbols = {r.symbol for r in ranked_candidates}
            symbols_to_evaluate = list(ranked_symbols | held_stocks)
            print(f"  🎯 V4: Evaluating {len(ranked_candidates)} ranked candidates + {len(held_stocks)} open positions")
        except Exception as e:
            print(f"  ⚠ Ranker failed (fail-open, using full universe): {e}")
            symbols_to_evaluate = STOCKS
    else:
        # Exit-only mode: evaluate held positions only
        symbols_to_evaluate = list(held_stocks)

    # ─── 1. Analyze Universe ──────────────────────────
    for symbol in symbols_to_evaluate:
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
                # V4.3: New entries gate — blocked when market closed, macro filtered, or circuit breakers
                if not allow_new_entries:
                    active_guards = []
                    if not market_open:
                        active_guards.append("market-closed")
                    elif macro_blocked:
                        active_guards.append("macro")
                    if daily_loss_limit_hit:
                        active_guards.append("daily")
                    if weekly_loss_limit_hit:
                        active_guards.append("weekly")
                    if performance_guard_hit:
                        active_guards.append("performance")
                    print(f"  ⛔ {short_name}: {'/'.join(active_guards) or 'guard'} active — BUY skipped")
                    continue

                # V3.5 Guard 1: RANGING regime double-check at execution time
                # Data: 13 RANGING trades, 23.1% WR, -₹2,271 total
                if sig.regime == "RANGING":
                    print(f"  ⛔ {short_name}: RANGING regime — BUY blocked")
                    continue

                # V3.6: Max 1 new entry per symbol per day
                # Separate from cooldown (cooldown = post-loss; this = any repeat same day)
                try:
                    if db.had_entry_today(symbol):
                        print(f"  ⏸ {short_name}: Already entered today — max 1 entry/symbol/day")
                        continue
                except Exception:
                    pass  # fail-open: allow trade if DB check fails

                # Guard 2: cooldown after losing exits on the same symbol.
                # One recent loss = 24h pause; two losses in five days = five-day pause.
                recent_losses = 0
                try:
                    recent_losses = db.count_recent_losses(symbol, days=5)
                except Exception:
                    recent_losses = 0
                last_loss_at = db.get_last_loss_time(symbol)
                if last_loss_at:
                    try:
                        # DB returns timezone-aware TIMESTAMPTZ; compare in UTC
                        now_utc = datetime.now(timezone.utc)
                        loss_utc = last_loss_at if last_loss_at.tzinfo else last_loss_at.replace(tzinfo=timezone.utc)
                        cooldown_hours = (now_utc - loss_utc).total_seconds() / 3600
                    except Exception:
                        cooldown_hours = 999  # fail-open: allow trade if time math breaks
                    cooldown_limit = 120 if recent_losses >= 2 else 24
                    if cooldown_hours < cooldown_limit:
                        print(f"  ⏳ {short_name}: {cooldown_limit}h loss cooldown active ({cooldown_hours:.1f}h since last loss)")
                        continue

                plan = sig.plan
                if plan and plan.quantity > 0:
                    cost = sig.price * plan.quantity
                    if cost <= cash:
                        print(f"  🟢 BUY: {short_name} @ ₹{sig.price:.2f}")
                        send_telegram_alert(f"🟢 BUY: {short_name} @ ₹{sig.price:.2f}")

                        try:
                            db.execute_buy(symbol, plan.quantity, sig.price, plan.stop_loss, 
                                           plan.target_1, plan.target_2, plan.target_3,
                                           sig.reason_str, sig.confluence_score, sig.regime, sig.atr, sig.sentiment_score)
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
    print(f"  Checking open positions (V4.1 Tranche Model)...")
    open_pos_snapshot = db.get_open_positions()
    for pos in open_pos_snapshot:
        stock = pos["stock"]
        qty = int(pos["quantity"])
        orig_qty = int(pos["original_quantity"])
        entry = float(pos["entry_price"])
        sl = float(pos["stop_loss"])
        t1, t2, t3 = float(pos["target_1"]), float(pos["target_2"]), float(pos["target_3"])
        tx = int(pos["tranches_exited"])

        try:
            frames = universe_data.get(stock, {})
            df = frames.get("15m")
            if df is None or df.empty or len(df) < 2:
                continue
            curr = df.iloc[-1]
            price = float(curr["close"])
            bar_open = float(curr["open"])
            bar_high = float(curr["high"])
            bar_low = float(curr["low"])
            
            # --- 1. FULL STOP LOSS CHECK ---
            if bar_low <= sl:
                fill_price = bar_open * 0.999 if bar_open < sl else sl
                c = calculate_realistic_charges(entry, fill_price, qty, False)
                print(f"  🛑 STOP LOSS hit for {stock} at ₹{fill_price:.2f}")
                send_telegram_alert(f"🛑 STOP LOSS hit for {stock} at ₹{fill_price:.2f} | Final PnL: ₹{c.net_pnl:+.2f}")
                db.execute_sell(stock, qty, entry, fill_price, c.net_pnl, c.total)
                cash += (fill_price * qty - c.total)
                open_count -= 1
                held_stocks.discard(stock)
                continue

            # --- 2. TRANCHE TARGET CHECKS ---
            # Tranche 1: 40% exit at 1:1 R/R
            if tx < 1 and bar_high >= t1:
                t1_qty = int(orig_qty * 0.4)
                if t1_qty > 0:
                    fill_price = bar_open * 0.999 if bar_open > t1 else t1
                    c = calculate_realistic_charges(entry, fill_price, t1_qty, False)
                    print(f"  🎯 TRANCHE 1 hit for {stock} at ₹{fill_price:.2f} (Moved SL to Break-Even)")
                    send_telegram_alert(f"🎯 TRANCHE 1 hit for {stock} at ₹{fill_price:.2f} | PnL: ₹{c.net_pnl:+.2f}\n🛡 SL moved to Break-Even (₹{entry:.2f})")
                    db.execute_partial_sell(stock, t1_qty, entry, fill_price, c.net_pnl, c.total, 1)
                    db.update_trailing_stop(stock, entry) # Elevate SL to Entry
                    cash += (fill_price * t1_qty - c.total)
                    # Refresh local state for next tranches in same loop if needed
                    qty -= t1_qty
                    tx = 1
                    sl = entry 

            # Tranche 2: 40% exit at 1:2 R/R
            if tx < 2 and bar_high >= t2:
                t2_qty = int(orig_qty * 0.4)
                if t2_qty > 0 and qty >= t2_qty:
                    fill_price = bar_open * 0.999 if bar_open > t2 else t2
                    c = calculate_realistic_charges(entry, fill_price, t2_qty, False)
                    print(f"  🎯 TRANCHE 2 hit for {stock} at ₹{fill_price:.2f} (Moved SL to T1)")
                    send_telegram_alert(f"🎯 TRANCHE 2 hit for {stock} at ₹{fill_price:.2f} | PnL: ₹{c.net_pnl:+.2f}\n🛡 SL moved to T1 (₹{t1:.2f})")
                    db.execute_partial_sell(stock, t2_qty, entry, fill_price, c.net_pnl, c.total, 2)
                    db.update_trailing_stop(stock, t1) # Elevate SL to T1
                    cash += (fill_price * t2_qty - c.total)
                    qty -= t2_qty
                    tx = 2
                    sl = t1

            # Tranche 3: Runner (20%) exit at Target 3 or Trailing Stop
            if tx >= 2:
                if bar_high >= t3:
                    # Final target hit
                    fill_price = bar_open * 0.999 if bar_open > t3 else t3
                    c = calculate_realistic_charges(entry, fill_price, qty, False)
                    print(f"  🏁 RUNNER Target hit for {stock} at ₹{fill_price:.2f}")
                    send_telegram_alert(f"🏁 RUNNER Target hit for {stock} at ₹{fill_price:.2f} | Final PnL: ₹{c.net_pnl:+.2f}")
                    db.execute_sell(stock, qty, entry, fill_price, c.net_pnl, c.total)
                    cash += (fill_price * qty - c.total)
                    open_count -= 1
                    held_stocks.discard(stock)
                    continue
                else:
                    # Standard trailing stop for the runner
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
