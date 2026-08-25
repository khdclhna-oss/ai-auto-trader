"""
Multi-Timeframe Confluence Analyzer
====================================
Analyzes Daily, Hourly, and 15-minute charts simultaneously.
A trade signal only fires when ALL timeframes agree on direction.

Daily  → Sets the trend direction (EMA 50/200) or Range Position (Bollinger Bands)
Hourly → Confirms momentum (RSI, MACD histogram slope, Volatility Squeeze)
15-min → Precision entry timing (EMA 9/21 crossover, Volume, VWAP anchoring)
"""

import yfinance as yf
import pandas as pd
import pandas_ta as ta
import numpy as np
from dataclasses import dataclass
from typing import Optional


@dataclass
class TimeframeBias:
    """Result of analyzing a single timeframe."""
    timeframe: str
    direction: int  # +1 bullish, -1 bearish, 0 neutral, -99 hard reject
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
    confluence_score: int  # -3 to +5
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


def analyze_daily(df: pd.DataFrame, regime: str = "TRENDING") -> TimeframeBias:
    """
    Daily chart analysis:
    - TRENDING/VOLATILE: Golden cross (EMA 50/200) and ADX > 25.
    - RANGING: Range position / Bollinger Bands support & resistance.
    """
    if df is None or len(df) < 50:
        return TimeframeBias("1d", 0, 0.0, ["Insufficient daily data"], {})

    df = df.copy()
    df.columns = [c[0].lower() if isinstance(c, tuple) else str(c).lower() for c in df.columns]
    if "close" not in df.columns:
        return TimeframeBias("1d", 0, 0.0, ["Missing 'close' column"], {})

    close = df["close"].astype(float)
    direction = 0
    strength = 0.0
    reasons = []
    indicators = {}

    if regime == "RANGING":
        # In RANGING regime, evaluate price position within range / Bollinger Bands
        sma20 = float(close.tail(20).mean())
        std20 = float(close.tail(20).std())
        lower_bb = sma20 - (2.0 * std20)
        upper_bb = sma20 + (2.0 * std20)
        curr_p = float(close.iloc[-1])
        bb_denom = upper_bb - lower_bb
        pct_b = (curr_p - lower_bb) / bb_denom if bb_denom > 0 else 0.5

        indicators["pct_b"] = pct_b
        indicators["lower_bb"] = lower_bb
        indicators["upper_bb"] = upper_bb

        if curr_p <= lower_bb or pct_b < 0.15:
            direction += 1
            strength += 0.5
            reasons.append(f"Daily price oversold near range support/lower BB (pct_b: {pct_b:.2f})")
        elif curr_p >= upper_bb or pct_b > 0.85:
            direction -= 1
            strength += 0.5
            reasons.append(f"Daily price overbought near range resistance/upper BB (pct_b: {pct_b:.2f})")
        else:
            reasons.append(f"Daily range neutral (pct_b: {pct_b:.2f})")
    else:
        # TRENDING or VOLATILE regime: evaluate macro trend via EMA 50/200 & ADX
        if len(df) >= 200:
            ema50 = ta.ema(close, length=50)
            ema200 = ta.ema(close, length=200)
            if ema50 is not None and ema200 is not None and len(ema50.dropna()) > 0 and len(ema200.dropna()) > 0:
                e50 = float(ema50.dropna().iloc[-1])
                e200 = float(ema200.dropna().iloc[-1])
                indicators["ema50"] = e50
                indicators["ema200"] = e200

                if e50 > e200:
                    direction += 1
                    pct_above = ((e50 - e200) / e200) * 100
                    strength = min(pct_above / 5.0, 1.0)
                    reasons.append(f"Daily EMA50 > EMA200 (golden cross, {pct_above:.1f}% above)")
                else:
                    direction -= 1
                    pct_below = ((e200 - e50) / e200) * 100
                    strength = min(pct_below / 5.0, 1.0)
                    reasons.append(f"Daily EMA50 < EMA200 (death cross, {pct_below:.1f}% below)")

        if "high" in df.columns and "low" in df.columns:
            adx_data = ta.adx(df["high"].astype(float), df["low"].astype(float), close, length=14)
            if adx_data is not None and "ADX_14" in adx_data.columns:
                adx_series = adx_data["ADX_14"].dropna()
                if len(adx_series) > 0:
                    adx_val = float(adx_series.iloc[-1])
                    indicators["adx"] = adx_val
                    if adx_val > 25:
                        strength = min(strength + 0.3, 1.0)
                        reasons.append(f"Strong trend (ADX: {adx_val:.1f})")

    return TimeframeBias("1d", direction, min(strength, 1.0), reasons, indicators)


