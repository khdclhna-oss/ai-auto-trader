"""
Multi-Timeframe Confluence Analyzer
====================================
Analyzes Daily, Hourly, and 15-minute charts simultaneously.
A trade signal only fires when ALL timeframes agree on direction.

Daily  → Sets the trend direction (EMA 50/200)
Hourly → Confirms momentum (RSI, MACD histogram slope)
15-min → Precision entry timing (EMA 9/21 crossover + volume)
"""

import yfinance as yf
import pandas as pd
import pandas_ta as ta
from dataclasses import dataclass
from typing import Optional


@dataclass
class TimeframeBias:
    """Result of analyzing a single timeframe."""
    timeframe: str
    direction: int  # +1 bullish, -1 bearish, 0 neutral
    strength: float  # 0.0 to 1.0
    reasons: list
    indicators: dict  # raw indicator values for logging


@dataclass
class ConfluenceResult:
    """Combined result across all timeframes."""
    stock: str
    daily: TimeframeBias
    hourly: TimeframeBias
    quarter: TimeframeBias  # 15-min
    confluence_score: int  # -3 to +3
    action: str  # BUY, SELL, HOLD
    regime: str
    reasons: list


def fetch_multi_timeframe(symbol: str) -> dict:
    """Download candle data for all three timeframes."""
    frames = {}
    try:
        df_d = yf.download(symbol, period="2y", interval="1d", progress=False)
        df_d.columns = [c[0].lower() if isinstance(c, tuple) else c.lower() for c in df_d.columns]
        frames["1d"] = df_d.dropna()
    except Exception:
        frames["1d"] = pd.DataFrame()

    try:
        df_h = yf.download(symbol, period="1mo", interval="1h", progress=False)
        df_h.columns = [c[0].lower() if isinstance(c, tuple) else c.lower() for c in df_h.columns]
        frames["1h"] = df_h.dropna()
    except Exception:
        frames["1h"] = pd.DataFrame()

    try:
        df_15 = yf.download(symbol, period="5d", interval="15m", progress=False)
        df_15.columns = [c[0].lower() if isinstance(c, tuple) else c.lower() for c in df_15.columns]
        frames["15m"] = df_15.dropna()
    except Exception:
        frames["15m"] = pd.DataFrame()

    return frames


def analyze_daily(df: pd.DataFrame) -> TimeframeBias:
    """
    Daily chart: trend direction via EMA 50/200 and ADX.
    This is the "big picture" — we never fight the daily trend.
    """
    if len(df) < 200:
        return TimeframeBias("1d", 0, 0.0, ["Insufficient daily data"], {})

    close = df["close"]
    direction = 0
    strength = 0.0
    reasons = []
    indicators = {}

    # EMA 50/200 — the gold standard of trend detection
    ema50 = ta.ema(close, length=50)
    ema200 = ta.ema(close, length=200)
    if ema50 is not None and ema200 is not None:
        e50 = ema50.iloc[-1]
        e200 = ema200.iloc[-1]
        indicators["ema50"] = float(e50)
        indicators["ema200"] = float(e200)

        if e50 > e200:
            direction += 1
            pct_above = ((e50 - e200) / e200) * 100
            strength = min(pct_above / 5.0, 1.0)  # normalize
            reasons.append(f"Daily EMA50 > EMA200 (golden cross, {pct_above:.1f}% above)")
        else:
            direction -= 1
            pct_below = ((e200 - e50) / e200) * 100
            strength = min(pct_below / 5.0, 1.0)
            reasons.append(f"Daily EMA50 < EMA200 (death cross, {pct_below:.1f}% below)")

    # ADX — trend strength (used by regime detector too)
    adx_data = ta.adx(df["high"], df["low"], close, length=14)
    if adx_data is not None and len(adx_data.dropna()) > 0:
        adx_val = float(adx_data["ADX_14"].dropna().iloc[-1])
        indicators["adx"] = adx_val
        if adx_val > 25:
            strength = min(strength + 0.3, 1.0)
            reasons.append(f"Strong trend (ADX: {adx_val:.1f})")
        elif adx_val < 20:
            strength *= 0.5  # weaken signal in ranging market
            reasons.append(f"Weak trend (ADX: {adx_val:.1f})")

    return TimeframeBias("1d", direction, strength, reasons, indicators)


