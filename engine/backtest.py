"""
QuantumTrader V2 — Backtesting Framework
==========================================
Simulates the V2 strategy on historical data to measure:
  - Sharpe Ratio, Sortino Ratio
  - Max Drawdown
  - Win Rate, Profit Factor
  - Expectancy (expected ₹ per trade)

Usage:
  python engine/backtest.py              # Run backtest and print results
  python engine/backtest.py --save       # Run and save results to database
"""

import os
import sys
import math
import argparse
import yfinance as yf
import pandas as pd
import pandas_ta as ta
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from typing import List, Optional

# Add engine dir to path for imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from charges import calculate_charges

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
INITIAL_CAPITAL = 100000
RISK_PER_TRADE = 0.02
ATR_SL_MULT = 2.0
ATR_TP_MULT = 4.0    # V2.1: widened from 3x → 4x ATR for better RR
MAX_POSITIONS = 3


@dataclass
class BacktestTrade:
    stock: str
    action: str
    entry_price: float
    exit_price: float
    quantity: int
    pnl: float
    entry_time: datetime
    exit_time: datetime
    reason: str
    hold_bars: int = 0


@dataclass
class BacktestResult:
    strategy_name: str
    period_start: str
    period_end: str
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate: float
    gross_profit: float
    gross_loss: float
    net_pnl: float
    total_charges: float        # NEW: sum of all transaction costs
    profit_factor: float
    sharpe_ratio: float
    sortino_ratio: float
    max_drawdown_pct: float
    avg_win: float
    avg_loss: float
    avg_rr_ratio: float
    expectancy: float
    equity_curve: List[float] = field(default_factory=list)
    trades: List[BacktestTrade] = field(default_factory=list)


def fetch_historical(symbol: str, period: str = "2y") -> pd.DataFrame:
    """Download historical daily data for backtesting."""
    df = yf.download(symbol, period=period, interval="1d", progress=False)
    df.columns = [c[0].lower() if isinstance(c, tuple) else c.lower() for c in df.columns]
    return df.dropna()


