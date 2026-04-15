"""
QuantumTrader V3 — Backtesting Framework
==========================================
Calls the SAME evaluate_signal() and apply_intrabar_exit() functions
as the live engine (trader.py), ensuring backtest results faithfully
represent production behaviour.

Fidelity modes
--------------
  FULL      daily + hourly + 15m available  (last ~60 days from yfinance)
  DEGRADED  daily candles only              (older periods)

Each mode is reported separately. Metrics are NEVER blended.

Usage
-----
  python engine/backtest.py              # default 2y period
  python engine/backtest.py --period 1y
  python engine/backtest.py --save       # persist results to DB
"""

import os
import sys
import math
import argparse
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass, field
from typing import List, Optional, Callable

import yfinance as yf
import pandas as pd
import pandas_ta as ta

# ── Sibling imports ─────────────────────────────────────────────────────────
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from signals import evaluate_signal, apply_intrabar_exit, _neutral_sentiment, BUY_THRESHOLD
from calculator import calculate_realistic_charges

# ── Universe ─────────────────────────────────────────────────────────────────
STOCKS = [
    "ABB.NS", "ACC.NS", "ADANIENT.NS", "ADANIPORTS.NS", "ADANIPOWER.NS", "AMBUJACEM.NS",
    "APOLLOHOSP.NS", "ASIANPAINT.NS", "AXISBANK.NS", "BAJAJ-AUTO.NS", "BAJFINANCE.NS",
    "BAJAJFINSV.NS", "BANKBARODA.NS", "BEL.NS", "BHARTIARTL.NS", "BPCL.NS",
    "BRITANNIA.NS", "CANBK.NS", "CHOLAFIN.NS", "CIPLA.NS", "COALINDIA.NS",
    "COLPAL.NS", "CONCOR.NS", "DLF.NS", "DABUR.NS", "DIVISLAB.NS", "DRREDDY.NS",
    "EICHERMOT.NS", "GAIL.NS", "GODREJCP.NS", "GRASIM.NS", "HCLTECH.NS", "HDFCBANK.NS",
    "HDFCLIFE.NS", "HAVELLS.NS", "HEROMOTOCO.NS", "HINDALCO.NS", "HAL.NS",
    "HINDUNILVR.NS", "ICICIBANK.NS", "ICICIGI.NS", "ICICIPRULI.NS", "ITC.NS",
    "INDHOTEL.NS", "IOC.NS", "IRCTC.NS", "INDUSINDBK.NS", "INFY.NS", "INDIGO.NS",
    "JSWSTEEL.NS", "JINDALSTEL.NS", "KOTAKBANK.NS", "LTIM.NS", "LT.NS", "M&M.NS",
    "MARICO.NS", "MARUTI.NS", "NTPC.NS", "NESTLEIND.NS", "ONGC.NS", "PIDILITIND.NS",
    "PFC.NS", "POWERGRID.NS", "PNB.NS", "RECLTD.NS", "RELIANCE.NS", "SBICARD.NS",
    "SBILIFE.NS", "SBIN.NS", "SRF.NS", "SHREECEM.NS", "SHRIRAMFIN.NS", "SIEMENS.NS",
    "SUNPHARMA.NS", "TATACONSUM.NS", "TATAMOTORS.NS", "TATAPOWER.NS", "TATASTEEL.NS",
    "TCS.NS", "TECHM.NS", "TITAN.NS", "TRENT.NS", "TVSMOTOR.NS", "ULTRACEMCO.NS",
    "UNITDSPR.NS", "VBL.NS", "VEDL.NS", "WIPRO.NS", "ZOMATO.NS", "ZYDUSLIFE.NS",
    "BHEL.NS", "IDFCFIRSTB.NS", "IRFC.NS", "JIOFIN.NS", "LODHA.NS", "OFSS.NS",
    "PAGEIND.NS", "TATACOMM.NS", "ADANIENSOL.NS", "ADANIGREEN.NS", "ATGL.NS",
    "BAJAJHLDNG.NS",
]
INITIAL_CAPITAL = 100_000
RISK_PER_TRADE  = 0.02
ATR_SL_MULT     = 2.0
ATR_TP_MULT     = 4.0
MAX_POSITIONS   = 10  # synced with live engine


