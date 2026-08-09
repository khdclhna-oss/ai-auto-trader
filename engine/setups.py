"""
engine/setups.py — Explicit Trade Setup Classifier Engine
=========================================================
Classifies candidate stocks into structured setups:
  1. TREND_PULLBACK (Active in TRENDING regime: ADX >= 20, 50-EMA slope > 0, 21-EMA/VWAP distance <= 0.8%, 15m RSI 40-55, RVOL >= 1.2x)
  2. MEAN_REVERSION (Active in RANGING regime: ADX < 20, %B < 0.05 or Price <= Lower BB, RSI < 30, RVOL >= 1.0x)
  3. BREAKOUT_CONTINUATION (Active in TRENDING regime: 20-day high breakout, Daily RVOL >= 1.5x)
"""

from dataclasses import dataclass
from typing import Dict, Optional, Tuple
import pandas as pd
import numpy as np


# ─── Constants ────────────────────────────────────────────────────────────────
PULLBACK_RVOL_MIN    = 1.2     # Min RVOL for trend pullbacks
MEAN_REV_RVOL_MIN    = 1.0     # Min RVOL for mean reversion bounces
PULLBACK_RSI_MIN     = 40.0    # Oversold pullback floor in uptrend
PULLBACK_RSI_MAX     = 55.0    # Max RSI to avoid buying overbought tops
BREAKOUT_RSI_MIN     = 50.0    # V5.2: Breakout RSI must show momentum
BREAKOUT_RSI_MAX     = 65.0    # V5.2: Cap breakout RSI — avoid late/overbought entries
MEAN_REV_RSI_MAX     = 30.0    # Oversold RSI threshold for mean-reversion
MEAN_REV_PCT_B_MAX   = 0.05    # %B threshold (price near lower band)
ANCHOR_PROXIMITY_PCT = 0.008   # 0.8% max distance to 21-EMA or VWAP
ADX_TRENDING_FLOOR   = 25.0    # V5.1: Raised from 20 to 25 — require strong confirmed trend
ADX_RANGING_CEILING  = 20.0    # ADX ceiling for ranging regime


@dataclass(frozen=True)
class SetupResult:
    """Dataclass holding setup classification results."""
    name: str                  # "TREND_PULLBACK" | "MEAN_REVERSION" | "BREAKOUT_CONTINUATION" | "UNCLASSIFIED"
    eligible: bool             # True if candidate meets all quantitative rules for setup
    reason: str                # Human-readable breakdown of setup qualification
    daily_volume_ratio: float  # Volume ratio on daily frame vs 20-day SMA
    rvol_15m: float = 1.0      # Relative volume ratio on 15m frame vs 20-bar SMA
    regime: str = "UNKNOWN"    # Market regime ("TRENDING" | "RANGING" | "VOLATILE" | "UNKNOWN")
    score_bonus: int = 0       # Confluence bonus points (+1 or +2)


def classify_long_setup(
    frames: Dict[str, pd.DataFrame],
    regime: str = "TRENDING",
    adx: float = 25.0,
) -> SetupResult:
    """
    Classify trade candidate setup without mixing incompatible strategies.
    
    Parameters
    ----------
    frames : dict containing "1d", "1h", "15m" DataFrames
    regime : Current market regime ("TRENDING", "RANGING", "VOLATILE")
    adx    : ADX indicator value (from regime calculation)
    """
    daily = frames.get("1d")
    intraday = frames.get("15m")

    if daily is None or intraday is None or len(daily) < 51 or len(intraday) < 22:
        return SetupResult("UNCLASSIFIED", False, "Insufficient bar history for setup evaluation", 0.0, 1.0, regime, 0)

    daily_vol_ratio = _calculate_volume_ratio(daily)
    rvol_15m = _calculate_volume_ratio(intraday)

    # 1. Evaluate TREND_PULLBACK (Active in TRENDING regime)
    if regime == "TRENDING" or adx >= ADX_TRENDING_FLOOR:
        is_pullback, reason, bonus = _check_trend_pullback(daily, intraday, adx, rvol_15m)
        if is_pullback:
            return SetupResult("TREND_PULLBACK", True, reason, daily_vol_ratio, rvol_15m, regime, bonus)

        # 2. Evaluate BREAKOUT_CONTINUATION (Secondary trend setup)
        is_breakout, reason, bonus = _check_breakout(daily, intraday, adx, daily_vol_ratio)
        if is_breakout:
            return SetupResult("BREAKOUT_CONTINUATION", True, reason, daily_vol_ratio, rvol_15m, regime, bonus)

    # 3. Evaluate MEAN_REVERSION (Active in RANGING regime)
    if regime == "RANGING" or adx < ADX_RANGING_CEILING:
        is_mean_rev, reason, bonus = _check_mean_reversion(daily, intraday, adx, rvol_15m)
        if is_mean_rev:
            return SetupResult("MEAN_REVERSION", True, reason, daily_vol_ratio, rvol_15m, regime, bonus)

    return SetupResult("UNCLASSIFIED", False, "No valid TREND_PULLBACK, MEAN_REVERSION, or BREAKOUT setup", daily_vol_ratio, rvol_15m, regime, 0)