def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Compute all technical indicators needed for the strategy."""
    close = df["close"]
    high = df["high"]
    low = df["low"]

    # EMAs
    df["ema9"] = ta.ema(close, length=9)
    df["ema21"] = ta.ema(close, length=21)
    df["ema50"] = ta.ema(close, length=50)
    df["ema200"] = ta.ema(close, length=200)

    # RSI
    df["rsi"] = ta.rsi(close, length=14)

    # MACD
    macd = ta.macd(close, fast=12, slow=26, signal=9)
    if macd is not None:
        df["macd"] = macd.get("MACD_12_26_9")
        df["macd_signal"] = macd.get("MACDs_12_26_9")
        df["macd_hist"] = macd.get("MACDh_12_26_9")

    # ADX
    adx = ta.adx(high, low, close, length=14)
    if adx is not None:
        df["adx"] = adx.get("ADX_14")

    # ATR
    df["atr"] = ta.atr(high, low, close, length=14)

    # Volume average
    df["vol_avg"] = df["volume"].rolling(20).mean()

    return df.dropna()


def score_bar(row, prev_row) -> tuple:
    """Score a single bar using V2.1 confluence logic."""
    score = 0
    reasons = []

    # ADX check: skip RANGING regime entirely
    adx_val = float(row.get("adx", 20)) if hasattr(row, "get") else float(row["adx"]) if "adx" in row.index else 20
    if adx_val < 20:
        return 0, "HOLD", ["RANGING regime — skipped"]

    # Daily trend (EMA 50/200)
    if row["ema50"] > row["ema200"]:
        score += 1
        reasons.append("EMA50>200")
    else:
        score -= 1
        reasons.append("EMA50<200")

    # RSI
    if row["rsi"] < 35:
        score += 1
        reasons.append(f"RSI oversold({row['rsi']:.0f})")
    elif row["rsi"] > 65:
        score -= 1
        reasons.append(f"RSI overbought({row['rsi']:.0f})")

    # MACD crossover
    if "macd" in row.index and "macd" in prev_row.index:
        if row["macd"] > row["macd_signal"] and prev_row["macd"] <= prev_row["macd_signal"]:
            score += 1
            reasons.append("MACD bullish cross")
        elif row["macd"] < row["macd_signal"] and prev_row["macd"] >= prev_row["macd_signal"]:
            score -= 1
            reasons.append("MACD bearish cross")

    # EMA 9/21 trend
    if row["ema9"] > row["ema21"]:
        score += 1
        reasons.append("EMA9>21")
    else:
        score -= 1
        reasons.append("EMA9<21")

    # Volume spike
    if row["vol_avg"] > 0 and row["volume"] > row["vol_avg"] * 1.5:
        score += 1
        reasons.append("Vol spike")

    # V2.1: Tightened threshold — require full 3-point confluence
    if score >= 3:
        action = "BUY"
    elif score <= -3:
        action = "SELL"
    else:
        action = "HOLD"

    return score, action, reasons


def run_backtest(period: str = "2y") -> BacktestResult:
    """Run the full backtest across all stocks."""
    print(f"\n{'='*60}")
    print(f"  QuantumTrader V2 — Backtest Engine")
    print(f"  Period: {period} | Stocks: {len(STOCKS)} | Capital: ₹{INITIAL_CAPITAL:,}")
    print(f"{'='*60}\n")

    capital = INITIAL_CAPITAL
    cash = INITIAL_CAPITAL
    positions = {}  # stock -> {qty, entry, sl, target, entry_time, entry_idx}
    all_trades: List[BacktestTrade] = []
    equity_curve = [INITIAL_CAPITAL]
    peak_equity = INITIAL_CAPITAL
    max_dd = 0.0
    daily_returns = []
    total_charges_paid = 0.0   # running tally of all transaction costs

    # Fetch and prepare data for all stocks
    stock_data = {}
    for symbol in STOCKS:
        short = symbol.replace(".NS", "")
        print(f"  Fetching {short}...", end=" ")
        df = fetch_historical(symbol, period)
        if len(df) < 200:
            print(f"skipped (only {len(df)} bars)")
            continue
        df = compute_indicators(df)
        stock_data[symbol] = df
        print(f"✓ {len(df)} bars")

    if not stock_data:
        print("  ❌ No valid data found.")
        return BacktestResult("V2_Confluence", "", "", 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0)

    # Get the common date range
    all_dates = None
    for df in stock_data.values():
        dates = set(df.index)
        all_dates = dates if all_dates is None else all_dates.intersection(dates)
    
    sorted_dates = sorted(all_dates)
    print(f"\n  Backtest period: {sorted_dates[0].strftime('%Y-%m-%d')} → {sorted_dates[-1].strftime('%Y-%m-%d')}")
    print(f"  Trading days: {len(sorted_dates)}\n")

    # Simulate day by day
    prev_equity = INITIAL_CAPITAL
    for i, date in enumerate(sorted_dates[1:], 1):
        prev_date = sorted_dates[i - 1]

        for symbol, df in stock_data.items():
            if date not in df.index or prev_date not in df.index:
                continue

            row = df.loc[date]
            prev_row = df.loc[prev_date]
            price = float(row["close"])
            atr = float(row["atr"]) if row["atr"] > 0 else price * 0.01

            # Check existing positions for SL/TP/trailing
            if symbol in positions:
                pos = positions[symbol]
                # Stop loss hit
                if float(row["low"]) <= pos["sl"]:
                    exit_price = pos["sl"]
                    c = calculate_charges(pos["entry"], exit_price, pos["qty"], is_intraday=True)
                    pnl = c.net_pnl
                    all_trades.append(BacktestTrade(
                        symbol, "SELL", pos["entry"], exit_price, pos["qty"],
                        pnl, pos["entry_time"], date,
                        f"Stop loss hit | charges ₹{c.total:.2f}",
                        hold_bars=i - pos["entry_idx"]
                    ))
                    cash += exit_price * pos["qty"] - c.total
                    total_charges_paid += c.total
                    del positions[symbol]
                    continue

                # Target hit
                if float(row["high"]) >= pos["target"]:
                    exit_price = pos["target"]
                    c = calculate_charges(pos["entry"], exit_price, pos["qty"], is_intraday=True)
                    pnl = c.net_pnl
                    all_trades.append(BacktestTrade(
                        symbol, "SELL", pos["entry"], exit_price, pos["qty"],
                        pnl, pos["entry_time"], date,
                        f"Target hit | charges ₹{c.total:.2f}",
                        hold_bars=i - pos["entry_idx"]
                    ))
                    cash += exit_price * pos["qty"] - c.total
                    total_charges_paid += c.total
                    del positions[symbol]
                    continue

                # Trailing stop: if profit > 1.5*ATR, trail by 1*ATR
                unrealized = price - pos["entry"]
                if unrealized > 1.5 * atr:
                    new_sl = max(pos["sl"], price - atr)
                    pos["sl"] = new_sl

            # Generate signal
            score, action, reasons = score_bar(row, prev_row)

            if action == "BUY" and symbol not in positions and len(positions) < MAX_POSITIONS:
                sl_dist = ATR_SL_MULT * atr
                sl = round(price - sl_dist, 2)
                target = round(price + ATR_TP_MULT * atr, 2)
                risk_amt = capital * RISK_PER_TRADE
                qty = int(risk_amt / sl_dist)

                if qty > 0 and price * qty <= cash:
                    positions[symbol] = {
                        "qty": qty, "entry": price, "sl": sl,
                        "target": target, "entry_time": date, "entry_idx": i
                    }
                    cash -= price * qty

            elif action == "SELL" and symbol in positions:
                pos = positions[symbol]
                c = calculate_charges(pos["entry"], price, pos["qty"], is_intraday=True)
                pnl = c.net_pnl
                all_trades.append(BacktestTrade(
                    symbol, "SELL", pos["entry"], price, pos["qty"],
                    pnl, pos["entry_time"], date,
                    f"Signal sell | charges ₹{c.total:.2f}",
                    hold_bars=i - pos["entry_idx"]
                ))
                cash += price * pos["qty"] - c.total
                total_charges_paid += c.total
                del positions[symbol]

        # Calculate daily equity
        invested_value = sum(
            float(stock_data[s].loc[date]["close"]) * p["qty"]
            for s, p in positions.items()
            if date in stock_data[s].index
        )
        equity = cash + invested_value
        equity_curve.append(equity)

        # Track drawdown
        if equity > peak_equity:
            peak_equity = equity
        dd = ((peak_equity - equity) / peak_equity) * 100
        max_dd = max(max_dd, dd)

        # Daily return
        daily_ret = (equity - prev_equity) / prev_equity if prev_equity > 0 else 0
        daily_returns.append(daily_ret)
        prev_equity = equity

    # Close any remaining positions at last price
    last_date = sorted_dates[-1]
    for symbol, pos in list(positions.items()):
        if last_date in stock_data[symbol].index:
            price = float(stock_data[symbol].loc[last_date]["close"])
            c = calculate_charges(pos["entry"], price, pos["qty"], is_intraday=True)
            pnl = c.net_pnl
            total_charges_paid += c.total
            all_trades.append(BacktestTrade(
                symbol, "SELL", pos["entry"], price, pos["qty"],
                pnl, pos["entry_time"], last_date,
                f"End of backtest | charges ₹{c.total:.2f}",
                hold_bars=len(sorted_dates) - pos["entry_idx"]
            ))

    # ─── Compute metrics ──────────────────────────────────────
    wins = [t for t in all_trades if t.pnl > 0]
    losses = [t for t in all_trades if t.pnl <= 0]
    total = len(all_trades)
    win_rate = (len(wins) / total * 100) if total > 0 else 0
    gross_profit = sum(t.pnl for t in wins)
    gross_loss = abs(sum(t.pnl for t in losses))
    net_pnl = gross_profit - gross_loss
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else float('inf')
    avg_win = (gross_profit / len(wins)) if wins else 0
    avg_loss = (gross_loss / len(losses)) if losses else 0
    avg_rr = (avg_win / avg_loss) if avg_loss > 0 else float('inf')
    expectancy = (win_rate / 100 * avg_win) - ((1 - win_rate / 100) * avg_loss)

    # Sharpe & Sortino (annualized, 252 trading days)
    if daily_returns and len(daily_returns) > 1:
        mean_ret = sum(daily_returns) / len(daily_returns)
        variance = sum((r - mean_ret) ** 2 for r in daily_returns) / (len(daily_returns) - 1)
        std_ret = math.sqrt(variance) if variance > 0 else 0.0001
        sharpe = (mean_ret / std_ret) * math.sqrt(252) if std_ret > 0 else 0

        downside = [r for r in daily_returns if r < 0]
        down_var = sum(r ** 2 for r in downside) / len(downside) if downside else 0.0001
        down_std = math.sqrt(down_var)
        sortino = (mean_ret / down_std) * math.sqrt(252) if down_std > 0 else 0
    else:
        sharpe, sortino = 0, 0

    result = BacktestResult(
        strategy_name="V2.1_MultiTF_Confluence",
        period_start=sorted_dates[0].strftime("%Y-%m-%d"),
        period_end=sorted_dates[-1].strftime("%Y-%m-%d"),
        total_trades=total,
        winning_trades=len(wins),
        losing_trades=len(losses),
        win_rate=round(win_rate, 2),
        gross_profit=round(gross_profit, 2),
        gross_loss=round(gross_loss, 2),
        net_pnl=round(net_pnl, 2),
        total_charges=round(total_charges_paid, 2),
        profit_factor=round(profit_factor, 2),
        sharpe_ratio=round(sharpe, 3),
        sortino_ratio=round(sortino, 3),
        max_drawdown_pct=round(max_dd, 2),
        avg_win=round(avg_win, 2),
        avg_loss=round(avg_loss, 2),
        avg_rr_ratio=round(avg_rr, 2),
        expectancy=round(expectancy, 2),
        equity_curve=equity_curve,
        trades=all_trades,
    )

    # ─── Print report ─────────────────────────────────────────
    print(f"\n{'='*60}")
    print(f"  BACKTEST RESULTS (with real transaction charges)")
    print(f"{'='*60}")
    print(f"  Period:          {result.period_start} → {result.period_end}")
    print(f"  Total Trades:    {total}")
    print(f"  Win Rate:        {win_rate:.1f}% ({len(wins)}W / {len(losses)}L)")
    print(f"  Net P&L:         ₹{net_pnl:+,.2f} ({net_pnl/INITIAL_CAPITAL*100:+.2f}%)")
    print(f"  Total Charges:   ₹{total_charges_paid:,.2f} ({total_charges_paid/INITIAL_CAPITAL*100:.3f}% of capital)")
    print(f"  Charges/Trade:   ₹{total_charges_paid/total:.2f} avg" if total else "")
    print(f"  Profit Factor:   {profit_factor:.2f}")
    print(f"  Avg Win:         ₹{avg_win:,.2f}")
    print(f"  Avg Loss:        ₹{avg_loss:,.2f}")
    print(f"  Avg RR Ratio:    {avg_rr:.2f}")
    print(f"  Expectancy:      ₹{expectancy:+,.2f} per trade")
    print(f"  ─────────────────────────────────")
    print(f"  Sharpe Ratio:    {sharpe:.3f}")
    print(f"  Sortino Ratio:   {sortino:.3f}")
    print(f"  Max Drawdown:    {max_dd:.2f}%")
    print(f"  Final Equity:    ₹{equity_curve[-1]:,.2f}")
    print(f"{'='*60}\n")

    # Grade the strategy
    if sharpe > 2:
        grade = "🏆 EXCELLENT — Strong edge detected"
    elif sharpe > 1:
        grade = "✅ GOOD — Viable strategy with positive edge"
    elif sharpe > 0.5:
        grade = "⚠️ MEDIOCRE — Marginal edge, needs improvement"
    elif sharpe > 0:
        grade = "🟡 WEAK — Barely positive, high risk of failure"
    else:
        grade = "❌ NEGATIVE — This strategy loses money. Do NOT trade real capital."

    print(f"  Verdict: {grade}\n")

    return result


def save_to_database(result: BacktestResult):
    """Save backtest results to the database."""
    import psycopg2
    DATABASE_URL = os.environ.get(
        "DATABASE_URL",
        "postgresql://neondb_owner:npg_ie0GzmROxE9f@ep-proud-bird-an4ydv35-pooler.c-6.us-east-1.aws.neon.tech/neondb?sslmode=require"
    )
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO backtest_results 
        (strategy_name, period_start, period_end, total_trades, win_rate,
         profit_factor, sharpe_ratio, sortino_ratio, max_drawdown_pct, 
         expectancy, total_pnl)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    """, (
        result.strategy_name, result.period_start, result.period_end,
        result.total_trades, result.win_rate, result.profit_factor,
        result.sharpe_ratio, result.sortino_ratio, result.max_drawdown_pct,
        result.expectancy, result.net_pnl
    ))
    conn.commit()
    cur.close()
    conn.close()
    print("  ✅ Results saved to database.\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="QuantumTrader V2 Backtester")
    parser.add_argument("--period", default="2y", help="Backtest period (e.g. 1y, 2y, 5y)")
    parser.add_argument("--save", action="store_true", help="Save results to database")
    args = parser.parse_args()

    result = run_backtest(period=args.period)

    if args.save and result.total_trades > 0:
        save_to_database(result)