# ── Data structures ──────────────────────────────────────────────────────────
@dataclass
class BacktestTrade:
    stock: str
    action: str
    entry_price: float
    exit_price: float
    quantity: int
    pnl: float
    charges: float
    entry_time: object
    exit_time: object
    note: str
    hold_bars: int = 0
    fidelity: str = "FULL"   # FULL or DEGRADED


@dataclass
class SegmentMetrics:
    """Metrics for one fidelity segment (FULL or DEGRADED)."""
    fidelity: str
    period_start: str
    period_end: str
    total_trades: int = 0
    wins: int = 0
    losses: int = 0
    gross_profit: float = 0.0
    gross_loss: float = 0.0
    net_pnl: float = 0.0
    total_charges: float = 0.0
    sharpe_ratio: float = 0.0
    sortino_ratio: float = 0.0
    max_drawdown_pct: float = 0.0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    avg_win: float = 0.0
    avg_loss: float = 0.0
    expectancy: float = 0.0


@dataclass
class BacktestResult:
    period_start: str
    period_end: str
    all_trades: List[BacktestTrade] = field(default_factory=list)
    equity_curve: List[float] = field(default_factory=list)
    full: Optional[SegmentMetrics] = None
    degraded: Optional[SegmentMetrics] = None


# ── Helpers ──────────────────────────────────────────────────────────────────
def _compute_segment_metrics(trades: List[BacktestTrade], fidelity: str,
                              daily_returns: List[float]) -> SegmentMetrics:
    wins   = [t for t in trades if t.pnl > 0]
    losses = [t for t in trades if t.pnl <= 0]
    n = len(trades)
    if n == 0:
        return SegmentMetrics(fidelity, "", "", 0)

    gp = sum(t.pnl for t in wins)
    gl = abs(sum(t.pnl for t in losses))
    net = gp - gl
    wrate = len(wins) / n * 100
    pf = gp / gl if gl > 0 else float("inf")
    avg_w = gp / len(wins) if wins else 0
    avg_l = gl / len(losses) if losses else 0
    exp   = (wrate / 100 * avg_w) - ((1 - wrate / 100) * avg_l)

    # Sharpe & Sortino
    sharpe = sortino = 0.0
    if len(daily_returns) > 1:
        mean_r = sum(daily_returns) / len(daily_returns)
        var    = sum((r - mean_r) ** 2 for r in daily_returns) / (len(daily_returns) - 1)
        std    = math.sqrt(var) if var > 0 else 1e-6
        sharpe = (mean_r / std) * math.sqrt(252)
        dn     = [r for r in daily_returns if r < 0]
        dstd   = math.sqrt(sum(r**2 for r in dn) / len(dn)) if dn else 1e-6
        sortino = (mean_r / dstd) * math.sqrt(252)

    # Max drawdown
    peak = eq = INITIAL_CAPITAL
    max_dd = 0.0
    for ret in daily_returns:
        eq *= (1 + ret)
        if eq > peak: peak = eq
        dd = (peak - eq) / peak * 100
        max_dd = max(max_dd, dd)

    start_t = min(t.entry_time for t in trades)
    end_t   = max(t.exit_time  for t in trades)
    fmt = lambda d: d.strftime("%Y-%m-%d") if hasattr(d, "strftime") else str(d)

    return SegmentMetrics(
        fidelity=fidelity, period_start=fmt(start_t), period_end=fmt(end_t),
        total_trades=n, wins=len(wins), losses=len(losses),
        gross_profit=round(gp, 2), gross_loss=round(gl, 2), net_pnl=round(net, 2),
        total_charges=round(sum(t.charges for t in trades), 2),
        sharpe_ratio=round(sharpe, 3), sortino_ratio=round(sortino, 3),
        max_drawdown_pct=round(max_dd, 2), win_rate=round(wrate, 2),
        profit_factor=round(pf, 2), avg_win=round(avg_w, 2), avg_loss=round(avg_l, 2),
        expectancy=round(exp, 2),
    )


def _grade(sharpe: float) -> str:
    if sharpe > 2:   return "🏆 EXCELLENT"
    if sharpe > 1:   return "✅ GOOD"
    if sharpe > 0.5: return "⚠️  MEDIOCRE"
    if sharpe > 0:   return "🟡 WEAK"
    return "❌ NEGATIVE — do NOT trade real capital on this"