def _check_trend_pullback(
    daily: pd.DataFrame,
    intraday: pd.DataFrame,
    adx: float,
    rvol_15m: float,
) -> Tuple[bool, str, int]:
    """Check criteria for TREND_PULLBACK setup."""
    daily_close = daily["close"].astype(float)
    ema50 = daily_close.ewm(span=50, adjust=False).mean()
    ema50_slope = (ema50.iloc[-1] - ema50.iloc[-6]) / ema50.iloc[-6] if len(ema50) >= 6 and ema50.iloc[-6] > 0 else 0.0

    if ema50_slope <= 0:
        return False, f"Daily 50-EMA slope is flat or negative ({ema50_slope:.4f})", 0

    intra_close = intraday["close"].astype(float)
    price = float(intra_close.iloc[-1])
    ema21 = float(intra_close.ewm(span=21, adjust=False).mean().iloc[-1])

    # Calculate VWAP
    vwap = _calculate_vwap(intraday)
    dist_ema21 = abs(price - ema21) / ema21 if ema21 > 0 else 1.0
    dist_vwap = abs(price - vwap) / vwap if vwap > 0 else 1.0

    near_anchor = (dist_ema21 <= ANCHOR_PROXIMITY_PCT) or (dist_vwap <= ANCHOR_PROXIMITY_PCT)
    if not near_anchor:
        return False, f"Price ₹{price:.2f} far from 21-EMA (dist {dist_ema21:.1%}) & VWAP (dist {dist_vwap:.1%})", 0

    # Calculate RSI
    rsi_15m = _calculate_rsi(intra_close, period=14)
    if not (PULLBACK_RSI_MIN <= rsi_15m <= PULLBACK_RSI_MAX):
        return False, f"15m RSI ({rsi_15m:.1f}) outside pullback window [{PULLBACK_RSI_MIN}-{PULLBACK_RSI_MAX}]", 0

    if rvol_15m < PULLBACK_RVOL_MIN:
        return False, f"15m RVOL ({rvol_15m:.2f}x) below threshold ({PULLBACK_RVOL_MIN:.1f}x)", 0

    reason = f"TREND_PULLBACK qualified: 50-EMA slope up, anchor touch (EMA21/VWAP), RSI {rsi_15m:.1f}, RVOL {rvol_15m:.2f}x"
    return True, reason, 2


def _check_mean_reversion(
    daily: pd.DataFrame,
    intraday: pd.DataFrame,
    adx: float,
    rvol_15m: float,
) -> Tuple[bool, str, int]:
    """Check criteria for MEAN_REVERSION setup."""
    intra_close = intraday["close"].astype(float)
    price = float(intra_close.iloc[-1])

    pct_b, lower_bb, upper_bb = _calculate_bollinger_pct_b(intra_close, period=20, std_dev=2.0)
    rsi_15m = _calculate_rsi(intra_close, period=14)

    oversold_bb = (pct_b < MEAN_REV_PCT_B_MAX) or (price <= lower_bb)
    if not oversold_bb:
        return False, f"Price ₹{price:.2f} above lower BB (pct_b {pct_b:.3f} >= {MEAN_REV_PCT_B_MAX})", 0

    if rsi_15m >= MEAN_REV_RSI_MAX:
        return False, f"15m RSI ({rsi_15m:.1f}) not oversold (< {MEAN_REV_RSI_MAX})", 0

    if rvol_15m < MEAN_REV_RVOL_MIN:
        return False, f"15m RVOL ({rvol_15m:.2f}x) below floor ({MEAN_REV_RVOL_MIN:.1f}x)", 0

    reason = f"MEAN_REVERSION qualified: ADX {adx:.1f} ranging, BB %B {pct_b:.3f} < {MEAN_REV_PCT_B_MAX}, RSI {rsi_15m:.1f} < 30, RVOL {rvol_15m:.2f}x"
    return True, reason, 2