def analyze_hourly(df: pd.DataFrame) -> TimeframeBias:
    """
    Hourly chart: momentum confirmation via RSI and MACD histogram.
    Confirms whether the daily trend has active momentum behind it.
    """
    if len(df) < 50:
        return TimeframeBias("1h", 0, 0.0, ["Insufficient hourly data"], {})

    close = df["close"]
    direction = 0
    strength = 0.0
    reasons = []
    indicators = {}

    # RSI — momentum gauge
    rsi = ta.rsi(close, length=14)
    if rsi is not None and len(rsi.dropna()) > 0:
        r = float(rsi.dropna().iloc[-1])
        indicators["rsi"] = r
        if r < 30:
            direction += 1
            strength += 0.5
            reasons.append(f"RSI oversold ({r:.1f}) — reversal likely")
        elif r > 70:
            direction -= 1
            strength += 0.5
            reasons.append(f"RSI overbought ({r:.1f}) — reversal likely")
        elif 40 < r < 60:
            reasons.append(f"RSI neutral ({r:.1f})")
        elif r >= 50:
            direction += 1
            strength += 0.2
            reasons.append(f"RSI bullish ({r:.1f})")
        else:
            direction -= 1
            strength += 0.2
            reasons.append(f"RSI bearish ({r:.1f})")

    # MACD histogram slope — is momentum accelerating?
    macd = ta.macd(close, fast=12, slow=26, signal=9)
    if macd is not None and len(macd.dropna()) > 2:
        hist = macd["MACDh_12_26_9"].dropna()
        if len(hist) >= 3:
            slope = float(hist.iloc[-1]) - float(hist.iloc[-3])
            indicators["macd_hist"] = float(hist.iloc[-1])
            indicators["macd_slope"] = slope
            if slope > 0 and hist.iloc[-1] > 0:
                direction += 1
                strength += 0.3
                reasons.append(f"MACD histogram accelerating bullish")
            elif slope < 0 and hist.iloc[-1] < 0:
                direction -= 1
                strength += 0.3
                reasons.append(f"MACD histogram accelerating bearish")

    # Volatility Squeeze (Bollinger within Keltner)
    try:
        sma20 = close.rolling(20).mean()
        std20 = close.rolling(20).std()
        bbu = sma20 + 2 * std20
        bbl = sma20 - 2 * std20
        
        atr20 = ta.atr(df["high"], df["low"], close, length=20)
        ema20 = ta.ema(close, length=20)
        
        if atr20 is not None and ema20 is not None and len(bbu.dropna()) > 1:
            curr_bbu = bbu.iloc[-1]
            curr_bbl = bbl.iloc[-1]
            curr_kcu = ema20.iloc[-1] + 1.5 * atr20.iloc[-1]
            curr_kcl = ema20.iloc[-1] - 1.5 * atr20.iloc[-1]
            
            prev_bbu = bbu.iloc[-2]
            prev_kcu = ema20.iloc[-2] + 1.5 * atr20.iloc[-2]
            
            if (curr_bbu < curr_kcu) and (curr_bbl > curr_kcl): # Squeeze ON
                direction -= 1
                reasons.append("Volatility squeeze ON (waiting)")
            elif (prev_bbu < prev_kcu) and (curr_bbu >= curr_kcu): # Breakout
                direction += 1
                strength += 0.5
                reasons.append("Volatility squeeze FIRED (Bullish breakout)")
    except Exception:
        pass

    return TimeframeBias("1h", max(-1, min(1, direction)), min(strength, 1.0), reasons, indicators)