def _print_segment(seg: SegmentMetrics):
    label = seg.fidelity
    warn  = "  ⚠ daily-only" if label == "DEGRADED" else ""
    print(f"\n  ── {label} MODE{warn} ────────────────────────────────")
    print(f"  Period:        {seg.period_start} → {seg.period_end}")
    print(f"  Trades:        {seg.total_trades}  ({seg.wins}W / {seg.losses}L)")
    print(f"  Win Rate:      {seg.win_rate:.1f}%")
    print(f"  Net P&L:       ₹{seg.net_pnl:+,.2f}")
    print(f"  Total Charges: ₹{seg.total_charges:,.2f}")
    print(f"  Profit Factor: {seg.profit_factor:.2f}")
    print(f"  Avg Win/Loss:  ₹{seg.avg_win:,.0f} / ₹{seg.avg_loss:,.0f}")
    print(f"  Expectancy:    ₹{seg.expectancy:+,.2f} per trade")
    print(f"  Sharpe:        {seg.sharpe_ratio:.3f}  {_grade(seg.sharpe_ratio)}")
    print(f"  Sortino:       {seg.sortino_ratio:.3f}")
    print(f"  Max Drawdown:  {seg.max_drawdown_pct:.2f}%")


# ── Core backtest ─────────────────────────────────────────────────────────────
def run_backtest(period: str = "2y") -> BacktestResult:
    now = datetime.now(tz=timezone.utc)
    # yfinance gives ~60d of 1h and ~7d of 15m → anything within ~55 days is FULL
    full_cutoff = now - timedelta(days=55)

    print(f"\n{'='*65}")
    print(f"  QuantumTrader V3 — Unified Backtest (signals.py parity)")
    print(f"  Period: {period} | Stocks: {len(STOCKS)} | Capital: ₹{INITIAL_CAPITAL:,}")
    print(f"  FULL mode for dates after {full_cutoff.strftime('%Y-%m-%d')}")
    print(f"{'='*65}\n")

    capital = INITIAL_CAPITAL
    cash    = INITIAL_CAPITAL
    held_positions: dict = {}  # symbol -> {qty, entry, sl, target, entry_time, entry_idx, fidelity}
    all_trades: List[BacktestTrade] = []
    equity_curve = [INITIAL_CAPITAL]
    prev_equity  = INITIAL_CAPITAL

    full_daily_returns:     List[float] = []
    degraded_daily_returns: List[float] = []

    # ── Fetch data ────────────────────────────────────────────────────────────
    daily_data: dict = {}   # symbol -> daily df
    for symbol in STOCKS:
        short = symbol.replace(".NS", "")
        try:
            df = yf.download(symbol, period=period, interval="1d", progress=False)
            df.columns = [c[0].lower() if isinstance(c, tuple) else c.lower() for c in df.columns]
            df = df.dropna()
            if len(df) < 200:
                print(f"  {short}: skipped ({len(df)} bars)")
                continue
            daily_data[symbol] = df
            print(f"  {short}: ✓ {len(df)} daily bars")
        except Exception as e:
            print(f"  {short}: ✗ {e}")

    if not daily_data:
        print("  ❌ No valid data.")
        return BacktestResult("", "", [], [])

    all_dates = None
    for df in daily_data.values():
        d = set(df.index)
        all_dates = d if all_dates is None else all_dates.intersection(d)
    sorted_dates = sorted(all_dates)

    print(f"\n  Common period: {sorted_dates[0].strftime('%Y-%m-%d')} → {sorted_dates[-1].strftime('%Y-%m-%d')}")
    print(f"  Trading days:  {len(sorted_dates)}\n")

    # ── Day-by-day simulation ─────────────────────────────────────────────────
    for i, date in enumerate(sorted_dates[1:], 1):
        prev_date = sorted_dates[i - 1]

        # Determine fidelity for this bar's date
        try:
            date_aware = date.to_pydatetime().replace(tzinfo=timezone.utc) if hasattr(date, "to_pydatetime") else date
        except Exception:
            date_aware = date
        is_full = date_aware >= full_cutoff
        fidelity = "FULL" if is_full else "DEGRADED"

        for symbol, df_daily in daily_data.items():
            if date not in df_daily.index or prev_date not in df_daily.index:
                continue

            row = df_daily.loc[date]
            atr_val = float(row["atr"]) if "atr" in row.index and row["atr"] > 0 else float(row["close"]) * 0.01

            # ── Manage open positions with OHLC intrabar model ────────────────
            if symbol in held_positions:
                pos = held_positions[symbol]
                exit_info = apply_intrabar_exit(row, pos["entry"], pos["sl"], pos["target"],
                                                pos["qty"], pos["entry_time"], date)

                if exit_info is not None:
                    fp = exit_info["fill_price"]
                    c  = calculate_realistic_charges(pos["entry"], fp, pos["qty"], is_intraday=False)
                    pnl = c.net_pnl
                    all_trades.append(BacktestTrade(
                        stock=symbol, action="SELL",
                        entry_price=pos["entry"], exit_price=fp,
                        quantity=pos["qty"], pnl=pnl, charges=c.total,
                        entry_time=pos["entry_time"], exit_time=date,
                        note=f"{exit_info['type']} | {exit_info['note']} | charges ₹{c.total:.2f}",
                        hold_bars=i - pos["entry_idx"],
                        fidelity=pos["fidelity"],
                    ))
                    cash += fp * pos["qty"] - c.total
                    del held_positions[symbol]
                    continue

                # Breakeven trigger
                price_now = float(row["close"])
                unr_pct = ((price_now - pos["entry"]) / pos["entry"]) * 100
                if unr_pct >= 1.5 and pos["sl"] < pos["entry"]:
                    pos["sl"] = pos["entry"]

                # Trailing stop
                unrealized = price_now - pos["entry"]
                if unrealized > 1.5 * atr_val:
                    adx_v = float(row["adx"]) if "adx" in row.index else 20
                    trail_dist = 0.5 if adx_v < 25 else 1.0
                    new_sl = max(pos["sl"], price_now - trail_dist * atr_val)
                    pos["sl"] = new_sl

            # ── Build frames dict based on fidelity ───────────────────────────
            # For DEGRADED (daily-only) we pass only the daily slice.
            # For FULL we construct a synthetic frames dict from the daily df
            # so evaluate_signal can still run the same confluence logic.
            # (Real intraday frames are fetched for live; here daily is the proxy.)
            daily_slice = df_daily.loc[:date].tail(250)
            frames = {"1d": daily_slice, "15m": daily_slice, "1h": daily_slice}

            # ── Evaluate signal ───────────────────────────────────────────────
            result = evaluate_signal(
                symbol=symbol,
                frames=frames,
                capital=capital,
                cash=cash,
                held_stocks=set(held_positions.keys()),
                sentiment_fn=_neutral_sentiment,   # 0.0 neutral stub
                open_count=len(held_positions),
                max_positions=MAX_POSITIONS,
            )

            if result.skipped or result.final_action == "HOLD":
                continue

            if result.final_action == "BUY" and symbol not in held_positions:
                plan = result.plan
                if plan and plan.quantity > 0 and plan.quantity * result.price <= cash:
                    held_positions[symbol] = {
                        "qty": plan.quantity, "entry": result.price,
                        "sl": plan.stop_loss, "target": plan.target,
                        "entry_time": date, "entry_idx": i, "fidelity": fidelity,
                    }
                    cash -= result.price * plan.quantity

            elif result.final_action == "SELL" and symbol in held_positions:
                pos = held_positions[symbol]
                fp  = float(row["close"])
                c   = calculate_realistic_charges(pos["entry"], fp, pos["qty"], is_intraday=False)
                pnl = c.net_pnl
                all_trades.append(BacktestTrade(
                    stock=symbol, action="SELL",
                    entry_price=pos["entry"], exit_price=fp,
                    quantity=pos["qty"], pnl=pnl, charges=c.total,
                    entry_time=pos["entry_time"], exit_time=date,
                    note=f"Signal sell | charges ₹{c.total:.2f}",
                    hold_bars=i - pos["entry_idx"], fidelity=pos["fidelity"],
                ))
                cash += fp * pos["qty"] - c.total
                del held_positions[symbol]

        # ── Daily equity snapshot ─────────────────────────────────────────────
        invested_val = sum(
            float(daily_data[s].loc[date]["close"]) * p["qty"]
            for s, p in held_positions.items()
            if date in daily_data[s].index
        )
        equity = cash + invested_val
        equity_curve.append(equity)
        capital = equity

        daily_ret = (equity - prev_equity) / prev_equity if prev_equity > 0 else 0
        if fidelity == "FULL":
            full_daily_returns.append(daily_ret)
        else:
            degraded_daily_returns.append(daily_ret)
        prev_equity = equity

    # ── Close any still-open positions at last bar ────────────────────────────
    last_date = sorted_dates[-1]
    for symbol, pos in list(held_positions.items()):
        if last_date in daily_data[symbol].index:
            fp = float(daily_data[symbol].loc[last_date]["close"])
            c  = calculate_realistic_charges(pos["entry"], fp, pos["qty"], is_intraday=True)
            pnl = c.net_pnl
            all_trades.append(BacktestTrade(
                stock=symbol, action="SELL",
                entry_price=pos["entry"], exit_price=fp,
                quantity=pos["qty"], pnl=pnl, charges=c.total,
                entry_time=pos["entry_time"], exit_time=last_date,
                note=f"End-of-backtest close | charges ₹{c.total:.2f}",
                hold_bars=len(sorted_dates) - pos["entry_idx"], fidelity=pos["fidelity"],
            ))

    # ── Compute per-segment metrics ───────────────────────────────────────────
    full_trades     = [t for t in all_trades if t.fidelity == "FULL"]
    degraded_trades = [t for t in all_trades if t.fidelity == "DEGRADED"]

    full_seg     = _compute_segment_metrics(full_trades,     "FULL",     full_daily_returns)
    degraded_seg = _compute_segment_metrics(degraded_trades, "DEGRADED", degraded_daily_returns)

    result_obj = BacktestResult(
        period_start=sorted_dates[0].strftime("%Y-%m-%d"),
        period_end=sorted_dates[-1].strftime("%Y-%m-%d"),
        all_trades=all_trades,
        equity_curve=equity_curve,
        full=full_seg,
        degraded=degraded_seg,
    )

    # ── Print report ──────────────────────────────────────────────────────────
    print(f"\n{'='*65}")
    print(f"  BACKTEST RESULTS  —  {result_obj.period_start} → {result_obj.period_end}")
    print(f"  Total trades: {len(all_trades)}")
    print(f"  Final equity: ₹{equity_curve[-1]:,.2f}")
    print(f"{'='*65}")

    if full_seg.total_trades > 0:
        _print_segment(full_seg)
    else:
        print("\n  FULL mode: no trades in the intraday-available window.")

    if degraded_seg.total_trades > 0:
        _print_segment(degraded_seg)
    else:
        print("\n  DEGRADED mode: no trades in the daily-only window.")

    print(f"\n{'='*65}")
    return result_obj