def _check_breakout(
    daily: pd.DataFrame,
    intraday: pd.DataFrame,
    adx: float,
    daily_vol_ratio: float,
) -> Tuple[bool, str, int]:
    """Check criteria for BREAKOUT_CONTINUATION setup (V5.2)."""
    daily_close = daily["close"].astype(float)
    price = float(daily_close.iloc[-1])
    high_series = daily["high"].astype(float)
    if len(high_series) < 21:
        return False, "Insufficient history for 20-day high calculation", 0
    high_20d = float(high_series.iloc[-21:-1].max())

    if not (price > high_20d and daily_vol_ratio >= 1.5):
        return False, "Not a 20-day breakout with volume", 0

    # V5.2: RSI gate — breakout must have momentum but not be overbought
    intra_close = intraday["close"].astype(float)
    rsi_15m = _calculate_rsi(intra_close, period=14)
    if not (BREAKOUT_RSI_MIN <= rsi_15m <= BREAKOUT_RSI_MAX):
        return False, f"Breakout RSI ({rsi_15m:.1f}) outside window [{BREAKOUT_RSI_MIN}-{BREAKOUT_RSI_MAX}] — avoid overbought/weak breakouts", 0

    return True, f"BREAKOUT_CONTINUATION qualified: 20-day high breakout (\u20b9{price:.2f} > \u20b9{high_20d:.2f}), Daily Vol {daily_vol_ratio:.2f}x, RSI {rsi_15m:.1f}", 1


def _calculate_volume_ratio(df: pd.DataFrame, period: int = 20) -> float:
    """Calculate ratio of current volume vs 20-bar SMA volume."""
    vol = df.get("volume")
    if vol is None:
        return 1.0
    v_clean = vol.astype(float).dropna()
    if len(v_clean) < (period + 1):
        return 1.0
    avg_vol = float(v_clean.iloc[-(period + 1):-1].mean())
    curr_vol = float(v_clean.iloc[-1])
    return (curr_vol / avg_vol) if avg_vol > 0 else 1.0


def _calculate_vwap(df: pd.DataFrame) -> float:
    """Calculate Volume-Weighted Average Price (VWAP) for intraday frame."""
    try:
        high = df["high"].astype(float)
        low = df["low"].astype(float)
        close = df["close"].astype(float)
        volume = df.get("volume")
        if volume is None or volume.astype(float).sum() == 0:
            return float(close.iloc[-1])
        vol = volume.astype(float)
        typical_price = (high + low + close) / 3.0
        vwap = (typical_price * vol).sum() / vol.sum()
        return float(vwap) if not np.isnan(vwap) and vwap > 0 else float(close.iloc[-1])
    except Exception:
        return float(df["close"].iloc[-1])


def _calculate_rsi(close: pd.Series, period: int = 14) -> float:
    """Calculate RSI for series."""
    if len(close) < period + 1:
        return 50.0
    delta = close.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    last_gain = float(gain.iloc[-1]) if not np.isnan(gain.iloc[-1]) else 0.0
    last_loss = float(loss.iloc[-1]) if not np.isnan(loss.iloc[-1]) else 0.0

    if last_loss == 0:
        return 100.0 if last_gain > 0 else 50.0
    rs = last_gain / last_loss
    rsi = 100.0 - (100.0 / (1.0 + rs))
    return float(rsi) if not np.isnan(rsi) else 50.0


def _calculate_bollinger_pct_b(close: pd.Series, period: int = 20, std_dev: float = 2.0) -> Tuple[float, float, float]:
    """Calculate Bollinger Bands and %B indicator."""
    c = float(close.iloc[-1])
    if len(close) < period:
        return 0.5, c, c
    sma = float(close.tail(period).mean())
    std = float(close.tail(period).std())
    upper = sma + (std * std_dev)
    lower = sma - (std * std_dev)
    band_width = upper - lower
    pct_b = (c - lower) / band_width if band_width > 0 else 0.5
    return float(pct_b), lower, upper
