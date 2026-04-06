import os
import psycopg2
import yfinance as yf
import pandas as pd
import pandas_ta as ta
from datetime import datetime
from news import get_news_sentiment

STOCKS = ["RELIANCE.NS", "TCS.NS", "INFY.NS", "HDFCBANK.NS", "ICICIBANK.NS"]
DATABASE_URL = os.environ["DATABASE_URL"]
CAPITAL = 100000
MAX_POSITIONS = 2
RISK_PCT = 0.02
SL_PCT = 0.015
TARGET_PCT = 0.03

def get_conn():
    return psycopg2.connect(DATABASE_URL)

def fetch_candles(symbol):
    df = yf.download(symbol, period="5d", interval="15m", progress=False)
    df.columns = [c[0].lower() if isinstance(c, tuple) else c.lower() for c in df.columns]
    return df.dropna()

def score_stock(df, symbol):
    score = 0
    reasons = []
    close = df["close"]

    # RSI
    rsi = ta.rsi(close, length=14)
    if rsi is not None and len(rsi.dropna()) > 0:
        r = rsi.dropna().iloc[-1]
        if r < 30:
            score += 2
            reasons.append(f"RSI oversold ({r:.1f}) +2")
        elif r > 70:
            score -= 2
            reasons.append(f"RSI overbought ({r:.1f}) -2")

    # MACD
    macd = ta.macd(close, fast=12, slow=26, signal=9)
    if macd is not None and len(macd.dropna()) > 1:
        macd_line = macd["MACD_12_26_9"].dropna()
        signal_line = macd["MACDs_12_26_9"].dropna()
        if len(macd_line) > 1:
            if macd_line.iloc[-1] > signal_line.iloc[-1] and macd_line.iloc[-2] <= signal_line.iloc[-2]:
                score += 2
                reasons.append("MACD bullish crossover +2")
            elif macd_line.iloc[-1] < signal_line.iloc[-1] and macd_line.iloc[-2] >= signal_line.iloc[-2]:
                score -= 2
                reasons.append("MACD bearish crossover -2")

    # EMA trend
    ema20 = ta.ema(close, length=20)
    ema50 = ta.ema(close, length=50)
    if ema20 is not None and ema50 is not None:
        e20 = ema20.dropna()
        e50 = ema50.dropna()
        if len(e20) > 0 and len(e50) > 0:
            if e20.iloc[-1] > e50.iloc[-1]:
                score += 1
                reasons.append("EMA20 > EMA50 (uptrend) +1")
            else:
                score -= 1
                reasons.append("EMA20 < EMA50 (downtrend) -1")

    # Volume spike
    vol = df["volume"]
    avg_vol = vol.rolling(20).mean()
    if len(avg_vol.dropna()) > 0 and avg_vol.iloc[-1] > 0:
        if vol.iloc[-1] > avg_vol.iloc[-1] * 1.5:
            score += 1
            reasons.append("Volume spike (1.5x avg) +1")

    # News sentiment
    sentiment = get_news_sentiment(symbol.replace(".NS", ""))
    if sentiment > 0:
        score += 1
        reasons.append("Positive news sentiment +1")
    elif sentiment < 0:
        score -= 1
        reasons.append("Negative news sentiment -1")

    return score, reasons

def get_action(score):
    if score >= 4:
        return "BUY"
    elif score <= -4:
        return "SELL"
    return "HOLD"

def run():
    conn = get_conn()
    cur = conn.cursor()

    # Check open positions
    cur.execute("SELECT COUNT(*) FROM open_positions")
    open_count = cur.fetchone()[0]

    cur.execute("SELECT capital, cash FROM portfolio ORDER BY updated_at DESC LIMIT 1")
    row = cur.fetchone()
    capital = float(row[0]) if row else CAPITAL
    cash = float(row[1]) if row else CAPITAL

    for symbol in STOCKS:
        try:
            df = fetch_candles(symbol)
            if len(df) < 60:
                continue

            score, reasons = score_stock(df, symbol)
            action = get_action(score)
            price = float(df["close"].iloc[-1])
            reason_str = " + ".join(reasons) + f" → score {score:+d} → {action}"

            if action == "BUY" and open_count < MAX_POSITIONS and cash > 0:
                risk_amount = capital * RISK_PCT
                quantity = int(risk_amount / (price * SL_PCT))
                if quantity > 0 and price * quantity <= cash:
                    sl = round(price * (1 - SL_PCT), 2)
                    target = round(price * (1 + TARGET_PCT), 2)
                    cur.execute("""
                        INSERT INTO open_positions (stock, quantity, entry_price, stop_loss, target, entry_time, reason)
                        VALUES (%s, %s, %s, %s, %s, NOW(), %s)
                        ON CONFLICT (stock) DO NOTHING
                    """, (symbol, quantity, price, sl, target, reason_str))
                    cur.execute("""
                        INSERT INTO trades (stock, action, entry_price, quantity, reason, entry_time, status)
                        VALUES (%s, 'BUY', %s, %s, %s, NOW(), 'OPEN')
                    """, (symbol, price, quantity, reason_str))
                    invested = price * quantity
                    cur.execute("""
                        UPDATE portfolio SET cash = cash - %s, invested = invested + %s, updated_at = NOW()
                    """, (invested, invested))
                    open_count += 1
                    cash -= invested

            elif action == "SELL":
                cur.execute("SELECT quantity, entry_price FROM open_positions WHERE stock = %s", (symbol,))
                pos = cur.fetchone()
                if pos:
                    qty, entry = int(pos[0]), float(pos[1])
                    pnl = (price - entry) * qty
                    cur.execute("""
                        UPDATE trades SET exit_price=%s, exit_time=NOW(), pnl=%s, status='CLOSED'
                        WHERE stock=%s AND status='OPEN'
                    """, (price, pnl, symbol))
                    cur.execute("DELETE FROM open_positions WHERE stock = %s", (symbol,))
                    proceeds = price * qty
                    cur.execute("""
                        UPDATE portfolio SET cash = cash + %s, invested = invested - %s, updated_at = NOW()
                    """, (proceeds, entry * qty))
                    open_count -= 1
                    cash += proceeds

            # Always log the signal
            cur.execute("""
                INSERT INTO trades (stock, action, entry_price, quantity, reason, entry_time, status)
                VALUES (%s, %s, %s, 0, %s, NOW(), 'CLOSED')
            """, (symbol, action if action == "HOLD" else action, price, reason_str))

        except Exception as e:
            print(f"Error processing {symbol}: {e}")
            continue

    # Snapshot equity
    cur.execute("SELECT cash, invested FROM portfolio ORDER BY updated_at DESC LIMIT 1")
    row = cur.fetchone()
    if row:
        c, i = float(row[0]), float(row[1])
        cur.execute("""
            INSERT INTO equity_snapshots (capital, cash, invested) VALUES (%s, %s, %s)
        """, (c + i, c, i))

    conn.commit()
    cur.close()
    conn.close()
    print(f"Done at {datetime.now()}")

if __name__ == "__main__":
    run()