def analyze_hourly(df: pd.DataFrame, regime: str = "TRENDING") -> TimeframeBias:
    """
    Hourly chart analysis:
    - RSI regime-aware threshold (oversold zone in RANGING vs pullback zone in TRENDING).
    - MACD histogram turning up (bullish convergence).
    - Volatility Squeeze detection.
    """
    if df is None or len(df) < 50:
        return TimeframeBias("1h", 0, 0.0, ["Insufficient hourly data"], {})

    df = df.copy()
    df.columns = [c[0].lower() if isinstance(c, tuple) else str(c).lower() for c in df.columns]
    if "close" not in df.columns:
        return TimeframeBias("1h", 0, 0.0, ["Missing 'close' column"], {})

    close = df["close"].astype(float)
    direction = 0
    strength = 0.0
    reasons = []
    indicators = {}

    rsi = ta.rsi(close, length=14)
    if rsi is not None and len(rsi.dropna()) > 0:
        r = float(rsi.dropna().iloc[-1])
        indicators["rsi"] = r

        if regime == "RANGING":
            if r < 35:
                direction += 1
                strength += 0.5
                reasons.append(f"1h RSI oversold for mean reversion ({r:.1f})")
            elif r > 65:
                direction -= 1
                strength += 0.5
                reasons.append(f"1h RSI overbought for mean reversion ({r:.1f})")
            else:
                reasons.append(f"1h RSI neutral ({r:.1f})")
        else:  # TRENDING / VOLATILE
            if 40 <= r <= 55:
                direction += 1
                strength += 0.4
                reasons.append(f"1h RSI in prime pullback zone ({r:.1f})")
            elif r < 30:
                direction += 1
                strength += 0.3
                reasons.append(f"1h RSI deeply oversold ({r:.1f})")
            elif r > 70:
                direction -= 1
                strength += 0.5
                reasons.append(f"1h RSI overbought ({r:.1f})")
            elif r >= 50:
                direction += 1
                strength += 0.2
                reasons.append(f"1h RSI bullish ({r:.1f})")
            else:
                direction -= 1
                reasons.append(f"1h RSI bearish ({r:.1f})")

    # MACD Histogram Slope (Bullish Convergence Check)
    macd = ta.macd(close, fast=12, slow=26, signal=9)
    if macd is not None and "MACDh_12_26_9" in macd.columns:
        hist = macd["MACDh_12_26_9"].dropna()
        if len(hist) >= 3:
            slope = float(hist.iloc[-1]) - float(hist.iloc[-3])
            indicators["macd_hist"] = float(hist.iloc[-1])
            indicators["macd_slope"] = slope
            if slope > 0:
                direction += 1
                strength += 0.3
                reasons.append("1h MACD histogram turning up (bullish momentum convergence)")
            elif slope < 0 and float(hist.iloc[-1]) < 0:
                direction -= 1
                reasons.append("1h MACD histogram accelerating downward")

    # Volatility Squeeze
    try:
        if "high" in df.columns and "low" in df.columns:
            sma20 = close.rolling(20).mean()
            std20 = close.rolling(20).std()
            bbu = sma20 + 2 * std20
            bbl = sma20 - 2 * std20
            atr20 = ta.atr(df["high"].astype(float), df["low"].astype(float), close, length=20)
            ema20 = ta.ema(close, length=20)

            if atr20 is not None and ema20 is not None and len(bbu.dropna()) > 1:
                curr_bbu = float(bbu.iloc[-1])
                curr_bbl = float(bbl.iloc[-1])
                curr_kcu = float(ema20.iloc[-1]) + 1.5 * float(atr20.iloc[-1])
                curr_kcl = float(ema20.iloc[-1]) - 1.5 * float(atr20.iloc[-1])
                prev_bbu = float(bbu.iloc[-2])
                prev_kcu = float(ema20.iloc[-2]) + 1.5 * float(atr20.iloc[-2])

                if (curr_bbu < curr_kcu) and (curr_bbl > curr_kcl):
                    if regime != "RANGING":
                        direction -= 1
                        reasons.append("Volatility squeeze ON (waiting for trend breakout)")
                    else:
                        reasons.append("Volatility squeeze ON in ranging regime")
                elif (prev_bbu < prev_kcu) and (curr_bbu >= curr_kcu):
                    direction += 1
                    strength += 0.5
                    reasons.append("Volatility squeeze FIRED (Bullish expansion)")
    except Exception:
        pass

    return TimeframeBias("1h", direction, min(strength, 1.0), reasons, indicators)