def analyze_15min(df: pd.DataFrame) -> TimeframeBias:
    """
    15-minute chart: precision entry via EMA 9/21 crossover and volume spike.
    This is the trigger — we only enter when the micro-trend aligns.
    """
    if len(df) < 30:
        return TimeframeBias("15m", 0, 0.0, ["Insufficient 15m data"], {})

    close = df["close"]
    direction = 0
    strength = 0.0
    reasons = []
    indicators = {}

    # EMA 9/21 — fast crossover for entries
    ema9 = ta.ema(close, length=9)
    ema21 = ta.ema(close, length=21)
    if ema9 is not None and ema21 is not None and len(ema9.dropna()) > 1:
        curr_9, prev_9 = float(ema9.iloc[-1]), float(ema9.iloc[-2])
        curr_21, prev_21 = float(ema21.iloc[-1]), float(ema21.iloc[-2])
        indicators["ema9"] = curr_9
        indicators["ema21"] = curr_21

        if curr_9 > curr_21 and prev_9 <= prev_21:
            direction += 1
            strength += 0.5
            reasons.append("EMA9/21 bullish crossover (entry trigger)")
        elif curr_9 < curr_21 and prev_9 >= prev_21:
            direction -= 1
            strength += 0.5
            reasons.append("EMA9/21 bearish crossover (entry trigger)")
        elif curr_9 > curr_21:
            direction += 1
            strength += 0.2
            reasons.append("EMA9 > EMA21 (micro-uptrend)")
        else:
            direction -= 1
            strength += 0.2
            reasons.append("EMA9 < EMA21 (micro-downtrend)")

    # Volume confirmation
    if "volume" in df.columns:
        vol = df["volume"]
        avg_vol = vol.rolling(20).mean()
        if len(avg_vol.dropna()) > 0 and avg_vol.iloc[-1] > 0:
            vol_ratio = float(vol.iloc[-1]) / float(avg_vol.iloc[-1])
            indicators["vol_ratio"] = vol_ratio
            if vol_ratio > 1.5:
                strength += 0.3
                reasons.append(f"Volume spike ({vol_ratio:.1f}x average)")
            elif vol_ratio < 0.5:
                strength *= 0.5  # low volume = weak signal
                reasons.append(f"Low volume ({vol_ratio:.1f}x average)")

    # VWAP Institutional Anchoring
    if "volume" in df.columns:
        try:
            vwap_series = ta.vwap(df["high"], df["low"], df["close"], df["volume"])
            if vwap_series is not None and len(vwap_series.dropna()) > 1:
                curr_vwap = float(vwap_series.iloc[-1])
                prev_vwap = float(vwap_series.iloc[-2])
                curr_close = float(close.iloc[-1])
                prev_close = float(close.iloc[-2])
                indicators["vwap"] = curr_vwap

                pct_from_vwap = (curr_close - curr_vwap) / curr_vwap * 100

                if curr_close > curr_vwap and prev_close <= prev_vwap:
                    direction += 1
                    strength += 0.2
                    reasons.append(f"VWAP Breakout (VWAP: {curr_vwap:.2f})")
                elif curr_close > curr_vwap and pct_from_vwap < 1.0:
                    strength += 0.1
                    reasons.append(f"Price near VWAP (+{pct_from_vwap:.2f}%)")
                elif curr_close > curr_vwap and pct_from_vwap >= 1.0:
                    direction -= 1  # punish, institutions taking profit here
                    reasons.append(f"Price extended above VWAP (+{pct_from_vwap:.2f}%)")
                elif curr_close < curr_vwap:
                    direction -= 1
                    reasons.append(f"Price below VWAP (-{abs(pct_from_vwap):.2f}%)")
        except Exception:
            pass

    return TimeframeBias("15m", max(-1, min(1, direction)), min(strength, 1.0), reasons, indicators)


def get_confluence(stock: str, frames: dict, regime: str) -> ConfluenceResult:
    """
    Combine all three timeframes into a single confluence score.
    Range: -3 (all bearish) to +3 (all bullish).
    Action thresholds are adjusted based on market regime.
    """
    daily = analyze_daily(frames.get("1d", pd.DataFrame()))
    hourly = analyze_hourly(frames.get("1h", pd.DataFrame()))
    quarter = analyze_15min(frames.get("15m", pd.DataFrame()))

    # Time-of-Day Chop Filter (11:30 to 13:30 IST)
    df_15 = frames.get("15m", pd.DataFrame())
    time_penalty = 0
    chop_reason = None
    if not df_15.empty:
        try:
            last_time = df_15.index[-1]
            hour = last_time.hour
            minute = last_time.minute
            time_in_mins = hour * 60 + minute
            if 11 * 60 + 30 <= time_in_mins <= 13 * 60 + 30:
                time_penalty = -1
                chop_reason = "Mid-day Chop Filter (11:30-13:30) active"
        except Exception:
            pass

    confluence = daily.direction + hourly.direction + quarter.direction + time_penalty
    all_reasons = daily.reasons + hourly.reasons + quarter.reasons
    
    if chop_reason:
        all_reasons.append(chop_reason)

    # Regime-adaptive thresholds
    # V2.1: RANGING regime = no trades. Momentum strategies need a trend.
    if regime == "RANGING":
        # Don't trade in directionless markets — edge disappears
        return ConfluenceResult(
            stock=stock, daily=daily, hourly=hourly, quarter=quarter,
            confluence_score=confluence, action="HOLD",
            regime=regime, reasons=all_reasons + ["RANGING regime — no trades"],
        )

    # TRENDING or VOLATILE: require all 3 timeframes aligned (score ≥ 3)
    buy_threshold = 3    # All 3 TFs must be bullish
    sell_threshold = -3  # All 3 TFs must be bearish

    if confluence >= buy_threshold:
        action = "BUY"
    elif confluence <= sell_threshold:
        action = "SELL"
    else:
        action = "HOLD"

    return ConfluenceResult(
        stock=stock,
        daily=daily,
        hourly=hourly,
        quarter=quarter,
        confluence_score=confluence,
        action=action,
        regime=regime,
        reasons=all_reasons,
    )