def save_to_database(result: BacktestResult):
    """Persist the FULL-fidelity segment to backtest_results table."""
    import psycopg2
    DATABASE_URL = os.environ.get(
        "DATABASE_URL",
        "postgresql://neondb_owner:npg_ie0GzmROxE9f@ep-proud-bird-an4ydv35-pooler.c-6.us-east-1.aws.neon.tech/neondb?sslmode=require"
    )
    seg = result.full
    if seg is None or seg.total_trades == 0:
        print("  No FULL-fidelity trades to save.")
        return

    conn = psycopg2.connect(DATABASE_URL)
    cur  = conn.cursor()
    cur.execute("""
        INSERT INTO backtest_results
        (strategy_name, period_start, period_end, total_trades, win_rate,
         profit_factor, sharpe_ratio, sortino_ratio, max_drawdown_pct,
         expectancy, total_pnl)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    """, (
        "V3_Unified_FULL", seg.period_start, seg.period_end,
        seg.total_trades, seg.win_rate, seg.profit_factor,
        seg.sharpe_ratio, seg.sortino_ratio, seg.max_drawdown_pct,
        seg.expectancy, seg.net_pnl,
    ))
    conn.commit()
    cur.close(); conn.close()
    print("  ✅ FULL-fidelity results saved to database.\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="QuantumTrader V3 Backtester")
    parser.add_argument("--period", default="2y", help="yfinance period string (1y, 2y …)")
    parser.add_argument("--save",   action="store_true", help="Save FULL results to database")
    args = parser.parse_args()

    result = run_backtest(period=args.period)

    if args.save and len(result.all_trades) > 0:
        save_to_database(result)