def analyze_15min(df: pd.DataFrame, regime: str = "TRENDING") -> TimeframeBias:
    """
    15-minute chart analysis:
    - Micro-trend via EMA 9/21 (allows oversold EMA9 < EMA21 in RANGING regime).
    - Volume validation & surge detection.
    - VWAP regime-aware anchoring (oversold below VWAP in RANGING, pullback anchor in TRENDING).
    """
    if df is None or len(df) < 30:
        return TimeframeBias("15m", 0, 0.0, ["Insufficient 15m data"], {})

    df = df.copy()
    df.columns = [c[0].lower() if isinstance(c, tuple) else str(c).lower() for c in df.columns]
    if "close" not in df.columns:
        return TimeframeBias("15m", 0, 0.0, ["Missing 'close' column"], {})

    if "volume" in df.columns:
        vol = df["volume"].astype(float)
        avg_vol = vol.rolling(20).mean()
        last_avg = float(avg_vol.iloc[-1]) if not avg_vol.empty and not pd.isna(avg_vol.iloc[-1]) else 0.0
        last_vol = float(vol.iloc[-1]) if not vol.empty and not pd.isna(vol.iloc[-1]) else 0.0
        if vol.sum() == 0 or last_vol == 0 or last_avg <= 0:
            return TimeframeBias("15m", -99, 0.0, ["ZERO VOLUME/DATA MISSING — signal killed"], {})

    close = df["close"].astype(float)
    direction = 0
    strength = 0.0
    reasons = []
    indicators = {}

    ema9 = ta.ema(close, length=9)
    ema21 = ta.ema(close, length=21)
    if ema9 is not None and ema21 is not None and len(ema9.dropna()) > 1:
        curr_9, prev_9 = float(ema9.dropna().iloc[-1]), float(ema9.dropna().iloc[-2])
        curr_21, prev_21 = float(ema21.dropna().iloc[-1]), float(ema21.dropna().iloc[-2])
        indicators["ema9"] = curr_9
        indicators["ema21"] = curr_21

        if curr_9 > curr_21 and prev_9 <= prev_21:
            direction += 1
            strength += 0.5
            reasons.append("EMA9/21 bullish crossover")
        elif curr_9 > curr_21:
            direction += 1
            strength += 0.2
            reasons.append("EMA9 > EMA21 (micro-uptrend)")
        else:
            if regime == "RANGING":
                # In RANGING regime, EMA9 < EMA21 is expected at oversold extreme; do NOT penalize with -1
                reasons.append("EMA9 < EMA21 (oversold range condition)")
            else:
                direction -= 1
                reasons.append("EMA9 < EMA21 (micro-downtrend)")

    # Volume Validation
    if "volume" in df.columns:
        vol = df["volume"].astype(float)
        avg_vol = vol.rolling(20).mean()
        last_avg = float(avg_vol.iloc[-1]) if not avg_vol.empty and not pd.isna(avg_vol.iloc[-1]) else 0.0
        last_vol = float(vol.iloc[-1]) if not vol.empty and not pd.isna(vol.iloc[-1]) else 0.0

        if last_avg > 0 and last_vol > 0:
            vol_ratio = last_vol / last_avg
            indicators["vol_ratio"] = vol_ratio
            if vol_ratio >= 1.2:
                strength += 0.3
                reasons.append(f"Volume surge ({vol_ratio:.1f}x average)")

    # VWAP Regime-Aware Evaluation
    if "volume" in df.columns and "high" in df.columns and "low" in df.columns and direction != -99:
        try:
            vwap_series = ta.vwap(df["high"].astype(float), df["low"].astype(float), close, df["volume"].astype(float), anchor="D")
            if vwap_series is not None and len(vwap_series.dropna()) > 1:
                curr_vwap = float(vwap_series.dropna().iloc[-1])
                curr_close = float(close.iloc[-1])
                indicators["vwap"] = curr_vwap
                pct_from_vwap = (curr_close - curr_vwap) / curr_vwap * 100.0

                if regime == "RANGING":
                    # For MEAN_REVERSION: Price below VWAP (0.3% to 3.0% below) is expected oversold entry zone
                    if -3.0 <= pct_from_vwap <= -0.3:
                        direction += 1
                        strength += 0.4
                        reasons.append(f"Price oversold below VWAP ({pct_from_vwap:+.2f}%) — valid mean-reversion entry")
                    elif pct_from_vwap > 0.5:
                        direction -= 1
                        reasons.append(f"Price above VWAP ({pct_from_vwap:+.2f}%) — mean-reversion long not eligible")
                    else:
                        reasons.append(f"Price near VWAP ({pct_from_vwap:+.2f}%)")
                else:  # TRENDING / VOLATILE
                    if curr_close >= curr_vwap:
                        if pct_from_vwap < 1.0:
                            direction += 1
                            strength += 0.3
                            reasons.append(f"Price above VWAP (+{pct_from_vwap:.2f}%)")
                        else:
                            reasons.append(f"Price extended above VWAP (+{pct_from_vwap:.2f}%)")
                    else:
                        # Dip below VWAP within 0.8% proximity is allowed as pullback anchor touch
                        if abs(pct_from_vwap) <= 0.8:
                            direction += 1
                            strength += 0.2
                            reasons.append(f"Price within pullback distance of VWAP ({pct_from_vwap:.2f}%)")
                        else:
                            direction -= 1
                            reasons.append(f"Price broken below VWAP ({pct_from_vwap:.2f}%)")
        except Exception:
            pass

    return TimeframeBias("15m", direction, min(strength, 1.0), reasons, indicators)


def get_confluence(stock: str, frames: dict, regime: str = "TRENDING") -> ConfluenceResult:
    """
    Combine Daily, Hourly, and 15-minute timeframes into a single confluence score.
    Range: -3 to +5.
    Returns 100% backward-compatible ConfluenceResult dataclass.
    """
    daily = analyze_daily(frames.get("1d", pd.DataFrame()), regime=regime)
    hourly = analyze_hourly(frames.get("1h", pd.DataFrame()), regime=regime)
    quarter = analyze_15min(frames.get("15m", pd.DataFrame()), regime=regime)

    # Time-of-Day Hard Block Filter
    df_15 = frames.get("15m", pd.DataFrame())
    time_penalty = 0
    chop_reason = None
    if df_15 is not None and not df_15.empty:
        try:
            def _to_ist_mins(dt):
                if hasattr(dt, "tz") and dt.tz is not None:
                    import pytz
                    ist_dt = dt.astimezone(pytz.timezone("Asia/Kolkata"))
                    return ist_dt.hour * 60 + ist_dt.minute
                else:
                    h, m = dt.hour, dt.minute
                    mins = h * 60 + m
                    if h <= 10:
                        return mins + 5 * 60 + 30
                    else:
                        return mins

            ist_mins = _to_ist_mins(df_15.index[-1])
            if not (9 * 60 + 15 <= ist_mins <= 15 * 60 + 30) and len(df_15.index) > 1:
                first_ist = _to_ist_mins(df_15.index[0])
                if 9 * 60 + 15 <= first_ist <= 15 * 60 + 30:
                    ist_mins = first_ist

            if 11 * 60 + 30 <= ist_mins <= 13 * 60 + 30:
                time_penalty = -1
                chop_reason = "Mid-day Chop Filter (11:30-13:30 IST) — HARD BLOCK"
            elif ist_mins >= 14 * 60:
                time_penalty = -1
                chop_reason = "Late Session Filter (after 14:00 IST) — HARD BLOCK"
        except Exception:
            pass

    confluence = daily.direction + hourly.direction + quarter.direction + time_penalty
    all_reasons = daily.reasons + hourly.reasons + quarter.reasons

    if chop_reason:
        all_reasons.append(chop_reason)
        return ConfluenceResult(
            stock=stock, daily=daily, hourly=hourly, quarter=quarter,
            confluence_score=confluence, action="HOLD",
            regime=regime, reasons=all_reasons,
        )

    # Action Determination
    buy_threshold = 3
    sell_threshold = -2

    if any(bias.direction == -99 for bias in [daily, hourly, quarter]):
        action = "HOLD"
        all_reasons.append("HARD REJECT (One or more timeframes killed the signal)")
    elif confluence >= buy_threshold:
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
